"""Provider 抽象接口。"""

from __future__ import annotations

import abc
import time
from typing import Any, Dict, List, Optional

from ..budget import Budget, BudgetExceeded, Usage
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
        self.usage: Usage = Usage()          # 成本护栏：本 Provider 累计用量（M1-⑥）
        self._budget: Optional[Budget] = None
        self._budget_t0: float = 0.0
        self._budget_tripped: str = ""

    # ------------------------------------------------------------------ #
    def available(self) -> bool:
        return True

    def describe(self) -> str:
        return self.name

    def _warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    # -- 成本护栏（roadmap M1-⑥） ---------------------------------------- #
    def attach_budget(self, budget: Optional[Budget]) -> None:
        """挂上每篇论文的硬上限；计时从这里开始。"""
        self._budget = budget
        self._budget_t0 = time.time()

    def reset_run(self) -> None:
        """开始处理新论文前清空上一轮的记账与降级状态。

        批量处理复用同一个 Provider；若不重置，第 N 篇会继承前 N-1 篇的
        累计用量并误触成本护栏，一旦降级还会让后续所有论文都走离线。
        """
        self.usage = Usage()
        self._budget = None
        self._budget_tripped = ""
        self.warnings = []

    def get_usage(self) -> Usage:
        return self.usage

    def budget_tripped(self) -> str:
        """护栏是否真的触发过（供上层写进 warnings）。"""
        return self._budget_tripped

    def budget_exceeded(self) -> Optional[str]:
        if self._budget is None:
            return None
        return self._budget.exceeded(self.get_usage(), time.time() - self._budget_t0)

    def guard_budget(self, stage: str = "") -> None:
        """在发起调用前检查；超限即抛（并记下触发事实）。"""
        why = self.budget_exceeded()
        if why:
            self._budget_tripped = why
            raise BudgetExceeded("成本护栏触发（stage=%s）：%s" % (stage or "llm", why))

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

    # MVP-PATCH:llm-base
    def complete(self, system: str, user: str, stage: str = "") -> str:
        """自由文本补全（MVP 代码 / 图表说明生成用）。

        默认不提供：离线 Provider 会抛 NotImplementedError，mvp 层据此降级到离线脚手架。
        """
        raise NotImplementedError("该 Provider 不提供自由文本生成（离线模式）")
