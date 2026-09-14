"""Minimal Docling PDF->Markdown helper.

Runs inside the *bakeoff* venv (has docling installed). Invoked as::

    <docling-python> _docling_convert.py <src.pdf> <dst.md>
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: _docling_convert.py <src.pdf> <dst.md>", file=sys.stderr)
        return 2
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    if not src.exists():
        print(f"PDF not found: {src}", file=sys.stderr)
        return 1
    from docling.document_converter import DocumentConverter

    result = DocumentConverter().convert(str(src))
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(result.document.export_to_markdown(), encoding="utf-8")
    print(f"wrote {dst} ({dst.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
