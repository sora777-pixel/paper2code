"""从论文正文里抽取「可执行的方法学信息」（roadmap：报告改进）。

复现报告原有的「操作手册」讲的是**本工具怎么跑**；这里补的是**论文自己的方法流程**：

1. **数据来源 → 官方链接**：正文提到 PDBbind / PRODIGY / SKEMPI2 / Docking Benchmark 5.5
   这类公开资源时，附上官方入口，省掉读者再去猜 URL；
2. **关键筛选标准的注释**：如 “with a maximum resolution of 2.5Å” 属于**结构质量门槛**，
   要说明它在筛什么、为什么这么筛；
3. **方法流程 → 技术操作说明书**：把论文里明确给出的流程线索（分节标题、连接符串起来的
   步骤句）整理成编号步骤，并**逐条标注出处与缺口**。

诚实性约定（沿用本项目口径）：
* 全部为**确定性抽取**（正则 + 章节结构），不猜、不编；
* 抽不到就明说「未能自动抽取」，并给出可核对的出处（章节名 / 原句），绝不硬凑；
* 官方链接是**登记值**，与论文引用不一致时以论文为准——报告里会写明这句话。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ..models import Paper

# --------------------------------------------------------------------------- #
# 公开资源 → 官方入口
# --------------------------------------------------------------------------- #
#: 只登记官方站点（不给镜像/代理/第三方转存）。键是**匹配用的别名**，值是 (展示名, URL)。
KNOWN_RESOURCES: List[Tuple[str, str, str]] = [
    ("pdbbind", "PDBbind", "http://www.pdbbind.org.cn/"),
    ("protein-protein docking benchmark", "Docking Benchmark (5.5)", "https://zlab.umassmed.edu/benchmark/"),
    ("docking benchmark", "Docking Benchmark (5.5)", "https://zlab.umassmed.edu/benchmark/"),
    ("skempi", "SKEMPI / SKEMPI2", "https://life.bsc.es/pid/skempi2"),
    ("prodigy", "PRODIGY", "https://wenmr.science.uu.nl/prodigy/"),
    ("dbaasp", "DBAASP", "https://dbaasp.org/"),
    ("chembl", "ChEMBL", "https://www.ebi.ac.uk/chembl/"),
    ("bindingdb", "BindingDB", "https://www.bindingdb.org/"),
    ("alphafold", "AlphaFold DB", "https://alphafold.ebi.ac.uk/"),
    ("uniprot", "UniProt", "https://www.uniprot.org/"),
    ("pubchem", "PubChem", "https://pubchem.ncbi.nlm.nih.gov/"),
    ("chembl", "ChEMBL", "https://www.ebi.ac.uk/chembl/"),
    ("emdb", "EMDB", "https://www.ebi.ac.uk/emdb/"),
    ("protein data bank", "PDB", "https://www.rcsb.org/"),
    ("rcsb", "PDB", "https://www.rcsb.org/"),
]

#: 常用软件/服务器：不给下载站，只标注名称与用途，避免把读者引到非官方镜像
KNOWN_TOOLS: List[Tuple[str, str, str]] = [
    ("rosetta", "Rosetta", "蛋白结构建模与能量计算"),
    ("pyrosetta", "PyRosetta", "Rosetta 的 Python 接口"),
    ("gromacs", "GROMACS", "全原子分子动力学"),
    ("amber", "AMBER", "分子力学 / MM-GBSA"),
    ("haddock", "HADDOCK", "蛋白-蛋白对接"),
    ("autodock", "AutoDock", "分子对接"),
    ("vina", "AutoDock Vina", "分子对接（打分函数）"),
    ("openmm", "OpenMM", "GPU 分子模拟"),
    ("rdkit", "RDKit", "化学信息学工具包"),
    ("tensorflow", "TensorFlow", "深度学习框架"),
    ("pytorch", "PyTorch", "深度学习框架"),
    ("keras", "Keras", "深度学习框架"),
    ("scikit-learn", "scikit-learn", "机器学习工具包"),
    ("xgboost", "XGBoost", "梯度提升树"),
    ("libsvm", "LIBSVM", "支持向量机"),
    ("mm/gbsa", "MM/GBSA", "结合自由能估算"),
    ("mm/pbsa", "MM/PBSA", "结合自由能估算"),
    ("dssp", "DSSP", "二级结构分配"),
    ("freesasa", "FreeSASA", "溶剂可及表面积"),
]


# --------------------------------------------------------------------------- #
# 1. 数据来源 → 官方链接
# --------------------------------------------------------------------------- #
def data_sources(paper: Paper) -> List[Dict[str, Any]]:
    """正文里出现过的公开数据资源 + 官方链接（只列**确实出现过**的）。"""
    haystack = _scan_text(paper).lower()
    out: List[Dict[str, Any]] = []
    seen = set()
    for alias, name, url in KNOWN_RESOURCES:
        if alias in haystack and name not in seen:
            seen.add(name)
            out.append({"name": name, "url": url,
                        "evidence": _evidence(paper, alias)})
    return out


def tools_mentioned(paper: Paper) -> List[Dict[str, str]]:
    """论文里点名用到的软件/方法（用于说明书里的「工具链」一栏）。"""
    haystack = _scan_text(paper).lower()
    out: List[Dict[str, str]] = []
    seen = set()
    for alias, name, purpose in KNOWN_TOOLS:
        if alias in haystack and name not in seen:
            seen.add(name)
            out.append({"name": name, "purpose": purpose, "evidence": _evidence(paper, alias)})
    return out


# --------------------------------------------------------------------------- #
# 2. 关键筛选标准
# --------------------------------------------------------------------------- #
#: (正则, 名称, 注释, 建议核对的量)
_CRITERIA: List[Tuple[re.Pattern, str, str, str]] = [
    (re.compile(r"maximum\s+resolution\s+of\s+([0-9.]+)\s*(?:Å|A|angstrom)", re.I),
     "结构分辨率门槛",
     "**晶体结构质量门槛**：分辨率数值越小、电子密度越清楚。用它筛掉低质量结构，"
     "是 X 射线结构数据集的标准做法；换数据源时必须沿用同一门槛，否则不可比。",
     "分辨率（Å）"),
    (re.compile(r"resolution\s*(?:of|≤|<=|<)\s*([0-9.]+)\s*(?:Å|A|angstrom)", re.I),
     "结构分辨率门槛", "同上：控制结构精细度的筛选线。", "分辨率（Å）"),
    (re.compile(r"R-?free\s*(?:of|≤|<=|<)?\s*([0-9.]+)", re.I),
     "R-free 门槛", "晶体学交叉验证指标，越低越可信；与分辨率一起用才足以判断结构质量。", "R-free"),
    (re.compile(r"(?:sequence\s+identity|identity)\s*(?:of|≤|<=|<|below|less than)?\s*([0-9.]+)\s*%", re.I),
     "序列同一性去冗余", "**去冗余门槛**：同一性过高的复合物会被剔除，避免训练/测试集泄漏、"
     "把指标抬虚。复现时若不做这一步，数值会明显偏高。", "序列同一性（%）"),
    (re.compile(r"\bnon-?redundant\b|nonredundant|去冗余", re.I),
     "去冗余", "在相似结构/复合物之间只保留一条，防止数据泄漏。", "去冗余规则"),
    (re.compile(r"X-?ray[- ]resolved", re.I),
     "实验结构类型限制", "只取**实验解析**的晶体结构（区别于 NMR、冷冻电镜与预测模型），"
     "决定了数据可获取性与误差量级。", "结构来源类型"),
    (re.compile(r"(?:experimental|experimentally)\s+(?:determined|measured)", re.I),
     "实验测定限制", "只取实验测定值，不用计算/预测值充当真值。", "真值来源"),
]


def selection_criteria(paper: Paper) -> List[Dict[str, Any]]:
    """论文明确写出的筛选/纳入标准 + 大白话注释 + 原句出处。"""
    text = _scan_text(paper)
    out: List[Dict[str, Any]] = []
    seen = set()
    for pat, name, note, metric in _CRITERIA:
        m = pat.search(text)
        if not m or name in seen:
            continue
        seen.add(name)
        quote = _sentence_around(text, m.start(), m.end())
        out.append({"name": name, "quote": quote, "note": note, "metric": metric,
                    "section": _section_of(paper, quote)})
    return out


# --------------------------------------------------------------------------- #
# 3. 方法流程 → 技术操作说明书
# --------------------------------------------------------------------------- #
#: 明显不是「方法阶段」的标题
_GENERIC_HEADS = {
    "introduction", "background", "related work", "methods", "method", "materials and methods",
    "results", "result", "discussion", "conclusion", "conclusions", "references",
    "acknowledgments", "acknowledgements", "abstract", "supplementary", "appendix",
    "introduction and background", "data availability", "author contributions",
    "results and discussion", "final considerations", "associated content",
    "author information", "abbreviations", "supporting information", "experimental section",
    "materials", "concluding remarks", "future work", "limitations",
    "引言", "背景", "方法", "材料与方法", "结果", "讨论", "结论", "参考文献", "致谢", "附录",
    "结果与讨论", "结论与展望", "作者信息", "补充材料",
}

#: 形似章节标题但其实不是「方法阶段」的整句（全大写、含 and/of 的连接词等）
_GENERIC_HEAD_RE = re.compile(
    r"^(?:results?|discussion|conclusions?|references?|acknowledg\w*|appendix|supplementary|"
    r"associated content|author information|abbreviations|supporting information|"
    r"final considerations|concluding remarks|data availability|author contributions)\b",
    re.I)

_CONNECTORS = ("→", "⟶", "➔", "->", "—", "–", " then ", " afterwards ", "之后", "随后",
               "接着", "继而", "而后")

#: 「A — B — C — D」式流程链：≥3 段、段间用连接符相连、每段以大写字母或汉字开头
_CHAIN_RE = re.compile(
    r"([A-Z\u4e00-\u9fff][^—–→⟶\n]{1,140})(?:(?:—|–|->|→|⟶)([^—–→⟶\n]{1,140})){2,}")
_CHAIN_SPLIT_RE = re.compile(r"—|–|->|→|⟶")

_STEP_HINT = re.compile(r"^(?:step\s*\d|stage\s*\d|phase\s*\d|第\s*[0-9一二三四五六七八九十]+\s*步)", re.I)


def workflow_steps(paper: Paper) -> Dict[str, Any]:
    """从论文里抽取「方法流程」，整理成编号步骤。返回 ``{steps, source, note}``。

    两条独立线索，谁先抽到用谁：
    ① **连续的小节标题**——方法部分常按阶段命名小节（结构后处理 / 几何优化 / 描述符 / 机器学习 / 软件），
       把它们按出现顺序串起来就是流程；
    ② **连接符串起来的流程链**——形如 ``结构后处理 — 几何优化 — 描述符 — 机器学习 — 软件``
       的一段文字（英文论文里常写成 ``A — B — C — D``，或标题式短语用破折号连缀）。

    两条都抽不到就返回空，并在报告里写「未能自动抽取，请以原文为准」——
    绝不用零散句子硬凑出一条看着像流程的东西。
    """
    heads = _stage_headings(paper)
    if len(heads) >= 3:
        return {"steps": [{"n": i, "name": h, "detail": "", "evidence": "小节标题"}
                          for i, h in enumerate(heads, 1)],
                "source": "方法部分的连续小节标题",
                "note": "按小节标题出现顺序串起来的流程；标题命名由作者决定，可能与实际执行顺序不完全一致。"}

    chain = _flow_chain(_scan_text(paper))
    if len(chain) >= 3:
        return {"steps": [{"n": i, "name": p, "detail": "", "evidence": "正文流程链"}
                          for i, p in enumerate(chain, 1)],
                "source": "正文中用连接符串起来的流程链",
                "note": "从一段用破折号/箭头连缀的文字里切出的阶段；可能漏掉作者写在别处的中间环节，"
                        "也可能把收尾的条件从句一并纳入，请回原文核对。"}

    runin = _runin_headings(_scan_text(paper))
    #: 上限定在 12：方法流程超过 12 段就不像「一条流水线」了，宁可如实说「没抽到」
    if 3 <= len(runin) <= 12:
        return {"steps": [{"n": i, "name": p, "detail": "", "evidence": "段首小节标题（run-in heading）"}
                          for i, p in enumerate(runin, 1)],
                "source": "方法部分段首的「小节标题 + 正文」写法（此刊把方法阶段嵌在段落开头）",
                "note": "这些阶段名取自段首的小节标题（作者用的「标题式短语 + 句号」写法），"
                        "顺序按正文出现次序；个别阶段可能被拆到别的段落里，请回原文核对完整性。"}

    return {"steps": [], "source": "",
            "note": "论文没有给出可自动识别的流程线索（连续小节标题、段首小节标题或连接符流程链）。"}


#: 标题式短语里可以小写的虚词
_STOPWORDS = {"a", "an", "the", "of", "for", "and", "in", "on", "to", "with", "by", "from",
              "at", "as", "into", "over", "between", "via", "using", "based", "our"}


def _runin_headings(text: str) -> List[str]:
    """段首的「标题式短语 + 句号」小节标题（此刊把方法阶段写成 `Machine Learning. We ...`）。

    只取**开头连续**的那一段：收录顺序里一旦出现「不像阶段名」的条目就立刻停下。
    实测这样能干净地截出真正的阶段（Data Sets / Structure Preprocessing / Structure Postprocessing /
    Geometry Optimization / Descriptors of Protein-Protein Association / Machine Learning / The PBEE Software），
    而把后面的参考文献缩写（`via GitHub` 起就不再像阶段名）挡在外面。
    """
    cands: List[str] = []
    for para in re.split(r"\n+", _cut_back_matter(text)):
        head = re.match(r"^((?:[^.\n]{1,70}?\.\s+)+)", para.strip())
        if not head:
            continue
        cands.extend(f.strip() for f in re.findall(r"([^.\n]{1,70}?)\.\s", head.group(1)))

    run: List[str] = []
    misses = 0
    for frag in cands:
        if frag in run:
            continue
        if _is_title_phrase(frag):
            run.append(frag)
            misses = 0
        else:
            misses += 1
            if len(run) >= 3 and misses >= 3:
                break      # 连着 3 个都不像阶段名 → 流程结束（后面通常是参考文献/致谢等后置部分）
    return run


#: 后置部分（参考文献/关联内容/致谢…）在 PDF 里常被并进方法章节，必须切掉
_BACK_MATTER_RE = re.compile(
    r"\n\s*(?:REFERENCES|References|ASSOCIATED CONTENT|Associated Content|"
    r"AUTHOR INFORMATION|Author Information|ACKNOWLEDGMENTS|Acknowledgments|"
    r"SUPPORTING INFORMATION|Supporting Information|参考文献|致谢|附录)\s*\n")


def _cut_back_matter(text: str) -> str:
    """截掉参考文献等后置部分：否则会把作者名、期刊缩写当成「方法阶段」。"""
    m = _BACK_MATTER_RE.search(text)
    return text[:m.start()] if m else text


def _is_title_phrase(s: str) -> bool:
    """判断是不是「标题式短语」：2–8 个词、实词全大写、不以标点收尾、不含逗号/缩写人名。

    这是区分 `Machine Learning.`（小节标题）与 `We have coded a super learner ...`（正常句子）
    的关键：正常句子里的实词基本是小写，标题式短语基本每个实词都大写。
    """
    words = (s or "").split()
    if not (2 <= len(words) <= 8):        # 单词碎片（Model / Loop / Trees / F）一律不要
        return False
    if s[-1] in ",;:—-–(/" or "," in s:
        return False
    if any(len(w.strip("(),-/")) == 1 for w in words):   # 人名缩写（A. / B.）
        return False
    significant = [w for w in words if w.strip("(),-/").lower() not in _STOPWORDS]
    if not significant:
        return False
    titled = sum(1 for w in significant if w[0].isupper() or w.isupper())
    return titled == len(significant)


def _flow_chain(text: str) -> List[str]:
    """在正文里找最长的「A — B — C …」流程链；每段都要像阶段名（大写/汉字开头、词数有限）。"""
    best: List[str] = []
    for m in _CHAIN_RE.finditer(text):
        segs = [s.strip(" ,;:、，；（(") for s in _CHAIN_SPLIT_RE.split(m.group(0))]
        segs = [s for s in segs if 2 <= len(s) <= 140]
        if len(segs) < 3 or sum(1 for s in segs if _stage_like(s)) < 3:
            continue
        if len(segs) > len(best):
            best = segs
    return best


def _stage_like(seg: str) -> bool:
    if not seg:
        return False
    head = seg[0]
    if not (head.isupper() or "\u4e00" <= head <= "\u9fff"):
        return False
    return len(seg.split()) <= 20


def _stage_headings(paper: Paper) -> List[str]:
    """取「方法部分里、连续出现、看起来像阶段名」的标题。"""
    run: List[str] = []
    best: List[str] = []
    for s in paper.sections:
        h = (s.heading or "").strip()
        if _is_stage_heading(h):
            run.append(h)
        else:
            if len(run) > len(best):
                best = run
            run = []
    if len(run) > len(best):
        best = run
    return best if len(best) >= 3 else []


def _is_stage_heading(heading: str) -> bool:
    if not heading or len(heading) > 60:
        return False
    if heading.strip().lower().strip("0123456789. ") in _GENERIC_HEADS:
        return False
    if _GENERIC_HEAD_RE.match(heading.strip()):
        return False
    if re.match(r"^(figure|table|fig\.|tab\.|equation|image)\b", heading, re.I):
        return False
    if re.match(r"^[\d.]+$", heading):       # 纯页码
        return False
    # 全大写的多词标题（如 "RESULTS AND DISCUSSION"）多半是顶层章节，不是方法阶段
    if heading.isupper() and len(heading.split()) >= 2:
        return False
    return True


def _split_connectors(sentence: str) -> List[str]:
    """按连接符把一句话切成步骤片段；片段太长/太短都不算步骤。"""
    s = sentence.strip()
    if not s or len(s) > 600:
        return []
    pat = "|".join(re.escape(c) for c in _CONNECTORS if c.strip())
    parts = [p.strip(" \t,;:、，；") for p in re.split("(?:%s)" % pat, s)]
    parts = [p for p in parts if 2 <= len(p) <= 80]
    return parts


# --------------------------------------------------------------------------- #
# 4. 渲染
# --------------------------------------------------------------------------- #
def render_manual(paper: Paper) -> str:
    """把上面三块内容渲染成一份独立的 Markdown 技术操作说明书。"""
    sources = data_sources(paper)
    tools = tools_mentioned(paper)
    criteria = selection_criteria(paper)
    flow = workflow_steps(paper)

    L: List[str] = []
    a = L.append
    a("# 技术操作说明书：%s" % (paper.title or paper.id))
    a("")
    a("> **怎么来的**：以下内容由 paper2code 从论文正文**确定性抽取**（正则 + 章节结构），"
      "不猜、不编。每条都附出处，便于你回原文核对；抽不到的地方会明确写「未能自动抽取」。")
    a("> **官方链接**是登记值（只登记官方站点），与论文引用不一致时**以论文为准**。")
    a("")

    a("## 一、方法流程（论文给出的执行链路）")
    a("")
    if flow["steps"]:
        a("抽取来源：%s" % flow["source"])
        a("")
        for st in flow["steps"]:
            a("%d. **%s**" % (st["n"], st["name"]))
        a("")
        a("> %s" % flow["note"])
    else:
        a("_%s_" % flow["note"])
    a("")

    a("## 二、数据来源与官方入口")
    a("")
    if sources:
        a("| 资源 | 官方入口 | 正文出处 |")
        a("| --- | --- | --- |")
        for s in sources:
            a("| %s | %s | %s |" % (s["name"], s["url"], _md_cell(s["evidence"])))
    else:
        a("_正文里没有识别到已登记的公开数据资源。_")
    a("")

    a("## 三、关键筛选标准（论文明确写出的）")
    a("")
    if criteria:
        for c in criteria:
            a("### %s" % c["name"])
            a("")
            a("- 论文原句：> %s%s" % (c["quote"], ("（出处：%s）" % c["section"]) if c["section"] else ""))
            a("- 它在筛什么：%s" % c["note"])
            a("- 复现时要核对的量：**%s**" % c["metric"])
            a("")
    else:
        a("_未识别到可解释的筛选标准（论文可能没有显式披露，或用了本模块未登记的说法）。_")
    a("")

    a("## 四、工具链")
    a("")
    if tools:
        a("| 工具 | 用途 | 正文出处 |")
        a("| --- | --- | --- |")
        for t in tools:
            a("| %s | %s | %s |" % (t["name"], t["purpose"], _md_cell(t["evidence"])))
    else:
        a("_正文里没有识别到已登记的软件/方法名。_")
    a("")

    a("## 五、缺口与执行前提")
    a("")
    a("- 上面「方法流程」若来源于小节标题，**只反映作者的组织方式**，不代表完整执行顺序；")
    a("  中间参数（阈值、软件版本、随机种子、硬件）请逐节回原文核对。")
    a("- 数据来源与官方入口只覆盖本模块登记的公开资源；论文自建/私有数据不在此列。")
    a("- 若要真正跑通整条链路，需要按「二」里的入口准备数据、按「三」的门槛筛结构，"
      "并补齐论文未披露的参数——缺哪一项，复现结论就只能限定在「数据自洽」层面。")
    a("")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #
def _scan_text(paper: Paper) -> str:
    """扫描用正文：优先「方法/实验」章节；再按标题名兜底；最后才退到正文段落（**排除参考文献**）。

    实测教训：PDF 解析出的章节 ``kind`` 未必是 ``method``（不同期刊的标题/层级各异），
    直接退回「所有章节拼接」会把参考文献一起扫进来，抽出一堆作者名和期刊缩写当「流程步骤」。
    """
    try:
        sect = paper.section_text("method", "experiment", "方法", "实验") or ""
    except Exception:
        sect = ""
    if sect.strip():
        return sect

    prefixes = ("method", "methods", "materials", "experimental", "methodology",
                "methods and materials", "方法", "材料与方法", "实验")
    picked = [s.text for s in paper.sections
              if (s.heading or "").strip().lower().startswith(prefixes) and (s.text or "").strip()]
    if picked:
        return "\n\n".join(picked)

    return "\n\n".join((s.text or "") for s in paper.body_sections())


def _evidence(paper: Paper, needle: str) -> str:
    """返回 needle 所在的句子（截断），供人工核对。"""
    text = _scan_text(paper)
    idx = text.lower().find(needle.lower())
    if idx < 0:
        return ""
    return _sentence_around(text, idx, idx + len(needle))[:200]


def _sentence_around(text: str, start: int, end: int) -> str:
    left = max(text.rfind(c, 0, start) for c in "。！？.!?\n")
    right_candidates = [text.find(c, end) for c in "。！？.!?\n"]
    right_candidates = [i for i in right_candidates if i != -1]
    right = min(right_candidates) if right_candidates else len(text)
    return text[left + 1:right].strip()[:300]


def _section_of(paper: Paper, quote: str) -> str:
    if not quote:
        return ""
    for s in paper.sections:
        if quote[:40] and quote[:40] in (s.text or ""):
            return s.heading or s.kind
    return ""


def _md_cell(text: str) -> str:
    return (text or "—").replace("|", "/").replace("\n", " ")[:180]


__all__ = ["KNOWN_RESOURCES", "KNOWN_TOOLS", "data_sources", "selection_criteria",
           "tools_mentioned", "workflow_steps", "render_manual"]
