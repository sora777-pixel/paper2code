"""文本抽取工具：从论文正文中识别表格 / 图 / 算法块 / 代码链接。

这些函数只依赖 stdlib，输入是规范化后的「按行文本」，因此对 Markdown、
HTML 转文本、PDF 转文本三条链路都适用。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from ..models import Algorithm, CodeRef, Figure, Formula, Table

# --------------------------------------------------------------------------- #
# 表格
# --------------------------------------------------------------------------- #
_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_PIPE_RE = re.compile(r"^\s*\|.*\|\s*$")
_CAPTION_RE = re.compile(
    r"^\s*\*{0,2}\s*(Table|表)\s*([0-9]+[a-zA-Z]?)\s*[.:：、]?\s*(.*)$", re.IGNORECASE
)


# --------------------------------------------------------------------------- #
# 公式（roadmap M3-③ 的前置：``Paper.formulas`` 此前一直没有抽取器）
# --------------------------------------------------------------------------- #
#: ``<expr> (12)`` —— 公式与编号同行的期刊排版
_EQ_TAG_TAIL_RE = re.compile(r"^(?P<body>.+?)\s*\(\s*(?P<num>\d{1,3}[a-z]?)\s*\)\s*$")
#: 只有编号，公式在上一行
_EQ_TAG_ONLY_RE = re.compile(r"^\(\s*(\d{1,3}[a-z]?)\s*\)$")
_MATH_CHARS = ("=", "\u2264", "\u2265", "\u2211", "\u222b", "\u221a", "\\frac", "\\sum",
               "\\int", "<=", ">=", "^", "_", "/", "\\")


def _looks_math(s: str) -> bool:
    """像公式而不像散文：含数学符号，且不是「以句号收尾的长句」。"""
    t = (s or "").strip()
    if not t or len(t) > 200:
        return False
    if t.endswith((".", "\u3002")) and len(t) > 60:
        return False
    return any(c in t for c in _MATH_CHARS)


def _nearest_prose(lines: List[str], idx: int, back: int = 8) -> str:
    """往上找最近一句「文字解释」，作为公式的语义锚点。"""
    for j in range(idx - 1, max(-1, idx - 1 - back), -1):
        t = (lines[j] or "").strip()
        if len(t) >= 40 and re.search(r"[A-Za-z\u4e00-\u9fff]", t):
            return t
    return ""


def extract_formulas(lines: List[str]) -> List[Formula]:
    """抽编号公式：``<expr> (12)`` 或 ``<expr>`` + 下一行 ``(12)``。

    只认带**编号**的展示公式——这是「论文中编号公式」的字面含义，也避免把正文里
    随手出现的 ``=`` 误当公式。每条公式尽量带上最近的**文字解释**，供 M3-③
    生成数值断言时锚定语义（离线没有 LLM，只能靠这段文字）。
    """
    out: List[Formula] = []
    seen: set[str] = set()
    for i, raw in enumerate(lines):
        s = (raw or "").strip()
        if not s:
            continue
        only = _EQ_TAG_ONLY_RE.match(s)
        tail = _EQ_TAG_TAIL_RE.match(s)
        expr, num, anchor = "", "", i
        if only and i > 0 and _looks_math(lines[i - 1]):
            expr, num, anchor = (lines[i - 1] or "").strip(), only.group(1), i - 1
        elif tail and _looks_math(tail.group("body")):
            expr, num, anchor = tail.group("body").strip(), tail.group("num"), i
        else:
            continue
        if num in seen:
            continue
        seen.add(num)
        out.append(Formula(id="E%s" % num, number=num, expression=expr[:300],
                           explanation=_nearest_prose(lines, anchor)[:300]))
    return out

#: 分页标记——PDF 链路为保留页边界而插入。**任何情况下都不能当成章节标题**，
#: 否则每页都会被记成一个语义章节（历史上正是这么坏的）。兼容 ``[[PAGE:2]]``
#: 与 ``[Page 2]`` / ``# [Page 2]`` 等历史写法。
PAGE_MARK_RE = re.compile(r"^#?\s*\[{1,2}\s*page\s*:?\s*\d+\s*\]{1,2}$", re.IGNORECASE)


def is_page_marker(s: str) -> bool:
    return bool(PAGE_MARK_RE.match((s or "").strip()))


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
    r"^\s*\*{0,2}\s*(Figure|Fig\.?|图)\s*([0-9]+[a-zA-Z]?)\s*[.:：、]?\s*(.*)$",
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
#: 捕获 owner / repo 两个分组：候选排序要拿 repo 名与论文标题/上下文比对。
_GITHUB_RE = re.compile(r"https?://(?:www\.)?github\.com/([\w.\-]+)/([\w.\-]+)", re.IGNORECASE)
#: 跨行断开的 URL 续行：整行只能是 URL 字符（没有空格/中文）
_URL_CONT_RE = re.compile(r"^[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%\-]+$")
_DATASET_HINTS = (
    "dataset", "data set", "data available", "data is available", "zenodo",
    "figshare", "huggingface", "kaggle", "dryad", "openml", "数据",
)
_CODE_HINTS = (
    "code", "implementation", "source code", "released", "available at",
    "github", "repository", "repo", "开源", "代码",
)
#: 指向「本论文自己的代码」的正向线索
_OWN_HINTS = (
    "code availability", "data availability", "source code", "code is available",
    "code available", "publicly available", "our code", "we release", "we provide",
    "implementation is available", "implementation available", "code for ",
    "开源代码", "代码可用", "代码已开源",
)
#: 指向「被引用的第三方仓库」的负向线索
_THIRD_PARTY_HINTS = (
    "maintained by", "accessed 20", "accessed:", "accessed on", "third-party",
    "third party", "external repository", "we use the", "built upon",
)
#: 文献清单 / awesome-list 不是可复现代码
_LIST_REPO_HINTS = ("awesome", "survey", "literature", "paper-list", "paperlist", "reading-list")
_CITATION_RE = re.compile(r"\[\d{1,3}\]")
_FOOTNOTE_URL_RE = re.compile(r"^\s*\d+\s*https?://")


def _norm_alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _clean_scheme(url: str) -> str:
    """修掉 PDF 常见的 ``https://https://...`` 重复协议与 ``www.`` 前缀。"""
    raw = (url or "").strip().rstrip(".,;)")
    raw = re.sub(r"^(https?://)https?://", r"\1", raw, flags=re.IGNORECASE)
    raw = re.sub(r"^(https?://)www\.", r"\1", raw, flags=re.IGNORECASE)
    return raw


def _normalize_github_url(url: str) -> Tuple[str, str]:
    """把 GitHub 链接归一成 ``(仓库根, 子目录)``。

    ``/tree|blob/main/sub/dir`` 只保留 ``https://github.com/owner/repo``，
    子目录另存（克隆时不能用带子目录的 URL，git 会直接 404）。
    """
    raw = _clean_scheme(url)
    m = _GITHUB_RE.match(raw)
    if not m:
        return raw, ""
    root = m.group(0).rstrip("/")
    if root.lower().endswith(".git"):
        root = root[:-4]
    return root, raw[len(m.group(0)):].strip("/")


def _join_wrapped_urls(lines: List[str]) -> List[str]:
    """把被 PDF 换行截断的 URL 拼回去。

    实测两例：``https://github.`` + ``com/BIMSBbioinfo/PocketVina.``，
    ``https://github.com/msinclair-py/molecular-`` + ``simulations``。
    续行必须整行都是 URL 字符，避免把正文里的普通单词接进来。
    """
    out: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        while i + 1 < len(lines) and _is_url_continuation(line, lines[i + 1]):
            line = line.rstrip() + lines[i + 1].strip()
            i += 1
        out.append(line)
        i += 1
    return out


def _is_url_continuation(line: str, nxt: str) -> bool:
    nxt = (nxt or "").strip()
    if not nxt or len(nxt) > 120 or not _URL_CONT_RE.match(nxt):
        return False
    m = _URL_RE.search(line or "")
    if not m:
        return False
    tail = m.group(0)
    if tail.endswith(("/", "-", "_", "=", "?", "&")):
        return True
    if tail.endswith("."):
        after = tail.split("//", 1)[-1].rstrip(".")
        return "/" not in after  # 只补「域名被断在点号处」，不接正文句号
    return False


def _repo_role(url: str, ctx: str, line: str, title: str = "") -> Tuple[str, float, str]:
    """判定一个 GitHub 仓库是「本论文自有」还是「被引用的第三方」。

    只靠 URL 本身无法区分：实测同一篇论文里，自有仓库与被引用的依赖仓库
    混在一起，旧逻辑永远取第一个，于是 MECo 克隆了 Qwen2.5-Coder、
    StructBioReasoner 克隆了 crewAI、pocketvina 克隆了别人的 Blind。
    这里用「代码可用表述 + 仓库名匹配 - 引用标记 - awesome-list」打分。
    """
    key = _repo_key(url)
    low = (ctx or "").lower()
    # URL 本身也含仓库名：比对前先剔除，否则每个仓库都会“匹配”自己的 URL
    prose = _norm_alnum(_URL_RE.sub(" ", low))
    score = 0.0
    why: List[str] = []

    if any(h in low for h in _OWN_HINTS):
        score += 3
        why.append("+3 代码可用表述")
    if key and len(key) >= 4 and (key in prose or key in _norm_alnum(title)):
        score += 2
        why.append("+2 仓库名与论文/上下文匹配")
    if (_CITATION_RE.search(ctx or "") or _FOOTNOTE_URL_RE.match((line or "").strip())
            or any(h in low for h in _THIRD_PARTY_HINTS)):
        score -= 4
        why.append("-4 引用/第三方线索")
    if any(h in key for h in _LIST_REPO_HINTS):
        score -= 5
        why.append("-5 文献/列表仓库")

    role = "own_code" if score >= 2 else ("third_party" if score <= -2 else "unclear")
    confidence = min(0.99, 0.5 + abs(score) / 10.0)
    return role, confidence, "；".join(why) or "无明确线索"


def _repo_key(url: str) -> str:
    m = _GITHUB_RE.match(url or "")
    return _norm_alnum(m.group(2)) if m else ""


def extract_code_refs(lines: List[str], title: str = "") -> Tuple[List[CodeRef], List[CodeRef]]:
    """返回 ``(code_refs, data_refs)``。

    通过 URL 所在行/相邻行的关键词判断该链接是代码仓库还是数据集；
    GitHub 链接还会归一化并判定归属（自有 / 第三方），供候选排序使用。
    """
    code_refs: List[CodeRef] = []
    data_refs: List[CodeRef] = []
    seen: set[str] = set()

    logical = _join_wrapped_urls(lines)
    for i, line in enumerate(logical):
        ctx = " ".join(logical[max(0, i - 2) : i + 3]).lower()
        for m in _URL_RE.finditer(line):
            url = m.group(0).rstrip(".,;)")
            root, subpath = _normalize_github_url(url)
            if _GITHUB_RE.match(root):
                if root in seen:
                    continue
                seen.add(root)
                role, confidence, reason = _repo_role(root, ctx, line, title)
                code_refs.append(
                    CodeRef(url=root, kind="repo", evidence=line.strip()[:200],
                            role=role, confidence=confidence, subpath=subpath,
                            role_reason=reason)
                )
                continue
            # 只剩空域名的残片（``https://github.com/``）不是可用仓库
            if re.fullmatch(r"https?://(?:www\.)?github\.com/?", url, re.IGNORECASE):
                continue
            if url in seen:
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
    return code_refs, data_refs


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
    ("参考文献", "references"),
    ("附录", "appendix"),
]


def classify_heading(heading: str) -> str:
    low = (heading or "").strip().lower()
    for needle, kind in _KIND_MAP:
        if needle in low:
            return kind
    return "section"
