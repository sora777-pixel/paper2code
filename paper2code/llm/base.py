"""Provider 抽象接口。"""

from __future__ import annotations

import abc
from typing import Any, Dict, List, Optional

from ..models import Algorithm, Paper, Slide, Table


class LLMProvider(abc.ABC):
    """大模型能力接口。

    所有方法都必须是「可降级」的：即使模型不可用，也应返回一个合理的
    结构化结果，而不是抛异常。上层会记录 ``warnings``。
    """

    name: str = "base"

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config or {}
        self.warnings: List[str] = []

    # ------------------------------------------------------------------ #
    def available(self) -> bool:
        return True

    def describe(self) -> str:
        return self.name

    def _warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    # -- 讲解材料 -------------------------------------------------------- #
    @abc.abstractmethod
    def outline(self, paper: Paper, target_slides: int = 12) -> List[str]:
        """产出面向「讲解」的章节大纲（不是论文原章节的照抄）。"""

    @abc.abstractmethod
    def slides(self, paper: Paper, outline: List[str]) -> List[Slide]:
        """按大纲生成幻灯片内容。"""

    @abc.abstractmethod
    def dialogue(self, paper: Paper) -> List[Dict[str, str]]:
        """生成双人知识播客对白，元素形如 ``{"speaker": "A", "text": "..."}``。"""

    # -- 复现 ------------------------------------------------------------ #
    @abc.abstractmethod
    def pseudocode_to_python(self, algo: Algorithm, context: str = "") -> str:
        """把论文中的伪代码翻译为可执行的 Python（情形 C）。"""

    @abc.abstractmethod
    def repro_strategy(self, paper: Paper, mode: str, targets: List[Dict[str, Any]]) -> str:
        """针对三种情形给出复现策略说明（供报告使用）。"""

    def explain_table(self, table: Table) -> str:
        """对单张表格做一句话解读（可选能力）。"""
        return ""
