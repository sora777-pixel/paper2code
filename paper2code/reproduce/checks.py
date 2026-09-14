"""复现校验。

两类校验，对应两种复现深度：

**数值对标（情形 A）** — 仓库产出结果 vs 论文报告值，逐格计算相对误差。
由 :mod:`paper2code.reproduce.compare` 负责。

**内部一致性 / 性质校验（情形 B、C）** — 没有可比对的“真值”时，退而验证
论文自身数据是否自洽、算法输出是否满足其应有性质。这是本工具对「无代码论文」
给出的务实答案：不假装能对标指标，而是给出**可验证、可证伪**的检查项。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import numutil
from ..models import Algorithm, Paper, Table
from . import dataset


@dataclass
class CheckResult:
    name: str
    scope: str = ""
    status: str = "skipped"  # pass | warn | fail | skipped
    detail: str = ""
    expected: Optional[float] = None
    actual: Optional[float] = None
    rel_delta: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "scope": self.scope, "status": self.status,
            "detail": self.detail, "expected": self.expected, "actual": self.actual,
            "rel_delta": None if self.rel_delta is None else round(self.rel_delta, 5),
        }


_TOTAL_RE = re.compile(
    r"^\s*(?:total|overall|sum|average|avg|mean|weighted\s+average|"
    r"汇总|总计|合计|平均|总体|总和)\b",
    re.IGNORECASE,
)
_OURS_RE = re.compile(r"(ours?|our\s|ourselves|本文|我们|本方法|proposed|this\s+work)", re.IGNORECASE)
#: 只有当**表头**声明这是一列「占比」时才做求和校验。
#: 刻意不包含裸的 "ratio"：命中率 / 比值类的列不是占比，不会求和为 1。
_SHARE_RE = re.compile(
    r"(share|proportion|percent|pct|distribution|composition|fraction|"
    r"占比|比例|份额|分布|比重|构成)",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# 表格一致性
# --------------------------------------------------------------------------- #
def run_table_checks(paper: Paper, thresholds: Optional[Dict[str, float]] = None) -> List[CheckResult]:
    out: List[CheckResult] = []
    for table in paper.tables:
        if not table.rows:
            continue
        out.extend(_check_percentage_sums(table, thresholds))
        out.extend(_check_total_rows(table, thresholds))
        out.extend(_check_best_row_claim(table, thresholds))
        out.extend(_check_rank_column(table))
        out.extend(_check_non_negative_counts(table))
    return out


def _num_matrix(table: Table) -> Tuple[List[str], List[List[Optional[float]]]]:
    recs = dataset.table_to_records(table)
    cols = list(recs[0].keys()) if recs else []
    mat = [[numutil.to_number(r.get(c)) for c in cols] for r in recs]
    return cols, mat


def _check_percentage_sums(table: Table, thresholds: Optional[Dict[str, float]]) -> List[CheckResult]:
    cols, mat = _num_matrix(table)
    if not mat:
        return []
    th = thresholds or {"pass": 0.05, "warn": 0.20}
    out: List[CheckResult] = []

    for ci, name in enumerate(cols):
        # 只校验「表头自声明为占比」的列，避免把 0-1 量纲的指标列误判为占比
        if not _SHARE_RE.search(str(name)):
            continue
        vals = [row[ci] for row in mat]
        good = [v for v in vals if v is not None]
        if len(good) < 2:
            continue
        lo, hi = min(good), max(good)
        if hi - lo < 1e-9:
            continue
        # 无论原文写 "34.2%" 还是 "0.342"，numutil 都会归一化成 0..1 的分数，
        # 因此统一以「和 ≈ 1.0」为期望值。
        target = 1.0
        total = sum(good)
        rd = abs(total - target) / target
        status = "pass" if rd <= th["pass"] else ("warn" if rd <= th["warn"] else "fail")
        out.append(
            CheckResult(
                name="占比列求和",
                scope=f"{table.id}.{name}",
                status=status,
                detail=(
                    f"表头声明为占比列，实测合计 {total:.4f}（≈{total*100:.2f}%），"
                    f"期望 1.0000（100%），{len(good)} 个有效值"
                ),
                expected=target,
                actual=total,
                rel_delta=rd,
            )
        )
    return out


def _check_total_rows(table: Table, thresholds: Optional[Dict[str, float]]) -> List[CheckResult]:
    cols, mat = _num_matrix(table)
    # 「汇总行」只在「明细行 × 指标列」形态的交叉表里才有意义。
    # 两列的 (Metric, Value) 表里出现一行 "Total requests" 只是普通数据行，
    # 强行套用汇总校验会产生大量假阳性，所以这里要求至少 3 列。
    if not mat or len(mat) < 3 or len(cols) < 3:
        return []

    th = thresholds or {"pass": 0.05, "warn": 0.20}
    out: List[CheckResult] = []

    for ri, row in enumerate(table.rows):
        label = str(row[0]) if row else ""
        if not _TOTAL_RE.search(label):
            continue
        for ci in range(1, len(cols)):
            reported = mat[ri][ci]
            if reported is None:
                continue
            others = [mat[r][ci] for r in range(len(mat)) if r != ri and mat[r][ci] is not None]
            if len(others) < 2:
                continue
            s = sum(others)  # type: ignore[arg-type]
            m = s / len(others)
            best, kind = min(
                ((abs(reported - s) / max(abs(reported), 1e-9), "求和"),
                 (abs(reported - m) / max(abs(reported), 1e-9), "求均值")),
                key=lambda kv: kv[0],
            )
            status = "pass" if best <= th["pass"] else ("warn" if best <= th["warn"] else "fail")
            out.append(
                CheckResult(
                    name="汇总行校验",
                    scope=f"{table.id}.{cols[ci]}",
                    status=status,
                    detail=f"「{label.strip()}」列值为 {reported:.6g}，与其余行{kind} {s if kind=='求和' else m:.6g} 相对偏差 {best*100:.2f}%",
                    expected=s if kind == "求和" else m,
                    actual=reported,
                    rel_delta=best,
                )
            )
    return out


def _check_best_row_claim(table: Table, thresholds: Optional[Dict[str, float]]) -> List[CheckResult]:
    """若表中存在「本文/ours」行，检查其在主要指标列上是否确实最优。"""
    cols, mat = _num_matrix(table)
    if not mat:
        return []

    out: List[CheckResult] = []
    ours_idx = [ri for ri, row in enumerate(table.rows) if row and _OURS_RE.search(str(row[0]))]
    if not ours_idx:
        return []

    ri = ours_idx[0]
    for ci in range(1, len(cols)):
        col_vals = [mat[r][ci] for r in range(len(mat)) if mat[r][ci] is not None]
        mine = mat[ri][ci]
        if mine is None or len(col_vals) < 2:
            continue
        mx = max(col_vals)  # type: ignore[arg-type]
        mn = min(col_vals)  # type: ignore[arg-type]
        if abs(mx - mn) < 1e-12:
            continue
        is_best = abs(mine - mx) < 1e-9
        is_worst = abs(mine - mn) < 1e-9
        out.append(
            CheckResult(
                name="最优性声明",
                scope=f"{table.id}.{cols[ci]}",
                status="pass" if is_best else ("warn" if not is_worst else "fail"),
                detail=(
                    f"「{str(table.rows[ri][0]).strip()}」在该列为 {mine:.6g}，"
                    f"全表最优 {mx:.6g}、最差 {mn:.6g} → "
                    + ("确为最优" if is_best else ("处于最差" if is_worst else "非最优"))
                ),
                expected=mx,
                actual=mine,
                rel_delta=numutil.rel_delta(mx, mine),
            )
        )
    return out


def _check_rank_column(table: Table) -> List[CheckResult]:
    cols, mat = _num_matrix(table)
    for ci, name in enumerate(cols):
        if "rank" not in str(name).lower() and "排名" not in str(name):
            continue
        vals = [mat[r][ci] for r in range(len(mat)) if mat[r][ci] is not None]
        n = len(vals)
        if n < 2:
            continue
        ok = sorted(vals) == list(range(1, n + 1))
        return [
            CheckResult(
                name="排名列合法性",
                scope=f"{table.id}.{name}",
                status="pass" if ok else "warn",
                detail=f"排名列取值 {vals}，" + ("构成 1..n 排列" if ok else "并非 1..n 的排列（可能为并列或分段排名）"),
            )
        ]
    return []


_COUNT_RE = re.compile(r"(count|number|num_|_num|\bsize\b|\bn\b|n_|数量|样本数|计数|个数)", re.IGNORECASE)


def _check_non_negative_counts(table: Table) -> List[CheckResult]:
    cols, mat = _num_matrix(table)
    out: List[CheckResult] = []
    for ci, name in enumerate(cols):
        # 用正则而非裸子串匹配，避免把 "NMI" / "attention" 这类列误判为计数列
        if not _COUNT_RE.search(str(name)):
            continue
        vals = [mat[r][ci] for r in range(len(mat)) if mat[r][ci] is not None]
        if not vals:
            continue
        bad = [v for v in vals if v < 0]
        out.append(
            CheckResult(
                name="计数列非负",
                scope=f"{table.id}.{name}",
                status="pass" if not bad else "fail",
                detail=f"计数列 {name} 共 {len(vals)} 个值，" + ("全部非负" if not bad else f"存在 {len(bad)} 个负值"),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# 算法性质校验（情形 C）
# --------------------------------------------------------------------------- #
_INTENT_RULES: List[Tuple[str, Sequence[str]]] = [
    # 注意顺序：topk 必须排在 sort 之前。
    # 因为 Top-K 伪代码里常出现 "sort_descending" 这类原语，先匹配 sort 会把
    # Top-K 误判为排序，从而选错性质校验。
    ("topk", ("top-k", "top k", "topk", "argmax", "前 k", "最大的 k", "select the largest", "空 min-heap", "min-heap")),
    ("sort", ("sort", "sorting", "排序", "merge sort", "quicksort", "bubble")),
    ("sum", ("sum", "summation", "求和", "累加", "average", "mean", "均值")),
    ("count", ("count", "frequency", "histogram", "计数", "频次")),
    ("normalize", ("softmax", "normalize", "归一化", "概率分布")),
    ("distance", ("distance", "similarity", "euclidean", "cosine", "距离", "相似度")),
    ("matmul", ("matrix multipl", "matmul", "矩阵乘")),
]


def infer_algorithm_intent(algo: Algorithm) -> str:
    text = (algo.caption + "\n" + algo.source).lower()
    for intent, keys in _INTENT_RULES:
        if any(k in text for k in keys):
            return intent
    return "unknown"


def run_property_checks(
    algo: Algorithm,
    inputs: Dict[str, Any],
    outputs: Dict[str, Any],
) -> List[CheckResult]:
    """按算法意图做性质校验；输出为 ``{变量名: 值}``。"""
    intent = infer_algorithm_intent(algo)
    out: List[CheckResult] = []
    scope = f"algo:{algo.id}"

    def first_output() -> Any:
        for k, v in outputs.items():
            if k.startswith("_"):
                continue
            if isinstance(v, (list, tuple)) or isinstance(v, (int, float)):
                return v
        return None

    def first_input() -> Any:
        for v in inputs.values():
            if isinstance(v, list) and v:
                return v
        return None

    if intent == "sort":
        res = outputs.get("_result") or first_output()
        if isinstance(res, (list, tuple)) and len(res) >= 2:
            ok = all(res[i] <= res[i + 1] for i in range(len(res) - 1))
            out.append(
                CheckResult(
                    name="输出有序性", scope=scope, status="pass" if ok else "fail",
                    detail=f"排序算法输出长度 {len(res)}，" + ("满足非降序" if ok else "存在逆序"),
                )
            )
            src = first_input()
            if isinstance(src, list) and len(src) == len(res):
                same = sorted(src) == list(res)
                out.append(
                    CheckResult(
                        name="排列不变性", scope=scope, status="pass" if same else "fail",
                        detail="输出与输入元素多重集一致" if same else "输出元素与输入不一致（可能丢失/重复）",
                    )
                )

    elif intent == "topk":
        res = outputs.get("_result") or first_output()
        src = first_input()
        if isinstance(res, (list, tuple)) and isinstance(src, list) and res and src:
            try:
                k = len(res)
                expect = set(sorted(src, key=lambda x: -x)[:k]) if all(isinstance(x, (int, float)) for x in src) else set(src[:k])
                got = set(res)
                ok = got == expect
                out.append(
                    CheckResult(
                        name="Top-K 正确性", scope=scope, status="pass" if ok else "fail",
                        detail=f"k={k}，" + ("选中元素与全量排序 Top-K 一致" if ok else "Top-K 结果与期望不一致"),
                    )
                )
            except Exception:
                pass

    elif intent in ("sum", "count"):
        res = outputs.get("_result") or first_output()
        src = first_input()
        if isinstance(res, (int, float)) and isinstance(src, list) and src:
            if intent == "sum" and all(isinstance(x, (int, float)) for x in src):
                expect = sum(src)
                rd = numutil.rel_delta(float(expect), float(res))
                out.append(
                    CheckResult(
                        name="求和一致性", scope=scope,
                        status="pass" if (rd is not None and rd <= 1e-6) else "warn",
                        detail=f"输出 {res}，输入求和 {expect:.6g}",
                        expected=float(expect), actual=float(res), rel_delta=rd,
                    )
                )
            if intent == "count" and isinstance(res, dict):
                out.append(
                    CheckResult(
                        name="计数守恒", scope=scope,
                        status="pass" if sum(res.values()) == len(src) else "fail",
                        detail=f"各桶计数之和 {sum(res.values())}，输入元素数 {len(src)}",
                    )
                )

    elif intent == "normalize":
        res = outputs.get("_result") or first_output()
        if isinstance(res, (list, tuple)) and res and all(isinstance(x, (int, float)) for x in res):
            s = sum(res)
            rd = abs(s - 1.0)
            out.append(
                CheckResult(
                    name="归一化守恒", scope=scope,
                    status="pass" if rd <= 1e-6 else "warn",
                    detail=f"输出元素之和 = {s:.8f}（期望 1.0）",
                    expected=1.0, actual=s, rel_delta=rd,
                )
            )

    if not out:
        out.append(
            CheckResult(
                name="性质校验", scope=scope, status="skipped",
                detail=f"未能从算法意图（{intent}）推导出可自动验证的性质，建议人工审阅执行输出。",
            )
        )
    return out


def summarize_checks(checks: Sequence[CheckResult]) -> Dict[str, Any]:
    counter: Dict[str, int] = {"pass": 0, "warn": 0, "fail": 0, "skipped": 0}
    for c in checks:
        counter[c.status] = counter.get(c.status, 0) + 1
    total = sum(counter.values())
    judged = counter["pass"] + counter["warn"] + counter["fail"]
    score = (counter["pass"] + 0.5 * counter["warn"]) / judged if judged else 0.0
    return {
        "total": total,
        **counter,
        "score": round(score, 4),
        "verdict": "consistent" if counter["fail"] == 0 and judged else ("issues_found" if judged else "not_verifiable"),
    }
