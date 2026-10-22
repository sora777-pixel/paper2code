"""生成代码的静态安全扫描（roadmap M4-①）。

**为什么要有它**：容器执行器是本项目的目标形态，但 Windows 上容器体验差，且复现情形 A
必须用「仓库自己的 venv」（容器镜像里没有那个环境），所以本地执行模式会长期存在。
本地模式无法真正隔离，于是在执行之前加一道**确定性、可离线验收**的闸：扫描待运行的
源码，命中危险能力就不执行、判失败并记录理由。

**拦什么**（与路线图一致）：

* 进程与原生能力：``os.system`` / ``os.popen`` / ``os.exec*`` / ``os.spawn*`` / ``os.fork`` /
  ``subprocess`` / ``ctypes`` / ``multiprocessing`` / ``pty``；
* 网络能力：``socket`` / ``urllib.request`` / ``http.client`` / ``requests`` / ``httpx`` /
  ``aiohttp`` / ``ftplib`` / ``smtplib`` / ``telnetlib`` / ``webbrowser``；
* 工作目录之外的读写：绝对路径字面量、``..`` 越界、``os.path.expanduser`` / ``Path.home`` /
  ``HOME`` 环境变量（读家目录）；
* 依赖白名单：既不在 stdlib、也不在白名单里的第三方 import（按 ``sandbox_dep_policy``
  只告警或拦截）。

**拦不住什么（务必诚实）**：字符串拼接出来的动态 import / 路径、被混淆的调用、第三方库
内部的危险能力。真正的隔离只有容器能给——扫描只是**纵深防御的一层**，不是替代品。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# --------------------------------------------------------------------------- #
# 规则表
# --------------------------------------------------------------------------- #
#: 整模块禁用：导入即视为拥有该能力
BLOCK_MODULES: frozenset = frozenset({
    "socket", "ssl", "subprocess", "multiprocessing", "ctypes", "pty",
    "ftplib", "smtplib", "telnetlib", "poplib", "imaplib", "requests",
    "httpx", "aiohttp", "webbrowser", "win32api", "win32com",
})

#: 模块前缀禁用（如 ``urllib.request`` / ``http.client``）
BLOCK_MODULE_PREFIXES: Tuple[str, ...] = ("urllib.request", "urllib.error", "http.client", "http.server", "xmlrpc")

#: ``from os import ...`` 里这些名字等价于拿到进程/原生能力
BLOCK_OS_NAMES: frozenset = frozenset({
    "system", "popen", "fork", "forkpty", "execv", "execve", "execl", "execle",
    "execvp", "execlp", "spawnl", "spawnle", "spawnv", "spawnve", "spawnvp",
    "spawnvpe", "kill", "killpg", "setuid", "setgid", "chroot", "startfile",
})

#: 依赖白名单（第三方）。stdlib 由 ``sys.stdlib_module_names`` 判定，不在此列。
DEP_WHITELIST: frozenset = frozenset({
    "numpy", "scipy", "pandas", "sklearn", "matplotlib", "seaborn", "statsmodels",
    "torch", "torchvision", "tensorflow", "jax", "flax", "keras",
    "gym", "gymnasium", "networkx", "sympy", "numba", "joblib", "tqdm",
    "PIL", "yaml", "cv2", "plotly", "xgboost", "lightgbm", "catboost",
    "polars", "pyarrow", "tabulate", "rich",
})

#: 动态 import 的入口（只告警：可能是无害的插件式加载）
DYNAMIC_IMPORT = (r"__import__\s*\(", r"importlib\.import_module\s*\(", r"importlib\.util\.spec_from_file\s*\(")

#: 单行危险调用（名字即能力）
CALL_RULES: Tuple[Tuple[str, str, str], ...] = (
    (r"\bos\.(system|popen|startfile)\s*\(", "block", "执行外部命令"),
    (r"\bos\.(fork|forkpty)\s*\(", "block", "fork（fork 炸弹的原料）"),
    (r"\bos\.exec\w*\s*\(", "block", "替换进程映像执行外部程序"),
    (r"\bos\.spawn\w*\s*\(", "block", "派生外部进程"),
    (r"\bos\.(kill|killpg|setuid|setgid|chroot)\s*\(", "block", "进程/权限操作"),
    (r"\bctypes\.", "block", "调用原生库（可绕过全部 Python 级限制）"),
    (r"\bmultiprocessing\.", "block", "多进程（fork 炸弹的原料）"),
    (r"\bsubprocess\.", "block", "启动外部进程"),
    (r"\bsocket\.", "block", "网络能力"),
    (r"\bos\.path\.expanduser\s*\(", "block", "读家目录"),
    (r"\bPath\.home\s*\(", "block", "读家目录"),
    (r"""\bos\.environ\s*(?:\[|\.get\()\s*['"](?:HOME|USERPROFILE|HOMEPATH)['"]""", "block", "读家目录"),
    (r"(?<![.\w])(eval|exec)\s*\(", "warn", "动态执行字符串代码（可疑，需人工确认）"),
    (r"\bpickle\.loads\s*\(", "warn", "反序列化不可信数据"),
    (r"\bshutil\.rmtree\s*\(", "warn", "递归删除（确认目标在工作目录内）"),
)

#: 出现这些「文件操作」调用时，才对该行做路径越界判定，避免误伤普通字符串字面量
_FILEISH = re.compile(
    r"(?:\bopen|\bPath|\bos\.makedirs|\bos\.mkdir|\bos\.remove|\bos\.unlink|\bos\.rmdir"
    r"|\bshutil\.\w+|\bread_text|\bwrite_text|\bread_bytes|\bwrite_bytes"
    r"|\bsavefig|\bsavetxt|\bto_csv|\bto_json|\bjson\.dump|\bjson\.load|\bglob)\s*\(")

#: 绝对路径字面量（``/etc/...`` 或 Windows 盘符路径）
_ABS_LITERAL = re.compile(r"""(['"])((?:[A-Za-z]:[\\/]|/)[^'"]*)\1""")

#: ``..`` 越界字面量
_DOTDOT_LITERAL = re.compile(r"""(['"])[^'"]*\.\.[\\/][^'"]*\1""")

_IMPORT_RE = re.compile(r"^\s*import\s+(.+)")
_FROM_RE = re.compile(r"^\s*from\s+([A-Za-z_][\w.]*)\s+import\s+(.+)")


def _stdlib() -> frozenset:
    names = getattr(__import__("sys"), "stdlib_module_names", None)
    return frozenset(names) if names else frozenset()


STDLIB: frozenset = _stdlib()


# --------------------------------------------------------------------------- #
@dataclass
class Finding:
    """一条扫描结论。``severity`` 为 ``block`` 时该代码不得执行。"""

    rule: str
    severity: str          # block | warn
    file: str
    line: int
    snippet: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {"rule": self.rule, "severity": self.severity, "file": self.file,
                "line": self.line, "snippet": self.snippet, "reason": self.reason}

    def __str__(self) -> str:  # 报告里的一行摘要
        return "%s:%d [%s] %s —— %s" % (self.file, self.line, self.severity, self.snippet.strip()[:80], self.reason)


@dataclass
class ScanReport:
    findings: List[Finding] = field(default_factory=list)
    files_scanned: List[str] = field(default_factory=list)

    @property
    def blocked(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "block"]

    @property
    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "warn"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "n_block": len(self.blocked),
            "n_warn": len(self.warnings),
            "files": list(self.files_scanned),
            "blocked": [f.to_dict() for f in self.blocked],
            "warnings": [f.to_dict() for f in self.warnings],
            "summary": self.summary(),
        }

    def summary(self) -> str:
        if not self.findings:
            return "静态扫描未命中任何规则"
        parts = []
        if self.blocked:
            parts.append("拦截 %d 条：%s" % (len(self.blocked), "；".join(str(f) for f in self.blocked[:4])))
        if self.warnings:
            parts.append("告警 %d 条：%s" % (len(self.warnings), "；".join(str(f) for f in self.warnings[:3])))
        return " ｜ ".join(parts)


# --------------------------------------------------------------------------- #
def _strip_comment(line: str) -> str:
    """去掉行内注释（粗略：不解析字符串，够用于能力检测）。"""
    out, quote = [], ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote and line[i - 1] != "\\":
                quote = ""
            out.append(ch)
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out)


def _module_names(statement: str) -> List[str]:
    """``import a, b as c`` → ``["a", "b"]``。"""
    out: List[str] = []
    for chunk in statement.split(","):
        name = chunk.strip().split(" as ")[0].strip()
        if name:
            out.append(name)
    return out


def _scan_imports(path: str, lineno: int, line: str, dep_policy: str) -> List[Finding]:
    out: List[Finding] = []

    def add(rule: str, severity: str, module: str, reason: str) -> None:
        out.append(Finding(rule=rule, severity=severity, file=path, line=lineno,
                           snippet=line, reason="%s（%s）" % (reason, module)))

    m = _IMPORT_RE.match(line)
    if m:
        for name in _module_names(m.group(1)):
            top = name.split(".")[0]
            if top in BLOCK_MODULES or name.startswith(BLOCK_MODULE_PREFIXES):
                add("forbidden-import", "block", name, "禁用模块")
            elif name not in STDLIB and top not in STDLIB and top not in DEP_WHITELIST:
                add("dep-not-whitelisted", dep_policy, name, "不在依赖白名单内")
        return out

    m = _FROM_RE.match(line)
    if m:
        module, names = m.group(1), [n.strip().split(" as ")[0].strip() for n in m.group(2).split(",")]
        top = module.split(".")[0]
        if top in BLOCK_MODULES or module.startswith(BLOCK_MODULE_PREFIXES):
            add("forbidden-import", "block", module, "禁用模块")
        else:
            for n in names:
                full = "%s.%s" % (module, n)
                if full.startswith(BLOCK_MODULE_PREFIXES):
                    add("forbidden-import", "block", full, "禁用模块")
                elif module == "os" and n in BLOCK_OS_NAMES:
                    add("forbidden-import", "block", full, "从 os 直接取得进程/原生能力")
                elif top not in STDLIB and top not in DEP_WHITELIST:
                    add("dep-not-whitelisted", dep_policy, top, "不在依赖白名单内")
                    break
        return out

    for pat in DYNAMIC_IMPORT:
        if re.search(pat, line):
            add("dynamic-import", "warn", line.strip()[:40], "动态导入（静态扫描看不穿）")
            break
    return out


def scan_source(path: str, source: str, dep_policy: str = "warn") -> List[Finding]:
    """扫描单个源码文件。``path`` 仅用于报告定位。"""
    findings: List[Finding] = []
    for i, raw in enumerate((source or "").splitlines(), start=1):
        line = _strip_comment(raw)
        if not line.strip():
            continue
        findings.extend(_scan_imports(path, i, line, dep_policy))
        for pat, severity, reason in CALL_RULES:
            if re.search(pat, line):
                findings.append(Finding(rule="forbidden-call", severity=severity, file=path,
                                        line=i, snippet=line, reason=reason))
        if _FILEISH.search(line):
            m = _ABS_LITERAL.search(line)
            if m:
                findings.append(Finding(rule="abs-path", severity="block", file=path, line=i, snippet=line,
                                        reason="绝对路径访问（%s）：只允许在工作目录内用相对路径" % m.group(2)[:60]))
            elif _DOTDOT_LITERAL.search(line):
                findings.append(Finding(rule="path-escape", severity="block", file=path, line=i, snippet=line,
                                        reason="`..` 越界路径：只允许在工作目录内读写"))
    return findings


def scan_files(files: Mapping[str, str], dep_policy: str = "warn",
               suffixes: Sequence[str] = (".py",)) -> ScanReport:
    """扫描一批文件（键为相对路径）。默认只看 ``.py``。"""
    report = ScanReport()
    for rel, src in sorted((files or {}).items()):
        if suffixes and Path(rel).suffix.lower() not in suffixes:
            continue
        report.files_scanned.append(str(rel))
        report.findings.extend(scan_source(str(rel), src or "", dep_policy=dep_policy))
    return report


def scan_file(path: Path | str, dep_policy: str = "warn") -> ScanReport:
    """扫描磁盘上的一个脚本。"""
    p = Path(path)
    try:
        src = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ScanReport(findings=[Finding(rule="read-error", severity="warn", file=str(p), line=0,
                                            snippet="", reason="无法读取待扫描文件：%s" % exc)],
                          files_scanned=[])
    return scan_files({p.name: src}, dep_policy=dep_policy)
