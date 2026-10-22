"""性质断言（roadmap M1-④）：从论文的**可检验声明**生成断言，在 demo 的真实产物上求值。

与 M1-③「计划自检」的分工：
* 计划自检管**写码前**的诚实性（计划与论文对不对得上）；
* 本模块管**跑完之后**的可证伪性 —— 论文里那些"应该成立"的断言，在 demo 输出上真的成立吗？

思路沿用 `reproduce/checks.py`：**只在论文/表头自己声明了的时候才断言**，
不做"猜"（例如只有表头写 share/proportion 才校验求和为 1，`Hit ratio` 不会被误判）。

三条纪律：

1. **fail 才算失败**；`skip`（键不存在、不是数值）**不等于通过**，必须如实列出；
2. 断言必须**可复算**（纯函数求值，不起模型判断）；
3. 离线也要能用：没有 LLM 时从论文表格派生确定性断言，永不空转。
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import numutil
from ..reproduce import checks, dataset
from .llmio import LLMUnavailable, ask_json
from .textio import describe_results, get_path

KINDS = {"all_finite", "non_negative", "bounds", "sum_equals", "monotonic", "non_empty"}
_EPS = 1e-9


# --------------------------------------------------------------------------- #
# 取数
# --------------------------------------------------------------------------- #
def _numbers(v: Any) -> Optional[List[float]]:
    """把任意 JSON 值抽成数值序列；抽不出数值返回 None（→ skip）。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return [float(v)]
    if isinstance(v, list):
        nums = [float(x) for x in v if isinstance(x, (int, float)) and not isinstance(x, bool)]
        return nums or None
    if isinstance(v, dict):
        for k in ("values", "y", "x", "data", "series"):
            if k in v:
                got = _numbers(v[k])
                if got:
                    return got
    return None


def _walk_numbers(obj: Any) -> List[float]:
    out: List[float] = []
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_walk_numbers(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_walk_numbers(v))
    return out


# --------------------------------------------------------------------------- #
# 求值
# --------------------------------------------------------------------------- #
def _res(claim: Dict[str, Any], status: str, detail: str) -> Dict[str, Any]:
    return {"claim": claim, "status": status, "detail": detail}


def evaluate_claim(claim: Dict[str, Any], metrics: Dict[str, Any]) -> Dict[str, Any]:
    kind = claim.get("kind")
    if kind not in KINDS:
        return _res(claim, "skip", "未知断言类型：%s" % kind)

    key = claim.get("key")
    if kind == "all_finite" and not key:
        nums = _walk_numbers(metrics)
        if not nums:
            return _res(claim, "skip", "产物里没有任何数值")
        bad = [n for n in nums if not math.isfinite(n)]
        return _res(claim, "fail" if bad else "pass",
                    "共 %d 个数值，" % len(nums) + ("存在 NaN/Inf" if bad else "全部有限"))

    v = None
    if key:
        try:
            v = get_path(metrics, key)
        except (KeyError, TypeError):
            v = None
    if key and v is None:
        return _res(claim, "skip", "产物里没有键 `%s`（demo 未产出该指标）" % key)

    # `non_empty` 判的是"有没有内容"，不要求是数值序列（如整张表的 columns 字典）
    if kind == "non_empty":
        if isinstance(v, (dict, list, str)):
            return _res(claim, "pass" if len(v) else "fail",
                        "非空（%d 项）" % len(v) if len(v) else "内容为空")
        return _res(claim, "skip", "`%s` 不是可判空的容器或序列" % key)

    nums = _numbers(v)
    if nums is None:
        return _res(claim, "skip", "`%s` 不是数值序列，无法断言" % key)

    if kind == "all_finite":
        bad = [n for n in nums if not math.isfinite(n)]
        return _res(claim, "fail" if bad else "pass",
                    "共 %d 个数值，" % len(nums) + ("存在 NaN/Inf" if bad else "全部有限"))

    if kind == "non_negative":
        bad = [n for n in nums if n < 0]
        return _res(claim, "fail" if bad else "pass",
                    "共 %d 个值，" % len(nums) + ("存在 %d 个负值（%s）" % (len(bad), bad[:3]) if bad else "全部非负"))

    if kind == "bounds":
        lo, hi = claim.get("min"), claim.get("max")
        problems = []
        if lo is not None and min(nums) < lo - _EPS:
            problems.append("最小 %.6g < 下界 %.6g" % (min(nums), lo))
        if hi is not None and max(nums) > hi + _EPS:
            problems.append("最大 %.6g > 上界 %.6g" % (max(nums), hi))
        return _res(claim, "fail" if problems else "pass",
                    "；".join(problems) if problems else
                    "最小值 %.6g、最大值 %.6g（区间 [%s, %s]）" % (min(nums), max(nums), lo, hi))

    if kind == "sum_equals":
        target = float(claim.get("target", 1.0))
        tol = float(claim.get("tol", 0.05))
        total = sum(nums)
        rel = abs(total - target) / abs(target) if target else abs(total)
        return _res(claim, "fail" if rel > tol else "pass",
                    "实测合计 %.6g（期望 %.6g，相对偏差 %.4f，容差 %.4f）" % (total, target, rel, tol))

    if kind == "monotonic":
        direction = str(claim.get("direction") or "decreasing")
        strict = bool(claim.get("strict"))
        scale = max((abs(n) for n in nums), default=1.0)
        eps = _EPS * scale
        bad = -1
        for i in range(len(nums) - 1):
            a, b = nums[i], nums[i + 1]
            if direction == "increasing":
                ok = (b > a + eps) if strict else (b >= a - eps)
            else:
                ok = (b < a - eps) if strict else (b <= a + eps)
            if not ok:
                bad = i
                break
        if bad < 0:
            return _res(claim, "pass", "%d 个点按 %s 单调" % (len(nums), "递增" if direction == "increasing" else "递减"))
        return _res(claim, "fail", "第 %d→%d 个点破坏 %s 单调：%.6g → %.6g"
                    % (bad + 1, bad + 2, "递增" if direction == "increasing" else "递减", nums[bad], nums[bad + 1]))

    return _res(claim, "skip", "未实现的断言类型")


def evaluate(claims: Sequence[Dict[str, Any]], metrics: Dict[str, Any]) -> Dict[str, Any]:
    results = [evaluate_claim(c, metrics) for c in claims]
    n_fail = sum(1 for r in results if r["status"] == "fail")
    n_pass = sum(1 for r in results if r["status"] == "pass")
    n_skip = sum(1 for r in results if r["status"] == "skip")
    first = next((r for r in results if r["status"] == "fail"), None)
    return {
        "failed": bool(n_fail),
        "n_pass": n_pass, "n_fail": n_fail, "n_skip": n_skip,
        "first_failure": ("%s：%s" % (first["claim"].get("desc") or first["claim"].get("key"), first["detail"])) if first else "",
        "results": results,
    }


# --------------------------------------------------------------------------- #
# 断言来源一：论文表格（确定性，永远可用）
# --------------------------------------------------------------------------- #
def table_claims(paper: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for t in getattr(paper, "tables", []) or []:
        if not t.rows:
            continue
        recs = dataset.table_to_records(t)
        cols = list(recs[0].keys()) if recs else []
        for c in cols:
            vals = [numutil.to_number(r.get(c)) for r in recs]
            good = [v for v in vals if v is not None]
            if len(good) < 2:
                continue
            key = "tables.%s.columns.%s.values" % (t.id, c)
            if checks.SHARE_COL_RE.search(str(c)) and max(good) - min(good) > _EPS:
                # 量纲按列自身的尺度定：带 "%" 的单元格会被 numutil 归一成 0..1（目标 1.0），
                # 写成 "40" 这类纯数字则是百分数尺度（目标 100）。否则断言会出现假 fail。
                target = 100.0 if max(good) > 1.5 else 1.0
                out.append({"kind": "sum_equals", "key": key, "target": target, "tol": 0.05,
                            "desc": "论文表 %s 的「%s」声明为占比列，应求和为 %s"
                                    % (t.id, c, "100%（即 1.0）" if target == 1.0 else "100"),
                            "source": "table"})
            if checks.COUNT_COL_RE.search(str(c)):
                out.append({"kind": "non_negative", "key": key,
                            "desc": "论文表 %s 的计数列「%s」不应出现负值" % (t.id, c),
                            "source": "table"})
        if t.rows:
            out.append({"kind": "non_empty", "key": "tables.%s.columns" % t.id,
                        "desc": "论文表 %s 的数值应被 demo 回放出来" % t.id, "source": "table"})
    return out


# --------------------------------------------------------------------------- #
# 断言来源二：LLM 从论文的「可检验声明」提取
# --------------------------------------------------------------------------- #
def llm_claims(provider: Any, ctx: str, metric_lines: List[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    from . import prompts

    data = ask_json(provider, "mvp_claims", prompts.CLAIMS_SYS, prompts.claims_user(ctx, metric_lines))
    claims: List[Dict[str, Any]] = []
    dropped: List[str] = []
    for c in (data.get("claims") or [])[:20] if isinstance(data, dict) else []:
        if not isinstance(c, dict):
            continue
        kind = str(c.get("kind") or "")
        key = str(c.get("key") or "").strip()
        if kind not in KINDS or not key:
            dropped.append("%s(%s)" % (kind or "?", key or "?"))
            continue
        item = {"kind": kind, "key": key, "desc": str(c.get("desc") or ""), "quote": str(c.get("quote") or ""),
                "source": "llm"}
        for numk in ("min", "max", "target", "tol"):
            if isinstance(c.get(numk), (int, float)):
                item[numk] = float(c[numk])
        if c.get("direction") in ("increasing", "decreasing"):
            item["direction"] = c["direction"]
        if c.get("strict"):
            item["strict"] = True
        claims.append(item)
    return claims, dropped


# --------------------------------------------------------------------------- #
# 编排
# --------------------------------------------------------------------------- #
#: 公式断言的语义线索（离线无 LLM，只能靠公式旁的文字解释）
_NORM_HINTS = ("probabilit", "normaliz", "归一", "fraction", "占比", "份额",
               "share", "比率", "ratio", "百分比")
_AT_MOST_ONE = ("\u2264 1", "\u22641", "<= 1", "<=1", "不超过 1", "不超过1", "at most 1")
_NONNEG_HINTS = ("non-negative", "nonnegative", "非负", "\u2265 0", ">= 0", "\u22650")
_SUM_HINTS = ("sum", "求和", "总和", "\u2211", "\\sum", "normaliz", "归一")


def formula_claims(paper: Any, known_keys: Sequence[str] = ()) -> List[Dict[str, Any]]:
    """把编号公式的**文字解释**变成数值断言（roadmap M3-③）。

    离线没有 LLM，所以只做**机械化可判**的那一类：归一化/占比 ⇒ 落在 [0,1]；
    「非负」⇒ 非负；「求和/归一」⇒ 求和为 1。断言的**目标键**由公式文字与
    demo 实际产出的指标键做词重叠锚定；锚不上时仍然发出断言（``key=None``），
    它会在产物上如实记为 ``skip``，从而在报告里暴露「这条公式还没接进 demo」，
    而不是悄悄丢掉。
    """
    out: List[Dict[str, Any]] = []
    for f in getattr(paper, "formulas", []) or []:
        blob = ("%s %s" % (getattr(f, "explanation", "") or "",
                           getattr(f, "expression", "") or "")).lower()
        if not blob.strip():
            continue
        key = _anchor_key(blob, known_keys)
        base = {"desc": "公式 (%s) 的性质" % (getattr(f, "number", "") or "?"),
                "quote": (getattr(f, "expression", "") or "")[:160],
                "source": "formula"}
        if any(h in blob for h in _AT_MOST_ONE + _NORM_HINTS):
            claim = {"kind": "bounds", "key": key, "min": 0.0, "max": 1.0, **base}
        elif any(h in blob for h in _NONNEG_HINTS):
            claim = {"kind": "non_negative", "key": key, **base}
        elif any(h in blob for h in _SUM_HINTS):
            claim = {"kind": "sum_equals", "key": key, "target": 1.0, "tol": 0.05, **base}
        else:
            continue  # 无法机械化判定的公式：留给 LLM 路径
        out.append(claim)
    return out


def _anchor_key(blob: str, known_keys: Sequence[str]) -> Optional[str]:
    """把公式文字锚定到 demo 的一个指标键（词重叠，取重合最多者）。

    指标键用下划线（``metrics.hit_rate_c16``）而公式文字用空格，所以两侧都要
    先把 ``_`` 归一成空格再分词，否则 ``hit_rate_c`` 与 ``hit``/``rate`` 永远对不上。
    """
    def words(text: str) -> set:
        return set(re.findall(r"[a-z]{3,}", str(text).lower().replace("_", " ")))

    blob_words = words(blob)
    if not blob_words:
        return None
    best, best_score = None, 0
    for k in known_keys or ():
        score = len(blob_words & words(k))
        if score > best_score:
            best, best_score = k, score
    return best


def evaluate_after_demo(paper: Any, provider: Any, ctx: str, demo_dir: Path,
                        llm_mode: bool) -> Dict[str, Any]:
    """在 demo 的真实产物上求值；任何异常都不应打断流水线（由调用方兜底）。"""
    mp = Path(demo_dir) / "results" / "metrics.json"
    if not mp.exists():
        return {"failed": False, "n_pass": 0, "n_fail": 0, "n_skip": 0, "results": [],
                "note": "demo 没有产出 results/metrics.json（该问题已在「运行与校验」环节处理）"}
    metrics = json.loads(mp.read_text(encoding="utf-8"))

    known_keys = describe_results(metrics)
    claims = table_claims(paper)
    # M3-③：编号公式的文字解释 → 数值断言（与 M1-④ 的表格断言合并去重）
    for c in formula_claims(paper, known_keys):
        sig = json.dumps(c, sort_keys=True)
        if sig not in {json.dumps(x, sort_keys=True) for x in claims}:
            claims.append(c)
    warnings: List[str] = []
    if llm_mode:
        try:
            extra, dropped = llm_claims(provider, ctx, describe_results(metrics))
            known = {json.dumps(c, sort_keys=True) for c in claims}
            for c in extra:
                sig = json.dumps(c, sort_keys=True)
                if sig not in known:
                    claims.append(c)
                    known.add(sig)
            if dropped:
                warnings.append("LLM 给出的 %d 条断言不合规已丢弃：%s" % (len(dropped), "、".join(dropped[:3])))
        except (LLMUnavailable, ValueError) as exc:
            warnings.append("可检验声明的 LLM 提取失败，仅使用论文表格派生的确定性断言：%s" % exc)

    outcome = evaluate(claims, metrics)
    outcome["warnings"] = warnings
    outcome["keys"] = describe_results(metrics)
    return outcome
