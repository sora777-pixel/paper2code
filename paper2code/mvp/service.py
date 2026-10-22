"""MVP 编排：plan → demo(代码) → 运行校验/修复 → 图表说明 → 绘图脚本 → 报告。

LLM 路径与离线脚手架路径共用同一套「运行 + 校验」，区别只在代码从哪来。
任何一步 LLM 不可用/失败都会降级到离线脚手架，并把原因写进 warnings，绝不中断整条流水线。
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import math
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import control
from ..budget import BudgetReport
from ..config import get_settings
from ..deps import analyze as analyze_deps, local_module_names, warnings_for as dep_warnings
from ..llm import get_provider
from ..models import Paper
from ..reproduce import runner
from . import claims, export as export_mod, figspec, iterate, prompts, scaffold, selfcheck, templates
from .llmio import LLMUnavailable, ask, ask_json
from .textio import describe_results, get_path, parse_files

HERE = Path(__file__).parent


@dataclass
class MvpResult:
    paper_id: str
    ok: bool = False
    generator: str = ""            # llm | offline-scaffold | offline-scaffold (llm demo failed)
    demo_ok: Optional[bool] = None  # None = 未执行
    attempts: List[Dict[str, Any]] = field(default_factory=list)
    figures: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    figure_specs: List[Dict[str, Any]] = field(default_factory=list)
    report_path: str = ""
    figures_md: str = ""
    demo_dir: str = ""
    selfcheck: Dict[str, Any] = field(default_factory=dict)   # 计划自检结论（roadmap M1-③）
    claims: Dict[str, Any] = field(default_factory=dict)      # 性质断言结论（roadmap M1-④）
    usage: Dict[str, Any] = field(default_factory=dict)       # LLM 用量（roadmap M1-⑥）
    budget: Dict[str, Any] = field(default_factory=dict)      # 成本护栏结论（roadmap M1-⑥）
    sandbox: Dict[str, Any] = field(default_factory=dict)     # 执行沙箱策略（roadmap M4）
    version: Dict[str, Any] = field(default_factory=dict)     # 版本清单（roadmap M5-②）
    exported: Dict[str, Any] = field(default_factory=dict)    # 导出产物（roadmap M5-③）
    modified_files: List[str] = field(default_factory=list)   # 本次「改一次」动过的文件
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


def env_probe() -> Dict[str, Any]:
    def has(m: str) -> bool:
        return importlib.util.find_spec(m) is not None

    env: Dict[str, Any] = {"python": sys.version.split()[0], "numpy": has("numpy"),
                           "matplotlib": has("matplotlib"), "torch": has("torch"), "cuda": False}
    if env["torch"]:
        try:
            import torch  # type: ignore
            env["cuda"] = bool(torch.cuda.is_available())
        except Exception:
            pass
    return env


# --------------------------------------------------------------------------- #
# demo 运行与校验
# --------------------------------------------------------------------------- #
def _no_nan(x: str) -> None:
    raise ValueError("non-finite constant %s" % x)


def check_demo(r: Any, demo_dir: Path, required: List[str]) -> Tuple[bool, str]:
    if getattr(r, "blocked", False):  # M4-① 静态扫描拦截：从未执行
        return False, "静态安全扫描拦截（未执行）：%s" % ((r.scan or {}).get("summary") or r.error)
    if r.timed_out:
        return False, "运行超时；请缩小 smoke 模式的规模"
    if r.returncode != 0:
        return False, "退出码 %s" % r.returncode
    mp = demo_dir / "results" / "metrics.json"
    if not mp.exists():
        return False, "没有写出 results/metrics.json"
    try:
        data = json.loads(mp.read_text(encoding="utf-8"), parse_constant=_no_nan)
    except Exception as exc:
        return False, "metrics.json 不合法：%s" % exc
    missing = []
    for k in required:
        try:
            v = get_path(data, k)
            if isinstance(v, float) and not math.isfinite(v):
                missing.append("%s（非有限数）" % k)
        except (KeyError, IndexError, ValueError):
            missing.append(k)
    if missing:
        return False, "缺少或非法的必需键：%s" % missing
    return True, "ok"


def _existing_plan(out: Path) -> Optional[Dict[str, Any]]:
    """读回上一版的 MVP 计划（「改一次」要沿用它，而不是重新规划）。"""
    p = Path(out) / "artifacts" / "plan.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _tail(r: Any, n: int = 3500) -> str:
    s = (r.stdout or "")[-n:]
    if r.stderr:
        s += "\n--- stderr ---\n" + r.stderr[-n:]
    return s.strip()


def _read_dir(base: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for p in sorted(base.rglob("*")):
        if p.is_file() and p.suffix in (".py", ".md", ".txt", ".yaml", ".yml") and "results" not in p.relative_to(base).parts:
            out[str(p.relative_to(base)).replace("\\", "/")] = p.read_text(encoding="utf-8", errors="replace")
    return out


def _write_files(base: Path, files: Dict[str, str]) -> None:
    for rel, content in files.items():
        if rel.startswith("demo/"):
            rel = rel[len("demo/"):]
        dest = base / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")


def _verify_demo(res: MvpResult, provider: Any, demo: Path, plan: Dict[str, Any], llm_mode: bool) -> bool:
    st = get_settings()
    max_fix = int(getattr(st, "mvp_max_fix", 3))
    timeout = int(getattr(st, "mvp_smoke_timeout", 90))
    required = plan.get("required_outputs", [])
    for i in range(max_fix + 1):
        shutil.rmtree(demo / "results", ignore_errors=True)
        # trusted = 离线脚手架/领域模板（本仓库确定性生成物）；LLM 写的代码必须先过静态扫描（M4-①）
        r = runner.run_python_script(demo / "run_demo.py", demo, timeout=timeout,
                                     allow_network=False, trusted=not llm_mode)
        ok, why = check_demo(r, demo, required)
        res.attempts.append({"attempt": i, "ok": ok, "why": why, "tail": _tail(r, 1200)})
        if ok:
            return True
        if not llm_mode or i == max_fix:
            return False
        try:
            raw = ask(provider, "mvp_fix_demo", prompts.FIX_SYS, prompts.fix_user(_read_dir(demo), why, _tail(r), required))
            changed = parse_files(raw)
        except (LLMUnavailable, ValueError) as exc:
            res.attempts.append({"attempt": i, "note": "修复调用失败：%s" % exc})
            return False
        if not changed:
            res.attempts.append({"attempt": i, "note": "修复回复里没有文件"})
            continue
        _write_files(demo, changed)
    return False


# --------------------------------------------------------------------------- #
# 图表
# --------------------------------------------------------------------------- #
def _rel(out: Path, p: Path) -> str:
    return str(p.relative_to(out)).replace("\\", "/")


def _figures_stage(res: MvpResult, paper: Paper, provider: Any, out: Path, plan: Dict[str, Any],
                   llm_mode: bool, execute: bool) -> None:
    st = get_settings()
    figs = out / "figures"
    (figs / "specs").mkdir(parents=True, exist_ok=True)
    (figs / "out").mkdir(parents=True, exist_ok=True)
    shutil.copy(HERE / "figlib_template.py", figs / "figlib.py")
    shutil.copy(HERE / "run_figures_template.py", figs / "run_figures.py")

    results: Optional[Dict[str, Any]] = None
    mp = out / "demo" / "results" / "metrics.json"
    if mp.exists():
        try:
            results = json.loads(mp.read_text(encoding="utf-8"))
        except ValueError:
            results = None
    lines = describe_results(results) if results else []

    specs = figspec.baseline_specs(paper)
    if llm_mode and paper.figures:
        figs_in = [{"ref": "Figure %s" % figspec._num(f.id), "caption": f.caption,
                    "type_guess": figspec.classify_figure(f)} for f in paper.figures]
        try:
            raw = ask_json(provider, "mvp_figspec", prompts.FIGSPEC_SYS, prompts.figspec_user(figs_in, lines))
            specs = figspec.merge_specs(specs, figspec.normalize_llm_specs(raw))
        except (LLMUnavailable, ValueError) as exc:
            llm_mode = False
            res.warnings.append("图表说明的 LLM 调用失败，仅使用确定性基线：%s" % exc)

    status: Dict[str, Dict[str, Any]] = {}
    have_mpl = importlib.util.find_spec("matplotlib") is not None
    max_fix_fig = int(getattr(st, "mvp_max_fix_fig", 2))
    for s in specs:
        fid, sl = s["id"], s["id"][4:]
        if not s.get("chart"):
            s["chart"] = figspec.resolve_chart(s, results)
        (figs / "specs" / ("%s.json" % fid)).write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
        st_i: Dict[str, Any] = {"state": "skipped", "files": [], "script": False}
        status[fid] = st_i
        if not (s.get("chart") or s.get("diagram") or s["reproducibility"] in ("from_demo", "schematic")):
            st_i["why"] = s.get("demo_change_needed") or "无可绘制的数据"
            continue
        builtin = figspec.render_builtin(s, figs / "out")
        if builtin:
            for ext in ("png", "svg"):
                q = figs / "out" / ("%s.%s" % (fid, ext))
                if q.exists():
                    st_i["files"].append(_rel(out, q))
            st_i["state"] = "builtin_only"
        # 绘图脚本：LLM 优先，失败回退确定性模板
        code: Optional[str] = None
        if llm_mode and (s["reproducibility"] in ("from_demo", "schematic")):
            try:
                raw = ask(provider, "mvp_figcode", prompts.FIGCODE_SYS, prompts.figcode_user(s, lines))
                fs = parse_files(raw)
                code = next(iter(fs.values())) if fs else None
            except (LLMUnavailable, ValueError) as exc:
                res.warnings.append("图 %s 的 LLM 绘图脚本生成失败，改用模板：%s" % (fid, exc))
        tmpl = figspec.template_script(s)
        code = code or tmpl
        if not code:
            if st_i["state"] == "skipped":
                st_i["why"] = "没有可绘制的内容（缺少数据键或节点）"
            continue
        script = figs / ("draw_%s.py" % sl)
        script.write_text(code, encoding="utf-8")
        st_i["script"] = True
        if not (execute and have_mpl):
            st_i["why"] = "脚本已生成但未运行（%s）" % ("未执行模式" if not execute else "未安装 matplotlib，使用内置渲染器出图")
            continue
        # trusted：模板脚本是本仓库确定性生成物；LLM 写的绘图脚本必须先过静态扫描（M4-①）
        trusted = code == tmpl
        ok, why = _run_figure(script, sl, figs, trusted)
        tries = 0
        while not ok and llm_mode and code is not tmpl and tries < max_fix_fig:
            tries += 1
            try:
                raw = ask(provider, "mvp_fix_fig", prompts.FIGFIX_SYS,
                          prompts.figfix_user("figures/%s" % script.name, script.read_text(encoding="utf-8"), why, why))
                fs = parse_files(raw)
                if fs:
                    script.write_text(next(iter(fs.values())), encoding="utf-8")
                    trusted = False
            except (LLMUnavailable, ValueError):
                break
            ok, why = _run_figure(script, sl, figs, trusted)
        if not ok and tmpl and script.read_text(encoding="utf-8") != tmpl:  # LLM 脚本失败 → 模板兜底
            script.write_text(tmpl, encoding="utf-8")
            ok, why = _run_figure(script, sl, figs, True)
            if ok:
                res.warnings.append("图 %s 的 LLM 脚本未通过，已回退到模板脚本" % fid)
        if ok:
            st_i["state"] = "ok"
            st_i["files"] = sorted({_rel(out, p) for ext in ("png", "svg") for p in (figs / "out").glob("%s.%s" % (fid, ext))})
        else:
            st_i["state"] = "builtin_only" if builtin else "error"
            st_i["why"] = why[:300]

    res.figure_specs = specs
    res.figures = status
    figspec.write_figures_md(specs, status, figs / "FIGURES.md", out)
    res.figures_md = _rel(out, figs / "FIGURES.md")


def _run_figure(script: Path, slug_: str, figs: Path, trusted: bool) -> Tuple[bool, str]:
    fid = "fig_%s" % slug_
    for ext in ("png", "svg"):
        p = figs / "out" / ("%s.%s" % (fid, ext))
        if p.exists():
            p.unlink()
    r = runner.run_python_script(figs / "run_figures.py", figs, timeout=120,
                                 env_extra={"P2C_ONLY": slug_}, allow_network=False, trusted=trusted)
    png = figs / "out" / ("%s.png" % fid)
    if r.ok and png.exists() and png.stat().st_size > 1500:
        return True, "ok"
    return False, (r.stderr or r.error or "没有生成 %s.png 或文件过小" % fid)[-1500:]


def _run_claims(res: MvpResult, paper: Paper, provider: Any, ctx: str, demo: Path, llm_mode: bool) -> bool:
    """性质断言（roadmap M1-④）：在 demo 的真实产物上求值，返回是否通过（fail 即不通过）。

    断言来自「论文的可检验声明」：离线从表格派生（占比求和 / 计数非负 / 非空），
    LLM 路径额外从正文提取（单调性 / 上下界 等）。`skip` 不算通过。
    任何异常都不打断流水线 —— 断言环节坏掉只记 warning。
    """
    try:
        outcome = claims.evaluate_after_demo(paper, provider, ctx, demo, llm_mode=llm_mode)
    except control.RunCancelled:
        raise              # M5-①：取消/超时不算「断言环节坏掉」，必须向上传播
    except Exception as exc:
        res.warnings.append("性质断言环节失败（不影响其他产物）：%s: %s" % (type(exc).__name__, exc))
        return True
    res.claims = outcome
    res.warnings.extend(outcome.get("warnings") or [])
    if outcome.get("failed"):
        res.attempts.append({"ok": False, "why": "性质断言失败：%s" % outcome.get("first_failure")})
        return False
    return True


# --------------------------------------------------------------------------- #
def build_mvp(paper: Paper, out_dir: Any, provider: Any = None, execute: bool = True,
              use_llm: Optional[bool] = None, modify: str = "") -> MvpResult:
    """生成（或**迭代修改**）一篇论文的 MVP。

    ``modify`` 非空 = 「改一次」（roadmap M5-②）：先把当前版本存档（`mvp/versions/v{n}/`），
    再让模型按这条指令改代码，最后算出版本间的 diff。改不动就如实降级并说明，不假装改好了。
    """
    st = get_settings()
    provider = provider or get_provider()
    use_llm = bool(getattr(st, "mvp_use_llm", True)) if use_llm is None else use_llm
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    iterate.save_manifest(out, iterate.load_manifest(out))   # 保证版本清单存在（幂等）
    # M5-②「改一次」需要 LLM：离线模式只能重新生成，指令无从生效——如实说明，不伪造一个「改过」的版本
    apply_modify = modify if (modify and use_llm) else ""
    # M5-② 版本化：存档必须发生在**清空产物之前**，否则「改之前」的东西就没了
    archived: Optional[Dict[str, Any]] = None
    if apply_modify:
        archived = iterate.archive_current(out, instruction=apply_modify)
        prev_files = iterate.read_files(archived["dir"])
    for sub in ("demo", "figures", "artifacts", "demo_llm_failed"):  # 每次整体重建，避免陈旧产物
        shutil.rmtree(out / sub, ignore_errors=True)
    demo = out / "demo"
    (out / "artifacts").mkdir(parents=True, exist_ok=True)
    demo.mkdir(parents=True, exist_ok=True)
    if archived:
        iterate.restore(out, archived)      # 放回上一版文件，作为「改动前的底稿」
        demo.mkdir(parents=True, exist_ok=True)
    res = MvpResult(paper_id=paper.id, demo_dir=str(demo))
    # 执行沙箱策略（M4）：本次到底跑在哪、隔离级别、是否降级——如实写进报告与 warnings
    pol = runner.resolve_policy()
    res.sandbox = pol.to_dict()
    notice = runner.sandbox_notice()
    if notice:
        res.warnings.append(notice)
    ctx = prompts.paper_context(paper)
    t0 = time.time()
    if use_llm and hasattr(provider, "attach_budget"):
        # 成本护栏（M1-⑥）：整篇论文一份预算，从这一刻开始计时
        provider.attach_budget(st.cost_budget())

    if modify and not use_llm:
        res.warnings.append("「改一次」需要 LLM：当前离线模式无法按指令修改，已按原方式重新生成"
                            "（指令「%s」未生效）。" % modify)
    plan: Optional[Dict[str, Any]] = None
    llm_mode = False
    if use_llm:
        try:
            if apply_modify:
                # M5-②「改一次」：复用上一版的计划，只让模型按指令改代码
                plan = _existing_plan(out) or ask_json(
                    provider, "mvp_plan", prompts.PLAN_SYS, prompts.plan_user(ctx, env_probe()))
                for k, v in (("required_outputs", []), ("simplified_parts", []), ("faithful_parts", [])):
                    plan.setdefault(k, v)
                changed = parse_files(ask(provider, "mvp_modify", prompts.MODIFY_SYS,
                                          prompts.modify_user(prev_files, apply_modify, plan, ctx)))
                if not changed:
                    raise ValueError("修改指令没有产出任何文件（模型未按格式返回）")
                _write_files(demo, changed)
                res.modified_files = sorted(changed)
            else:
                plan = ask_json(provider, "mvp_plan", prompts.PLAN_SYS, prompts.plan_user(ctx, env_probe()))
                plan.setdefault("required_outputs", [])
                plan.setdefault("simplified_parts", [])
                plan.setdefault("faithful_parts", [])
                # 写码前自检（roadmap M1-③）：先对照论文核对计划是否诚实，再让模型写代码，
                # 减少「看起来对但实现错了」。判定需改时只修订一次，成本有上界。
                plan = selfcheck.prepare(paper, plan, provider, ctx, llm_mode=True)
                files = parse_files(ask(provider, "mvp_code", prompts.CODE_SYS, prompts.code_user(plan, ctx)))
                if not any(p.split("/")[-1] == "run_demo.py" for p in files):
                    raise ValueError("LLM 没有产出 run_demo.py")
                _write_files(demo, files)
            llm_mode = True
        except (LLMUnavailable, ValueError) as exc:
            if apply_modify:
                # 改不动就别动：保留上一版，如实说明，而不是假装改好了
                res.warnings.append(
                    "「改一次」失败（%s）：模型没能给出可用的修改，已保留改动前的版本。" % exc)
            else:
                res.warnings.append("LLM 不可用或输出不合规，已降级为离线脚手架：%s" % exc)
            plan = None
            shutil.rmtree(demo, ignore_errors=True)
            demo.mkdir(parents=True, exist_ok=True)
            if archived:
                iterate.restore(out, archived)      # 修改失败 → 退回上一版的文件
                demo.mkdir(parents=True, exist_ok=True)
                plan = _existing_plan(out)
    offline_generator = "offline-scaffold"
    if plan is None:
        # M3-①：先试领域模板（确定性匹配）。命中就用「领域骨架」而不是「表格回放」，
        # 骨架能产出论文那类指标与曲线；未命中才回落到表格回放脚手架。
        tpl = templates.select_template(paper)
        if tpl is not None:
            info = templates.build_template_demo(paper, tpl, demo)
            plan = templates.template_plan(paper, tpl, info["payload"])
            offline_generator = "offline-template:%s" % tpl["name"]
        else:
            info = scaffold.build_scaffold(paper, demo, provider,
                                           timeout=int(getattr(st, "exec_timeout", 120)))
            plan = scaffold.scaffold_plan(paper)
        plan = selfcheck.prepare(paper, plan, provider, ctx, llm_mode=False)
        res.warnings.extend(info["warnings"])
    res.selfcheck = dict(plan.get("selfcheck") or {})
    if res.selfcheck.get("verdict") == "unfaithful":
        res.warnings.append(
            "计划自检判定「不忠实」（unfaithful）：计划声称的能力与论文机制不符，问题清单见 MVP_REPORT.md「计划自检」一节。")
    res.generator = "llm" if llm_mode else offline_generator
    if apply_modify and not llm_mode and plan is not None:
        offline_generator = "previous-version（「改一次」未生效，保留改动前的版本）"
        res.generator = offline_generator
    # M3-⑤：依赖解析——报告代码实际需要什么依赖、本机有没有、缺了怎么装或怎么降级。
    # 只报告不自动安装：本项目硬约束是「离线永远可用」，静默 pip install 会引入网络与环境污染。
    # 本地模块（模型自己写在 demo 目录里的 .py，如 as_topic.py）**不能**算第三方缺失依赖，
    # 否则会误导成 `pip install as_topic`（实测真的发生过）。
    demo_files = _read_dir(demo)
    dep_report = analyze_deps(demo_files.values(), plan.get("dependencies") or [],
                              local_modules=local_module_names(demo_files.keys()))
    plan["resolved_dependencies"] = dep_report
    res.warnings.extend(dep_warnings(dep_report))
    (out / "artifacts" / "deps.json").write_text(
        json.dumps(dep_report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "artifacts" / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    if execute:
        res.demo_ok = _verify_demo(res, provider, demo, plan, llm_mode)
        claims_failed = False
        # 基础契约通过后，再验「论文声明的性质」；断言失败即判 demo 失败（roadmap M1-④）
        if res.demo_ok and not _run_claims(res, paper, provider, ctx, demo, llm_mode):
            res.demo_ok = False
            claims_failed = True
            res.warnings.append(
                "demo 的基础契约通过，但**论文声明的性质断言失败** → 判定该 demo 失败（明细见 MVP_REPORT.md「性质断言」）。")
        if llm_mode and not res.demo_ok:  # LLM demo 没通过：保留现场并给出可运行的脚手架
            shutil.move(str(demo), str(out / "demo_llm_failed"))
            demo.mkdir(parents=True, exist_ok=True)
            # 降级原因要说准：是「修复轮次用尽仍未跑通」还是「跑通了但论文性质断言不成立」——
            # 写成一句话会把后者误报成前者，读报告的人就会去查错方向。
            why = ("论文声明的性质断言不成立（smoke 契约本身是通过的）" if claims_failed
                   else "smoke 契约在 %d 轮修复后仍未通过" % int(getattr(st, "mvp_max_fix", 3)))
            res.warnings.append("LLM 生成的 demo 未通过（%s；现场已保留在 demo_llm_failed/），改为输出离线脚手架。" % why)
            llm_mode = False
            offline_generator = "offline-scaffold (llm demo failed)"
            tpl = templates.select_template(paper)
            if tpl is not None:
                info = templates.build_template_demo(paper, tpl, demo)
                plan = templates.template_plan(paper, tpl, info["payload"])
                offline_generator = "offline-template:%s (llm demo failed)" % tpl["name"]
            else:
                info = scaffold.build_scaffold(paper, demo, provider,
                                               timeout=int(getattr(st, "exec_timeout", 120)))
                plan = scaffold.scaffold_plan(paper)
            plan = selfcheck.prepare(paper, plan, provider, ctx, llm_mode=False)
            res.selfcheck = dict(plan.get("selfcheck") or {})
            res.generator = offline_generator
            res.warnings.extend(info["warnings"])
            res.demo_ok = _verify_demo(res, provider, demo, plan, False)
            if res.demo_ok and not _run_claims(res, paper, provider, ctx, demo, False):
                res.demo_ok = False
    else:
        res.warnings.append("未执行（execute=False）：代码已生成但从未运行。")

    if res.claims:
        (out / "artifacts" / "claims.json").write_text(
            json.dumps(res.claims, ensure_ascii=False, indent=2), encoding="utf-8")

    _figures_stage(res, paper, provider, out, plan, llm_mode, execute)

    # -- 成本护栏结论（M1-⑥）：用量、上限、是否触发过 ---------------------- #
    try:
        usage = provider.get_usage().to_dict() if hasattr(provider, "get_usage") else {}
        tripped = provider.budget_tripped() if hasattr(provider, "budget_tripped") else ""
    except Exception:
        usage, tripped = {}, ""
    res.usage = usage
    res.budget = BudgetReport(limit=st.cost_budget().to_dict(), usage=usage, tripped=tripped,
                              elapsed_s=time.time() - t0).to_dict()
    if tripped:
        res.warnings.append(
            "成本护栏触发：%s；MVP 的 LLM 路径已停用（改用离线脚手架 / 跳过该步），明细见报告「成本护栏」一节。" % tripped)

    res.ok = bool(res.demo_ok) if execute else True

    # M5-②：版本清单收口（记录 live 版本 + 上一版 → 本版的 diff）
    try:
        res.version = iterate.finish_build(
            out, generator=res.generator, instruction=apply_modify, demo_ok=res.demo_ok,
            demo_ok_detail=(str(res.attempts[-1].get("why", "")) if res.attempts else ""),
            archived=archived,
            extra={"modified_files": res.modified_files, "sandbox": (res.sandbox or {}).get("mode", ""),
                   "figures_drawn": sum(1 for v in res.figures.values() if v.get("files"))})
    except Exception as exc:
        res.warnings.append("版本清单写入失败（不影响产物）：%s: %s" % (type(exc).__name__, exc))
        res.version = iterate.summarize(out)

    # M5-③：导出（自包含 ipynb + Streamlit 小演示 + 用法 README）——失败只记 warning。
    # 必须排在 write_report 之前：报告里要写出导出的产物路径。
    try:
        res.exported = export_mod.export_mvp(out, paper.id, title=paper.title,
                                             repo=str(getattr(st, "notebook_repo", "") or ""))
    except Exception as exc:
        res.warnings.append("导出（ipynb / Streamlit）失败，不影响其他产物：%s: %s"
                            % (type(exc).__name__, exc))

    from .report import write_report
    res.report_path = write_report(paper, plan, res, out)
    (out / "mvp_result.json").write_text(json.dumps(res.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return res
