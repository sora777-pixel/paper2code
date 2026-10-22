"""任务级控制：取消与超时（roadmap M5-①）。

**为什么要有它**：Web 端提交的任务可能跑很久（LLM 调用 + 执行生成的代码），用户必须能**取消**，
系统必须在**超时**后收尾——否则就是「卡死投诉」的来源（M5 验收要求「崩溃/卡死投诉归零」）。

设计要点：

* 控制对象随任务走，一个任务一份；
* :meth:`Control.check` 在**阶段边界**抛 :class:`RunCancelled`（流水线调用）；
* :func:`paper2code.reproduce.runner.run_python_script` 内部读**当前线程的控制对象**，
  长子进程会被轮询、取消/超时时杀掉**整个进程树**——因此不必改所有执行入口的签名；
* 用 thread-local 而不是进程全局：队列 worker 是一任务一线程，互不干扰；CLI 单线程跑时没有控制对象，
  行为与以前完全一致。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

#: 轮询粒度：子进程等待循环多久检查一次「取消 / 超时」
POLL_INTERVAL_S = 0.25


class RunCancelled(Exception):
    """任务被用户取消，或超过任务级超时被强制收尾。"""

    def __init__(self, reason: str, timed_out: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.timed_out = bool(timed_out)


@dataclass
class Control:
    """一个任务的取消/超时控制。

    ``is_cancelled`` 由上层注入（队列任务=查数据库的取消标记；测试=任意回调）。
    """

    is_cancelled: Callable[[], bool] = lambda: False
    timeout_s: float = 0.0                       # 0 或负数 = 不限制
    started_at: float = field(default_factory=time.time)
    label: str = ""

    # -- 判定 --------------------------------------------------------------- #
    def remaining(self) -> float:
        if self.timeout_s <= 0:
            return float("inf")
        return self.timeout_s - (time.time() - self.started_at)

    def expired(self) -> bool:
        return self.remaining() <= 0

    def stopped_reason(self) -> str:
        """返回停止原因（空 = 应继续）。取消优先于超时。"""
        if self.is_cancelled():
            return "任务已被用户取消"
        if self.expired():
            return "任务超时（超过 %ss 上限）" % _fmt(self.timeout_s)
        return ""

    def check(self) -> None:
        """:raises RunCancelled: 已取消或已超时。"""
        reason = self.stopped_reason()
        if reason:
            raise RunCancelled(reason, timed_out=reason.startswith("任务超时"))


def _fmt(seconds: float) -> str:
    return "%g" % seconds


# --------------------------------------------------------------------------- #
# 当前线程的控制对象
# --------------------------------------------------------------------------- #
_local = threading.local()


def set_current(control: Optional[Control]) -> None:
    _local.control = control


def get_current() -> Optional[Control]:
    return getattr(_local, "control", None)


def clear_current() -> None:
    _local.control = None


def check() -> None:
    """阶段边界调用：已取消/已超时就抛出（没有控制对象时是空操作）。"""
    c = get_current()
    if c is not None:
        c.check()


def nothing() -> Control:
    """无限制的控制对象（用于测试与显式占位）。"""
    return Control()
