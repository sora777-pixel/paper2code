"""论文摄取层：把各种来源的论文变成结构化的 :class:`~paper2code.models.Paper`。"""

from .loader import (KIND_ARXIV, KIND_DIR, KIND_DOI, KIND_EMPTY,  # noqa: F401
                     KIND_FILE, KIND_LABELS, KIND_MISSING, KIND_PDF_URL, KIND_WEB,
                     UPLOAD_EXTS, classify_source, load_paper, paper_id_for_source,
                     paper_id_from_path, parse_doi, save_upload)

__all__ = ["load_paper", "paper_id_from_path", "paper_id_for_source",
           "classify_source", "parse_doi", "save_upload", "KIND_LABELS", "UPLOAD_EXTS",
           "KIND_ARXIV", "KIND_DOI", "KIND_PDF_URL", "KIND_WEB",
           "KIND_DIR", "KIND_FILE", "KIND_MISSING", "KIND_EMPTY"]
