"""离线脚手架 demo：没有 LLM 时仍能产出「可运行、可校验、诚实标注」的 demo。

它**不是**对论文方法的实现，而是：
* 回放论文表格（数值已内嵌，demo 自带数据、不依赖本仓库）并做汇总统计；
* 若论文有伪代码，复用 ``reproduce.pseudo2code`` 的离线翻译器，把翻译产物放进 demo 并在每次运行时真的执行；
报告里会明确写出「这是脚手架」，并提示启用 LLM 以获得忠实的方法实现。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List

from .. import control, numutil
from ..config import get_settings
from ..models import Paper
from ..reproduce import dataset, pseudo2code

SCAFFOLD_PLAN_NOTE = "离线脚手架：回放论文表格并执行已翻译的伪代码；不实现论文方法本身"

RUN_DEMO = '''"""离线脚手架 demo（由 paper2code.mvp 自动生成）。

注意：这是脚手架，不是论文方法的实现——它回放论文表格并执行已翻译的伪代码。
不带参数直接运行 = smoke 模式；结果写入 results/metrics.json。
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARK = "__P2C_RESULT__"


def summarize(values):
    nums = [v for v in values if v is not None]
    if not nums:
        return {"n": 0}
    return {"n": len(nums), "mean": sum(nums) / len(nums), "min": min(nums), "max": max(nums)}


def run_algorithm(path):
    try:
        p = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, timeout=60,
                           cwd=str(path.parent), encoding="utf-8", errors="replace")
    except Exception as exc:  # 超时 / 无法启动
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    parsed = None
    for line in reversed((p.stdout or "").splitlines()):
        if line.startswith(MARK):
            try:
                parsed = json.loads(line[len(MARK):])
            except ValueError:
                parsed = None
            break
    if not parsed:
        return {"ok": False, "error": (p.stderr or "no result marker")[-300:]}
    out = parsed.get("output")
    return {"ok": bool(parsed.get("ok")), "error": parsed.get("error") or "",
            "output_preview": json.dumps(out, ensure_ascii=False)[:300]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="脚手架无区别，仅为契约兼容")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="results")
    a = ap.parse_args()
    tables = json.loads((HERE / "data" / "tables.json").read_text(encoding="utf-8"))
    out = {"meta": {"kind": "offline-scaffold", "seed": a.seed,
                    "note": "回放论文表格 + 执行已翻译伪代码；不是论文方法的实现"},
           "tables": {}, "algorithms": {}}
    for t in tables:
        cols = {}
        for name, values in t["columns"].items():
            cols[name] = {"labels": t["labels"], "values": [v for v in values], "summary": summarize(values)}
        out["tables"][t["id"]] = {"caption": t["caption"], "columns": cols}
    for path in sorted((HERE / "algorithms").glob("*_main.py")):
        out["algorithms"][path.stem[: -len("_main")]] = run_algorithm(path)
    res = Path(a.out)
    res.mkdir(parents=True, exist_ok=True)
    (res / "metrics.json").write_text(json.dumps(out, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
    print("MVP_OK")


if __name__ == "__main__":
    main()
'''

README = """# 离线脚手架 demo

由 paper2code.mvp 在**没有可用 LLM** 时生成。它回放论文表格（`data/tables.json`）并执行已翻译的伪代码（`algorithms/`），
**不是论文方法的实现**。要得到忠实的 MVP，请在设置页配置 LLM 后重新生成。

```bash
python run_demo.py          # smoke，写出 results/metrics.json
```
"""


def scaffold_plan(paper: Paper) -> Dict[str, Any]:
    return {
        "framework": "stdlib",
        "core_idea_to_implement": SCAFFOLD_PLAN_NOTE,
        "faithful_parts": [],
        "simplified_parts": [{"what": "论文方法本身", "why": "离线模式无法理解并实现方法",
                              "how": "仅回放论文表格并执行已翻译的伪代码"}],
        "toy_data": {"description": "论文自带表格数值", "generator": "tables.json"},
        "demo_files": [{"path": "run_demo.py", "purpose": "回放与汇总"}],
        "required_outputs": ["meta.kind"],
        "acceptance_checks": ["metrics.json 可解析且无 NaN"],
        "runtime_budget_s": 30,
        "dependencies": [],
    }


def _tables_payload(paper: Paper) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    limit = get_settings().max_records
    for t in paper.tables:
        if not t.rows:
            continue
        cols = dataset.numeric_columns(t)
        recs = dataset.table_to_records(t)[:limit]
        if not cols or not recs:
            continue
        first = list(recs[0].keys())[0]
        labels = [str(r.get(first, "")).strip() or "row%d" % (i + 1) for i, r in enumerate(recs)]
        data = {c: [numutil.to_number(r.get(c)) for r in recs] for c in cols if c != first}
        if data:
            out.append({"id": str(t.id), "caption": t.caption, "labels": labels, "columns": data})
    return out


def build_scaffold(paper: Paper, demo_dir: Path, provider: Any, timeout: int = 60) -> Dict[str, Any]:
    """写出脚手架文件到 demo_dir；返回 {'files': [...], 'warnings': [...]}。"""
    demo_dir = Path(demo_dir)
    warnings: List[str] = []
    (demo_dir / "data").mkdir(parents=True, exist_ok=True)
    (demo_dir / "algorithms").mkdir(parents=True, exist_ok=True)
    (demo_dir / "run_demo.py").write_text(RUN_DEMO, encoding="utf-8")
    (demo_dir / "README.md").write_text(README, encoding="utf-8")
    payload = _tables_payload(paper)
    (demo_dir / "data" / "tables.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if not payload and not paper.algorithms:
        warnings.append("论文既无可用表格也无伪代码：脚手架没有可回放的内容，请启用 LLM 生成真正的 MVP。")
    work = demo_dir / "algorithms" / "_work"
    for algo in paper.algorithms[:3]:
        try:
            pr = pseudo2code.execute_pseudocode(paper, algo, provider, work / algo.id, timeout=timeout)
            src = Path(pr.code_path)
            if pr.ok and src.exists():
                shutil.copy2(src, demo_dir / "algorithms" / ("%s_main.py" % algo.id))
            else:
                lines = [l for l in (pr.error or pr.stderr or "").strip().splitlines() if l.strip()]
                warnings.append("伪代码 %s 经离线翻译后无法执行（未放入 demo）：%s" % (algo.id, lines[-1][:160] if lines else "未知原因"))
        except Exception as exc:
            if isinstance(exc, control.RunCancelled):
                raise          # M5-①：取消/超时必须穿透，不能降级成一条 warning
            warnings.append("伪代码 %s 处理失败：%s" % (algo.id, exc))
    shutil.rmtree(work, ignore_errors=True)
    files = sorted(str(p.relative_to(demo_dir)).replace("\\", "/") for p in demo_dir.rglob("*") if p.is_file())
    return {"files": files, "warnings": warnings}
