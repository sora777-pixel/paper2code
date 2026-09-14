"""讲解材料生成：课件（HTML 幻灯片）+ 知识播客。"""

from .courseware import render_courseware  # noqa: F401
from .podcast import build_podcast  # noqa: F401

__all__ = ["render_courseware", "build_podcast"]
