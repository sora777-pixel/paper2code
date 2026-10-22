"""依赖解析（roadmap M3-⑤）。

**做什么**：扫描生成代码与 plan 声明的依赖 → 判定需要 stdlib / 轻量（numpy 等）/ 重型
（torch 等）→ 检查本机是否可用 → 缺依赖时给出**一键安装提示**，并说明可降级到 stdlib。

**为什么不自动装**：本项目的一条硬约束是「离线永远可用」。自动 `pip install` 会引入网络与
环境破坏，因此这里只**报告**与**建议**，绝不静默安装；由人或上层流程决定。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

#: import 名 -> 安装名（pip 包名与 import 名不一致的常见几例）
PACKAGE_NAMES = {
    "sklearn": "scikit-learn",
    "cv2": "opencv-python",
    "PIL": "pillow",
    "yaml": "pyyaml",
    "gym": "gym",
    "torch": "torch",
    "torchvision": "torchvision",
    "hydra": "hydra-core",
    "torch_geometric": "torch-geometric",
    "pytorch_lightning": "pytorch-lightning",
    "skimage": "scikit-image",
    "rdkit": "rdkit",
    "crewai_cli": "crewai-cli",
    "crewai_core": "crewai-core",
}

#: 安装名 -> import 名。**必需**：依赖声明里写的是 pip 包名（如 `scikit-learn=1.3.0`），
#: 而可用性要按 import 名查（`sklearn`）；不做这层映射就会把已装好的包误报成缺失。
PACKAGE_TO_MODULE = {
    "scikit-learn": "sklearn",
    "scikit_learn": "sklearn",
    "scikit-image": "skimage",
    "opencv-python": "cv2",
    "pillow": "PIL",
    "pyyaml": "yaml",
    "python-dateutil": "dateutil",
    "msgpack-python": "msgpack",
    "hydra-core": "hydra",
    "torch-geometric": "torch_geometric",
    "pytorch-lightning": "pytorch_lightning",
    "rdkit-pypi": "rdkit",
    "crewai-cli": "crewai_cli",
    "crewai-core": "crewai_core",
}

#: 依赖声明里可以直接跳过的行（本地安装 / 版本控制 / 索引配置等）
_REQ_LINE_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(.*)$")

#: 重型依赖：装不上或装起来很贵的
HEAVY = {"torch", "tensorflow", "jax", "transformers", "torchvision", "cupy"}
#: D：自动安装隔离依赖时的重型包清单（含「会把 torch/rdkit 一起拖下来」的包）。
#: 默认不自动装，必须在 repo_install_heavy / P2C_REPO_INSTALL_HEAVY 显式放行。
HEAVY_INSTALL = HEAVY | {
    "rdkit", "torch-geometric", "torch_geometric", "pytorch-lightning",
    "pytorch_lightning", "lightning", "torchaudio", "apex",
}

#: **不是 PyPI 包**（或必须走官方安装器）的依赖：给 `pip install` 会误导人
NON_PYPI = {
    "pyrosetta": "pyrosetta 不是 PyPI 包：需按官方说明安装（`python -c \"import pyrosetta_installer; "
                 "pyrosetta_installer.install_pyrosetta()\"`），且需要 Rosetta 许可",
    "rosetta": "Rosetta 需从官方获取许可后安装，不能 pip 安装",
    "openmm": "openmm 建议按官方 conda 渠道安装",
}

_IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_]*)")


def _stdlib_names() -> Set[str]:
    names = getattr(__import__("sys"), "stdlib_module_names", None)
    return set(names) if names else set()


def scan_imports(sources: Iterable[str]) -> Set[str]:
    """扫出代码里的顶层 import 模块（跳过相对 import）。"""
    out: Set[str] = set()
    for src in sources:
        for line in (src or "").splitlines():
            m = _IMPORT_RE.match(line)
            if m:
                out.add(m.group(1))
    return out


def requirement_name(spec: str) -> str:
    """从一条 pip 声明里取包名：``numpy<2.0.0`` → ``numpy``。"""
    m = _REQ_LINE_RE.match(str(spec or "").strip())
    return m.group(1) if m else ""


def clean_requirements(declared: Sequence[str]) -> List[str]:
    """归一依赖声明：去重、去注释、丢掉本地/VCS 行，保留可安装的版本约束。

    实测真实仓库的 requirements 里混着重复项、``numpy<2.0.0``、``-e .``、
    ``--index-url`` 与注释；原样塞给 ``pip install`` 会失败或产生误导性的
    「缺失包」名字。这里只做保守过滤，不改写版本号。
    """
    out: List[str] = []
    seen: Set[str] = set()
    for raw in declared or ():
        s = str(raw or "").strip()
        if not s or s.startswith("#"):
            continue
        first = s.split()[0] if s.split() else s
        # 本地目录 / VCS / 索引配置 / 递归 -r 都不是「包名」
        if s.startswith("-") or first.startswith((".", "git+", "http://", "https://", "file:")):
            continue
        s = s.split(" #", 1)[0].strip()
        name = requirement_name(s)
        if not name:
            continue
        key = name.lower().replace("_", "-")
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _declared_modules(declared: Sequence[str]) -> Set[str]:
    out: Set[str] = set()
    for d in clean_requirements(declared):
        name = requirement_name(d)
        if name:
            # pip 包名 -> import 名（`scikit-learn` -> `sklearn`），否则可用性会查错模块
            out.add(PACKAGE_TO_MODULE.get(name.lower(), name))
    return out


def _available(module: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def available_in(python_exe: str, modules: Iterable[str]) -> Dict[str, bool]:
    """查询**另一个解释器**（通常是独立 venv）里这些模块是否可用。

    复现情形 A 时，依赖不该装在主解释器里；用 ``P2C_REPO_VENV`` 指向专用 venv
    后，这一步让预检与真实运行落在同一个环境，避免「预检过了、一跑就 ImportError」。

    入参可以带版本修饰（``scikit-learn=1.3.0``），这里先归一成模块名；并且逐个
    查询都带兜底——一个非法名字不能让整个探测失败（实测会导致「全部报缺」）。
    """
    mods = sorted({m for m in _declared_modules(list(modules)) if m})
    if not mods or not python_exe:
        return {}
    code = (
        "import importlib.util as u, json\n"
        "def has(m):\n"
        "    try:\n"
        "        return bool(u.find_spec(m))\n"
        "    except Exception:\n"
        "        return False\n"
        "print(json.dumps({m: has(m) for m in %r}))\n" % (mods,)
    )
    try:
        import subprocess

        r = subprocess.run([python_exe, "-c", code], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return {}
        return {str(k): bool(v) for k, v in json.loads(r.stdout.strip().splitlines()[-1]).items()}
    except Exception:
        return {}


def analyze(sources: Iterable[str], declared: Sequence[str] = (),
            available_override: Optional[Dict[str, bool]] = None,
            local_modules: Iterable[str] = ()) -> Dict[str, Any]:
    """返回依赖报告：``{level, third_party, available, missing, install_hint, degrade_to_stdlib}``。

    ``local_modules``：demo 目录里**自带的模块**（如模型自己写的 ``as_topic.py``）。
    不算本地模块就会把它们误报成「缺失的第三方依赖」并建议 ``pip install as_topic``——
    实测在真实 LLM 路径上真的会发生，纯属误导。
    """
    stdlib = _stdlib_names()
    modules = scan_imports(sources) | _declared_modules(declared)
    local = {str(m).strip() for m in (local_modules or ()) if str(m).strip()}
    third = sorted(m for m in modules if m and m not in stdlib and m not in local)

    if available_override:
        avail = [m for m in third if available_override.get(m, False)]
        missing = [m for m in third if m not in avail]
    else:
        avail = [m for m in third if _available(m)]
        missing = [m for m in third if m not in avail]

    if any(m in HEAVY for m in third):
        level = "heavy"
    elif third:
        level = "light"
    else:
        level = "stdlib"

    packages = sorted({PACKAGE_NAMES.get(m, m) for m in missing if m not in NON_PYPI})
    manual = [NON_PYPI[m] for m in missing if m in NON_PYPI]
    return {
        "level": level,
        "third_party": third,
        "available": avail,
        "missing": missing,
        "pip_missing": [m for m in missing if m not in NON_PYPI],
        "manual_missing": manual,
        "local_modules": sorted(m for m in modules if m in local),
        "declared_clean": clean_requirements(declared),
        "install_hint": ("pip install %s" % " ".join(packages)) if packages else "",
        "manual_hint": "；".join(manual),
        # 缺依赖时的出路：走 stdlib 骨架（模板与离线脚手架都只用 stdlib）
        "degrade_to_stdlib": bool(missing),
    }


def local_module_names(paths: Iterable[str]) -> List[str]:
    """从文件名推「本地模块名」：``demo/as_topic.py`` → ``as_topic``。

    ``__init__.py`` 与 ``run_demo.py`` 这类不是被 import 的模块名，按需排除；
    这里只做保守推断（同目录下 ``x.py`` 可被 ``import x``），够用且不会误伤。
    """
    out: List[str] = []
    for p in paths or ():
        name = str(p or "").replace("\\", "/").rsplit("/", 1)[-1]
        if not name.endswith(".py"):
            continue
        stem = name[:-3]
        if stem and stem not in ("__init__", "__main__") and stem not in out:
            out.append(stem)
    return out


def describe(report: Dict[str, Any]) -> str:
    """一句话摘要，供计划自检 / 报告引用。"""
    level = {"stdlib": "仅 stdlib", "light": "轻量依赖", "heavy": "重型依赖"}.get(
        report.get("level"), report.get("level") or "?")
    third = report.get("third_party") or []
    if not third:
        return "%s——任何环境都能直接跑" % level
    missing = report.get("missing") or []
    if missing:
        return "%s：%s；**缺 %s**（%s，或降级为 stdlib 骨架）" % (
            level, "、".join(third), "、".join(missing), report.get("install_hint") or "无安装提示")
    return "%s：%s（本机已具备）" % (level, "、".join(third))


def warnings_for(report: Dict[str, Any]) -> List[str]:
    missing = report.get("missing") or []
    if not missing:
        return []
    hint = report.get("install_hint") or ""
    manual = report.get("manual_hint") or ""
    return [
        "demo 需要未安装的依赖：%s。%s%s%s"
        % ("、".join(missing),
           ("可执行 `%s` 安装；" % hint) if hint else "",
           ("其中 %s；" % manual) if manual else "",
           "也可降级为 stdlib 骨架（离线模板与脚手架只用 stdlib）。")
    ]
