"""对 Provider 的薄封装：统一「LLM 不可用」语义，让上层能干净地降级到离线脚手架。"""

from __future__ import annotations

from typing import Any

from .textio import extract_json


class LLMUnavailable(RuntimeError):
    """离线模式 / 无 Key / 远程调用失败时抛出；上层据此降级而不是中断流水线。"""


def ask(provider: Any, stage: str, system: str, user: str) -> str:
    fn = getattr(provider, "complete", None)
    if fn is None:
        raise LLMUnavailable("当前 Provider 不支持 complete()")
    try:
        out = fn(system, user, stage=stage)
    except NotImplementedError as exc:
        raise LLMUnavailable(str(exc) or "离线模式不提供自由文本生成") from exc
    except Exception as exc:  # 网络 / 限流 / 鉴权
        raise LLMUnavailable("%s: %s" % (type(exc).__name__, exc)) from exc
    if not out or not str(out).strip():
        raise LLMUnavailable("模型返回空内容")
    return str(out)


def ask_json(provider: Any, stage: str, system: str, user: str) -> Any:
    raw = ask(provider, stage, system, user)
    try:
        return extract_json(raw)
    except ValueError:
        raw = ask(provider, stage, system,
                  user + "\n\n上一次回复不是合法 JSON。请只回复一个合法的 JSON 对象。")
        try:
            return extract_json(raw)
        except ValueError as exc:
            raise LLMUnavailable("模型两次都没有返回合法 JSON") from exc
