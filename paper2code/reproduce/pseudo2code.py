"""情形 C（仅含伪代码）：伪代码 → 可执行 Python → 小规模执行 → 性质校验。

这是三种情形里唯一「必须自己造代码」的一类。设计要点：

* 翻译交给 :class:`~paper2code.llm.base.LLMProvider`（离线用规则翻译器，
  在线用提示词），两个实现产出同一契约：一个可调用的 Python 函数；
* 执行在 :mod:`runner` 的临时目录里，输入规模受 ``max_records`` 约束；
* **明确区分「逻辑复现」与「指标复现」**：产物报告里只声明性质校验结论
  （有序性、Top-K 正确性、归一化守恒等），不谎称复现了论文指标。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..models import Algorithm, Paper
from . import dataset, runner

RESULT_MARKER = "__P2C_RESULT__"

#: 注入给翻译产物的标准库导入 + **伪代码原语 shim**。
#: 论文伪代码常用 push/pop/size/sort_descending 这类抽象原语，本身不是 Python。
#: 与其把它们当错误，不如在骨架里补一层薄适配——这是「让伪代码真的跑起来」的关键。
PREAMBLE = """import json, math, random, statistics, itertools, collections, heapq
from collections import Counter, defaultdict, deque
from heapq import heappush as _heappush, heappop as _heappop, heapify as _heapify

# ---- 伪代码原语 shim（由 paper2code 注入） ---- #
def push(h, x):
    \"\"\"压入（列表视为堆）。\"\"\"
    try:
        _heappush(h, x)
    except Exception:
        h.append(x)
    return h

def pop(h):
    \"\"\"弹出堆顶；空堆返回 None。\"\"\"
    try:
        return _heappop(h) if h else None
    except Exception:
        return h.pop() if h else None

def size(x):
    return len(x)

def length(x):
    return len(x)

def is_empty(x):
    return len(x) == 0

def sort_ascending(x):
    return sorted(x)

def sort_descending(x):
    return sorted(x, reverse=True)

def sort(x):
    return sorted(x)

def empty_min_heap():
    return []

def empty_max_heap():
    return []

def swap(a, b):
    return b, a

def abs_val(x):
    return abs(x)

def max_val(x):
    return max(x)

def min_val(x):
    return min(x)
"""

#: 执行骨架模板。刻意**不使用 ``str.format``**，而用 ``__P2C_*__`` 占位符 +
#: ``str.replace`` 注入，因为骨架里包含大量 ``{}``（字典、集合、推导式），
#: 用 format 就必须逐个转义，极易出错。
HARNESS = '''

# ------------------------- paper2code 自动生成的执行骨架 ------------------------- #
# 形参名 → 值的推断规则：论文伪代码的签名千变万化（TOPK(S, k)、Normalize(x)、
# Cluster(X, n_clusters)…），这里用「名字语义」把数据与标量分别绑定，而不是
# 盲目地 fn(data)。
_P2C_INT_EXACT = {
    "k", "n", "m", "d", "r", "c", "topk", "top_k", "size", "count",
    "window", "budget", "limit", "num", "dim", "epochs", "iters",
}
_P2C_INT_HINT = ("num", "count", "size", "window", "budget", "limit", "dim", "iter", "epoch", "cluster", "topk", "top_k")
_P2C_SEQ_HINT = (
    "data", "stream", "scores", "values", "arr", "array", "items", "seq", "list",
    "matrix", "input", "points", "vec", "vector", "samples", "docs", "tokens", "dist",
)


def _p2c_value(name, idx, data, k):
    nm = (name or "").lower()
    if nm in _P2C_INT_EXACT or any(h in nm for h in _P2C_INT_HINT):
        return k
    if any(h in nm for h in _P2C_SEQ_HINT) or len(name or "") == 1:
        return data
    return data if idx == 0 else k


def _p2c_call(fn, data):
    """按签名把输入绑定到形参；全部失败时抛出示意最明确的那个异常。"""
    import inspect

    k = max(1, min(50, max(1, len(data) // 10))) if isinstance(data, (list, tuple)) else 1
    plan = {}
    first_err = None

    try:
        params = [
            p for p in inspect.signature(fn).parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
        ]
    except (TypeError, ValueError):
        params = []

    if params:
        plan = dict((p.name, _p2c_value(p.name, i, data, k)) for i, p in enumerate(params))
        try:
            return fn(**plan), plan
        except Exception as exc:
            first_err = exc

    for attempt in (lambda: fn(data), lambda: fn(*data), lambda: fn()):
        try:
            return attempt(), dict(positional=True)
        except Exception as exc:
            if first_err is None:
                first_err = exc

    raise first_err if first_err is not None else TypeError("无法调用目标函数")


def _p2c_sanitize(o, depth=0):
    if depth > 6:
        return str(o)
    if o is None or isinstance(o, (str, int, float, bool)):
        return o
    if isinstance(o, dict):
        return dict((str(k), _p2c_sanitize(v, depth + 1)) for k, v in list(o.items())[:500])
    if isinstance(o, (list, tuple, set)):
        return [_p2c_sanitize(v, depth + 1) for v in list(o)[:5000]]
    try:
        import numpy as np
        if isinstance(o, np.ndarray):
            return o.ravel().tolist()[:5000]
        if isinstance(o, np.generic):
            return o.item()
    except Exception:
        pass
    return str(o)


if __name__ == "__main__":
    data = __P2C_INPUT__
    random.seed(__P2C_SEED__)
    out = None
    plan = {}
    err = ""
    try:
        out, plan = _p2c_call(__P2C_FN__, data)
    except Exception:
        import traceback
        err = traceback.format_exc()
    payload = {
        "ok": not err,
        "error": err,
        "output": _p2c_sanitize(out),
        "n_input": len(data) if isinstance(data, list) else None,
        "call_plan": dict(
            (k, (type(v).__name__ if not isinstance(v, list) else "list[%d]" % len(v)))
            for k, v in plan.items()
        ),
    }
    print("__P2C_RESULT__" + json.dumps(payload, ensure_ascii=False))
'''


@dataclass
class PseudocodeRun:
    ok: bool = False
    code: str = ""
    code_path: str = ""
    fn_name: str = ""
    result: Optional[Dict[str, Any]] = None
    stdout: str = ""
    stderr: str = ""
    error: str = ""
    input_spec: Dict[str, Any] = field(default_factory=dict)
    workdir: str = ""
    artifacts: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok, "fn_name": self.fn_name, "code_path": self.code_path,
            "result": self.result, "stdout": self.stdout[-3000:], "stderr": self.stderr[-3000:],
            "error": self.error, "input_spec": self.input_spec, "artifacts": self.artifacts,
        }


#: PREAMBLE 里 shim 提供的名字，识别目标函数时要跳过它们
_SHIM_NAMES = {
    "push", "pop", "size", "length", "is_empty", "sort_ascending", "sort_descending",
    "sort", "empty_min_heap", "empty_max_heap", "swap", "abs_val", "max_val", "min_val",
    "heappush", "heappop", "heapify",
}


def extract_function_name(code: str) -> str:
    """取第一个**非 shim** 的顶层函数名，作为复现入口。"""
    for m in re.finditer(r"^\s*def\s+([A-Za-z_]\w*)\s*\(", code or "", re.MULTILINE):
        if m.group(1) not in _SHIM_NAMES:
            return m.group(1)
    return "run"


def make_input(kind: str, n: int, seed: int) -> List[Any]:
    spec = {"kind": kind, "n": n, "seed": seed}
    if kind == "int_vec":
        return dataset.synthesize_input(n, "int_vec", seed, low=0, high=max(10, n * 3))
    if kind == "matrix":
        return dataset.synthesize_input(n, "matrix", seed, low=-1.0, high=1.0)
    if kind == "str_list":
        return dataset.synthesize_input(n, "str_list", seed)
    return dataset.synthesize_input(n, "vec", seed, low=0.0, high=1.0)


def guess_input_kind(algo: Algorithm) -> str:
    text = (algo.caption + "\n" + algo.source).lower()
    if any(k in text for k in ("matrix", "矩阵", "2-d", "two-dimensional")):
        return "matrix"
    if any(k in text for k in ("string", "字符串", "token", "word", "text")):
        return "str_list"
    if any(k in text for k in ("integer", "int ", "整数", "count", "index", "排序", "sort")):
        return "int_vec"
    return "vec"


# --------------------------------------------------------------------------- #
def translate(paper: Paper, algo: Algorithm, provider: Any) -> Tuple[str, str]:
    """返回 ``(可直接执行的源码, 目标函数名)``。

    函数名必须从**翻译产物本体**里取，而不是从拼上 PREAMBLE（含 shim）之后的
    整段源码里取——否则第一个 ``def`` 会命中 shim 里的 ``push``，导致调用错函数。
    """
    context = "\n\n".join(s.text for s in paper.body_sections()[:4])
    raw = provider.pseudocode_to_python(algo, context)
    fn_name = extract_function_name(raw)
    # 无论翻译产物以什么开头，都补上标准库导入 + 伪代码原语 shim。
    # 导入是幂等的，shim 被重复定义也无害；漏掉反而会让 "sort_descending"
    # 这种伪代码原语在运行时直接 NameError。
    raw = PREAMBLE + "\n" + raw
    return raw, fn_name


def build_executable(
    code: str,
    algo: Algorithm,
    n: int,
    seed: int,
    kind: Optional[str] = None,
    fn_name: Optional[str] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    kind = kind or guess_input_kind(algo)
    data = make_input(kind, n, seed)
    fn_name = fn_name or extract_function_name(code)
    literal = json.dumps(data, ensure_ascii=False)
    if len(literal) > 200_000:  # 极端情况下压缩输入规模
        data = data[: max(8, n // 4)]
        literal = json.dumps(data, ensure_ascii=False)
    script = code + "\n" + (
        HARNESS.replace("__P2C_INPUT__", literal)
        .replace("__P2C_SEED__", str(seed))
        .replace("__P2C_FN__", fn_name)
    )
    return script, fn_name, {"kind": kind, "n": len(data), "seed": seed}


def parse_result(stdout: str) -> Optional[Dict[str, Any]]:
    for line in reversed((stdout or "").splitlines()):
        if line.startswith(RESULT_MARKER):
            try:
                return json.loads(line[len(RESULT_MARKER):])
            except Exception:
                return None
    return None


def execute_pseudocode(
    paper: Paper,
    algo: Algorithm,
    provider: Any,
    work_dir: Path,
    timeout: int = 120,
    n_records: Optional[int] = None,
    seed: int = 0,
) -> PseudocodeRun:
    """完整跑一遍：翻译 → 装骨架 → 执行 → 回收结果。"""
    from ..config import get_settings

    settings = get_settings()
    n = n_records or dataset.infer_scale_hint(paper)
    n = max(8, min(n, settings.max_records))

    code, fn_name = translate(paper, algo, provider)
    script, fn_name, input_spec = build_executable(code, algo, n, seed, fn_name=fn_name)

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    script_path = work_dir / f"{algo.id}_main.py"
    script_path.write_text(script, encoding="utf-8")

    res = runner.run_python_script(script_path, work_dir, timeout=timeout, allow_network=False)
    parsed = parse_result(res.stdout)

    out = PseudocodeRun(
        ok=bool(res.ok and parsed and parsed.get("ok")),
        code=script,
        code_path=str(script_path),
        fn_name=fn_name,
        result=parsed,
        stdout=res.stdout,
        stderr=res.stderr,
        error=(parsed.get("error") if parsed else "") or res.error,
        input_spec=input_spec,
        workdir=str(work_dir),
        artifacts=res.artifacts,
    )
    return out
