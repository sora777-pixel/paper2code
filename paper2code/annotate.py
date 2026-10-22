"""人工复核的初判模板（roadmap M1 的「人工评语」+ M6 的核验报告的复核入口）。

**为什么要有它**：保真度（1–5 分）是**人工**判定，模型评审只能辅助、不能替代。
但让评审的人从零翻产物，成本高且容易漏证据。这里把「机器已经知道的事」整理成一张
**证据齐全、留白待填**的复核表：

* 每篇一张卡：目标机制（人工标注）、demo 是否跑通、必需输出覆盖率、性质断言、出图情况、用量；
* **初判分数 + 理由**（由确定性规则给出，明确标注「待复核」），让人只需判断「同意 / 改分」；
* 复核清单（逐条打勾）：核心机制、简化说明是否诚实、数字是否只是回放、图是否只反映趋势；
* **证据缺口**：机器判不了的地方（例如「数值主张全部未检验」）单独列出，避免被误读成通过；
* 留白字段：`最终分数 / 核心机制 / 置信度 / 评语`。

**纪律**：初判**绝不自动写回**评测集。人工填好后由 `--apply` 显式落盘，且会打印 diff
（`fidelity_score` / `fidelity_source` / `fidelity_note`），避免「分数被悄悄改掉」。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

SHEET_MD = "review_sheet.md"
SHEET_JSON = "review_sheet.json"

#: 初判用到的阈值（与评测集/路线图口径一致）
COVERAGE_GOOD = 0.8
DRAW_RATE_GOOD = 0.7


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def draft_fidelity(res: Any) -> Dict[str, Any]:
    """确定性地给出「初判分数 + 理由 + 清单 + 证据缺口」。**这是建议，不是结论。**"""
    gen = str(getattr(res, "generator", "") or "")
    gaps: List[str] = []
    if getattr(res, "status", "ok") != "ok":
        return {"score": 1, "reason": "该用例没有跑完（%s），无法判定保真度。" % (res.error or res.status),
                "gaps": ["没有产物可复核"]}

    demo_ok = getattr(res, "demo_ok", None)
    cov = getattr(res, "required_coverage", None)
    if demo_ok is False:
        return {"score": 1, "reason": "demo 未跑通（契约或性质的对应步骤失败），谈不上保真。",
                "gaps": ["demo 未通过，需先修好再评"]}

    if gen.startswith("llm"):
        cov_known = cov is not None
        good_cov = (cov or 0) >= COVERAGE_GOOD
        no_fail = not getattr(res, "claims_fail", 0)
        # 覆盖率不可知时（评测集没声明 required_outputs）**不能当成「覆盖不足」来降分**——
        # 那是评测集缺少标注，不是 demo 的错。此时改用「demo 跑通 + 断言无 fail」判。
        if (cov_known and good_cov and no_fail) or (not cov_known and no_fail):
            score, reason = 4, ("LLM 写的实现：demo 跑通%s，性质断言无 fail —— 但要人看「简化说明」是否诚实，"
                                "故不给满分。" % ("、必需输出覆盖 %.0f%%" % (cov * 100) if cov_known else ""))
        else:
            score, reason = 3, ("LLM 写的实现：demo 跑通，但%s —— 机制可能只实现了一部分。"
                                % ("性质断言有 fail" if not no_fail else "必需输出覆盖不足（%s）" % _pct(cov)))
        if not cov_known:
            gaps.append("该用例未声明 `required_outputs`，覆盖率无法判分（初判改按 demo+断言+核验的证据给出）")
    elif gen.startswith("offline-template"):
        score, reason = 3, ("领域模板骨架：实现了该领域的通用骨架，**论文特有的部分留在 TODO**，"
                            "不属于「实现了论文那个方法」，故封顶 3 分。")
        gaps.append("论文特有机制未实现（见 run_demo.py 里的 TODO 标记）")
    else:
        score, reason = 2, "离线回放脚手架：回放论文表格、不实现论文方法本身（这是诚实标注，但保真度就是低）。"
        gaps.append("属于「回放论文自带数字」，不实现论文机制；要判保真度需走 LLM 路径或人工改写")

    if not getattr(res, "n_claims", 0):
        gaps.append("没有产生核验主张（无法核对论文声明）")
    elif (getattr(res, "tested_rate", None) or 0) < 0.5:
        gaps.append("多数主张「未检验」（多为回放论文自带数字，**不算验证**）")
    if getattr(res, "claims_fail", 0):
        gaps.append("有 %d 条性质断言 fail（论文声明在 demo 上不成立）" % res.claims_fail)
    if (getattr(res, "figure_specs", 0) or 0) and \
            (getattr(res, "figure_drawn", 0) or 0) / max(1, res.figure_specs) < DRAW_RATE_GOOD:
        gaps.append("图表出图率 %.0f%%（低于 70%%）" % (100.0 * res.figure_drawn / max(1, res.figure_specs)))
    return {"score": score, "reason": reason, "gaps": gaps}


def _pct(v: Optional[float]) -> str:
    return "-" if v is None else ("%.0f%%" % (v * 100))


def _plan_of(run_dir: Path) -> Dict[str, Any]:
    return _read_json(run_dir / "mvp" / "artifacts" / "plan.json")


def entry_card(entry: Dict[str, Any], res: Any) -> Dict[str, Any]:
    """一篇论文的复核卡（含证据路径与留白字段）。"""
    run_dir = Path(str(getattr(res, "run_dir", "") or ""))
    mvp = run_dir / "mvp"
    plan = _plan_of(run_dir)
    ann = entry.get("annotation") or {}
    draft = draft_fidelity(res)
    return {
        "id": entry.get("id"), "suite": entry.get("suite"), "source": entry.get("source"),
        "title": ann.get("title") or "",
        "mode": getattr(res, "mode", "") or "",
        "generator": getattr(res, "generator", "") or "",
        "core_mechanism": ann.get("core_mechanism", ""),
        "qualitative_conclusion": ann.get("qualitative_conclusion", ""),
        "current_annotation": {"fidelity_score": ann.get("fidelity_score"),
                               "source": ann.get("annotation_source", ""),
                               "note": ann.get("annotation_note", "")},
        "demo": {"ok": getattr(res, "demo_ok", None), "first_pass": getattr(res, "first_pass", None),
                 "fix_rounds": getattr(res, "fix_rounds", None)},
        "coverage": getattr(res, "required_coverage", None),
        "claims": {"pass": getattr(res, "claims_pass", 0), "fail": getattr(res, "claims_fail", 0),
                   "skip": getattr(res, "claims_skip", 0)},
        "verification": {"line": getattr(res, "verdict_line", "") or "",
                         "reproduced_rate": getattr(res, "reproduced_rate", None),
                         "tested_rate": getattr(res, "tested_rate", None),
                         "n_claims": getattr(res, "n_claims", 0)},
        "figures": {"specs": getattr(res, "figure_specs", 0), "drawn": getattr(res, "figure_drawn", 0)},
        "cost": {"tokens": getattr(res, "tokens", 0), "elapsed_s": getattr(res, "elapsed_s", 0.0),
                 "budget_tripped": getattr(res, "budget_tripped", "") or ""},
        "what_it_implements": plan.get("core_idea_to_implement", ""),
        "faithful_parts": plan.get("faithful_parts") or [],
        "simplified_parts": plan.get("simplified_parts") or [],
        "evidence": {
            "mvp_report": str(mvp / "MVP_REPORT.md"),
            "verification": str(getattr(res, "verification_md", "") or (run_dir / "VERIFICATION.md")),
            "demo_code": str(mvp / "demo" / "run_demo.py"),
            "screenshots_dir": str(mvp / "figures" / "out"),
        },
        "draft": draft,
        "human": {"fidelity_score": None, "mechanism": "", "confidence": "", "comment": ""},
    }


def build(eval_set: Dict[str, Any], results: Sequence[Any]) -> Dict[str, Any]:
    by_id = {getattr(r, "id", ""): r for r in results}
    cards = [entry_card(e, by_id[e.get("id")]) for e in eval_set.get("entries", []) if e.get("id") in by_id]
    return {"generated_at": datetime.now().isoformat(timespec="seconds"),
            "_howto": "把每张卡的 human.fidelity_score 填成 1–5（机制/置信度/评语可选），"
                      "再执行 `python -m paper2code review-sheet --apply <本文件>` 写回评测集。"
                      "draft 只是机器初判，**未写回评测集**，请以你的判断为准。",
            "entries": cards}


def render(sheet: Dict[str, Any]) -> str:
    L = ["# 人工复核表（保真度 1–5 分，**初判待复核**）\n",
         "> 生成时间：%s" % sheet.get("generated_at"),
         "> `draft` 是**确定性规则**给出的初判（不是模型打分、也**没有写回评测集**），"
         "请只把它当「先看哪里」的索引；四档核验口径见各卡的「核验」一行。\n",
         "## 怎么用\n",
         "1. 逐卡打开「证据」里的 `MVP_REPORT.md`，对照本卡的「目标机制」判断：**是不是实现了论文的核心机制**；",
         "2. 重点看报告里的「这个 demo 实现了什么」与「简化说明」——简化可以，**不诚实不行**；",
         "3. 填 `human.fidelity_score`（1–5）：1=跑不通/无实现，2=回放论文数字，3=领域骨架，4=实现了核心机制但有明确简化，5=核心机制+关键设置都对；",
         "4. 填好后：`python -m paper2code review-sheet --apply outputs/eval/llm/review_sheet.json`（会打印 diff 再落盘）。\n",
         "| 用例 | 生成方式 | demo | 覆盖率 | 断言(p/f/s) | 图 | 核验（已判定率） | 初判 |",
         "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for c in sheet.get("entries", []):
        L.append("| `%s` | %s | %s | %s | %d/%d/%d | %d/%d | %s | **%d/5** |" % (
            c["id"], c["generator"] or "-",
            {True: "通过", False: "失败", None: "未执行"}.get(c["demo"]["ok"], "-"),
            _pct(c["coverage"]), c["claims"]["pass"], c["claims"]["fail"], c["claims"]["skip"],
            c["figures"]["drawn"], c["figures"]["specs"],
            ("%s（判定 %s）" % (c["verification"]["line"] or "-", _pct(c["verification"]["tested_rate"]))),
            c["draft"]["score"]))
    for c in sheet.get("entries", []):
        L.append("\n---\n")
        L.append("## %s ｜ %s ｜ %s ｜ 生成方式 `%s` ｜ 初判 **%d/5**（待复核）\n" % (
            c["id"], c["suite"], c["mode"] or "-", c["generator"] or "-", c["draft"]["score"]))
        if c["core_mechanism"]:
            L.append("- **目标机制（人工标注）**：%s" % c["core_mechanism"])
        if c["qualitative_conclusion"]:
            L.append("- **可定性复现的结论（人工标注）**：%s" % c["qualitative_conclusion"])
        L.append("- **demo**：%s%s ｜ **必需输出覆盖** %s ｜ **断言** pass %d / fail %d / skip %d ｜ **图** %d/%d" % (
            {True: "通过", False: "失败", None: "未执行"}.get(c["demo"]["ok"], "-"),
            ("（首轮通过，修复 %s 轮）" % (c["demo"]["fix_rounds"] if c["demo"]["fix_rounds"] is not None else "-")
             if c["demo"]["ok"] else ""),
            _pct(c["coverage"]), c["claims"]["pass"], c["claims"]["fail"], c["claims"]["skip"],
            c["figures"]["drawn"], c["figures"]["specs"]))
        L.append("- **核验**：%s ｜ 已判定率 %s" % (c["verification"]["line"] or "-", _pct(c["verification"]["tested_rate"])))
        L.append("- **用量**：%s tokens ｜ %.0fs%s" % (
            c["cost"]["tokens"], c["cost"]["elapsed_s"],
            " ｜ ⚠️ 护栏触发：" + c["cost"]["budget_tripped"] if c["cost"]["budget_tripped"] else ""))
        if c["what_it_implements"]:
            L.append("- **报告自述「实现了什么」**：%s" % c["what_it_implements"][:300])
        if c["simplified_parts"]:
            L.append("- **报告自述「简化了什么」**：" + "；".join(
                "%s（%s）" % (s.get("what"), s.get("how")) for s in c["simplified_parts"][:4]))
        L.append("- **证据**：MVP 报告 `%s` ｜ 核验报告 `%s` ｜ demo 代码 `%s` ｜ 图 `%s`" % (
            c["evidence"]["mvp_report"], c["evidence"]["verification"],
            c["evidence"]["demo_code"], c["evidence"]["screenshots_dir"]))
        L.append("- **初判理由**：%s" % c["draft"]["reason"])
        if c["draft"]["gaps"]:
            L.append("- **证据缺口（机器判不了，需你裁决）**：")
            L.extend("  - %s" % g for g in c["draft"]["gaps"])
        L.append("- **复核清单**：")
        L.append("  - [ ] 是否实现了论文的**核心机制**（不是泛泛替代）")
        L.append("  - [ ] 「简化说明」是否诚实、无夸大")
        L.append("  - [ ] 数字是否只是回放论文自带值（**回放不算复现**）")
        L.append("  - [ ] 图是否只反映定性趋势（带水印，不是论文数值）")
        L.append("- **人工填写**：最终分数 `___/5` ｜ 核心机制 `是 / 部分 / 否` ｜ 置信度 `高 / 中 / 低` ｜ 评语：")
        if c["current_annotation"]["fidelity_score"] is not None:
            L.append("  - （评测集当前值：%s%s）" % (
                c["current_annotation"]["fidelity_score"],
                "，来源：%s" % c["current_annotation"]["source"] if c["current_annotation"]["source"] else ""))
    return "\n".join(L) + "\n"


def write(sheet: Dict[str, Any], out_dir: Path | str) -> Dict[str, str]:
    """写出复核表（Markdown 给人看、JSON 给 `--apply` 用）。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md, js = out / SHEET_MD, out / SHEET_JSON
    md.write_text(render(sheet), encoding="utf-8")
    js.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"markdown": str(md), "json": str(js)}


# --------------------------------------------------------------------------- #
# 把人工填好的分数写回评测集
# --------------------------------------------------------------------------- #
def merge_into_eval_set(eval_set_path: Path | str, filled: Dict[str, Any],
                        apply: bool = False) -> Dict[str, Any]:
    """把 `human.fidelity_score` 写回评测集的 annotation（默认**只演练不落盘**）。"""
    path = Path(eval_set_path)
    data = _read_json(path)
    if not data.get("entries"):
        return {"ok": False, "reason": "评测集读取失败或没有 entries：%s" % path, "changes": []}
    filled_by_id = {str(e.get("id")): (e.get("human") or {}) for e in filled.get("entries", [])}
    changes: List[Dict[str, Any]] = []
    for e in data["entries"]:
        h = filled_by_id.get(str(e.get("id"))) or {}
        score = h.get("fidelity_score")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            continue
        score = int(score)
        if not (1 <= score <= 5):
            changes.append({"id": e.get("id"), "status": "ignored", "reason": "分数必须在 1–5：%s" % score})
            continue
        ann = e.setdefault("annotation", {})
        old = ann.get("fidelity_score")
        ann["fidelity_score"] = score
        if h.get("mechanism"):
            ann["mechanism_verdict"] = str(h["mechanism"])
        if h.get("confidence"):
            ann["annotation_confidence"] = str(h["confidence"])
        if h.get("comment"):
            ann["fidelity_comment"] = str(h["comment"])
        ann["annotation_source"] = "人工复核（%s）" % datetime.now().date().isoformat()
        changes.append({"id": e.get("id"), "status": "updated" if old != score else "unchanged",
                        "old": old, "new": score, "comment": str(h.get("comment") or "")[:200]})
    result = {"ok": True, "applied": bool(apply), "path": str(path),
              "n_changed": sum(1 for c in changes if c.get("status") == "updated"),
              "changes": changes}
    if apply and result["n_changed"]:
        data["updated_at"] = datetime.now().date().isoformat()
        data["revision_note"] = ("人工复核写回保真度分数（%d 条）：由 review-sheet --apply 更新；"
                                 "此前分数为离线路径下的初判。" % result["n_changed"])
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def render_merge(result: Dict[str, Any]) -> str:
    L = ["## 人工复核写回%s\n" % ("（**已落盘**）" if result.get("applied") else "（演练，未落盘）"),
         "- 评测集：`%s`" % result.get("path"),
         "- 将更新 %d 条；未填分数的条目不动。" % result.get("n_changed", 0)]
    for c in result.get("changes", []):
        if c.get("status") == "updated":
            L.append("  - `%s`：%s → **%s**%s" % (c["id"], c.get("old"), c.get("new"),
                                                ("　%s" % c["comment"]) if c.get("comment") else ""))
        elif c.get("status") == "unchanged":
            L.append("  - `%s`：保持 %s（与当前值相同）" % (c["id"], c.get("new")))
        else:
            L.append("  - `%s`：跳过（%s）" % (c.get("id"), c.get("reason")))
    if not result.get("applied") and result.get("n_changed"):
        L.append("\n> 确认无误后加 `--yes` 落盘。")
    return "\n".join(L) + "\n"
