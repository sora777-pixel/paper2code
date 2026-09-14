"""论文摄取层：把各种来源的论文变成结构化的 :class:`~paper2code.models.Paper`。"""

from .loader import load_paper, paper_id_from_path, sanitize_paper_id  # noqa: F401

__all__ = ["load_paper", "paper_id_from_path", "sanitize_paper_id"]
