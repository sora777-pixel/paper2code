"""Provider 路由与降级。

规则：
1. ``llm_mode == "openai"`` 且存在 api_key → 用 ``OpenAIProvider``；
2. 否则用 ``OfflineProvider``；
3. 运行中若远程调用连续失败，自动降级（由 ``FallbackProvider`` 包装）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..config import get_settings
from ..models import Algorithm, Paper, Slide, Table
from .base import LLMProvider
from .offline import OfflineProvider
from .openai_compat import OpenAIProvider

_PROVIDER: Optional[LLMProvider] = None


class FallbackProvider(LLMProvider):
    """先试远程，失败即降级到离线，并把降级原因暴露给上层。"""

    name = "auto"

    def __init__(self, primary: LLMProvider, fallback: Optional[LLMProvider] = None) -> None:
        super().__init__()
        self.primary = primary
        self.fallback = fallback or OfflineProvider()
        self.degraded = False

    def available(self) -> bool:
        return self.primary.available() or self.fallback.available()

    def describe(self) -> str:
        state = "degraded->offline" if self.degraded else "remote"
        return f"auto({self.primary.describe()}, {state})"

    # -- 成本护栏：用量与触发事实都从 primary 取（真正花钱的是它） -------- #
    def attach_budget(self, budget) -> None:
        super().attach_budget(budget)
        if hasattr(self.primary, "attach_budget"):
            self.primary.attach_budget(budget)

    def reset_run(self) -> None:
        super().reset_run()
        self.degraded = False
        if hasattr(self.primary, "reset_run"):
            self.primary.reset_run()
        if hasattr(self.fallback, "reset_run"):
            self.fallback.reset_run()

    def get_usage(self):
        return self.primary.get_usage() if hasattr(self.primary, "get_usage") else super().get_usage()

    def budget_tripped(self) -> str:
        if hasattr(self.primary, "budget_tripped"):
            got = self.primary.budget_tripped()
            if got:
                return got
        return super().budget_tripped()

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        if self.degraded or not self.primary.available():
            return getattr(self.fallback, method)(*args, **kwargs)
        try:
            out = getattr(self.primary, method)(*args, **kwargs)
            self.warnings.extend(self.primary.warnings)
            return out
        except Exception as exc:
            self.degraded = True
            self._warn(f"远程模型调用失败，已自动降级为离线模式：{exc}")
            self.warnings.extend(self.primary.warnings)
            return getattr(self.fallback, method)(*args, **kwargs)

    # -- 能力转发 -------------------------------------------------------- #
    def outline(self, paper: Paper, target_slides: int = 12) -> List[str]:
        return self._call("outline", paper, target_slides)

    def slides(self, paper: Paper, outline: List[str]) -> List[Slide]:
        return self._call("slides", paper, outline)

    def dialogue(self, paper: Paper) -> List[Dict[str, str]]:
        return self._call("dialogue", paper)

    def pseudocode_to_python(self, algo: Algorithm, context: str = "") -> str:
        return self._call("pseudocode_to_python", algo, context)

    def repro_strategy(self, paper: Paper, mode: str, targets: List[Dict[str, Any]]) -> str:
        return self._call("repro_strategy", paper, mode, targets)

    def explain_table(self, table: Table) -> str:
        return self._call("explain_table", table)

    # MVP-PATCH:llm-router
    def complete(self, system: str, user: str, stage: str = "") -> str:
        return self._call("complete", system, user, stage)


def get_provider(refresh: bool = False) -> LLMProvider:
    global _PROVIDER
    if _PROVIDER is not None and not refresh:
        return _PROVIDER

    cfg = get_settings().llm_config()
    offline = OfflineProvider(cfg)
    if cfg.get("mode") == "openai" and cfg.get("api_key"):
        _PROVIDER = FallbackProvider(OpenAIProvider(cfg), offline)
    else:
        _PROVIDER = offline
    return _PROVIDER


def set_provider(provider: LLMProvider) -> None:
    global _PROVIDER
    _PROVIDER = provider
