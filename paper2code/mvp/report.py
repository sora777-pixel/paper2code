"""MVP 报告（模板生成，不调用 LLM：诚实、稳定、可复核）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

_GEN_ZH = {
    "llm": "大模型生成的 MVP（忠实度取决于模型与论文，请对照下方「简化说明」）",
    "offline-scaffold": "离线脚手架（回放论文表格 + 执行已翻译伪代码；**不是论文方法的实现**，配置 LLM 后可生成真正的 MVP）",
    "offline-scaffold (llm demo failed)": "离线脚手架（LLM 生成的 demo 多轮修复仍失败，已回退；失败现场保留在 demo_llm_failed/）",
}
_STATE_ZH = {"ok": "脚本出图成功", "builtin_only": "内置渲染器出图", "skipped": "仅有说明（未出图）", "error": "出图失败"}
_REPRO_ZH = {"from_paper_data": "论文自带数据重绘", "from_demo": "demo 玩具数据（定性）",
             "schematic": "示意图", "not_reproducible": "无法复现"}
_VERDICT_ZH = {"ok": "**ok**（诚实）", "revise": "**revise**（需修订）", "unfaithful": "**unfaithful**（不忠实）"}
_CLAIM_ZH = {"pass": "✅ pass", "fail": "❌ fail", "skip": "⏭️ skip"}


def write_report(paper: Any, plan: Dict[str, Any], res: Any, out: Path) -> str:
    L = ["# MVP 自动生成报告\n", "**论文**：%s（`%s`）\n" % (paper.title, paper.id),
         "## 一眼看结论\n",
         "- **生成方式**：%s" % _GEN_ZH.get(res.generator, res.generator)]
    if res.demo_ok is None:
        L.append("- **运行结果**：未执行（只生成了代码）")
    else:
        last = res.attempts[-1] if res.attempts else {}
        L.append("- **运行结果**：smoke 运行 **%s**，共尝试 %d 次（%s）" % (
            "通过" if res.demo_ok else "失败", len([a for a in res.attempts if "ok" in a]), last.get("why", "")))
    ok_figs = sum(1 for v in res.figures.values() if v.get("files"))
    L.append("- **图表**：共 %d 张说明，其中 %d 张已出图；其余均写明了无法复现的原因" % (len(res.figures), ok_figs))
    L.append("")
    L.append("## 这个 demo 实现了什么\n")
    L.append(plan.get("core_idea_to_implement", "") + "\n")
    if plan.get("faithful_parts"):
        L.append("**忠实于论文的部分**：" + "；".join(plan["faithful_parts"]) + "\n")
    dep = plan.get("resolved_dependencies") or {}
    if dep:
        from .. import deps as _deps

        L.append("**依赖**：" + _deps.describe(dep) + "\n")
    L.append("## 简化说明（信任任何数字之前先读这里）\n")
    for s in plan.get("simplified_parts", []):
        L.append("- **%s** —— %s（做法：%s）" % (s.get("what"), s.get("why"), s.get("how")))
    L.append("")
    sc = getattr(res, "selfcheck", None) or {}
    if sc:
        L.append("## 计划自检（写码前对照论文核对计划是否诚实）\n")
        L.append("- **判定**：%s ｜ 方式：%s%s" % (
            _VERDICT_ZH.get(sc.get("verdict"), sc.get("verdict", "?")),
            "模型评审（LLM）" if sc.get("mode") == "llm" else "确定性规则（离线）",
            "；已按评审意见修订一次" if sc.get("revised") else ""))
        if sc.get("advice"):
            L.append("- **评审建议**：%s" % sc["advice"])
        issues = sc.get("issues") or []
        if issues:
            L.append("- **发现的问题**：")
            for it in issues:
                L.append("  - `%s` **%s** —— %s（建议：%s）" % (
                    it.get("severity", "?"), it.get("claim", ""), it.get("problem", ""), it.get("fix", "")))
        else:
            L.append("- 未发现诚实性问题。")
        if sc.get("verdict") == "revise" and sc.get("mode") == "llm":
            L.append("  - 注意：修订后仍判 revise，说明计划与论文仍有出入，请人工核对「简化说明」。")
        L.append("")
    cl = getattr(res, "claims", None) or {}
    if cl:
        rows = cl.get("results") or []
        L.append("## 性质断言（从论文的「可检验声明」生成；fail 即判本 demo 失败）\n")
        if not rows:
            L.append("- %s" % (cl.get("note") or "本次没有可求值的断言。"))
        else:
            L.append("- **结论**：pass %d / fail %d / skip %d%s" % (
                cl.get("n_pass", 0), cl.get("n_fail", 0), cl.get("n_skip", 0),
                "　→ 该 demo 判定为**失败**" if cl.get("failed") else ""))
            L.append("")
            L.append("| 状态 | 断言 | 依据 | 实测 |")
            L.append("|---|---|---|---|")
            for r in rows:
                c = r.get("claim") or {}
                basis = (c.get("quote") or c.get("desc") or "")[:120] or c.get("key") or ""
                L.append("| %s | `%s` | %s | %s |" % (
                    _CLAIM_ZH.get(r.get("status"), r.get("status", "?")),
                    c.get("key") or c.get("kind") or "", basis.replace("|", "/"),
                    str(r.get("detail", "")).replace("|", "/")))
            L.append("")
            L.append("> `skip` **不等于通过**：判不了的断言如实列出，且不计入通过率。")
        L.append("")
    bd = getattr(res, "budget", None) or {}
    us = bd.get("usage") or {}
    if bd and us.get("calls"):
        lim = bd.get("limit") or {}
        L.append("## 成本护栏（每篇论文的硬上限）\n")
        L.append("- **用量**：LLM 调用 %s 次 · prompt %s + completion %s = **%s tokens** · 本次耗时 %.1fs" % (
            us.get("calls", 0), us.get("prompt_tokens", 0), us.get("completion_tokens", 0),
            us.get("total_tokens", 0), float(bd.get("elapsed_s") or 0.0)))
        L.append("- **上限**：调用 %s 次 / %s tokens / %s 秒" % (
            lim.get("max_calls"), lim.get("max_tokens"), lim.get("max_seconds")))
        L.append("- **结论**：%s" % ("⚠️ **触发过** —— " + str(bd.get("tripped")) if bd.get("tripped")
                                     else "未触发（未超限）"))
        L.append("")
    sb = getattr(res, "sandbox", None) or {}
    if sb:
        L.append("## 执行沙箱（这段代码跑在哪、隔离到什么程度）\n")
        L.append("- **执行器**：`%s` ｜ **隔离级别**：%s ｜ **风险**：%s" % (
            sb.get("mode", "-"), sb.get("isolation", "-"), "高" if sb.get("risk") == "high" else "低"))
        L.append("- **网络**：%s" % sb.get("network", "-"))
        if sb.get("degraded"):
            L.append("- ⚠️ **已降级**：%s" % sb.get("reason", ""))
        elif sb.get("reason"):
            L.append("- 说明：%s" % sb.get("reason"))
        L.append("")
    vs = getattr(res, "version", None) or {}
    live = vs.get("live") or {}
    hist = vs.get("history") or []
    if live or hist:
        L.append("## 版本与改动（「改一次」的历史）\n")
        L.append("- **当前版本**：v%s ｜ 生成方式：`%s` ｜ 本次指令：%s ｜ 更新时间：%s" % (
            live.get("n", 1), live.get("generator", "-"),
            ("「%s」" % live["instruction"]) if live.get("instruction") else "（首次生成）",
            live.get("updated_at", "-")))
        mods = getattr(res, "modified_files", None) or []
        if mods:
            L.append("- **本次改动的文件**：%s" % "、".join("`%s`" % m for m in mods))
        for e in sorted(hist, key=lambda x: int(x.get("n", 0)), reverse=True):
            diff = e.get("diff") or ""
            tail = ("；改动明细见 `%s`（%s 行 diff）" % (diff, e.get("diff_lines"))
                    if diff else "；无 diff（该版与上一版无文本差异）")
            L.append("- v%s 存档于 %s，指令：%s%s" % (
                e.get("n"), e.get("archived_at", "-"),
                ("「%s」" % e["instruction"]) if e.get("instruction") else "（首次生成）", tail))
        L.append("\n> 每一版都留在 `mvp/versions/vN/`；diff 是文本级的，二进制产物只列增删/大小变化。\n")
    ex = getattr(res, "exported", None) or {}
    if ex.get("ok"):
        L.append("## 导出（Colab / Streamlit）\n")
        L.append("- **自包含 notebook**：`%s`（%s 个单元格；demo 与绘图脚本都写在 notebook 里，可整段运行）"
                 % (ex.get("notebook_rel"), ex.get("n_cells")))
        L.append("- **Streamlit 小演示**：`%s`（只读已跑出来的产物，不重新执行代码）" % ex.get("streamlit_rel"))
        L.append("- 用法与「一键 Colab」的说明见 `%s`%s"
                 % (ex.get("readme_rel"), "；Colab 链接：%s" % ex["colab_url"] if ex.get("colab_url") else ""))
        L.append("")
    L.append("## 怎么自己跑\n\n```bash\ncd demo\npython run_demo.py          # 无参数 = smoke，写出 results/metrics.json\n"
             "cd ../figures\npython run_figures.py      # 重新绘制所有图（需要 matplotlib；不需要 LLM）\n```\n")
    L.append("## 图表清单\n\n| 图 | 论文位置 | 数据来源 | 状态 | 产物 |\n|---|---|---|---|---|")
    for s in res.figure_specs:
        st = res.figures.get(s["id"], {})
        files = "、".join("`%s`" % f for f in st.get("files", [])) or "—"
        L.append("| %s | %s | %s | %s | %s |" % (s["id"], s["paper_ref"], _REPRO_ZH.get(s["reproducibility"], ""),
                                                  _STATE_ZH.get(st.get("state", ""), st.get("state", "")), files))
    L.append("\n逐图的绘制信息（类型、坐标轴、系列→数据键、风格）见 `%s`。demo 玩具数据的图带有水印，**只反映定性趋势，不是论文数值**。\n" % res.figures_md)
    if res.warnings:
        L.append("## 注意事项\n")
        L.extend("- %s" % w for w in dict.fromkeys(res.warnings))
        L.append("")
    path = Path(out) / "MVP_REPORT.md"
    path.write_text("\n".join(L), encoding="utf-8")
    return str(path)
