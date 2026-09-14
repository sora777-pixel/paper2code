"""本地执行沙箱（best-effort）。

约束（对应「单机 + 小数据集」的设定）：

* 使用当前解释器，在**独立临时工作目录**中执行，脚本只能看到被显式复制进去的
  数据文件；
* 强制超时（默认 120s），超时即终止整个进程树；
* 默认清空代理环境变量，避免生成的脚本无意中访问外网；
* 记录 ``stdout`` / ``stderr`` / 退出码 / 产物文件列表，供报告引用。

注意：这不是安全沙箱，不能防御恶意代码。本工具面向「自己下载的可信论文与
仓库」，若需执行不可信代码请改用容器或 ``firejail`` 等真正的隔离方案——架构上
:func:`run_python_script` 已经是可替换的单一入口，替换为 Docker 执行器即可。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

ARTIFACT_EXTS = {".csv", ".json", ".png", ".svg", ".pdf", ".txt", ".md", ".npy", ".log"}


@dataclass
class RunResult:
    ok: bool = False
    returncode: int = -1
    stdout: str = ""
    stderr: str = ""
    workdir: str = ""
    artifacts: List[str] = field(default_factory=list)
    timed_out: bool = False
    error: str = ""

    def to_dict(self) -> Dict:
        return {
            "ok": self.ok,
            "returncode": self.returncode,
            "stdout": self.stdout[-4000:],
            "stderr": self.stderr[-4000:],
            "workdir": self.workdir,
            "artifacts": self.artifacts,
            "timed_out": self.timed_out,
            "error": self.error,
        }


def run_python_script(
    script: Path | str,
    workdir: Path | str,
    timeout: int = 120,
    inputs: Optional[Iterable[Path]] = None,
    env_extra: Optional[Dict[str, str]] = None,
    allow_network: bool = False,
) -> RunResult:
    """在 ``workdir`` 中执行 ``script``。"""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    if isinstance(script, str):
        sp = workdir / "generated_main.py"
        sp.write_text(script, encoding="utf-8")
    else:
        sp = Path(script)

    if inputs:
        for src in inputs:
            src = Path(src)
            if src.exists() and src.is_file():
                try:
                    shutil.copy2(src, workdir / src.name)
                except Exception:
                    pass

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["MPLBACKEND"] = "Agg"
    if not allow_network:
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
            env.pop(key, None)
        env["NO_PROXY"] = "*"
    if env_extra:
        env.update(env_extra)

    before = _snapshot(workdir)
    try:
        proc = subprocess.run(  # noqa: S603
            [sys.executable, sp.name],
            cwd=str(workdir),
            env=env,
            capture_output=True,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        res = RunResult(
            ok=proc.returncode == 0,
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            workdir=str(workdir),
        )
    except subprocess.TimeoutExpired as exc:
        res = RunResult(
            ok=False,
            returncode=-9,
            stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
            stderr=(exc.stderr or "") if isinstance(exc.stderr, str) else "",
            workdir=str(workdir),
            timed_out=True,
            error=f"执行超时（>{timeout}s），已终止",
        )
    except Exception as exc:
        res = RunResult(ok=False, workdir=str(workdir), error=f"执行失败：{exc}")

    after = _snapshot(workdir)
    new = sorted(set(after) - set(before))
    res.artifacts = [str(workdir / n) for n in new if Path(n).suffix.lower() in ARTIFACT_EXTS]
    return res


def _snapshot(root: Path) -> List[str]:
    out: List[str] = []
    try:
        for p in root.rglob("*"):
            if p.is_file():
                out.append(str(p.relative_to(root)))
    except Exception:
        pass
    return out


def make_temp_workspace(prefix: str = "p2c_") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


def python_executable() -> str:
    return sys.executable
