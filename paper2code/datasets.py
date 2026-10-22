"""数据集自动获取（roadmap M6-③）：**校验和验证 + 许可检查**，不通过就不给数据。

**为什么把关这么严**：论文附带的数据集常常没有校验和、许可不明。悄悄下载一个来路不明的
大文件，既可能拿到损坏/被替换的数据（导致「复现失败」其实是数据问题），也可能踩许可红线
（比如把"仅限学术、不得再分发"的数据塞进用户的产物里再往外发）。所以这里的规矩是：

1. **先查许可**：许可不在白名单里、或没有声明，默认**拒绝下载**（除非显式 `allow_unknown_license`）；
2. **再查校验和**：给了 ``sha256`` 就必须逐字节对上，不一致立刻删掉并记为失败；
3. **一切如实记账**：每个数据源的 ``下载状态/许可/实际 sha256/字节数`` 都写进 ``DATA_SOURCES.json``，
   失败也留在里面——不静默跳过。

离线（无网络）时如实报错并继续跑，「离线永远可用」这条硬约束不被破坏。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

#: 允许自动下载的许可（宽松/开放）。不在表里的（含「未声明」）默认拒绝。
ALLOWED_LICENSES = {
    "cc0-1.0", "cc-by-4.0", "cc-by-sa-4.0", "mit", "apache-2.0", "bsd-3-clause",
    "bsd-2-clause", "odc-by-1.0", "odbl-1.0", "public-domain", "pddl-1.0", "isc",
}
#: 明确**不可**自动下载的（含再分发限制）；给了这些即使 allow_unknown 也拒绝。
DENIED_LICENSES = {
    "cc-by-nc-4.0", "cc-by-nc-sa-4.0", "cc-by-nd-4.0", "proprietary", "non-commercial",
    "research-only", "all-rights-reserved",
}
MANIFEST = "DATA_SOURCES.json"
MAX_BYTES_DEFAULT = 2 * 1024 * 1024 * 1024      # 单文件上限 2 GiB


@dataclass
class DataSource:
    """一个待获取的数据源。``license`` 与 ``sha256`` 是**必须显式给出**的元数据。"""

    name: str
    url: str = ""
    sha256: str = ""
    license: str = ""
    size_bytes: Optional[int] = None
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def parse_sources(spec: Any) -> List[DataSource]:
    """把 ``[{...}]`` / YAML 里的数据源声明解析成 :class:`DataSource`。"""
    items = spec.get("datasets") if isinstance(spec, dict) else spec
    out: List[DataSource] = []
    for i, d in enumerate(items or []):
        if isinstance(d, str):
            out.append(DataSource(name=Path(d).name or "dataset%d" % i, url=d))
        elif isinstance(d, dict):
            out.append(DataSource(name=str(d.get("name") or "dataset%d" % i),
                                  url=str(d.get("url") or d.get("path") or ""),
                                  sha256=str(d.get("sha256") or "").strip().lower(),
                                  license=str(d.get("license") or "").strip().lower(),
                                  size_bytes=(int(d["size_bytes"]) if d.get("size_bytes") else None),
                                  note=str(d.get("note") or "")))
    return out


def license_ok(license_name: str, allow_unknown: bool = False) -> Dict[str, Any]:
    """许可判定：返回 ``{ok, reason}``。白名单以外一律拒绝（除非显式放开「未知」）。"""
    name = str(license_name or "").strip().lower()
    if not name:
        return {"ok": bool(allow_unknown),
                "reason": "未声明许可" + ("" if allow_unknown else "：默认拒绝自动下载（显式放开才继续）")}
    if name in DENIED_LICENSES:
        return {"ok": False, "reason": "许可 %s 不允许自动下载/再分发" % name}
    if name in ALLOWED_LICENSES:
        return {"ok": True, "reason": "许可 %s 在白名单内" % name}
    return {"ok": bool(allow_unknown),
            "reason": "许可 %s 不在白名单" % name + ("" if allow_unknown else "：默认拒绝（显式放开才继续）")}


def sha256_of(path: Path | str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def _is_local(url: str) -> bool:
    return url.startswith("file://") or "://" not in url


def _local_path(url: str) -> Path:
    if url.startswith("file://"):
        return Path(url[len("file://"):].lstrip("/\\"))
    return Path(url)


def fetch_source(src: DataSource, dest_dir: Path, allow_network: bool = True,
                 allow_unknown_license: bool = False, timeout: int = 60,
                 max_bytes: int = MAX_BYTES_DEFAULT) -> Dict[str, Any]:
    """获取单个数据源。**先许可、后校验和**；任何一步不过都记失败并清理落盘文件。"""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    rec: Dict[str, Any] = {"name": src.name, "url": src.url, "license": src.license,
                           "declared_sha256": src.sha256, "status": "pending",
                           "ts": datetime.now().isoformat(timespec="seconds")}
    if not src.url:
        rec["status"], rec["reason"] = "failed", "没有给出 url/path"
        return rec

    lic = license_ok(src.license, allow_unknown_license)
    rec["license_check"] = lic["reason"]
    if not lic["ok"]:
        rec["status"], rec["reason"] = "failed", "许可检查未通过：%s" % lic["reason"]
        return rec

    target = dest_dir / (Path(src.url).name if not _is_local(src.url) else _local_path(src.url).name)

    try:
        if _is_local(src.url):
            origin = _local_path(src.url)
            if not origin.exists():
                rec["status"], rec["reason"] = "failed", "本地数据源不存在：%s" % origin
                return rec
            shutil.copy2(origin, target)
            rec["origin"] = "local"
        else:
            if not allow_network:
                rec["status"], rec["reason"] = "failed", "禁止联网（allow_network=False）"
                return rec
            with urllib.request.urlopen(src.url, timeout=timeout) as resp:   # noqa: S310
                total = 0
                with open(target, "wb") as fh:
                    while True:
                        block = resp.read(1 << 20)
                        if not block:
                            break
                        total += len(block)
                        if total > max_bytes:
                            raise ValueError("超过单文件上限 %d 字节" % max_bytes)
                        fh.write(block)
            rec["origin"] = "http"
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        rec["status"], rec["reason"] = "failed", "下载失败：%s: %s" % (type(exc).__name__, exc)
        target.unlink(missing_ok=True)
        return rec

    actual = sha256_of(target)
    rec["actual_sha256"] = actual
    rec["bytes"] = target.stat().st_size
    rec["path"] = str(target)
    if src.sha256 and actual != src.sha256:
        target.unlink(missing_ok=True)
        rec["status"] = "failed"
        rec["reason"] = "校验和不匹配（声明 %s…，实际 %s…）：已删除" % (src.sha256[:12], actual[:12])
        rec.pop("path", None)
        return rec
    if not src.sha256:
        rec["checksum"] = "未声明（无法验证完整性）"
    else:
        rec["checksum"] = "sha256 校验通过"
    if src.size_bytes is not None and rec["bytes"] != src.size_bytes:
        target.unlink(missing_ok=True)
        rec["status"], rec["reason"] = "failed", "字节数不符（声明 %d，实际 %d）" % (src.size_bytes, rec["bytes"])
        rec.pop("path", None)
        return rec
    rec["status"], rec["reason"] = "ok", "已获取并通过检查"
    return rec


def acquire(spec: Any, dest_dir: Path | str, allow_network: bool = True,
            allow_unknown_license: bool = False, timeout: int = 60) -> Dict[str, Any]:
    """按声明获取一批数据源，并写 ``DATA_SOURCES.json``（成功/失败都记账）。"""
    dest_dir = Path(dest_dir)
    records = [fetch_source(s, dest_dir, allow_network=allow_network,
                            allow_unknown_license=allow_unknown_license, timeout=timeout)
               for s in parse_sources(spec)]
    ok = [r for r in records if r["status"] == "ok"]
    failed = [r for r in records if r["status"] != "ok"]
    manifest = {"generated_at": datetime.now().isoformat(timespec="seconds"),
                "allow_network": allow_network, "allow_unknown_license": allow_unknown_license,
                "n_ok": len(ok), "n_failed": len(failed), "sources": records}
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / MANIFEST
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": bool(ok) and not failed, "n_ok": len(ok), "n_failed": len(failed),
            "manifest": str(path), "sources": records,
            "warnings": ["数据源 %s 未获取：%s" % (r["name"], r["reason"]) for r in failed]}


def sources_from_paper(paper: Any) -> List[Dict[str, Any]]:
    """把论文里抽取到的**数据链接**（``Paper.data_refs``）转成待确认的数据源声明。

    注意：论文一般只给链接，**不会**给出 sha256 与许可——所以这里产出的声明是
    「待人工补全」的：缺许可就默认拒绝下载（见 :func:`license_ok`），这是有意的。
    """
    out: List[Dict[str, Any]] = []
    for r in getattr(paper, "data_refs", []) or []:
        url = str(getattr(r, "url", "") or "")
        if not url:
            continue
        out.append({"name": url.rstrip("/").split("/")[-1] or "dataset",
                    "url": url, "license": "", "sha256": "",
                    "note": "论文里抽到的数据链接；许可与校验和需人工补全后才允许下载"})
    return out


# --------------------------------------------------------------------------- #
# 声明文件与「先审后取」
# --------------------------------------------------------------------------- #
def load_spec(path: Path | str) -> List[Dict[str, Any]]:
    """读数据源声明（``configs/datasets.yaml`` 或 .json）。文件不存在 → 空表（不报错）。"""
    p = Path(path)
    if not p.exists():
        return []
    try:
        if p.suffix.lower() in (".yaml", ".yml"):
            import yaml  # type: ignore

            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        else:
            data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [s.to_dict() for s in parse_sources(data)]


def check_spec(spec: Any, allow_unknown_license: bool = False) -> Dict[str, Any]:
    """**只审查不下载**：把每个数据源的许可/校验和元数据过一遍，给出可下载清单。

    这是「先审后取」的落点：跑之前就知道哪些会被拒、为什么，而不是下载完才发现。
    """
    rows: List[Dict[str, Any]] = []
    for s in parse_sources(spec):
        lic = license_ok(s.license, allow_unknown_license)
        rows.append({"name": s.name, "url": s.url, "license": s.license,
                     "license_ok": lic["ok"], "license_check": lic["reason"],
                     "has_sha256": bool(s.sha256),
                     "checksum_check": "已声明 sha256（下载后校验）" if s.sha256
                                       else "未声明 sha256：无法验证完整性（会如实记账）"})
    allowed = [r for r in rows if r["license_ok"]]
    return {"n_total": len(rows), "n_allowed": len(allowed), "n_blocked": len(rows) - len(allowed),
            "sources": rows,
            "warnings": ["数据源 %s 被许可检查拦下：%s" % (r["name"], r["license_check"])
                         for r in rows if not r["license_ok"]]}
