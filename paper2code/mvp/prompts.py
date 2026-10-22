"""提示词。契约（文件块格式 / JSON 键 / demo 约定）由 service.py 强制校验。"""

from __future__ import annotations

import json
from typing import Any, Dict, List

FILE_FORMAT = """只用下面的格式返回文件（块外不要有任何说明文字）：
<<<FILE: relative/path.py>>>
...完整文件内容...
<<<END>>>
路径必须是相对路径，禁止 '..' 和绝对路径。"""


def paper_context(paper: Any, budget: int = 26000) -> str:
    """把已结构化的 Paper 压成提示词上下文（不再调用 LLM 做 digest，省一次调用）。"""
    parts: List[str] = ["# 标题\n%s" % paper.title, "# 摘要\n%s" % (paper.abstract or "")[:2500]]
    for kinds, cap in ((("method",), 7000), (("experiment",), 6000), (("introduction",), 2500)):
        txt = paper.section_text(*kinds)
        if txt.strip():
            parts.append("# %s\n%s" % ("/".join(kinds), txt[:cap]))
    for a in paper.algorithms[:4]:
        parts.append("# 伪代码 %s（%s）\n%s" % (a.id, a.caption, a.source[:2500]))
    for f in paper.formulas[:12]:
        parts.append("# 公式 %s\n%s\n%s" % (f.number or f.id, f.expression, (f.explanation or "")[:300]))
    for t in paper.tables[:6]:
        rows = "\n".join(" | ".join(r) for r in t.rows[:8])
        parts.append("# 表 %s（%s）\n%s\n%s" % (t.id, t.caption, " | ".join(t.header), rows))
    for f in paper.figures[:20]:
        parts.append("# 图 %s [%s]\n%s" % (f.id, f.kind, f.caption))
    return "\n\n".join(p for p in parts if p.strip())[:budget]


PLAN_SYS = """你是资深 ML 工程师，要为一篇论文设计「最小可行且忠实」的演示（MVP demo）。
演示必须在普通笔记本 CPU 上 60 秒内跑完，使用玩具/合成数据，但要实现论文真正的机制，而不是泛泛的替代品。
只回复一个 JSON 对象。"""

PLAN_SCHEMA = """{
 "framework": "stdlib" | "numpy" | "torch",
 "core_idea_to_implement": str,
 "faithful_parts": [str],
 "simplified_parts": [{"what": str, "why": str, "how": str}],
 "toy_data": {"description": str, "generator": str},
 "demo_files": [{"path": str, "purpose": str}],
 "required_outputs": [str],      // results/metrics.json 里必须出现的点分键
 "acceptance_checks": [str],
 "runtime_budget_s": int,
 "dependencies": [str]
}"""


def plan_user(ctx: str, env: Dict[str, Any]) -> str:
    return f"""环境：{json.dumps(env)}
只有在 torch 已安装且思想确实需要自动求导时才选 "torch"；能用标准库就用 "stdlib"，需要数组运算且 numpy 已安装时用 "numpy"。
规则：
- smoke 模式 <= 60 秒 CPU；数据量要小（<= 2k 样本、<= 200 步）。
- required_outputs 要让论文里尽可能多的图能被「定性」复现：学习曲线 -> curves.<name>.{{x,y}}；消融 -> ablation.{{names,values}}；
  扫参/缩放 -> sweep.<name>.{{x,y}}；标量指标 -> metrics.<name>。序列优先用 {{"x":[...],"y":[...]}}。
- 诚实写出 simplified_parts（相对论文简化了什么）。
JSON schema：
{PLAN_SCHEMA}

=== 论文 ===
{ctx}"""


CODE_SYS = """你是严谨的研究工程师，写一个小而正确、可读的演示。
demo/run_demo.py 的硬性契约：
- 不带任何参数直接运行 = smoke 模式（<=60 秒 CPU）；可选 --full（更大规模）、--seed INT（默认 0）、--out DIR（默认 results）
- 创建 DIR 并写入 DIR/metrics.json（合法 JSON，禁止 NaN/Infinity），包含 required_outputs 里的每个键
- 最后一行打印：MVP_OK
- 给定 --seed 结果确定；不联网；不写 DIR 以外的文件；不依赖 GPU
- 只能使用 plan.framework 声明的依赖（stdlib 优先）
- 实现论文的真实机制；关键处用简短注释标注对应的公式/章节
同时产出 README.md（实现了什么、怎么跑、简化了什么）。"""


def code_user(plan: Dict[str, Any], ctx: str) -> str:
    return f"""{FILE_FORMAT}

至少产出：run_demo.py、README.md（路径相对 demo 目录）。计划文件：{json.dumps(plan.get('demo_files', []), ensure_ascii=False)}

=== MVP 计划 ===
{json.dumps(plan, ensure_ascii=False)}

=== 论文要点 ===
{ctx[:14000]}"""


FIX_SYS = """你在修复一个 Python demo。从报错里找根因，尽量少改，并保持契约（无参运行=smoke、results/metrics.json 键、MVP_OK）。
只返回被修改的文件，每个都给完整内容。"""


MODIFY_SYS = """你在按用户的要求**迭代修改**一个已经能跑的论文 demo（MVP）。
规则：
- 只改与要求相关的地方，其余文件保持不动（不必回传未改动的文件）；
- 必须继续满足原有契约：无参运行=smoke（<=60 秒 CPU）、写 results/metrics.json、键不变（可新增）、末行打印 MVP_OK、不联网、不写 demo 目录以外的文件；
- 若要求会让 demo 与论文机制偏离，就在 README.md 里如实说明简化了什么，不要偷偷换掉论文的核心思想；
- 如果这个要求在当前玩具规模下**做不到**，不要假装做到：在 README.md 里写清原因，并保持 demo 仍可运行。
只返回被修改的文件，每个都给完整内容。"""


def modify_user(files: Dict[str, str], instruction: str, plan: Dict[str, Any], ctx: str) -> str:
    body = "\n\n".join("<<<FILE: %s>>>\n%s<<<END>>>" % (p, c) for p, c in files.items())
    return f"""{FILE_FORMAT}

=== 用户要求 ===
{instruction}

=== MVP 计划（改动后仍应体现论文机制）===
{json.dumps(plan, ensure_ascii=False)[:4000]}

=== 论文要点 ===
{ctx[:6000]}

=== 当前版本的完整文件（改动前）===
{body}"""


def fix_user(files: Dict[str, str], why: str, tail: str, required: List[str]) -> str:
    body = "\n\n".join("<<<FILE: %s>>>\n%s<<<END>>>" % (p, c) for p, c in files.items())
    return f"""{FILE_FORMAT}

问题：{why}
metrics.json 必须包含的键：{required}

--- 运行输出（尾部）---
{tail}

--- 当前文件 ---
{body}"""


FIGSPEC_SYS = """你把论文里的图转成可实现的 matplotlib 绘图说明（「绘图信息」），
只能引用 demo 结果里真实存在的数据键（见下方清单）。对每张图选择 reproducibility：
- "from_demo"：demo 结果能定性复现其形状（必须使用真实键）
- "schematic"：概念/架构图，可用方框和箭头画出（给出 nodes/edges）
- "not_reproducible"：需要真实数据集/大规模结果；说明原因以及 demo 需要怎么改
只回复一个 JSON 对象。"""

FIGSPEC_SCHEMA = """{
 "figures": [{
   "paper_ref": "Figure N", "caption": str,
   "kind": "line|bar|grouped_bar|scatter|heatmap|diagram",
   "reproducibility": "from_demo|schematic|not_reproducible",
   "axes": {"x": str, "y": str, "xscale": "linear|log", "yscale": "linear|log"},
   "series": [{"name": str, "key": str}],    // key = results 里的点分键，指向 {x,y} 对象或数值列表
   "layout": str, "style_notes": str,
   "diagram": {"nodes": [{"id": str, "label": str, "x": 0-1, "y": 0-1}], "edges": [{"from": str, "to": str, "label": str}]} | null,
   "mermaid": str | null, "demo_change_needed": str | null
 }]
}"""


def figspec_user(figures: List[Dict[str, Any]], result_lines: List[str]) -> str:
    return f"""JSON schema：
{FIGSPEC_SCHEMA}

=== 论文里的图（只处理这些，表格由系统另行处理）===
{json.dumps(figures, ensure_ascii=False)}

=== demo/results/metrics.json 中可用的键 ===
{chr(10).join(result_lines) or '（demo 未执行；请提出 demo 应该写出的键）'}"""


FIGCODE_SYS = """你写一个 matplotlib 绘图脚本，只能使用下面的 figlib API 以及 matplotlib/numpy。
文件必须定义 main(results_dir, out_dir)，并用 save_fig 以图的 id 为名保存恰好一张图。
通过 load_results/get 读数据；键缺失时跳过该序列，不要崩溃。不联网，不写其他文件。"""

FIGLIB_API = """figlib API（已可导入：`from figlib import *`）：
  load_results(results_dir) -> dict           # metrics.json 在顶层
  get(res, 'curves.train_loss.y', default)    # 点分取值，不抛异常
  new_fig(w, h, nrows, ncols) -> (fig, axes)  # axes 永远是二维数组：axes[0][0]
  save_fig(fig, out_dir, name, toy=True, title=None) -> png 路径   # name = 图 id
  flow_diagram(ax, nodes, edges)              # nodes [{'id','label','x','y'}] x,y∈[0,1]；edges [{'from','to','label'}]
  PALETTE, plt
仅 kind 为 diagram 时用 toy=False（示意图不含数据）。"""


def figcode_user(spec: Dict[str, Any], result_lines: List[str]) -> str:
    slug = spec["id"][4:]
    return f"""{FILE_FORMAT}

只产出一个文件：figures/draw_{slug}.py
{FIGLIB_API}

图的绘图说明：
{json.dumps({k: v for k, v in spec.items() if k not in ('chart',)}, ensure_ascii=False, indent=1)}

可用的结果键：
{chr(10).join(result_lines)}"""


FIGFIX_SYS = """你在修复一个使用 figlib 的 matplotlib 绘图脚本。尽量少改，返回完整文件。"""


def figfix_user(path: str, code: str, why: str, tail: str) -> str:
    return f"""{FILE_FORMAT}

问题：{why}
--- 运行输出（尾部）---
{tail}
--- 当前文件 ---
<<<FILE: {path}>>>
{code}<<<END>>>"""


# -- 计划自检（roadmap M1-③） ---------------------------------------------- #
PLAN_CHECK_SYS = """你是严格的方法评审人，任务是**在写代码之前**核对这份 MVP 计划是否诚实。
逐条检查：
1. faithful_parts 里有没有「声称忠实、其实没实现」的？
2. simplified_parts 有没有**漏报**（实际简化了却没说）？
3. core_idea_to_implement 是不是论文真正的核心机制，而不是泛泛的替代品？
4. required_outputs 能不能支撑论文里那些图被「定性」复现？
不要美化、不要客气；宁可指出问题，也不要放过。只回复一个 JSON 对象。"""

PLAN_CHECK_SCHEMA = """{
 "verdict": "ok|revise|unfaithful",
 "issues": [{"claim": str, "problem": str, "severity": "high|medium|low", "fix": str}],
 "missing_simplifications": [str],
 "advice": str
}"""


def plan_check_user(ctx: str, plan: Dict[str, Any]) -> str:
    return f"""JSON schema：
{PLAN_CHECK_SCHEMA}

verdict 含义：ok = 计划诚实且可执行；revise = 改一两处即可诚实；unfaithful = 计划声称的能力与论文机制不符，必须降级声明。
severity 含义：high = 过度声明（把没实现的说成实现了）；medium = 会误导读者或让 demo 跑不起来；low = 提示。

=== 论文要点 ===
{ctx[:14000]}

=== 待核对的 MVP 计划 ===
{json.dumps(plan, ensure_ascii=False, indent=1)}"""


PLAN_FIX_SYS = """你在修正一份 MVP 计划，目标是**诚实**：补上漏报的简化、把不成立的能力声明改成实际做法。
不要新增无法实现的承诺，也不要删掉论文真正要实现的机制。只回复同一个 JSON schema（修正后的完整计划）。"""


CLAIMS_SYS = """你在把论文里的「可检验声明」翻译成**可机检的断言**。
只挑那些在 demo 产物上真的能被判对错的声明——例如：
- 「误差随 N 单调下降」→ monotonic(decreasing, 指向实际的曲线键)
- 「命中率 ≤ 1」→ bounds(max=1)
- 「占比求和 = 100%」→ sum_equals(target=1)
- 「计数非负」→ non_negative
**key 必须出自下方给出的可用键清单，不要编造键名**；暂时没有对应键的声明就**不要输出**。
每条都带上论文原文片段（quote）作为依据。只回复一个 JSON 对象。"""

CLAIMS_SCHEMA = """{
 "claims": [{
   "kind": "monotonic|bounds|non_negative|sum_equals|all_finite|non_empty",
   "key": str,                          // 必填，且必须来自可用键清单
   "direction": "increasing|decreasing", // 仅 monotonic
   "min": number|null, "max": number|null,   // 仅 bounds
   "target": number|null, "tol": number|null, // 仅 sum_equals
   "strict": bool,                      // 仅 monotonic，默认 false（允许平台段）
   "desc": str, "quote": str
 }]
}"""


def claims_user(ctx: str, metric_lines: List[str]) -> str:
    return f"""JSON schema：
{CLAIMS_SCHEMA}

=== 论文要点 ===
{ctx[:12000]}

=== demo 产物里可用的键（只能引用这里的键）===
{chr(10).join(metric_lines) or '（产物里没有任何键：请只输出空 claims 列表）'}"""


def plan_fix_user(ctx: str, plan: Dict[str, Any], check: Dict[str, Any]) -> str:
    return f"""JSON schema：
{PLAN_CHECK_SCHEMA.replace('"verdict": "ok|revise|unfaithful",', '"framework": "stdlib|numpy|torch", "core_idea_to_implement": str, "faithful_parts": [str], "simplified_parts": [{{"what": str, "why": str, "how": str}}], "required_outputs": [str],')}

=== 自检发现的问题 ===
{json.dumps(check.get("issues", []), ensure_ascii=False, indent=1)}
{chr(10) + json.dumps(check.get("missing_simplifications", []), ensure_ascii=False) if check.get("missing_simplifications") else ""}

=== 原计划 ===
{json.dumps(plan, ensure_ascii=False, indent=1)}

=== 论文要点（判据） ===
{ctx[:8000]}"""
