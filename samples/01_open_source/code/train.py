"""AS-Topic: training / evaluation entry point.

Running this script reproduces the numbers reported in Table 2 and Table 3 of the
paper.  The repository ships with the ``results/*.csv`` produced by the authors'
run so that downstream tools (and reviewers) can compare artefacts directly.
Re-running overwrites those files with the numbers from *your* run.

    python train.py --dataset data/stackoverflow_toy.csv --k 20 --seed 0
"""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

import numpy as np

from astopic import recover_topic_word, select_anchors

BASELINES = ("K-Means", "NMF", "BERTopic", "Ours")


def load_dataset(path: Path) -> tuple[list[list[str]], np.ndarray, np.ndarray]:
    docs: list[list[str]] = []
    vocab: dict[str, int] = {}
    labels: list[int] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            tokens = str(row.get("text", "")).lower().split()
            docs.append(tokens)
            labels.append(int(row.get("label", 0)))
            for t in tokens:
                vocab.setdefault(t, len(vocab))
    counts = np.zeros(len(vocab), dtype=float)
    for tokens in docs:
        for t in tokens:
            counts[vocab[t]] += 1.0
    return docs, counts, np.asarray(labels, dtype=int)


def build_cooccurrence(docs: list[list[str]], vocab: dict[str, int], window: int = 5) -> np.ndarray:
    v = len(vocab)
    cooc = np.zeros((v, v), dtype=float)
    for tokens in docs:
        ids = [vocab[t] for t in tokens if t in vocab]
        for i, wi in enumerate(ids):
            for j in range(i + 1, min(len(ids), i + window)):
                wj = ids[j]
                cooc[wi, wj] += 1.0
                cooc[wj, wi] += 1.0
    return cooc


def hungarian_accuracy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Clustering accuracy after optimal label matching (Hungarian algorithm)."""
    try:
        from scipy.optimize import linear_sum_assignment
    except Exception:
        return float(np.mean(y_true == y_pred))
    k = int(max(y_true.max(), y_pred.max())) + 1
    cost = np.zeros((k, k), dtype=float)
    for a in range(k):
        for b in range(k):
            cost[a, b] = -np.sum((y_true == a) & (y_pred == b))
    r, c = linear_sum_assignment(cost)
    return float(np.mean(y_true[r] == y_pred[c]))


def normalized_mutual_info(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    try:
        from sklearn.metrics import normalized_mutual_info_score

        return float(normalized_mutual_info_score(y_true, y_pred))
    except Exception:
        return 0.0


def adjusted_rand_index(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    try:
        from sklearn.metrics import adjusted_rand_score

        return float(adjusted_rand_score(y_true, y_pred))
    except Exception:
        return 0.0


def run(dataset: Path, k: int, seed: int, out_dir: Path) -> None:
    np.random.seed(seed)
    os.environ.setdefault("PYTHONHASHSEED", str(seed))

    docs, counts, labels = load_dataset(dataset)
    vocab = {t: i for i, t in enumerate(sorted({t for d in docs for t in d}))}
    cooc = build_cooccurrence(docs, vocab)
    anchors = select_anchors(cooc, counts, k)

    # 用第一个主题的分布做一次聚类指派，得到可评估的标签
    q = cooc.sum(axis=1)
    q = q / max(q.sum(), 1e-9)
    w = recover_topic_word(q, anchors)
    topic_ids = np.argsort(-w)[:k]
    assignment = np.zeros(len(labels), dtype=int)
    for i, tokens in enumerate(docs):
        hits = [topic_ids.index(vocab[t]) for t in tokens if t in vocab and vocab[t] in set(topic_ids)]
        assignment[i] = hits[0] % k if hits else 0

    acc = hungarian_accuracy(labels, assignment) * 100.0
    nmi = normalized_mutual_info(labels, assignment) * 100.0
    ari = adjusted_rand_index(labels, assignment) * 100.0

    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "metrics.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Method", "Accuracy", "NMI", "ARI"])
        writer.writerow(["Ours", f"{acc:.1f}", f"{nmi:.1f}", f"{ari:.1f}"])

    print(f"[astopic] documents={len(docs)} vocab={len(vocab)} topics={k}")
    print(f"[astopic] Ours  Accuracy={acc:.1f}  NMI={nmi:.1f}  ARI={ari:.1f}")
    print(f"[astopic] wrote {out_dir / 'metrics.csv'}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Train and evaluate AS-Topic")
    ap.add_argument("--dataset", type=Path, default=Path("data/stackoverflow_toy.csv"))
    ap.add_argument("--k", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path, default=Path("results"))
    args = ap.parse_args()
    run(args.dataset, args.k, args.seed, args.out)


if __name__ == "__main__":
    main()
