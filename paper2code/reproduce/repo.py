"""情形 A（有开源代码）：仓库复用。

本模块**不重复实现论文的方法**——那是仓库自己的事。它做的是复现工程中最耗
时间的那部分：

1. 把仓库「读懂」：入口脚本、依赖声明、数据文件、已有结果文件；
2. 产出可执行的复现入口 ``repro_run.sh``（含锁定依赖、固定随机种子）；
3. 把仓库自带的结果文件与论文表格对齐，做逐格指标对标。

在单机 MVP 里，若仓库已在本地（论文包 ``code/`` 目录），以上全部离线可做；
若只有 URL 且网络不可用，则输出「待执行脚本 + 明确降级说明」，不做假复现。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

DATA_EXTS = {".csv", ".tsv", ".json", ".jsonl", ".txt", ".parquet", ".npy", ".npz", ".xlsx"}
RESULT_DIR_HINTS = ("result", "results", "output", "outputs", "exp", "experiments", "tables", "figures", "logs")
ENTRY_HINTS = ("main.py", "train.py", "run.py", "eval.py", "evaluate.py", "demo.py", "experiment.py", "reproduce.py", "repro.py")
REQ_FILES = ("requirements.txt", "environment.yml", "environment.yaml", "pyproject.toml", "setup.py", "Pipfile", "poetry.lock")
README_HINTS = ("readme.md", "readme.rst", "readme.txt", "readme")


@dataclass
class RepoManifest:
    root: str = ""
    url: str = ""
    exists: bool = False
    entrypoints: List[str] = field(default_factory=list)
    requirements_files: List[str] = field(default_factory=list)
    requirements: List[str] = field(default_factory=list)
    has_dockerfile: bool = False
    readme: str = ""
    data_files: List[str] = field(default_factory=list)
    result_files: List[str] = field(default_factory=list)
    code_language: str = "python"
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        d["readme"] = (self.readme or "")[:1500]
        return d


# --------------------------------------------------------------------------- #
_META_NAMES = {x.lower() for x in REQ_FILES} | {x.lower() for x in README_HINTS} | {
    "license", "license.txt", "license.md", "notice", "authors", "citation.cff",
    "changelog.md", "contributing.md", ".gitignore", ".gitattributes", "makefile",
}


def _is_metadata_file(name: str) -> bool:
    """排除 README / LICENSE / requirements.txt 之类被误判为「数据文件」的元数据。"""
    return name.lower() in _META_NAMES


def introspect(root: Path | str) -> RepoManifest:
    root = Path(root)
    m = RepoManifest(root=str(root))
    if not root.exists():
        m.notes.append(f"仓库目录不存在：{root}")
        return m
    m.exists = True

    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = str(p.relative_to(root)).replace("\\", "/")
        name = p.name.lower()
        suffix = p.suffix.lower()

        if name in README_HINTS and not m.readme:
            try:
                m.readme = p.read_text(encoding="utf-8", errors="ignore")[:4000]
            except Exception:
                pass
        if name in REQ_FILES:
            m.requirements_files.append(rel)
            if name == "requirements.txt":
                try:
                    m.requirements.extend(
                        ln.strip()
                        for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines()
                        if ln.strip() and not ln.strip().startswith("#")
                    )
                except Exception:
                    pass
        if name == "dockerfile":
            m.has_dockerfile = True
        if suffix in DATA_EXTS and not _is_metadata_file(name):
            (m.result_files if any(h in rel.lower() for h in RESULT_DIR_HINTS) else m.data_files).append(rel)
        if suffix == ".py" and p.name.lower() in ENTRY_HINTS:
            m.entrypoints.append(rel)

    # 没有命中命名清单时，退化为「含 __main__ 的脚本」
    if not m.entrypoints:
        for p in sorted(root.rglob("*.py")):
            try:
                head = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if "__main__" in head:
                m.entrypoints.append(str(p.relative_to(root)).replace("\\", "/"))
            if len(m.entrypoints) >= 6:
                break

    m.data_files = sorted(m.data_files)[:80]
    m.result_files = sorted(m.result_files)[:80]
    m.entrypoints = sorted(m.entrypoints)[:10]

    if not m.requirements_files:
        m.notes.append("未发现依赖声明文件，复现脚本无法锁定环境，建议人工确认依赖。")
    if not m.entrypoints:
        m.notes.append("未识别到入口脚本，需人工指定复现入口。")
    return m


def build_run_script(manifest: RepoManifest, python_exe: str = "python", seed: int = 0) -> str:
    """生成 ``repro_run.sh``：隔离环境 → 固定种子 → 执行入口。"""
    req = manifest.requirements_files[0] if manifest.requirements_files else ""
    entry = manifest.entrypoints[0] if manifest.entrypoints else "<请指定入口脚本>"
    data_hint = ", ".join(manifest.data_files[:5]) if manifest.data_files else "无"

    lines = [
        "#!/usr/bin/env bash",
        "# ============================================================",
        "# 由 paper2code 自动生成的复现脚本（情形 A：有开源代码）",
        "# 目标：在单机环境下，用仓库自身代码重跑论文中的表格与图表",
        "# ============================================================",
        "set -euo pipefail",
        "",
        f'REPO_DIR="${{REPO_DIR:-{manifest.root or "$(pwd)"}}}"',
        'WORK_DIR="${WORK_DIR:-$PWD/.p2c_repro}"',
        "",
        'echo "[1/5] 创建工作目录 $WORK_DIR"',
        'mkdir -p "$WORK_DIR"',
        "",
        'echo "[2/5] 创建隔离虚拟环境（避免污染系统环境）"',
        f'{python_exe} -m venv "$WORK_DIR/venv" 2>/dev/null || echo "  venv 已存在"',
        '# shellcheck disable=SC1091',
        'source "$WORK_DIR/venv/bin/activate" 2>/dev/null || source "$WORK_DIR/venv/Scripts/activate"',
        "",
        'echo "[3/5] 安装依赖"',
    ]
    if req:
        lines += [
            f'if [ -f "$REPO_DIR/{req}" ]; then',
            f'  pip install -r "$REPO_DIR/{req}"',
            "else",
            f'  echo "  未找到 {req}，跳过（请人工确认依赖）"',
            "fi",
        ]
    else:
        lines.append('echo "  仓库未提供依赖声明，跳过；请人工确认（见 report 的 notes）"')

    lines += [
        "",
        'echo "[4/5] 固定随机种子以保证可复现"',
        f'export PYTHONHASHSEED={seed}',
        f'export P2C_SEED={seed}',
        "export MPLBACKEND=Agg",
        "export PYTHONIOENCODING=utf-8",
        "export OMP_NUM_THREADS=4",
        "",
        'echo "[5/5] 执行复现入口"',
        f'cd "$REPO_DIR"',
        f'{python_exe} {entry} ' + '"$@"',
        "",
        f'echo "完成。仓库内的数据文件（前若干）： {data_hint}"',
        'echo "把产出的 results/*.csv 放回 paper2code 的 outputs/<paper>/reproduce/ 即可自动对标。"',
    ]
    return "\n".join(lines) + "\n"


def write_manifest(manifest: RepoManifest, out_dir: Path, extra: Optional[Dict[str, Any]] = None) -> str:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = manifest.to_dict()
    if extra:
        payload.update(extra)
    p = out_dir / "repo_manifest.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)


# --------------------------------------------------------------------------- #
def try_clone(url: str, dest: Path, timeout: int = 180) -> Dict[str, Any]:
    """尝试浅克隆。失败不抛出，返回带 ``ok=False`` 的结果供报告说明。"""
    if shutil.which("git") is None:
        return {"ok": False, "error": "系统未安装 git，无法自动克隆；请手工下载仓库后放入论文包的 code/ 目录"}
    if dest.exists() and any(dest.iterdir()):
        return {"ok": True, "path": str(dest), "note": "目标目录已存在，跳过克隆"}
    try:
        proc = subprocess.run(  # noqa: S603
            ["git", "clone", "--depth", "1", url, str(dest)],
            capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace",
        )
        if proc.returncode == 0:
            return {"ok": True, "path": str(dest)}
        return {"ok": False, "error": (proc.stderr or "")[-800:]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def pick_result_files(manifest: RepoManifest, root: Path) -> List[Path]:
    """挑出最可能承载「论文表格数值」的结果文件。"""
    root = Path(root)
    scored: List[tuple[int, Path]] = []
    for rel in manifest.result_files:
        p = root / rel
        if not p.exists():
            continue
        score = 0
        low = rel.lower()
        if low.endswith(".csv"):
            score += 5
        if any(h in low for h in ("table", "main", "result", "eval", "metric")):
            score += 4
        if "fig" in low or "figures" in low:
            score -= 2
        try:
            score += min(3, p.stat().st_size // 4096)
        except Exception:
            pass
        scored.append((-score, p))
    scored.sort(key=lambda kv: kv[0])
    return [p for _, p in scored[:8]]


def extract_seed_from_readme(readme: str) -> Optional[int]:
    m = re.search(r"seed\D{0,12}(\d{1,6})", readme or "", re.IGNORECASE)
    return int(m.group(1)) if m else None
