"""统一入口：把文件 / 目录 / URL 加载为 :class:`Paper`。

支持的输入形态
--------------
1. 单个文件：``.md`` / ``.txt`` / ``.html`` / ``.htm`` / ``.pdf``
2. **论文包目录**（推荐用于演示与复现）：目录内放 ``paper.md``（或 pdf/html），
   可选 ``data/*.csv``（论文附带的数据）、可选 ``code/``（附带的代码仓库副本）。
   这一约定把「论文正文」「论文附带数据」「开源代码」三者解耦，正好对应
   需求里的三种论文情形。
3. arXiv：``https://arxiv.org/abs/1706.03762`` 或裸编号 ``1706.03762``
"""

from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import get_settings
from ..models import Paper, Table
from . import arxiv as arxiv_mod
from . import mdparse, pdfparse

BUNDLE_PAPER_NAMES = ("paper.md", "paper.markdown", "paper.html", "paper.htm", "paper.pdf", "paper.txt")
BUNDLE_DATA_DIR = ("data", "datasets", "tables")
BUNDLE_CODE_DIR = ("code", "repo", "source")

# Windows MAX_PATH is 260. incoming/<id>/paper.pdf must stay well under that.
PAPER_ID_MAX = 72
_ID_SUFFIXES = {".pdf", ".md", ".html", ".htm", ".txt", ".zip", ".markdown"}


def sanitize_paper_id(name: str, max_len: int = PAPER_ID_MAX) -> str:
    """Filesystem-safe paper_id from a filename or folder name.

    Long Nature-style titles are truncated and given a short hash so
    ``incoming/<id>/paper.pdf`` stays valid on Windows.
    """
    raw = Path(name or "paper").name
    p = Path(raw)
    base = p.stem if p.suffix.lower() in _ID_SUFFIXES else raw
    base = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", base, flags=re.UNICODE).strip("._")
    base = base or "paper"
    if len(base) <= max_len:
        return base
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:8]
    keep = max(8, max_len - 9)
    return f"{base[:keep].rstrip('._-')}_{digest}"


def paper_id_from_path(path: Path) -> str:
    """Filesystem-safe id from a path stem; keep CJK / unicode word chars.

    Must stay aligned with `api._sanitize_paper_id` so upload paper_id
    (incoming/<id>/) matches run outputs (<outputs>/<id>/). The old
    ASCII-only regex turned Chinese stems into empty → "paper", so the
    dashboard card pointed at the upload id while artifacts landed under
    outputs/paper/ and the detail page showed 尚未生成 for everything.
    """
    return sanitize_paper_id(path.name)


# --------------------------------------------------------------------------- #
# 论文包
# --------------------------------------------------------------------------- #
def _read_csv_table(path: Path, tid: str) -> Optional[Table]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            rows = [r for r in csv.reader(fh) if any(str(c).strip() for c in r)]
    except Exception:
        try:
            with path.open("r", encoding="gbk", newline="") as fh:
                rows = [r for r in csv.reader(fh) if any(str(c).strip() for c in r)]
        except Exception:
            return None
    if not rows:
        return None
    return Table(id=tid, caption=f"provided data: {path.name}", header=rows[0], rows=rows[1:], source="provided")


def load_bundle(directory: Path) -> Paper:
    """加载「论文包目录」。"""
    paper_path: Optional[Path] = None
    for name in BUNDLE_PAPER_NAMES:
        cand = directory / name
        if cand.exists():
            paper_path = cand
            break

    if paper_path is None:
        # 退而求其次：目录下第一个 pdf/md/html
        for ext in ("*.pdf", "*.md", "*.html", "*.htm", "*.txt"):
            found = sorted(directory.glob(ext))
            if found:
                paper_path = found[0]
                break

    if paper_path is None:
        paper = Paper(id=directory.name or paper_id_from_path(directory), title=directory.name, source_path=str(directory))
        paper.meta["warnings"] = ["论文包目录中未找到 paper.md / paper.pdf / paper.html"]
    else:
        paper = load_paper(paper_path, bundle_dir=directory)
        # 论文包以「目录名」作为标识，避免同一目录下的 paper.md / paper.pdf 互相覆盖
        paper.id = directory.name or paper_id_from_path(directory)

    # -- 附带数据 --------------------------------------------------------- #
    provided: List[str] = []
    for sub in BUNDLE_DATA_DIR:
        d = directory / sub
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.csv")):
            t = _read_csv_table(f, f"P{len(paper.tables) + 1}")
            if t is not None:
                provided.append(str(f.relative_to(directory)))
                paper.tables.append(t)
    paper.meta.setdefault("bundle_dir", str(directory))
    paper.meta.setdefault("provided_data", provided)

    # -- 附带代码 --------------------------------------------------------- #
    for sub in BUNDLE_CODE_DIR:
        d = directory / sub
        if d.is_dir():
            paper.meta["local_code_path"] = str(d)
            break
    return paper


# --------------------------------------------------------------------------- #
# 主分发
# --------------------------------------------------------------------------- #
def load_paper(
    source: str | Path,
    bundle_dir: Optional[Path] = None,
    cache_dir: Optional[Path] = None,
) -> Paper:
    settings = get_settings()
    cache_dir = Path(cache_dir or settings.work_dir / "cache")
    cache_dir.mkdir(parents=True, exist_ok=True)

    raw = str(source)

    # -- arXiv ------------------------------------------------------------ #
    if isinstance(source, str) and ("arxiv.org" in raw or re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", raw.strip())):
        return _load_arxiv(raw, cache_dir)

    # -- 网络 URL（非 arXiv）----------------------------------------------- #
    if isinstance(source, str) and re.match(r"^https?://", raw):
        return _load_url(raw, cache_dir)

    path = Path(source)

    if path.is_dir():
        return load_bundle(path)

    if not path.exists():
        raise FileNotFoundError(f"找不到输入：{source}")

    pid = paper_id_from_path(path)
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        paper = pdfparse.parse_pdf(path, pid)
    elif suffix in (".html", ".htm"):
        paper = mdparse.parse_html(path.read_text(encoding="utf-8", errors="ignore"), pid, str(path))
    elif suffix in (".md", ".markdown", ".txt", ".tex"):
        paper = mdparse.parse_markdown(path.read_text(encoding="utf-8", errors="ignore"), pid, str(path))
    else:
        # 未知后缀：按文本处理
        paper = mdparse.parse_markdown(path.read_text(encoding="utf-8", errors="ignore"), pid, str(path))

    if bundle_dir:
        paper.meta.setdefault("bundle_dir", str(bundle_dir))
    return paper


def _load_arxiv(raw: str, cache_dir: Path) -> Paper:
    aid = arxiv_mod.normalize_arxiv_id(raw)
    html, pdf_path, meta = arxiv_mod.resolve_arxiv(raw, cache_dir)
    pid = "arxiv-" + aid.replace("/", "-")

    if html:
        paper = mdparse.parse_html(html, pid)
        paper.source_kind = "arxiv"
        paper.source_path = f"https://arxiv.org/abs/{aid}"
    elif pdf_path:
        paper = pdfparse.parse_pdf(pdf_path, pid)
        paper.source_kind = "arxiv"
        paper.source_path = str(pdf_path)
    else:
        paper = Paper(id=pid, source_path=f"https://arxiv.org/abs/{aid}", source_kind="arxiv")
        paper.meta["warnings"] = ["arXiv 抓取失败（网络受限？），仅保留元数据"]

    if meta.get("title"):
        paper.title = meta["title"]
    if meta.get("abstract"):
        paper.abstract = re.sub(r"\s+", " ", meta["abstract"]).strip()
    if meta.get("authors"):
        paper.authors = meta["authors"]
    paper.meta["arxiv_id"] = aid
    return paper


def _load_url(url: str, cache_dir: Path) -> Paper:
    import re as _re

    try:
        data = arxiv_mod._get(url)  # noqa: SLF001 - 同包内复用
    except Exception as exc:
        raise RuntimeError(f"抓取 URL 失败：{url}（{exc}）") from exc

    pid = "web-" + _re.sub(r"[^0-9A-Za-z]+", "-", url.split("//")[-1])[:60]
    if url.lower().endswith(".pdf") or data[:4] == b"%PDF":
        cache_dir.mkdir(parents=True, exist_ok=True)
        p = cache_dir / f"{pid}.pdf"
        p.write_bytes(data)
        return pdfparse.parse_pdf(p, pid)
    return mdparse.parse_html(data.decode("utf-8", "ignore"), pid, source_path=url)
