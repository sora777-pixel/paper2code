"""paper2code —— 论文学习工具（讲解材料 + 复现结果）。

模块总览::

    paper2code/
      ingest/      论文摄取：PDF/Markdown/HTML/arXiv  ->  结构化 Paper 对象
      llm/         可插拔大模型层（OpenAI 兼容 / 离线规则降级）
      explain/     讲解材料：课件（HTML 幻灯片）+ 知识播客（脚本/字幕/音频）
      reproduce/   复现结果：三种代码情形策略 + 表格图表重现 + 数值比对
      tts/         语音合成适配层（edge-tts / 占位降级）
      pipeline.py  端到端编排
      cli.py       命令行入口
      api.py       本地 Web 服务入口
"""

__version__ = "0.1.0"

from .models import (  # noqa: F401
    Algorithm,
    CodeRef,
    Figure,
    Paper,
    Section,
    Table,
)

__all__ = [
    "__version__",
    "Paper",
    "Section",
    "Table",
    "Figure",
    "Algorithm",
    "CodeRef",
]
