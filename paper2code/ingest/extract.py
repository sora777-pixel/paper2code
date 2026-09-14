"""文本抽取工具：从论文正文中识别表格 / 图 / 算法块 / 代码链接。

这些函数只依赖 stdlib，输入是规范化后的「按行文本」，因此对 Markdown、
HTML 转文本、PDF 转文本三条链路都适用。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from ..models import Algorithm, CodeRef, Figure, Formula, Section, Table

# --------------------------------------------------------------------------- #
# 表格
# --------------------------------------------------------------------------- #
_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_PIPE_RE = re.compile(r"^\s*\|.*\|\s*$")
_CAPTION_RE = re.compile(
    r"^\s*\*{0,2}\s*(Table|TABLE|表)\s*((?:S-?)?(?:[0-9]+|[IVXLCDM]+))\s*[.:：、]?\s*(.*)$",
    re.IGNORECASE,
)


def _split_pipe(line: str) -> List[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def extract_tables(lines: List[str]) -> List[Table]:
    """抽取 Markdown 管道表格（含 LaTeX 风格的 ``\\hline`` 退化处理）。

    返回的 Table 保留原始字符串单元格；表题从表格上方/下方一行的
    ``Table N.`` 捕获。
    """
    tables: List[Table] = []
    idx = 0
    i = 0
    n = len(lines)
    while i < n:
        if _PIPE_RE.match(lines[i]) and i + 1 < n and _SEP_RE.match(lines[i + 1]):
            header = _split_pipe(lines[i])
            i += 2
            rows: List[List[str]] = []
            while i < n and _PIPE_RE.match(lines[i]) and not _SEP_RE.match(lines[i]):
                rows.append(_split_pipe(lines[i]))
                i += 1
            idx += 1
            tables.append(
                Table(
                    id=f"T{idx}",
                    caption=_find_caption(lines, i - len(rows) - 2),
                    header=header,
                    rows=rows,
                    source="text",
                )
            )
            continue
        i += 1
    return tables


def _find_caption(lines: List[str], start: int, window: int = 3) -> str:
    """在给定行的上下窗口里找 ``Table N.`` 表题。"""
    for k in range(max(0, start - 1), min(len(lines), start + window + 1)):
        m = _CAPTION_RE.match(lines[k])
        if m:
            return m.group(3).strip() or lines[k].strip()
    return ""


# --------------------------------------------------------------------------- #
# 图
# --------------------------------------------------------------------------- #
_FIG_CAP_RE = re.compile(
    r"^\s*\*{0,2}\s*(Figure|Fig\.?|FIG\.?|图)\s*(S?\d+[a-zA-Z]?)\s*[.:：、]?\s*(.*)$",
    re.IGNORECASE,
)
_MD_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")


def extract_figures(lines: List[str]) -> List[Figure]:
    """识别图题与内嵌图片引用，尽量建立「图 N ↔ 图片文件」映射。"""
    figures: List[Figure] = []
    pending_imgs: List[str] = []
    seen: Dict[str, Figure] = {}

    for line in lines:
        for alt, src in _MD_IMG_RE.findall(line):
            pending_imgs.append(src)
        m = _FIG_CAP_RE.match(line)
        if not m:
            continue
        num = m.group(2)
        fid = f"F{num}"
        caption = m.group(3).strip() or line.strip()
        fig = seen.get(fid)
        if fig is None:
            fig = Figure(id=fid, caption=caption)
            if pending_imgs:
                fig.image_path = pending_imgs.pop(0)
            elif not fig.image_path and f"fig{num}" in line:
                pass
            fig.kind = classify_figure(caption)
            seen[fid] = fig
            figures.append(fig)
        else:
            if caption and caption not in fig.caption:
                fig.caption = (fig.caption + " " + caption).strip()

    # 没有图题的独立图片也记录为 F?，避免信息丢失
    for src in pending_imgs:
        figures.append(Figure(id=f"FX{len(figures) + 1}", image_path=src, kind="unknown"))
    return figures


_ARCH_HINTS = ("architecture", "framework", "overview", "pipeline", "workflow", "结构", "框架", "流程")
_PLOT_HINTS = ("accuracy", "loss", "curve", "comparison", "results", "performance", "精度", "结果", "对比")


def classify_figure(caption: str) -> str:
    low = (caption or "").lower()
    if any(h in low for h in _PLOT_HINTS):
        return "plot"
    if any(h in low for h in _ARCH_HINTS):
        return "architecture"
    return "unknown"


# --------------------------------------------------------------------------- #
# 算法 / 伪代码
# --------------------------------------------------------------------------- #
_ALGO_CAP_RE = re.compile(
    r"^\s*\*{0,2}\s*(Algorithm|Alg\.?|算法|Procedure|Pseudocode)\s*([0-9]+[a-zA-Z]?)"
    r"\s*[.:：、]?\s*(.*)$",
    re.IGNORECASE,
)
_FENCE_RE = re.compile(r"^\s*```+\s*([A-Za-z0-9_+-]*)\s*$")
_PSEUDO_HINTS = (
    "algorithm", "pseudo", "pseudocode", "algo", "alg",
)
_PSEUDO_KEYWORDS = (
    "for ", "while ", "if ", "end for", "end while", "end if", "return",
    "input", "output", "input:", "output:", "require", "ensure", "←", "<-",
    "procedure", "function", "repeat", "until", "do",
)


_TITLE_LINE_RE = re.compile(
    r"^\s*(?:Algorithm|Alg\.?|Procedure|Pseudocode|算法)\s*[0-9]*\s*[.:：]\s*(.+?)\s*$",
    re.IGNORECASE,
)


def extract_algorithms(lines: List[str]) -> List[Algorithm]:
    """抽取 fenced code block 中的伪代码。

    判定策略（任一命中即为伪代码块）：
    1. 围栏信息串包含 algorithm / pseudo；
    2. 块内出现 ≥2 个算法关键字且几乎不含 ``def``/``import`` 等真实代码特征。

    块内首行若是 ``Algorithm 1: xxx`` 这类标题行，则抽出作为图题并从代码正文里移除，
    否则这行会污染后续的伪代码→Python 翻译。
    """
    algos: List[Algorithm] = []
    pending_caption = ""
    i = 0
    n = len(lines)
    while i < n:
        cap = _ALGO_CAP_RE.match(lines[i])
        if cap:
            # 正文里的一句话常常也以 "Algorithm 1 ..." 开头，只取第一句做图题
            rest = re.split(r"(?<=[.。;；])\s", cap.group(3), maxsplit=1)[0]
            pending_caption = f"Algorithm {cap.group(2)}. {rest}".strip().rstrip(".")
            i += 1
            continue

        fence = _FENCE_RE.match(lines[i])
        if fence:
            info = (fence.group(1) or "").lower()
            body: List[str] = []
            i += 1
            while i < n and not _FENCE_RE.match(lines[i]):
                body.append(lines[i])
                i += 1
            i += 1  # 跳过收尾围栏
            if _looks_like_pseudocode(info, body):
                caption = pending_caption or _first_sentence(body)
                # 块内标题行优先（更贴近论文原图题），并从代码正文里剔除
                if body:
                    title = _TITLE_LINE_RE.match(body[0])
                    if title:
                        caption = title.group(0).strip().rstrip(".")
                        body = body[1:]
                algos.append(
                    Algorithm(
                        id=f"A{len(algos) + 1}",
                        caption=caption,
                        lines=body,
                        language="pseudocode",
                    )
                )
                pending_caption = ""
            continue
        i += 1
    return algos


def _looks_like_pseudocode(info: str, body: List[str]) -> bool:
    if any(h in info for h in _PSEUDO_HINTS):
        return True
    if info in ("python", "py", "java", "cpp", "c", "js", "javascript", "ts") and not any(
        h in info for h in _PSEUDO_HINTS
    ):
        # 明确标注真实语言的代码块，交给 code_refs / repo 模块处理
        if any(("def " in ln or "import " in ln or "class " in ln) for ln in body):
            return False
    text = "\n".join(body).lower()
    hits = sum(1 for kw in _PSEUDO_KEYWORDS if kw in text)
    real_code = sum(1 for ln in body if re.match(r"^\s*(def |class |import |from )", ln))
    return hits >= 2 and real_code == 0


def _first_sentence(body: List[str]) -> str:
    for line in body:
        s = line.strip()
        if s:
            return s[:120]
    return ""


# --------------------------------------------------------------------------- #
# 代码 / 数据资源链接
# --------------------------------------------------------------------------- #
_URL_RE = re.compile(r"https?://[^\s\)\]\}>\"',;]+")
_GITHUB_RE = re.compile(r"https?://(?:www\.)?github\.com/[\w.\-]+/[\w.\-]+", re.IGNORECASE)
_DATASET_HINTS = (
    "dataset", "data set", "data available", "data is available", "zenodo",
    "figshare", "huggingface", "kaggle", "dryad", "openml", "数据",
)
_CODE_HINTS = (
    "code", "implementation", "source code", "released", "available at",
    "github", "repository", "repo", "开源", "代码",
)


def extract_code_refs(lines: List[str]) -> Tuple[List[CodeRef], List[CodeRef]]:
    """返回 ``(code_refs, data_refs)``。

    通过 URL 所在行/相邻行的关键词判断该链接是代码仓库还是数据集。
    """
    code_refs: List[CodeRef] = []
    data_refs: List[CodeRef] = []
    seen: set[str] = set()

    for i, line in enumerate(lines):
        ctx = " ".join(lines[max(0, i - 1) : i + 2]).lower()
        for m in _URL_RE.finditer(line):
            url = m.group(0).rstrip(".,;)")
            if url in seen:
                continue
            if _GITHUB_RE.match(url):
                seen.add(url)
                code_refs.append(
                    CodeRef(url=url, kind="repo", evidence=line.strip()[:200])
                )
                continue
            if any(h in ctx for h in _DATASET_HINTS) or any(
                h in url.lower() for h in ("zenodo", "figshare", "huggingface", "kaggle", "dryad", "openml")
            ):
                seen.add(url)
                data_refs.append(
                    CodeRef(url=url, kind="dataset", evidence=line.strip()[:200])
                )
                continue
            if any(h in ctx for h in _CODE_HINTS):
                seen.add(url)
                code_refs.append(
                    CodeRef(url=url, kind="other", evidence=line.strip()[:200])
                )
    code_refs = add_named_repo_refs(lines, code_refs)
    return code_refs, data_refs


# 论文只写 "SchNOrb repository"、正文没有 github.com 字符串时，补官方仓库。
_NAMED_REPOS = {
    "schnorb": "https://github.com/atomistic-machine-learning/SchNOrb",
}


def add_named_repo_refs(lines: List[str], code_refs: List[CodeRef]) -> List[CodeRef]:
    blob = "\n".join(lines).lower()
    if not any(k in blob for k in ("repository", "github", "gitlab", "source code", "开源")):
        return code_refs
    seen = {r.url for r in code_refs}
    for name, url in _NAMED_REPOS.items():
        if name in blob and url not in seen:
            code_refs.append(CodeRef(url=url, kind="repo", evidence=f"正文提到 {name} repository"))
            seen.add(url)
    return code_refs


_CODE_NOUNS = (
    "code", "implementation", "source code", "repository", "repo", "github",
    "gitlab", "script", "代码", "开源",
)
_RELEASE_VERBS = (
    "available", "release", "released", "open-source", "open source", "开源",
    "公开", "已发布", "提供",
)


def find_code_statement(lines: List[str]) -> str:
    """找「代码是否开源」的显式表述，用于情形判定留痕。

    必须**同时**出现「代码类名词」与「发布类动词」，避免把
    "We release the aggregated workload statistics" 这类数据发布声明
    误判为代码开源声明。
    """
    for line in lines:
        low = line.lower()
        if any(n in low for n in _CODE_NOUNS) and any(v in low for v in _RELEASE_VERBS):
            return line.strip()[:240]
        if re.search(r"existing\\s+\\w+\\s+repository", low) or re.search(r"\\b\\w+\\s+repository\\b", low) and "github" in low:
            return line.strip()[:240]
    return ""


# --------------------------------------------------------------------------- #
# 章节分类
# --------------------------------------------------------------------------- #
_KIND_MAP: List[Tuple[str, str]] = [
    ("abstract", "abstract"),
    ("introduction", "introduction"),
    ("related work", "related_work"),
    ("background", "background"),
    ("preliminar", "background"),
    ("method", "method"),
    ("approach", "method"),
    ("model", "method"),
    ("algorithm", "method"),
    ("architecture", "method"),
    ("experiment", "experiment"),
    ("evaluation", "experiment"),
    ("result", "experiment"),
    ("ablation", "experiment"),
    ("discussion", "discussion"),
    ("conclusion", "conclusion"),
    ("outlook", "conclusion"),
    ("future work", "conclusion"),
    ("limitation", "discussion"),
    ("reference", "references"),
    ("bibliograph", "references"),
    ("appendix", "appendix"),
    ("摘要", "abstract"),
    ("引言", "introduction"),
    ("相关工作", "related_work"),
    ("方法", "method"),
    ("实验", "experiment"),
    ("结果", "experiment"),
    ("结论", "conclusion"),
    ("展望", "conclusion"),
    ("不足", "discussion"),
    ("局限", "discussion"),
    ("参考文献", "references"),
    ("附录", "appendix"),
]


def classify_heading(heading: str) -> str:
    low = (heading or "").strip().lower()
    for needle, kind in _KIND_MAP:
        if needle in low:
            return kind
    return "section"


# --------------------------------------------------------------------------- #
# 学术论文分节（I. INTRODUCTION）与表题（TABLE I / S2）
# --------------------------------------------------------------------------- #
_ROMAN = r"(?:I{1,3}|IV|V|VI|VII|VIII|IX|X)"
_SEC_NAME = (
    r"(?:ABSTRACT|INTRODUCTION|RELATED\s+WORK|BACKGROUND|THEORY|"
    r"METHODS?|RESULTS?(?:\s+AND\s+DISCUSSION)?|DISCUSSION|"
    r"CONCLUSIONS?(?:\s+AND\s+OUTLOOK)?|ACKNOWLEDGMENTS?|"
    r"REFERENCES|BIBLIOGRAPHY|APPENDIX|SUPPLEMENTARY(?:\s+MATERIAL)?|"
    r"DATA\s+AVAILABILITY(?:\s+STATEMENT)?)"
)
_SECTION_HEAD = re.compile(
    rf"^(?:{_ROMAN}|[0-9]+)\.\s+{_SEC_NAME}\s*$|^\s*{_SEC_NAME}\s*$",
    re.I,
)
_LONE_NUM = re.compile(rf"^(?:{_ROMAN}|[0-9]+)\.\s*$")


def split_academic_sections(lines: List[str]) -> List[Section]:
    """从双栏重排后的正文里切 I./II. 风格章节；少于 2 个标题则返回空。"""
    marks: List[Tuple[int, int, str]] = []
    i = 0
    n = len(lines)
    while i < n:
        s = lines[i].strip()
        j = i + 1
        while j < n and not lines[j].strip():
            j += 1
        nxt = lines[j].strip() if j < n else ""
        heading = None
        end_idx = i
        if s and len(s) <= 80 and _SECTION_HEAD.match(s):
            heading = re.sub(r"\s+", " ", s)
        elif s and _LONE_NUM.match(s) and nxt and len(nxt) <= 80 and _SECTION_HEAD.match(nxt):
            heading = re.sub(r"\s+", " ", f"{s} {nxt}")
            end_idx = j
        if heading:
            marks.append((i, end_idx, heading))
            i = end_idx + 1
            continue
        i += 1
    if len(marks) < 2:
        return []
    out: List[Section] = []
    for k, (_start, end_idx, heading) in enumerate(marks):
        stop = marks[k + 1][0] if k + 1 < len(marks) else n
        body = "\n".join(lines[end_idx + 1 : stop]).strip()
        out.append(Section(heading=heading, level=1, text=body, kind=classify_heading(heading)))
    return out


def extract_captioned_tables(lines: List[str]) -> List[Table]:
    """即使没有网格线，也把 TABLE I / TABLE S2 题注收成表。"""
    out: List[Table] = []
    seen = set()
    for i, line in enumerate(lines):
        m = _CAPTION_RE.match(line.strip())
        if not m:
            continue
        num = m.group(2)
        tid = f"T{num}"
        if tid in seen:
            continue
        seen.add(tid)
        caption = (m.group(3) or "").strip()
        extra: List[str] = []
        for j in range(i + 1, min(len(lines), i + 4)):
            nxt = lines[j].strip()
            if not nxt or _CAPTION_RE.match(nxt) or _FIG_CAP_RE.match(nxt) or _SECTION_HEAD.match(nxt):
                break
            if len(nxt) < 180:
                extra.append(nxt)
            else:
                break
        if extra:
            caption = (caption + " " + " ".join(extra)).strip()
        out.append(Table(id=tid, caption=caption or line.strip(), header=[], rows=[], source="pdf-caption"))
    return out

def extract_formulas(lines: List[str]) -> List["Formula"]:
    """抽取带编号、且正文有文字解释的公式。

    版式常见：数学行在上，单独一行 ``(12)`` 作编号；解释里写 ``Eq. 12``。
    """
    num_only = re.compile(r"^[（(]\s*(\d{1,2})\s*[)）]\s*$")
    mention = re.compile(
        r"(?:Eq(?:uation)?\.?|eq\.|公式)\s*[\(（]?\s*(\d{1,2})\s*[\)）]?",
        re.I,
    )

    # 先收集正文里被点名的编号
    explained: dict[str, List[str]] = {}
    for i, line in enumerate(lines):
        for m in mention.finditer(line):
            n = m.group(1)
            # 取当前句及前后各一句，作为该编号的文字解释
            ctx = []
            for j in range(max(0, i - 1), min(len(lines), i + 3)):
                s = lines[j].strip()
                if s and not num_only.match(s):
                    ctx.append(s)
            blob = " ".join(ctx)
            blob = re.sub(r"\s+", " ", blob).strip()
            if blob:
                explained.setdefault(n, []).append(blob)

    formulas: List[Formula] = []
    seen = set()
    for i, line in enumerate(lines):
        m = num_only.match(line.strip())
        if not m:
            continue
        n = m.group(1)
        if n in seen or n not in explained:
            continue
        # 向上收集公式本体（短行、像数学）
        body: List[str] = []
        j = i - 1
        while j >= 0:
            s = lines[j].strip()
            if not s:
                if body:
                    break
                j -= 1
                continue
            if num_only.match(s):
                break
            if mention.search(s) and len(s) > 80:
                break
            if len(s) > 140 and not re.search(r"[=∑∫√ψϕεϵ∈→←]", s):
                break
            body.append(s)
            if len(body) >= 6:
                break
            j -= 1
        expr = " ".join(reversed(body)).strip()
        expr = re.sub(r"\s+", " ", expr)
        if len(expr) < 2:
            continue
        seen.add(n)
        formulas.append(
            Formula(
                id=f"E{n}",
                number=n,
                expression=expr,
                explanation=explained[n][0][:400],
            )
        )
    # 按编号排序
    formulas.sort(key=lambda f: int(f.number) if f.number.isdigit() else 999)
    return formulas

