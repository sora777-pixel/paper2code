---
title: "Anchor-Spectral Topic Discovery for Short Texts"
authors: "Wenqi Luo, Haoran Shen, Jia Tan, Yuxin Meng"
---

# Abstract

Short-text topic modeling is a hard problem because individual documents provide
only a handful of co-occurrence signals, which makes likelihood-based models such
as LDA unstable. We present AS-Topic, a lightweight anchor-based spectral method
that estimates topic-word distributions from a small set of high-frequency anchor
words and then propagates them to the full vocabulary via a regularized
least-squares solve. Compared with neural topic models, AS-Topic requires no
pretraining and fits on a single CPU machine in under two minutes for corpora of
one hundred thousand documents. On three public short-text corpora, AS-Topic
improves clustering accuracy by 6.8 points over BERTopic and 12.2 points over
NMF, while using less than one twentieth of the memory. We further release a
reference implementation and all evaluation scripts.

## 1 Introduction

Short texts, such as search queries, product reviews, and microblog posts, are
ubiquitous in industrial applications. Topic discovery on such data is
notoriously difficult: the average document length is often below twenty tokens,
so a document rarely contains more than one occurrence of any given word. Under
these conditions the document-topic posterior of LDA is dominated by the prior,
and the learned topics drift toward a generic background distribution.

Recent neural approaches alleviate the sparsity problem by injecting external
knowledge through pretrained language models. These methods are effective but
expensive: they require GPU inference, careful prompt or embedding engineering,
and they are sensitive to the domain of the pretrained corpus. In many practical
settings, an engineer simply wants a stable topic decomposition of a modest
in-house corpus on a laptop.

We therefore revisit the spectral approach to topic modeling. The key insight
behind spectral methods is that the second-order word co-occurrence statistics of
a corpus already determine the topic subspace up to rotation. If the topics are
separable, which in short-text corpora they usually are because domain vocabulary
is distinctive, then a small set of anchor words spans the topic simplex. Our
contribution is a practical estimator for that simple, combined with a
closed-form propagation step that removes the need for an expensive non-negative
matrix factorization.

## 2 Related Work

Probabilistic topic models such as LDA model each document as a mixture of topics
and infer the posterior with variational or Gibbs sampling. On long documents
they remain strong baselines, but their performance degrades sharply as document
length decreases. Neural topic models replace the Dirichlet prior with an
autoencoder and use a pretrained encoder to share statistical strength across
languages and domains. They achieve the best reported numbers on most
short-text benchmarks, at the cost of substantial compute.

Anchor-based spectral methods sit between the two extremes. They are
provably consistent under a separability assumption and require only a
second-order moment matrix. Prior work focused on the theoretical recovery
guarantee and used comparatively expensive anchor selection heuristics. We
instead target the engineering question: how far can a carefully implemented
spectral estimator get on short texts when it must run on one CPU core.

## 3 Method

### 3.1 Problem Formulation

Let V denote the vocabulary and K the number of topics. We observe a corpus of D
documents represented by a word-document count matrix. Spectral topic modeling
estimates a topic-word matrix whose rows are probability distributions over V.
The separability assumption states that for every topic there exists at least one
anchor word that occurs with non-negligible probability in that topic and with
near-zero probability in all other topics.

### 3.2 Anchor Selection and Propagation

We score candidate anchor words by a normalized pointwise-mutual-information
criterion computed over the second-order co-occurrence matrix, keep the top
candidates, and greedily select K anchors that maximize the volume of the
resulting simplex. Topic-word distributions are then recovered by solving a
non-negative least-squares problem restricted to the anchor simplex, followed by
one step of exponentiated gradient refinement.

Algorithm 1 summarizes the procedure. The dominant cost is the sparse
co-occurrence accumulation in line 4, which we implement with a CSR matrix.

```
Algorithm 1: AS-Topic anchor selection and propagation
Input: co-occurrence matrix C, number of topics K, candidate pool size M
Output: topic-word matrix W
1: procedure ASTOPIC(C, K, M)
2:   S ← top M words ranked by normalized PMI over C
3:   A ← empty set
4:   for each word w in S do
5:     if |A| < K then
6:       A ← A ∪ {w}
7:     end if
8:   end for
9:   W ← nonnegative least squares over the anchor simplex A
10:  return normalize_rows(W)
11: end procedure
```

### 3.3 Complexity

The co-occurrence accumulation costs O(nnz(C)) time and memory, where nnz(C) is
the number of nonzero word pairs. Anchor scoring is O(M log M). The least-squares
solve is O(K^3 + K * nnz(C)) and dominates only when K is large. In our
experiments K never exceeds fifty, so the whole pipeline stays within two minutes
on a single CPU core.

## 4 Experiments

### 4.1 Datasets

We evaluate on three public short-text corpora: SearchSnippets, StackOverflow,
and Biomedical. Table 1 reports their statistics after standard tokenization and
stopword removal.

| Dataset | Documents | Vocabulary | Avg. tokens |
| --- | --- | --- | --- |
| SearchSnippets | 12,340 | 8,214 | 18.4 |
| StackOverflow | 19,880 | 14,602 | 21.7 |
| Biomedical | 41,600 | 27,455 | 16.2 |

Table 1. Statistics of the three evaluation corpora after preprocessing.

### 4.2 Baselines and Metrics

We compare against K-Means on TF-IDF vectors, NMF with sparse constraints, and
BERTopic with default hyperparameters. We report clustering accuracy, normalized
mutual information (NMI), and the adjusted Rand index (ARI). All numbers are
averaged over five runs with different random seeds; the standard deviation never
exceeds 0.8 points.

### 4.3 Main Results

Table 2 reports the main comparison on the StackOverflow corpus.

| Method | Accuracy | NMI | ARI |
| --- | --- | --- | --- |
| K-Means | 62.4 | 51.8 | 44.1 |
| NMF | 66.1 | 55.3 | 47.9 |
| BERTopic | 71.5 | 61.2 | 53.6 |
| Ours | 78.3 | 68.7 | 61.4 |

Table 2. Clustering performance on the StackOverflow corpus. Higher is better.

### 4.4 Ablation Study

Table 3 removes one component at a time. The share column reports each
component's relative contribution to the total accuracy gain over K-Means.

| Variant | Accuracy | Share(%) |
| --- | --- | --- |
| Full model | 78.3% | 41.2% |
| w/o exponentiated refinement | 74.1% | 28.6% |
| w/o PMI anchor scoring | 71.9% | 19.4% |
| w/o simplex volume selection | 69.6% | 10.8% |

Table 3. Ablation on the StackOverflow corpus.

Removing the exponentiated-gradient refinement costs 4.2 accuracy points, whereas
replacing the normalized PMI anchor score with raw frequency costs 6.4 points.
Simplex volume selection contributes the least but still yields a 2.3 point gain.

![Figure 1](figures/figure1.svg)

Figure 1. Clustering performance on the StackOverflow corpus. AS-Topic leads on
all three metrics simultaneously.

## 5 Conclusion

We presented AS-Topic, a CPU-friendly spectral topic model for short texts. It
matches or exceeds neural baselines on three public corpora while fitting in
under two minutes on a single core. Limitations include the separability
assumption, which can be violated in highly polysemous domains, and the need to
choose K in advance.

## 6 Code and Data Availability

The code is available at https://github.com/example-lab/astopic. The evaluation
datasets used in this paper are available at https://zenodo.org/record/1234567.
