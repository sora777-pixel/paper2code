"""插件接口（roadmap M5-④）：领域模板 / 图表风格 / 执行器都可注册。

**它解决什么**：此前模板、绘图风格、执行器都是写死的模块级常量——外部想加一个领域模板
（比如「排队论」）或一种图表风格，只能改源码。这里给出**三种注册点**，全部走同一套约定：

* **进程内注册**：``register_template({...})`` / ``register_style({...})`` / ``register_executor(obj)``；
* **包入口注册**：`configs/plugins.yaml` 里列 ``plugins: [my_pkg.plugins]``，
  模块导入时调用上面的注册函数即可（导入失败只记 warning，不影响主流程）；
* **查询**：``list_templates()`` / ``list_styles()`` / ``list_executors()``。

**诚实边界**：插件只加能力、不改判定语义——注册的模板参与与内置模板**同样的**确定性打分，
命中不了就还是不命中；执行器必须实现 ``run(...) -> RunResult`` 的接口约定，
注册了但不可用（例如没装依赖）时按「不可用」如实降级。
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

#: 内置模板名（插件模板与它们并列参与打分；重名时插件**不覆盖**内置，避免破坏既有评测）
BUILTIN_TEMPLATE_NAMES: List[str] = []
#: 插件注册的模板 / 风格 / 执行器
_EXTRA_TEMPLATES: Dict[str, Dict[str, Any]] = {}
_EXTRA_STYLES: Dict[str, Dict[str, Any]] = {}
_EXTRA_EXECUTORS: Dict[str, Any] = {}
_LOADED: Dict[str, Any] = {"modules": [], "errors": []}
_AUTO: Dict[str, bool] = {"done": False}


def ensure_loaded() -> Dict[str, Any]:
    """幂等加载：第一次调用才真的读配置并导入插件（之后直接返回结果）。

    主流程（``run_paper`` / Web 启动）只需调它一次；插件坏掉不影响主流程。
    同时按配置注册**远程执行器**（M6-④）——没配 ``remote_dir`` 就不注册。
    """
    if not _AUTO["done"]:
        _AUTO["done"] = True
        info = load_plugins()
        try:
            from . import remote

            remote.register_from_settings()
        except Exception:
            pass
        return info
    return {"modules": list(_LOADED["modules"]), "errors": list(_LOADED["errors"]),
            "templates": list(_EXTRA_TEMPLATES), "styles": list(_EXTRA_STYLES),
            "executors": list(_EXTRA_EXECUTORS)}


# --------------------------------------------------------------------------- #
# 注册
# --------------------------------------------------------------------------- #
def register_template(tpl: Dict[str, Any], overwrite: bool = False) -> Dict[str, Any]:
    """注册一个领域模板。至少要有 ``name`` 与 ``hints``（匹配关键词）。

    ``builder`` 可选：签名 ``builder(paper, demo_dir, payload) -> info``，留给需要自定义生成的插件；
    不给则复用内置模板的生成逻辑（模板只贡献「匹配关键词 + 标签」）。
    """
    name = str((tpl or {}).get("name") or "").strip()
    if not name:
        raise ValueError("模板必须有 name")
    if not tpl.get("hints"):
        raise ValueError("模板 %s 必须有 hints（匹配关键词），否则永远命中不了" % name)
    if name in BUILTIN_TEMPLATE_NAMES and not overwrite:
        raise ValueError("模板名 %s 与内置模板冲突；换个名字或显式 overwrite=True" % name)
    if name in _EXTRA_TEMPLATES and not overwrite:
        raise ValueError("模板 %s 已注册过；要替换请传 overwrite=True" % name)
    _EXTRA_TEMPLATES[name] = dict(tpl)
    return _EXTRA_TEMPLATES[name]


def register_style(style: Dict[str, Any], overwrite: bool = False) -> Dict[str, Any]:
    """注册一种图表风格（配色/线型/字体等），供绘图脚本按名引用。"""
    name = str((style or {}).get("name") or "").strip()
    if not name:
        raise ValueError("风格必须有 name")
    if not style.get("palette"):
        raise ValueError("风格 %s 必须有 palette（颜色列表）" % name)
    if name in _EXTRA_STYLES and not overwrite:
        raise ValueError("风格 %s 已注册过；要替换请传 overwrite=True" % name)
    _EXTRA_STYLES[name] = dict(style)
    return _EXTRA_STYLES[name]


def register_executor(executor: Any, name: str = "", overwrite: bool = False) -> Any:
    """注册一个执行器。约定：对象要有 ``name`` 与 ``available() -> bool``，
    以及 ``run(script, workdir, timeout, env, **kw) -> RunResult``。
    """
    key = str(name or getattr(executor, "name", "") or "").strip()
    if not key:
        raise ValueError("执行器必须有 name")
    for attr in ("available", "run"):
        if not callable(getattr(executor, attr, None)):
            raise ValueError("执行器 %s 必须实现 %s()" % (key, attr))
    if key in _EXTRA_EXECUTORS and not overwrite:
        raise ValueError("执行器 %s 已注册过；要替换请传 overwrite=True" % key)
    _EXTRA_EXECUTORS[key] = executor
    return executor


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
def list_templates() -> List[Dict[str, Any]]:
    """内置模板 + 插件模板（内置在前，重名以插件为准时已在校验处拒绝）。"""
    from .mvp import templates as builtin

    out = [dict(t) for t in builtin.TEMPLATES]
    names = {t["name"] for t in out}
    for name, t in sorted(_EXTRA_TEMPLATES.items()):
        if name not in names:
            out.append(dict(t))
    return out


def list_styles() -> List[Dict[str, Any]]:
    return [dict(s) for _, s in sorted(_EXTRA_STYLES.items())]


def list_executors() -> Dict[str, Any]:
    """执行器（含内置 local/container）与它们的可用性。"""
    from .reproduce import runner

    out: Dict[str, Any] = {
        "local": {"kind": "builtin", "available": True,
                  "isolation": "低隔离（本机进程，无隔离边界）", "risk": "high"},
        "container": {"kind": "builtin", "available": runner.docker_available(),
                      "isolation": "容器隔离（无网络 / 只读根 / 仅挂载工作目录 / 资源上限）",
                      "risk": "low"},
    }
    for name, ex in sorted(_EXTRA_EXECUTORS.items()):
        try:
            ok = bool(ex.available())
        except Exception:
            ok = False
        out[name] = {"kind": "plugin", "available": ok,
                     "isolation": getattr(ex, "isolation", "插件提供的执行器（隔离级别由其自身决定）"),
                     "risk": getattr(ex, "risk", "unknown")}
    return out


# --------------------------------------------------------------------------- #
# 加载
# --------------------------------------------------------------------------- #
def load_plugins(entries: Optional[Sequence[str]] = None, config_file: Optional[Path] = None) -> Dict[str, Any]:
    """导入插件模块（幂等）。返回 ``{modules, errors, templates, styles, executors}``。

    插件配置来源：``configs/plugins.yaml`` 的 ``plugins: [module, ...]``，
    或显式传入 ``entries``。导入失败**不抛异常**——插件坏了不该拖垮主流程。
    """
    if entries is None:
        entries = _entries_from_config(config_file)
    errors: List[str] = []
    for mod_name in entries:
        mod_name = str(mod_name or "").strip()
        if not mod_name:
            continue
        if mod_name in _LOADED["modules"]:
            continue
        try:
            importlib.import_module(mod_name)
            _LOADED["modules"].append(mod_name)
        except Exception as exc:
            errors.append("%s: %s: %s" % (mod_name, type(exc).__name__, exc))
    _LOADED["errors"] = errors
    return {"modules": list(_LOADED["modules"]), "errors": errors,
            "templates": list(_EXTRA_TEMPLATES), "styles": list(_EXTRA_STYLES),
            "executors": list(_EXTRA_EXECUTORS)}


def _entries_from_config(config_file: Optional[Path] = None) -> List[str]:
    from .config import ROOT, _load_yaml

    path = Path(config_file) if config_file else (ROOT / "configs" / "plugins.yaml")
    data = _load_yaml(path)
    items = data.get("plugins") if isinstance(data, dict) else None
    return [str(x) for x in (items or [])]


def register_builtin_names() -> List[str]:
    """记录内置模板名（供冲突校验；导入时调用一次即可）。"""
    global BUILTIN_TEMPLATE_NAMES
    from .mvp import templates as builtin

    BUILTIN_TEMPLATE_NAMES = [t["name"] for t in builtin.TEMPLATES]
    return list(BUILTIN_TEMPLATE_NAMES)


def reset() -> None:
    """清空插件注册（测试用）。"""
    _EXTRA_TEMPLATES.clear()
    _EXTRA_STYLES.clear()
    _EXTRA_EXECUTORS.clear()
    _LOADED.update({"modules": [], "errors": []})
    _AUTO["done"] = False


@dataclass
class PluginReport:
    """给报告/WEB 端看的插件状态。"""

    modules: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    n_templates: int = 0
    n_styles: int = 0
    n_executors: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    def summary(self) -> str:
        if not self.modules and not self.errors:
            return "未配置插件"
        parts = ["已加载 %d 个插件模块" % len(self.modules)]
        if self.n_templates or self.n_styles or self.n_executors:
            parts.append("贡献 模板 %d / 风格 %d / 执行器 %d"
                         % (self.n_templates, self.n_styles, self.n_executors))
        if self.errors:
            parts.append("⚠️ %d 个插件导入失败：%s" % (len(self.errors), "；".join(self.errors[:2])))
        return "；".join(parts)

    @classmethod
    def current(cls) -> "PluginReport":
        return cls(modules=list(_LOADED["modules"]), errors=list(_LOADED["errors"]),
                   n_templates=len(_EXTRA_TEMPLATES), n_styles=len(_EXTRA_STYLES),
                   n_executors=len(_EXTRA_EXECUTORS))


register_builtin_names()