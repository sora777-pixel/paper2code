"""MVP 自动生成层：论文 → 可运行的最小演示（MVP demo）+ 图表绘制说明（figure drawing spec）与绘图代码。

设计原则（与本仓库一致）：
* 核心全部可用标准库运行；没有 LLM / 没有 matplotlib 时自动降级并在产物里写明原因；
* 一切代码在 ``reproduce.runner`` 的受限子进程里执行（超时、无代理）；
* demo 的图一律标注「玩具数据」，绝不把演示数值当作论文结果。
"""

from .service import build_mvp  # noqa: F401

__all__ = ["build_mvp"]
