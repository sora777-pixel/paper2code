"""复现数据集构建。

把「论文中提及或附带的数据」整理成可执行的 tidy 数据：

* 情形 A：优先使用仓库自带的数据文件（由 :mod:`repo` 处理）；
* 情形 B / C：使用论文表格抽取值 + 论文包内 ``data/*.csv``。

所有写出都受 ``max_records`` 规模保护——超出上限时按确定性采样截断，
并在报告中显式说明，避免“静默丢数据”。
"""

from __future__ import annotations

import csv
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .. import numutil
from ..config import get_settings
from ..models import Paper, Table


@dataclass
class DatasetInfo:
    table_id: str
    csv_path: str
    n_rows: int
    n_cols: int
    truncated: bool = False
    source: str = "text"
    numeric_columns: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "table_id": self.table_id, "csv_path": self.csv_path,
            "n_rows": self.n_rows, "n_cols": self.n_cols,
            "truncated": self.truncated, "source": self.source,
            "numeric_columns": self.numeric_columns,
        }


def table_to_records(table: Table) -> List[Dict[str, str]]:
    header = list(table.header)
    if not header:
        width = max((len(r) for r in table.rows), default=0)
        header = [f"col{i+1}" for i in range(width)]
    header = [_dedupe_name(h, i) for i, h in enumerate(header)]

    out: List[Dict[str, str]] = []
    for r in table.rows:
        row = list(r) + [""] * (len(header) - len(r))
        out.append({header[i]: row[i] for i in range(len(header))})
    return out


def _dedupe_name(name: str, idx: int) -> str:
    n = str(name or "").strip() or f"col{idx+1}"
    return n


def numeric_columns(table: Table) -> List[str]:
    recs = table_to_records(table)
    if not recs:
        return []
    cols = list(recs[0].keys())
    out = []
    for c in cols:
        vals = [numutil.to_number(r.get(c)) for r in recs]
        good = [v for v in vals if v is not None]
        if good and len(good) >= max(2, int(0.5 * len(recs))):
            out.append(c)
    return out


def write_csv(records: Sequence[Dict[str, Any]], out_path: Path) -> str:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fields: List[str] = []
    for rec in records:
        for k in rec:
            if k not in fields:
                fields.append(k)
    with out_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for rec in records:
            writer.writerow({k: rec.get(k, "") for k in fields})
    return str(out_path)


def build_datasets(paper: Paper, out_dir: Path, max_records: Optional[int] = None) -> Dict[str, DatasetInfo]:
    """把论文里的每张表写成 CSV，返回 ``{table_id: DatasetInfo}``。"""
    settings = get_settings()
    limit = max_records or settings.max_records
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    infos: Dict[str, DatasetInfo] = {}
    for table in paper.tables:
        if not table.rows:
            continue
        records = table_to_records(table)
        truncated = False
        if len(records) > limit:
            rnd = random.Random(settings.random_seed)
            records = rnd.sample(records, limit)
            truncated = True

        csv_path = write_csv(records, out_dir / f"{table.id}.csv")
        infos[table.id] = DatasetInfo(
            table_id=table.id,
            csv_path=csv_path,
            n_rows=len(records),
            n_cols=len(table.header) or table.n_cols,
            truncated=truncated,
            source=table.source,
            numeric_columns=numeric_columns(table),
        )

        # 附带数据的表格同时记录其原始 schema，便于报告展示
        if table.source == "provided":
            schema_path = out_dir / f"{table.id}.schema.json"
            schema_path.write_text(
                json.dumps(
                    {"columns": list(records[0].keys()) if records else [], "n_rows": len(records)},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
    return infos


def synthesize_input(
    n: int,
    kind: str = "vec",
    seed: int = 0,
    low: float = 0.0,
    high: float = 1.0,
) -> List[Any]:
    """为情形 C（伪代码执行）生成小规模输入。

    ``kind`` ∈ {vec, int_vec, matrix, str_list}。全部为**确定性**生成（固定种子），
    报告里会写明“输入为按论文描述规模合成的数据，用于验证算法逻辑”。
    """
    rnd = random.Random(seed)
    n = max(1, min(int(n), get_settings().max_records))
    if kind == "int_vec":
        return [rnd.randint(int(low), int(high)) for _ in range(n)]
    if kind == "matrix":
        return [[rnd.uniform(low, high) for _ in range(5)] for _ in range(n)]
    if kind == "str_list":
        alphabet = "abcdefghijklmnopqrstuvwxyz"
        return ["".join(rnd.choice(alphabet) for _ in range(6)) for _ in range(n)]
    return [rnd.uniform(low, high) for _ in range(n)]


def collect_numeric(paper: Paper) -> Dict[str, List[float]]:
    """汇总论文中所有可解析的数值，按 ``表格id.列名`` 分组。"""
    out: Dict[str, List[float]] = {}
    for table in paper.tables:
        recs = table_to_records(table)
        for col in recs[0].keys() if recs else []:
            vals = [numutil.to_number(r.get(col)) for r in recs]
            good = [v for v in vals if v is not None]
            if good:
                out[f"{table.id}.{col}"] = good
    return out


def infer_scale_hint(paper: Paper) -> int:
    """从正文中猜测实验规模（用于情形 C 的输入构造），并夹到上限内。"""
    import re

    limit = get_settings().max_records
    text = (paper.section_text("experiment", "method") or paper.raw_text)[:20000]
    candidates: List[int] = []
    for m in re.finditer(r"\b(N|n)\s*[=≈]\s*([\d,]{2,9})", text):
        try:
            v = int(m.group(2).replace(",", ""))
            if 2 <= v <= 10**7:
                candidates.append(v)
        except ValueError:
            continue
    if not candidates:
        return 400
    guess = max(candidates)
    return int(min(guess, limit))
