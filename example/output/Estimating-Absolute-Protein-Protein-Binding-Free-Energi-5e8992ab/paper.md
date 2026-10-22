# Estimating Absolute Protein−Protein Binding Free Energies by a Super Learner Model

**作者**：Elton J. F. Chaves, João Sartori, Whendel M. Santos, Carlos H. B. Cruz, Emmanuel N. Mhrous, Manassés F. Nacimento-Filho, Matheus V. F. Ferraz, Roberto D. Lins*

## 摘要

Protein−protein binding is central to most bio- chemical processes of all living beings. Its importance underlies mechanisms ranging from cell interactions to metabolic control, but also to ex vivo biotechnology, such as the development of therapeutic monoclonal antibodies, the engineering of enzymes for industrial biocatalysis, the development of biosensors for disease detection, and the assembly of artificial protein complexes for drug screening. Therefore, predicting the strength of their association allows for understanding the molecular mechanisms and ultimately controlling them. We devised a machine learning ensemble model that uses Rosetta-based quantities to predict binding free energies of protein−protein complexes with accuracy rivaling both computationally demanding methods and currently available ML/DL tools. The method was encoded into an application Python pipeline named PBEE, which stands for Protein Binding Energy Estimator, allowing a rapid calculation of the absolute binding free energies of protein complexes from their PDB coordinates.

### INTRODUCTION

Protein-protein association is central to most biochemical
processes of all living beings. Its importance underlies
mechanisms ranging from cell interactions to metabolic control
but also to ex vivo biotechnology (e.g., biosensors and
diagnostic kits). Understanding the underlying mechanisms
and quantifying the strength of these interactions is crucial for
advancing our knowledge of protein function, drug discovery,
and the development of targeted therapeutic interventions
through protein engineering (e.g., diagnostic markers1 and
biopharmaceuticals2,3). Binding affinity can be satisfactorily
assessed by the Gibbs free energy (ΔG0), a quantity that is
experimentally challenging and resource-intensive to obtain.
There are several molecular mechanics-based methods to
accurately compute the absolute binding free energy ( Gbind
0
)
of proteins and small ligands.4-7 However, obtaining the
Gbind
0
for protein-protein association has proven to be a
harder task, likely due to the higher number of degrees of
freedom, non-negligible entropic effects, and the involved time
scale associated with binding events, which far exceeds what is
achievable by conventional molecular simulations. Among the
many methods published in the literature, enhanced sampling
methods, such as metadynamics, are worth mentioning by their
accuracy when computing protein-protein binding affinities.
These calculations are highly computationally demanding, but
Wang and collaborators have recently proposed a metady-
namics protocol that was tested on 19 protein-protein
structure complexes. The authors reported a standard error
of 1.61 kcal/mol and a Pearson’s correlation of 0.84 from the
experimental values.8 Nevertheless, the computational need
associated with the technique precludes its use in a high-
throughput fashion.
To overcome this issue, the community has increasingly
turned to machine and deep learning algorithms in the past few
years. Guo and Yamaguchi9 have reviewed the field and
reported 6 structure-based models10-16 to predict protein-
protein binding affinities. Nevertheless, most models have been
developed to predict binding free energy of single and multiple
point mutations considering small subsets of protein
complexes using experimental data available in the SKEMPI2
database.10-16 As a consequence, these models are highly
specific, and their performance varies significantly upon the
test set used.
A more general approach was used by Vangone and
Bonvin.13 They developed an ML model based on interface
contacts and noninteracting surface to predict the binding
energy of protein-protein complexes. Their approach is based
Received:
September 9, 2024
Revised:
February 5, 2025
Accepted:
February 10, 2025
Published: February 20, 2025
Article
pubs.acs.org/jcim
© 2025 The Authors. Published by
American Chemical Society
2602
https://doi.org/10.1021/acs.jcim.4c01641
J. Chem. Inf. Model. 2025, 65, 2602-2609
This article is licensed under CC-BY 4.0
Downloaded from pubs.acs.org/jcisd8/article-pdf/65/5/2602/68457794/ci4c01641.pdf by guest on 19 October 2026

[[PAGE:2]]

on the evidence that a greater number of interfacial contacts
correlates with stronger binding. The authors used a curated
data set comprising 81 protein complexes from affinity
benchmarks,17 reaching a validation accuracy of 1.89 kcal/
mol RMSE and a Pearson correlation of 0.73. The method has
been implemented as part of PRODIGY.18
Other recent studies from Yang and collaborators have
highlighted the importance of using interface descriptors to
train an artificial neural network (ANN) to predict protein-
protein binding affinity.19,20 They have generated a variety of
machine learning (ML) and deep learning (DL) models. Their
model is not general, as it requires the use of one distinct
model for predicting binding affinities of antibody-antigens
and another for protein-protein complexes.
In agreement with the importance of interfacial properties to
predict affinities, we have also recently trained an ANN based
on 14 UMAP-reduced Rosetta-calculated descriptors, where
interface area, shape complementarity, solvent-accessible
solvent area, and number of residues on the interface were
listed among the most important features.21 We used the 81
protein complexes from PRODIGY for training. Validation was
performed using PDBbind and a structural database containing
all nanobody complexes available in the PDB. Prediction
yielded an RMSE of ca. 2.5 kcal/mol and Pearson’s correlation
of 0.53.21 Overall, these previous attempts using structure-
based properties to train the ML/DL algorithm have not been
able to provide a model that is both general and can accurately
predict the absolute binding affinities/free energies of protein-
protein association. In the present work, we aim to address the
limitations of existing methods by developing a generalizable
and accurate machine learning model for predicting the
absolute binding affinities of protein-protein interactions. For
this, a tailored extensive Rosetta22 descriptors list from
hundreds of protein-protein structure complexes was used
to train 10 ML algorithms. These algorithms were used as base
learners to devise a super learner (SL) model.23 The SL, in
turn, is an ensemble learning technique that combines
predictions from multiple models (base learners) to achieve
a better predictive performance than any single model. It
leverages the strengths of various algorithms and reduces their
weakness by creating a weighted combination linear regression
of their outputs. Weights are calculated by non-negative least
squares based on the Lawson-Hanson algorithm.24,25
We tested our SL model on 105 protein complexes
(validation set) retrieved from the main protein databases
(BENCHMARK-5.5,26 PDBbind,27 PRODIGY,18 and SKEM-
PI228) and achieved an accuracy of 1.98 kcal/mol RMSE and a
Pearson correlation of 0.70. Therefore, the SL model
demonstrates improved accuracy and generalizability across
diverse protein-protein complexes. The code is available as an
open-source tool on GitHub (https://github.com/chavesejf/
pbee). This work represents a significant step toward the
efficient and scalable prediction of protein-protein binding
affinities.

### METHODS

Data Sets. We have initially selected 603 X-ray resolved
protein-protein complexes from the following databases: (i)
PDBbind,27 (ii) PRODIGY,18 (iii) SKEMPI2,28 and (iv)
protein-protein docking benchmark version 5.526 that had no
gaps or missing heavy atoms and with a maximum resolution of
2.5 Å. The data set was split into training and validation sets
(more details in the Machine Learning section). An extensive
manual inspection of protein-protein interface similarities was
performed to ensure interface uniqueness and nonredundancy
across training and validation sets. We have excluded mutants
of the same protein-protein complex and complexes featuring
similar three-dimensional interfaces across data sets, aiming to
prevent data leaking. The final data set was comprised of 532
protein-protein complexes (split into 427 protein-protein
complexes in the training set and 105 in the validation set).
The number of protein-protein complexes from each database
used in this work is shown in Table 1. It is important to
highlight that PDBbind contains the largest number of
protein-protein complexes, which in turn included complexes
from the other data sets. The experimental affinity data for
these protein-protein complexes were obtained from the
databases themselves, and if necessary, the experimental
binding free energy ( Gbind
0
) was calculated from the
dissociation constant (KD) based on the following relationship:
=
G
RT
K
ln(
)
bind
0
D , where R is the ideal gas constant, and
T is the temperature in Kelvin (here considered as 298.15 K
for all cases).
Structure Preprocessing. The preprocessing pipeline
consisted of the following steps: (i) search for gaps in the
backbone, (ii) add missing hydrogens, (iii) identify structural
ion atoms such as Mg2+, Ca2+, Na+, Cl-, Fe2+/3+, K+, and Zn2+
with a Euclidean distance of less than 2.0 Å from any protein
atom, and (iv) eliminate any other heteroatom types (e.g.,
nonstructural ion atoms, water molecules, small ligands,
cofactors, and sugar moieties from glycosylation).
Structure Postprocessing. Geometry Optimization. The
resulting geometries (preprocessed structures) were optimized
using the Rosetta software according to the following protocol:
(i) a first optimization of the side chains with the backbone
held fixed; (ii) a second optimization for all atoms, both using
Rosetta’s standard minimization algorithm, adjusted to 50,000
steps and a convergence threshold of 10-4 Rosetta energy
units; (iii) flipping histidine, asparagine, and glutamine
residues were probed during hydrogen placement optimiza-
tion, as well as sampling χ1 and χ2 rotamer angles for all
residues. The BETA_NOV16 potentials16,29 were employed
for scoring geometry optimization.
Descriptors of Protein-Protein Association. Protein-
protein interface properties were computed using the
InteractionEnergyMetric, ContactMolecularSurface, Interface-
Holes, and InterfaceAnalyzerMover applications29 within
Rosetta.18 The auto_setup_metals parameter was activated if
ions were detected in the structures. All properties were
calculated using the BETA_NOV16 potentials.16,29 The final
data set was comprised of 52 features (Table S1) for each of
Table 1. Size of Nonredundant Data Set of Protein-Protein
Complexes Used to Train and Validate ML Models
Database
Training set #
Validation set #
PDBbinda
366
90
PRODIGYb
2
-
SKEMPI2c
14
7
Benchmark 5.5d
45
8
Total
427
105
ahttp://pdbbind.org.cn.
bhttp://wenmr.science.uu.nl/prodigy/data-
set. chttp://life.bsc.es/pid/skempi2. dhttp://zlab.wenglab.org/bench-
mark.
Journal of Chemical Information and Modeling
pubs.acs.org/jcim
Article
https://doi.org/10.1021/acs.jcim.4c01641
J. Chem. Inf. Model. 2025, 65, 2602-2609
2603
Downloaded from pubs.acs.org/jcisd8/article-pdf/65/5/2602/68457794/ci4c01641.pdf by guest on 19 October 2026

[[PAGE:3]]

the 532 structures along with their experimental
Gbind
0
.
(Unsupervised and supervised methods such as UMAP, PCA,
LDA, and SHAP were used to attempt to reduce
dimensionality. Final performance was poor in all cases. Data
not shown for conciseness.)
Machine Learning. We have coded a super learner (SL)
model that combines 10 different ML algorithms: AdaBoost,
bagging, decision tree, elastic net, extra trees, KNeighbors,
linear regression, random forest, support vector regressor, and
XGBoost. A schematic illustration of the super learner model
can be viewed in Figure 1. The data set was split into 80%
training set and 20% validation set, where a Gaussian
distribution of the experimental binding free energies was
present in both sets, therefore ensuring that the model was
trained on the entire target property interval. The validation set
was set apart and not used in the training (external validation).
K-fold split the training set into 10 subsets, which were used to
train all algorithms individually and a meta model was
generated. The training set was also used to train each ML
model individually using the same parameters for each case
and the same K-fold cross-validation scheme. Extensive
hyperparameter tuning was applied prior to defining the
training engine. The number of iterations was set to 1,000,000
for elastic networks and 1,000 for bagging, random forest, and
Figure 1. Schematic representation of the SL pipeline, which undergoes three steps. (A) The full training set is used to train 10 different ML
algorithms, and (B) it was split into 10 subsets via K-fold. The subsets were trained by each ML algorithm and evaluated using k-fold cross-
validation. All out-of-fold predictions were stored as a covariance matrix, and the model was fit to the full subset and stored. A meta-model
(m(z;β)) was generated by estimating (E(ΔG0|Z)) the target function (ΔG0, or specifically here
Gbind
0
) in the validation block on the
corresponding training block candidate learner, where Z is the covariate vector. (C) A SL model is generated by combining predictions from each
candidate using a weighted combination linear regression of their outputs of the full data set and the metamodel (m(z;β)).
Figure 2. PBEE flowchart for the protein-protein complex structure PDB ID 1XGU, corresponding to antibody (HyHEL-63) that binds to
lysozyme C (PDB 1XGU). Proteins are represented in cartoon model, where the antibody heavy and light chains are shown as chains “A″ and “B″
and color-coded in blue and red, respectively, while Lysozyme C is shown as “C″ and color-coded as dark gray. The pipeline works as follows: (i)
the argument --ipdb receives the structure of the complex(es) in PDB format; (ii) the arguments --partner1 and --partner2 receive the chain IDs of
the binding partners (it is worth noting that chains are renamed in order of appearance in the PDB file to A, B, C and so on); and (iii) PBEE runs
the pre- and postprocessing stages and returns the free energy of binding of the complex in kcal/mol and corresponding dissociation constant,
calculated by the SL model. The user can also choose to print predictions from any of the individual ML methods. In addition, we also deployed
the PBEE code as a Google Colab notebook. It is designed as an alternative to enhance user accessibility, as there is no need for local installations,
ensuring end user convenience with the same standalone features. The application can be used on any device. The code and instructions of how to
use are available at: https://colab.research.google.com/drive/1lu1dC0yRltKK_Wp-gF26oHcZSCiHaI8b.
Journal of Chemical Information and Modeling
pubs.acs.org/jcim
Article
https://doi.org/10.1021/acs.jcim.4c01641
J. Chem. Inf. Model. 2025, 65, 2602-2609
2604
Downloaded from pubs.acs.org/jcisd8/article-pdf/65/5/2602/68457794/ci4c01641.pdf by guest on 19 October 2026

[[PAGE:4]]

extra trees. The objective was set to square deviation error for
extreme gradient boosting. The coefficient of determination
(R2) was used as a loss function metric in all cases. The
remaining parameters were set to default. The meta model was
combined with the training results of each algorithm
individually using the entire training set to generate the SL
engine. Prediction is carried out using the test set, and accuracy
was assessed by root-mean-square error (RMSE) and Pearson
correlation (R). The remaining properties are fit to the
individual base models, which are stacked to be used with the
SL engine. The
Gbind
0
is then computed for all individual ML
and SL models.
The PBEE Software. A Python pipeline called PBEE
(protein binding energy estimator) was developed to easily
Figure 3. Regressions on the validation set using PRODIGY, Rosetta, and the trained ML and SL models (RMSE: root-mean-square error; R:
Pearson correlation). Each point represents a protein-protein complex, and they are color-coded by the dataset they were collected (clear:
Benchmark-5.5; light blue: PDBbind; blue: SKEMPI2). the database the protein-protein complexes were collected.
Figure 4. Comparison of PBEE prediction accuracy by ML method to Rosetta and PRODIGY, for the three protein-protein complex data sets.
Journal of Chemical Information and Modeling
pubs.acs.org/jcim
Article
https://doi.org/10.1021/acs.jcim.4c01641
J. Chem. Inf. Model. 2025, 65, 2602-2609
2605
Downloaded from pubs.acs.org/jcisd8/article-pdf/65/5/2602/68457794/ci4c01641.pdf by guest on 19 October 2026

[[PAGE:5]]

calculate the absolute binding free energies from the
coordinates of protein-protein complexes in the PDB format.
The PBEE workflow, as well as an example of how to use it, as
the command line, is shown in Figure 2. The software can be
easily installed and run locally. It was built to run on a premade
virtual environment, containing all dependencies needed to run
the PBEE pipeline described in Figure 2. It uses PyRosetta to
calculate the Rosetta descriptors and therefore bypasses the
need of installing the Rosetta software.

### RESULTS AND DISCUSSION

We have assessed PBEE’s accuracy by the ML method,
considering the protein-protein complexes included in the test
set (Figure 3), but also its performance by database (Figure 4).
Corresponding PRODIGY18 and Rosetta18 predictions were
used for comparison. While the former is arguably considered
the best performer, it is also important to evaluate the latter, as
Rosetta has been used to generate the descriptors used in this
study. The correlation between the calculated
Gbind
0
by
Rosetta is shown in Figure 3. The lack of correlation is not a
surprise, as the Rosetta software was not specifically developed
to predict binding free energies of protein-protein complexes.
The use of any ML model represents an improvement over
Rosetta’s BETA_NOV1616,29 (Figure 3). PRODIGY18 also
represents an improvement over Rosetta’s prediction. As
described in the literature, it performs extremely well on its
own set of structures, but its prediction lacks the consistency of
our SL model when other data sets are considered. When
considering all structures in the four data sets, PRODIGY18
performance decreases significantly (Figure 4). Similarly, Laan
et al. have also noticed that PRODIGY performance is
compromised when a large data set is used.23 The authors
developed the ProBAN method for predicting binding affinity
in protein-protein complexes based on a deep convolutional
neural network.30 The model was trained considering 3959
protein-protein complexes and evaluated using an external
test for the PPI-Affinity service (82 complexes). Its prediction
accuracy peaked at a Pearson correlation of 0.55 and an RMSE
of 1.75 kcal/mol. In addition, in this study, PRODIGY also
obtained a poor Pearson correlation for one of the data sets (R
= 0.28).
In this study, SL and ET rank highest and tie in prediction
accuracy when data sets are combined (Figure 3). While tree-
based methods are well-known for bias reduction, SL creates
an optimal weighted average of several ML models, as
demonstrated by Laan et al. in their work on ensemble
learning.23 Our data confirms that SL is as accurate as the best
ML prediction algorithms. It achieves RMSE equal to 1.98
kcal/mol and R equal to 0.70 (Figure 3). However, the
advantage of the SL model over individual models is shown
when considering the different data sets (Figure 4). PBEE’s SL
achieves a consistent level of accuracy, scoring higher among
the best predictions regardless of the database. Therefore,
given its robustness, the SL is set as the default in the PBEE
pipeline, and it is the recommended option. Nevertheless, the
user can select a different method to be used for
Gbind
0
prediction for benchmarking purposes and/or ML method
comparison.
Increasing PBEE’s overall accuracy faces some challenges.
We are currently limited by the number of high-resolution
structures, capping the size of the training data set to a few
hundred structures. In addition, binding data come from
different experimental conditions and techniques (e.g., SPR,
BLI, ITC), and their intrinsic standard deviation errors are
typically between 1% and 5%. Nevertheless, PBEE’s accuracy
can be considered acceptable for high-throughput applications
aiming for speeding up time to solution and saving laboratory
costs. It represents an attractive strategy to triage the
thousands or millions of binding decoys generated by
structure-based computational protein design methods (e.g.,
Rosetta,22 FoldX,31,32 RFdiffusion33), as it can rapidly compute
their association binding free energies. Benchmarks show that
PBEE’s speed depends on the number of atoms. Most
complexes take less than 2 min in a modern-day desktop
computer (Figure 5), while large complexes can take over 5
min to compute. Nevertheless, as predictions for each
protein-protein complex are independent, it will scale with
the number of cores available.
Moreover, to improve the interpretability of the SL model
and understand the impact of each feature on its predictions,
we have used SHAP (Shapley Additive exPlanations)34
(method details are available n the Supporting Information)
to develop a model explainer for the SL. Figure 6 shows the 20
descriptors having the highest contribution to the prediction of
the
Gbind
0
according to the SL model. It revealed that
quantities related to the protein-protein interface (such as the
contact molecular surface (cms) and those calculated by the
InterfaceAnalyzer routine, shown by the ifa prefix) exert a
significant influence on the model’s outcome.

### FINAL CONSIDERATIONS

We have explored 10 different ML models for the prediction of
protein-protein binding affinities and combined them then
into a SL model. The SL engine was implemented into
software named PBEE, which is available to download and use
via GitHub. The prediction performance of PBEE is in pair
with the most accurate methods available, regardless of
computational requirements. It also does not require nontrivial
setup, often associated with enhanced sampling techniques.
While a few other models for the same purpose are available in
the literature to date, we have used the most comprehensive,
diverse, unbiased, and least redundant data set. In addition,
Figure 5. PBEE processing time as a function of the number of atoms
in the protein-protein complex. Benchmark was conducted on a 13th
generation Intel core i5-13450HX with 23 GM RAM. Numbers on
top of the bars correspond to the number of atoms of the complexes.
Journal of Chemical Information and Modeling
pubs.acs.org/jcim
Article
https://doi.org/10.1021/acs.jcim.4c01641
J. Chem. Inf. Model. 2025, 65, 2602-2609
2606
Downloaded from pubs.acs.org/jcisd8/article-pdf/65/5/2602/68457794/ci4c01641.pdf by guest on 19 October 2026

[[PAGE:6]]

validation was carried out on an external, randomly selected,
data set of protein-protein complexes. Moreover, our SL
engine implemented as PBEE accelerates high-throughput
protein-protein binding affinity calculations, making it
valuable for a myriad of biotechnological applications such as
hit identification, pathogen immune evasion upon antigen
mutations, evaluation of antibody therapy efficacy, and protein
design endeavors in general. In addition, to date, PBEE
provides unprecedented accuracy when compared to other
ML/DL methods available for the same purpose.

### ASSOCIATED CONTENT

Data Availability Statement
Data supporting the findings of this study are available within
the electronic Supporting Information. A Python pipeline
application is available to download and use from the GitHub
link: https://github.com/chavesejf/PBEE. In addition, PBEE
is also available as the Google Colab notebook at the following
link: https://colab.research.google.com/drive/1lu1d-
C0yRltKK_Wp-gF26oHcZSCiHaI8b.
*
sı Supporting Information
The Supporting Information is available free of charge at
https://pubs.acs.org/doi/10.1021/acs.jcim.4c01641.
The list of structures from the Protein Data Bank (PDB)
used in this work; the list of scoring functions and
Rosetta descriptors used to train and validate the
models; the RosettaScript used to calculate the
descriptors; and the theoretical description of SHAP
(PDF)

### AUTHOR INFORMATION

Corresponding Author
Roberto D. Lins -Aggeu Magalhães Institute, Oswaldo Cruz
Foundation, Recife 50670-465, Brazil; Department of
Fundamental Chemistry, Federal University of Pernambuco,
Recife 50670-901, Brazil;
orcid.org/0000-0002-3983-
8025; Email: roberto.neto@fiocruz.br
Authors
Elton J. F. Chaves -Aggeu Magalhães Institute, Oswaldo
Cruz Foundation, Recife 50670-465, Brazil;
orcid.org/
0000-0001-8573-2216
João Sartori -Laboratory for Applied Genomics and Bio-
Innovations, Oswaldo Cruz Foundation, Rio de Janeiro
21040-900, Brazil
Whendel M. Santos -Department of Fundamental
Chemistry, Federal University of Pernambuco, Recife 50670-
901, Brazil
Carlos H. B. Cruz -Institute of Structural and Molecular
Biology, University College London, London WC1E 6BT,
U.K.
Emmanuel N. Mhrous -Department of Computer Science,
Princeton University, Princeton, New Jersey 08544, United
States
Manassés F. Nacimento-Filho -Institute of Structural and
Molecular Biology, University College London, London
WC1E 6BT, U.K.
Matheus V. F. Ferraz -NEC OncoImmunity AS, Oslo 0349,
Norway;
orcid.org/0000-0002-6958-3115
Complete contact information is available at:
https://pubs.acs.org/10.1021/acs.jcim.4c01641
Author Contributions
E.J.F.C. has coded the pre- and postprocessing scripts to curate
the data sets, calculated the Rosetta-based properties, and
coded the PBEE pipeline. M.V.F.F., E.M.N., and M.F.N.-F.
have helped with coding and tested individual ML algorithms.
C.H.B.C. has helped to devise the RosettaScripts for
calculation of molecular quantities. J.S. has implemented the
PBEE to be used with PyRosetta, and WMS has ported the
code to be used within the Google Colab framework. R.D.L.
has conceptualized the idea, overseen all tasks, and has coded
the super learner algorithm. The manuscript was written
through contributions of all authors. All authors have given
approval to the final version of the manuscript.
Funding
The Article Processing Charge for the publication of this
research was funded by the Coordination for the Improvement
of Higher Education Personnel - CAPES (ROR identifier:
00x0ma614).
Notes
The authors declare the following competing financial
interest(s): The author Matheus Ferraz is employed by NEC
OncoImmunity AS. Otherwise, there are no conflict of
interests to declare.

### ACKNOWLEDGMENTS

This work was supported by grants from CNPq 303833/2022-
0 and INCT-FCx. Partial computer allocation was provided by
Figure 6. Feature importance is based on SHAP-values. The violin
plots employ color gradients to convey insights. Shades of red signify
higher values, indicating impactful features (Rosetta descriptors
shown on the y axis) associated with the outcome ( Gbind
0
). In
contrast, shades of blue represent lower values, highlighting features
that influence the result differently. (Description of the Rosetta
descriptors is available in Table S3.)
Journal of Chemical Information and Modeling
pubs.acs.org/jcim
Article
https://doi.org/10.1021/acs.jcim.4c01641
J. Chem. Inf. Model. 2025, 65, 2602-2609
2607
Downloaded from pubs.acs.org/jcisd8/article-pdf/65/5/2602/68457794/ci4c01641.pdf by guest on 19 October 2026

[[PAGE:7]]

the Brazilian Supercomputing Centre at LNCC. J.S. thanks
Lucas de Almeida Machado and Ana Carolina Ramos
Guimarães for valuable discussions on PyRosetta. R.L thanks
Ulisses Braga-Neto for invaluable discussion on ML regression
methods.

### ABBREVIATIONS

Gbind
0
, binding free energy; ANN, artificial neural network;
DL, deep learning; LDA, linear discriminant analysis; ML,
machine learning; PDB, protein data bank; PBEE, protein
binding energy estimator; PCA, principal component analysis;
R, Pearson correlation; RMSE, root-mean-square error; SHAP,
Shapley Additive exPlanations; RFdiffusion, Rosetta fold
diffusion; UMAP, uniform manifold approximation and
projection

### REFERENCES

(1) Loeffler, F. F.; Viana, I. F. T.; Fischer, N.; Coelho, D. F.; Silva, C.
S.; Purificacao, A. F., Jr.; Araujo, C.; Leite, B. H. S.; Duraes-Carvalho,
R.; Magalhaes, T.; et al. Identification of a Zika Ns2b Epitope as a
Biomarker for Severe Clinical Phenotypes. RSC Med. Chem. 2021, 12,
1525-1539.
(2) Viana, I. F. T.; Cruz, C. H. B.; Athayde, D.; Adan, W. C. S.;
Xavier, L. S. S.; Archer, M.; Lins, R. D. In Vitro Neutralisation of Zika
Virus by an Engineered Protein Targeting the Viral Envelope Fusion
Loop. Mol. Syst. Des. Eng. 2023, 8, 516-526.
(3) Chaves, E. J. F.; Coelho, D. F.; Cruz, C. H. B.; Moreira, E. G.;
Simoes, J. C. M.; Nascimento-Filho, M. J.; Lins, R. D. Structure-Based
Computational Design of Antibody Mimetics: Challenges and
Perspectives. FEBS Open Bio 2025, 15, 223-235.
(4) Lawrenz, M.; Baron, R.; Wang, Y.; McCammon, J. A.
Independent-Trajectory Thermodynamic Integration: A Practical
Guide to Protein-Drug Binding Free Energy Calculations Using
Distributed Computing. Methods Mol. Biol. 2012, 819, 469-486.
(5) Oostenbrink, C.; van Gunsteren, W. F. Free Energies of Binding
of Polychlorinated Biphenyls to the Estrogen Receptor from a Single
Simulation. Proteins 2004, 54, 237-246.
(6) Singh, N.; Warshel, A. Absolute Binding Free Energy
Calculations: On the Accuracy of Computational Scoring of
Protein-Ligand Interactions. Proteins 2010, 78, 1705-1723.
(7) Ries, B.; Alibay, I.; Anand, N. M.; Biggin, P. C.; Magarkar, A.
Automated Absolute Binding Free Energy Calculation Workflow for
Drug Discovery. J. Chem. Inf. Model. 2024, 64, 5357-5364.
(8) Wang, J.; Ishchenko, A.; Zhang, W.; Razavi, A.; Langley, D. A
highly accurate metadynamics-based Dissociation Free Energy
method to calculate protein-protein and protein-ligand binding
potencies. Sci. Rep. 2022, 12, 2024.
(9) Guo, Z.; Yamaguchi, R. Machine Learning Methods for Protein-
Protein Binding Affinity Prediction in Protein Design. Front.
Bioinform. 2022, 2, 1065703.
(10) Liu, X.; Luo, Y.; Li, P.; Song, S.; Peng, J. Deep Geometric
Representations for Modeling Effects of Mutations on Protein-Protein
Binding Affinity. PloS Comput. Biol. 2021, 17, No. e1009284.
(11) Rodrigues, C. H. M.; Pires, D. E. V.; Ascher, D. B. Mmcsm-Ppi:
Predicting the Effects of Multiple Point Mutations on Protein-Protein
Interactions. Nucleic Acids Res. 2021, 49, W417-W424.
(12) Romero-Molina, S.; Ruiz-Blanco, Y. B.; Mieres-Perez, J.;
Harms, M.; Munch, J.; Ehrmann, M.; Sanchez-Garcia, E. Ppi-Affinity:
A Web Tool for the Prediction and Optimization of Protein-Peptide
and Protein-Protein Binding Affinity. J. Proteome Res. 2022, 21,
1829-1841.
(13) Vangone, A.; Bonvin, A. M. Contacts-based prediction of
binding affinity in protein-protein complexes. Elife 2015, 4,
No. e07454.
(14) Wang, M.; Cang, Z.; Wei, G. W. A Topology-Based Network
Tree for the Prediction of Protein-Protein Binding Affinity Changes
Following Mutation. Nat. Mach. Intell. 2020, 2, 116-123.
(15) Wee, J.; Xia, K. Persistent spectral based ensemble learning
(PerSpect-EL) for protein-protein binding affinity prediction. Brief.
Bioinform. 2022, 23, bbac024.
(16) Shringari, S. R.; Giannakoulias, S.; Ferrie, J. J.; Petersson, E. J.
Rosetta Custom Score Functions Accurately Predict Deltadeltag of
Mutations at Protein-Protein Interfaces Using Machine Learning.
Chem. Commun. 2020, 56, 6774-6777.
(17) Kastritis, P. L.; Moal, I. H.; Hwang, H.; Weng, Z.; Bates, P. A.;
Bonvin, A. M.; Janin, J. A Structure-Based Benchmark for Protein-
Protein Binding Affinity. Protein Sci. 2011, 20, 482-491.
(18) Xue, L. C.; Rodrigues, J. P.; Kastritis, P. L.; Bonvin, A. M.;
Vangone, A. Prodigy: A Web Server for Predicting the Binding
Affinity of Protein-Protein Complexes. Bioinformatics 2016, 32,
3676-3678.
(19) Yang, Y. X.; Wang, P.; Zhu, B. T. Importance of Interface and
Surface Areas in Protein-Protein Binding Affinity Prediction: A
Machine Learning Analysis Based on Linear Regression and Artificial
Neural Network. Biophys. Chem. 2022, 283, 106762.
(20) Yang, Y. X.; Wang, P.; Zhu, B. T. Binding Affinity Prediction
for Antibody-Protein Antigen Complexes: A Machine Learning
Analysis Based on Interface and Surface Areas. J. Mol. Graphics
Modell. 2023, 118, 108364.
(21) Ferraz, M. V. F.; Neto, J. C. S.; Lins, R. D.; Teixeira, E. S. An
Artificial Neural Network Model to Predict Structure-Based Protein-
Protein Free Energy of Binding from Rosetta-Calculated Properties.
Phys. Chem. Chem. Phys. 2023, 25, 7257-7267.
(22) Leaver-Fay, A.; Tyka, M.; Lewis, S. M.; Lange, O. F.;
Thompson, J.; Jacak, R.; Kaufman, K.; Renfrew, P. D.; Smith, C. A.;
Sheffler, W.; Davis, I. W.; Cooper, S.; Treuille, A.; Mandell, D. J.;
Richter, F.; Ban, Y. E.; Fleishman, S. J.; Corn, J. E.; Kim, D. E.;
Lyskov, S.; Berrondo, M.; Mentzer, S.; Popovic, Z.; Havranek, J. J.;
Karanicolas, J.; Das, R.; Meiler, J.; Kortemme, T.; Gray, J. J.;
Kuhlman, B.; Baker, D.; Bradley, P. Rosetta3: An Object-Oriented
Software Suite for the Simulation and Design of Macromolecules.
Methods Enzymol. 2011, 487, 545-574.
(23) van der Laan, M. J.; Polley, E. C.; Hubbard, A. E. Super
Learner. Stat. Appl. Genet. Mol. Biol. 2007, 6, 25.
(24) Lawson, C. L.; Hanson, R. J. Solving Least Squares Problems;
Prentice-Hall: Englewood Cliffs, N. J., 1974; p 340.
(25) Lawson, C. L.; Hanson, R. J. Solving Least Squares Problems;
SIAM: Philadelphia, 1995; p 337.
(26) Vreven, T.; Moal, I. H.; Vangone, A.; Pierce, B. G.; Kastritis, P.
L.; Torchala, M.; Chaleil, R.; Jimenez-Garcia, B.; Bates, P. A.;
Fernandez-Recio, J.; Bonvin, A. M.; Weng, Z. Updates to the
Integrated Protein-Protein Interaction Benchmarks: Docking Bench-
mark Version 5 and Affinity Benchmark Version 2. J. Mol. Biol. 2015,
427, 3031-3041.
(27) Wang, R.; Fang, X.; Lu, Y.; Wang, S. The Pdbbind Database:
Collection of Binding Affinities for Protein-Ligand Complexes with
Known Three-Dimensional Structures. J. Med. Chem. 2004, 47,
2977-2980.
(28) Jankauskaite, J.; Jimenez-Garcia, B.; Dapkunas, J.; Fernandez-
Recio, J.; Moal, I. H. Skempi 2.0: An Updated Benchmark of Changes
in Protein-Protein Binding Energy, Kinetics and Thermodynamics
Upon Mutation. Bioinformatics 2019, 35, 462-469.
(29) Pavlovicz, R. E.; Park, H.; DiMaio, F. Efficient Consideration of
Coordinated Water Molecules Improves Computational Protein-
Protein and Protein-Ligand Docking Discrimination. PloS Comput.
Biol. 2020, 16, No. e1008103.
(30) Bogdanova, E. A.; Novoseletsky, V. N. Proban: Neural Network
Algorithm for Predicting Binding Affinity in Protein-Protein
Complexes. Proteins 2024, 92, 1127-1136.
(31) Sapozhnikov, Y.; Patel, J. S.; Ytreberg, F. M.; Miller, C. R.
Statistical Modeling to Quantify the Uncertainty of Foldx-Predicted
Protein Folding and Binding Stability. BMC Bioinf. 2023, 24, 426.
(32) Buss, O.; Rudat, J.; Ochsenreither, K. Foldx as Protein
Engineering Tool: Better Than Random Based Approaches? Comput.
Struct. Biotechnol. J. 2018, 16, 25-33.
Journal of Chemical Information and Modeling
pubs.acs.org/jcim
Article
https://doi.org/10.1021/acs.jcim.4c01641
J. Chem. Inf. Model. 2025, 65, 2602-2609
2608
Downloaded from pubs.acs.org/jcisd8/article-pdf/65/5/2602/68457794/ci4c01641.pdf by guest on 19 October 2026

[[PAGE:8]]

(33) Watson, J. L.; Juergens, D.; Bennett, N. R.; Trippe, B. L.; Yim,
J.; Eisenach, H. E.; Ahern, W.; Borst, A. J.; Ragotte, R. J.; Milles, L. F.;
Wicky, B. I. M.; Hanikel, N.; Pellock, S. J.; Courbet, A.; Sheffler, W.;
Wang, J.; Venkatesh, P.; Sappington, I.; Torres, S. V.; Lauko, A.; De
Bortoli, V.; Mathieu, E.; Ovchinnikov, S.; Barzilay, R.; Jaakkola, T. S.;
DiMaio, F.; Baek, M.; Baker, D. De Novo Design of Protein Structure
and Function with Rfdiffusion. Nature 2023, 620, 1089-1100.
(34) Lundberg, S. M.; Erion, G.; Chen, H.; DeGrave, A.; Prutkin, J.
M.; Nair, B.; Katz, R.; Himmelfarb, J.; Bansal, N.; Lee, S. I. From
Local Explanations to Global Understanding with Explainable Ai for
Trees. Nat. Mach. Intell. 2020, 2, 56-67.
Journal of Chemical Information and Modeling
pubs.acs.org/jcim
Article
https://doi.org/10.1021/acs.jcim.4c01641
J. Chem. Inf. Model. 2025, 65, 2602-2609
2609
Downloaded from pubs.acs.org/jcisd8/article-pdf/65/5/2602/68457794/ci4c01641.pdf by guest on 19 October 2026
