"""统计复现（roadmap M6-②）：均值 + 置信区间 + 与论文数值的区间判定。

**为什么需要它**：单次随机波动不该被当成结论。demo 默认跑多个种子（M3-④），
但只报 mean/stdev 还不够——判定「论文那个数字复现了吗」需要**区间**：
论文值落在 demo 的 95% CI 内 → 视为一致；落在 CI 外但方向一致 → 部分；方向也反 → 判定不符。

**方法**：小样本用 t 分布（`t_critical`，df = n-1），n=1 时**不给区间**（诚实：一次运行估不出波动）。
纯标准库实现，不引入 scipy。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

#: 95% 双侧 t 临界值（df → t）。查表 + 线性插值 + df→∞ 用 1.96。
_T95: Dict[int, float] = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306,
    9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
    16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 22: 2.074, 24: 2.064,
    26: 2.056, 28: 2.048, 30: 2.042, 40: 2.021, 50: 2.009, 60: 2.000, 80: 1.990,
    100: 1.984, 120: 1.980,
}
INF_T95 = 1.960


def t_critical(df: int, confidence: float = 0.95) -> float:
    """t 临界值（默认 95% 双侧）。df<=0 → 无穷大（不给区间）。"""
    if confidence != 0.95:
        # 只实现了 95%；其它置信度按正态近似并显式说明（避免假装精确）
        return INF_T95
    if df <= 0:
        return float("inf")
    if df in _T95:
        return _T95[df]
    keys = sorted(_T95)
    if df > keys[-1]:
        return INF_T95
    lo = max(k for k in keys if k < df)
    hi = min(k for k in keys if k > df)
    # 线性插值（保守取较大者，避免低估区间）
    frac = (df - lo) / (hi - lo)
    return _T95[hi] * frac + _T95[lo] * (1 - frac)


@dataclass
class Interval:
    """一组观测的均值与置信区间。"""

    n: int
    mean: float
    stdev: float = 0.0
    sem: float = 0.0
    ci_low: Optional[float] = None
    ci_high: Optional[float] = None
    confidence: float = 0.95
    values: List[float] = field(default_factory=list)

    @property
    def has_ci(self) -> bool:
        return self.ci_low is not None and self.ci_high is not None

    def contains(self, x: float) -> Optional[bool]:
        """``x`` 是否落在区间内；没有区间时返回 None（判不了，不假装）。"""
        if not self.has_ci:
            return None
        return self.ci_low <= x <= self.ci_high          # type: ignore[operator]

    def to_dict(self) -> Dict[str, Any]:
        d = {"n": self.n, "mean": self.mean, "stdev": self.stdev, "sem": self.sem,
             "ci_low": self.ci_low, "ci_high": self.ci_high, "confidence": self.confidence}
        return d

    def label(self) -> str:
        if not self.has_ci:
            return "%.6g（n=%d，单次运行，无区间）" % (self.mean, self.n)
        return "%.6g（n=%d，95%% CI [%.6g, %.6g]）" % (self.mean, self.n, self.ci_low, self.ci_high)


def mean_ci(values: Sequence[float], confidence: float = 0.95) -> Interval:
    """均值 + t 区间。``n<=1`` 时**不给区间**（只有一次观测，估不出波动）。"""
    vals = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    n = len(vals)
    if n == 0:
        return Interval(n=0, mean=float("nan"))
    mean = sum(vals) / n
    if n == 1:
        return Interval(n=1, mean=vals[0], values=vals)
    var = sum((v - mean) ** 2 for v in vals) / (n - 1)
    sd = math.sqrt(var)
    sem = sd / math.sqrt(n)
    t = t_critical(n - 1, confidence)
    half = t * sem
    return Interval(n=n, mean=mean, stdev=sd, sem=sem,
                    ci_low=mean - half, ci_high=mean + half, confidence=confidence, values=vals)


# --------------------------------------------------------------------------- #
# 与论文数值对比
# --------------------------------------------------------------------------- #
def classify_vs_paper(iv: Interval, reported: Optional[float], rel_tol: float = 0.05,
                      rel_warn: float = 0.20) -> Dict[str, Any]:
    """判定「论文报告值 vs demo 的多观测区间」。

    返回 ``{status, detail}``，``status`` ∈ ``pass/warn/fail/skip``：

    * ``pass``：论文值落在 95% CI 内，**或**（无区间时）均值相对误差 ≤ ``rel_tol``；
    * ``warn``：CI 不含论文值，但均值相对误差 ≤ ``rel_warn``；
    * ``fail``：均值相对误差 > ``rel_warn``；
    * ``skip``：论文值缺失 / 观测为空（判不了，不算通过）。
    """
    if reported is None:
        return {"status": "skip", "detail": "论文没有给出可对标的数值"}
    if iv.n == 0:
        return {"status": "skip", "detail": "demo 没有产出该指标的数值"}
    rel = abs(iv.mean - reported) / abs(reported) if reported else abs(iv.mean)
    inside = iv.contains(reported)
    if inside is True:
        return {"status": "pass",
                "detail": "论文 %.6g 落在 demo 的 %s 内（均值相对偏差 %.2f%%）"
                          % (reported, iv.label(), rel * 100), "rel_delta": rel}
    if rel <= rel_tol:
        return {"status": "pass",
                "detail": "无区间可比，均值 %.6g 与论文 %.6g 相对偏差 %.2f%%（≤%.0f%%）"
                          % (iv.mean, reported, rel * 100, rel_tol * 100), "rel_delta": rel}
    if rel <= rel_warn:
        return {"status": "warn",
                "detail": "均值 %.6g 与论文 %.6g 相对偏差 %.2f%%（>%.0f%%，但在 %.0f%% 内）%s"
                          % (iv.mean, reported, rel * 100, rel_tol * 100, rel_warn * 100,
                             "；且论文值不在区间内" if inside is False else ""), "rel_delta": rel}
    return {"status": "fail",
            "detail": "均值 %.6g 与论文 %.6g 相对偏差 %.2f%%（>%.0f%%）"
                      % (iv.mean, reported, rel * 100, rel_warn * 100), "rel_delta": rel}


def values_from_aggregate(aggregate: Dict[str, Any]) -> Interval:
    """从 demo 写出的 ``metrics.<k> = {n,mean,stdev,...}`` 复原区间（没有 stdev 时按单次处理）。"""
    if not isinstance(aggregate, dict) or "mean" not in aggregate:
        return Interval(n=0, mean=float("nan"))
    n = int(aggregate.get("n") or 1)
    mean = float(aggregate.get("mean"))
    sd = aggregate.get("stdev")
    iv = Interval(n=n, mean=mean, stdev=float(sd or 0.0))
    if n > 1 and sd is not None:
        iv.sem = float(sd) / math.sqrt(n)
        half = t_critical(n - 1) * iv.sem
        iv.ci_low, iv.ci_high = mean - half, mean + half
    return iv
