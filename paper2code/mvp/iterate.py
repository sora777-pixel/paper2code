"""MVP 的版本化与差异（roadmap M5-②）。

**它做什么**：详情页的「改一次」会产生一个新版本，这里负责把**改动前**的 mv产目录存档、
算出版本间的 diff、并把「谁在第几版改了什么」记成可复核的清单（``versions.json``）。

目录约定（**不破坏既有路径**：``outputs/single/<id>/mvp/`` 仍然是「当前版本」）：

```
outputs/single/<id>/mvp/
  demo/ figures/ artifacts/ MVP_REPORT.md ...     ← 当前生效版本
  versions.json                                   ← 版本清单（current + live + history）
  versions/v1/…                                   ← 历史版本存档（含改动前的全部文件）
  versions/v1_DIFF.md                             ← v1 → 下一版的统一 diff
```

**诚实边界**：diff 是文本级的（``difflib``），只对文本文件算；二进制（png）只列「新增/删除/大小变化」。
改不动的版本不会被写成「改好了」——若修改后 demo 跑不通，`MVP_REPORT.md` 会照实说明。
"""

from __future__ import annotations

import difflib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

VERSIONS_DIR = "versions"
MANIFEST = "versions.json"
#: 存档与 diff 时要跳过的目录（版本目录自身、执行产物、缓存）
SKIP_PARTS = ("versions", "results", "__pycache__", "demo_llm_failed")
#: 要跳过的文件（版本清单自身：它是元数据，不该被当成「被改动的文件」再存档/再 diff）
SKIP_NAMES = (MANIFEST,)
TEXT_EXTS = (".py", ".md", ".json", ".txt", ".yaml", ".yml", ".csv")


def manifest_path(out: Path | str) -> Path:
    return Path(out) / MANIFEST


def load_manifest(out: Path | str) -> Dict[str, Any]:
    p = manifest_path(out)
    if not p.exists():
        return {"current": 1, "live": {}, "history": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"current": 1, "live": {}, "history": []}
    data.setdefault("current", 1)
    data.setdefault("live", {})
    data.setdefault("history", [])
    return data


def save_manifest(out: Path | str, data: Dict[str, Any]) -> None:
    manifest_path(out).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_versioned(rel: Path) -> bool:
    parts = rel.parts
    if any(p in SKIP_PARTS for p in parts):
        return False
    if rel.name in SKIP_NAMES:
        return False
    return not (len(parts) >= 2 and parts[0] == "figures" and parts[1] == "out")


def versioned_files(out: Path) -> List[Path]:
    """当前版本里值得存档的文件（相对路径）。"""
    out = Path(out)
    if not out.exists():
        return []
    return sorted(p.relative_to(out) for p in out.rglob("*") if p.is_file() and _is_versioned(p.relative_to(out)))


def archive_current(out: Path | str, instruction: str = "") -> Dict[str, Any]:
    """把**当前生效版本**存档为 ``versions/v{n}``，并把 current 推进到 n+1。

    返回 ``{n, dir, files}``；``files`` 是存档的相对路径清单（供恢复用）。
    """
    out = Path(out)
    data = load_manifest(out)
    n = int(data.get("current") or 1)
    dest = out / VERSIONS_DIR / ("v%d" % n)
    shutil.rmtree(dest, ignore_errors=True)
    files = versioned_files(out)
    for rel in files:
        d = dest / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(out / rel, d)
        except OSError:
            pass
    entries = [e for e in data["history"] if int(e.get("n", -1)) != n]
    entries.append({"n": n, "dir": "%s/v%d" % (VERSIONS_DIR, n), "archived_at": _now(),
                    "instruction": instruction or "", "files": len(files),
                    "note": "改动前的版本"})
    data["history"] = sorted(entries, key=lambda e: int(e.get("n", 0)))
    data["current"] = n + 1
    save_manifest(out, data)
    return {"n": n, "dir": str(dest), "files": files}


def read_files(root: Path | str, rels: Optional[List[Path]] = None) -> Dict[str, str]:
    """读出一个目录下的文本文件（供「改代码」提示词与 diff 用）。"""
    root = Path(root)
    rels = versioned_files(root) if rels is None else rels
    out: Dict[str, str] = {}
    for rel in rels:
        p = root / rel
        if p.suffix.lower() in TEXT_EXTS:
            try:
                out[str(rel).replace("\\", "/")] = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    return out


def restore(out: Path | str, snap: Dict[str, Any]) -> None:
    """把存档里的文件放回 ``out``（作为「改之前」的底稿）。"""
    out = Path(out)
    src = Path(snap["dir"])
    for rel in snap.get("files") or []:
        s, d = src / rel, out / rel
        if not s.exists():
            continue
        d.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(s, d)
        except OSError:
            pass


def diff_unified(old_dir: Path | str, new_dir: Path | str, context: int = 3) -> str:
    """两个版本之间的统一 diff（文本文件）+ 二进制文件的增删说明。"""
    old_dir, new_dir = Path(old_dir), Path(new_dir)
    old = read_files(old_dir)
    new = read_files(new_dir)
    chunks: List[str] = []
    for rel in sorted(set(old) | set(new)):
        a = (old.get(rel) or "").splitlines(keepends=True)
        b = (new.get(rel) or "").splitlines(keepends=True)
        if old.get(rel) == new.get(rel):
            continue
        # 去掉 results/versions 干扰后仍无差异的，跳过
        d = list(difflib.unified_diff(a, b, fromfile="a/" + rel, tofile="b/" + rel, n=context))
        if d:
            chunks.extend(d)
            if not chunks[-1].endswith("\n"):
                chunks.append("\n")
    bins = []
    for rel in sorted(set(_binaries(old_dir)) | set(_binaries(new_dir))):
        o, nw = old_dir / rel, new_dir / rel
        name = str(rel).replace("\\", "/")
        if o.exists() and not nw.exists():
            bins.append("- 删除：%s" % name)
        elif nw.exists() and not o.exists():
            bins.append("+ 新增：%s" % name)
        elif nw.exists() and o.exists() and o.stat().st_size != nw.stat().st_size:
            bins.append("~ 改变：%s（%d → %d 字节）" % (name, o.stat().st_size, nw.stat().st_size))
    if bins:
        chunks.append("\n## 二进制产物变化\n")
        chunks.extend(b + "\n" for b in bins)
    return "".join(chunks)


def _binaries(root: Path) -> List[Path]:
    root = Path(root)
    if not root.exists():
        return []
    return sorted(p.relative_to(root) for p in root.rglob("*")
                  if p.is_file() and _is_versioned(p.relative_to(root))
                  and p.suffix.lower() not in TEXT_EXTS)


def write_diff(out: Path | str, n: int, text: str) -> str:
    """把 diff 写到 ``versions/v{n}_DIFF.md``，返回相对路径。"""
    out = Path(out)
    p = out / VERSIONS_DIR / ("v%d_DIFF.md" % n)
    p.parent.mkdir(parents=True, exist_ok=True)
    header = "# 版本 v%d → v%d 的差异\n\n> 由 `paper2code.mvp.iterate` 生成的文本级 diff（只含文本文件；二进制只列增删/大小变化）。\n\n" % (n, n + 1)
    p.write_text(header + "```diff\n" + (text or "（无差异）") + "\n```\n", encoding="utf-8")
    return str(p.relative_to(out)).replace("\\", "/")


def finish_build(out: Path | str, *, generator: str, instruction: str = "", demo_ok: Optional[bool] = None,
                 demo_ok_detail: str = "", extra: Optional[Dict[str, Any]] = None,
                 archived: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """一次构建/修改结束后收口：更新 ``live``，必要时补上「上一版 → 本版」的 diff。"""
    out = Path(out)
    data = load_manifest(out)
    n = int(data.get("current") or 1)
    if archived is not None:
        text = diff_unified(archived["dir"], out)
        rel = write_diff(out, int(archived["n"]), text)
        for e in data["history"]:
            if int(e.get("n", -1)) == int(archived["n"]):
                e["diff"] = rel
                e["changed_files"] = _changed_files(text)
                e["diff_lines"] = len(text.splitlines())
                break
    live = {"n": n, "generator": generator, "instruction": instruction or "",
            "demo_ok": demo_ok, "demo_ok_detail": demo_ok_detail, "updated_at": _now()}
    if extra:
        live.update(extra)
    data["live"] = live
    save_manifest(out, data)
    return data


def summarize(out: Path | str) -> Dict[str, Any]:
    """给 Web 端渲染的版本摘要。"""
    data = load_manifest(out)
    out = Path(out)
    hist = []
    for e in data.get("history", []):
        d = e.get("dir") or ""
        hist.append({**e, "diff": e.get("diff") or "", "exists": bool(d) and (out / d).exists()})
    return {"current": data.get("current"), "live": data.get("live") or {},
            "history": sorted(hist, key=lambda e: int(e.get("n", 0)), reverse=True)}


def _changed_files(diff_text: str) -> List[str]:
    """从 unified diff 的头部行里抽出「被改动的文件」（去掉 a/ b/ 前缀，去重排序）。"""
    out = set()
    for ln in diff_text.splitlines():
        if ln.startswith("--- ") or ln.startswith("+++ "):
            target = ln[4:].strip()
            if target.startswith(("a/", "b/")):
                target = target[2:]
            if target and target != "/dev/null":
                out.add(target)
    return sorted(out)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
