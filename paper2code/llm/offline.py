"""离线 Provider：不依赖任何外部 API 的讲解/复现能力实现。

* 大纲：论文结构 → 教学叙事重排
* 幻灯片：抽取式摘要 + 位置先验 + 图/表锚定
* 播客对白：双人问答模板 + 论文事实填充
* 伪代码→Python：基于规则的逐行翻译器

这不是「假的占位实现」——它对结构清晰的技术论文能产出可读、可用的结果，
因此 MVP 在完全离线的单机环境下即可演示。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .. import numutil, textutil
from ..models import Algorithm, Figure, Paper, Section, Slide, Table
from .base import LLMProvider

# --------------------------------------------------------------------------- #
# 教学大纲模板
# --------------------------------------------------------------------------- #

_ZH_HEAD = {
    "I. INTRODUCTION": "引言：问题从哪来",
    "II. METHODS": "方法：模型与基组",
    "III. RESULTS AND DISCUSSION": "结果与讨论",
    "IV. CONCLUSIONS AND OUTLOOK": "结论与展望",
    "DATA AVAILABILITY STATEMENT": "数据可用性",
    "ACKNOWLEDGMENTS": "致谢",
    "SUPPLEMENTARY MATERIAL": "补充材料",
}

_GLOSS = [
    (r"wave functions?", "波函数"),
    (r"molecular orbitals?|\bMOs?\b", "分子轨道"),
    (r"Hamiltonians?", "哈密顿量"),
    (r"overlap matrices?", "重叠矩阵"),
    (r"eigenvalues?", "本征值"),
    (r"eigenvalue problem", "本征值问题"),
    (r"quasi-atomic minimal basis", "准原子最小基"),
    (r"\bQUAMBOs?\b", "QUAMBO"),
    (r"\bSchNOrb\b", "SchNOrb"),
    (r"deep (convolutional )?neural network", "深度神经网络"),
    (r"Hartree[-\s]Fock|\bHF\b", "Hartree-Fock"),
    (r"Density Functional Theory|\bDFT\b", "密度泛函理论"),
    (r"atomic orbitals?", "原子轨道"),
    (r"basis (set|representation)", "基组表示"),
]


def _zh_head(head: str) -> str:
    h = (head or "").strip()
    if h in _ZH_HEAD:
        return _ZH_HEAD[h]
    return h


def _to_zh(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return s
    for pat, zh in _GLOSS:
        s = __import__("re").sub(pat, zh, s, flags=__import__("re").IGNORECASE)
    s = __import__("re").sub(r"\s+", " ", s).strip()
    if not any("\u4e00" <= c <= "\u9fff" for c in s):
        return "论文指出：" + s
    return s


_FALLBACK_OUTLINE = [
    "一句话总结",
    "研究背景与要解决的问题",
    "核心思想",
    "方法细节",
    "实验设置与数据",
    "关键结果",
    "复现要点",
    "局限与展望",
]


class OfflineProvider(LLMProvider):
    name = "offline"

    # ------------------------------------------------------------------ #
    # 大纲
    # ------------------------------------------------------------------ #
    def outline(self, paper: Paper, target_slides: int = 12) -> List[str]:
        names = [s.heading for s in paper.body_sections()]
        kinds = [s.kind for s in paper.body_sections()]

        outline: List[str] = []
        title = textutil.title_case_heading(paper.title or "论文")
        outline.append(f"论文速览：{title}")

        def pick(kind: str) -> Optional[str]:
            for name, k in zip(names, kinds):
                if k == kind:
                    return name
            return None

        for kind, label in (
            ("introduction", "研究背景与要解决的问题"),
            ("related_work", "相关工作与本文定位"),
            ("background", "预备知识"),
            ("method", "核心方法"),
            ("experiment", "实验：设置、数据与指标"),
        ):
            hit = pick(kind)
            outline.append(_zh_head(hit) if hit else label)

        # 方法/实验章节往往有多个，逐个补入
        for name, k in zip(names, kinds):
            if k in ("method", "experiment") and name not in outline:
                outline.append(_zh_head(name))

        if getattr(paper, "formulas", None):
            outline.append("带编号的公式（仅收录正文有解释的）")
        if paper.algorithms:
            outline.append("关键算法流程（伪代码逐行精读）")
        if paper.tables:
            outline.append(f"重点结果解读（{len(paper.tables)} 张表格）")
        if paper.figures:
            outline.append(f"图表速读（{len(paper.figures)} 张图）")

        conc = pick("conclusion")
        outline.append(_zh_head(conc) if conc else "结论与局限")
        outline.append("复现路径与本工具的处理方式")

        outline = textutil.dedupe_keep_order(outline)
        if len(outline) > target_slides:
            # 保留头尾，压缩中间
            keep = max(4, target_slides - 2)
            outline = outline[:keep] + outline[-2:]
        return outline

    # ------------------------------------------------------------------ #
    # 幻灯片
    # ------------------------------------------------------------------ #
    def slides(self, paper: Paper, outline: List[str]) -> List[Slide]:
        slides: List[Slide] = []

        # 封面
        slides.append(
            Slide(
                title=paper.title or "论文讲解",
                bullets=[
                    (", ".join(paper.authors[:4]) or "作者信息未抽取到"),
                    self._one_liner(paper),
                    f"讲解材料自动生成 · 共 {len(outline)} 节",
                ],
                kind="title",
                notes=self._one_liner(paper),
            )
        )

        # 目录
        slides.append(
            Slide(title="本次讲解路线", bullets=[f"{i+1}. {h}" for i, h in enumerate(outline)], kind="agenda")
        )

        by_kind: Dict[str, List[Section]] = {}
        for s in paper.body_sections():
            by_kind.setdefault(s.kind, []).append(s)

        figure_pool = [f for f in paper.figures if f.kind != "unknown"] or paper.figures
        table_pool = list(paper.tables)
        used_fig: set[str] = set()
        used_tab: set[str] = set()

        for i, head in enumerate(outline):
            if "公式" in head:
                continue
            secs = self._sections_for(head, paper, by_kind)
            body = "\n\n".join(s.text for s in secs)
            bullets = self._bullets(body or paper.abstract, head, n=5)

            slide = Slide(title=head, bullets=bullets, kind="content")

            # 图表锚定：把还没用过的表/图分配到语义上最合适的页
            low = head.lower()
            is_result = any(k in low for k in ("结果", "实验", "result", "experiment", "图表", "速读", "解读"))
            is_method = any(k in low for k in ("方法", "框架", "架构", "method", "核心", "算法"))

            def take_table() -> Optional[Table]:
                avail = [t for t in table_pool if t.id not in used_tab]
                if not avail:
                    return None
                used_tab.add(avail[0].id)
                return avail[0]

            def take_figure(prefer_arch: bool = False) -> Optional[Figure]:
                pool = [f for f in figure_pool if f.id not in used_fig]
                if prefer_arch:
                    arch = [f for f in pool if f.kind == "architecture"]
                    if arch:
                        used_fig.add(arch[0].id)
                        return arch[0]
                if not pool:
                    return None
                used_fig.add(pool[0].id)
                return pool[0]

            if is_result:
                tb = take_table()
                if tb is not None:
                    slide.table_id = tb.id
                    slide.kind = "table"
                    slide.notes = self.explain_table(tb)
                else:
                    fg = take_figure()
                    if fg is not None:
                        slide.figure_id = fg.id
                        slide.kind = "figure"
            elif is_method:
                fg = take_figure(prefer_arch=True) or take_figure()
                if fg is not None:
                    slide.figure_id = fg.id
                    slide.kind = "figure"

            slides.append(slide)

        if getattr(paper, "formulas", None):
            for f in paper.formulas[:8]:
                slides.append(
                    Slide(
                        title=f"公式 ({f.number})",
                        bullets=[
                            f"表达式：{f.expression}",
                            f"论文解释：{_to_zh(f.explanation)}",
                            f"编号 ({f.number}) 与原文一致，便于对照。",
                        ],
                        kind="formula",
                        notes=f"对应论文公式 ({f.number})",
                    )
                )

        # 结论页
        bullets = self._bullets(paper.section_text("conclusion", "discussion") or paper.abstract, "结论", n=4)
        slides.append(Slide(title="小结与可复现性", bullets=bullets, kind="takeaway",
                            notes=self._repro_hint(paper)))
        return slides

    # -- 内部工具 --------------------------------------------------------- #
    def _one_liner(self, paper: Paper) -> str:
        if paper.abstract:
            sents = textutil.split_sentences(paper.abstract)
            if sents:
                return _to_zh(textutil.condense(sents[0], 150))
        return f"本文提出 {textutil.title_case_heading(paper.title)}。"

    def _sections_for(self, head: str, paper: Paper, by_kind: Dict[str, List[Section]]) -> List[Section]:
        # 1) 标题完全匹配
        for s in paper.body_sections():
            if s.heading.strip() == head.strip():
                return [s]
        # 2) 大纲标签 → 章节类型
        mapping = {
            "研究背景": ("introduction", "background"),
            "引言": ("introduction", "background"),
            "相关工作": ("related_work",),
            "预备知识": ("background",),
            "核心方法": ("method",),
            "方法": ("method",),
            "实验": ("experiment",),
            "结果": ("experiment",),
            "结论": ("conclusion", "discussion"),
            "一句话": ("abstract",),
            "速览": ("abstract",),
        }
        # 中文大纲标题 ↔ 原文章节名
        for en, zh in _ZH_HEAD.items():
            if head.strip() == zh or zh in head:
                for s in paper.body_sections():
                    if s.heading.strip() == en:
                        return [s]
        for key, kinds in mapping.items():
            if key in head:
                out: List[Section] = []
                for k in kinds:
                    out.extend(by_kind.get(k, []))
                if out:
                    return out
        if "速览" in head or "总结" in head:
            return [Section(heading="Abstract", text=paper.abstract or "", kind="abstract")]
        if "复现" in head:
            return []
        return []

    def _bullets(self, text: str, head: str, n: int = 5) -> List[str]:
        text = (text or "").strip()
        if not text:
            return [f"（{head}：原文未抽取到对应内容，建议人工补充）"]
        sents = textutil.summarize(text, n=n)
        bullets = [textutil.condense(s, 110) for s in sents]
        bullets = [_to_zh(b) for b in bullets if len(b) >= 8]
        return bullets[:n] or [_to_zh(textutil.condense(text, 120))]

    def _repro_hint(self, paper: Paper) -> str:
        bits = []
        if paper.code_refs:
            bits.append(f"论文给出了 {len(paper.code_refs)} 个代码链接，可直接复用其仓库复现。")
        if paper.algorithms:
            bits.append(f"论文含 {len(paper.algorithms)} 段伪代码，需先翻译为可执行代码。")
        if paper.tables:
            bits.append(f"论文含 {len(paper.tables)} 张表格，其数值可作为复现的对照基准。")
        return " ".join(bits) or "论文未提供代码与伪代码，复现需依据正文描述重建数据。"

    # ------------------------------------------------------------------ #
    # 表格解读
    # ------------------------------------------------------------------ #
    def explain_table(self, table: Table) -> str:
        if not table.rows:
            return f"表 {table.id}：未抽取到有效数据行。"
        numeric_cols: Dict[str, List[float]] = {}
        for ci, name in enumerate(table.header[1:], start=1):
            vals = [numutil.to_number(r[ci]) for r in table.rows if ci < len(r)]
            vals = [v for v in vals if v is not None]
            if vals:
                numeric_cols[name or f"col{ci}"] = vals

        if not numeric_cols:
            return f"表 {table.id}：{table.n_rows} 行，以描述性内容为主。"

        parts = []
        for name, vals in list(numeric_cols.items())[:3]:
            best_i = max(range(len(vals)), key=lambda i: vals[i])
            parts.append(f"{name} 最优 {vals[best_i]:.4g}（第 {best_i + 1} 行）")
        return f"表 {table.id}：{table.n_rows} 行 × {table.n_cols} 列；" + "；".join(parts) + "。"

    # ------------------------------------------------------------------ #
    # 播客对白
    # ------------------------------------------------------------------ #
    def dialogue(self, paper: Paper) -> List[Dict[str, str]]:
        """全中文播客稿：引导作者/读者理解研究目的、行文思路、实验设计、结论与不足。

        仅依据 Introduction / Methods / Results / Discussion（及结论）组织内容；
        **不**讲解公式推导或图表含义。课件中的公式页仍可保留，但不进入本对白。
        """
        t = textutil.title_case_heading(paper.title or "这篇论文")
        intro = self._podcast_excerpt(paper, "introduction", "background", head="引言", n=3)
        methods = self._podcast_excerpt(paper, "method", head="方法", n=4)
        results = self._podcast_excerpt(paper, "experiment", "results", head="结果", n=3)
        discussion = self._podcast_excerpt(
            paper, "discussion", "conclusion", head="讨论与结论", n=3
        )
        if discussion and discussion[0].startswith("（讨论与结论"):
            extra = self._podcast_excerpt(paper, "conclusion", head="结论", n=2)
            if not (extra and extra[0].startswith("（结论")):
                discussion = extra

        tts: List[Dict[str, str]] = []
        add = lambda spk, txt: tts.append({"speaker": spk, "text": str(txt).strip()})  # noqa: E731

        add("A", f"欢迎收听本期论文写作导读。今天我们一起读《{t}》。这期不讲公式、也不拆图表，只帮作者把研究目的、行文思路、实验设计和结论理清楚。")
        add("B", f"好。这篇工作的核心，是围绕「{t}」回答一个具体的科学问题；我们按引言—方法—结果—讨论的顺序走一遍。")

        add("A", "先看引言：作者的研究目的是什么？想解决什么问题？")
        add("B", " ".join(intro))

        add("A", "行文思路上，作者打算怎样展开论证？读者读完全文应该带走什么主线？")
        add(
            "B",
            "全文大致按「提出问题 → 给出方法与实验设计 → 报告结果 → 讨论局限与改进」推进。"
            "引言负责立题与动机，方法章交代设计取舍，结果与讨论负责回答「做到了什么、还差什么」。",
        )

        add("A", "方法与实验设计方面，作者做了哪些关键安排？注意：我们只谈设计意图，不展开公式。")
        add("B", " ".join(methods))

        add("A", "结果部分，作者希望读者抓住哪些结论性信息？同样不要逐图解读。")
        add("B", " ".join(results))

        add("A", "讨论与结论里，有哪些不足，或作者暗示的改进方向？")
        add("B", " ".join(discussion))

        add("A", "如果作者要改下一版稿件，你会提醒他优先补强哪一块？")
        add(
            "B",
            "优先检查：研究目的是否在引言里一句说清；方法章是否让人看懂实验设计的因果链；"
            "结果是否直接回答引言提出的问题；讨论是否诚实写出局限与可改进点。这四块对齐，稿件主线就会更稳。",
        )

        add("A", "好，今天的写作导读就到这里。下期见。")
        add("B", "下期见。")
        return [x for x in tts if x["text"].strip()]

    def _podcast_excerpt(self, paper: Paper, *kinds: str, head: str = "", n: int = 3) -> List[str]:
        """抽取章节要点并尽量中文化；跳过公式/表格式噪声行。"""
        text = (paper.section_text(*kinds) or "").strip()
        if not text and kinds:
            text = self._section_text_by_heading_hints(paper, kinds)
        if not text:
            text = (paper.abstract or "").strip()
        cleaned_lines = []
        for ln in text.splitlines():
            s = ln.strip()
            if not s:
                continue
            low = s.lower()
            if low.startswith(("eq.", "equation", "fig.", "figure", "table ", "tab.")):
                continue
            if s.startswith("$$") or (s.count("=") >= 3 and len(s) < 120):
                continue
            cleaned_lines.append(s)
        text = " ".join(cleaned_lines) if cleaned_lines else text
        return self._bullets(text, head or "章节", n=n)

    def _section_text_by_heading_hints(self, paper: Paper, kinds: tuple) -> str:
        hints = {
            "introduction": ("introduction", "引言", "背景"),
            "background": ("introduction", "background", "背景"),
            "method": ("method", "methods", "方法"),
            "experiment": ("result", "results", "experiment", "结果"),
            "results": ("result", "results", "结果"),
            "discussion": ("discussion", "讨论"),
            "conclusion": ("conclusion", "结论", "outlook", "展望"),
        }
        needles: List[str] = []
        for k in kinds:
            needles.extend(hints.get(k, (k,)))
        chunks: List[str] = []
        for sec in paper.sections:
            h = (sec.heading or "").lower()
            if any(n in h for n in needles):
                if sec.text and sec.text.strip():
                    chunks.append(sec.text.strip())
        return "\n\n".join(chunks)

    def repro_strategy(self, paper: Paper, mode: str, targets: List[Dict[str, Any]]) -> str:
        from ..models import MODE_NO_CODE, MODE_OPEN_SOURCE, MODE_PSEUDOCODE

        n_tab = sum(1 for t in targets if t.get("kind") == "table")
        n_alg = sum(1 for t in targets if t.get("kind") == "algorithm")
        n_fig = sum(1 for t in targets if t.get("kind") == "figure")

        if mode == MODE_OPEN_SOURCE:
            entry = ""
            path = paper.meta.get("local_code_path")
            if path:
                entry = f"本地代码目录为 {path}，可直接离线扫描与执行。"
            elif paper.repo_urls():
                entry = f"正文给出的仓库链接为 {paper.repo_urls()[0]}。"
            return (
                f"本次需对标 {n_tab} 张表格、重绘 {n_fig} 张图。"
                f"{entry}"
                "建议顺序：先锁定依赖版本并固定随机种子（多轮实验的方差往往来自这里），"
                "再跑通仓库入口，最后把产物 CSV 与论文表格按「首列键 + 同名列」对齐做逐格对标。"
                "最典型的失败原因是依赖版本漂移与未固定种子导致的小数位抖动，"
                "因此应先看相对误差的量级分布，再决定是否需要复跑。"
            )
        if mode == MODE_PSEUDOCODE:
            return (
                f"本次需翻译 {n_alg} 段伪代码。"
                "建议顺序：先逐行确认伪代码的输入/输出契约与边界条件（空输入、k 大于流长、"
                "重复值并列等），再翻译为可执行代码，然后用小规模合成输入验证数学性质，"
                "例如排序结果有序、Top-K 与全量排序一致、概率分布归一到 1。"
                "必须明确：这样做只能证明「逻辑等价」，不能证明「指标一致」——"
                "论文报告的数值还依赖数据集、超参与硬件，这些在仅有伪代码的情形下不可复现。"
            )
        if mode == MODE_NO_CODE:
            return (
                f"本次可用输入为 {n_tab} 张表格与论文附带的数据文件。"
                "建议顺序：先把表格整理为 tidy CSV，再校验其内部一致性，"
                "例如占比列是否求和为 100%、汇总行是否等于明细的求和或均值、"
                "标称最优的方法是否确实在对应列上取到极值。"
                "最后按正文描述重绘图表以做风格比对。"
                "数据集、超参与预处理细节的缺失会限制复现上限，这些要素会被列成清单，"
                "供人工补齐后二次复现。"
            )
        return "论文未提供足够信息以制定复现策略。"

    # ------------------------------------------------------------------ #
    # 伪代码 → Python（规则翻译）
    # ------------------------------------------------------------------ #
    def pseudocode_to_python(self, algo: Algorithm, context: str = "") -> str:
        _title_re = re.compile(
            r"^\s*(?:Algorithm|Alg\.?|Procedure|Pseudocode|算法)\s*[0-9]*\s*[.:：]\s*.+$",
            re.IGNORECASE,
        )
        src = [l.rstrip() for l in algo.lines]
        src = [re.sub(r"^\s*(\d+[:.)]|\d+\s)\s*", "", l) for l in src]  # 去掉行号
        # 块首若残留标题行（如 "Algorithm 1: TOPK ..."），剔除，否则会成为非法 Python
        src = [l for l in src if not _title_re.match(l)]

        lines: List[str] = []
        indent = 0
        inputs: List[str] = []
        outputs: List[str] = []
        header_done = False

        def emit(text: str) -> None:
            lines.append("    " * indent + text if text else "")

        def dedent() -> None:
            nonlocal indent
            indent = max(0, indent - 1)

        fn_name = re.sub(r"\W+", "_", (algo.caption or f"algorithm_{algo.id}").lower())[:40].strip("_") or "run"

        for raw in src:
            line = raw.strip()
            if not line:
                continue

            # 注释：▷ ① // # 以及 "comment:"
            line = re.sub(r"^\s*[▷►▸]\s*", "# ", line)
            line = re.sub(r"\s*[▷►▸]\s*.*$", "", line)
            if line.startswith("#"):
                emit(line)
                continue

            low = line.lower().rstrip(".;")

            # Input / Output 声明
            m = re.match(r"^(input|输入|require|requires)\s*[:：]\s*(.+)$", line, re.IGNORECASE)
            if m:
                inputs = [p.strip() for p in re.split(r"[,，;；]", m.group(2)) if p.strip()]
                header_done = True
                continue
            m = re.match(r"^(output|输出|ensure|ensure)\s*[:：]\s*(.+)$", line, re.IGNORECASE)
            if m:
                outputs = [p.strip() for p in re.split(r"[,，;；]", m.group(2)) if p.strip()]
                continue

            # function / procedure
            m = re.match(r"^(?:function|procedure|def|algorithm)\s+([\w\-]+)\s*[\(（](.*?)[\)）]?\s*$", line, re.IGNORECASE)
            if m:
                fn_name = re.sub(r"\W+", "_", m.group(1)) or fn_name
                params = m.group(2).strip()
                emit(f"def {fn_name}({params or ', '.join(_safe_name(i) for i in inputs)}):")
                indent += 1
                header_done = True
                for o in outputs:
                    emit(f"# returns: {o}")
                continue

            # for i = a to b [by s] do
            m = re.match(
                r"^for\s+(\w+)\s*(?:←|=|:=)\s*(.+?)\s+to\s+(.+?)(?:\s+by\s+(.+?))?\s*(?:do)?$",
                line, re.IGNORECASE,
            )
            if m:
                var, start, end, step = m.group(1), m.group(2).strip(), m.group(3).strip(), m.group(4)
                rng = f"range({start}, ({end}) + 1)" if not step else f"range({start}, ({end}) + 1, {step.strip()})"
                emit(f"for {var} in {rng}:")
                indent += 1
                continue

            # for each x in S do
            m = re.match(r"^for\s+each\s+(\w+)\s+in\s+(.+?)\s*(?:do)?$", line, re.IGNORECASE)
            if m:
                emit(f"for {m.group(1)} in {m.group(2).strip()}:")
                indent += 1
                continue

            # while cond do
            m = re.match(r"^while\s+(.+?)\s*(?:do)?$", line, re.IGNORECASE)
            if m:
                emit(f"while {_py_expr(m.group(1))}:")
                indent += 1
                continue

            # repeat / until
            if low in ("repeat", "do"):
                emit("while True:")
                indent += 1
                continue
            m = re.match(r"^until\s+(.+)$", line, re.IGNORECASE)
            if m:
                emit(f"if {_py_expr(m.group(1))}:")
                indent += 1
                emit("break")
                dedent()
                dedent()
                continue

            # if / else if / else
            m = re.match(r"^(?:if|when)\s+(.+?)\s*(?:then)?$", line, re.IGNORECASE)
            if m:
                emit(f"if {_py_expr(m.group(1))}:")
                indent += 1
                continue
            m = re.match(r"^(?:else\s*if|elif|otherwise\s+if)\s+(.+?)\s*(?:then)?$", line, re.IGNORECASE)
            if m:
                dedent()
                emit(f"elif {_py_expr(m.group(1))}:")
                indent += 1
                continue
            if low in ("else", "otherwise", "否则"):
                dedent()
                emit("else:")
                indent += 1
                continue

            # end / 结束
            if re.match(r"^end\s*(for|while|if|function|procedure|loop|if)?$", low) or low in ("endif", "endfor", "endwhile", "end function", "结束", "fi", "od"):
                dedent()
                continue

            # return
            m = re.match(r"^return\s*(.*)$", line, re.IGNORECASE)
            if m:
                emit(f"return {_py_expr(m.group(1))}" if m.group(1).strip() else "return")
                continue

            # break / continue
            if low in ("break", "中断退出"):
                emit("break")
                continue
            if low in ("continue", "continue loop"):
                emit("continue")
                continue

            # 赋值：x ← expr / x := expr
            m = re.match(r"^([\w\[\]\.]+)\s*(?:←|<-|:=|=)\s*(.+)$", line)
            if m:
                lhs = m.group(1).strip()
                rhs = _py_expr(m.group(2))
                if re.search(r"(?<![<>=!])=(?!=)", rhs) and "==" not in rhs:
                    rhs = re.sub(r"(?<![<>=!])=(?!=)", "==", rhs)
                emit(f"{lhs} = {rhs}")
                continue

            # 单纯函数调用
            emit(_py_expr(line))

        # 收尾：函数体为空则补 pass
        if not lines:
            lines = ["def " + fn_name + "():", "    pass"]
        elif not any(l.strip().startswith("def ") for l in lines):
            # 没有函数头 → 包一层
            body = ["    " + l if l.strip() else "" for l in lines]
            lines = [f"def {fn_name}({', '.join(_safe_name(i) for i in inputs) or ''}):"] + body
        if len(lines) == 1:
            lines.append("    pass")

        code = "\n".join(lines)
        return code


# --------------------------------------------------------------------------- #
# 表达式 / 标识符规整
# --------------------------------------------------------------------------- #
_MATH_MAP = [
    (r"\\times", "*"), (r"\\cdot", "*"), (r"\\left", ""), (r"\\right", ""),
    (r"\\arg\s*max", "argmax"), (r"\\arg\s*min", "argmin"),
    (r"\\log", "math.log"), (r"\\exp", "math.exp"), (r"\\sqrt", "math.sqrt"),
    (r"\\lVert|\\rVert|\\|", ""), (r"\\infty", "float('inf')"),
    (r"\bAND\b", "and"), (r"\bOR\b", "or"), (r"\bNOT\b", "not"),
    (r"\btrue\b", "True"), (r"\bfalse\b", "False"), (r"\bnil\b", "None"), (r"\bnull\b", "None"),
    (r"≠", "!="), (r"≤", "<="), (r"≥", ">="), (r"×", "*"), (r"÷", "/"),
    (r"∈", " in "), (r"∉", " not in "), (r"∧", " and "), (r"∨", " or "),
    (r"¬", " not "), (r"√", "math.sqrt"), (r"∞", "float('inf')"),
]

#: 伪代码里常见的「空容器 / 空值」描述 → 具体 Python 字面量
_VALUE_MAP = [
    (r"\bempty\s+(?:min[- ]?heap|max[- ]?heap|heap|priority\s*queue|pq)\b", "[]"),
    (r"\bempty\s+(?:set)\b", "set()"),
    (r"\bempty\s+(?:map|dict|dictionary|hash\s*table|hashtable)\b", "{}"),
    (r"\bempty\s+(?:list|array|vector|sequence|stack|queue|buffer|table)\b", "[]"),
    (r"\bempty\s+(?:string|str)\b", "''"),
    (r"\bnew\s+(?:min[- ]?heap|max[- ]?heap|heap|priority\s*queue)\b", "[]"),
    (r"\ba\s+new\s+(?:list|array|set|map|dict)\b", "[]"),
]


def _py_expr(expr: str) -> str:
    e = (expr or "").strip()
    for pat, rep in _VALUE_MAP:
        e = re.sub(pat, rep, e, flags=re.IGNORECASE)
    for pat, rep in _MATH_MAP:
        e = re.sub(pat, rep, e)
    e = re.sub(r"\s+", " ", e).strip()
    return e


def _safe_name(name: str) -> str:
    n = re.sub(r"\W+", "_", (name or "").strip()).strip("_")
    if not n:
        return "arg"
    if n[0].isdigit():
        n = "a_" + n
    return n
