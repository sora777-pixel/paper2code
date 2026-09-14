"""Optional Docling PDF->Markdown backend (subprocess into bakeoff venv).

Docling stays in ``pdf-bakeoff/.venv`` (torch is huge). paper2code never
imports docling; it shells out to that interpreter.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_DOCLING_PYTHON = Path(
    r"C:\Users\lenovo\paper2code-xfer\pdf-bakeoff\.venv\Scripts\python.exe"
)
CONVERT_HELPER = Path(__file__).resolve().parent / "_docling_convert.py"
# Docling 走 torch 子进程，首启很慢甚至可能卡住（曾让 /api/run 假死 15 分钟）。
# Web 上传默认给 90s，超时后 parse_pdf 回退 PyMuPDF，保证仍能生成课件。
try:
    TIMEOUT_SEC = int(os.environ.get("P2C_DOCLING_TIMEOUT", "90") or 90)
except ValueError:
    TIMEOUT_SEC = 90


def resolve_docling_python(python_exe: str | None = None) -> Optional[Path]:
    """Return a usable Docling Python, or None if missing."""
    candidates: list[Path] = []
    if python_exe:
        candidates.append(Path(python_exe))
    env = (os.environ.get("P2C_DOCLING_PYTHON") or "").strip()
    if env:
        candidates.append(Path(env))
    candidates.append(DEFAULT_DOCLING_PYTHON)
    for c in candidates:
        if c and c.is_file():
            return c
    return None


def find_cached_markdown(pdf_path: Path, out_md: Path | None = None) -> Optional[Path]:
    """Reuse prior Docling markdown when available.

    Lookup order:
    1. ``P2C_DOCLING_CACHE`` file, or files under that directory
    2. sibling ``*.docling.md`` / ``paper.docling.md``
    3. explicit ``out_md`` if it already exists
    """
    stem = pdf_path.stem
    candidates: list[Path] = []
    env = (os.environ.get("P2C_DOCLING_CACHE") or "").strip()
    if env:
        p = Path(env)
        if p.is_file():
            candidates.append(p)
        elif p.is_dir():
            candidates.extend(
                [
                    p / f"{stem}.docling.md",
                    p / "paper.docling.md",
                    p / "docling.md",
                ]
            )
    candidates.extend(
        [
            pdf_path.with_name(f"{stem}.docling.md"),
            pdf_path.with_suffix(".docling.md"),
            pdf_path.parent / "paper.docling.md",
        ]
    )
    if out_md is not None:
        candidates.append(out_md)
    seen: set[str] = set()
    for c in candidates:
        key = str(c)
        if key in seen:
            continue
        seen.add(key)
        try:
            if c.is_file() and c.stat().st_size > 100:
                return c
        except OSError:
            continue
    return None


def convert_pdf_to_markdown(
    pdf_path: Path,
    out_md: Path,
    *,
    python_exe: str | None = None,
) -> Path:
    """Convert PDF to Markdown via Docling subprocess (or cache).

    Raises ``RuntimeError`` if the Docling Python is missing / convert fails.
    """
    pdf_path = Path(pdf_path)
    out_md = Path(out_md)

    cached = find_cached_markdown(pdf_path, out_md=out_md)
    if cached is not None:
        if cached.resolve() != out_md.resolve():
            out_md.parent.mkdir(parents=True, exist_ok=True)
            out_md.write_text(cached.read_text(encoding="utf-8"), encoding="utf-8")
            logger.info("Docling cache reused: %s -> %s", cached, out_md)
        else:
            logger.info("Docling cache hit: %s", out_md)
        return out_md

    py = resolve_docling_python(python_exe)
    if py is None:
        raise RuntimeError(
            "Docling Python not found. Set P2C_DOCLING_PYTHON to the bakeoff "
            f"venv python (default: {DEFAULT_DOCLING_PYTHON}). "
            "Docling is NOT installed in the paper2code .venv."
        )
    if not CONVERT_HELPER.is_file():
        raise RuntimeError(f"Docling helper missing: {CONVERT_HELPER}")

    out_md.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(py), str(CONVERT_HELPER), str(pdf_path), str(out_md)]
    logger.info("Docling convert: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SEC,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Docling convert timed out after {TIMEOUT_SEC // 60} min for {pdf_path}"
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"Failed to launch Docling python {py}: {exc}") from exc

    if proc.returncode != 0 or not out_md.is_file() or out_md.stat().st_size < 100:
        err = (proc.stderr or proc.stdout or "").strip()[-2000:]
        raise RuntimeError(
            f"Docling convert failed (exit {proc.returncode}) for {pdf_path}: {err}"
        )
    return out_md


def default_pdf_backend() -> str:
    """PDF 解析后端默认值。

    默认使用零依赖、稳定快速的 ``pymupdf``。``docling`` / ``hybrid`` 会 shell
    到 bakeoff venv 跑 torch，首启慢、可能卡住（曾导致 Web 上传后长时间无响应，
    Docling 子进程超时上限达 15 分钟），因此只在显式设置 ``P2C_PDF_BACKEND``
    （或 CLI ``--pdf-backend``）时才启用，绝不作为默认上传路径。
    """
    env = (os.environ.get("P2C_PDF_BACKEND") or "").strip().lower()
    if env in ("hybrid", "docling", "pymupdf"):
        return env
    return "pymupdf"
