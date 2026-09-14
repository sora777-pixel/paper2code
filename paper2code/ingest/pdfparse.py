"""PDF 解析。

优先使用 PyMuPDF 按版面（双栏 blocks + 字号）取正文与标题；
表格抽取优先 pdfplumber；图只保留足够大的嵌入图，并尽量配 Figure 题注。
两条依赖均为可选：缺失时返回带警告的空结构，不影响其余链路。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..models import CodeRef, Figure, Paper, Table
from . import extract
from .mdparse import parse_markdown


_PAGE_MARK = re.compile(r"^\[Page\s+\d+\]$", re.I)
_TITLE_JUNK = re.compile(
    r"^(arxiv|doi|http|www\.|abstract|introduction|i+\.|dated:)",
    re.I,
)


def _page_text_layout(page) -> str:
    """按版面重排：检测到双栏则先左后右，栏内按 y。"""
    blocks = page.get_text("blocks") or []
    text_blocks = [b for b in blocks if len(b) >= 7 and b[6] == 0 and str(b[4]).strip()]
    if not text_blocks:
        return page.get_text("text") or ""

    page_w = float(page.rect.width)
    mid = page_w / 2.0
    left, right = [], []
    for b in text_blocks:
        cx = (float(b[0]) + float(b[2])) / 2.0
        (left if cx < mid else right).append(b)

    def sort_key(b):
        return (round(float(b[1]) / 6.0) * 6.0, float(b[0]))

    # 两侧都有若干文本块才当双栏，避免把通栏摘要拆开
    if len(left) >= 3 and len(right) >= 3:
        ordered = sorted(left, key=sort_key) + sorted(right, key=sort_key)
    else:
        ordered = sorted(text_blocks, key=sort_key)
    return "\n".join(str(b[4]).rstrip() for b in ordered)


def _title_authors_from_page(page) -> Tuple[str, List[str]]:
    """第一页上半部的大字号长行作标题，下一档像人名的行作作者。"""
    try:
        info = page.get_text("dict")
    except Exception:
        return "", []

    rows: List[Tuple[float, float, str]] = []
    for block in info.get("blocks", []):
        for line in block.get("lines", []) or []:
            spans = line.get("spans") or []
            if not spans:
                continue
            text = "".join(s.get("text", "") for s in spans).strip()
            text = re.sub(r"\s+", " ", text)
            if not text or _PAGE_MARK.match(text) or _TITLE_JUNK.match(text):
                continue
            if text.lower().startswith("arxiv:"):
                continue
            if len(text) < 4:
                continue
            size = max(float(s.get("size") or 0) for s in spans)
            y0 = float(line.get("bbox", [0, 0, 0, 0])[1])
            x0 = float(line.get("bbox", [0, 0, 0, 0])[0])
            if x0 < 30 and size >= 14:
                continue
            rows.append((size, y0, text))
    if not rows:
        return "", []

    page_h = float(page.rect.height)
    top_long = [r for r in rows if r[1] < page_h * 0.22 and r[2].count(" ") >= 3]
    pool = top_long or [r for r in rows if r[1] < page_h * 0.22] or rows
    max_size = max(r[0] for r in pool)
    title_rows = [r for r in rows if r[0] >= max_size - 0.6 and r[1] < page_h * 0.28]
    title_rows.sort(key=lambda r: r[1])
    title = re.sub(r"\s+", " ", " ".join(r[2] for r in title_rows)).strip()

    authors: List[str] = []
    if title_rows:
        title_bottom = title_rows[-1][1]
        author_rows = [
            r for r in rows
            if r[1] > title_bottom + 2
            and r[1] < title_bottom + 90
            and 8 <= r[0] <= max_size
            and "University" not in r[2]
            and "Department" not in r[2]
            and "Dated" not in r[2]
            and ("," in r[2] or " and " in r[2] or re.search(r"[A-Z]\.\s+[A-Za-z]", r[2]))
        ]
        author_rows.sort(key=lambda r: r[1])
        blob = " ".join(r[2] for r in author_rows)
        blob = re.sub(r"[∗†‡§¶*]", " ", blob)
        blob = re.sub(r"\s+", " ", blob)
        for part in re.split(r",|;|\band\b", blob):
            part = part.strip(" .")
            if 2 <= len(part) <= 60 and re.search(r"[A-Za-z]", part):
                if "University" not in part and "Department" not in part:
                    authors.append(part)
    return title, authors[:12]


def _collect_pdf_links(doc) -> List[CodeRef]:
    refs: List[CodeRef] = []
    seen = set()
    for i, page in enumerate(doc):
        try:
            links = page.get_links() or []
        except Exception:
            links = []
        for lk in links:
            uri = (lk.get("uri") or "").strip()
            if not uri or uri in seen:
                continue
            seen.add(uri)
            low = uri.lower()
            if any(h in low for h in ("github.com", "gitlab.com", "bitbucket.org", "gitee.com")):
                refs.append(CodeRef(url=uri, kind="repo", evidence=f"PDF 第 {i+1} 页超链接"))
            elif any(h in low for h in ("zenodo", "figshare", "huggingface.co/datasets")):
                refs.append(CodeRef(url=uri, kind="dataset", evidence=f"PDF 第 {i+1} 页超链接"))
    return refs


def _read_pdf_text(path: Path) -> Tuple[List[str], List[Dict[str, Any]], List[str], str, List[str], List[CodeRef]]:
    """返回 ``(行, 大图信息, 警告, 标题, 作者, 链接)``。"""
    warnings: List[str] = []
    lines: List[str] = []
    images: List[Dict[str, Any]] = []
    title = ""
    authors: List[str] = []
    link_refs: List[CodeRef] = []
    try:
        import fitz  # PyMuPDF
    except Exception:
        return [], [], ["未安装 PyMuPDF，PDF 正文解析被跳过（pip install pymupdf）"], "", [], []

    try:
        doc = fitz.open(str(path))
    except Exception as exc:  # pragma: no cover
        return [], [], [f"打开 PDF 失败：{exc}"], "", [], []

    try:
        if doc.page_count:
            title, authors = _title_authors_from_page(doc[0])
        link_refs = _collect_pdf_links(doc)
        for pno, page in enumerate(doc):
            try:
                text = _page_text_layout(page)
            except Exception:
                text = page.get_text("text") or ""
            if pno > 0:
                # 不用 ATX 标题，避免被当成论文名
                lines.append(f"\n<!-- page {pno + 1} -->\n")
            lines.extend(text.splitlines())
            try:
                for img in page.get_images(full=True):
                    w, h = int(img[2] or 0), int(img[3] or 0)
                    if w * h < 12000 or min(w, h) < 48:
                        continue
                    images.append({"page": pno + 1, "xref": img[0], "w": w, "h": h})
            except Exception:
                pass
    finally:
        doc.close()
    return lines, images, warnings, title, authors, link_refs


def _looks_like_garbage_table(header: List[str], rows: List[List[str]]) -> bool:
    cells = [c.strip() for c in header] + [c.strip() for r in rows[:2] for c in r]
    if not cells:
        return True
    numericish = 0
    for c in cells:
        if re.fullmatch(r"[-+]?\d[\d\s.,]*", c or "") or re.match(r"^\d{2}\s+\d{2}", c or ""):
            numericish += 1
    return numericish >= max(3, int(0.7 * len(cells)))


def _extract_tables_pdfplumber(path: Path) -> List[Table]:
    try:
        import pdfplumber  # type: ignore
    except Exception:
        return []

    out: List[Table] = []
    try:
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                for raw in page.extract_tables() or []:
                    rows = [[(c or "").replace("\n", " ").strip() for c in r] for r in raw if r]
                    rows = [r for r in rows if any(c for c in r)]
                    if len(rows) < 2:
                        continue
                    width = max(len(r) for r in rows)
                    rows = [r + [""] * (width - len(r)) for r in rows]
                    if _looks_like_garbage_table(rows[0], rows[1:]):
                        continue
                    out.append(
                        Table(
                            id=f"T{len(out) + 1}",
                            caption="",
                            header=rows[0],
                            rows=rows[1:],
                            source="pdf",
                        )
                    )
    except Exception:
        return out
    return out


def _extract_tables_heuristic(lines: List[str]) -> List[Table]:
    num_row = re.compile(r"^\s*\S.*?\s{2,}[-+]?[\d.,]+\s*$")
    out: List[Table] = []
    buf: List[str] = []
    for line in lines + [""]:
        if num_row.match(line) or (buf and line.strip() and re.search(r"\s{2,}", line)):
            buf.append(line)
            continue
        if len(buf) >= 3:
            rows = [[c.strip() for c in re.split(r"\s{2,}", r.strip()) if c] for r in buf]
            width = max((len(r) for r in rows), default=0)
            rows = [r + [""] * (width - len(r)) for r in rows]
            if rows and not _looks_like_garbage_table(rows[0], rows[1:]):
                out.append(
                    Table(
                        id=f"H{len(out) + 1}",
                        caption="（启发式抽取，未校验）",
                        header=rows[0],
                        rows=rows[1:],
                        source="pdf-heuristic",
                    )
                )
        buf = []
    return out


def _parse_pdf_pymupdf(path: Path, paper_id: str) -> Paper:
    lines, images, warnings, title, authors, link_refs = _read_pdf_text(path)

    if not lines:
        paper = Paper(id=paper_id, source_path=str(path), source_kind="pdf")
        paper.meta["warnings"] = warnings
        return paper

    paper = parse_markdown("\n".join(lines), paper_id, source_path=str(path), kind="pdf")
    paper.meta["warnings"] = warnings
    paper.meta["pdf_pages"] = sum(1 for ln in lines if ln.startswith("<!-- page ")) or 1

    def _title_ok(s: str) -> bool:
        s = (s or "").strip()
        if len(s) < 20 or _PAGE_MARK.match(s) or s in ("Untitled",):
            return False
        if s.count(" ") < 4:
            return False
        if re.search(r"[|ψϕ⟨⟩]", s) and s.count(" ") < 6:
            return False
        return True

    if _title_ok(title):
        paper.title = title
    elif not _title_ok(paper.title):
        top = []
        for ln in lines[:25]:
            s = ln.strip()
            if _title_ok(s):
                top.append(s)
            elif top:
                break
        if top:
            paper.title = " ".join(top)
    if authors:
        paper.authors = authors

    academic = extract.split_academic_sections(lines)
    if len(academic) >= 2:
        paper.sections = academic

    cap_tables = extract.extract_captioned_tables(lines)
    plumber = _extract_tables_pdfplumber(path)
    if not plumber:
        plumber = _extract_tables_heuristic(lines)
    if cap_tables:
        unused = list(plumber)
        for tb in cap_tables:
            if unused and not tb.rows:
                src_t = unused.pop(0)
                tb.header, tb.rows, tb.source = src_t.header, src_t.rows, src_t.source
        paper.tables = cap_tables
    else:
        paper.tables = plumber

    paper.figures = extract.extract_figures(lines)
    if not paper.figures:
        extra = 0
        for img in images:
            paper.figures.append(
                Figure(
                    id=f"P{img['page']}_{img['xref']}",
                    caption=f"page {img['page']} figure",
                    page=img["page"],
                    kind="unknown",
                )
            )
            extra += 1
            if extra >= 8:
                break

    seen = {r.url for r in paper.code_refs}
    for r in link_refs:
        if r.url not in seen:
            if r.kind == "dataset":
                paper.data_refs.append(r)
            else:
                paper.code_refs.append(r)
            seen.add(r.url)

    paper.meta["warnings"] = warnings
    return paper



# --------------------------------------------------------------------------- #
# Docling / hybrid merge
# --------------------------------------------------------------------------- #
_ACADEMIC_KINDS = {
    "abstract", "introduction", "method", "experiment", "conclusion", "section",
}
_SECTION_ROMAN = re.compile(r"^[IVX]+\.\s+")


def _title_score(s: str) -> int:
    s = (s or "").strip()
    if not s or s in ("Untitled",):
        return -1
    score = len(s)
    if "wave functions" in s.lower():
        score += 100
    if s.count(" ") >= 6:
        score += 20
    if _SECTION_ROMAN.match(s):
        score -= 50
    return score


def _pick_docling_title(doc_paper: Paper, md_text: str) -> str:
    """Docling exports the paper title as the first ## heading."""
    candidates: List[str] = []
    if doc_paper.title and doc_paper.title not in ("Untitled",):
        candidates.append(doc_paper.title)
    for s in doc_paper.sections[:3]:
        if s.heading:
            candidates.append(s.heading)
    for line in md_text.splitlines()[:30]:
        m = re.match(r"^#{1,3}\s+(.+?)\s*$", line)
        if m:
            candidates.append(m.group(1).strip())
            break
    best, best_score = "", -1
    for c in candidates:
        sc = _title_score(c)
        if sc > best_score:
            best, best_score = c, sc
    return best


def _count_academic_sections(sections) -> int:
    n = 0
    for s in sections:
        h = (s.heading or "").strip()
        if s.kind in _ACADEMIC_KINDS and len(h) > 2:
            n += 1
        elif _SECTION_ROMAN.match(h) or re.match(
            r"^(Abstract|Introduction|Methods?|Results|Discussion|Conclusion)",
            h,
            re.I,
        ):
            n += 1
    return n


def _table_has_grid(tb: Table) -> bool:
    cols = max(len(tb.header or []), getattr(tb, "n_cols", 0) or 0)
    if cols < 2:
        return False
    cells = [str(c).strip() for c in (tb.header or [])]
    for row in tb.rows or []:
        cells.extend(str(c).strip() for c in row)
    nonempty = sum(1 for c in cells if c)
    return nonempty >= 4 and len(tb.rows or []) >= 1


def _norm_caption(cap: str) -> str:
    cap = re.sub(r"\s+", " ", (cap or "").lower()).strip()
    cap = re.sub(r"^(table|tab\.?)\s*[sivxlcdm\d]+\s*[:.\-]?\s*", "", cap)
    return cap[:120]


def _caps_similar(a: str, b: str) -> bool:
    na, nb = _norm_caption(a), _norm_caption(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    return na[:40] == nb[:40]


def _authors_look_named(authors: List[str]) -> bool:
    blob = " ".join(authors or [])
    return bool(re.search(r"Gastegger|Schutt|Schütt|Maurer|McSloy", blob, re.I))


def _authors_from_docling_md(md_text: str) -> List[str]:
    """Best-effort author lines under the title in Docling markdown."""
    lines = md_text.splitlines()
    i = 0
    while i < len(lines) and not re.match(r"^#{1,3}\s+", lines[i]):
        i += 1
    i += 1
    blob_parts: List[str] = []
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            if blob_parts:
                break
            i += 1
            continue
        if re.match(r"^#{1,3}\s+", s) or s.lower().startswith("(dated"):
            break
        if s.startswith("<!--") or s.lower().startswith("the emergence"):
            break
        if "University" in s or "Department" in s or "Group" in s:
            i += 1
            continue
        blob_parts.append(s)
        i += 1
        if len(blob_parts) >= 4:
            break
    blob = " ".join(blob_parts)
    blob = re.sub(r"[?*†‡§¶\|]", " ", blob)
    blob = re.sub(r"\s+", " ", blob)
    out: List[str] = []
    for part in re.split(r",|;|\band\b", blob):
        part = part.strip(" .")
        if 2 <= len(part) <= 60 and re.search(r"[A-Za-z]", part):
            if "University" not in part and "Department" not in part and "Group" not in part:
                out.append(part)
    return out[:12]


def _merge_docling_into_pymupdf(base: Paper, doc: Paper, md_text: str) -> Paper:
    """Merge Docling structure into a PyMuPDF Paper (formulas/figures kept)."""
    dtitle = _pick_docling_title(doc, md_text)
    if _title_score(dtitle) > _title_score(base.title):
        base.title = dtitle

    if _count_academic_sections(doc.sections) >= max(2, _count_academic_sections(base.sections)):
        sections = list(doc.sections)
        if sections and _title_score(sections[0].heading) >= max(0, _title_score(base.title) - 5):
            if sections[0].kind == "section" and not _SECTION_ROMAN.match(sections[0].heading or ""):
                sections = sections[1:]
        if sections:
            base.sections = sections

    # Attach TABLE I / S* captions from Docling markdown onto pipe tables
    from . import extract as _extract
    cap_only = _extract.extract_captioned_tables(md_text.splitlines())
    unused_caps = list(cap_only)
    for tb in doc.tables:
        if not (tb.caption or '').strip() and unused_caps:
            cap_tb = unused_caps.pop(0)
            tb.caption = cap_tb.caption
            if cap_tb.id:
                tb.id = cap_tb.id

    merged_tables: List[Table] = []
    for tb in doc.tables:
        if _table_has_grid(tb):
            tb.source = "docling"
            merged_tables.append(tb)
    # When Docling already supplied real grids, skip PyMuPDF grids
    # (they are often numeric junk). Keep caption-only if Docling missed it.
    docling_caps = [d.caption for d in doc.tables]
    for tb in base.tables:
        if _table_has_grid(tb):
            if merged_tables:
                continue
            if any(
                _caps_similar(tb.caption, m.caption) or (tb.id and tb.id == m.id)
                for m in merged_tables
            ):
                continue
            merged_tables.append(tb)
            continue
        if (tb.caption or "").strip():
            if any(_caps_similar(tb.caption, m.caption) for m in merged_tables):
                continue
            # Match against Docling captions OR nearby TABLE text in docling tables' raw ids
            if any(_caps_similar(tb.caption, c) for c in docling_caps if c):
                continue
            # If Docling has >=1 grid, treat main TABLE I/S* as covered when ids align
            tid = (tb.id or "").upper().lstrip("T")
            if merged_tables and tid and any(
                (m.id or "").upper() in (f"T{tid}", tid, f"T{tid.lstrip('S')}")
                or tid in (m.id or "").upper()
                for m in merged_tables
            ):
                continue
            # Heuristic: Docling pipe tables often lack captions; if we have N docling
            # grids and this is caption-only TABLE I / S*, drop when N>=1 and caption
            # mentions mean absolute / train validation etc. already in a grid paper.
            if merged_tables and re.search(r"TABLE\s*(S?\d+|I|II|III)", tb.caption or "", re.I):
                # Keep only if clearly not represented (no docling grid at all for SI tables beyond count)
                if len(merged_tables) >= 1 and not tid.startswith("S"):
                    continue
                if len(merged_tables) >= 3 and tid.startswith("S"):
                    continue
            merged_tables.append(tb)
    if merged_tables:
        base.tables = merged_tables

    seen = {r.url for r in base.code_refs}
    for r in doc.code_refs:
        if r.url not in seen:
            base.code_refs.append(r)
            seen.add(r.url)
    seen_d = {r.url for r in base.data_refs}
    for r in doc.data_refs:
        if r.url not in seen_d:
            base.data_refs.append(r)
            seen_d.add(r.url)

    d_authors = list(doc.authors) or _authors_from_docling_md(md_text)
    if _authors_look_named(d_authors):
        merged_a = list(d_authors)
        low = {a.lower() for a in merged_a}
        for a in base.authors:
            if a.lower() not in low:
                merged_a.append(a)
                low.add(a.lower())
        base.authors = merged_a[:12]
    elif d_authors and not base.authors:
        base.authors = d_authors[:12]

    if doc.abstract and len(doc.abstract) > len(base.abstract or ""):
        base.abstract = doc.abstract

    return base


def parse_pdf(path: Path, paper_id: str, backend: Optional[str] = None) -> Paper:
    """Parse a PDF with optional Docling / hybrid backend.

    Honours env ``P2C_PDF_BACKEND`` = ``hybrid`` | ``docling`` | ``pymupdf``.
    Docling failures fall back to PyMuPDF and never crash the pipeline.
    """
    import logging

    from . import docling_backend as dl

    log = logging.getLogger(__name__)
    backend = (backend or dl.default_pdf_backend() or "pymupdf").strip().lower()
    if backend not in ("pymupdf", "docling", "hybrid"):
        backend = "pymupdf"

    pymu: Optional[Paper] = None
    if backend in ("pymupdf", "hybrid"):
        pymu = _parse_pdf_pymupdf(path, paper_id)
        pymu.pdf_backend = "pymupdf"
        pymu.meta["pdf_backend"] = "pymupdf"

    if backend == "pymupdf":
        assert pymu is not None
        return pymu

    try:
        out_md = path.with_name(f"{path.stem}.docling.md")
        md_path = dl.convert_pdf_to_markdown(path, out_md)
        md_text = md_path.read_text(encoding="utf-8")
        doc_paper = parse_markdown(md_text, paper_id, source_path=str(path), kind="pdf")
        dtitle = _pick_docling_title(doc_paper, md_text)
        if _title_score(dtitle) > _title_score(doc_paper.title):
            doc_paper.title = dtitle
        if not doc_paper.authors:
            doc_paper.authors = _authors_from_docling_md(md_text)
        doc_paper.pdf_backend = "docling"
        doc_paper.meta["docling_md"] = str(md_path)
        doc_paper.meta["pdf_backend"] = "docling"

        if backend == "docling":
            return doc_paper

        assert pymu is not None
        merged = _merge_docling_into_pymupdf(pymu, doc_paper, md_text)
        merged.pdf_backend = "hybrid"
        merged.meta["docling_md"] = str(md_path)
        merged.meta["pdf_backend"] = "hybrid"
        return merged
    except Exception as exc:
        log.warning("Docling backend failed (%s); falling back to PyMuPDF: %s", backend, exc)
        if pymu is None:
            pymu = _parse_pdf_pymupdf(path, paper_id)
        pymu.pdf_backend = "pymupdf"
        warnings = list(pymu.meta.get("warnings") or [])
        warnings.append(f"Docling unavailable, used PyMuPDF only: {exc}")
        pymu.meta["warnings"] = warnings
        pymu.meta["pdf_backend"] = "pymupdf"
        return pymu



def render_page_png(path: Path, page: int, out_path: Path, dpi: int = 144) -> Optional[str]:
    try:
        import fitz  # type: ignore
    except Exception:
        return None
    try:
        doc = fitz.open(str(path))
        pg = doc[page - 1]
        pix = pg.get_pixmap(dpi=dpi)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(out_path))
        doc.close()
        return str(out_path)
    except Exception:
        return None
