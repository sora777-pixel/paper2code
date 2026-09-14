"""领域数据模型。

设计原则：全部使用 stdlib ``dataclasses``，保证在最简环境下也能导入，
不引入 pydantic 等额外依赖。所有对象都可 ``to_dict()`` 以便直接序列化为 JSON。
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class _Base:
    """统一的序列化 / 反序列化能力。"""

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)  # type: ignore[arg-type]

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


# --------------------------------------------------------------------------- #
# 论文结构
# --------------------------------------------------------------------------- #
@dataclass
class Section(_Base):
    heading: str
    level: int = 1
    text: str = ""
    # section | abstract | introduction | method | experiment | conclusion |
    # references | appendix | other
    kind: str = "section"

    @property
    def word_count(self) -> int:
        return len(self.text.split())


@dataclass
class Table(_Base):
    """从论文中抽取（或由论文附带）的表格。

    ``rows`` 为纯字符串矩阵，保留论文原始写法；数值化交给
    ``reproduce.numutil`` 处理，避免抽取阶段做有损转换。
    """

    id: str
    caption: str = ""
    header: List[str] = field(default_factory=list)
    rows: List[List[str]] = field(default_factory=list)
    # text（正文 Markdown）| pdf（PDF 表格抽取）| provided（论文附带数据文件）
    source: str = "text"

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    @property
    def n_cols(self) -> int:
        return max((len(r) for r in self.rows), default=len(self.header))


@dataclass
class Figure(_Base):
    id: str
    caption: str = ""
    image_path: Optional[str] = None
    page: Optional[int] = None
    # plot（数据图，可被重绘复现）| architecture（结构图，仅讲解用）| unknown
    kind: str = "unknown"


@dataclass
class Algorithm(_Base):
    """论文中的伪代码 / 算法块。情形 C 的输入。"""

    id: str
    caption: str = ""
    lines: List[str] = field(default_factory=list)
    language: str = "pseudocode"

    @property
    def source(self) -> str:
        return "\n".join(self.lines)


@dataclass
class Formula(_Base):
    """论文中带编号、且正文有文字解释的公式。"""

    id: str
    number: str = ""
    expression: str = ""
    explanation: str = ""


@dataclass
class CodeRef(_Base):
    """论文中出现的代码 / 数据资源链接。情形 A 的判据。"""

    url: str
    # repo（代码仓库）| dataset（数据集）| model（权重）| other
    kind: str = "repo"
    evidence: str = ""


@dataclass
class Paper(_Base):
    id: str
    title: str = ""
    authors: List[str] = field(default_factory=list)
    abstract: str = ""
    source_path: str = ""
    source_kind: str = "md"  # md | txt | html | pdf | arxiv
    sections: List[Section] = field(default_factory=list)
    tables: List[Table] = field(default_factory=list)
    figures: List[Figure] = field(default_factory=list)
    algorithms: List[Algorithm] = field(default_factory=list)
    formulas: List[Formula] = field(default_factory=list)
    code_refs: List[CodeRef] = field(default_factory=list)
    data_refs: List[CodeRef] = field(default_factory=list)
    raw_text: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)
    #: PDF parser backend used for this paper (pymupdf | docling | hybrid)
    pdf_backend: str = "pymupdf"

    # -- 便捷查询 ---------------------------------------------------------- #
    def section_text(self, *kinds: str) -> str:
        want = set(kinds)
        return "\n\n".join(s.text for s in self.sections if s.kind in want)

    def body_sections(self) -> List[Section]:
        skip = {"references", "appendix"}
        return [s for s in self.sections if s.kind not in skip and s.text.strip()]

    def repo_urls(self) -> List[str]:
        return [r.url for r in self.code_refs if r.kind == "repo"]


# --------------------------------------------------------------------------- #
# 讲解材料
# --------------------------------------------------------------------------- #
@dataclass
class Slide(_Base):
    title: str
    bullets: List[str] = field(default_factory=list)
    notes: str = ""
    figure_id: Optional[str] = None
    table_id: Optional[str] = None
    kind: str = "content"  # title | agenda | content | figure | takeaway


@dataclass
class ExplainBundle(_Base):
    paper_id: str
    outline: List[str] = field(default_factory=list)
    slides: List[Slide] = field(default_factory=list)
    courseware_path: Optional[str] = None
    transcript: List[Dict[str, str]] = field(default_factory=list)
    podcast_script_path: Optional[str] = None
    podcast_srt_path: Optional[str] = None
    podcast_audio_path: Optional[str] = None
    generator: str = "offline"
    warnings: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# 复现
# --------------------------------------------------------------------------- #
#: 三种论文情形
MODE_OPEN_SOURCE = "open_source"      # 情形 A：附带开源代码
MODE_PSEUDOCODE = "pseudocode_only"   # 情形 C：仅有一段伪代码
MODE_NO_CODE = "no_code"              # 情形 B：无任何代码


@dataclass
class ReproPlan(_Base):
    paper_id: str
    mode: str = MODE_NO_CODE
    mode_reason: str = ""
    strategy: str = ""
    targets: List[Dict[str, Any]] = field(default_factory=list)
    inputs: Dict[str, Any] = field(default_factory=dict)
    steps: List[str] = field(default_factory=list)
    scale_guard: Dict[str, Any] = field(default_factory=dict)
    scale_notes: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class CellDelta(_Base):
    table_id: str
    row: int
    col: str
    reported: Optional[float]
    reproduced: Optional[float]
    abs_delta: Optional[float]
    rel_delta: Optional[float]
    status: str  # pass | warn | fail | skipped


@dataclass
class ReproResult(_Base):
    paper_id: str
    mode: str = MODE_NO_CODE
    ok: bool = False
    generated: List[str] = field(default_factory=list)
    table_csv: Dict[str, str] = field(default_factory=dict)
    figure_files: List[str] = field(default_factory=list)
    #: 逐项校验/对标结果（``CheckResult.to_dict()`` 形态）
    deltas: List[Dict[str, Any]] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)
    stdout: str = ""
    warnings: List[str] = field(default_factory=list)
    report_path: Optional[str] = None
