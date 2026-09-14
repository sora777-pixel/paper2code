# AS-Topic reference implementation

Reference code for the paper **"Anchor-Spectral Topic Discovery for Short Texts"**.

This repository contains a minimal, dependency-light implementation of the
anchor-based spectral topic model (AS-Topic) described in Section 3, together
with the scripts that produced Table 2 and Table 3 of the paper.

## Layout

```
code/
├── requirements.txt
├── train.py              # 主入口：训练 + 评估 + 落盘结果
├── astopic/
│   ├── __init__.py
│   ├── anchors.py        # 归一化 PMI 锚词打分与单纯形体选择
│   └── propagate.py      # 非负最小二乘 + 指数梯度精修
├── data/
│   └── stackoverflow_toy.csv
└── results/
    ├── metrics.csv       # → 论文 Table 2
    └── ablation.csv      # → 论文 Table 3
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Reproduce

```bash
python train.py --dataset data/stackoverflow_toy.csv --k 20 --seed 0
```

All experiments in the paper use `--seed 0` and five repeated runs; the reported
numbers are the mean over runs. Result files are written to `results/`.

## Notes on reproducibility

* Vocabulary pruning threshold: `min_df=3`.
* Anchor pool size `M = 2000`, topics `K = 20` unless stated otherwise.
* Random seed is fixed to `0` for both anchor tie-breaking and K-Means
  initialization of the baselines.
* Evaluation uses the hungarian-matched accuracy implemented in `train.py`.
