"""执行沙箱（roadmap M4-②/③）。

两个执行器，同一个单一入口 :func:`run_python_script`（这正是路线图预留的替换点）：

* :class:`~paper2code.safety` 的**静态扫描**先过一遍待运行代码，命中危险能力就**不执行**、
  直接判失败并记录理由（M4-①）；
* ``local``：本机进程执行。能跑任何本机环境（包括复现情形 A 的仓库专用 venv），
  但**不是安全沙箱**——只有超时、清代理、独立工作目录，隔离级别标为「低隔离 / 高风险」；
* ``container``：Docker 执行。默认**无网络**、根文件系统**只读**、宿主只挂载工作目录
  （其余目录一概不可见）、CPU / 内存 / 进程数 / 墙钟时间都有上限。

无论哪个执行器，都会落盘**执行日志**与**产物哈希**，供报告引用与事后审计。

**降级是诚实的**：要求 ``container`` 但 Docker 不可用时，不会假装隔离，而是降级为本地执行、
把 ``degraded`` 与原因写进返回的 ``policy``，由上层写进 warnings 与报告。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .. import control, safety

ARTIFACT_EXTS = {".csv", ".json", ".png", ".svg", ".pdf", ".txt", ".md", ".npy", ".log"}
LOG_DIR_NAME = "exec_logs"

#: 容器执行器用的镜像与它的构建文件。镜像里带上绘图依赖，否则容器模式下「出图率」会假性下降。
#: 基础镜像与 pip 源都可配置：某些网络到 Docker Hub 不通，需要换可用镜像源。
SANDBOX_DEFAULT_BASE = "python:3.12-slim"

#: 拉不到配置的基础镜像时依次兜底尝试的镜像源（顺序即优先级）。
#: 本机实测：`registry-1.docker.io` 直连超时，而下列镜像源可用——所以这份兜底是**默认生效**的，
#: 不需要用户额外设 `P2C_SANDBOX_BASE`。
FALLBACK_BASES: Tuple[str, ...] = (
    "python:3.12-slim",
    "docker.m.daocloud.io/library/python:3.12-slim",
)

#: 构建镜像时 pip 源兜底（空 = 默认 PyPI）。
FALLBACK_PIP_INDEXES: Tuple[str, ...] = ("", "https://pypi.tuna.tsinghua.edu.cn/simple")

#: 最近一次镜像构建/检查的实际结果（供 CLI、报告如实展示「到底用了哪个基础镜像」）
IMAGE_INFO: Dict[str, Any] = {"image": "", "base": "", "pip_index": "", "built": False,
                              "attempts": [], "error": ""}


def sandbox_dockerfile(base: str = SANDBOX_DEFAULT_BASE, pip_index: str = "") -> str:
    idx = " -i %s" % pip_index if pip_index else ""
    return (
        "FROM %s\n"
        "RUN pip install --no-cache-dir --timeout 180%s numpy matplotlib\n"
        "ENV PYTHONDONTWRITEBYTECODE=1 MPLBACKEND=Agg\n"
    ) % (base, idx)


# --------------------------------------------------------------------------- #
# 结果与策略
# --------------------------------------------------------------------------- #
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
    # -- M4 新增：隔离与审计信息 ------------------------------------------- #
    executor: str = ""                                        # local | container | ""
    blocked: bool = False                                     # 被静态扫描拦下，从未执行
    scan: Dict[str, Any] = field(default_factory=dict)         # 静态扫描结论
    policy: Dict[str, Any] = field(default_factory=dict)       # 本次实际生效的沙箱策略
    artifacts_hash: Dict[str, str] = field(default_factory=dict)  # 产物 sha256（前 16 位）
    log: str = ""                                             # 执行日志文件路径
    container: str = ""                                       # 容器名（container 模式）
    cancelled: str = ""                                       # M5-①：被取消/任务超时的原因（非空即中断）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "returncode": self.returncode,
            "stdout": self.stdout[-4000:],
            "stderr": self.stderr[-4000:],
            "workdir": self.workdir,
            "artifacts": self.artifacts,
            "timed_out": self.timed_out,
            "error": self.error,
            "executor": self.executor,
            "blocked": self.blocked,
            "scan": self.scan,
            "policy": self.policy,
            "artifacts_hash": self.artifacts_hash,
            "log": self.log,
            "cancelled": self.cancelled,
        }


@dataclass
class SandboxPolicy:
    """本次执行实际生效的隔离级别——要在报告 / UI 上**如实**展示。"""

    requested: str = "local"     # 配置里要求的模式
    mode: str = "local"          # 实际使用的执行器
    isolation: str = "低隔离（本机进程，无隔离边界）"
    risk: str = "high"           # high | low
    network: str = "无网络（清空代理环境变量）"
    reason: str = ""             # 降级原因（空 = 按配置生效）
    degraded: bool = False
    image: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    def label(self) -> str:
        return "%s（%s，风险：%s）" % (self.mode, self.isolation, "高" if self.risk == "high" else "低")


# --------------------------------------------------------------------------- #
# Docker 可用性 / 镜像
# --------------------------------------------------------------------------- #
_DOCKER_CACHE: Dict[str, Any] = {"checked": False, "ok": False, "err": "", "path": ""}


def docker_path() -> str:
    return shutil.which("docker") or ""


def docker_available(refresh: bool = False) -> bool:
    """探测 Docker 守护进程是否可用（结果缓存，单测可用 ``reset_docker_cache`` 清掉）。"""
    if _DOCKER_CACHE["checked"] and not refresh:
        return bool(_DOCKER_CACHE["ok"])
    exe = docker_path()
    _DOCKER_CACHE.update({"checked": True, "ok": False, "err": "", "path": exe})
    if not exe:
        _DOCKER_CACHE["err"] = "未找到 docker 可执行文件"
        return False
    try:
        r = subprocess.run([exe, "info", "--format", "{{.ServerVersion}}"],  # noqa: S603
                           capture_output=True, text=True, timeout=25)
    except Exception as exc:
        _DOCKER_CACHE["err"] = "docker info 失败：%s: %s" % (type(exc).__name__, exc)
        return False
    if r.returncode != 0:
        _DOCKER_CACHE["err"] = "Docker 守护进程不可用：%s" % (r.stderr or r.stdout or "").strip()[:200]
        return False
    _DOCKER_CACHE["ok"] = True
    _DOCKER_CACHE["version"] = (r.stdout or "").strip()
    return True


def reset_docker_cache() -> None:
    _DOCKER_CACHE.update({"checked": False, "ok": False, "err": "", "path": ""})


def _image_present(exe: str, image: str) -> bool:
    try:
        r = subprocess.run([exe, "image", "inspect", image],  # noqa: S603
                           capture_output=True, text=True, timeout=60)
        return r.returncode == 0
    except Exception:
        return False


def _pull_base(exe: str, base: str, timeout: int = 240) -> str:
    """单独先把基础镜像拉下来——先拉再 build，才能把「源不通」和「pip 装不上」分开处理。"""
    if _image_present(exe, base):
        return ""
    try:
        r = subprocess.run([exe, "pull", base],  # noqa: S603
                           capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
    except Exception as exc:
        return "%s: %s" % (type(exc).__name__, exc)
    return "" if r.returncode == 0 else (r.stderr or r.stdout or "").strip()[-200:]


def _build_image(exe: str, image: str, base: str, pip_index: str, timeout: int) -> str:
    ctx = Path(tempfile.mkdtemp(prefix="p2c_build_"))
    try:
        (ctx / "Dockerfile").write_text(sandbox_dockerfile(base, pip_index), encoding="utf-8")
        r = subprocess.run([exe, "build", "-t", image, str(ctx)],  # noqa: S603
                           capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            return "base=%s pip=%s：%s" % (base, pip_index or "默认",
                                           (r.stderr or r.stdout or "").strip()[-300:])
        return ""
    except Exception as exc:
        return "base=%s：%s: %s" % (base, type(exc).__name__, exc)
    finally:
        shutil.rmtree(ctx, ignore_errors=True)


def ensure_image(image: str, base: str = SANDBOX_DEFAULT_BASE, pip_index: str = "",
                 timeout: int = 900) -> str:
    """确保沙箱镜像可用（不存在就构建，构建期允许联网、运行期不联网）。返回错误串，空 = 成功。

    两阶段、可兜底：先按顺序把**基础镜像**拉下来（配置的源 → 内置镜像源），再按顺序试 **pip 源**
    构建。这样「Docker Hub 直连不通」的环境也能自动出镜像，不必让用户手工设环境变量；
    实际用到的组合记入 :data:`IMAGE_INFO`，由 CLI / 报告如实展示。
    """
    exe = docker_path()
    if not exe:
        return "未找到 docker 可执行文件"
    IMAGE_INFO.update({"image": image, "attempts": [], "built": False, "error": ""})
    if _image_present(exe, image):
        IMAGE_INFO.update({"base": IMAGE_INFO.get("base") or "", "pip_index": IMAGE_INFO.get("pip_index") or ""})
        return ""

    bases: List[str] = [base or SANDBOX_DEFAULT_BASE]
    bases += [b for b in FALLBACK_BASES if b not in bases]
    picked, errors = "", []
    for b in bases:
        err = _pull_base(exe, b)
        IMAGE_INFO["attempts"].append({"stage": "pull", "base": b, "ok": not err, "err": err[:200]})
        if not err:
            picked = b
            break
        errors.append("拉取 %s 失败：%s" % (b, err))
    if not picked:
        msg = "基础镜像都拉不下来（已试 %s）｜ %s" % ("、".join(bases), "；".join(errors))
        IMAGE_INFO["error"] = msg
        return msg

    idxs: List[str] = [pip_index] if pip_index else [""]
    idxs += [i for i in FALLBACK_PIP_INDEXES if i not in idxs]
    for i in idxs:
        err = _build_image(exe, image, picked, i, timeout)
        IMAGE_INFO["attempts"].append({"stage": "build", "base": picked, "pip_index": i,
                                       "ok": not err, "err": err[:200]})
        if not err:
            IMAGE_INFO.update({"base": picked, "pip_index": i, "built": True})
            return ""
        errors.append(err)
    msg = "构建沙箱镜像失败（base=%s）：%s" % (picked, "；".join(errors)[-400:])
    IMAGE_INFO["error"] = msg
    return msg


def image_status(image: str = "") -> Dict[str, Any]:
    """沙箱镜像状态（供 CLI / 报告）：是否存在、上次构建用的基础镜像与 pip 源。"""
    from ..config import get_settings

    img = image or str(get_settings().sandbox_config().get("image") or "p2c-sandbox:latest")
    exe = docker_path()
    present = bool(exe) and _image_present(exe, img)
    out: Dict[str, Any] = {"image": img, "present": present,
                           "base": IMAGE_INFO.get("base", ""), "pip_index": IMAGE_INFO.get("pip_index", ""),
                           "built_now": bool(IMAGE_INFO.get("built")), "error": IMAGE_INFO.get("error", "")}
    if present and not out["base"]:
        try:
            r = subprocess.run([exe, "image", "inspect", "--format", "{{.Size}}", img],  # noqa: S603
                               capture_output=True, text=True, timeout=60)
            out["size_bytes"] = int((r.stdout or "0").strip() or 0)
        except Exception:
            pass
    return out


def resolve_policy(requested: Optional[str] = None) -> SandboxPolicy:
    """把配置翻译成**实际生效**的策略；拿不到容器就如实降级并写清原因。"""
    from ..config import get_settings

    cfg = get_settings().sandbox_config()
    want = (requested or cfg.get("mode") or "local").lower()
    pol = SandboxPolicy(requested=want, image=str(cfg.get("image") or ""))
    if want not in ("local", "auto", "container"):
        want, pol.requested = "local", "local"
        pol.reason = "未知的 sandbox_mode=%s，按 local 处理" % pol.requested
    if want == "local":
        # 显式选 local 时**不探测 Docker**：没有容器可用性可言，也不该为一条提示付 `docker info` 的代价
        pol.mode = "local"
        pol.reason = pol.reason or "配置为 local：本机进程执行，无隔离边界"
        return pol
    if docker_available():
        pol.mode = "container"
        pol.isolation = "容器隔离（无网络 / 只读根 / 仅挂载工作目录 / 资源上限）"
        pol.risk = "low"
        pol.network = "无网络（--network none）"
        return pol
    pol.mode = "local"
    pol.degraded = True
    pol.reason = "要求 %s，但 %s；已降级为本机进程执行（低隔离 / 高风险），未做任何隔离" % (
        want, _DOCKER_CACHE.get("err") or "Docker 不可用")
    return pol


def sandbox_notice(requested: Optional[str] = None) -> str:
    """给上层用的一句告警（无问题返回空串）。"""
    pol = resolve_policy(requested)
    if pol.degraded:
        return "执行沙箱降级：%s" % pol.reason
    if pol.mode == "local":
        return ("执行沙箱为**本地模式**（低隔离 / 高风险）：生成的代码在本机进程里运行，"
                "只有超时与独立工作目录约束；要真正的隔离请开启容器执行器（sandbox_mode=container）。")
    return ""


# --------------------------------------------------------------------------- #
# 单一执行入口
# --------------------------------------------------------------------------- #
def run_python_script(
    script: Path | str,
    workdir: Path | str,
    timeout: int = 120,
    inputs: Optional[Iterable[Path]] = None,
    env_extra: Optional[Dict[str, str]] = None,
    allow_network: bool = False,
    python_exe: Optional[str] = None,
    trusted: bool = False,
    scan: Optional[bool] = None,
    executor: Optional[str] = None,
) -> RunResult:
    """在 ``workdir`` 中执行 ``script``。

    * ``python_exe``：本地模式的解释器覆盖（复现情形 A 的专用 venv，见 ``P2C_REPO_VENV``）。
    * ``trusted``：这份代码由 paper2code **自己的确定性生成器**产出（本仓库脚手架 / 领域模板 /
      内部 smoke 运行器）。此时跳过静态扫描——它们的危险调用是有意为之且边界明确
      （见 ``tests/test_safety.py`` 钉住的那一条 subprocess）。**来自 LLM 或论文仓库的代码
      一律不要传 trusted**。
    * ``scan``：显式覆盖是否扫描（默认取 ``sandbox_scan`` 配置）。
    * ``executor``：``local`` / ``container`` / None（按配置）。
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    if isinstance(script, str):
        sp = workdir / "generated_main.py"
        sp.write_text(script, encoding="utf-8")
    else:
        sp = Path(script).resolve()

    if inputs:
        for src in inputs:
            src = Path(src)
            if src.exists() and src.is_file():
                try:
                    shutil.copy2(src, workdir / src.name)
                except Exception:
                    pass

    from ..config import get_settings

    cfg = get_settings().sandbox_config()
    do_scan = bool(cfg.get("scan", True)) if scan is None else bool(scan)
    # M6-④：`executor` 也可以指名一个**注册过的插件执行器**（如 remote/远程 GPU）
    plugin_ex, plugin_why = (None, "")
    if executor and executor not in ("local", "container", "auto"):
        plugin_ex, plugin_why = _plugin_executor(str(executor))
    pol = resolve_policy(None if plugin_ex is not None else executor)
    result = RunResult(workdir=str(workdir), executor=pol.mode, policy=pol.to_dict())
    if plugin_ex is not None:
        pol.mode = str(getattr(plugin_ex, "name", executor))
        pol.isolation = str(getattr(plugin_ex, "isolation", "插件提供的执行器（隔离级别由其自身决定）"))
        pol.risk = str(getattr(plugin_ex, "risk", "unknown"))
        pol.network = "由远端环境决定"
        result.executor, result.policy = pol.mode, pol.to_dict()

    # -- M4-① 静态扫描：命中即不执行 --------------------------------------- #
    if do_scan and not trusted:
        report = safety.scan_files(_sources(workdir), dep_policy=str(cfg.get("dep_policy") or "warn"))
        result.scan = report.to_dict()
        if report.blocked:
            result.ok = False
            result.blocked = True
            result.error = "静态扫描拦截（未执行）：%s" % report.summary()
            _write_log(result, command=[], timeout=timeout, allow_network=allow_network, trusted=False)
            return result

    if plugin_ex is None and executor and executor not in ("local", "container", "auto", None):
        # 用户明确指名了一个不存在的执行器：直接失败，不悄悄改用本地执行
        result.ok = False
        result.error = plugin_why or "未知执行器 `%s`" % executor
        _write_log(result, command=[str(executor)], timeout=timeout,
                   allow_network=allow_network, trusted=trusted)
        return result
    if plugin_ex is not None and not plugin_ex.available():
        # M6-④：指名远端却不可用 → **拒绝执行**（绝不退回本机跑用户的代码）
        result.ok = False
        result.error = "执行器 `%s` 不可用：%s" % (pol.mode, _why_unavailable(plugin_ex))
        _write_log(result, command=[pol.mode], timeout=timeout,
                   allow_network=allow_network, trusted=trusted)
        return result

    env = _build_env(env_extra, allow_network=allow_network)
    before = _snapshot(workdir)

    if plugin_ex is not None:
        _run_plugin(result, plugin_ex, sp, workdir, timeout, env)
    elif pol.mode == "container":
        _run_in_container(result, sp, workdir, timeout, env, cfg, allow_network)
        if result.error.startswith("容器不可用") or result.error.startswith("沙箱镜像"):
            # 容器在运行前不可用 → 如实降级为本地，并保留原因（不假装隔离）
            pol.mode, pol.degraded = "local", True
            pol.reason = result.error
            pol.isolation, pol.risk = "低隔离（本机进程，无隔离边界）", "high"
            result.executor, result.policy, result.error = "local", pol.to_dict(), ""
    if result.executor == "local" and not result.blocked:
        _run_local(result, sp, workdir, timeout, env, python_exe)

    after = _snapshot(workdir)
    new = sorted(set(after) - set(before))
    result.artifacts = [str(workdir / n) for n in new if Path(n).suffix.lower() in ARTIFACT_EXTS]
    result.artifacts_hash = {Path(a).name: _hash(a) for a in result.artifacts}
    _write_log(result, command=[result.executor, sp.name], timeout=timeout,
               allow_network=allow_network, trusted=trusted)
    if result.cancelled:   # M5-①：取消/任务超时必须向上传播（日志与产物哈希已落盘，便于事后看现场）
        raise control.RunCancelled(result.cancelled, timed_out=result.cancelled.startswith("任务超时"))
    return result


def _plugin_executor(name: str) -> tuple:
    """查注册过的插件执行器（M5-④ 的注册表）。返回 ``(executor, 错误原因)``。"""
    try:
        from .. import plugins
    except Exception as exc:
        return None, "插件执行器不可用（插件模块加载失败）：%s" % exc
    ex = getattr(plugins, "_EXTRA_EXECUTORS", {}).get(name)
    if ex is None:
        return None, ("未知执行器 `%s`：内置只有 local/container；插件执行器需要先注册"
                      "（见 paper2code.plugins.register_executor）" % name)
    return ex, ""


def _why_unavailable(ex: Any) -> str:
    for attr in ("unavailable_reason", "describe"):
        fn = getattr(ex, attr, None)
        if callable(fn):
            try:
                got = fn()
                if isinstance(got, str) and got:
                    return got
                if isinstance(got, dict) and got.get("reason"):
                    return str(got["reason"])
            except Exception:
                pass
    return "执行器未说明原因"


def _run_plugin(res: RunResult, ex: Any, sp: Path, workdir: Path, timeout: int,
                env: Dict[str, str]) -> None:
    """跑插件执行器（如 remote）；异常一律记成失败，不抛出。"""
    try:
        r = ex.run(sp.name, workdir, timeout=timeout, env=env)
    except Exception as exc:
        res.ok = False
        res.error = "执行器 `%s` 异常：%s: %s" % (getattr(ex, "name", "?"), type(exc).__name__, exc)
        return
    res.executor = str(getattr(r, "executor", "") or getattr(ex, "name", res.executor))
    res.returncode = int(getattr(r, "returncode", -1))
    res.stdout = getattr(r, "stdout", "") or ""
    res.stderr = getattr(r, "stderr", "") or ""
    res.ok = bool(getattr(r, "ok", False))
    res.timed_out = bool(getattr(r, "timed_out", False))
    res.error = getattr(r, "error", "") or ""
    res.container = ""


def _kill_tree(proc: "subprocess.Popen") -> None:
    """杀掉整个进程树（生成的 demo 常自己再起子进程，只杀直接子进程会留下孤儿）。"""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],  # noqa: S603
                           capture_output=True, timeout=20)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _run_process(cmd: Sequence[str], cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None,
                 timeout: float = 0, on_stop: Optional[Any] = None) -> Dict[str, Any]:
    """跑一个子进程，支持**取消**与超时，并在停止时杀掉进程树（M5-①）。

    为什么不用 ``subprocess.run(timeout=)``：它只能定时杀直接子进程、且中途无法响应取消；
    队列任务要求「用户点取消就尽快停」，所以这里用 ``Popen`` + 轮询：
    每次轮询先问当前线程的控制对象（:mod:`paper2code.control`），再检查执行级超时。
    """
    from .. import control as _control

    ctl = _control.get_current()
    popen_kwargs: Dict[str, Any] = dict(cwd=cwd, env=env, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True,
                                        encoding="utf-8", errors="replace")
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True     # 建独立进程组，便于整组杀
    try:
        proc = subprocess.Popen(list(cmd), **popen_kwargs)  # noqa: S603
    except Exception as exc:
        return {"returncode": -1, "stdout": "", "stderr": "", "timed_out": False,
                "cancelled": "", "error": "无法启动子进程：%s: %s" % (type(exc).__name__, exc)}

    deadline = (time.time() + timeout) if timeout and timeout > 0 else None
    out, err, timed_out, cancelled, error = "", "", False, "", ""
    while True:
        try:
            out, err = proc.communicate(timeout=_control.POLL_INTERVAL_S)
            break
        except subprocess.TimeoutExpired:
            stop = ctl.stopped_reason() if ctl is not None else ""
            if stop:
                cancelled = stop               # 任务被取消 / 任务级超时 → 需要向上传播
            elif deadline is not None and time.time() > deadline:
                timed_out = True
                error = "执行超时（>%ss），已终止" % timeout
            else:
                continue
            if on_stop is not None:
                try:
                    on_stop()                     # 容器要额外 docker kill
                except Exception:
                    pass
            _kill_tree(proc)
            try:
                out, err = proc.communicate(timeout=10)
            except Exception:
                pass
            break
        except Exception as exc:
            error = "执行失败：%s: %s" % (type(exc).__name__, exc)
            break
    return {"returncode": proc.returncode if proc.returncode is not None else -1,
            "stdout": out or "", "stderr": err or "", "timed_out": timed_out,
            "cancelled": cancelled, "error": error}


# --------------------------------------------------------------------------- #
# 执行器实现
# --------------------------------------------------------------------------- #
def _run_local(res: RunResult, sp: Path, workdir: Path, timeout: int, env: Dict[str, str],
               python_exe: Optional[str]) -> None:
    res.executor = "local"
    exe = python_exe or sys.executable
    o = _run_process([exe, sp.name], cwd=str(workdir), env=env, timeout=timeout)
    res.stdout, res.stderr = o["stdout"], o["stderr"]
    res.cancelled = o["cancelled"]
    if o["cancelled"]:
        res.ok, res.returncode, res.error = False, -9, o["cancelled"]
        return
    if o["timed_out"]:
        res.ok, res.returncode, res.timed_out, res.error = False, -9, True, o["error"]
        return
    res.returncode = o["returncode"]
    res.ok = o["returncode"] == 0
    res.error = o["error"]


def _run_in_container(res: RunResult, sp: Path, workdir: Path, timeout: int, env: Dict[str, str],
                      cfg: Mapping[str, Any], allow_network: bool) -> None:
    """Docker 执行：无网络、只读根、只挂工作目录、CPU/内存/pids/时间上限。"""
    exe = docker_path()
    if not exe:
        res.error = "容器不可用：未找到 docker 可执行文件"
        return
    image = str(cfg.get("image") or "p2c-sandbox:latest")
    err = ensure_image(image, base=str(cfg.get("base") or SANDBOX_DEFAULT_BASE),
                       pip_index=str(cfg.get("pip_index") or ""))
    if err:
        res.error = "沙箱镜像不可用：%s" % err
        return
    res.policy["image"] = image
    res.policy["image_base"] = IMAGE_INFO.get("base", "")
    mem = str(cfg.get("memory") or "1g")
    name = "p2c_" + uuid.uuid4().hex[:10]
    cmd = [
        exe, "run", "--rm", "--name", name,
        "--network", "bridge" if allow_network else "none",
        "--cpus", str(cfg.get("cpus") or 1),
        "--memory", mem, "--memory-swap", mem,
        "--pids-limit", str(int(cfg.get("pids") or 128)),
        "--read-only", "--tmpfs", "/tmp:rw,size=256m",
        "--security-opt", "no-new-privileges",
        # 绑定挂载必须用宿主绝对路径（Docker 不接受相对路径），否则会被当成卷名而报错
        "-v", "%s:/work" % Path(workdir).resolve(), "-w", "/work",
    ]
    res.container = name
    # 只保留必要的环境变量；HOME 指向内存盘，避免任何「读家目录」落到宿主
    cenv = {k: v for k, v in env.items()
            if k.startswith("P2C_") or k in ("PYTHONIOENCODING", "PYTHONDONTWRITEBYTECODE", "MPLBACKEND")}
    cenv["HOME"] = "/tmp"
    cenv["MPLCONFIGDIR"] = "/tmp/mpl"
    for k, v in sorted(cenv.items()):
        cmd += ["-e", "%s=%s" % (k, v)]
    cmd += [image, "python", sp.name]

    def _kill_container() -> None:
        try:
            subprocess.run([exe, "kill", name], capture_output=True, timeout=30)  # noqa: S603
        except Exception:
            pass

    o = _run_process(cmd, timeout=timeout + 20, on_stop=_kill_container)
    res.stdout, res.stderr = o["stdout"], o["stderr"]
    res.cancelled = o["cancelled"]
    if o["cancelled"]:
        _kill_container()
        res.ok, res.returncode, res.error = False, -9, o["cancelled"]
        return
    if o["timed_out"]:
        _kill_container()
        res.ok, res.returncode, res.timed_out = False, -9, True
        res.error = "容器内执行超时（>%ss），容器已 kill" % timeout
        return
    res.returncode = o["returncode"]
    res.ok = o["returncode"] == 0
    res.error = o["error"]


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #
def _sources(workdir: Path) -> Dict[str, str]:
    """收集工作目录下待执行的源码（静态扫描的对象）。"""
    out: Dict[str, str] = {}
    try:
        for p in sorted(workdir.rglob("*.py")):
            rel = p.relative_to(workdir)
            if "results" in rel.parts:
                continue
            out[str(rel).replace("\\", "/")] = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass
    return out


def _build_env(env_extra: Optional[Dict[str, str]], allow_network: bool) -> Dict[str, str]:
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
    return env


def _hash(path: str | Path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()[:16]


def _write_log(res: RunResult, command: Sequence[str], timeout: int,
               allow_network: bool, trusted: bool = False) -> None:
    """执行日志（M4-③）：谁、在哪、用什么策略、跑了什么、结果与产物哈希。"""
    try:
        from ..config import get_settings

        log_dir = Path(get_settings().work_dir) / LOG_DIR_NAME
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / ("%s_%s.json" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:6]))
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "executor": res.executor,
            "policy": res.policy,
            "command": list(command),
            "timeout_s": timeout,
            "allow_network": allow_network,
            "workdir": res.workdir,
            "trusted": trusted,
            "scan": res.scan,
            "blocked": res.blocked,
            "returncode": res.returncode,
            "timed_out": res.timed_out,
            "cancelled": res.cancelled,
            "ok": res.ok,
            "error": res.error,
            "artifacts": res.artifacts,
            "artifacts_hash": res.artifacts_hash,
            "stdout_tail": (res.stdout or "")[-2000:],
            "stderr_tail": (res.stderr or "")[-2000:],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        res.log = str(path)
    except Exception:
        res.log = ""


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
