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

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

DATA_EXTS = {".csv", ".tsv", ".json", ".jsonl", ".txt", ".parquet", ".npy", ".npz", ".xlsx"}
RESULT_DIR_HINTS = ("result", "results", "output", "outputs", "exp", "experiments", "tables", "figures", "logs")
ENTRY_HINTS = ("main.py", "train.py", "run.py", "eval.py", "evaluate.py", "demo.py", "experiment.py", "reproduce.py", "repro.py")
#: 候选入口的基础分：越像「跑论文主实验」的脚本分越高
_ENTRY_BASE_SCORE = {
    "train.py": 5, "main.py": 4, "run.py": 4, "finetune.py": 4, "finetuning.py": 4,
    "train_model.py": 4, "run_experiment.py": 3, "reproduce.py": 3, "repro.py": 3,
    "experiment.py": 2, "eval.py": 2, "evaluate.py": 2, "demo.py": 0,
}
#: 这些目录下的脚本不是论文入口：实测 trisds 选中 cmake 的 nvcc 构建脚本、
#: chemcraft 选中 examples 的 demo，都属于「能跑但根本不是论文实验」。
_NON_ENTRY_SEGMENTS = {
    "test", "tests", "cmake", "build", "dist", "docs", "doc", "examples", "example",
    "third_party", "vendor", "node_modules", "__pycache__", ".github", "metrics",
}
#: 这些文件名是脚手架/数据准备脚本，不是论文实验入口（实测 chemcraft 的
#: dummy_agent.py / create_dataset.py 会被 __main__ 兜底逻辑选中）。
_NON_ENTRY_NAME_HINTS = (
    "dummy", "scratch", "tutorial", "playground", "create_dataset", "process_dataset",
    "prepare_data", "download", "install",
)
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


def _is_non_entry_path(rel: str) -> bool:
    """测试 / 构建 / 示例/脚手架脚本不是论文入口。"""
    p = Path(rel)
    stem = p.stem.lower()
    if p.name.lower() in ("setup.py", "conftest.py") or stem.startswith("test_"):
        return True
    if any(stem == h or stem.startswith(h + "_") for h in _NON_ENTRY_NAME_HINTS):
        return True
    return any(part.lower() in _NON_ENTRY_SEGMENTS for part in p.parts[:-1])


def _entry_score(root: Path, rel: str) -> int:
    """给入口候选打分：README 引用 > train/main/run > 顶层 > 深层。

    ``-100`` 表示明确排除（tests/cmake/examples 等）。分数只用于排序，不改变
    ``entrypoints`` 的「可执行候选」语义——被排除的脚本不会进入候选集。
    """
    p = Path(rel)
    name = p.name.lower()
    score = _ENTRY_BASE_SCORE.get(name, 0)
    depth = len(p.parts) - 1
    score += 2 if depth == 0 else (1 if depth == 1 else 0)
    if p.parts and p.parts[0].lower() in ("src", "scripts"):
        score += 3  # 论文自有实现通常在 src/；Pocket2Mol/ 这类内嵌子项目降一档
    if any(k in name for k in ("train", "finetune", "main", "run")):
        score += 1  # 兜底候选（不在 ENTRY_HINTS 里）优先训练/主程序脚本
    try:
        head = (Path(root) / rel).read_text(encoding="utf-8", errors="ignore")[:4000]
    except OSError:
        head = ""
    if "argparse" in head:
        score += 1
    if "__main__" in head:
        score += 1
    # 构建/驱动脚本（如 trids_nvcc_driver.py）即使命名命中，也不该当论文入口
    if any(k in head for k in ("nvcc", "cmake")) and "subprocess" in head:
        score -= 2
    return score


def entry_candidates(manifest: "RepoManifest", limit: int = 3) -> List[str]:
    """按排序返回前若干个入口候选；供 smoke 失败后自动换下一个。"""
    return list((manifest.entrypoints or [])[:max(0, limit)])


# --------------------------------------------------------------------------- #
# D：隔离 venv（默认不启用；必须显式开启）
# --------------------------------------------------------------------------- #
def venv_python(venv_dir: Path) -> Path:
    """venv 里的解释器路径（Windows: ``Scripts/python.exe``）。"""
    venv_dir = Path(venv_dir)
    return venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def write_install_scripts(out_dir: Path, venv_dir: Path, specs: Sequence[str],
                          pip_index: str = "") -> Dict[str, str]:
    """落一份可人工执行的安装脚本 + 锁定清单（**不执行**）。

    自动安装默认关闭；即使关闭，也要让用户能一键复现同一套依赖，
    而不是只看到一句「缺少依赖」。
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    lock = out / "requirements.lock.txt"
    lock.write_text("\n".join(specs) + ("\n" if specs else ""), encoding="utf-8")

    index_ps = (" -i %s" % pip_index) if pip_index else ""
    py = venv_python(Path(venv_dir))
    ps1 = out / "install_env.ps1"
    ps1.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "python -m venv \"%s\"\n"
        "& \"%s\" -m pip install --no-input -r \"%s\"%s\n"
        % (Path(venv_dir), py, lock, index_ps),
        encoding="utf-8",
    )
    sh = out / "install_env.sh"
    py_sh = py.as_posix()
    sh.write_text(
        "#!/usr/bin/env sh\nset -e\n"
        "python -m venv \"%s\"\n"
        "\"%s\" -m pip install --no-input -r \"%s\"%s\n"
        % (Path(venv_dir).as_posix(), py_sh, lock.as_posix(), index_ps),
        encoding="utf-8",
    )
    return {"lock": str(lock), "ps1": str(ps1), "sh": str(sh)}


def provision_venv(venv_dir: Path, specs: Sequence[str], pip_index: str = "",
                   timeout: int = 900, log_path: Optional[Path] = None,
                   allow_heavy: bool = False) -> Dict[str, Any]:
    """在 ``venv_dir`` 创建隔离环境并安装依赖；失败只返回原因，不抛异常。

    安全边界：
    * 仅操作 ``outputs/single/<id>/reproduce/venv``（论文自己的产物目录）；
    * 同依赖集合已装过则直接复用；
    * 重型依赖默认拒绝安装，必须 ``repo_install_heavy=True`` 才放行。
    """
    from ..deps import HEAVY_INSTALL, PACKAGE_TO_MODULE, requirement_name  # noqa: PLC0415

    venv_dir = Path(venv_dir)
    specs = [s for s in dict.fromkeys(str(x).strip() for x in (specs or ()) if str(x).strip())]
    py = venv_python(venv_dir)
    log_path = Path(log_path) if log_path else (venv_dir.parent / "venv_install.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)

    names = {requirement_name(s).lower() for s in specs if requirement_name(s)}
    modules = {PACKAGE_TO_MODULE.get(n, n) for n in names}
    heavy = sorted((names | modules) & HEAVY_INSTALL)
    if heavy and not allow_heavy:
        msg = ("重型依赖未自动安装：%s。确认要安装请显式设置 "
               "P2C_REPO_INSTALL_HEAVY=1（或 repo_install_heavy: true）。" % "、".join(heavy))
        log_path.write_text(msg + "\n", encoding="utf-8")
        return {"ok": False, "status": "heavy_blocked", "heavy": heavy,
                "venv": str(venv_dir), "log": str(log_path), "error": msg}

    digest = hashlib.sha256(
        ("\n".join(sorted(specs)) + "|" + pip_index).encode("utf-8")).hexdigest()[:16]
    marker = venv_dir / ".p2c_provisioned"
    try:
        if marker.exists() and marker.read_text(encoding="utf-8").strip() == digest and py.exists():
            return {"ok": True, "status": "reused", "venv": str(venv_dir),
                    "venv_python": str(py), "log": str(log_path), "installed": specs}
    except OSError:
        pass

    if not specs:
        return {"ok": True, "status": "no_deps", "venv": str(venv_dir),
                "venv_python": str(py), "log": str(log_path), "installed": []}

    transcript: List[str] = []
    if not py.exists():
        cmd = [sys.executable, "-m", "venv", str(venv_dir)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                                  encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - 创建失败只降级
            msg = "创建隔离 venv 失败：%s: %s" % (type(exc).__name__, exc)
            log_path.write_text(msg + "\n", encoding="utf-8")
            return {"ok": False, "status": "failed", "venv": str(venv_dir),
                    "log": str(log_path), "error": msg}
        transcript.append("[venv] " + " ".join(cmd))
        transcript.append((proc.stdout or "") + (proc.stderr or ""))
        if proc.returncode != 0:
            msg = "创建隔离 venv 失败（exit=%s）" % proc.returncode
            log_path.write_text(msg + "\n\n" + "\n".join(transcript), encoding="utf-8")
            return {"ok": False, "status": "failed", "venv": str(venv_dir),
                    "log": str(log_path), "error": msg}

    cmd = [str(py), "-m", "pip", "install", "--disable-pip-version-check",
           "--no-input", "--timeout", "120"]
    if pip_index:
        cmd += ["-i", pip_index]
    cmd += specs
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        msg = "pip 安装超时（>%ss）" % timeout
        log_path.write_text(msg + "\n\n" + "\n".join(transcript), encoding="utf-8")
        return {"ok": False, "status": "failed", "venv": str(venv_dir),
                "log": str(log_path), "error": msg}
    except Exception as exc:  # noqa: BLE001
        msg = "pip 安装异常：%s: %s" % (type(exc).__name__, exc)
        log_path.write_text(msg + "\n\n" + "\n".join(transcript), encoding="utf-8")
        return {"ok": False, "status": "failed", "venv": str(venv_dir),
                "log": str(log_path), "error": msg}

    transcript.append("[pip] " + " ".join(cmd))
    transcript.append((proc.stdout or "") + (proc.stderr or ""))
    log_path.write_text("\n".join(transcript), encoding="utf-8")
    if proc.returncode != 0:
        tail = ((proc.stderr or "") + (proc.stdout or "")).strip()[-400:]
        return {"ok": False, "status": "failed", "venv": str(venv_dir),
                "log": str(log_path), "error": tail or "pip install 失败（exit=%s）" % proc.returncode}

    try:
        marker.write_text(digest, encoding="utf-8")
    except OSError:
        pass
    return {"ok": True, "status": "installed", "venv": str(venv_dir),
            "venv_python": str(py), "log": str(log_path), "installed": specs}


def introspect(root: Path | str) -> RepoManifest:
    root = Path(root)
    m = RepoManifest(root=str(root))
    if not root.exists():
        m.notes.append(f"仓库目录不存在：{root}")
        return m
    m.exists = True
    entry_rejected: List[str] = []

    for p in root.rglob("*"):
        if not p.is_file():
            continue
        # 跳过隐藏/临时目录（.git/.venv/.smoke/__pycache__…）：它们里面的脚本
        # 不是仓库的入口，实测会把真正的入口挤下去
        rel_parts = p.relative_to(root).parts[:-1]
        if any(part.startswith(".") or part == "__pycache__" for part in rel_parts):
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
            elif name in ("environment.yml", "environment.yaml"):
                # conda 环境文件：只取 dependencies 段里的包名（channels 段不是依赖）
                try:
                    m.requirements.extend(_conda_dependencies(
                        p.read_text(encoding="utf-8", errors="ignore")))
                except Exception:
                    pass
        if name == "dockerfile":
            m.has_dockerfile = True
        if suffix in DATA_EXTS and not _is_metadata_file(name):
            (m.result_files if any(h in rel.lower() for h in RESULT_DIR_HINTS) else m.data_files).append(rel)
        if suffix == ".py" and p.name.lower() in ENTRY_HINTS:
            if _is_non_entry_path(rel):
                entry_rejected.append(rel)
            else:
                m.entrypoints.append(rel)

    # 没有命中命名清单时，退化为「含 __main__ 的脚本」
    if not m.entrypoints:
        for p in sorted(root.rglob("*.py")):
            if any(part.startswith(".") or part == "__pycache__"
                   for part in p.relative_to(root).parts[:-1]):
                continue
            rel = str(p.relative_to(root)).replace("\\", "/")
            if _is_non_entry_path(rel):
                continue
            try:
                head = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if "__main__" in head:
                m.entrypoints.append(rel)
            if len(m.entrypoints) >= 6:
                break

    m.data_files = sorted(m.data_files)[:80]
    m.result_files = sorted(m.result_files)[:80]
    # 入口按「顶层优先 + 像不像论文主实验」排序：顶层 main/train/run 永远是
    # 最可信的入口；没有顶层脚本时才在子目录里按分数挑（src/ 优于内嵌子项目）。
    m.entrypoints = sorted(
        set(m.entrypoints),
        key=lambda r: (0 if len(Path(r).parts) == 1 else 1, -_entry_score(root, r), r),
    )[:10]

    # M3-②：**README 文档化命令引用过的脚本是最可靠的入口**，移到最前。
    # 实测 PBEE 的入口叫 `pbee.py`（不在 ENTRY_HINTS 里），按字母序还会排在
    # etc/ modules/ 之后被 6 个名额挤掉——只有 README 这条线能把它捞回来。
    # 注意：已在列表里也必须**前移**，否则会被 `.smoke/` 之类先出现的脚本压住。
    for token in reversed(_readme_entry_tokens(m.readme)):
        if not (root / token).exists():
            continue
        if token in m.entrypoints:
            m.entrypoints.remove(token)
        m.entrypoints.insert(0, token)
    m.entrypoints = m.entrypoints[:10]

    if entry_rejected:
        m.notes.append(
            "已排除 %d 个非入口脚本（tests/examples/cmake 等）：%s"
            % (len(entry_rejected), "、".join(sorted(entry_rejected)[:3])))

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
        'echo "把产出的 results/*.csv 放回 paper2code 的 outputs/single/<paper>/reproduce/ 即可自动对标。"',
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
def _same_repo(a: str, b: str) -> bool:
    def norm(s: str) -> str:
        s = (s or "").strip().lower().rstrip("/")
        s = re.sub(r"^[a-z]+://", "", s)                 # 去掉 https://
        s = re.sub(r"^[^/@]+@([^/:]+):", r"\1/", s)      # git@github.com:owner/repo -> github.com/owner/repo
        return s[:-4] if s.endswith(".git") else s
    return norm(a) == norm(b)


def _clone_origin(dest: Path) -> str:
    """读已存在目录的 ``.git/config`` origin；不是 git 仓库则返回空串。"""
    cfg = Path(dest) / ".git" / "config"
    try:
        text = cfg.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    m = re.search(r"^\s*url\s*=\s*(\S+)", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def probe_remote(url: str, timeout: int = 25) -> Dict[str, Any]:
    """克隆前的**存在性预检**：``git ls-remote`` 比直接 clone 更快、错误更明确。

    仓库 404 时不再进入 60–180s 的 clone 超时，也不再把「被引用的第三方仓库」
    误当成「本论文仓库」；返回 ``status`` 便于报告区分 not_found / timeout /
    error / no_git。
    """
    if not url or not re.match(r"^https?://", url or ""):
        return {"ok": False, "status": "unsupported", "error": "仅支持 http(s) 仓库地址"}
    if shutil.which("git") is None:
        return {"ok": False, "status": "no_git", "error": "系统未安装 git，无法探测仓库"}
    try:
        proc = subprocess.run(  # noqa: S603
            ["git", "ls-remote", "--heads", url],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "status": "timeout", "error": "仓库探测超时（%ss）" % timeout}
    except Exception as exc:  # noqa: BLE001 - 探测失败只降级，不抛
        return {"ok": False, "status": "error", "error": str(exc)}

    if proc.returncode == 0:
        heads = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
        return {"ok": True, "status": "reachable", "heads": len(heads)}
    err = (proc.stderr or proc.stdout or "").strip()
    low = err.lower()
    status = "not_found" if ("not found" in low or "does not exist" in low
                             or "repository" in low) else "error"
    return {"ok": False, "status": status, "error": err[-400:]}


def try_clone(url: str, dest: Path, timeout: int = 180) -> Dict[str, Any]:
    """尝试浅克隆。失败不抛出，返回带 ``ok=False`` 的结果供报告说明。"""
    if shutil.which("git") is None:
        return {"ok": False, "error": "系统未安装 git，无法自动克隆；请手工下载仓库后放入论文包的 code/ 目录"}
    if dest.exists() and any(dest.iterdir()):
        origin = _clone_origin(dest)
        if origin and not _same_repo(origin, url):
            # 目录里已有别的仓库：绝不静默复用（否则会把 crewAI 当成论文代码）
            return {"ok": False, "error": "目标目录已有其他仓库（origin=%s），不覆盖" % origin}
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


# --------------------------------------------------------------------------- #
# M3-② 情形 A 仓库适配器
#
# 目标：不再只产出「待人工执行的 repro_run.sh」，而是**自动定位入口**、生成一份
# **缩减规模的 smoke 运行**（小数据、单次、CPU、固定种子），并把运行结果落盘。
#
# 关键设计：smoke 命令**不猜 flag**，而是优先采用 README 里**作者自己文档化**的示例
# 命令（实测 PBEE 的 README 就给了 `python3 pbee.py --ipdb ./tests/pdbs/1xgu.pdb
# --partner1 AB --partner2 C`）。只有文档化命令缺失时才退回「入口名 + 已知缩小 flag」。
# --------------------------------------------------------------------------- #
SMOKE_TIMEOUT_S = 180

#: README 里的示例命令行：``python3 xxx.py <args...>``
_CMD_RE = re.compile(r"^\s*(?:python3?|py|bash)\s+([^\s<>]+\.py)\s*(.*)$", re.MULTILINE)
#: 占位符参数（``<path/to/data>``）——不能直接用
_PLACEHOLDER_RE = re.compile(r"^[<\[]|[>\]]$")
#: 只在 README 缺失时兜底的缩小用 flag（值取自实测常见写法）
_SCALE_FLAGS = {
    "--epochs": "1", "--num-epochs": "1", "--epoch": "1",
    "--steps": "1", "--max-steps": "1", "--max_steps": "1", "--iters": "1",
    "--batch-size": "2", "--batch_size": "2", "--bs": "2",
    "--n-samples": "8", "--num-samples": "8", "--limit": "8", "--sample-size": "8",
    "--device": "cpu", "--gpus": "0", "--num-workers": "0", "--workers": "0",
}
_CPU_ENV = {
    "OMP_NUM_THREADS": "1",
    "CUDA_VISIBLE_DEVICES": "",
    "MPLBACKEND": "Agg",
    "PYTHONIOENCODING": "utf-8",
}


def smoke_env(seed: int) -> Dict[str, str]:
    """固定 CPU、单线程、固定种子的环境变量。"""
    env = dict(_CPU_ENV)
    env["PYTHONHASHSEED"] = str(seed)
    env["P2C_SEED"] = str(seed)
    return env


def _readme_entry_tokens(readme: str) -> List[str]:
    """README 里被 ``python xxx.py`` 引用过的脚本路径（已归一成仓库相对路径）。"""
    out: List[str] = []
    for line in (readme or "").splitlines():
        m = _CMD_RE.match(line)
        if not m:
            continue
        token = m.group(1).strip().lstrip("./")
        if token.endswith(".py") and token not in out:
            out.append(token)
    return out


def readme_example_command(readme: str, entry: str) -> Optional[List[str]]:
    """从 README 抽出**作者文档化**的示例命令；含占位符参数的候选会被跳过。"""
    if not entry:
        return None
    name = Path(entry).name
    for line in (readme or "").splitlines():
        m = _CMD_RE.match(line)
        if not m or Path(m.group(1)).name != name:
            continue
        try:
            args = shlex.split(m.group(2).replace("\\", ""))
        except ValueError:
            continue
        if not args or any(_PLACEHOLDER_RE.search(a) for a in args):
            continue
        return args
    return None


def _single_existing_path(token: str, cwd: Path) -> Optional[str]:
    """把可能的 glob / 多路径参数缩成**一个真实存在**的路径。"""
    base = cwd / token if not Path(token).is_absolute() else Path(token)
    if base.exists():
        return str(base)
    parent, pattern = base.parent, base.name
    if parent.is_dir():
        matches = sorted(parent.glob(pattern))
        if matches:
            return str(matches[0])
    return None


_OUT_FLAGS = ("--odir", "--outdir", "--output-dir", "--output_dir", "--out")
_FLAG_EQ_RE = re.compile(r"^(--[A-Za-z0-9_-]+)=(.*)$")
#: ``add_argument('--flag', ...)`` —— 用来确定性地得知入口的 CLI 表面
_ADD_ARG_RE = re.compile(r"""add_argument\(\s*['"](--[A-Za-z0-9_-]+)['"]""")


def argparse_flags(path: Optional[Path]) -> List[str]:
    """扫出入口脚本声明的长选项（确定性，不执行代码）。"""
    if path is None:
        return []
    try:
        src = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    seen: List[str] = []
    for m in _ADD_ARG_RE.finditer(src):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def reduce_scale(args: Sequence[str], repo_root: Path, out_dir: Path) -> Tuple[List[str], List[str]]:
    """把文档化示例命令缩成 smoke 规模，并回写输出目录到 smoke 工作目录。

    **必须连带吃掉原值**：早期实现只替换了 flag 对应的值却没消费掉原参数，
    于是 `--epochs 100` 变成了 `--epochs 1 100`，入口直接报「unrecognized arguments」。
    """
    out: List[str] = []
    notes: List[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else ""
        has_value = bool(nxt) and not nxt.startswith("-")

        if a in _OUT_FLAGS:
            out.extend([a, str(out_dir)])
            notes.append("输出目录改到 smoke 工作目录（不污染仓库）")
            i += 2 if has_value else 1
            continue

        m = _FLAG_EQ_RE.match(a)
        if m and m.group(1) in _SCALE_FLAGS:            # --epochs=100 形式
            out.extend([m.group(1), _SCALE_FLAGS[m.group(1)]])
            notes.append("缩小规模：%s %s" % (m.group(1), _SCALE_FLAGS[m.group(1)]))
            i += 1
            continue

        if a in _SCALE_FLAGS:
            out.extend([a, _SCALE_FLAGS[a]])
            notes.append("缩小规模：%s %s" % (a, _SCALE_FLAGS[a]))
            i += 2 if has_value else 1
            continue

        if not a.startswith("-") and ("/" in a or a.endswith((".pdb", ".csv", ".tsv"))):
            one = _single_existing_path(a, repo_root)
            if one:
                if one != a:
                    notes.append("多/通配输入缩成单个样本：%s" % Path(one).name)
                out.append(one)
                i += 1
                continue

        out.append(a)
        i += 1
    return out, notes


def _conda_dependencies(text: str) -> List[str]:
    """只取 conda ``environment.yml`` 的 ``dependencies:`` 段里的包名。

    ``channels:`` 段里那些 ``- conda-forge`` 不是依赖；解释器本身（python/pip/conda）
    也不该被当成缺依赖去报告。
    """
    skip = {"python", "pip", "conda", "conda-forge", "defaults"}
    out: List[str] = []
    in_deps = False
    for ln in (text or "").splitlines():
        if re.match(r"^dependencies:\s*$", ln):
            in_deps = True
            continue
        if in_deps and ln and not ln[0].isspace() and not ln.startswith(("-", "#")):
            in_deps = False  # 回到顶层，dependencies 段结束
        if not in_deps:
            continue
        s = ln.strip()
        if not s.startswith("- "):
            continue
        dep = s[2:].strip().strip("\"'")
        if not dep or dep.endswith(":"):
            continue
        bare = re.split(r"[<>=!~\s\[]", dep)[0].lower()  # 去掉版本修饰再判解释器本身
        if bare in skip:
            continue
        out.append(dep)
    return out


def _is_repo_local(module: str, root: Path) -> bool:
    """该 import 是不是仓库自带的模块/包（不是第三方依赖）。"""
    return (root / module).is_dir() or (root / ("%s.py" % module)).exists() \
        or (root / "src" / module).is_dir() or (root / "src" / ("%s.py" % module)).exists()


# --------------------------------------------------------------------------- #
# library-mode smoke（M3-② 的第二条路）
#
# 当仓库的**命令行入口**跑不动时（实测 PBEE 的 `pbee.py` 需要 pyrosetta——非 PyPI
# 且要 Rosetta 许可），退而求其次：直接调用**仓库自带的训练 API**（实测
# `modules/superlearner.py::train_base_models(x, y, models)` 只依赖 numpy/sklearn）。
# 这条路只证明「仓库自己的 ML 核心能在本环境跑起来」，**不是论文数值复现**
# （真实特征仍需 pyrosetta 计算），所以结果里必须带这句话。
# --------------------------------------------------------------------------- #
_FUNC_RE = re.compile(r"^def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)", re.MULTILINE)
_TRAIN_FN_HINTS = ("train_base_models", "train", "fit", "build_model", "train_model")
_PREDICT_FN_HINTS = ("super_learner_predictions", "predict", "predictor", "infer")


def find_training_api(root: Optional[Path]) -> Optional[Dict[str, Any]]:
    """在仓库自己的模块里找「可直接调用的训练/预测 API」。"""
    if root is None or not Path(root).exists():
        return None
    root = Path(root)
    best: Optional[Dict[str, Any]] = None
    for p in sorted(root.rglob("*.py")):
        if any(part.startswith(".") or part == "__pycache__"
               for part in p.relative_to(root).parts[:-1]):
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        funcs = {m.group(1): [a.strip() for a in m.group(2).split(",") if a.strip()]
                 for m in _FUNC_RE.finditer(src)}
        train = next((f for f in _TRAIN_FN_HINTS if f in funcs), None)
        if not train:
            continue
        mod_path = p.relative_to(root).with_suffix("")
        module = ".".join(mod_path.parts)
        predict = next((f for f in _PREDICT_FN_HINTS if f in funcs), None)
        cand = {"module": module, "file": str(p.relative_to(root)).replace("\\", "/"),
                "train": train, "train_params": funcs[train],
                "predict": predict, "predict_params": funcs.get(predict or "", [])}
        # 参数个数越多越像完整的训练 API（(x, y, models) 优于 (x)）
        if best is None or len(cand["train_params"]) > len(best["train_params"]):
            best = cand
    return best


def build_smoke_plan(manifest: RepoManifest, root: Optional[Path], out_dir: Path,
                     seed: int = 0, entrypoint: str = "") -> Dict[str, Any]:
    """产出 smoke 计划：命令、环境、阻塞项、依赖清单（供 M3-⑤ 复用）。"""
    root = Path(root) if root else Path(manifest.root or ".")
    entry = entrypoint or (manifest.entrypoints[0] if manifest.entrypoints else "")
    documented = readme_example_command(manifest.readme, entry) if entry else None
    notes: List[str] = []
    blockers: List[str] = []

    if not entry:
        blockers.append("未在仓库里定位到入口脚本（main/train/run 等）")
    if documented:
        command, reduce_notes = reduce_scale(documented, root, Path(out_dir) / "outputs")
        notes.append("smoke 命令取自 README 已文档化的示例（不猜 flag）")
        notes.extend(reduce_notes)
        source = "readme-example"
    else:
        command = [entry] if entry else []
        for flag, value in _SCALE_FLAGS.items():
            command.extend([flag, value])
        if entry:
            notes.append("README 未给出示例命令：退回「入口 + 已知缩小 flag」，参数可能需要人工确认")
            source = "inferred"
        else:
            source = "none"
    if command and not command[0].endswith(".py"):
        command = [entry] + command

    # 若命令里没有输出目录开关、而入口确实支持一个，就自动补上：
    # 否则产物会直接落进仓库根目录（既污染仓库，也让结果收集不到）。
    surface = argparse_flags((root / entry) if entry else None)
    work_out = Path(out_dir) / "outputs"
    if command and not any(f in command for f in _OUT_FLAGS):
        for flag in _OUT_FLAGS:
            if flag in surface:
                command.extend([flag, str(work_out)])
                notes.append("命令未指定输出目录，已按入口声明的 `%s` 补上" % flag)
                break

    # 入口文件的直接 import：既用于依赖预检，也用于判断是否缺「不可 pip 装」的重型依赖。
    # 注意排除**仓库自己的模块**（实测 PBEE 的 `from modules.xxx import *` 会被
    # 误当成第三方依赖，还给出 `pip install modules` 这种错误提示）。
    required: List[str] = []
    if entry and root and (root / entry).exists():
        try:
            src = (root / entry).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            src = ""
        from ..deps import scan_imports, _stdlib_names  # noqa: PLC0415

        required = sorted(
            m for m in scan_imports([src])
            if m not in _stdlib_names() and not _is_repo_local(m, root))

    # D：依赖声明先归一（去重/去注释/丢掉本地安装行），并剔除仓库自带模块——
    # 否则 `evaluation`、`sascorer` 这类本地包会被建议 `pip install`。
    from ..deps import clean_requirements, requirement_name  # noqa: PLC0415

    declared_clean = [
        spec for spec in clean_requirements(manifest.requirements)
        if requirement_name(spec) and not _is_repo_local(requirement_name(spec), root)
    ]

    return {
        "entrypoint": entry,
        "command": command,
        "cwd": str(root),
        "source": source,
        "env": smoke_env(seed),
        "timeout_s": SMOKE_TIMEOUT_S,
        "required_modules": required,
        "declared_requirements": declared_clean[:40],
        "blockers": blockers,
        "notes": notes,
        "mode": "entry",
    }


def add_library_mode(plan: Dict[str, Any], root: Optional[Path],
                     missing: Sequence[str]) -> Dict[str, Any]:
    """命令行入口跑不动时，看能否改走「调用仓库自带训练 API」的 library 模式。

    触发条件（确定性）：入口的依赖里有**非 PyPI** 项（装不上，如 pyrosetta），
    或压根没定位到入口。此时若仓库暴露了可调用的训练函数，就切到 library 模式；
    否则保持 entry 模式并把阻塞原因写清楚（不假装能跑）。

    实测补充：普通「缺 PyPI 依赖」时**不应**触发 library 模式——peptide2mol 的
    训练 API 自身 import 了仓库子目录 `src`，一跑就变成 `failed`，反而掩盖了
    「缺 torch」这个真正可行动的阻塞原因。
    """
    if plan.get("mode") == "library" or not plan.get("blockers"):
        return plan
    from ..deps import NON_PYPI  # noqa: PLC0415

    blocked_by_install = any(m in NON_PYPI for m in (missing or [])) or not plan.get("entrypoint")
    if not blocked_by_install:
        return plan
    api = find_training_api(root)
    if not api or not api.get("train"):
        return plan
    plan["library"] = api
    plan["mode"] = "library"
    plan["notes"].append(
        "入口不可用（%s），改走 library 模式：直接调用仓库自带的 `%s.%s` —— "
        "这只是**代码路径 smoke**，不是论文数值复现。"
        % ("；".join(plan["blockers"])[:120], api["module"], api["train"]))
    return plan


_SMOKE_RUNNER = '''"""情形 A 的 smoke 运行器（由 paper2code 生成）。

用**缩减后的参数**跑仓库入口一次，并把结果落盘到 ``result.json``；
不联网、不装依赖（依赖是否齐备由 paper2code 预先判定）。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    cfg = json.loads((HERE / "smoke_config.json").read_text(encoding="utf-8"))
    if cfg.get("blockers"):
        _write({"status": "blocked", "reason": "；".join(cfg["blockers"]), "metrics": {}})
        return
    env = dict(os.environ)
    env.update(cfg.get("env") or {})
    cmd = [sys.executable] + list(cfg.get("command") or [])
    try:
        r = subprocess.run(cmd, cwd=cfg.get("cwd") or str(HERE), capture_output=True,
                           text=True, timeout=int(cfg.get("timeout_s") or 180),
                           env=env, encoding="utf-8", errors="replace")
    except Exception as exc:
        _write({"status": "failed", "reason": "%s: %s" % (type(exc).__name__, exc),
                "command": cmd, "metrics": {}})
        return
    metrics = _collect_numbers(HERE / "outputs")
    _write({"status": "ran" if r.returncode == 0 else "failed",
            "returncode": r.returncode, "command": cmd,
            "stdout_tail": (r.stdout or "")[-2000:], "stderr_tail": (r.stderr or "")[-2000:],
            "metrics": metrics})
    print("SMOKE_DONE")


def _collect_numbers(base):
    """尽力收集 smoke 产出的数值（json/csv），供后续与论文表格对标。"""
    import csv
    out = {}
    if not base.exists():
        return out
    for p in sorted(base.rglob("*")):
        if p.suffix == ".json":
            try:
                out[p.name] = json.loads(p.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
        elif p.suffix == ".csv":
            try:
                with p.open(encoding="utf-8", errors="replace") as fh:
                    rows = list(csv.reader(fh))[:6]
                out[p.name] = rows
            except Exception:
                continue
    return out


def _write(payload):
    """写结果文件；序列化失败也必须留下一条可读的原因，而不是让文件消失。"""
    try:
        text = json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2)
    except Exception as exc:
        text = json.dumps({"status": "failed",
                           "reason": "结果序列化失败：%s: %s" % (type(exc).__name__, exc)},
                          ensure_ascii=False, indent=2)
    (HERE / "result.json").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
'''

_SMOKE_README = """# 情形 A：仓库 smoke 运行

由 paper2code 自动生成。目标是**用缩小规模**跑通仓库入口一次，从而知道
「仓库在这台机器上到底能不能跑」，而不是只给一份待人工执行的脚本。

- 模式：{mode}
- 命令来源：{source}
- 入口：`{entry}`
- smoke 命令：`{command}`

```bash
python run_smoke.py     # 结果写入 result.json
```

阻塞项：{blockers}
备注：{notes}
"""


_SMOKE_RUNNER_LIB = '''"""情形 A 的 library-mode smoke（由 paper2code 生成）。

入口脚本跑不动时（实测 PBEE 的 `pbee.py` 需要 pyrosetta——非 PyPI 且要 Rosetta
许可），改为**直接调用仓库自带的训练 API**，在一份缩规模输入上确认它的 ML 核心
能在这个环境里跑起来。

**这是代码路径 smoke，不是论文数值复现**：输入是合成小样本，真实特征仍需论文/仓库
各自的预处理（PBEE 需要 pyrosetta 计算 Rosetta 描述符）。
"""
import importlib
import json
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARK = "__P2C_RESULT__"


def _tiny_dataset(n=120, d=4, seed=0):
    """确定性小样本：y 是 x 的线性组合 + 噪声（可校验拟合质量）。"""
    import random
    rnd = random.Random(seed)
    xs, ys = [], []
    for _ in range(n):
        row = [rnd.gauss(0, 1) for _ in range(d)]
        ys.append(1.5 * row[0] - 0.7 * row[1] + 0.3 * row[2] + rnd.gauss(0, 0.05))
        xs.append(row)
    return xs, ys


def _default_models():
    """仓库自带的 train 函数通常接收一个 estimator 列表（如 PBEE 的 train_base_models）。

    优先用 sklearn；**没有 sklearn 时退化为内置的 stdlib estimator**，这样 library
    smoke 在任何环境都能跑（「离线永远可用」是本项目的硬约束），只是拟合能力弱一些。
    """
    try:
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.linear_model import LinearRegression, Ridge
        from sklearn.tree import DecisionTreeRegressor
        return [LinearRegression(), Ridge(alpha=1.0), DecisionTreeRegressor(random_state=0),
                RandomForestRegressor(n_estimators=8, random_state=0)]
    except Exception:
        return [_MeanModel(), _LinModel(0), _LinModel(1)]


class _MeanModel:
    """stdlib 兜底：常数均值预测。"""

    def fit(self, x, y):
        self.mean = (sum(y) / len(y)) if len(y) else 0.0
        return self

    def predict(self, x):
        return [self.mean for _ in range(len(x))]


class _LinModel:
    """stdlib 兜底：拿第 ``col`` 列做最小二乘。"""

    def __init__(self, col=0):
        self.col = col

    def fit(self, x, y):
        pairs = [(float(row[self.col]), float(t)) for row, t in zip(x, y)]
        n = len(pairs)
        if n < 2:
            self.a, self.b = 0.0, (sum(t for _, t in pairs) / n if n else 0.0)
            return self
        mx = sum(p for p, _ in pairs) / n
        my = sum(t for _, t in pairs) / n
        den = sum((p - mx) ** 2 for p, _ in pairs)
        self.a = (sum((p - mx) * (t - my) for p, t in pairs) / den) if den else 0.0
        self.b = my - self.a * mx
        return self

    def predict(self, x):
        return [self.a * float(row[self.col]) + self.b for row in x]


def _rmse(pred, obs):
    return (sum((p - o) ** 2 for p, o in zip(pred, obs)) / max(1, len(obs))) ** 0.5


def main():
    cfg = json.loads((HERE / "smoke_config.json").read_text(encoding="utf-8"))
    api = cfg.get("library") or {}
    repo = str(cfg.get("cwd") or ".")
    sys.path.insert(0, repo)
    out = {"status": "failed", "mode": "library", "metrics": {},
           "note": "代码路径 smoke（合成输入），不是论文数值复现"}
    try:
        import numpy as np
        mod = importlib.import_module(api["module"])
        train = getattr(mod, api["train"])
        predict = getattr(mod, api.get("predict") or "", None) if api.get("predict") else None

        seed = int((cfg.get("env") or {}).get("P2C_SEED") or 0)
        xs, ys = _tiny_dataset(seed=seed)
        X, y = np.array(xs, dtype=float), np.array(ys, dtype=float)
        split = int(len(X) * 0.8)
        Xtr, ytr, Xte, yte = X[:split], y[:split], X[split:], y[split:]

        if len(api.get("train_params") or []) >= 3:
            base = train(Xtr, ytr, _default_models())          # 仓库自己的训练函数
        else:
            base = train(Xtr, ytr)
        out["trained_models"] = len(base) if isinstance(base, (list, tuple, dict)) else 1

        metrics = {"n_train": float(split), "n_test": float(len(Xte)),
                   "trained_models": float(out["trained_models"])}
        if predict is not None and isinstance(base, list) and len(api.get("predict_params") or []) == 3:
            try:
                from sklearn.linear_model import LinearRegression
                meta = LinearRegression().fit(np.column_stack([m.predict(Xtr) for _, m in base]), ytr)
                meta_kind = "sklearn.LinearRegression"
            except Exception:                       # 没有 sklearn：用内置均值模型当元模型
                meta = _MeanModel().fit(None, list(np.asarray(
                    [m.predict(Xtr) for _, m in base]).mean(axis=0)))
                meta_kind = "stdlib.mean"
            metrics["meta_model"] = 1.0 if meta_kind.startswith("sklearn") else 0.0
            predictions = predict(Xte, base, meta)   # 仓库自己的预测函数
            if isinstance(predictions, dict) and "SuperLearner" in predictions:
                pred = np.asarray(predictions["SuperLearner"], dtype=float).ravel()
                metrics["rmse_superlearner"] = _rmse(list(pred[:len(yte)]), list(yte))
                # 只留可 JSON 化的预览：ndarray 直接进 json.dumps 会抛 TypeError
                metrics["pred_preview"] = [float(x) for x in pred[:3]]
        out["status"] = "ran"
        out["metrics"] = metrics
    except Exception as exc:
        out["reason"] = "%s: %s" % (type(exc).__name__, exc)
        out["traceback"] = traceback.format_exc()[-1500:]
    # 序列化本身也可能失败（实测 ndarray 漏进来会抛 TypeError 并让结果文件消失），
    # 所以这一步必须自带兜底：宁可写成「序列化失败」，也不能什么都留不下。
    try:
        (HERE / "result.json").write_text(
            json.dumps(out, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
    except Exception as exc:
        (HERE / "result.json").write_text(json.dumps(
            {"status": "failed", "mode": "library",
             "reason": "结果序列化失败：%s: %s" % (type(exc).__name__, exc)},
            ensure_ascii=False, indent=2), encoding="utf-8")
    print("SMOKE_DONE")


if __name__ == "__main__":
    main()
'''


def write_smoke(out_dir: Path, plan: Dict[str, Any]) -> Dict[str, Any]:
    """把 smoke 计划与运行器写到 ``out_dir``；返回文件清单。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "smoke_config.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    runner_src = _SMOKE_RUNNER_LIB if plan.get("mode") == "library" else _SMOKE_RUNNER
    (out_dir / "run_smoke.py").write_text(runner_src, encoding="utf-8")
    (out_dir / "README.md").write_text(
        _SMOKE_README.format(
            mode=plan.get("mode") or "entry",
            source=plan.get("source"), entry=plan.get("entrypoint") or "—",
            command=" ".join(plan.get("command") or []) or "—",
            blockers="；".join(plan.get("blockers") or []) or "无",
            notes="；".join(plan.get("notes") or []) or "无"),
        encoding="utf-8")
    return {"dir": str(out_dir), "files": sorted(
        str(p.name) for p in out_dir.iterdir() if p.is_file())}

