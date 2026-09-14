"""数值解析与比对工具（stdlib 实现，无第三方依赖）。"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

#: 匹配 12.3 / 0.812 / 85% / 1,234 / 3.2e-4 / -4.5 / ±0.3 / 1.2±0.1
_NUM_RE = re.compile(
    r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?%?"
)
_PM_RE = re.compile(r"([-+]?\d+(?:\.\d+)?)\s*(?:±|\+-|\+/-)\s*(\d+(?:\.\d+)?)")

#: 无意义的占位符（表格常见）
_PLACEHOLDERS = {"-", "--", "—", "n/a", "na", "nan", "none", "", "?", "tbd"}


def to_number(value: Any) -> Optional[float]:
    """把表格单元格字符串解析为浮点数；无法解析返回 ``None``。

    >>> to_number("85.2%")
    0.852
    >>> to_number("1,204")
    1204.0
    >>> to_number("1.2±0.1")
    1.2
    >>> to_number("n/a") is None
    True
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if not (isinstance(value, float) and math.isnan(value)) else None

    s = str(value).strip()
    if s.lower() in _PLACEHOLDERS:
        return None

    pm = _PM_RE.search(s)
    if pm:
        return float(pm.group(1))

    s = s.replace("−", "-").replace("–", "-").replace("—", "-")
    s = re.sub(r"\\[a-zA-Z]+", " ", s)          # LaTeX 宏，如 \textbf
    s = s.replace("$", "").replace("\\%", "%")
    s = re.sub(r"\s+", " ", s).strip()

    m = _NUM_RE.search(s)
    if not m:
        return None
    token = m.group(0)
    is_pct = token.endswith("%")
    token = token.rstrip("%").replace(",", "")
    # 表格里常见 "85.2±1.1" 已由上面处理；这里再兜底一次
    try:
        num = float(token)
    except ValueError:
        return None
    return num / 100.0 if is_pct else num


def extract_numbers(text: str, limit: int = 200) -> List[float]:
    """从自由文本中抽取所有数字（用于发现论文正文中的报告值）。"""
    out: List[float] = []
    for m in _NUM_RE.finditer(text or ""):
        n = to_number(m.group(0))
        if n is not None:
            out.append(n)
        if len(out) >= limit:
            break
    return out


def is_numeric_row(row: Iterable[Any], min_ratio: float = 0.6) -> bool:
    cells = [c for c in row if str(c).strip()]
    if not cells:
        return False
    hit = sum(1 for c in cells if to_number(c) is not None)
    return hit / len(cells) >= min_ratio


def rel_delta(reported: Optional[float], reproduced: Optional[float]) -> Optional[float]:
    """相对误差。分母取 |reported|，为 0 时退化为绝对差。"""
    if reported is None or reproduced is None:
        return None
    if reported == 0:
        return abs(reproduced)
    return abs(reproduced - reported) / abs(reported)


def classify(
    reported: Optional[float],
    reproduced: Optional[float],
    thresholds: Optional[Dict[str, float]] = None,
) -> Tuple[str, Optional[float], Optional[float], Optional[float]]:
    """返回 ``(status, abs_delta, rel_delta, _)``。

    status ∈ {pass, warn, fail, skipped}
    """
    th = thresholds or {"pass": 0.05, "warn": 0.20}
    if reported is None or reproduced is None:
        return "skipped", None, None, None

    rd = rel_delta(reported, reproduced)
    ad = abs(reproduced - reported)
    assert rd is not None
    if rd <= th.get("pass", 0.05):
        return "pass", ad, rd, None
    if rd <= th.get("warn", 0.20):
        return "warn", ad, rd, None
    return "fail", ad, rd, None


def sort_numbers(values: Iterable[float]) -> List[float]:
    return sorted(v for v in values if v is not None)


def summarize(values: Iterable[Optional[float]]) -> Dict[str, Any]:
    clean = [v for v in values if v is not None]
    if not clean:
        return {"n": 0, "min": None, "max": None, "mean": None}
    return {
        "n": len(clean),
        "min": min(clean),
        "max": max(clean),
        "mean": sum(clean) / len(clean),
    }
