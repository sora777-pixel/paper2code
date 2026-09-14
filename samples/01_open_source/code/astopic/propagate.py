"""Non-negative least-squares propagation to the full vocabulary (Section 3.2)."""

from __future__ import annotations

import numpy as np


def nonneg_least_squares(q: np.ndarray, anchors: np.ndarray, iters: int = 200, lr: float = 0.5) -> np.ndarray:
    """Projective gradient descent with non-negativity and simplex constraints."""
    a = q[anchors]
    w = np.ones_like(a, dtype=float) / max(len(a), 1)
    for _ in range(iters):
        grad = 2.0 * a * (a @ w - q) @ a.T
        if np.ndim(grad) == 0:
            grad = np.full_like(w, float(grad))
        w = w - lr * np.asarray(grad).ravel()[: len(w)]
        w = np.maximum(w, 0.0)
        s = w.sum()
        if s > 0:
            w = w / s
    return w


def exponentiated_gradient(q: np.ndarray, w: np.ndarray, steps: int = 50, eta: float = 0.1) -> np.ndarray:
    """One run of exponentiated-gradient refinement (multiplicative updates)."""
    for _ in range(steps):
        err = q - w
        w = w * np.exp(eta * err)
        s = w.sum()
        if s > 0:
            w = w / s
    return w


def recover_topic_word(q: np.ndarray, anchors: np.ndarray, refine: bool = True) -> np.ndarray:
    w = nonneg_least_squares(q, anchors)
    if refine:
        w = exponentiated_gradient(q, w)
    s = w.sum()
    return w / s if s > 0 else w
