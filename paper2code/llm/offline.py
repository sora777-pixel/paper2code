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
# 赋值箭头归一化
# --------------------------------------------------------------------------- #
#: 论文伪代码的赋值符号五花八门，PDF 抽取时还可能进一步失真：
#:   * LaTeX 原生：``←`` ``⟵`` ``⇐`` ``≔`` ``:=``
#:   * ASCII 习惯：``<-``
#:   * **字体替换残渣**：基础 PDF 字体（base14）不含 U+2190，渲染/抽取时会
#:     把 ``←`` 退化成 ``·`` ``⋅`` ``∙`` ``•`` 这类中点/项目符号。
#:     这是实跑中真实遇到的问题——整段算法会因此变成语法错误。
#: 归一化只作用于**结构性位置**（语句首的赋值、for 头），因此表达式内部的
#: ``·``（乘法）不会被误伤。
_ARROW = r"(?:←|⟵|⇐|≔|:=|<-|·|⋅|∙|•)"
#: 语句首赋值：``heap · []`` / ``let x ← 3`` / ``a, b := 1, 2``
_ASSIGN_HEAD_RE = re.compile(
    r"^(\s*(?:let\s+|set\s+)?[A-Za-z_][\w\.\[\]\'\"]*"
    r"(?:\s*,\s*[A-Za-z_][\w\.\[\]\'\"]*)*)\s*" + _ARROW + r"\s*(?=\S)"
)
#: for 循环头：``for x · S do``
_FOR_HEAD_RE = re.compile(r"^(\s*for\s+(?:each\s+)?[\w\s,]+?)\s*" + _ARROW + r"\s*", re.IGNORECASE)


def _normalize_arrows(line: str) -> str:
    """把结构位置的赋值箭头统一成 ``=``，供后续规则翻译消费。"""
    line = _FOR_HEAD_RE.sub(r"\1 = ", line, count=1)
    line = _ASSIGN_HEAD_RE.sub(r"\1 = ", line, count=1)
    return line


# --------------------------------------------------------------------------- #
# 教学大纲模板
# --------------------------------------------------------------------------- #
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
            outline.append(hit if hit else label)

        # 方法/实验章节往往有多个，逐个补入
        for name, k in zip(names, kinds):
            if k in ("method", "experiment") and name not in outline:
                outline.append(name)

        if paper.algorithms:
            outline.append("关键算法流程（伪代码逐行精读）")
        if paper.tables:
            outline.append(f"重点结果解读（{len(paper.tables)} 张表格）")
        if paper.figures:
            outline.append(f"图表速读（{len(paper.figures)} 张图）")

        conc = pick("conclusion")
        outline.append(conc if conc else "结论与局限")
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

        # 结论页：固定「问题 → 方法 → 结果 → 局限 → 复现」叙事，和在线模式保持一致
        summary = self._bullets(paper.section_text("conclusion", "discussion") or paper.abstract, "结论", n=4)
        bullets = []
        for label, text in zip(("结果", "局限", "补充", "补充"), summary):
            item = f"{label}：{text}" if text else ""
            if item and item not in bullets:
                bullets.append(item)
        problem = self._bullets(paper.section_text("introduction", "background") or paper.abstract, "背景", 1)
        method = self._bullets(paper.section_text("method") or paper.abstract, "方法", 1)
        if problem:
            bullets.insert(0, f"问题：{problem[0]}")
        if method:
            bullets.insert(1 if problem else 0, f"方法：{method[0]}")
        bullets.append("复现：" + self._repro_hint(paper))
        slides.append(Slide(title="总结：要点回顾与复现路径", bullets=bullets[:6], kind="takeaway",
                            notes="按「问题、方法、结果、局限、复现」的顺序回顾全篇。"))
        return slides

    # -- 内部工具 --------------------------------------------------------- #
    def _one_liner(self, paper: Paper) -> str:
        if paper.abstract:
            sents = textutil.split_sentences(paper.abstract)
            if sents:
                return textutil.condense(sents[0], 150)
        return f"本文提出 {textutil.title_case_heading(paper.title)}。"

    def _sections_for(self, head: str, paper: Paper, by_kind: Dict[str, List[Section]]) -> List[Section]:
        # 1) 标题完全匹配
        for s in paper.body_sections():
            if s.heading.strip() == head.strip():
                return [s]
        # 2) 大纲标签 → 章节类型
        mapping = {
            "研究背景": ("introduction", "background"),
            "相关工作": ("related_work",),
            "预备知识": ("background",),
            "核心方法": ("method",),
            "实验": ("experiment",),
            "结论": ("conclusion", "discussion"),
            "一句话": ("abstract",),
        }
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
        bullets = [b for b in bullets if len(b) >= 8]
        return bullets[:n] or [textutil.condense(text, 120)]

    def _repro_hint(self, paper: Paper) -> str:
        bits = []
        own = paper.repo_urls()
        if own:
            bits.append(f"论文给出了 {len(own)} 个自有代码仓库链接，可直接复用其仓库复现。")
        elif any(getattr(r, "kind", "") == "repo" for r in paper.code_refs):
            bits.append("论文只引用了第三方/文献列表仓库，未给出本论文自有代码，不能直接复用其实现。")
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
        t = textutil.title_case_heading(paper.title or "这篇论文")
        abstract_sents = textutil.split_sentences(paper.abstract)
        one_liner = textutil.condense(abstract_sents[0], 140) if abstract_sents else ""

        intro_sents = self._bullets(paper.section_text("introduction", "background") or paper.abstract, "背景", 3)
        method_sents = self._bullets(paper.section_text("method") or paper.abstract, "方法", 4)
        exp_sents = self._bullets(paper.section_text("experiment") or paper.abstract, "实验", 3)
        conc_sents = self._bullets(paper.section_text("conclusion", "discussion") or paper.abstract, "结论", 3)

        def spoken(sents: List[str], n: int = 2) -> str:
            """把要点句合成一段 ≤n 句、适合朗读的口语回答（避免长句憋气）。"""
            picked = [textutil.condense(s, 90).rstrip("。；") for s in sents[:n] if s.strip()]
            return ("。".join(picked) + "。") if picked else ""

        tts: List[Dict[str, str]] = []
        add = lambda spk, txt: tts.append({"speaker": spk, "text": txt.strip()})  # noqa: E731

        add("A", f"欢迎收听本期论文精读。今天要聊的，是《{t}》。先别急着抠细节，一句话版本，这篇到底做了什么？")
        add("B", "一句话概括：" + (one_liner or f"这篇论文围绕《{t}》提出了一个新的技术方案。"))

        add("A", "嗯，听着有点意思。那它要解决的核心问题是什么？为什么这个问题值得做？")
        add("B", "背景是这样的。" + spoken(intro_sents))

        if paper.algorithms:
            a0 = paper.algorithms[0]
            add("A", f"论文里还给了伪代码。我们能不能顺着 Algorithm {a0.id} 的流程走一遍？")
            add(
                "B",
                f"可以。这段一共 {len(a0.lines)} 行，核心逻辑是："
                + " ".join(textutil.condense(l, 80) for l in a0.lines[:5] if l.strip()),
            )

        add("A", "那方法层面，最关键的设计是什么？")
        add("B", "说白了，" + spoken(method_sents, 3))

        if paper.tables:
            tb = paper.tables[0]
            add("A", f"效果怎么样？我看论文里表 {tb.id} 给了一堆数字。")
            add("B", f"{self.explain_table(tb)} 这些数，后面复现时要一格一格对一下。")

        if len(exp_sents) > 1:
            add("A", "实验设置上有什么坑要注意吗？")
            add("B", "嗯，有两个点得先说清楚。" + spoken(exp_sents, 2))

        add("A", "如果我自己动手复现，最难的地方在哪？")
        if paper.code_refs:
            add("B", "好消息是作者开源了。我们可以直接用它的仓库和数据跑通，"
                     "把论文表格里的数当对照基准，难点主要在依赖版本对齐。")
        elif paper.algorithms:
            add("B", "作者没给完整代码，只有伪代码。得先把伪代码翻成能跑的代码，"
                     "再按论文描述造小规模数据验证逻辑。注意，这只能验证逻辑对不对，验证不了指标对不对。")
        else:
            add("B", "这篇既没代码也没伪代码，只能照正文描述重建实验流程，"
                     "复现结论的置信度会低一些，通常只能做到趋势一致。")

        add("A", "最后聊聊局限，还有后续可能的方向？")
        add("B", spoken(conc_sents, 2) or "论文自己也提到了一些局限，细节可以看原文的讨论部分。")
        add("A", "好，今天就到这儿。感谢收听，我们下期再见。")
        add("B", "再见。")
        return [x for x in tts if x["text"].strip()]

    # ------------------------------------------------------------------ #
    # 复现策略（离线规则版）
    # ------------------------------------------------------------------ #
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
        # 归一化隐形字符（NBSP/零宽/连字）——参数表里的 NBSP 会让函数签名直接变成
        # 语法错误，这是 PDF 抽取链路上最常见也最隐蔽的坑
        src = [textutil.normalize_unicode(l) for l in src]
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

            # 赋值箭头归一化（含字体替换残渣 · / ⋅），必须在下面所有规则之前
            line = _normalize_arrows(line)

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
