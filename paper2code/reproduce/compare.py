"""数值对标：复现产出 vs 论文报告值。

只在**情形 A**（有开源代码 / 可执行复现）下才是严格意义的「指标复现」——
此时复现脚本会产出结果文件，本模块负责把它和论文表格逐格对齐、计算误差、
给出 pass/warn/fail 判定。

对齐策略：
1. 用「键列」（默认第 0 列）按大小写无关的模糊匹配对齐行；
2. 列名做归一化（去空格、去单位、统一大小写）后匹配；
3. 匹配不上的格子记为 ``skipped``，并在报告中列出，绝不静默丢弃。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .. import numutil
from ..models import Table
from .checks import CheckResult


# --------------------------------------------------------------------------- #
def _norm(text: Any) -> str:
    s = str(text or "").strip().lower()
    s = re.sub(r"[\(（\[].*?[\)）\]]", "", s)
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s)
    return s


def _loose(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _columns_of(rows: Sequence[Dict[str, Any]]) -> List[str]:
    cols: List[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    return cols


# --------------------------------------------------------------------------- #
def compare_rows(
    table: Table,
    repro_rows: Sequence[Dict[str, Any]],
    thresholds: Optional[Dict[str, float]] = None,
    max_cells: int = 2000,
) -> List[CheckResult]:
    """把复现结果行与论文表格逐格比对，返回检查结果列表。"""
    th = thresholds or {"pass": 0.05, "warn": 0.20}
    out: List[CheckResult] = []
    if not table.rows or not repro_rows:
        return out

    paper_cols = list(table.header) if table.header else _columns_of([{str(i): c for i, c in enumerate(r)} for r in table.rows])
    repro_cols = _columns_of(repro_rows)

    key_paper = paper_cols[0] if paper_cols else ""
    key_repro = repro_cols[0] if repro_cols else ""

    # 建立 复现行 的键索引
    repro_index: Dict[str, Dict[str, Any]] = {}
    for r in repro_rows:
        k = str(r.get(key_repro, "") or "")
        if k:
            repro_index[_norm(k)] = r

    cells = 0
    for ri, row in enumerate(table.rows):
        if cells >= max_cells:
            break
        row_key = str(row[0]) if row else ""
        match: Optional[Dict[str, Any]] = None
        nk = _norm(row_key)
        if nk in repro_index:
            match = repro_index[nk]
        else:
            for rk, rr in repro_index.items():
                if _loose(row_key, rk):
                    match = rr
                    break
        if match is None:
            out.append(
                CheckResult(
                    name="行对齐",
                    scope=f"{table.id}:{row_key[:24]}",
                    status="skipped",
                    detail="复现产物中未找到对应的行，无法对标",
                )
            )
            continue

        for ci, col in enumerate(paper_cols):
            if ci == 0 or ci >= len(row):
                continue
            reported = numutil.to_number(row[ci])
            if reported is None:
                continue
            # 在复现列中找同名列
            target_col = None
            for rc in repro_cols:
                if _loose(col, rc):
                    target_col = rc
                    break
            if target_col is None:
                continue
            reproduced = numutil.to_number(match.get(target_col))
            if reproduced is None:
                continue

            status, ad, rd, _ = numutil.classify(reported, reproduced, th)
            cells += 1
            out.append(
                CheckResult(
                    name="指标对标",
                    scope=f"{table.id}:{row_key[:20]}/{col[:18]}",
                    status=status,
                    detail=f"论文 {reported:g} vs 复现 {reproduced:g}（Δ={ad:g}，相对 {rd*100:.2f}%）",
                    expected=reported,
                    actual=reproduced,
                    rel_delta=rd,
                )
            )
    if not out:
        out.append(
            CheckResult(
                name="指标对标", scope=table.id, status="skipped",
                detail="论文表格与复现产物的列名无法对齐，建议手工指定键列/列映射",
            )
        )
    return out


def compare_series(
    reported: Sequence[float],
    reproduced: Sequence[float],
    thresholds: Optional[Dict[str, float]] = None,
    scope: str = "",
) -> List[CheckResult]:
    """两组等长序列的逐点比对（用于图表数据的对标）。"""
    th = thresholds or {"pass": 0.05, "warn": 0.20}
    out: List[CheckResult] = []
    for i, (a, b) in enumerate(zip(reported, reproduced)):
        status, ad, rd, _ = numutil.classify(a, b, th)
        out.append(
            CheckResult(
                name="序列对标", scope=f"{scope}#{i}",
                status=status, detail=f"{a:g} vs {b:g}",
                expected=a, actual=b, rel_delta=rd,
            )
        )
    return out


# --------------------------------------------------------------------------- #
def from_check_results(results: Sequence[CheckResult]) -> List[Dict[str, Any]]:
    return [r.to_dict() for r in results]


def summarize_deltas(results: Sequence[CheckResult]) -> Dict[str, Any]:
    judged = [r for r in results if r.status in ("pass", "warn", "fail")]
    if not judged:
        return {"n_judged": 0, "pass_rate": None, "median_rel_delta": None, "max_rel_delta": None}
    rels = sorted(r.rel_delta for r in judged if r.rel_delta is not None)
    return {
        "n_judged": len(judged),
        "n_pass": sum(1 for r in judged if r.status == "pass"),
        "n_warn": sum(1 for r in judged if r.status == "warn"),
        "n_fail": sum(1 for r in judged if r.status == "fail"),
        "pass_rate": round(sum(1 for r in judged if r.status == "pass") / len(judged), 4),
        "median_rel_delta": round(rels[len(rels) // 2], 6) if rels else None,
        "max_rel_delta": round(rels[-1], 6) if rels else None,
    }


def pick_repro_rows_from_csv(csv_path: str, limit: int = 5000) -> List[Dict[str, Any]]:
    """从 CSV 读取复现结果行（供情形 A 使用）。"""
    import csv as _csv
    from pathlib import Path

    p = Path(csv_path)
    if not p.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with p.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = _csv.DictReader(fh)
            for i, row in enumerate(reader):
                rows.append(dict(row))
                if i >= limit:
                    break
    except Exception:
        return []
    return rows
