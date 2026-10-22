"""PDF 图资产导出：把页面位图落盘成 PNG，并与 ``Figure N`` 图题按页配对。

**为什么需要它**：真实 PDF 的 ``Figure.image_path`` 之前恒为空（实测 P1 0/32、
P2 0/19），于是课件插图、FIGURES.md 的「原图」一栏、以及 digitize 取点全都断在
这里。旧实现还会把 ``get_images()`` 的每条位图当作一张图、并一律写
``caption="page N embedded image"`` —— 既造出假图题，又让 ``planner`` 的
``kind=="unknown" and not caption`` 拦不住它们。

配对原则（roadmap M2-2）：**以图题为主键**。先保留全部图题条目，再把位图按
「同页 + 版面位置最近」挂上去；挂不上的保留为 ``kind="embedded"``，不进重绘目标。
尺寸过滤用**版面显示尺寸**而不是像素尺寸——首页的刊名 logo / 二维码虽然像素很大，
但显示只有几十 pt，应当排除。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..models import Figure, Paper

#: 位图固有像素的最小边长
MIN_INTRINSIC = 80
#: 版面显示（pt）的最小边长与面积——用来滤掉 logo / 二维码这类小图标
MIN_DISPLAY_SIDE = 60.0
MIN_DISPLAY_AREA = 15000.0

#: 必须与 ``extract._FIG_CAP_RE`` 的词表一致（``Figure/Fig./图``）。
#: 若在这里额外收 ``Scheme 1``，它会与 ``Fig. 1`` 抢同一个编号——实测 P1 的
#: ``Scheme 1``（p5）因此把 ``Fig. 1``（p6）的图位占走，配错原图。
_FIG_CAP_RE = re.compile(
    r"^\s*\*{0,2}\s*(Figure|Fig\.?|图)\s*([0-9]+[a-zA-Z]?)", re.IGNORECASE
)
_PAGE_MARK_DIGIT_RE = re.compile(r"\[{1,2}\s*page\s*:?\s*(\d+)\s*\]{1,2}", re.IGNORECASE)


def caption_pages(lines: List[str]) -> Dict[int, int]:
    """扫出 ``Figure N`` 图题所在的页码（首页无分页标记，记为 1）。"""
    out: Dict[int, int] = {}
    page = 1
    for raw in lines:
        s = raw.strip()
        m = _PAGE_MARK_DIGIT_RE.search(s)
        if m:
            page = int(m.group(1))
            continue
        cap = _FIG_CAP_RE.match(s)
        if cap:
            num = int(re.sub(r"\D", "", cap.group(2)) or 0)
            if num and num not in out:
                out[num] = page
    return out


def _collect_from_meta(paper: Paper) -> List[Dict[str, Any]]:
    """用解析阶段就收好的图片块（带 xref/bbox/尺寸），免掉一趟页面扫描。"""
    out: List[Dict[str, Any]] = []
    for b in paper.meta.get("pdf_images") or []:
        w, h = int(b.get("w") or 0), int(b.get("h") or 0)
        rect = b.get("rect") or (0.0, 0.0, 0.0, 0.0)
        if min(w, h) < MIN_INTRINSIC:
            continue
        side = min(rect[2] - rect[0], rect[3] - rect[1])
        if side < MIN_DISPLAY_SIDE or (rect[2] - rect[0]) * (rect[3] - rect[1]) < MIN_DISPLAY_AREA:
            continue
        out.append({"page": int(b.get("page") or 1), "xref": b.get("xref"), "rect": rect,
                    "w": w, "h": h, "ext": b.get("ext") or "png", "y": rect[1], "x": rect[0]})
    return out


def _collect_bitmaps(doc: Any) -> List[Dict[str, Any]]:
    """回退路径：直接在文档上扫位图（``paper.meta["pdf_images"]`` 缺失时用）。"""
    out: List[Dict[str, Any]] = []
    for pno in range(1, doc.page_count + 1):
        page = doc[pno - 1]
        try:
            imgs = page.get_images(full=True)
        except Exception:
            continue
        for img in imgs:
            xref, w, h = img[0], img[2], img[3]
            if min(w, h) < MIN_INTRINSIC:
                continue
            try:
                rects = page.get_image_rects(xref)
            except Exception:
                rects = []
            if not rects:
                continue
            r = rects[0]
            if min(r.width, r.height) < MIN_DISPLAY_SIDE or r.width * r.height < MIN_DISPLAY_AREA:
                continue
            bb = (r.x0, r.y0, r.x1, r.y1)
            out.append({"page": pno, "xref": xref, "rect": bb, "w": w, "h": h,
                        "ext": "png", "y": bb[1], "x": bb[0]})
    return out


def _dedupe(bitmaps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for b in bitmaps:
        key = (b["page"], b["xref"])
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
    return out


def export_for_paper(paper: Paper, out_dir: Path) -> Dict[str, Any]:
    """PDF 论文才导出（含 arxiv 的本地 PDF）；任何失败都只降级为一则告警。"""
    is_pdf = str(paper.source_path or "").lower().endswith(".pdf")
    if not is_pdf or paper.source_kind not in ("pdf", "arxiv"):
        return {"exported": 0, "paired": 0, "embedded": 0, "skipped": 0, "dir": ""}
    try:
        summary = export_pdf_figures(paper, out_dir)
    except Exception as exc:  # 图资产失败不该阻断整条流水线
        paper.meta.setdefault("warnings", []).append(
            "图资产导出失败（%s: %s），图将只保留图题。" % (type(exc).__name__, exc)
        )
        return {"exported": 0, "paired": 0, "embedded": 0, "skipped": 0, "dir": ""}
    if summary["exported"] and not summary["paired"]:
        paper.meta.setdefault("warnings", []).append(
            "从 PDF 抽到 %d 张位图，但没有任何一张能与图题配对——图中内容的对照将缺失。"
            % summary["exported"]
        )
    return summary


def export_pdf_figures(paper: Paper, out_dir: Path) -> Dict[str, Any]:
    """导出位图并与图题配对；写回 ``paper.figures`` 与 ``paper.meta``。

    返回 ``{exported, paired, embedded, skipped, dir}``。
    """
    summary: Dict[str, Any] = {"exported": 0, "paired": 0, "embedded": 0, "skipped": 0, "dir": ""}
    source = paper.source_path or ""
    if not source or Path(source).suffix.lower() != ".pdf":
        return summary

    try:
        import fitz  # PyMuPDF
    except Exception:
        return summary

    try:
        doc = fitz.open(source)
    except Exception:
        return summary

    try:
        bitmaps = _dedupe(_collect_from_meta(paper))
        if not bitmaps and not paper.meta.get("pdf_images"):
            # 回退：解析阶段没留下图片块时，直接在文档上扫一遍
            bitmaps = _dedupe(_collect_bitmaps(doc))
        out_dir.mkdir(parents=True, exist_ok=True)
        by_page: Dict[int, List[Dict[str, Any]]] = {}
        for b in bitmaps:
            if b.get("xref") is None:
                continue
            info = doc.extract_image(b["xref"])
            rel = "p%02d_x%d.%s" % (b["page"], b["xref"], info.get("ext") or b.get("ext") or "png")
            (out_dir / rel).write_bytes(info["image"])
            b["rel"] = rel
            by_page.setdefault(b["page"], []).append(b)
            summary["exported"] += 1
    except Exception:
        return summary
    finally:
        doc.close()
    summary["dir"] = str(out_dir)

    pages = caption_pages((paper.raw_text or "").splitlines())
    used: set[Tuple[int, int]] = set()

    def _num(fid: str) -> int:
        m = re.search(r"\d+", fid or "")
        return int(m.group(0)) if m else 0

    def _assign(fig: Figure, max_dist: int) -> bool:
        """给一个图题找一个未占用、且页码距离 ≤ max_dist 的位图。"""
        want = pages.get(_num(fig.id))
        cands: List[Tuple[int, float, Dict[str, Any]]] = []
        for pno, items in by_page.items():
            for b in items:
                if (pno, b["xref"]) in used:
                    continue
                dist = abs(pno - want) if want else 0
                if dist <= max_dist:
                    cands.append((dist, b["y"], b))
        if not cands:
            return False
        cands.sort(key=lambda t: (t[0], t[1]))
        best = cands[0][2]
        used.add((best["page"], best["xref"]))
        fig.image_path = best["rel"]
        fig.page = best["page"]
        return True

    # 1) 图题为主键，**两轮**匹配：先只认同页位图，再放宽到相邻页。
    #    一轮到底会让先处理的图把邻页位图抢走，导致后面的图全部错位（实测 P2 整链偏移）。
    pending = [f for f in paper.figures if f.caption and f.kind != "embedded"]
    matched: List[Figure] = []
    for max_dist in (0, 1):
        rest: List[Figure] = []
        for fig in pending:
            (matched if _assign(fig, max_dist) else rest).append(fig)
        pending = rest
    summary["paired"] = len(matched)

    # 2) 挂不上的位图：保留为 embedded（有原图、不进重绘目标）
    for pno in sorted(by_page):
        for b in sorted(by_page[pno], key=lambda x: x["y"]):
            if (pno, b["xref"]) in used:
                continue
            used.add((pno, b["xref"]))
            paper.figures.append(
                Figure(
                    id="E%d_%d" % (pno, b["xref"]),
                    caption="",
                    image_path=b["rel"],
                    page=pno,
                    kind="embedded",
                )
            )
            summary["embedded"] += 1

    paper.meta["assets_dir"] = str(out_dir)
    paper.meta["figure_assets"] = dict(summary)
    return summary
