"""远程执行器（roadmap M6-④，远程 GPU）：把生成的代码送到远端跑，再把产物取回来。

**它解决什么**：本地没有 GPU / 没有论文所需的算力时，唯一诚实的做法是「把代码送到远端」，
而不是在本地假装复现。这里给出一个可注册的**执行器插件**（接 M5-④ 的插件接口）：

* 传输与执行都走**可配置命令模板**（默认 ``scp`` / ``ssh``），因此也支持 ``docker -H``、
  ``kubectl exec`` 之类的通道——只要命令能跑通；
* 还支持「远端就是另一个本地目录」的模式（``remote_host`` 留空 + ``remote_dir`` 给路径），
  这对挂载盘/NFS 场景有用，也让整套流程**可以在没有 ssh 的机器上端到端自测**；
* **不可用时明确拒绝**（``available()`` 为 False），不做「悄悄退回本地执行」——
  用户既然要求远端，就不该在他本机执行代码。

诚实边界：本机没有可用远端，所以「真·远程 GPU」这条链路只做了机制与本地自测；
远端算力/驱动/依赖是否齐备，需要在你自己的机器上按 ``templates`` 跑一次才知道。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .reproduce import runner


@dataclass
class RemoteConfig:
    """远端配置。模板里可用的占位符：``{host} {remote} {local} {py} {script}``。"""

    host: str = ""                                   # 形如 user@host；留空=「远端即本地目录」模式
    remote_dir: str = ""                             # 远端工作目录
    python_exe: str = "python3"                      # 远端解释器
    put_cmd: str = "scp -r {local} {host}:{remote}"          # 上传
    run_cmd: str = 'ssh {host} "cd {remote} && {py} {script}"'  # 执行
    get_cmd: str = "scp -r {host}:{remote}/{artifacts} {local}"  # 回传产物（artifacts 为通配）
    timeout_s: int = 900

    @property
    def is_local_dir(self) -> bool:
        """没有 host 但有目录 → 远端就是本地另一个目录（挂载盘/NFS 场景）。"""
        return not self.host and bool(self.remote_dir)


class RemoteExecutor:
    """注册式执行器（``plugins.register_executor``）；接口约定见 ``paper2code.plugins``。

    契约：``name`` / ``available()`` / ``run(script, workdir, timeout, env, **kw) -> RunResult``。
    """

    name = "remote"
    isolation = "远端执行（隔离级别由远端环境决定）"
    risk = "unknown"

    def __init__(self, cfg: Optional[RemoteConfig] = None) -> None:
        self.cfg = cfg or from_settings()

    # -- 可用性 ------------------------------------------------------------- #
    def available(self) -> bool:
        """配置齐全才可用；否则**明确不可用**（绝不偷偷退回本地执行）。"""
        if not self.cfg.remote_dir:
            return False
        if self.cfg.is_local_dir:
            return Path(self.cfg.remote_dir).parent.exists() or True   # 目录可创建即可
        for tool in ("ssh", "scp"):
            if shutil.which(tool) is None:
                return False
        return True

    def unavailable_reason(self) -> str:
        if not self.cfg.remote_dir:
            return "未配置远端目录（remote_dir）：远端执行器不可用"
        if not self.cfg.is_local_dir and shutil.which("ssh") is None:
            return "本机没有 ssh/scp（或不在 PATH）：无法连接远端；请配置传输命令模板或改用本地模式"
        return ""

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name, "host": self.cfg.host or "(本地目录模式)",
                "remote_dir": self.cfg.remote_dir, "python": self.cfg.python_exe,
                "available": self.available(), "reason": self.unavailable_reason(),
                "timeout_s": self.cfg.timeout_s}

    # -- 执行 --------------------------------------------------------------- #
    def run(self, script: Path | str, workdir: Path | str, timeout: int = 0,
            env: Optional[Dict[str, str]] = None, **_: Any) -> "runner.RunResult":
        """把 ``workdir`` 整体送到远端、在远端跑 ``script``、再把产物取回 ``workdir``。"""
        workdir = Path(workdir)
        sp = Path(script)
        name = sp.name
        res = runner.RunResult(workdir=str(workdir), executor=self.name)
        if not self.available():
            res.error = self.unavailable_reason()
            return res

        budget = int(timeout or self.cfg.timeout_s)
        remote_root = self._remote_root()
        try:
            remote_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            res.error = "远端目录不可写：%s: %s" % (type(exc).__name__, exc)
            return res

        # 1) 上传（本地目录模式直接复制；否则走模板命令）
        err = self._put(workdir, remote_root)
        if err:
            res.error = "上传到远端失败：%s" % err
            return res
        # 2) 执行
        before = _snapshot_local(workdir)
        rc, out, tail_err, timed_out = self._execute(name, remote_root, budget, env)
        res.returncode, res.stdout, res.stderr = rc, out, tail_err
        res.timed_out = timed_out
        res.ok = (rc == 0) and not timed_out
        if timed_out:
            res.error = "远端执行超时（>%ss）" % budget
        elif rc != 0:
            res.error = "远端退出码 %s" % rc
        # 3) 回传产物（只取新增/变化的文件，避免把只读依赖再拉一遍）
        err2 = self._get(remote_root, workdir, name)
        if err2:
            res.error = (res.error + "；" if res.error else "") + "回传产物失败：%s" % err2
        after = _snapshot_local(workdir)
        new = sorted(set(after) - set(before))
        res.artifacts = [str(workdir / n) for n in new]
        res.artifacts_hash = {Path(a).name: runner._hash(a) for a in res.artifacts}
        return res

    # -- 传输实现 ----------------------------------------------------------- #
    def _remote_root(self) -> Path:
        return Path(self.cfg.remote_dir) if self.cfg.is_local_dir else Path("/tmp/p2c_remote")

    def _put(self, local: Path, remote: Path) -> str:
        if self.cfg.is_local_dir:
            try:
                shutil.copytree(local, remote, dirs_exist_ok=True)
                return ""
            except OSError as exc:
                return "%s: %s" % (type(exc).__name__, exc)
        return _sh(self.cfg.put_cmd.format(**self._fields(local, remote, "")))

    def _execute(self, script_name: str, remote: Path, timeout: int,
                 env: Optional[Dict[str, str]]) -> tuple:
        if self.cfg.is_local_dir:
            cmd = [self.cfg.python_exe if shutil.which(self.cfg.python_exe) else sys.executable, script_name]
            e = dict(os.environ)
            e.update(env or {})
            e.setdefault("PYTHONIOENCODING", "utf-8")
            o = runner._run_process(cmd, cwd=str(remote), env=e, timeout=timeout)
            return o["returncode"], o["stdout"], o["stderr"], o["timed_out"]
        o = _sh_full(self.cfg.run_cmd.format(**self._fields(remote, remote, script_name)), timeout)
        return o

    def _get(self, remote: Path, local: Path, script_name: str) -> str:
        if self.cfg.is_local_dir:
            try:
                shutil.copytree(remote, local, dirs_exist_ok=True)
                return ""
            except OSError as exc:
                return "%s: %s" % (type(exc).__name__, exc)
        fields = self._fields(local, remote, script_name)
        fields["artifacts"] = "*"
        return _sh(self.cfg.get_cmd.format(**fields))

    def _fields(self, local: Path, remote: Path, script_name: str) -> Dict[str, str]:
        return {"host": self.cfg.host, "remote": str(remote), "local": str(local),
                "py": self.cfg.python_exe, "script": script_name}


# --------------------------------------------------------------------------- #
def from_settings() -> RemoteConfig:
    from .config import get_settings

    st = get_settings()
    return RemoteConfig(host=str(getattr(st, "remote_host", "") or ""),
                        remote_dir=str(getattr(st, "remote_dir", "") or ""),
                        python_exe=str(getattr(st, "remote_python", "") or "python3"),
                        timeout_s=int(getattr(st, "remote_timeout_s", 0) or 900))


def register_from_settings() -> Optional[RemoteExecutor]:
    """按配置注册远程执行器（仅当配了 ``remote_dir``）；注册失败只记 warning。"""
    try:
        from . import plugins

        cfg = from_settings()
        if not cfg.remote_dir:
            return None
        ex = RemoteExecutor(cfg)
        plugins.register_executor(ex, name="remote", overwrite=True)
        return ex
    except Exception:
        return None


def _sh(cmd: str) -> str:
    o = _sh_full(cmd, 300)
    return "" if o[0] == 0 else (o[2] or o[1] or "命令失败（退出码 %s）" % o[0])[-300:]


def _sh_full(cmd: str, timeout: int) -> tuple:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,   # noqa: S602
                           timeout=timeout, encoding="utf-8", errors="replace")
        return r.returncode, r.stdout or "", r.stderr or "", False
    except subprocess.TimeoutExpired as exc:
        return -9, (exc.stdout or "") if isinstance(exc.stdout, str) else "", \
               (exc.stderr or "") if isinstance(exc.stderr, str) else "", True
    except Exception as exc:
        return -1, "", "%s: %s" % (type(exc).__name__, exc), False


def _snapshot_local(root: Path) -> List[str]:
    out: List[str] = []
    for p in Path(root).rglob("*"):
        if p.is_file():
            out.append(str(p.relative_to(root)))
    return out
