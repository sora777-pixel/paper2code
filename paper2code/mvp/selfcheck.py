"""计划自检（roadmap M1-③）：在**写代码之前**对照论文核对 MVP 计划是否诚实。

为什么需要这一步：LLM 写的计划很容易「看起来对但实现错了」——
把泛泛的替代品说成论文的机制、把真实存在的简化悄悄藏起来。
这一步专门拦这个，产出的结论会写进 `plan.json` 与 `MVP_REPORT.md`。

两条路径：

* **LLM 路径**：让模型以「严格评审人」视角逐条核对 `faithful_parts` / `simplified_parts`，
  给出 `verdict` 与问题清单；判定为 `revise` / `unfaithful` 时**只允许修订一次**，
  修订后重检（额外调用次数有上界，便于 M1-⑥ 做成本护栏）。
* **离线路径（永远可用）**：确定性**规则**自检，只检查「声明是否完整」——
  简化项有没有漏报、有没有可绘图的数据键、依赖与框架是否自洽。
  **语义层的忠实度判断明确交给模型，规则不冒充它**（宁可少说，不可乱说）。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from . import prompts
from .llmio import LLMUnavailable, ask_json

# 「可绘图的数据键」的廉价判据：曲线/消融/扫参/指标，或 {x,y} / values 形态
_DATA_KEY = re.compile(
    r"(curves?|ablation|sweep|series|loss|accuracy|nmi|ari|throughput|latency|rmsd|rg|sasa|fel|dccm|"
    r"metrics\.)",
    re.I,
)
_DATA_SHAPE = re.compile(r"\.(y|values|x|points|curve)$", re.I)


def _issue(claim: str, problem: str, severity: str, fix: str) -> Dict[str, str]:
    return {"claim": claim, "problem": problem, "severity": severity, "fix": fix}


def _has_data_keys(required: List[str]) -> bool:
    return any(_DATA_KEY.search(k) or _DATA_SHAPE.search(k) for k in required)


def rule_check(paper: Any, plan: Dict[str, Any]) -> Dict[str, Any]:
    """确定性规则自检：只判「声明完整性」，不做语义忠实度判断。"""
    issues: List[Dict[str, str]] = []
    core = (plan.get("core_idea_to_implement") or "").strip()
    faithful = plan.get("faithful_parts") or []
    simplified = plan.get("simplified_parts") or []
    required = plan.get("required_outputs") or []
    framework = str(plan.get("framework") or "")
    deps = plan.get("dependencies") or []

    # 计划自己承认「没有实现论文方法」时，与它相关的提醒降级为提示（声明过的局限不是不诚实）
    admits_no_method = any("论文方法" in str(s.get("what") or "") for s in simplified)

    if not core:
        issues.append(_issue("core_idea_to_implement 为空", "没说明要实现论文的哪个核心机制", "high",
                             "从方法章节提炼一句话，写清要实现的核心机制"))
    if not simplified:
        issues.append(_issue("未声明任何简化（simplified_parts 为空）",
                             "没有任何简化声明，等于声称 100% 忠实——这通常是过度声明", "high",
                             "逐条列出相对论文简化了什么（数据规模、维度、迭代步数、精度等）"))
    if not faithful:
        issues.append(_issue("faithful_parts 为空", "没说明哪些部分是真忠实于论文的，人工无从核对", "low",
                             "列出忠实实现的部分，便于逐条对照论文"))

    n_fig = len(getattr(paper, "figures", []) or [])
    if n_fig and not _has_data_keys(required):
        issues.append(_issue(
            "论文有 %d 张图，但 required_outputs 里没有可绘图的数据键" % n_fig,
            "demo 不会产出曲线/消融/扫参这类键，论文的图无法被定性复现",
            "low" if admits_no_method else "medium",
            "按图清单补 required_outputs（如 curves.<name>.{x,y}、ablation.{names,values}）"))

    if framework and framework != "stdlib" and framework not in deps:
        issues.append(_issue("framework=%s 未列入 dependencies" % framework,
                             "声明的框架没写进依赖清单，别人按计划跑不起来", "medium", "把框架补进 dependencies"))

    verdict = "ok" if not any(i["severity"] in ("high", "medium") for i in issues) else "revise"
    return {"verdict": verdict, "issues": issues, "mode": "rule", "checked": _CHECKS}


_CHECKS = ["声明简化项", "声明忠实项", "核心机制非空", "可绘图数据键", "框架与依赖一致"]


# --------------------------------------------------------------------------- #
def _llm_check(provider: Any, ctx: str, plan: Dict[str, Any]) -> Dict[str, Any]:
    data = ask_json(provider, "mvp_plan_check", prompts.PLAN_CHECK_SYS, prompts.plan_check_user(ctx, plan))
    if not isinstance(data, dict) or data.get("verdict") not in ("ok", "revise", "unfaithful"):
        raise ValueError("自检输出缺少合法 verdict")
    issues = []
    for it in (data.get("issues") or [])[:20]:
        if not isinstance(it, dict):
            continue
        issues.append(_issue(str(it.get("claim") or ""), str(it.get("problem") or ""),
                             str(it.get("severity") or "medium").lower(), str(it.get("fix") or "")))
    for miss in (data.get("missing_simplifications") or [])[:10]:
        issues.append(_issue("漏报的简化", str(miss), "high", "把这条简化如实写进 simplified_parts"))
    return {"verdict": data["verdict"], "issues": issues, "mode": "llm",
            "advice": str(data.get("advice") or ""), "checked": _CHECKS}


def _llm_revise(provider: Any, ctx: str, plan: Dict[str, Any], check: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        data = ask_json(provider, "mvp_plan_fix", prompts.PLAN_FIX_SYS,
                        prompts.plan_fix_user(ctx, plan, check))
    except (LLMUnavailable, ValueError):
        return None
    if not isinstance(data, dict) or not (data.get("core_idea_to_implement") or "").strip():
        return None
    for k in ("required_outputs", "simplified_parts", "faithful_parts", "demo_files", "dependencies"):
        data.setdefault(k, [])
    return data


def prepare(paper: Any, plan: Dict[str, Any], provider: Any = None, ctx: str = "",
            llm_mode: bool = False) -> Dict[str, Any]:
    """跑一次计划自检并写回 `plan['selfcheck']`；必要时（且仅一次）修订计划。

    永不抛异常：LLM 不可用即回退到规则自检，绝不让自检环节打断流水线。
    """
    plan = dict(plan or {})
    check: Optional[Dict[str, Any]] = None
    if llm_mode:
        try:
            check = _llm_check(provider, ctx, plan)
        except (LLMUnavailable, ValueError):
            check = None
    if check is None:
        check = rule_check(paper, plan)

    if llm_mode and check.get("verdict") in ("revise", "unfaithful"):
        fixed = _llm_revise(provider, ctx, plan, check)
        if fixed:
            try:
                recheck = _llm_check(provider, ctx, fixed)
            except (LLMUnavailable, ValueError):
                recheck = rule_check(paper, fixed)
            recheck["revised_from"] = check.get("verdict")
            recheck["revised"] = True
            fixed["selfcheck"] = recheck
            return fixed

    plan["selfcheck"] = check
    return plan
