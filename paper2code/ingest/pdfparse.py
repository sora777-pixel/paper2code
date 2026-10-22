"""PDF 解析。

优先使用 PyMuPDF（fitz）取文本与页面图像；表格抽取优先 pdfplumber
（对网格线表格更稳），失败则退回「按空白列切分」的启发式。
两条依赖均为可选：缺失时返回带警告的空结构，不影响其余链路。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import textutil
from ..models import Paper, Section, Table
from . import extract, figassets
from .mdparse import parse_markdown

#: 正文小标题——一旦在首页扫到它们，说明标题区已经结束
_BOILERPLATE_HEADINGS = {
    "abstract", "introduction", "contents", "index", "keywords", "summary",
    "references", "1 introduction", "1. introduction",
    "摘要", "前言", "目录", "关键词", "参考文献", "引言",
}
#: 分页标记——**刻意不用** ``#`` 前缀，否则 markdown 解析会把它当成章节标题。
#: 识别逻辑与 markdown 链路共用（见 :func:`extract.is_page_marker`）。
_is_page_marker = extract.is_page_marker


def _page_marker(n: int) -> str:
    return f"[[PAGE:{n}]]"


#: pdfplumber 的两种取表策略：先有框线的，再纯文本对齐的
_STRATEGY_LINES = {"vertical_strategy": "lines", "horizontal_strategy": "lines"}
_STRATEGY_TEXT = {"vertical_strategy": "text", "horizontal_strategy": "text"}


def _text_tables_enabled() -> bool:
    """``text`` 策略是否启用（默认关）。

    实测在三篇真实 PDF 上，该策略只会把**双栏正文切成「版面糊」**（整页列切片、
    数值占比 0），合法表产出为 **0**，代价却是 19 页约 5–6s。因此默认关闭，
    需要时用 ``P2C_PDF_TEXT_TABLES=1`` 打开——它仍然是可用的能力，只是不再
    无条件地为每篇论文付出这个代价。
    """
    return os.environ.get("P2C_PDF_TEXT_TABLES", "").strip().lower() not in ("", "0", "false", "no")

#: 正文里引用表格的写法（``Table 3`` / ``Table S1``）
_TABLE_REF_RE = re.compile(r"\bTable\s+[Ss]?\d+\b")
#: 表题行：``Table 3. xxx`` / ``Table 2``（表题折到下一行）
_TABLE_CAP_RE = re.compile(r"^\**\s*Table\s+([Ss]?\d+[a-zA-Z]?)\s*([.:：]?)\s*(.*)$")


#: 首页里属于「版式噪声」的大字号行——期刊刊名、栏目名、页眉页脚等
_TITLE_BOILERPLATE = (
    "supporting information", "supplementary", "electronic supplementary",
    "journal homepage", "sciencedirect", "contents lists available",
    "this article is licensed", "cite this", "read online", "downloaded from",
    "abstract", "keywords", "copyright", "all rights reserved",
    "received", "accepted", "revised", "available online",
    "doi:", "https://doi.org", "arxiv", "preprint",
    "check for updates", "article recommendations", "metrics & more",
)

#: arXiv 首页的投稿戳（``arXiv:2301.10814v2 [q-bio.BM] 12 Dec 2023``）。
#: 实测字号常为 20pt，比标题（14–17pt）更大；若不先按版式噪声剔除，它会成为
#: 「页内最大字号」，把真标题全部挤到 ``0.9×20`` 以下（11 篇 arXiv 论文因此读空）。
_ARXIV_STAMP_RE = re.compile(r"^\s*arXiv:\s*\d{4}\.\d{4,5}", re.IGNORECASE)

#: 占位标题：``parse_markdown`` 在没读出标题时会写 ``Untitled``。
#: 它不能算「已有标题」，否则退化路径会直接返回它、不再看首页版面。
_EMPTY_TITLE_VALUES = {"", "untitled", "no title", "无标题", "未命名"}


def _norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


#: 编号标题：``2.1. Physicochemical …``。**必须带末尾的点**，否则正文里的
#: ``2.4 web server`` / ``128 neurons`` 会被误判成标题。
_HEADING_NUM_RE = re.compile(r"^(\d+(?:\.\d+)*)\.\s+\S")
#: ACS 等期刊用 ■ 前缀的栏目头
_BANNER_RE = re.compile(r"^\s*[■▮▪]\s*(.+)$")
_BANNER_BOILERPLATE = {
    "access", "article", "read online", "open access", "metrics & more",
    "article recommendations", "supporting information", "downloaded from",
}


def _body_size(rows: List[Dict[str, Any]]) -> float:
    """页面正文字号 = 按字符数加权的众数字号。"""
    counts: Dict[float, int] = {}
    for r in rows:
        key = round(r["size"] * 2) / 2
        counts[key] = counts.get(key, 0) + len(r["text"])
    return max(counts.items(), key=lambda kv: kv[1])[0] if counts else 0.0


def _heading_level(text: str, size: float, body: float) -> int:
    """判断一行是不是标题；是则返回 markdown 级别（2=节，3=小节），否则 0。

    PDF 真标题不带 ``#``，光靠字号也不行（实测 P1 的标题与正文同为 8pt）。
    两条实测有效的判据：
    1. **编号** ``\\d+.\\d*. ``（带末尾点）+ 长度 ≤90 + 不以逗号结尾——P1 的
       25 个章节全中，而正文里的 ``2.4 web server``、``128 neurons`` 全被排除；
    2. **栏目头**：``■`` 前缀（P2 的 ACS 版式）或全大写且字号 ≥1.5×正文
       ——后者挡掉了 SI 里 618 条同字号的表格单元格大写文本。
    """
    t = (text or "").strip()
    if not t:
        return 0
    m = _HEADING_NUM_RE.match(t)
    if m and len(t) <= 90 and not t.endswith((",", "，")):
        depth = m.group(1).count(".") + 1
        return 2 if depth == 1 else 3

    banner = _BANNER_RE.match(t)
    if banner:
        core = banner.group(1).strip()
        if core.lower() in _BANNER_BOILERPLATE or len(core) > 60 or not core:
            return 0
        return 2

    letters = [c for c in t if c.isalpha()]
    if (
        letters
        and all(c.isupper() for c in letters)
        and 3 <= len(t) <= 60
        and size >= 1.5 * body
        and t.lower() not in _BANNER_BOILERPLATE
    ):
        return 2
    return 0


def _mark_heading_row(row: Dict[str, Any], body: float) -> str:
    """把标题行写成 markdown 标题；非标题行原样返回。"""
    level = _heading_level(row["text"], row["size"], body)
    if not level:
        return row["text"]
    banner = _BANNER_RE.match(row["text"])
    text = banner.group(1).strip() if banner else row["text"]
    return "#" * level + " " + text


def _page_lines_with_size(page: Any) -> List[Dict[str, Any]]:
    """把一页文本按「行」聚合，带上该行最大字号与左上角坐标。"""
    out: List[Dict[str, Any]] = []
    try:
        d = page.get_text("dict")
    except Exception:
        return out
    for blk in d.get("blocks", []):
        if blk.get("type") != 0:
            continue
        for ln in blk.get("lines", []):
            spans = ln.get("spans") or []
            txt = "".join(s.get("text", "") for s in spans).strip()
            if not txt:
                continue
            size = max((s.get("size", 0.0) for s in spans), default=0.0)
            bb = ln.get("bbox") or (0.0, 0.0, 0.0, 0.0)
            out.append({"text": txt, "size": size, "x": bb[0], "y": bb[1]})
    return out


def _page_image_blocks(page: Any, pno: int) -> List[Dict[str, Any]]:
    """收一页的位图：用 ``get_images`` + ``get_image_rects``。

    注意 ``page.get_text("dict")`` 里图片块的 ``number`` **不是** xref（实测 P1
    p5 的块 number=5 而真实 xref=64），拿它去 ``extract_image`` 会抛异常；
    而 ``get_image_info(xrefs=True)`` 虽能给出 xref，但实测更慢（0.52s vs 0.30s）。
    """
    out: List[Dict[str, Any]] = []
    try:
        imgs = page.get_images(full=True)
    except Exception:
        return out
    for img in imgs:
        xref, w, h = img[0], img[2], img[3]
        if min(w, h) < figassets.MIN_INTRINSIC:
            continue  # 先按固有像素过滤，省掉对小图标的 rect 计算
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            rects = []
        if not rects:
            continue
        r = rects[0]
        out.append({
            "page": pno, "xref": xref, "w": w, "h": h,
            "rect": (r.x0, r.y0, r.x1, r.y1), "x": r.x0, "y": r.y0,
        })
    return out


def _looks_like_masthead(line: Dict[str, Any], all_lines: List[Dict[str, Any]],
                         height: float = 0.0) -> bool:
    """期刊刊名常常**同时**以小字号出现在页眉/页脚里，借此识别版式噪声。

    只有小字号重复行确实位于页眉/页脚（上/下 10%）时才算刊名。否则正文里
    任何以相同词开头的句子都会把跨行标题的第二行误杀——实测 MECo 的
    ``MOLECULAR OPTIMIZATION`` 就被摘要首句 ``Molecular optimization is ...``
    判成了 running head，标题丢掉后半截。
    """
    key = _norm_space(line["text"])
    if len(key) < 6:
        return False
    height = float(height or 0.0)
    for other in all_lines:
        if other is line or other["size"] >= 0.6 * line["size"]:
            continue
        if not _norm_space(other["text"]).startswith(key):
            continue
        if height and not (other["y"] < 0.10 * height or other["y"] > 0.90 * height):
            continue
        return True
    return False


def _join_title_fragments(a: str, b: str) -> str:
    """合并跨行断开的标题片段；单独成行的破折号直接对接，不留空格。"""
    a, b = a.strip(), b.strip()
    if not a:
        return b
    if not b:
        return a
    if b in ("-", "–", "—", "−") or a.endswith(("-", "–", "—", "−")):
        return a + b
    return a + " " + b


def _front_matter(path: Path) -> Dict[str, Any]:
    """打开 PDF **首页一次**，取标题 / 作者 / 摘要。

    原先标题、作者、摘要各开一次 PDF，19 页文档要解析三遍；这里合并成一次。
    失败（无 PyMuPDF / 文件损坏）时返回空 dict，调用方各自退化。
    """
    try:
        import fitz  # PyMuPDF
    except Exception:
        return {}
    try:
        doc = fitz.open(str(path))
    except Exception:
        return {}
    try:
        if doc.page_count == 0:
            return {}
        page = doc[0]
        height = float(page.rect.height) or 1.0
        rows = _page_lines_with_size(page)
    finally:
        doc.close()
    if not rows:
        return {}
    head = _candidate_title_rows(rows, height)
    return {
        "title": _merge_rows(head),
        "authors": authors_from_rows(rows, head, height),
        "abstract": abstract_from_rows(rows, height),
    }


def _merge_rows(head: List[Dict[str, Any]]) -> str:
    if not head:
        return ""
    merged = head[0]["text"]
    for r in head[1:]:
        merged = _join_title_fragments(merged, r["text"])
    return merged.strip()


def _candidate_title_rows(rows: List[Dict[str, Any]], height: float) -> List[Dict[str, Any]]:
    """挑出构成标题的连续行（字号相近 + y 相邻）。

    顺序很重要：**先剔除版式噪声，再取最大字号**。反过来会被 arXiv 首页戳
    （20pt，通常比标题更大）带偏——它被 boilerplate 过滤后，真标题全部低于
    ``0.9×20``，候选集为空（实测 11 篇 arXiv 论文标题读不出来）。
    """
    if not rows:
        return []
    height = height or 1.0
    # 只看首页上半部分，避免把页面深处的章节标题卷进来
    top = [r for r in rows if r["y"] < 0.5 * height] or rows

    filtered: List[Dict[str, Any]] = []
    for r in top:
        if _ARXIV_STAMP_RE.match(r["text"]):
            continue
        low = _norm_space(r["text"])
        if any(h in low for h in _TITLE_BOILERPLATE):
            continue
        if "@" in r["text"] or low.startswith(("http", "www")):
            continue
        if _looks_like_masthead(r, rows, height):
            continue
        filtered.append(r)
    if not filtered:
        return []

    max_size = max(r["size"] for r in filtered)
    if max_size <= 0:
        return []
    filtered = [r for r in filtered if r["size"] >= 0.9 * max_size]

    filtered.sort(key=lambda r: (round(r["y"], 1), r["x"]))
    head = [filtered[0]]
    prev = filtered[0]
    for r in filtered[1:]:
        gap = r["y"] - prev["y"]
        # 2.6×：覆盖「字号 14–18pt、行距 31–42pt」的大标题。实测 DeepHostGuest /
        # Quantum 的跨行行距是 2.2–2.3× 字号，旧的 1.8× 会把第二行截掉。
        if abs(r["size"] - prev["size"]) <= 0.6 and gap <= 2.6 * max(prev["size"], r["size"]):
            head.append(r)
            prev = r
        else:
            break
    return head


def title_from_rows(rows: List[Dict[str, Any]], height: float) -> str:
    """用「字号 + boilerplate 过滤 + 跨行合并」从首页版面取标题。

    双栏/期刊版式里「最长的一行」或「最大字号的一行」都不能直接当标题：
    刊名、栏目名往往字号更大，而标题本身常跨行断开。因此：
    1. 取首页上半部分字号 ≥ 0.9×页内最大字号的行；
    2. 剔除 boilerplate（DOI/版权/摘要/关键词…）与「刊名」（刊名会同时
       以小字出现在页眉页脚，见 :func:`_looks_like_masthead`）；
    3. 位置相邻且字号相近的行合并成一行。
    """
    return _merge_rows(_candidate_title_rows(rows, height))


#: 机构行 = 作者行到此为止
_AFFIL_HINTS = (
    "university", "universit", "institute", "institut", "department", "departmento",
    "laboratory", "laborator", "college", "foundation", "centre", "center", "school",
    "hospital", "academy", "faculty", "brazil", "norway", "china", "japan", "germany",
    "france", "india", "usa", "u.s.a", "u.k", "recife", "oslo", "london", "são paulo",
)
#: 作者行之后的版式噪声
_AUTHOR_STOP_HINTS = (
    "cite this", "read online", "downloaded from", "abstract", "keywords",
    "journal homepage", "sciencedirect", "contents lists available",
    "supporting information", "open access", "accepted", "received", "published",
)
_NAME_WORD_RE = re.compile(r"^[A-Z][a-zA-Z\u00c0-\u024f'.\-]*$")


def _looks_like_name_list(text: str) -> bool:
    """作者行判据：逗号分隔的「大写开头」名字串，而不是散文或机构名。"""
    t = (text or "").strip()
    if not (6 <= len(t) <= 400):
        return False
    if not re.search(r"[A-Za-z\u00c0-\u024f]", t):
        return False
    words = [w for w in re.split(r"[,\s]+", t) if w]
    caps = sum(1 for w in words if _NAME_WORD_RE.match(w.strip("*\u2020\u2021")))
    if len(t) > 60 and len(words) and caps / len(words) < 0.5:
        return False
    return caps >= 2 and ("," in t or caps >= 3)


def authors_from_rows(
    rows: List[Dict[str, Any]], head: List[Dict[str, Any]], height: float
) -> List[str]:
    """从标题下方的 1–3 行取作者名（遇到机构/版权行即停）。

    实测：P1 的作者行夹在标题与机构行之间；P2 是两行 ACS 作者；P3 是两行
    带机构角标的作者。三者的共同点是「紧跟标题、逗号分隔的大写名字串」，
    而机构行与版权行含 ``University``/``Cite This`` 等词，用来收口。
    """
    if not head:
        return []
    title_bottom = max(r["y"] for r in head)
    title_size = max(r["size"] for r in head)

    picked: List[str] = []
    for r in sorted(rows, key=lambda r: (round(r["y"], 1), r["x"])):
        if r["y"] <= title_bottom:
            continue
        t = r["text"].strip()
        low = t.lower()
        if any(h in low for h in _AUTHOR_STOP_HINTS) or any(h in low for h in _AFFIL_HINTS):
            break
        if "@" in t or low.startswith(("http", "www")):
            break
        if r["size"] > title_size or r["size"] < 0.5 * title_size:
            break
        if not _looks_like_name_list(t):
            continue
        picked.append(t)
        if len(picked) >= 3:
            break
    if not picked:
        return []
    out: List[str] = []
    for p in re.split(r",|;|\band\b|\u3001", " ".join(picked)):
        p = p.strip()
        # 丢掉机构角标碎片（如 "Roberto D. Linsa,c,*" 里的 "c" 与 "*"）
        if not p or re.fullmatch(r"[a-z]", p) or not re.search(r"[A-Za-z\u00c0-\u024f]", p):
            continue
        out.append(p)
    return out


#: 摘要标签：``ABSTRACT`` / ``A B S T R A C T``（期刊常见字距排版）/ 中文
_ABSTRACT_LABEL_RE = re.compile(
    r"^\s*(?:A\s+B\s+S\s+T\s+R\s+A\s+C\s+T|ABSTRACT|SUMMARY|摘\s*要)\s*[:：]?\s*(.*)$",
    re.IGNORECASE,
)


def abstract_from_rows(rows: List[Dict[str, Any]], height: float) -> str:
    """按版面取摘要：找到 ``ABSTRACT`` 标签后，只收**同一栏**的后续行。

    双栏版式里正文抽取会把左栏（关键词清单）与右栏（摘要正文）交错输出，
    所以不能简单地把标签后的行全部拼起来——必须按 x 坐标限制在同一栏内，
    并在遇到章节标题（见 :func:`_heading_level`）或栏末大间隙时收口。
    """
    if not rows:
        return ""
    height = height or 1.0
    ordered = sorted(rows, key=lambda r: (round(r["y"], 1), r["x"]))
    body = _body_size(rows)
    for i, r in enumerate(ordered):
        m = _ABSTRACT_LABEL_RE.match(r["text"])
        if not m:
            continue
        parts = [m.group(1).strip()] if m.group(1).strip() else []
        x0, y0 = r["x"], r["y"]
        cursor = y0
        for r2 in ordered[i + 1 :]:
            if r2["y"] <= y0:
                continue
            if r2["y"] - y0 > 0.6 * height:
                break
            if abs(r2["x"] - x0) > 45:  # 换栏了
                continue
            t = r2["text"].strip()
            if not t:
                continue
            if _heading_level(t, r2["size"], body):
                break
            if any(h in t.lower() for h in ("keywords", "introduction", "references")):
                break
            if r2["y"] - cursor > 2.5 * max(r2["size"], 1.0) + 8:  # 栏末大间隙
                break
            parts.append(t)
            cursor = r2["y"]
        text = re.sub(r"\s+", " ", " ".join(parts)).strip()
        if len(text) >= 100:
            return text
    return ""


def _guess_title(lines: List[str], current: str) -> str:
    """PDF 没有 ``#`` 标记，标题得靠版面启发式猜。

    策略：从首页往下扫，遇到 Abstract/Introduction 这类正文小标题就停；
    在此之前取「最长且像标题的一行」。论文标题通常是首页最长的一行，
    而作者行/机构行更短、邮箱行含 ``@``、页码行是纯数字，都能被过滤掉。
    """
    cur = (current or "").strip()
    low_cur = cur.strip("#").strip().lower()
    # `Untitled` 是 parse_markdown 的占位值，不是真标题；必须继续看首页版面。
    if (cur and low_cur not in _EMPTY_TITLE_VALUES
            and low_cur not in _BOILERPLATE_HEADINGS and not _is_page_marker(cur)):
        return cur

    best = ""
    for raw in lines[:60]:
        s = raw.strip().lstrip("#").strip()
        if not s:
            continue
        if _ARXIV_STAMP_RE.match(s):
            continue
        low = s.lower().rstrip(":.")
        if low in _BOILERPLATE_HEADINGS:
            break  # 已进入正文，标题区结束
        if _is_page_marker(s):
            continue
        if len(s) < 12 or len(s) > 220:
            continue
        if "@" in s or s.startswith(("http://", "https://")):
            continue
        if not re.search(r"[A-Za-z\u4e00-\u9fff]", s):
            continue
        if s.endswith((".", ",", ";", ":")):
            continue
        if len(s) > len(best):
            best = s
    return best or cur


def _read_pdf_text(path: Path) -> Tuple[List[str], List[Dict[str, Any]], int, List[str]]:
    """返回 ``(逐页文本行, 页面图片信息, 真实页数, 警告)``。"""
    warnings: List[str] = []
    lines: List[str] = []
    images: List[Dict[str, Any]] = []
    pages = 0
    try:
        import fitz  # PyMuPDF
    except Exception:
        return [], [], 0, ["未安装 PyMuPDF，PDF 正文解析被跳过（pip install pymupdf）"]

    try:
        doc = fitz.open(str(path))
    except Exception as exc:  # pragma: no cover - 文件损坏等
        return [], [], 0, [f"打开 PDF 失败：{exc}"]

    pages = doc.page_count
    for pno, page in enumerate(doc):
        try:
            rows = _page_lines_with_size(page)
        except Exception:
            rows = []
        if rows:
            # 版面识别出的真标题在这里写成 markdown 标题，交给 classify_heading
            body = _body_size(rows)
            page_text = [_mark_heading_row(r, body) for r in rows]
        else:  # 退化路径：拿不到 dict 时退回纯文本
            try:
                page_text = page.get_text("text").splitlines()
            except Exception:
                page_text = []
        if pno > 0:
            lines.append(f"\n{_page_marker(pno + 1)}\n")
        # PDF 抽取常混入 NBSP / 零宽字符 / ﬁ ﬂ 连字，先归一化再往下走
        lines.extend(textutil.normalize_unicode(t) for t in page_text)
        # 位图顺手在同一趟页面循环里收好，供 figassets 导出（省掉第二次全页扫描）
        images.extend(_page_image_blocks(page, pno + 1))
    doc.close()
    return lines, images, pages, warnings


def _extract_tables_pdfplumber(path: Path, force_text: bool = False) -> List[Tuple[int, Table]]:
    """双策略抽表 + 结构合法性校验，返回 ``[(页码, 表格)]``。

    ``force_text=True``：即使环境变量没开，也把 ``text`` 策略加进来
    （E 阶段的零召回自动重试）；``require_numeric=True`` 会把数值占比 <0.3
    的「版面糊」挡掉，因此重试不会凭空造表。

    策略：
    1. 先 ``vertical/horizontal = lines``——有框线 / 三线表；
    2. 该页没抽到合法表格时退回 ``text``——纯文本对齐表。

    ``text`` 策略在**双栏版式**上会把正文切成「版面糊」（整页的列切片，
    字形被切到词中间），因此对它额外要求「数值占比 ≥ 0.3」，避免把版面
    误报成表格；这正是本工具早前把 P1 报成「19 张表」的假阳性来源。
    """
    try:
        import pdfplumber  # type: ignore
    except Exception:
        return []

    out: List[Tuple[int, Table]] = []
    strategies: List[Tuple[Dict[str, str], bool]] = [(_STRATEGY_LINES, False)]
    if force_text or _text_tables_enabled():
        strategies.append((_STRATEGY_TEXT, True))
    try:
        with pdfplumber.open(str(path)) as pdf:
            # 先整体试 lines 策略；一张都没抽到，才整体退回 text 策略（若启用）
            for kw, require_numeric in strategies:
                found: List[Tuple[int, List[List[str]]]] = []
                for page in pdf.pages:
                    # 直接用 extract_tables（一次调用拿到行内容）；不再走
                    # find_tables + extract() 的两步，省掉一轮重复取单元格文本
                    for raw in page.extract_tables(kw) or []:
                        rows = [
                            [(c or "").replace("\n", " ").strip() for c in r]
                            for r in raw if r
                        ]
                        if _valid_table_rows(rows, require_numeric=require_numeric):
                            found.append((page.page_number, _pad_rows(rows)))
                if found:
                    out = [
                        (
                            pg,
                            Table(
                                id=f"T{i + 1}",
                                caption="",
                                header=rows[0],
                                rows=rows[1:],
                                source="pdf",
                            ),
                        )
                        for i, (pg, rows) in enumerate(found)
                    ]
                    break
    except Exception:
        return out
    return out


def _pad_rows(rows: List[List[str]]) -> List[List[str]]:
    width = max(len(r) for r in rows)
    return [list(r) + [""] * (width - len(r)) for r in rows]


# --------------------------------------------------------------------------- #
# 无框线表格（三线表 / 文本对齐表）
# --------------------------------------------------------------------------- #
_BORDERLESS_CAP_RE = re.compile(r"^Table\s*([Ss]?\d+[a-zA-Z]?)\s*[.:：]?")
#: 表头行里「列与列」的最小水平间隙（词间距通常只有 2–4 px）
_COL_GAP = 12


def _words_to_rows(words: List[Dict[str, Any]], tol: float = 2.6
                   ) -> List[Dict[str, Any]]:
    """把 pdfplumber 的词按 ``top`` 聚成视觉行。"""
    rows: List[Dict[str, Any]] = []
    for w in sorted(words, key=lambda x: (x["top"], x["x0"])):
        if rows and abs(w["top"] - rows[-1]["top"]) <= tol:
            rows[-1]["words"].append(w)
            rows[-1]["top"] = min(rows[-1]["top"], w["top"])
            rows[-1]["bottom"] = max(rows[-1]["bottom"], w["bottom"])
        else:
            rows.append({"top": w["top"], "bottom": w["bottom"], "words": [w]})
    for r in rows:
        r["words"].sort(key=lambda x: x["x0"])
        r["text"] = "".join(x["text"] for x in r["words"])
        r["text_ws"] = " ".join(x["text"] for x in r["words"])
        r["x0"] = min(x["x0"] for x in r["words"])
        r["x1"] = max(x["x1"] for x in r["words"])
        r["size"] = max(x.get("size", 0) or 0 for x in r["words"])
    return rows


def _split_cells(words: List[Dict[str, Any]], gap: float = _COL_GAP
                 ) -> List[List[Dict[str, Any]]]:
    """按水平间隙把一行词切成单元格。"""
    cells: List[List[Dict[str, Any]]] = []
    for w in sorted(words, key=lambda x: x["x0"]):
        if cells and w["x0"] - cells[-1][-1]["x1"] <= gap:
            cells[-1].append(w)
        else:
            cells.append([w])
    return cells


def _split_cells_adaptive(words: List[Dict[str, Any]],
                          target: int = 0) -> List[List[Dict[str, Any]]]:
    """表头专用：列间距很紧时（如 ``BEDROC EF0.5% EF1%``）用更小的 gap 再切。

    正文词间距通常只有 2–4px，而列间距 ≥12px；但真实期刊表头常把 7 个词
    压进 3 个「伪单元格」。这里以**数据行的单元格数**为目标，在 4–12px
    之间选最接近的一种切法，避免表头与表体列数对不上。
    """
    if not words:
        return []
    candidates = [_split_cells(words, gap=g) for g in (12, 10, 9, 8, 7, 6, 5, 4)]
    if target <= 0:
        return candidates[0]
    return min(candidates, key=lambda c: (abs(len(c) - target), -len(c)))


def _split_row_cells(words: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """数据行切列：在 12px 基础上尝试更小 gap，**以数值单元格数最多**为准。

    实测 mcemol 的 8 列数值表相邻列间隙只有 11px，默认 12px 会把
    ``2.41 4.48 540.75`` 并成一格；缩小 gap 能把 8 列还原出来，同时不会
    因为「非数值格变多」而误切文本列（只在数值格数严格增加时才换切法）。
    """
    cells = _split_cells(words)
    score = sum(1 for c in cells if any(_numish(w["text"]) for w in c))
    for gap in (10, 9, 8, 7, 6, 5):
        cand = _split_cells(words, gap=gap)
        s = sum(1 for c in cand if any(_numish(w["text"]) for w in c))
        if s > score:
            cells, score = cand, s
    return cells


def _median_size(words: List[Dict[str, Any]]) -> float:
    sizes = sorted(float(w.get("size") or 0) for w in words if w.get("size"))
    return sizes[len(sizes) // 2] if sizes else 0.0


def _caption_clean(s: str) -> str:
    """表题清洗：``Table1.`` → ``Table 1.``；拆分 PDF 里粘连的驼峰词。"""
    s = re.sub(r"^Table\s*([Ss]?\d+[a-zA-Z]?)\s*[.:：]?\s*",
               lambda m: "Table %s. " % m.group(1), s)
    s = re.sub(r"(?<=[a-z])(?=[A-Z][a-z])", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _extract_tables_borderless(path: Path) -> List[Tuple[int, Table]]:
    """按 ``Table N`` 表题锚点抽取**无框线表格**。

    为什么单独一条路：期刊三线表/文本对齐表没有横竖线，pdfplumber 的
    ``lines`` 策略抽不到；而全局 ``text`` 策略会把双栏正文切成「版面糊」。
    这里以表题为锚点——表题下方同一列区域内，按词坐标切列、按 ``top`` 聚行，
    召回高又不会污染其他版面。

    判定条件（宁缺勿滥）：表头 ≥2 列、数据行 ≥2、行内至少一个数值单元格，
    且数据行里带数值的比例 ≥ 0.25。
    """
    try:
        import pdfplumber  # type: ignore
    except Exception:
        return []

    out: List[Tuple[int, Table]] = []
    try:
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                words = page.extract_words(extra_attrs=["size"])
                if len(words) < 20:
                    continue
                for table, caption in _borderless_tables_on_page(page, words):
                    out.append((page.page_number, Table(
                        id=f"B{len(out) + 1}",
                        caption=caption,
                        header=table[0],
                        rows=table[1:],
                        source="pdf-borderless",
                    )))
    except Exception:
        return out
    return out


def _is_table_caption_word(w: Dict[str, Any], words: List[Dict[str, Any]]) -> bool:
    """判断某个词是否是 ``Table N`` 表题的开头。

    两种排版都要认：``Table1.Sizeof…``（词内粘连）与 ``Table`` + ``1.``
    （编号是独立词）。
    """
    text = re.sub(r"\s+", "", w.get("text") or "")
    if _BORDERLESS_CAP_RE.match(text):
        return True
    if not re.fullmatch(r"Table[.:：]?", text, re.I):
        return False
    nxt = [x for x in words if abs(x["top"] - w["top"]) <= 2.6 and x["x0"] > w["x1"]]
    if not nxt:
        return False
    n = min(nxt, key=lambda x: x["x0"])
    combined = text + re.sub(r"\s+", "", n.get("text") or "")
    return bool(_BORDERLESS_CAP_RE.match(combined))


def _header_cells_from_chars(page: Any, header_row: Dict[str, Any],
                             spans: List[Tuple[float, float]]) -> Optional[List[str]]:
    """用**字符坐标**把表头文字分配到数据列，解决“整行表头被压成一个词”。

    实测 mcemol：``Validity(%)DiversitySimilarityNovelScaffoldDiversity`` 在
    文本层是一个 word，词级切分永远切不开；但每个字符仍有 x 坐标，且数据行的
    列中心可由数值单元格稳定得到。这里按“字符中心最近的列中心”归并。
    """
    try:
        chars = list(page.chars) if page is not None else []
    except Exception:
        return None
    if not chars or not spans:
        return None
    top = float(header_row.get("top") or 0.0)
    tol = max(2.0, float(header_row.get("size") or 0.0) * 0.45)
    row_chars = [c for c in chars if abs(float(c.get("top") or 0.0) - top) <= tol]
    if not row_chars:
        return None
    centers = [((s[0] + s[1]) / 2.0) for s in spans]
    # 先按字符间隙把表头还原成「词/短语 token」，再整块分配到最近的列——
    # 逐字符分配会把单词从中间截断（实测会切出 'Similarit' / 'yNovelSc'）。
    tokens: List[Dict[str, Any]] = []
    for c in sorted(row_chars, key=lambda x: float(x["x0"])):
        x0, x1 = float(c["x0"]), float(c["x1"])
        if tokens and x0 - tokens[-1]["x1"] <= 2.5:
            tokens[-1]["text"] += str(c.get("text") or "")
            tokens[-1]["x1"] = max(tokens[-1]["x1"], x1)
        else:
            tokens.append({"text": str(c.get("text") or ""), "x0": x0, "x1": x1})
    out = [""] * len(spans)
    for t in tokens:
        if not re.search(r"\S", t["text"]):
            continue
        cx = (t["x0"] + t["x1"]) / 2.0
        idx = min(range(len(centers)), key=lambda i: abs(centers[i] - cx))
        out[idx] = (out[idx] + " " + t["text"]).strip()
    out = [re.sub(r"\s+", " ", s).strip() for s in out]
    return out if sum(1 for s in out if s) >= 2 else None


def _borderless_tables_on_page(page: Any, words: List[Dict[str, Any]]
                               ) -> List[Tuple[List[List[str]], str]]:
    """在单页里找所有「表题 + 无框线表体」。"""
    med = _median_size(words)
    tiny_ids = {id(w) for w in words if float(w.get("size") or 0) < med * 0.6}
    words = [w for w in words if id(w) not in tiny_ids]   # 上标脚注、页边竖排文字等噪声

    anchors: List[Dict[str, Any]] = []
    for w in words:
        if not _is_table_caption_word(w, words):
            continue
        if med and float(w.get("size") or 0) < med * 0.85:
            continue                            # 脚注/图例里的 Table 字样
        # 行首判定：同一 top 上紧邻左侧（<15px）有正文词 → 这是「shown in Table 1.」
        left = [x for x in words
                if abs(x["top"] - w["top"]) <= 2.6 and x["x1"] < w["x0"] + 0.5]
        if left and w["x0"] - max(x["x1"] for x in left) < 15:
            continue
        anchors.append(w)

    found: List[Tuple[List[List[str]], str]] = []
    for a in anchors:
        # 表题可能在右栏，而表格本身从页面中部/左栏开始（mcemol 即如此）。
        # 先用「下方含≥2 个数值单元格的行」的最左边界估计表格左沿，避免丢掉首列。
        below = [w for w in words if w["top"] >= a["top"] + 2]
        left_edges: List[float] = []
        for r in _words_to_rows(below):
            cells = _split_row_cells(r["words"])
            if sum(1 for c in cells if any(_numish(w["text"]) for w in c)) >= 2:
                left_edges.append(min(w["x0"] for w in r["words"]))
        band_x0 = min([a["x0"] - 8] + [e - 8 for e in left_edges]) if left_edges else a["x0"] - 8
        band = [w for w in words if w["x0"] >= band_x0 and w["top"] >= a["top"] - 1.6]
        if len(band) < 8:
            continue
        rows = _words_to_rows(band)

        # 表题（可能折成 2–3 行）；第一条「≥2 个单元格且含英文词」的短行就是表头。
        # E：不能只要求「≥2 个单元格」——有些表的首个数据行就是纯数字，会被误当表头；
        # 也不能要求小写英文词（``BEDROC / EF0.5%`` 这类全大写缩写表头会被误杀）。
        cap_parts: List[str] = []
        head_idx = -1
        for i, r in enumerate(rows[:8]):
            cells = _split_cells(r["words"])
            nxt_numeric = False
            if i + 1 < len(rows):
                nxt = _split_row_cells(rows[i + 1]["words"])
                nxt_numeric = sum(1 for c in nxt if any(_numish(w["text"]) for w in c)) >= 2
            # 常规表头：≥2 个单元格、短行、含英文词。
            # 例外：整行表头被 PDF 合并成 1 个单元格（mcemol），此时用
            # 「紧邻下一行是≥2 列数值行 + 本行有多个英文词」作为表头判据。
            regular = (len(cells) >= 2 and len(r["words"]) <= 14
                       and re.search(r"[A-Za-z]{2,}", r["text"]))
            merged = (nxt_numeric and len(r["words"]) >= 3 and len(r["text"]) <= 140
                      and len(re.findall(r"[A-Za-z]{2,}", r["text"])) >= 2)
            if regular or merged:
                head_idx = i
                break
            cap_parts.append(r["text_ws"])
        if head_idx < 1:
            continue                            # 没有表题前缀 → 不认

        # 用「表体里数值单元格最多的行」确定列结构：表头可能被压成一个词，
        # 但数据行的数值单元始终按列分布，是更可靠的列锚点。
        best_cells = None
        for r in rows[head_idx + 1: head_idx + 9]:
            c = _split_row_cells(r["words"])
            if sum(1 for cell in c if any(_numish(w["text"]) for w in cell)) >= 2:
                if best_cells is None or len(c) > len(best_cells):
                    best_cells = c
        if not best_cells or len(best_cells) < 2:
            continue
        spans = [(c[0]["x0"], c[-1]["x1"]) for c in best_cells]
        if len(spans) < 2:
            continue
        bounds = [(spans[i][1] + spans[i + 1][0]) / 2.0 for i in range(len(spans) - 1)]
        table_w = spans[-1][1] - spans[0][0]
        if table_w <= 0:
            continue

        # 表头优先用词级切分（列数与表体一致时最可靠）；只有词级明显少于列数
        # （表头被 PDF 合并成一个 word）时才用字符坐标重建。
        word_header_cells = _split_cells_adaptive(rows[head_idx]["words"], len(spans))
        word_header = [" ".join(x["text"] for x in c) for c in word_header_cells]
        if len(word_header_cells) == len(spans):
            header = word_header
        else:
            header = _header_cells_from_chars(page, rows[head_idx], spans) or word_header
        header = (header + [""] * len(spans))[:len(spans)]
        header = [re.sub(r"(?<=[a-z])#", " #", h) for h in header]   # Trainingset# → Trainingset #
        alpha_headers = sum(1 for h in header if re.search(r"[A-Za-z]", h or ""))
        if alpha_headers < 2:
            continue                        # 表头几乎全是数字 → 大概率把数据行当表头了

        body: List[List[str]] = []
        for r in rows[head_idx + 1:]:
            cells = _split_row_cells(r["words"])
            if float(r["size"] or 0) < med * 0.68:
                if body:
                    break
                continue
            if len(cells) < 2:
                width = r["x1"] - r["x0"]
                # 单单元格长句 = 正文段落；单单元格短行 = 表体折行/脚注，跳过但不终止
                if body and width >= table_w * 0.9 and len(r["text"]) >= 40:
                    break
                continue
            # 多单元格行即使横向铺满表格宽度，也可能是远端列（如另一组指标），
            # 不能按「版面文字」丢弃——实测 DeepHostGuest/trisds 的表体正是这种形态。
            row = [""] * len(spans)
            for w in r["words"]:
                cx = (w["x0"] + w["x1"]) / 2.0
                col = len([b for b in bounds if cx > b])
                row[col] = (row[col] + " " + w["text"]).strip()
            body.append(row)

        if not body:
            continue                        # 单行表合法；但一行数据都没有就不算表
        if len(body) == 1 and sum(1 for c in body[0] if _numish(c)) < 3:
            continue                        # 单行表至少要有 3 个数值格，挡住图例/残片
        numeric_rows = sum(1 for row in body if any(_numish(c) for c in row))
        if numeric_rows < max(1, len(body) * 0.25):
            continue
        if not _valid_table_rows([header, *body], require_numeric=False):
            continue
        caption = _caption_clean(" ".join(p for p in cap_parts if p))[:300]
        found.append(([header, *body], caption))
    return found


_NUM_CELL_RE = re.compile(r"^[-+]?[\d,.]*?\d[\d,.]*%?$")


def _numish(cell: str) -> bool:
    c = re.sub(r"\s+", "", (cell or "").replace("\u2212", "-").replace("\u00a0", ""))
    if not c or not re.search(r"\d", c):
        return False
    return bool(_NUM_CELL_RE.match(c))


def _valid_table_rows(rows: List[List[str]], *, require_numeric: bool) -> bool:
    """结构合法性校验：挡住「双栏正文被切成列」这类假表格。"""
    rows = [r for r in rows if any((c or "").strip() for c in r)]
    if len(rows) < 2:
        return False
    width = max(len(r) for r in rows)
    if width < 2:
        return False
    grid = _pad_rows(rows)
    # 整列空白 → 不是表格
    if any(all(not (grid[i][j] or "").strip() for i in range(len(grid))) for j in range(width)):
        return False
    cells = [(c or "").strip() for r in grid for c in r]
    if max((len(c) for c in cells), default=0) > 300:  # 单格过长 ≈ 版面文本
        return False
    if require_numeric:
        ratio = sum(1 for c in cells if _numish(c)) / max(1, len(cells))
        if ratio < 0.3:
            return False
    return True


def _marker_page(line: str) -> Optional[int]:
    """分页标记行 -> 页码；非标记行返回 ``None``。"""
    if not _is_page_marker(line):
        return None
    m = re.search(r"\d+", line)
    return int(m.group(0)) if m else None


def _scan_table_refs(lines: List[str]) -> Tuple[int, List[Tuple[int, int, str]]]:
    """扫描正文，返回 ``(Table N 引用次数, [(页码, 表号, 表题)])``。

    页码由分页标记还原（首页无标记，记为 1）。表题允许折到下一行，
    且只认「像表题」的行——``Table 4 shows …`` 这类正文引用不会被当成表题。
    """
    refs = 0
    captions: List[Tuple[int, int, str]] = []
    page = 1
    for i, raw in enumerate(lines):
        s = raw.strip()
        pn = _marker_page(s)
        if pn is not None:
            page = pn
            continue
        refs += len(_TABLE_REF_RE.findall(s))
        m = _TABLE_CAP_RE.match(s)
        if not m:
            continue
        num = int(re.sub(r"\D", "", m.group(1)) or 0)
        if not num:
            continue
        cap = m.group(3).strip()
        looks_like_caption = bool(m.group(2)) or not cap or cap[:1].isupper() or cap[:1].isdigit()
        if not cap and looks_like_caption:
            for nxt in lines[i + 1 : i + 3]:
                t = nxt.strip()
                if t and not _TABLE_CAP_RE.match(t) and not _is_page_marker(t):
                    cap = t
                    break
        if cap and looks_like_caption:
            captions.append((page, num, cap[:300]))
    return refs, captions


def _associate_table_captions(
    entries: List[Tuple[int, Table]], captions: List[Tuple[int, int, str]]
) -> None:
    """把抽到的表格与 ``Table N`` 表题关联——**只在同一页内配对**。

    跨页就近配对会把别的表的表题贴到本表上（实测会把数值表标成
    "List of structures from the PDB"），错标比不标更糟，故只做同页配对：
    同页表题多于表格时按出现顺序一对一；只有一条表题时视为跨表续表，复用它。
    """
    by_page: Dict[int, List[str]] = {}
    for page, _num, cap in captions:
        if cap and cap not in by_page.setdefault(page, []):
            by_page[page].append(cap)

    tables_by_page: Dict[int, List[Table]] = {}
    for page, table in entries:
        tables_by_page.setdefault(page, []).append(table)

    for page, tables in tables_by_page.items():
        caps = by_page.get(page, [])
        if not caps:
            continue
        for i, table in enumerate(tables):
            if table.caption:
                continue                       # 无框线抽取已自带表题，不要覆盖
            if i < len(caps):
                table.caption = caps[i]
            elif len(caps) == 1:
                table.caption = caps[0]


def table_warnings(refs: int, tables: List[Table], retried: bool = False) -> List[str]:
    """抽不到表格时的「点名式」告警。

    ``refs`` 是正文引用 ``Table N`` 的次数。**抽不到 ≠ 论文没有表格**：
    旧口径把「抽不到」写成「论文未提供可抽取的表格数据」，会误导读者。
    ``retried=True`` 表示已经自动尝试过 ``text`` 策略，此时不再建议用户手动设
    环境变量，而是如实说明「重试后仍无通过结构校验的表格」。
    """
    if not refs:
        return []
    if not tables:
        if retried:
            hint = "已自动尝试 `text` 策略（数值占比 <0.3 的版面切片会被拒绝），仍无可用表格；请对照原文人工确认。"
        else:
            hint = "" if _text_tables_enabled() else "若确为纯文本对齐表，可设 `P2C_PDF_TEXT_TABLES=1` 重试。"
        return [
            f"正文引用 `Table N` ×{refs}，但从 PDF 抽到 0 张表——"
            "该 PDF 的表格多为无框线三线表或按单元格独立排版，自动抽取失败。"
            "请勿据此认为论文没有表格。" + hint
        ]
    uncaptioned = sum(1 for t in tables if not t.caption)
    if uncaptioned:
        return [
            f"抽到 {len(tables)} 张表，其中 {uncaptioned} 张未能与 `Table N` 表题关联，"
            "序号可能对不上原文。"
        ]
    return []


def _extract_tables_heuristic(lines: List[str]) -> List[Table]:
    """无 pdfplumber 时：把「含 ≥2 个空白分隔数字列」的连续行视为表格。"""
    num_row = re.compile(r"^\s*\S.*?\s{2,}[-+]?[\d.,]+\s*$")
    out: List[Table] = []
    buf: List[str] = []
    for line in lines + [""]:
        if num_row.match(line) or (buf and line.strip() and re.search(r"\s{2,}", line)):
            buf.append(line)
            continue
        if len(buf) >= 3:
            rows = [[c.strip() for c in re.split(r"\s{2,}", r.strip()) if c] for r in buf]
            width = max(len(r) for r in rows)
            rows = [r + [""] * (width - len(r)) for r in rows]
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


def _table_is_plausible(table: Table) -> bool:
    """表级最低质量门槛：挡住 pdfplumber 在双栏版面上切出的单行碎片。

    实测 pocketvina / MECo / lambdazero 会产生「标题=一长串方法名、只有 1 行」
    或「表头全是空/数字」的伪表。规则：
    * 至少 2 个含字母的表头，且其中一个长度 ≥3（挡住 ``0,1,2,...,n`` 这类伪表头）；
    * 否则必须有 ``Table N`` 型表题且至少 2 行数据（数字表头但确有表题的表）。
    """
    cells = [str(h) for h in (table.header or [])]
    rows = len(table.rows or [])
    alpha = sum(1 for h in cells if re.search(r"[A-Za-z]", h))
    has_long = any(re.search(r"[A-Za-z]{3,}", h) for h in cells)
    if alpha >= 2 and has_long:
        return True
    cap = (table.caption or "").strip().lower()
    if cap.startswith("table") and rows >= 2:
        return True
    # 多行表至少有一个含字母的表头；1–2 行的碎片还要求表头里有长词，
    # 挡住 trio 的 C/C/O 化学片段与 pocketvina/lambdazero 的单行图例。
    if rows >= 2:
        return alpha >= 1 and (has_long or rows >= 3)
    return False


def _extract_tables_with_fallback(path: Path, lines: List[str], refs: int,
                                   captions: List[Tuple[int, int, str]]
                                   ) -> Tuple[List[Table], str, bool, List[str]]:
    """表格抽取主流程 + E 阶段的零召回自动重试。

    返回 ``(tables, strategy, text_retried, warnings)``。只有通过
    ``_valid_table_rows(require_numeric=True)`` 的 text 策略结果才会被采纳，
    因此「重试」不会把双栏版面糊当成表格。
    """
    extra_warnings: List[str] = []
    text_retried = _text_tables_enabled()

    entries = _extract_tables_pdfplumber(path)
    _associate_table_captions(entries, captions)
    # 无框线三线表：按 `Table N` 表题锚点补抽；同一页已有框线表时不重复
    lined_pages = {p for p, _t in entries}
    extra = [e for e in _extract_tables_borderless(path) if e[0] not in lined_pages]
    entries = sorted([*entries, *extra], key=lambda e: e[0])
    entries = [(p, t) for p, t in entries if _table_is_plausible(t)]
    for i, (_p, t) in enumerate(entries):
        t.id = f"T{i + 1}"
    tables = [t for _p, t in entries]

    strategy = "none"
    if tables:
        sources = {t.source for t in tables}
        strategy = ("plumber-lines+borderless" if "pdf" in sources and "pdf-borderless" in sources
                    else "borderless" if "pdf-borderless" in sources else "plumber-lines")

    # E：正文引用了 Table N 却零召回 → 自动强制 text 策略重试一次。
    if refs and not tables and not text_retried:
        text_retried = True
        retry = sorted(_extract_tables_pdfplumber(path, force_text=True), key=lambda e: e[0])
        _associate_table_captions(retry, captions)
        retry = [(p, t) for p, t in retry if _table_is_plausible(t)]
        if retry:
            for i, (_p, t) in enumerate(retry):
                t.id = f"T{i + 1}"
                t.source = "pdf-text"
            tables = [t for _p, t in retry]
            strategy = "text-fallback"
            extra_warnings.append(
                "主策略零召回，已自动用 `text` 策略重试并保留 %d 张通过结构校验的表格；"
                "该策略在双栏版面上可能误判，请对照原文复核。" % len(tables))

    if not tables:
        tables = _extract_tables_heuristic(lines)
        if tables:
            strategy = "heuristic"
            extra_warnings.append(
                f"仅使用启发式表格抽取（{len(tables)} 张），建议安装 pdfplumber 提升准确率"
            )
    return tables, strategy, text_retried, extra_warnings


def _read_pdf_attachments(path: Path) -> List[Dict[str, Any]]:
    """读取 PDF 内嵌附件（Supporting Information / 数据文件 / 补充 PDF）。

    只读取内容，不改写原 PDF；文本类附件直接解码，PDF 附件抽取逐页文本，
    二进制附件只记录名称与大小。结果写入 ``paper.meta["pdf_attachments"]``。
    """
    try:
        import fitz  # type: ignore
    except Exception:
        return []
    try:
        doc = fitz.open(str(path))
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    try:
        names = doc.embfile_names()
    except Exception:
        names = []
    for name in names:
        try:
            data = doc.embfile_get(name)
        except Exception as exc:
            out.append({"name": str(name), "error": "%s: %s" % (type(exc).__name__, exc)})
            continue
        item: Dict[str, Any] = {"name": str(name), "size": len(data)}
        low = str(name).lower()
        try:
            if low.endswith(".pdf"):
                sub = fitz.open(stream=data, filetype="pdf")
                try:
                    item["pages"] = sub.page_count
                    item["text"] = "\n".join(p.get_text() for p in sub)[:200_000]
                finally:
                    sub.close()
            elif low.endswith((".txt", ".md", ".csv", ".tsv", ".json", ".html", ".xml")):
                item["text"] = data.decode("utf-8", errors="ignore")[:200_000]
        except Exception as exc:
            item["error"] = "%s: %s" % (type(exc).__name__, exc)
        out.append(item)
    try:
        doc.close()
    except Exception:
        pass
    return out


def parse_pdf(path: Path, paper_id: str) -> Paper:
    lines, images, pages, warnings = _read_pdf_text(path)
    attachments = _read_pdf_attachments(path)

    if not lines:
        paper = Paper(id=paper_id, source_path=str(path), source_kind="pdf")
        paper.meta["warnings"] = warnings
        paper.meta["pdf_pages"] = pages
        paper.meta["pdf_attachments"] = attachments
        paper.meta["pdf_attachment_count"] = len(attachments)
        return paper

    paper = parse_markdown("\n".join(lines), paper_id, source_path=str(path), kind="pdf")
    # 版面法（字号 + boilerplate + 跨行合并）优先，退化时才用「最长行」启发式
    front = _front_matter(path)
    paper.title = front.get("title") or _guess_title(lines, paper.title)
    if front.get("authors"):
        paper.authors = front["authors"]
    if front.get("abstract"):
        paper.abstract = front["abstract"]
        if not any(s.kind == "abstract" for s in paper.sections):
            paper.sections.insert(
                0, Section(heading="Abstract", level=2, text=front["abstract"], kind="abstract")
            )
    paper.meta["warnings"] = warnings
    # 页数直接取 fitz 的真值，别再从文本里数标记（旧口径会误报成 1）
    paper.meta["pdf_pages"] = pages or sum(1 for ln in lines if _is_page_marker(ln)) + 1

    refs, captions = _scan_table_refs(lines)
    paper.meta["table_refs"] = refs

    # 正文里已经很干净时不重复抽表
    if paper.tables:
        table_strategy = "parsed-text"
        text_retried = _text_tables_enabled()
    else:
        tables, table_strategy, text_retried, table_notes = _extract_tables_with_fallback(
            path, lines, refs, captions)
        warnings.extend(table_notes)
        paper.tables = tables
    paper.meta["table_strategy"] = table_strategy

    # 抽不到 ≠ 论文没有：正文引用了 Table N 却零召回时必须点名说明
    warnings.extend(table_warnings(refs, paper.tables, retried=text_retried))
    if refs and not paper.tables:
        paper.meta["diagnostics"] = {
            **paper.meta.get("diagnostics", {}),
            "table_refs": refs,
            "tables": 0,
            "table_strategy": table_strategy,
            "text_retried": text_retried,
            "reason": "表格无可抽取的框线或按单元格独立排版",
        }

    # 位图不在这里造图条目：旧实现把每条 get_images() 都写成一张
    # caption="page N embedded image" 的假图题，既虚增图数又绕过了下游的
    # 「无 caption 不重绘」判断。真图资产由 figassets.export_pdf_figures 落盘并配对。
    paper.meta["pdf_images"] = images
    paper.meta["pdf_image_count"] = len(images)
    paper.meta["pdf_attachments"] = attachments
    paper.meta["pdf_attachment_count"] = len(attachments)

    # PDF 里图题/表题往往是抽得到的，重新扫一遍更稳
    if not paper.figures:
        paper.figures = extract.extract_figures(lines)
    return paper


def render_page_png(path: Path, page: int, out_path: Path, dpi: int = 144) -> Optional[str]:
    """把某一页渲染成 PNG（用于在课件里引用原文版面）。"""
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
