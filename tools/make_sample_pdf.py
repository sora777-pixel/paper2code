"""把示例论文的 Markdown 渲染成一份**像真论文**的多页 PDF，用于验证 PyMuPDF 直读链路。

两个要点（都是踩过的坑）：

1. **必须嵌入 Unicode 字体**。PyMuPDF 的 base14 字体（helv/times）只覆盖 Latin-1，
   伪代码里的赋值箭头 ``←``(U+2190) 会被替换成中点 ``·``，翻译出的 Python 直接语法错误。
2. **要去掉 Markdown 痕迹**。真论文 PDF 里没有 ``#`` 标题符、也没有 YAML frontmatter；
   如果直接把 paper.md 原样渲染，解析器会因为读到字面量 ``# Abstract`` 而误判标题，
   测试就失去意义了。

用法：
    python tools/make_sample_pdf.py samples/03_pseudocode/paper.md out.pdf
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import fitz  # PyMuPDF

BODY = 9.5
TITLE = 16.0
META = 10.5
LEADING_RATIO = 1.28
MARGIN = 54.0

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def _pick_font() -> str | None:
    for cand in _FONT_CANDIDATES:
        if Path(cand).exists():
            return cand
    return None


def _depaper(text: str) -> list[tuple[str, float]]:
    """Markdown 源 → 「像论文版面」的 (行文本, 字号) 列表。"""
    front: dict[str, str] = {}
    body = text
    if text.lstrip().startswith("---"):
        stripped = text.lstrip()
        end = stripped.find("\n---", 3)
        if end != -1:
            for ln in stripped[3:end].splitlines():
                if ":" in ln:
                    k, v = ln.split(":", 1)
                    front[k.strip()] = v.strip().strip('"').strip("'")
            body = stripped[end + 4:]

    out: list[tuple[str, float]] = []
    if front.get("title"):
        out += [(front["title"], TITLE), ("", BODY)]
    if front.get("authors"):
        out += [(front["authors"], META), ("", BODY)]

    for ln in body.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            level = len(m.group(1))
            size = {1: 13.0, 2: 11.5}.get(level, 10.5)
            out += [("", BODY), (m.group(2), size), ("", BODY)]
        else:
            out.append((ln, BODY))
    return out


def build(md_path: Path, out_path: Path, fontfile: str | None = None) -> int:
    text = md_path.read_text(encoding="utf-8", errors="replace")
    fontfile = fontfile or _pick_font()
    lines = _depaper(text)

    doc = fitz.open()
    st: dict = {}

    def new_page():
        page = doc.new_page()
        if fontfile:
            page.insert_font(fontname="body", fontfile=fontfile)
            name = "body"
        else:
            name = "helv"
        try:
            metric = fitz.Font(fontfile=fontfile) if fontfile else fitz.Font("helv")
        except Exception:
            metric = fitz.Font("helv")
        st.update(page=page, y=MARGIN, bottom=page.rect.height - MARGIN,
                  width=page.rect.width - 2 * MARGIN, font=name, metric=metric)

    new_page()

    def wrap(s: str, size: float) -> list[str]:
        out: list[str] = []
        while s:
            lo, hi = 1, len(s)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if st["metric"].text_length(s[:mid], fontsize=size) <= st["width"]:
                    lo = mid
                else:
                    hi = mid - 1
            out.append(s[:lo])
            s = s[lo:]
        return out or [""]

    for raw, size in lines:
        for chunk in wrap(raw.rstrip(), size):
            lead = size * LEADING_RATIO
            if st["y"] + lead > st["bottom"]:
                new_page()
            st["page"].insert_text((MARGIN, st["y"]), chunk, fontsize=size, fontname=st["font"])
            st["y"] += lead
        st["y"] += size * LEADING_RATIO * 0.3

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    pages = doc.page_count
    doc.close()
    return pages


def audit(out_path: Path) -> None:
    """回读一遍抽取结果，报告肉眼不可见的码点。

    PyMuPDF 做反向 cmap 映射时可能挑到冷门同形码点（实测 Arial 的
    ``空格→U+00A0``、``-→U+00AD``、``;→U+037E``）。这些字符不可见却会
    破坏下游处理，值得打出来看一眼。paper2code 的
    ``textutil.normalize_unicode`` 会统一清理它们。
    """
    doc = fitz.open(str(out_path))
    text = "\n".join(p.get_text("text") for p in doc)
    doc.close()
    odd: dict[str, int] = {}
    for ch in text:
        if ord(ch) > 126 and ch not in "\n\t":
            odd[f"U+{ord(ch):04X} {ch!r}"] = odd.get(f"U+{ord(ch):04X} {ch!r}", 0) + 1
    top = sorted(odd.items(), key=lambda kv: -kv[1])[:8]
    print("extraction audit:", top if top else "clean ASCII")


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: make_sample_pdf.py <input.md> <output.pdf> [fontfile]")
        return 2
    md_path, out_path = Path(argv[1]), Path(argv[2])
    fontfile = argv[3] if len(argv) > 3 else None
    pages = build(md_path, out_path, fontfile)
    print(f"built {out_path} ({pages} pages, font={fontfile or _pick_font() or 'helv'})")
    audit(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
