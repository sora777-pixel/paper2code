"""arXiv 抓取：优先 HTML（ar5iv / arxiv HTML），退回 PDF。

仅使用 stdlib ``urllib``，避免额外依赖；``requests`` 存在时优先使用。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Tuple

_ATOM = "{http://www.w3.org/2005/Atom}"

UA = "paper2code/0.1 (local research tool)"


def _get(url: str, timeout: int = 60) -> bytes:
    try:
        import requests  # type: ignore

        resp = requests.get(url, timeout=timeout, headers={"User-Agent": UA})
        resp.raise_for_status()
        return resp.content
    except Exception:
        import urllib.request

        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as fh:  # noqa: S310
            return fh.read()


def normalize_arxiv_id(raw: str) -> str:
    """从 URL 或裸 ID 中提取 arXiv 编号。"""
    raw = raw.strip()
    m = re.search(r"arxiv\.org/(?:abs|pdf|html)/([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", raw)
    if m:
        return m.group(1)
    m = re.search(r"([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", raw)
    if m:
        return m.group(1)
    # 旧式编号 cs.CL/0102001
    m = re.search(r"([a-z\-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)", raw)
    return m.group(1) if m else raw


def fetch_metadata(arxiv_id: str, timeout: int = 60) -> dict:
    """通过 arXiv API 取标题 / 作者 / 摘要。"""
    url = f"http://export.arxiv.org/api/query?id_list={arxiv_id}&max_results=1"
    try:
        xml = _get(url, timeout=timeout).decode("utf-8", "ignore")
        root = ET.fromstring(xml)
        entry = root.find(f"{_ATOM}entry")
        if entry is None:
            return {}
        title = (entry.findtext(f"{_ATOM}title") or "").strip()
        summary = (entry.findtext(f"{_ATOM}summary") or "").strip()
        authors = [
            (a.findtext(f"{_ATOM}name") or "").strip()
            for a in entry.findall(f"{_ATOM}author")
        ]
        return {"title": re.sub(r"\s+", " ", title), "abstract": summary, "authors": authors}
    except Exception:
        return {}


def fetch_html(arxiv_id: str, timeout: int = 60) -> Optional[str]:
    """尝试三条 HTML 源，返回正文 HTML 字符串。"""
    for tmpl in (
        "https://ar5iv.labs.arxiv.org/html/{id}",
        "https://arxiv.org/html/{id}",
        "https://www.arxiv-vanity.com/papers/{id}/",
    ):
        try:
            html = _get(tmpl.format(id=arxiv_id), timeout=timeout).decode("utf-8", "ignore")
            if len(html) > 5000 and ("<h1" in html or "<section" in html):
                return html
        except Exception:
            continue
    return None


def fetch_pdf(arxiv_id: str, dest_dir: Path, timeout: int = 120) -> Optional[Path]:
    try:
        data = _get(f"https://arxiv.org/pdf/{arxiv_id}", timeout=timeout)
    except Exception:
        return None
    if not data.startswith(b"%PDF"):
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"{arxiv_id.replace('/', '_')}.pdf"
    out.write_bytes(data)
    return out


def resolve_arxiv(raw: str, cache_dir: Path) -> Tuple[Optional[str], Optional[Path], dict]:
    """返回 ``(html, pdf_path, metadata)``，三者尽可能填充。"""
    aid = normalize_arxiv_id(raw)
    meta = fetch_metadata(aid)
    html = fetch_html(aid)
    pdf = None
    if html is None:
        pdf = fetch_pdf(aid, cache_dir)
    return html, pdf, meta
