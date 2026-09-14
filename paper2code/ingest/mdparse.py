"""Markdown / 纯文本 / HTML 论文解析。

本模块是「零依赖可运行」的主力链路：arXiv 的 ar5iv/HTML 版本、作者主页的
Markdown 预印本、以及本地 ``.md``/``.txt`` 都能直接解析，不需要任何
PDF 解析库。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple

from ..models import Paper, Section
from . import extract

# --------------------------------------------------------------------------- #
# 通用工具
# --------------------------------------------------------------------------- #
_FRONTMATTER_RE = re.compile(r"^---\s*$")
_ATX_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_SETEXT_RE = re.compile(r"^(=+|-{2,})\s*$")
_PAGE_HEADING = re.compile(r"^page\s+\d+$", re.I)


def _strip_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    lines = text.splitlines()
    if not lines or not _FRONTMATTER_RE.match(lines[0]):
        return {}, text
    meta: Dict[str, str] = {}
    for i in range(1, len(lines)):
        if _FRONTMATTER_RE.match(lines[i]):
            return meta, "\n".join(lines[i + 1 :])
        if ":" in lines[i]:
            k, v = lines[i].split(":", 1)
            meta[k.strip().lower()] = v.strip().strip("\"'")
    return meta, text


def _split_authors(value: str) -> List[str]:
    value = re.sub(r"[\[\]{}]", "", value)
    parts = re.split(r",|;|\band\b|、", value)
    return [p.strip() for p in parts if p.strip()]


# --------------------------------------------------------------------------- #
# Markdown -> (headings, body_lines)
# --------------------------------------------------------------------------- #
def markdown_to_blocks(text: str) -> Tuple[str, List[str], List[Tuple[int, str, int]]]:
    """返回 ``(title, lines, headings)``；headings 为 ``(level, heading, line_idx)``。"""
    meta, body = _strip_frontmatter(text)
    lines = body.splitlines()
    headings: List[Tuple[int, str, int]] = []
    title = meta.get("title", "")

    i = 0
    while i < len(lines):
        line = lines[i]
        m = _ATX_RE.match(line)
        if m:
            level = len(m.group(1))
            headings.append((level, m.group(2), i))
            if level == 1 and not title and not _PAGE_HEADING.match(m.group(2)):
                title = m.group(2)
            i += 1
            continue
        # setext 式标题
        if i + 1 < len(lines) and _SETEXT_RE.match(lines[i + 1]) and line.strip():
            level = 1 if lines[i + 1].strip().startswith("=") else 2
            headings.append((level, line.strip(), i))
            if level == 1 and not title:
                title = line.strip()
            i += 2
            continue
        i += 1
    return title, lines, headings


def parse_markdown(text: str, paper_id: str, source_path: str = "", kind: str = "md") -> Paper:
    meta, _ = _strip_frontmatter(text)
    title, lines, headings = markdown_to_blocks(text)

    # -- 组装章节 ---------------------------------------------------------- #
    sections: List[Section] = []
    for idx, (level, heading, line_idx) in enumerate(headings):
        # 只把 level<=3 视为分节，更深的标题视为正文
        if level > 3:
            continue
        end = len(lines)
        for lvl2, _, li2 in headings:
            if li2 > line_idx and lvl2 <= level:
                end = li2
                break
        body = "\n".join(lines[line_idx + 1 : end]).strip()
        kind_name = extract.classify_heading(heading)
        sections.append(Section(heading=heading, level=level, text=body, kind=kind_name))

    # -- 摘要：优先 frontmatter，其次章节 ---------------------------------- #
    abstract = meta.get("abstract", "")
    if not abstract:
        for s in sections:
            if s.kind == "abstract":
                abstract = s.text
                break
    if abstract:
        abstract = re.sub(r"\s*\n\s*", " ", abstract).strip()
        if not any(s.kind == "abstract" for s in sections):
            sections.insert(0, Section(heading="Abstract", level=2, text=abstract, kind="abstract"))

    # -- 全局抽取（在完整行序列上做，保证跨章节的表格/图不漏） -------------- #
    tables = extract.extract_tables(lines)
    figures = extract.extract_figures(lines)
    algorithms = extract.extract_algorithms(lines)
    code_refs, data_refs = extract.extract_code_refs(lines)
    code_stmt = extract.find_code_statement(lines)
    formulas = extract.extract_formulas(lines)

    authors = _split_authors(meta.get("authors", ""))

    paper = Paper(
        id=paper_id,
        title=title or meta.get("title", "Untitled"),
        authors=authors,
        abstract=abstract,
        source_path=source_path,
        source_kind=kind,
        sections=sections,
        tables=tables,
        figures=figures,
        algorithms=algorithms,
        formulas=formulas,
        code_refs=code_refs,
        data_refs=data_refs,
        raw_text="\n".join(lines),
        meta={"frontmatter": meta, "code_statement": code_stmt},
    )
    return paper


# --------------------------------------------------------------------------- #
# HTML -> 文本 / 结构
# --------------------------------------------------------------------------- #
class _HTMLPaperParser(HTMLParser):
    """极简 HTML 解析：抽取 title / h1-h3 / 表格 / 段落文本。"""

    _BLOCK = {"p", "div", "li", "br", "tr", "section", "article", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.headings: List[Tuple[int, str]] = []
        self.lines: List[str] = []
        self.tables: List[Tuple[List[str], List[List[str]]]] = []

        self._in_title = False
        self._heading_level = 0
        self._heading_buf: List[str] = []
        self._buf: List[str] = []
        self._skip_depth = 0
        self._table: Optional[Dict[str, object]] = None
        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None
        self._in_script = False

    # -- 生命周期 ---------------------------------------------------------- #
    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style", "nav", "footer", "svg"):
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        elif re.fullmatch(r"h[1-6]", tag):
            self._heading_level = int(tag[1])
            self._heading_buf = []
        elif tag == "table":
            self._table = {"rows": []}
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag in self._BLOCK:
            self._buf.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "nav", "footer", "svg"):
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "title":
            self._in_title = False
        elif re.fullmatch(r"h[1-6]", tag):
            text = _clean("".join(self._heading_buf))
            if text:
                self.headings.append((self._heading_level, text))
                self.lines.append("#" * self._heading_level + " " + text)
            self._heading_level = 0
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(_clean("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._table is not None and self._row is not None:
            self._table["rows"].append(self._row)  # type: ignore[union-attr]
            self._row = None
        elif tag == "table" and self._table is not None:
            rows = self._table["rows"]  # type: ignore[index]
            if rows:
                self.tables.append((list(rows[0]), [list(r) for r in rows[1:]]))
            self._table = None
        elif tag in self._BLOCK:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data
            return
        if self._heading_level:
            self._heading_buf.append(data)
        elif self._cell is not None:
            self._cell.append(data)
        else:
            self._buf.append(data)

    def _flush(self) -> None:
        text = _clean("".join(self._buf))
        self._buf = []
        if text:
            self.lines.append(text)

    def close(self) -> None:  # type: ignore[override]
        super().close()
        self._flush()


def _clean(text: str) -> str:
    return re.sub(r"[ \t\u00a0]+", " ", text).strip()


def html_to_text(html: str) -> Tuple[str, List[str], List[Tuple[List[str], List[List[str]]]]]:
    parser = _HTMLPaperParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    return parser.title, parser.lines, parser.tables


def parse_html(html: str, paper_id: str, source_path: str = "") -> Paper:
    title, lines, html_tables = html_to_text(html)
    paper = parse_markdown("\n".join(lines), paper_id, source_path=source_path, kind="html")
    if title.strip():
        paper.title = _clean(title)

    # 把 HTML 原生表格并入（Markdown 管道表可能为空）
    for idx, (header, rows) in enumerate(html_tables, start=1):
        if len(rows) <= 1 and len(header) <= 1:
            continue
        tid = f"T{len(paper.tables) + idx}"
        from ..models import Table

        paper.tables.append(Table(id=tid, caption="", header=list(header), rows=[list(r) for r in rows], source="html"))
    return paper
