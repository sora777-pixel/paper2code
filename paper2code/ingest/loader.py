"""统一入口：把文件 / 目录 / URL / 上传字节 变成 :class:`Paper`。

支持的输入形态
--------------
1. 单个文件：``.md`` / ``.txt`` / ``.html`` / ``.htm`` / ``.pdf``
2. **论文包目录**（推荐用于演示与复现）：目录内放 ``paper.md``（或 pdf/html），
   可选 ``data/*.csv``（论文附带的数据）、可选 ``code/``（附带的代码仓库副本）。
   这一约定把「论文正文」「论文附带数据」「开源代码」三者解耦，正好对应
   需求里的三种论文情形。
3. arXiv：``https://arxiv.org/abs/1706.03762`` 或裸编号 ``1706.03762``
4. **DOI**：``https://doi.org/10.1021/acs.jcim.4c01641`` 或裸 DOI ``10.1021/acs.jcim.4c01641``
5. 任意 http(s) 网页 / 直链 PDF

``classify_source`` 与 ``paper_id_for_source`` 把「来源判定」「paper_id 命名」收敛到一处：
Web 端与队列**必须**用它们推算 id，否则前端轮询的 id 会和真实产物目录对不上
（arXiv / URL 尤其明显：``1706.03762`` 曾推成 ``1706``，实际产物落在 ``arxiv-1706.03762``）。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import get_settings
from ..models import Paper, Table
from . import arxiv as arxiv_mod
from . import mdparse, pdfparse, quality

#: 上传允许的类型（其余一律拒绝，避免把任意文件写进工作区）
UPLOAD_EXTS = (".pdf", ".md", ".markdown", ".html", ".htm", ".txt", ".tex", ".zip")
MAX_UPLOAD_BYTES = 200 * 1024 * 1024     # 单个上传上限 200MB
MAX_ZIP_ENTRIES = 2000

#: Windows 默认 MAX_PATH=260；留出余量后按这个长度压目录名 / 文件名。
#: 论文标题常常整句当文件名，``incoming/<pid>/<原名>.pdf`` 很容易越界——
#: 越界时 Windows 抛的是 ``FileNotFoundError [Errno 2]``（不是「名字太长」），
#: 于是 Web 端只看到 HTTP 500。这里主动截断 + 名字哈希，保证唯一且可复现。
MAX_PATH_CHARS = 240
MAX_PID_CHARS = 64

BUNDLE_PAPER_NAMES = ("paper.md", "paper.markdown", "paper.html", "paper.htm", "paper.pdf", "paper.txt")
BUNDLE_DATA_DIR = ("data", "datasets", "tables")
BUNDLE_CODE_DIR = ("code", "repo", "source")


def paper_id_from_path(path: Path) -> str:
    p = Path(path)
    # 目录名的点不是扩展名：``incoming/Disruption-of-Hsp70.14-BAG2-x/`` 若按
    # ``Path.stem`` 处理会被截成 ``Disruption-of-Hsp70``，与上传时返回的 paper_id
    # 对不上（Web 端轮询的进度文件就找不到了）。目录一律用完整名字，文件才去后缀。
    name = p.name if p.is_dir() else p.stem
    stem = re.sub(r"[^0-9A-Za-z_.-]+", "-", name).strip("-")
    return stem or "paper"


# --------------------------------------------------------------------------- #
# 来源判定（Web 端 / 队列 / loader 共用同一套规则）
# --------------------------------------------------------------------------- #
_DOI_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?(10\.\d{4,9}/[^\s]+)$", re.I)
_ARXIV_BARE_RE = re.compile(r"\d{4}\.\d{4,5}(?:v\d+)?")
_ARXIV_OLD_RE = re.compile(r"[a-z\-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?")

KIND_ARXIV, KIND_DOI, KIND_PDF_URL, KIND_WEB = "arxiv", "doi", "pdf_url", "web"
KIND_DIR, KIND_FILE, KIND_MISSING, KIND_EMPTY = "dir", "file", "path_missing", "empty"

KIND_LABELS = {
    KIND_ARXIV: "arXiv 编号", KIND_DOI: "DOI 链接", KIND_PDF_URL: "PDF 直链",
    KIND_WEB: "网页链接", KIND_DIR: "本机论文包目录", KIND_FILE: "本机文件",
    KIND_MISSING: "本机路径（不存在）", KIND_EMPTY: "空",
}


def parse_doi(raw: str) -> str:
    """从 URL / 裸串里提取 DOI；不是 DOI 就返回空串。"""
    m = _DOI_RE.match(str(raw).strip())
    return m.group(1).rstrip(".") if m else ""


def classify_source(source: str) -> str:
    """判定输入形态。判定顺序必须与 :func:`load_paper` 的分发顺序一致。"""
    raw = str(source or "").strip()
    if not raw:
        return KIND_EMPTY
    if "arxiv.org" in raw or _ARXIV_BARE_RE.fullmatch(raw) or _ARXIV_OLD_RE.fullmatch(raw):
        return KIND_ARXIV
    if parse_doi(raw):
        return KIND_DOI
    if re.match(r"^https?://", raw, re.I):
        return KIND_PDF_URL if raw.split("?")[0].lower().endswith(".pdf") else KIND_WEB
    path = Path(raw)
    if path.is_dir():
        return KIND_DIR
    return KIND_FILE if path.exists() else KIND_MISSING


def paper_id_for_source(source: str | Path) -> str:
    """按「与 loader 完全一致」的规则推算 paper_id（**不联网、不落盘**）。"""
    raw = str(source).strip()
    kind = classify_source(raw)
    if kind == KIND_ARXIV:
        return "arxiv-" + arxiv_mod.normalize_arxiv_id(raw).replace("/", "-")
    if kind == KIND_DOI:
        return "doi-" + re.sub(r"[^0-9A-Za-z]+", "-", parse_doi(raw))[:60].strip("-")
    if kind in (KIND_PDF_URL, KIND_WEB):
        return "web-" + re.sub(r"[^0-9A-Za-z]+", "-", raw.split("//")[-1])[:60]
    return paper_id_from_path(Path(raw))


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
        paper = Paper(id=paper_id_from_path(directory), title=directory.name, source_path=str(directory))
        paper.meta["warnings"] = ["论文包目录中未找到 paper.md / paper.pdf / paper.html"]
    else:
        paper = load_paper(paper_path, bundle_dir=directory)
        # 论文包以「目录名」作为标识，避免同一目录下的 paper.md / paper.pdf 互相覆盖
        paper.id = paper_id_from_path(directory)

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
    # 附带数据表补进来之后再打分（否则 provided 表不计入）
    quality.apply_quality(paper)
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

    # -- 网络来源：arXiv / DOI / 直链 PDF / 网页（判定顺序与 classify_source 一致） -- #
    if isinstance(source, str):
        kind = classify_source(raw)
        if kind == KIND_EMPTY:
            raise ValueError("来源为空：请给出论文路径、链接或 arXiv / DOI 编号")
        if kind == KIND_ARXIV:
            return _load_arxiv(raw, cache_dir)
        if kind == KIND_DOI:
            return _load_doi(raw, cache_dir)
        if kind in (KIND_PDF_URL, KIND_WEB):
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
    quality.apply_quality(paper)
    return paper


def _load_arxiv(raw: str, cache_dir: Path) -> Paper:
    aid = arxiv_mod.normalize_arxiv_id(raw)
    html, pdf_path, meta = arxiv_mod.resolve_arxiv(raw, cache_dir)
    pid = paper_id_for_source(raw)

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
    try:
        data = arxiv_mod._get(url)  # noqa: SLF001 - 同包内复用
    except Exception as exc:
        raise RuntimeError(f"抓取 URL 失败：{url}（{exc}）") from exc

    pid = paper_id_for_source(url)
    if url.lower().endswith(".pdf") or data[:4] == b"%PDF":
        cache_dir.mkdir(parents=True, exist_ok=True)
        p = cache_dir / f"{pid}.pdf"
        p.write_bytes(data)
        return pdfparse.parse_pdf(p, pid)
    return mdparse.parse_html(data.decode("utf-8", "ignore"), pid, source_path=url)


def _load_doi(raw: str, cache_dir: Path) -> Paper:
    """DOI：尽力解析成正文，拿不到就**明确报因**，绝不假装成功。

    顺序：① doi.org 内容协商要 PDF（开放出版商 / 仓储普遍支持）；
    ② Crossref 元数据（标题·作者·摘要），若带 OA 的 PDF 链接再试一次；
    ③ 落地页 HTML（只在正文足够长、确实是全文页时才采用）。

    三条路都失败时抛 :class:`RuntimeError`，把「被墙 / 需订阅 / 返回非 PDF」如实写进消息——
    任务会落到 ``failed``，队列面板能直接看到并复制这段原因。
    """
    from urllib.parse import quote

    doi = parse_doi(raw)
    pid = paper_id_for_source(raw)
    canonical = "https://doi.org/" + doi
    tried: List[str] = []

    # ① 内容协商
    resp = None
    try:
        resp = arxiv_mod.http_get(canonical, accept="application/pdf")
    except Exception as exc:
        tried.append("doi.org 内容协商失败（%s）" % type(exc).__name__)
    if resp is not None and resp.body[:4] == b"%PDF":
        return _doi_paper_from_pdf(resp.body, pid, doi, canonical, cache_dir)

    land_html = ""
    if resp is not None:
        ctype = (resp.content_type or "").split(";")[0].strip().lower()
        if "html" in ctype or resp.body[:4096].lstrip()[:6].lower().startswith(b"<html"):
            land_html = resp.body.decode("utf-8", "ignore")
        tried.append("doi.org 返回 %s / HTTP %s，不是 PDF" % (ctype or "未知类型", resp.status))

    # ② Crossref 元数据 + 可能的 OA PDF
    meta: Dict[str, Any] = {}
    try:
        cj = arxiv_mod.http_get("https://api.crossref.org/works/" + quote(doi, safe=""))
        meta = (json.loads(cj.body.decode("utf-8", "ignore")) or {}).get("message") or {}
    except Exception as exc:
        tried.append("Crossref 元数据失败（%s）" % type(exc).__name__)

    for link in (meta.get("link") or []):
        url = str(link.get("URL") or "")
        if url and "pdf" in str(link.get("content-type") or "").lower():
            try:
                body = arxiv_mod.http_get(url, accept="application/pdf").body
                if body[:4] == b"%PDF":
                    return _doi_paper_from_pdf(body, pid, doi, canonical, cache_dir)
            except Exception as exc:
                tried.append("OA PDF 下载失败（%s）" % type(exc).__name__)
            break

    # ③ 落地页 HTML（够长才算全文页）
    if len(land_html) > 5000:
        paper = mdparse.parse_html(land_html, pid, source_path=canonical)
    else:
        paper = Paper(id=pid, source_path=canonical, source_kind="doi")

    paper.source_kind = "doi"
    paper.meta["doi"] = doi
    _apply_doi_meta(paper, meta)

    if len(land_html) > 5000:
        paper.meta.setdefault("warnings", []).append(
            "DOI 只取到出版商落地页（摘要级），没有全文 PDF；已尝试：%s" % "；".join(tried))
        quality.apply_quality(paper)
        return paper
    raise RuntimeError("DOI 无法解析成正文（%s）：%s" % (doi, "；".join(tried) or "未取到任何内容"))


def _doi_paper_from_pdf(body: bytes, pid: str, doi: str, canonical: str, cache_dir: Path) -> Paper:
    cache_dir.mkdir(parents=True, exist_ok=True)
    p = cache_dir / f"{pid}.pdf"
    p.write_bytes(body)
    paper = pdfparse.parse_pdf(p, pid)
    paper.source_kind = "doi"
    paper.source_path = canonical
    paper.meta["doi"] = doi
    return paper


def _apply_doi_meta(paper: Paper, meta: Dict[str, Any]) -> None:
    """把 Crossref 的标题 / 作者 / 摘要补进 Paper（只补空着的字段）。"""
    if not meta:
        return
    title = meta.get("title")
    if isinstance(title, list):
        title = title[0] if title else ""
    if title and not paper.title:
        paper.title = re.sub(r"\s+", " ", str(title)).strip()
    if not paper.authors:
        names = []
        for a in (meta.get("author") or []):
            nm = " ".join(x for x in (a.get("given"), a.get("family")) if x) or str(a.get("name") or "")
            if nm.strip():
                names.append(nm.strip())
        paper.authors = names
    if not paper.abstract and meta.get("abstract"):
        text = re.sub(r"<[^>]+>", " ", str(meta["abstract"]))
        paper.abstract = re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------- #
# 上传落盘
# --------------------------------------------------------------------------- #
def save_upload(filename: str, data: bytes, root: Path) -> Tuple[str, Path]:
    """把上传的字节落成 ``<root>/<paper_id>/`` 论文包，返回 ``(paper_id, 目录)``。

    * 文件名只取 basename（挡 ``../``），后缀必须在 :data:`UPLOAD_EXTS` 内；
    * ``.zip`` 走 :func:`_safe_extract_zip`；
    * 目录名 = 文件名 stem 的 sanitize 结果，于是这里的 ``paper_id`` 与之后 loader
      推算的**完全一致**（上传前端的轮询 id 才不会对不上）。
    """
    name = Path(str(filename or "").replace("\\", "/")).name
    if not name:
        raise ValueError("上传缺少文件名")
    ext = Path(name).suffix.lower()
    if ext not in UPLOAD_EXTS:
        raise ValueError("不支持的上传类型「%s」；支持：%s" % (ext or "无后缀", "、".join(UPLOAD_EXTS)))
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("文件超过 %d MB 上限" % (MAX_UPLOAD_BYTES // 1024 // 1024))

    pid = paper_id_from_path(Path(name))
    if pid == "paper" and Path(name).stem.strip() != "paper":
        # 文件名里一个 ASCII 字符都没有（如「论文.pdf」）时会整体塌成 "paper"，
        # 不同文件互相覆盖 —— 加一个名字哈希后缀保证唯一且可复现。
        pid = "paper-" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    elif len(pid) > MAX_PID_CHARS:
        # 整句标题当文件名时 pid 会很长（>120 字符），拼上原名后必然超过 Windows
        # MAX_PATH —— 截断并补名字哈希，既留住可读前缀又不会互相覆盖。
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
        pid = pid[:MAX_PID_CHARS - len(digest) - 1].rstrip("-._") + "-" + digest
    dest = Path(root) / pid
    if len(str(dest)) >= MAX_PATH_CHARS:
        raise ValueError("上传目录路径过长（%d 字符）：%s" % (len(str(dest)), dest))
    dest.mkdir(parents=True, exist_ok=True)
    if ext == ".zip":
        try:
            _safe_extract_zip(data, dest)
        except zipfile.BadZipFile as exc:
            raise ValueError("不是有效的 zip 文件：%s" % exc) from exc
    else:
        stored = _fit_filename(name, MAX_PATH_CHARS - len(str(dest)) - 1)
        (dest / stored).write_bytes(data)
    return pid, dest


def _fit_filename(name: str, budget: int) -> str:
    """把文件名压进 ``budget`` 字符以内：保留扩展名，截断处补名字哈希保证唯一。"""
    if len(name) <= budget:
        return name
    p = Path(name)
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    keep = budget - len(p.suffix) - len(digest) - 1
    if keep < 1:
        raise ValueError("文件名可用长度不足：%s" % name)
    return "%s-%s%s" % (p.stem[:keep].rstrip(" ."), digest, p.suffix)


def _safe_extract_zip(data: bytes, dest: Path) -> List[str]:
    """安全解包：拒绝绝对路径 / ``..`` / 超大条目；单一顶层目录会「提升」到根。

    提升是为了让 ``load_bundle`` 的根级 ``paper.pdf`` 查找能命中（否则解出来是
    ``<zip名>/paper.pdf``，loader 找不到就会退化成空结构）。
    """
    written: List[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_ZIP_ENTRIES:
            raise ValueError("zip 内文件数 %d 超过上限 %d" % (len(infos), MAX_ZIP_ENTRIES))
        names = [i.filename.replace("\\", "/") for i in infos]
        # 先校验安全，再谈「提升」——否则 `../x` 会在提升时被当成普通相对路径悄悄放行
        for nm in names:
            parts = Path(nm).parts
            if nm.startswith("/") or ".." in parts or (parts and parts[0].endswith(":")):
                raise ValueError("zip 内含不安全路径：%s" % nm)
        prefix = ""
        tops = {n.split("/", 1)[0] for n in names if "/" in n}
        if len(tops) == 1 and all("/" in n for n in names):
            prefix = tops.pop() + "/"
        for info, nm in zip(infos, names):
            rel = nm[len(prefix):] if prefix and nm.startswith(prefix) else nm
            if not rel:
                continue
            if info.file_size > MAX_UPLOAD_BYTES:
                raise ValueError("zip 内单个文件过大：%s" % nm)
            target = dest / rel
            if len(str(target)) >= MAX_PATH_CHARS:
                raise ValueError("zip 内路径过长（%d 字符）：%s" % (len(str(target)), nm))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info))
            written.append(rel)
    if not written:
        raise ValueError("zip 内没有任何文件")
    return written
