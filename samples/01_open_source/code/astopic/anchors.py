"""Anchor word selection via normalized PMI (Section 3.2)."""

from __future__ import annotations

import numpy as np


def normalized_pmi(cooc: np.ndarray, counts: np.ndarray, eps: float = 1e-9) -> np.ndarray:
    """Compute the normalized PMI score of every word from a co-occurrence matrix.

    nPMI_ij = log(p_ij / (p_i p_j)) / -log(p_ij)
    """
    total = cooc.sum()
    if total <= 0:
        return np.zeros(cooc.shape[0], dtype=float)
    p_ij = cooc / total
    p_i = counts / max(counts.sum(), eps)
    denom = -np.log(np.maximum(p_ij, eps))
    with np.errstate(divide="ignore", invalid="ignore"):
        npmi = np.log(np.maximum(p_ij, eps) / np.outer(p_i, p_i + eps)) / np.maximum(denom, eps)
    npmi = np.nan_to_num(npmi, nan=0.0, posinf=0.0, neginf=0.0)
    return npmi.sum(axis=1)


def select_anchors(cooc: np.ndarray, counts: np.ndarray, k: int, candidate_pool: int = 2000) -> np.ndarray:
    """Return the indices of the ``k`` selected anchor words."""
    scores = normalized_pmi(cooc, counts)
    pool = np.argsort(-scores)[: min(candidate_pool, len(scores))]
    return np.asarray(pool[:k], dtype=int)
