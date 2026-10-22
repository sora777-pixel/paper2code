"""评测闭环（roadmap M1）：一键跑评测集、汇总指标、与基线对比。

设计原则与本仓库一致：

* **纯标准库**：离线路径确定性可复算，不需要 API Key / 网络；
* **分母写清楚**：`skipped` 不计入通过（"判不了"不等于"通过"）；
* **分层报告**：L1 合成样本 / L2 真实论文分开报，**禁止求平均掩盖真实退化**
  （理由见落地清单 5.1：自建样本 100% 通过不构成质量证据）。

用法::

    python -m paper2code eval                       # 跑 configs/eval_set.json
    python -m paper2code eval --compare outputs/eval/latest.json   # 与基线对比
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .config import get_settings
from .pipeline import run_paper

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SET = ROOT / "configs" / "eval_set.json"

# 路线图 M1 的目标值（用于报告里"目标 vs 实测"）
TARGETS = {
    "smoke_pass_rate": 0.90,
    "figure_draw_rate": 0.70,
    "fidelity_mean": 3.5,
    # roadmap「度量仪表盘」另外两项：不依赖回退的通过率（M3 起 ≥80%）与离线路径可用率（100%）
    "no_fallback_pass_rate": 0.60,
    "offline_available_rate": 1.00,
}
#: M3 之后「不依赖回退」的目标提到 80%（见路线图仪表盘那一行）
TARGETS_M3 = {"no_fallback_pass_rate": 0.80, "fidelity_mean": 4.0}


@dataclass
class EntryResult:
    id: str
    suite: str
    source: str
    status: str = "ok"            # ok | missing | error
    error: str = ""
    mode: Optional[str] = None
    mode_ok: Optional[bool] = None
    verdict: str = ""
    verdict_ok: Optional[bool] = None
    structure_hit: Optional[float] = None
    structure_detail: Dict[str, Any] = field(default_factory=dict)
    demo_ok: Optional[bool] = None
    generator: str = ""
    figure_specs: int = 0
    figure_drawn: int = 0
    required_outputs: List[str] = field(default_factory=list)
    required_coverage: Optional[float] = None
    first_pass: Optional[bool] = None
    fix_rounds: Optional[int] = None
    elapsed_s: float = 0.0
    tokens: Optional[int] = 0     # 离线=0；接 LLM 后由 provider 统计（当前未实现）
    warnings: int = 0
    fidelity: Optional[int] = None
    claims_pass: int = 0          # 性质断言通过条数（M1-④）
    claims_fail: int = 0          # 性质断言失败条数
    claims_skip: int = 0          # 判不了的断言条数（skip ≠ pass）
    budget_tripped: str = ""      # 成本护栏是否触发过（M1-⑥）
    # -- M6-① 声明级核验（复现榜单的输入） -------------------------------- #
    n_claims: int = 0
    n_reproduced: int = 0
    n_partial: int = 0
    n_not_reproducible: int = 0
    n_untested: int = 0
    reproduced_rate: Optional[float] = None   # 已复现 / 已判定
    tested_rate: Optional[float] = None       # 已判定 / 全部主张
    verdict_line: str = ""
    verification_md: str = ""
    run_dir: str = ""              # 该用例的产物目录（供人工复核模板定位证据）

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


# --------------------------------------------------------------------------- #
def load_eval_set(path: Path) -> Dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data.get("entries"), list):
        raise ValueError("评测集格式不对：缺少 entries 列表")
    return data


def _resolve_source(source: str) -> Optional[Path]:
    p = Path(source)
    if not p.is_absolute():
        p = ROOT / source
    return p if p.exists() else None


def _structure_hit(stats: Dict[str, Any], expect: Dict[str, Any]) -> tuple:
    checks = {
        "sections": expect.get("sections_min"),
        "tables": expect.get("tables_min"),
        "figures": expect.get("figures_min"),
        "algorithms": expect.get("algorithms_min"),
        "code_refs": expect.get("code_refs_min"),
    }
    detail: Dict[str, Any] = {}
    ok = total = 0
    for key, minimum in checks.items():
        if minimum is None:
            continue
        got = int(stats.get(key) or 0)
        total += 1
        passed = got >= int(minimum)
        ok += 1 if passed else 0
        detail[key] = {"got": got, "min": int(minimum), "ok": passed}
    return (ok / total if total else None), detail


def _required_coverage(mvp_dir: Path, required: Sequence[str]) -> Optional[float]:
    if not required:
        return None
    import json as _json

    from .mvp.textio import get_path

    mp = mvp_dir / "demo" / "results" / "metrics.json"
    if not mp.exists():
        return None
    try:
        data = _json.loads(mp.read_text(encoding="utf-8"))
    except ValueError:
        return None
    hit = 0
    for key in required:
        v = get_path(data, key)
        if v is not None and (not isinstance(v, (list, dict)) or len(v) > 0):
            hit += 1
    return hit / len(required)


def run_entry(entry: Dict[str, Any], runs_root: Path, provider=None, use_llm: bool = False) -> EntryResult:
    res = EntryResult(id=entry["id"], suite=entry.get("suite", ""), source=entry["source"])
    ann = entry.get("annotation") or {}
    res.fidelity = ann.get("fidelity_score")
    expect = entry.get("expect") or {}
    res.required_outputs = list(expect.get("required_outputs") or [])

    src = _resolve_source(entry["source"])
    if src is None:
        res.status = "missing"
        res.error = "评测集里登记的路径不存在（真实论文语料不入库，需自行放置）"
        return res

    out = runs_root / entry["id"]
    t0 = time.time()
    try:
        m = run_paper(str(src), output_dir=out, explain=(), reproduce=True, audio=False, mvp=True,
                      provider=provider)
        res.elapsed_s = time.time() - t0
    except Exception as exc:
        res.status = "error"
        res.error = "%s: %s" % (type(exc).__name__, exc)
        return res

    stats = m.get("stats") or {}
    art = m.get("artifacts") or {}
    rp = art.get("reproduce") or {}
    mv = art.get("mvp") or {}

    res.mode = rp.get("mode")
    res.mode_ok = None if expect.get("mode") is None else (res.mode == expect.get("mode"))
    res.verdict = (rp.get("checks") or {}).get("verdict") or ""
    res.verdict_ok = None if expect.get("verdict") is None else (res.verdict == expect.get("verdict"))
    res.structure_hit, res.structure_detail = _structure_hit(stats, expect)
    res.generator = mv.get("generator") or ""
    res.demo_ok = mv.get("demo_ok")
    res.figure_specs = int(mv.get("n_figure_specs") or 0)
    res.figure_drawn = int(mv.get("n_figures_drawn") or 0)
    # 每篇论文的产物落在 <out>/<paper_id>/ 里（不是直接落在 out/ 下）。**以 manifest 里的绝对
    # 路径为准**，拿不到才退回「按用例 id 猜目录」——路径层级一变就静默读不到（曾导致
    # 断言 / 修复轮次 / 成本这几列长期显示为 0，而 0 看起来「正常」，没人发现）。
    run_dir, mvp_dir = _resolve_dirs(out, entry["id"], art)
    res.run_dir = str(run_dir)
    res.required_coverage = _required_coverage(mvp_dir, res.required_outputs)
    res.warnings = len(m.get("warnings") or [])

    # MVP 细节：修复轮次 / 性质断言 / 成本护栏（都只在有 mvp_result.json 时可读）
    mvp_json = mvp_dir / "mvp_result.json"
    mvp_data: Dict[str, Any] = {}
    if mvp_json.exists():
        try:
            mvp_data = json.loads(mvp_json.read_text(encoding="utf-8")) or {}
        except ValueError:
            mvp_data = {}
    attempts = mvp_data.get("attempts") or []
    if attempts:
        res.first_pass = bool(attempts[0].get("ok"))
        res.fix_rounds = max(0, len(attempts) - 1)
    cl = mvp_data.get("claims") or {}
    res.claims_pass = int(cl.get("n_pass") or 0)
    res.claims_fail = int(cl.get("n_fail") or 0)
    res.claims_skip = int(cl.get("n_skip") or 0)
    res.tokens = int((mvp_data.get("usage") or {}).get("total_tokens") or 0)
    res.budget_tripped = str((mvp_data.get("budget") or {}).get("tripped") or "")

    # M6-①：声明级核验（复现榜单的数据源）
    ver = art.get("verification") or {}
    ver_json = Path(str(ver.get("json"))) if ver.get("json") else (run_dir / "verification.json")
    if ver_json.exists():
        try:
            vdata = json.loads(ver_json.read_text(encoding="utf-8")) or {}
        except ValueError:
            vdata = {}
        vs = vdata.get("summary") or {}
        res.n_claims = int(vs.get("n_claims") or 0)
        res.n_reproduced = int(vs.get("n_reproduced") or 0)
        res.n_partial = int(vs.get("n_partial") or 0)
        res.n_not_reproducible = int(vs.get("n_not_reproducible") or 0)
        res.n_untested = int(vs.get("n_untested") or 0)
        res.reproduced_rate = vs.get("reproduced_rate")
        res.tested_rate = vs.get("tested_rate")
        res.verdict_line = str(vs.get("verdict_line") or "")
        res.verification_md = str(ver.get("markdown") or (run_dir / "VERIFICATION.md"))
    return res


def _resolve_dirs(out: Path, entry_id: str, art: Dict[str, Any]) -> tuple:
    """定位某条用例的 ``(<run目录>, <mvp目录>)``。

    优先用 **manifest 记录的绝对路径**（``artifacts.mvp.report`` / ``artifacts.verification.json``），
    这比按目录层级猜可靠得多；拿不到再退回 :func:`_run_dir`。
    """
    mvp = art.get("mvp") or {}
    rep = str(mvp.get("report") or "")
    if rep:
        mvp_dir = Path(rep).parent
        if mvp_dir.exists():
            return mvp_dir.parent, mvp_dir
    ver = art.get("verification") or {}
    js = str(ver.get("json") or "")
    if js:
        p = Path(js)
        if p.exists():
            return p.parent, p.parent / "mvp"
    d = _run_dir(out, entry_id)
    return d, d / "mvp"


def _run_dir(out: Path, entry_id: str) -> Path:
    """定位某条用例的**真实产物目录**。

    流水线写的是 ``<out>/<paper_id>/``，而用例 id 与 paper_id 在评测集里通常一致；
    不一致时退一步找 ``<out>/<用例id>/``，再退一步找 ``<out>/`` 里唯一的子目录，最后退回 ``out``。
    """
    cand = out / entry_id
    if cand.exists():
        return cand
    subs = [p for p in out.iterdir() if p.is_dir() and not p.name.startswith("_")] if out.exists() else []
    if len(subs) == 1:
        return subs[0]
    return out


# --------------------------------------------------------------------------- #
_FIG_NA = "-"


def _rate(pairs: Sequence[Any]) -> Optional[float]:
    vals = [v for v in pairs if v is not None]
    if not vals:
        return None
    return sum(1 for v in vals if v) / len(vals)


def _mean(vals: Sequence[Any]) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    return (sum(clean) / len(clean)) if clean else None


def summarize(results: Sequence[EntryResult]) -> Dict[str, Any]:
    def block(rows: Sequence[EntryResult]) -> Dict[str, Any]:
        usable = [r for r in rows if r.status == "ok"]
        draw = [(r.figure_drawn / r.figure_specs) if r.figure_specs else None for r in usable]
        return {
            "n": len(rows),
            "n_ok": len(usable),
            "smoke_pass_rate": _rate([r.demo_ok for r in usable]),
            "mode_ok_rate": _rate([r.mode_ok for r in usable]),
            "verdict_ok_rate": _rate([r.verdict_ok for r in usable]),
            "structure_hit_mean": _mean([r.structure_hit for r in usable]),
            "figure_draw_rate": _mean(draw),
            "required_outputs_coverage": _mean([r.required_coverage for r in usable]),
            "fidelity_mean": _mean([r.fidelity for r in usable]),
            "elapsed_mean_s": _mean([r.elapsed_s for r in usable]),
            "tokens_total": sum((r.tokens or 0) for r in usable),
            "claims_pass_total": sum(r.claims_pass for r in usable),
            "claims_fail_total": sum(r.claims_fail for r in usable),
            "claims_skip_total": sum(r.claims_skip for r in usable),
            # roadmap M3-① 验收：领域模板覆盖率（≥60%）。LLM 路径写的是「论文专有实现」，
            # 与模板同属「不靠通用回放」的一类，故一并计入覆盖。
            "template_covered": sum(1 for r in usable if _is_domain_covered(r.generator)),
            "template_covered_rate": _rate([_is_domain_covered(r.generator) for r in usable]),
            # M6-①：声明级核验汇总（复现榜单的底数）
            "claims_total": sum(r.n_claims for r in usable),
            "claims_reproduced": sum(r.n_reproduced for r in usable),
            "claims_partial": sum(r.n_partial for r in usable),
            "claims_not_reproducible": sum(r.n_not_reproducible for r in usable),
            "claims_untested": sum(r.n_untested for r in usable),
            "reproduced_rate": _mean([r.reproduced_rate for r in usable]),
            "tested_rate": _mean([r.tested_rate for r in usable]),
            # roadmap「度量仪表盘」两项（此前只算了 smoke/图/保真度，这两项一直缺）
            # 不依赖回退的通过率：demo 通过 **且** 生成方式不是「纯回放脚手架」
            "no_fallback_pass_rate": _rate([bool(r.demo_ok) and _is_domain_covered(r.generator)
                                            for r in usable]),
            # 离线路径可用率：跑过的用例里产出完整 manifest 的比例（离线路径永不因无 LLM/网络而拿不到产物）
            "offline_available_rate": (len(usable) / len(rows)) if rows else None,
        }

    by_suite: Dict[str, Dict[str, Any]] = {}
    for suite in dict.fromkeys(r.suite for r in results):
        by_suite[suite] = block([r for r in results if r.suite == suite])
    return {"overall": block(results), "by_suite": by_suite}


def _pct(v: Optional[float]) -> str:
    return _FIG_NA if v is None else "%.1f%%" % (v * 100)


def _is_domain_covered(generator: str) -> bool:
    """走领域模板或 LLM 写码 = 不是「通用表格回放」，计入 M3-① 模板覆盖。"""
    g = generator or ""
    return g.startswith("offline-template:") or g.startswith("llm")


def _rat(v: Optional[float]) -> str:
    return _FIG_NA if v is None else "%.2f" % v


def _goal(key: str, value: Optional[float]) -> str:
    if value is None or key not in TARGETS:
        return ""
    return "✅" if value >= TARGETS[key] else "⚠️"


def dashboard_markdown(summary: Dict[str, Any], settings: Any = None) -> str:
    """roadmap「度量仪表盘（M1 起常驻）」：把路线图的六项目标逐条对实测。

    六项＝smoke 通过率 / 不依赖回退的通过率 / 保真度均分 / 图表出图率 / 单篇耗时与成本 / 离线路径可用率。
    目标值取自路线图仪表盘那一节（含 M3 之后的加严档）；超限或未达标一律标 ⚠️，不粉饰。
    """
    ov = summary.get("overall") or {}
    st = settings or get_settings()

    def row(name: str, actual: str, target: str, ok: Optional[bool], note: str = "") -> tuple:
        return (name, actual, target, "-" if ok is None else ("✅" if ok else "⚠️"), note or "-")

    rows = [
        row("smoke 最终通过率（含回退）", _pct(ov.get("smoke_pass_rate")), "≥90.0%",
            None if ov.get("smoke_pass_rate") is None
            else (ov["smoke_pass_rate"] >= TARGETS["smoke_pass_rate"]), "含回退脚手架"),
        row("不依赖回退的通过率", _pct(ov.get("no_fallback_pass_rate")),
            "≥60%（M1）/ ≥{0:.0f}%（M3）".format(TARGETS_M3["no_fallback_pass_rate"] * 100),
            None if ov.get("no_fallback_pass_rate") is None
            else (ov["no_fallback_pass_rate"] >= TARGETS["no_fallback_pass_rate"]),
            "打回退不算数：只认领域模板 / LLM 写码"),
        row("保真度均分（人工标注）", _rat(ov.get("fidelity_mean")),
            "≥3.5（M1）/ ≥{0:.1f}（M3）".format(TARGETS_M3["fidelity_mean"]),
            None if ov.get("fidelity_mean") is None else (ov["fidelity_mean"] >= TARGETS["fidelity_mean"]),
            "来自评测集人工标注"),
        row("图表出图率", _pct(ov.get("figure_draw_rate")), "≥70.0%",
            None if ov.get("figure_draw_rate") is None
            else (ov["figure_draw_rate"] >= TARGETS["figure_draw_rate"]),
            "not_reproducible 不计入失败"),
    ]
    elapsed = ov.get("elapsed_mean_s")
    cap = int(getattr(st, "task_timeout_s", 0) or 0)
    rows.append(row("单篇耗时 / 成本",
                    "%s ｜ %s tokens" % (("%.1fs" % elapsed) if elapsed else "-", ov.get("tokens_total") or 0),
                    ("上限 %ds / 单篇" % cap) if cap else "设上限",
                    None if elapsed is None else bool(cap) and elapsed < cap,
                    "超限即降级（任务级超时 task_timeout_s）"))
    rows.append(row("离线路径可用率", _pct(ov.get("offline_available_rate")), "100%",
                    None if ov.get("offline_available_rate") is None
                    else (ov["offline_available_rate"] >= TARGETS["offline_available_rate"]),
                    "任何时候都不能因 LLM/网络而拿不到产物"))

    L = ["## 三之二、度量仪表盘（路线图常驻指标）\n",
         "| 指标 | 实测 | 目标 | 判定 | 口径 |", "| --- | --- | --- | --- | --- |"]
    for name, val, target, mark, note in rows:
        L.append("| %s | %s | %s | %s | %s |" % (name, val, target, mark, note or "-"))
    L.append("")
    L.append("> 未达标（⚠️）不粉饰、也不求平均掩盖：分层数字见上面「一、总体」与「三、复现榜单」。")
    return "\n".join(L)


def render_markdown(eval_set: Dict[str, Any], results: Sequence[EntryResult], summary: Dict[str, Any]) -> str:
    L: List[str] = []
    a = L.append
    a("# 评测基线（python -m paper2code eval）\n")
    a("- 评测集：`%s`（v%s，%s）" % (eval_set.get("_path", "configs/eval_set.json"),
                                     eval_set.get("version"), eval_set.get("updated_at")))
    a("- 运行时间：%s ｜ 端点：离线（`offline-scaffold`，0 token）" % datetime.now().isoformat(timespec="seconds"))
    a("")
    a("## 一、总体（**分层报告，禁止只看平均**）\n")
    a("| 层 | n | 成功跑完 | smoke 通过率 | 情形判定正确率 | 校验期望命中率 | 结构抽取命中率 | 图表出图率 | 保真度均分 | 均耗时 |")
    a("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    rows = [("**L1 合成**", "L1-synthetic"), ("**L2 真实**", "L2-real"), ("**全部**", None)]
    for label, suite in rows:
        b = summary["overall"] if suite is None else summary["by_suite"].get(suite)
        if not b:
            continue
        a("| %s | %d | %d | %s | %s | %s | %s | %s | %s | %.1fs |" % (
            label, b["n"], b["n_ok"], _pct(b["smoke_pass_rate"]), _pct(b["mode_ok_rate"]),
            _pct(b["verdict_ok_rate"]), _pct(b["structure_hit_mean"]), _pct(b["figure_draw_rate"]),
            _rat(b["fidelity_mean"]), b["elapsed_mean_s"] or 0.0))
    a("")
    a("> 路线图 M1 目标：smoke 通过率 ≥90% " + _goal("smoke_pass_rate", summary["overall"]["smoke_pass_rate"])
      + " ｜ 图表出图率 ≥70% " + _goal("figure_draw_rate", summary["overall"]["figure_draw_rate"])
      + " ｜ 保真度均分 ≥3.5 " + _goal("fidelity_mean", summary["overall"]["fidelity_mean"]))
    a("> `required_outputs 覆盖率` = %s（离线脚手架不实现论文方法，只有接 LLM 后该项才有意义）"
      % _pct(summary["overall"]["required_outputs_coverage"]))
    ov = summary["overall"]
    mark = "✅" if (ov.get("template_covered_rate") or 0) >= 0.6 else "⚠️"
    a("> 路线图 M3-① 验收：领域模板覆盖 %d/%d = %s %s（目标 ≥60%%）"
      % (ov.get("template_covered") or 0, ov.get("n_ok") or 0,
         _pct(ov.get("template_covered_rate")), mark))
    a("")
    a("## 二、逐条明细\n")
    a("| 用例 | 层 | 情形(期望) | 校验 | 结构(表/图/算法/链接) | demo | 出图 | 断言(pass/fail/skip) | 保真度 | 耗时 | 备注 |")
    a("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in results:
        if r.status == "missing":
            a("| `%s` | %s | — | — | — | — | — | — | — | — | ⬜ %s |" % (r.id, r.suite, r.error))
            continue
        if r.status == "error":
            a("| `%s` | %s | — | — | — | — | — | — | — | %.1fs | ❌ %s |"
              % (r.id, r.suite, r.elapsed_s, r.error[:60]))
            continue
        sd = r.structure_detail or {}
        got = "/".join(str(sd[k]["got"]) for k in ("tables", "figures", "algorithms", "code_refs") if k in sd)
        need = "/".join(str(sd[k]["min"]) for k in ("tables", "figures", "algorithms", "code_refs") if k in sd)
        mk = {True: "✅", False: "❌", None: "—"}[r.mode_ok]
        vk = {True: "✅", False: "❌", None: "—"}[r.verdict_ok]
        a("| `%s` | %s | %s %s | %s %s | %s vs 需 %s | %s | %d/%d | %d/%d/%d | %s | %.1fs | %s |" % (
            r.id, r.suite, r.mode or "-", mk, r.verdict or "-", vk, got, need,
            "通过" if r.demo_ok else ("未执行" if r.demo_ok is None else "失败"),
            r.figure_drawn, r.figure_specs, r.claims_pass, r.claims_fail, r.claims_skip,
            r.fidelity if r.fidelity is not None else "-",
            r.elapsed_s,
            (r.generator or "-") + " · " + "warn %d%s" % (
                r.warnings, " · ⚠️护栏触发" if r.budget_tripped else "")))
    a("")
    a("## 三、可复现性基准（复现榜单）\n")
    a("> 每条主张的四档判定来自 `outputs/single/<id>/VERIFICATION.md`（roadmap M6-①）。"
      "`已复现率` = 已复现 / 已判定；`已判定率` = 已判定 / 全部主张。"
      "**`已判定率` 低的论文没有资格谈「已复现率」**——多数主张还没验。\n")
    a("| 排名 | 用例 | 主张数 | 已复现 | 部分 | 不可复现 | 未检验 | 已复现率 | 已判定率 |")
    a("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    ranked = sorted([r for r in results if r.status == "ok" and r.n_claims],
                    key=lambda r: (-(r.reproduced_rate or 0.0), -(r.tested_rate or 0.0), r.id))
    for i, r in enumerate(ranked, start=1):
        a("| %d | `%s` | %d | %d | %d | %d | %d | %s | %s |" % (
            i, r.id, r.n_claims, r.n_reproduced, r.n_partial, r.n_not_reproducible, r.n_untested,
            _pct(r.reproduced_rate), _pct(r.tested_rate)))
    if not ranked:
        a("| — | — | — | — | — | — | — | — | — |")
    ov2 = summary["overall"]
    a("")
    a("**基准汇总**：主张 %d 条 ｜ 已复现 %d ｜ 部分 %d ｜ 不可复现 %d ｜ 未检验 %d ｜ 已复现率 %s ｜ 已判定率 %s"
      % (ov2.get("claims_total") or 0, ov2.get("claims_reproduced") or 0, ov2.get("claims_partial") or 0,
         ov2.get("claims_not_reproducible") or 0, ov2.get("claims_untested") or 0,
         _pct(ov2.get("reproduced_rate")), _pct(ov2.get("tested_rate"))))
    a("")
    a(dashboard_markdown(summary))
    a("## 四、人工标注（评测集自带，供复核）\n")
    for e in eval_set.get("entries", []):
        ann = e.get("annotation") or {}
        a("- **%s**（%s）：核心机制 —— %s" % (e["id"], e.get("suite"), ann.get("core_mechanism", "")))
        if ann.get("qualitative_conclusion"):
            a("  - 可定性复现的结论：%s" % ann["qualitative_conclusion"])
        if ann.get("expected_figures"):
            a("  - 应出现的图：%s" % "；".join(ann["expected_figures"]))
    a("")
    a("## 五、诚实边界\n")
    a("- L2 真实论文的 `sections/tables/figures` 期望值来自人工标注的**下限**，不是精确真值。")
    a("- 离线路径 0 token；真实 LLM 端点的 token/耗时**尚未统计**（provider 未做用量记账）。")
    a("- `missing` 表示评测集登记的语料不在本机（真实论文不入库）。")
    a("- **`未检验` 不是「通过」**：回放论文自带数字、缺指标键、脚本未执行都会记未检验；"
      "看到高「已复现率」时请先看「已判定率」。")
    return "\n".join(L)


#: 这些指标"越小越好"（耗时/成本类）——比较方向要反过来，否则会把提速标成退步
LOWER_IS_BETTER = {"elapsed_mean_s", "tokens_total"}


def compare_markdown(summary: Dict[str, Any], baseline: Dict[str, Any]) -> str:
    keys = ["smoke_pass_rate", "mode_ok_rate", "verdict_ok_rate", "structure_hit_mean",
            "figure_draw_rate", "fidelity_mean", "elapsed_mean_s"]
    L = ["", "## 六、与基线对比（`--compare`）", "",
         "| 指标 | 基线 | 当前 | 变化 | 判定 |", "| --- | --- | --- | --- | --- |"]
    base_all = (baseline.get("summary") or {}).get("overall") or {}
    cur_all = summary["overall"]
    for k in keys:
        b, c = base_all.get(k), cur_all.get(k)
        if b is None or c is None:
            L.append("| %s | %s | %s | — | — |" % (k, _rat(b), _rat(c)))
            continue
        d = c - b
        arrow = "→" if abs(d) < 1e-9 else ("↑" if d > 0 else "↓")
        if abs(d) < 1e-9:
            verdict = "持平"
        else:
            improved = (d < 0) if k in LOWER_IS_BETTER else (d > 0)
            verdict = "✅ 改善" if improved else "⚠️ **退步**"
        L.append("| %s%s | %s | %s | %s %+.4f | %s |" % (
            k, "（越小越好）" if k in LOWER_IS_BETTER else "", _rat(b), _rat(c), arrow, d, verdict))
    L.append("")
    L.append("> 标记为「退步」的指标必须解释或回滚；这是路线图「每个里程碑结束做一次评测对比，退步则不合并」的落点。")
    return "\n".join(L)


def run_eval(set_path: Path = DEFAULT_SET, out_dir: Optional[Path] = None,
             compare: Optional[Path] = None, use_llm: bool = False) -> Dict[str, Any]:
    settings = get_settings().ensure_dirs()
    out_dir = Path(out_dir) if out_dir else settings.eval_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    runs_root = out_dir / "_runs"

    eval_set = load_eval_set(Path(set_path))
    eval_set["_path"] = str(set_path)

    provider = None
    if not use_llm:  # 离线基线：关掉 MVP 的 LLM 尝试，保证确定性
        from .llm.offline import OfflineProvider

        get_settings(refresh=True, mvp_use_llm=False)
        provider = OfflineProvider()

    results: List[EntryResult] = []
    for entry in eval_set["entries"]:
        results.append(run_entry(entry, runs_root, provider=provider, use_llm=use_llm))

    summary = summarize(results)
    md = render_markdown(eval_set, results, summary)
    if compare:
        try:
            baseline = json.loads(Path(compare).read_text(encoding="utf-8"))
            md += compare_markdown(summary, baseline)
        except Exception as exc:
            md += "\n> ⚠️ 基线读取失败：%s\n" % exc

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "eval_set": str(set_path),
        "use_llm": use_llm,
        "summary": summary,
        "entries": [r.to_dict() for r in results],
    }
    (out_dir / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "latest.md").write_text(md, encoding="utf-8")
    bench = write_benchmark(results, summary, out_dir, str(set_path))
    # roadmap M1 的「人工评语」：把机器已知的证据整理成**初判待复核**的复核表（不自动改分）
    try:
        from . import annotate

        paths = annotate.write(annotate.build(eval_set, results), out_dir)
    except Exception as exc:
        paths = {}
        print("\n[eval] ⚠️ 人工复核表生成失败：%s: %s" % (type(exc).__name__, exc))
    print(md)
    print("\n[eval] JSON: %s\n[eval] Markdown: %s\n[eval] 可复现性基准: %s\n[eval] 人工复核表: %s"
          % (out_dir / "latest.json", out_dir / "latest.md", bench,
             paths.get("markdown", "（生成失败）")))
    return payload


def write_benchmark(results: Sequence[EntryResult], summary: Dict[str, Any], out_dir: Path,
                    eval_set_path: str = "") -> Path:
    """M6-⑤：写出可发布的**可复现性基准**（`benchmark.json`，含复现榜单）。

    榜单口径写死在文件里，避免被误读：`已复现率` = 已复现/已判定；`已判定率` = 已判定/全部主张。
    """
    ov = summary.get("overall") or {}
    ranked = sorted([r for r in results if r.status == "ok" and r.n_claims],
                    key=lambda r: (-(r.reproduced_rate or 0.0), -(r.tested_rate or 0.0), r.id))
    bench = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "eval_set": eval_set_path,
        "metric_definitions": {
            "reproduced_rate": "已复现 / 已判定（已判定 = 已复现 + 部分 + 不可复现）",
            "tested_rate": "已判定 / 全部主张（未检验不计入已判定）",
            "statuses": {"reproduced": "已复现", "partial": "部分复现",
                         "not_reproducible": "不可复现", "untested": "未检验"},
            "warning": "未检验不算通过；回放论文自带数字一律记未检验。已判定率低时不要引用已复现率。",
        },
        "overall": {k: ov.get(k) for k in
                    ("claims_total", "claims_reproduced", "claims_partial", "claims_not_reproducible",
                     "claims_untested", "reproduced_rate", "tested_rate")},
        "leaderboard": [{
            "rank": i, "id": r.id, "suite": r.suite, "n_claims": r.n_claims,
            "n_reproduced": r.n_reproduced, "n_partial": r.n_partial,
            "n_not_reproducible": r.n_not_reproducible, "n_untested": r.n_untested,
            "reproduced_rate": r.reproduced_rate, "tested_rate": r.tested_rate,
            "verdict_line": r.verdict_line, "verification": r.verification_md,
        } for i, r in enumerate(ranked, start=1)],
        "skipped": [{"id": r.id, "reason": r.error or r.status} for r in results
                    if r.status != "ok" or not r.n_claims],
    }
    p = Path(out_dir) / "benchmark.json"
    p.write_text(json.dumps(bench, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="跑评测集并汇总指标（roadmap M1）")
    ap.add_argument("--set", default=str(DEFAULT_SET), help="评测集 JSON")
    ap.add_argument("--out", default=None, help="输出目录（默认 outputs/eval）")
    ap.add_argument("--compare", default=None, help="与基线 latest.json 对比")
    ap.add_argument("--llm", action="store_true", help="使用已配置的 LLM 端点（默认离线）")
    a = ap.parse_args(list(argv) if argv is not None else None)
    run_eval(Path(a.set), Path(a.out) if a.out else None,
             Path(a.compare) if a.compare else None, use_llm=a.llm)
    return 0


if __name__ == "__main__":
    sys.exit(main())
