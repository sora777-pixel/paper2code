# -*- coding: utf-8 -*-
"""真实 PDF 语料的基线诊断（只读）。

用途：把「摄取侧到底做到了什么」变成可复算的数字，供改进前后对比。
不写任何产物（除非显式传 --json）。

用法::

    python tools/corpus_baseline.py _corpus
    python tools/corpus_baseline.py _corpus --json _corpus/baseline.json

依赖：PyMuPDF（读字号用）、pdfplumber（论文自带的表格抽取）。两者缺失时
本脚本仍能跑，只是 title/table 相关指标会缺失并显式标注 ``n/a``。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paper2code.ingest.loader import load_paper  # noqa: E402
from paper2code.reproduce import classifier  # noqa: E402

#: 标题真值采集时要剔除的首页 boilerplate
_SKIP = re.compile(r"^(arxiv:|doi|https?://|received|accepted|published|copyright|©|keywords)", re.I)

#: 这些阈值就是「测试点」的验收线，改动它们等于改动验收标准
TARGETS = {
    "title_ok_rate": 0.90,       # 标题抽取准确率
    "page_marker_sections": 0,   # 分页标记不得成为章节
    "sections_max": 60,          # 章节数上限（超过视为过分割）
    "figures_max": 40,           # 单篇图数量上限（超过视为过分割）
    "table_zero_max": 2,         # 正文有 Table 引用却抽不到表的篇数上限
    "algorithm_recall": 0.60,    # 有 Algorithm 提及的论文中成功抽出的比例
    "code_recall": 0.90,         # 有仓库链接的论文中成功识别的比例
}


def _norm(text: str) -> str:
    s = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s)


def _gt_title(path: Path) -> str:
    """用首页最大字号的行作为标题真值（≈出版社的排版定义）。"""
    try:
        import fitz
    except Exception:
        return ""
    try:
        doc = fitz.open(str(path))
        page = doc[0]
        rows = []
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                text = " ".join(s.get("text", "").strip() for s in line.get("spans", [])).strip()
                if len(text) < 4:
                    continue
                size = max((s.get("size", 0) for s in line.get("spans", [])), default=0)
                rows.append((size, text, line.get("bbox", [0, 0, 0, 0])))
        doc.close()
    except Exception:
        return ""
    pool = [r for r in rows if not _SKIP.match(r[1])]
    if not pool:
        return ""
    top = max(r[0] for r in pool)
    picked = sorted((r for r in pool if r[0] >= top * 0.9), key=lambda r: (r[2][1], r[2][0]))
    return re.sub(r"\s+", " ", " ".join(r[1] for r in picked)).strip()


def scan(root: Path) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for pdf in sorted(root.rglob("*.pdf")):
        paper = load_paper(pdf)
        raw = (paper.raw_text or "").lower()
        gt = _gt_title(pdf)
        a, b = _norm(paper.title), _norm(gt)
        title_ok = bool(a) and bool(b) and (a in b or b in a)
        rows.append({
            "file": str(pdf.relative_to(root)).replace("\\", "/"),
            "group": "agent" if "agent" in pdf.parent.name else "manual",
            "sections": len(paper.sections),
            "page_markers": sum(1 for s in paper.sections if s.heading.startswith("[Page")),
            "semantic_sections": sum(1 for s in paper.sections if s.kind not in ("section", "other")),
            "tables": len(paper.tables),
            "table_refs": len(re.findall(r"\btable\s*\d", raw)),
            "figures": len(paper.figures),
            "algo_mentions": len(re.findall(r"\balgorithm\s*\d", raw)),
            "algorithms": len(paper.algorithms),
            "gh_mentions": len(re.findall(r"github\.com|gitlab\.com|zenodo\.org", raw)),
            "code_refs": len(paper.code_refs),
            "title_ok": title_ok if gt else None,
            "mode": classifier.classify(paper).mode,
        })
    return {"n": len(rows), "rows": rows}


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows) or 1
    judged_title = [r for r in rows if r["title_ok"] is not None]
    algo_pool = [r for r in rows if r["algo_mentions"]]
    code_pool = [r for r in rows if r["gh_mentions"]]
    return {
        "n": len(rows),
        "title_ok_rate": round(sum(1 for r in judged_title if r["title_ok"]) / (len(judged_title) or 1), 3),
        "title_judged": len(judged_title),
        "page_marker_total": sum(r["page_markers"] for r in rows),
        "sections_over_max": sum(1 for r in rows if r["sections"] > TARGETS["sections_max"]),
        "figures_over_max": sum(1 for r in rows if r["figures"] > TARGETS["figures_max"]),
        "figures_max_seen": max((r["figures"] for r in rows), default=0),
        "table_zero_but_referenced": sum(1 for r in rows if r["tables"] == 0 and r["table_refs"] > 0),
        "table_zero": sum(1 for r in rows if r["tables"] == 0),
        "algorithm_recall": round(sum(1 for r in algo_pool if r["algorithms"]) / (len(algo_pool) or 1), 3),
        "algorithm_pool": len(algo_pool),
        "code_recall": round(sum(1 for r in code_pool if r["code_refs"]) / (len(code_pool) or 1), 3),
        "code_pool": len(code_pool),
        "modes": {m: sum(1 for r in rows if r["mode"] == m) for m in ("open_source", "pseudocode_only", "no_code")},
    }


def verdict(s: Dict[str, Any]) -> List[str]:
    """按 TARGETS 给出逐项判定，方便直接当回归断言用。"""
    checks = [
        ("标题准确率", s["title_ok_rate"], TARGETS["title_ok_rate"], ">="),
        ("分页标记章节数", s["page_marker_total"], TARGETS["page_marker_sections"], "<="),
        ("图过分割篇数", s["figures_over_max"], 1, "<="),
        ("零表但有引用篇数", s["table_zero_but_referenced"], TARGETS["table_zero_max"], "<="),
        ("算法块召回", s["algorithm_recall"], TARGETS["algorithm_recall"], ">="),
        ("代码链接召回", s["code_recall"], TARGETS["code_recall"], ">="),
    ]
    out = []
    for name, got, want, op in checks:
        ok = got >= want if op == ">=" else got <= want
        out.append(f"{'PASS' if ok else 'FAIL'}  {name}: {got} (目标 {op} {want})")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="真实 PDF 语料基线诊断")
    ap.add_argument("root", nargs="?", default="_corpus")
    ap.add_argument("--json", default="", help="把结果写到该路径")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"目录不存在：{root}")
        return 2

    data = scan(root)
    s = summarize(data["rows"])

    print(f"语料：{root}  共 {s['n']} 篇\n")
    print(f"{'文件':<44}{'sec':>5}{'pg':>4}{'tab':>5}{'fig':>5}{'alg':>5}{'mode':>14}")
    for r in data["rows"]:
        print(f"{r['file'][:43]:<44}{r['sections']:>5}{r['page_markers']:>4}"
              f"{r['tables']:>5}{r['figures']:>5}{r['algorithms']:>5}{r['mode']:>14}")

    print("\n汇总：")
    for k, v in s.items():
        print(f"  {k:28} {v}")
    print("\n验收：")
    for line in verdict(s):
        print("  " + line)

    if args.json:
        Path(args.json).write_text(
            json.dumps({"summary": s, "verdict": verdict(s), "rows": data["rows"]},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n已写出 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
