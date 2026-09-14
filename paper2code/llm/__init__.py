"""可插拔大模型层。

对外暴露的能力是**语义化的**（``outline`` / ``slides`` / ``dialogue`` /
``pseudocode_to_python`` …），而不是裸的 ``complete()``。这样：

* ``OfflineProvider`` 可以用规则 + 抽取式算法实现同一套能力，保证零 API Key
  也能跑通全流程；
* ``OpenAIProvider`` 用提示词实现同一套能力，切换到 GPT/DeepSeek/vLLM/Ollama
  只需改 base_url；
* 上层（explain / reproduce）完全不需要知道当前用的是哪种 provider。
"""

from .base import LLMProvider  # noqa: F401
from .router import get_provider, set_provider  # noqa: F401

__all__ = ["LLMProvider", "get_provider", "set_provider"]
