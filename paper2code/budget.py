"""成本护栏（roadmap M1-⑥）：每篇论文的 token / 调用次数 / 耗时硬上限。

为什么需要：接上真实端点后，一篇论文的 LLM 调用是**有成本且可能失控**的
（计划 + 自检 + 修复轮 + 图表说明 + 绘图脚本，动辄十几到几十次）。
本模块给出「上限 + 记账 + 超限即降级」三件套。

设计取舍（照本项目的口径）：

1. **记账是全局的，拦截在 `complete()`**：所有 LLM 调用都会计入用量
   （讲解/复现阶段也会算进同一份论文预算），但**只在 MVP 的自由文本补全处拦截**，
   因为路线图要求"超限降级为离线脚手架"——那正是 MVP 的降级路径。
   非 MVP 的 LLM 路径不会被中途打断（已如实写进边界说明）。
2. **超限不静默**：触发时抛 `BudgetExceeded`，并把这个事实记在 provider 上，
   由 MVP 写进产物 `warnings` 与报告。
3. 上限为 `0` 表示**不限制**（离线/自建环境不该被误伤）。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


class BudgetExceeded(RuntimeError):
    """成本护栏触发；上层据此降级为离线脚手架（而不是把整条流水线打断）。"""


@dataclass
class Usage:
    """一篇论文累计的 LLM 用量。"""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def record(self, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        p = max(0, int(prompt_tokens or 0))
        c = max(0, int(completion_tokens or 0))
        self.calls += 1
        self.prompt_tokens += p
        self.completion_tokens += c
        self.total_tokens += p + c

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Budget:
    """每篇论文的硬上限；`0` = 不限制。"""

    max_calls: int = 0
    max_tokens: int = 0
    max_seconds: float = 0.0

    def exceeded(self, usage: Usage, elapsed_s: float) -> Optional[str]:
        """**发起下一次调用前**的检查；返回原因（None = 未超限）。"""
        if self.max_calls and usage.calls + 1 > self.max_calls:
            return "调用次数将超过上限（已用 %d 次，上限 %d）" % (usage.calls, self.max_calls)
        if self.max_tokens and usage.total_tokens >= self.max_tokens:
            return "token 已达上限（已用 %d，上限 %d）" % (usage.total_tokens, self.max_tokens)
        if self.max_seconds and elapsed_s >= self.max_seconds:
            return "耗时已达上限（已用 %.0fs，上限 %.0fs）" % (elapsed_s, self.max_seconds)
        return None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BudgetReport:
    """护栏的最终结论（进 manifest / mvp_result / 报告）。"""

    limit: Dict[str, Any] = field(default_factory=dict)
    usage: Dict[str, Any] = field(default_factory=dict)
    tripped: str = ""            # 非空 = 确实触发过
    elapsed_s: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.tripped

    def to_dict(self) -> Dict[str, Any]:
        return {"limit": self.limit, "usage": self.usage, "tripped": self.tripped,
                "elapsed_s": round(self.elapsed_s, 2), "ok": self.ok}
