"""复现编排：按三种情形分别执行，输出复现产物与报告。"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import control, deps
from ..config import get_settings
from ..llm import LLMProvider, get_provider
from ..models import MODE_NO_CODE, MODE_OPEN_SOURCE, MODE_PSEUDOCODE, Paper, ReproResult
from . import (bitmap, charting, checks, classifier, compare, dataset, digitize, method, planner,  # noqa: E501
               pseudo2code, repo, runner)

#: 可以尝试「位图取数」的图片扩展名（SVG 走 digitize 的矢量标定）
bitmap_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def reproduce_paper(
    paper: Paper,
    out_dir: Path,
    provider: Optional[LLMProvider] = None,
    run_pseudocode: bool = True,
) -> ReproResult:
    settings = get_settings()
    provider = provider or get_provider()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = out_dir / "data"
    work_dir = out_dir / "work"
    fig_dir = out_dir / "figures"

    warnings: List[str] = []
    generated: List[str] = []

    # -- 1. 情形判定 ------------------------------------------------------ #
    decision = classifier.classify(paper)

    # -- 2. 数据集 -------------------------------------------------------- #
    infos = dataset.build_datasets(paper, data_dir)
    table_csv = {tid: info.csv_path for tid, info in infos.items()}
    generated.extend(table_csv.values())
    for info in infos.values():
        if info.truncated:
            warnings.append(
                f"表 {info.table_id} 行数超过上限 {settings.max_records}，已确定性采样截断（seed={settings.random_seed}）。"
            )

    # -- 3. 计划 ---------------------------------------------------------- #
    plan = planner.build_plan(
        paper,
        decision,
        inputs={
            "datasets": [i.to_dict() for i in infos.values()],
            "provided_data": paper.meta.get("provided_data", []),
            "local_code_path": paper.meta.get("local_code_path", ""),
            "code_refs": [r.url for r in paper.code_refs],
        },
        provider=provider,
    )
    plan.warnings.extend(warnings)
    plan_path = out_dir / "repro_plan.json"
    plan_path.write_text(plan.to_json(), encoding="utf-8")
    generated.append(str(plan_path))

    # -- 4. 按情形执行 ---------------------------------------------------- #
    deltas: List[Dict[str, Any]] = []
    extra: Dict[str, Any] = {}

    if decision.mode == MODE_OPEN_SOURCE:
        deltas, extra, w = _run_open_source(paper, out_dir, infos, decision)
        warnings.extend(w)
    elif decision.mode == MODE_PSEUDOCODE:
        deltas, extra, w = _run_pseudocode(paper, out_dir, work_dir, provider, run_pseudocode)
        warnings.extend(w)
    else:
        deltas, extra, w = _run_no_code(paper)
        warnings.extend(w)

    # 表格一致性校验对所有情形都跑（成本极低，收益高）
    table_checks = checks.run_table_checks(paper, settings.quality_thresholds())
    deltas = [*[c.to_dict() for c in table_checks], *deltas]

    # -- 5. 图表重绘 ------------------------------------------------------ #
    figure_files: List[str] = []
    for table in paper.tables:
        if not table.rows:
            continue
        spec = charting.infer_spec(table, title=f"{table.caption or ('表 ' + table.id)}")
        if spec is None:
            continue
        p = charting.render(spec, fig_dir, stem=f"{table.id}_redraw")
        if p:
            figure_files.append(p)
            generated.append(p)

    # 论文原图数字化重绘：矢量图精确标定刻度；位图走像素标定（改不到就如实跳过）
    bitmap_items: List[Dict[str, Any]] = []
    for fig in paper.figures:
        try:
            dig = digitize.digitize_figure(paper, fig)
        except Exception:
            dig = None
        if dig is not None:
            cs = charting.ChartSpec(
                kind=dig.kind, title=fig.caption or fig.id, xlabel=dig.xlabel, ylabel=dig.ylabel,
                categories=dig.categories, series=dig.series,
                note="从论文原图数字化取点（%s，置信度 %.2f）" % (dig.source, dig.confidence),
            )
            p = charting.render(cs, fig_dir, stem=f"{fig.id}_digitized")
            if p:
                figure_files.append(p)
                generated.append(p)
            continue

        # -- 位图取数：轴能标定才出数，逐值标置信 -------------------------- #
        path = digitize.resolve_image_path(paper, fig)
        if path is None:
            bitmap_items.append({
                "figure": getattr(fig, "id", ""), "caption": (getattr(fig, "caption", "") or "")[:120],
                "state": "skipped", "why": "未找到该图的图片文件（PDF 里可能未提取出图资产）",
                "confidence": None, "n_panels": 0, "panels": [], "files": {},
                "disclaimer": bitmap.DISCLAIMER,
            })
            continue
        if path.suffix.lower() not in bitmap_IMAGE_SUFFIXES:
            continue
        try:
            res = bitmap.digitize(path, fig_dir / f"{fig.id}_bitmap", fig.id)
        except Exception as exc:
            res = {"state": "skipped", "figure": fig.id,
                   "why": "位图取数异常：%s: %s" % (type(exc).__name__, exc)}
        item = _bitmap_item(fig, res)
        bitmap_items.append(item)
        files = res.get("files") or {}
        if res.get("state") == "ok":
            for key in ("preview", "csv", "script", "style", "meta"):
                if files.get(key):
                    generated.append(files[key])
            if files.get("preview"):
                figure_files.append(files["preview"])
        elif res.get("state") == "style_only":
            # 蜂群/小提琴图：数值不取，但代码 + 配色 + 同款预览图照样进复现产物
            for key in ("preview", "script", "style", "meta"):
                if files.get(key):
                    generated.append(files[key])
            if files.get("preview"):
                figure_files.append(files["preview"])

    # -- 6. 汇总 ---------------------------------------------------------- #
    summary = {
        "mode": decision.mode,
        "mode_label": classifier.mode_label(decision.mode),
        "mode_decision": decision.to_dict(),
        "datasets": {tid: i.to_dict() for tid, i in infos.items()},
        "checks": checks.summarize_checks([_to_check(d) for d in deltas]),
        "deltas_summary": compare.summarize_deltas([_to_check(d) for d in deltas]),
        "figures_redrawn": len(figure_files),
        "bitmap_digitization": bitmap_items,
        **extra,
    }

    # -- 6.5 方法学抽取：数据来源官方链接 / 筛选标准注释 / 技术操作说明书 -------- #
    # 报告原有的「操作手册」讲的是本工具怎么跑；这一块补的是**论文自己的方法**，
    # 并按用户要求单独落一份 TECH_GUIDE.md。抽不到就如实写「未能自动抽取」。
    try:
        manual_path = out_dir / "TECH_GUIDE.md"
        manual_path.write_text(method.render_manual(paper), encoding="utf-8")
        generated.append(str(manual_path))
        summary["tech_guide"] = str(manual_path)
        summary["data_sources"] = method.data_sources(paper)
        summary["selection_criteria"] = method.selection_criteria(paper)
        summary["method_tools"] = method.tools_mentioned(paper)
        summary["workflow"] = method.workflow_steps(paper)
    except Exception as exc:
        warnings.append("技术操作说明书生成失败（不影响其他产物）：%s: %s" % (type(exc).__name__, exc))

    result = ReproResult(
        paper_id=paper.id,
        mode=decision.mode,
        ok=summary["checks"]["verdict"] != "issues_found",
        generated=generated,
        table_csv=table_csv,
        figure_files=figure_files,
        deltas=deltas,
        summary=summary,
        warnings=warnings,
    )

    # -- 7. 报告 ---------------------------------------------------------- #
    report_path = out_dir / "repro_report.md"
    report_path.write_text(render_report(paper, plan, result), encoding="utf-8")
    result.report_path = str(report_path)
    generated.append(str(report_path))

    html_path = out_dir / "repro_report.html"
    html_path.write_text(render_html_report(paper, plan, result, html_path.parent), encoding="utf-8")
    generated.append(str(html_path))

    (out_dir / "repro_result.json").write_text(result.to_json(), encoding="utf-8")
    return result


# --------------------------------------------------------------------------- #
# 情形 A
# --------------------------------------------------------------------------- #
#: 仓库定位状态（报告用；区分「有自有代码」「只有第三方引用」「克隆失败」）
_REPO_STATUS_LABELS = {
    "local": "使用论文包内自带代码",
    "cloned": "已克隆自有仓库",
    "not_found": "未找到可用自有仓库（候选均不可用）",
    "clone_failed": "自有仓库存在但克隆失败",
    "no_candidate": "正文无自有代码链接（仅有第三方/文献列表引用）",
}


def _provision_repo_env(out_dir: Path, plan: Dict[str, Any], dep: Dict[str, Any],
                        settings: Any) -> Dict[str, Any]:
    """D：按显式开关为论文仓库准备隔离 venv；关闭时只生成可执行安装脚本。

    **绝不静默安装**：``repo_venv_auto`` 默认 False。关闭/失败/重型依赖被拦时，
    仍会把 ``requirements.lock.txt`` + ``install_env.ps1/.sh`` 落到
    ``reproduce/``，让用户能一键复现同一套依赖。
    """
    specs = list(plan.get("declared_requirements") or [])
    for m in dep.get("pip_missing") or []:
        specs.append(deps.PACKAGE_NAMES.get(m, m))
    specs = deps.clean_requirements(specs)
    venv_dir = Path(out_dir) / "venv"
    if not specs:
        return {"status": "no_deps", "specs": [], "venv": str(venv_dir)}

    pip_index = str(getattr(settings, "repo_pip_index", "")
                    or getattr(settings, "sandbox_pip_index", "") or "")
    scripts = repo.write_install_scripts(Path(out_dir), venv_dir, specs, pip_index=pip_index)
    info: Dict[str, Any] = {"status": "not_enabled", "specs": specs,
                            "venv": str(venv_dir), "scripts": scripts}
    if not bool(getattr(settings, "repo_venv_auto", False)):
        info["note"] = ("自动安装未开启；可执行 install_env.ps1 / install_env.sh，"
                        "或加 --install-repo-deps（等价于 P2C_REPO_VENV_AUTO=1）")
        return info

    info.update(repo.provision_venv(
        venv_dir, specs, pip_index=pip_index,
        timeout=int(getattr(settings, "repo_install_timeout", 900) or 900),
        log_path=Path(out_dir) / "venv_install.log",
        allow_heavy=bool(getattr(settings, "repo_install_heavy", False)),
    ))
    info["scripts"] = scripts
    return info


def _run_open_source(paper: Paper, out_dir: Path, infos: Dict[str, dataset.DatasetInfo], decision) -> tuple:
    warnings: List[str] = []
    extra: Dict[str, Any] = {}
    deltas: List[Dict[str, Any]] = []

    local = classifier.detect_local_repo(paper)
    root = Path(local) if local else None
    candidates = [
        r for r in paper.repo_candidates()
        if getattr(r, "role", "unclear") != "third_party"
    ]
    selected = None
    attempts: List[Dict[str, Any]] = []
    repo_status = "local" if root else ("no_candidate" if not candidates else "not_found")

    if root is None and candidates:
        dest = out_dir / "repo"
        for cand in candidates[:3]:
            probe = repo.probe_remote(cand.url)
            attempt = {
                "url": cand.url,
                "role": cand.role,
                "confidence": round(float(cand.confidence or 0.0), 3),
                "subpath": cand.subpath,
                "probe": probe,
            }
            attempts.append(attempt)
            if not probe.get("ok"):
                continue
            clone = repo.try_clone(cand.url, dest)
            attempt["clone"] = clone
            if clone.get("ok"):
                root = Path(clone["path"])
                selected = cand
                repo_status = "cloned"
                break
        if root is None:
            probed_ok = any(a["probe"].get("ok") for a in attempts)
            repo_status = "clone_failed" if probed_ok else "not_found"
            tried = "、".join(a["url"] for a in attempts) or "（无）"
            warnings.append(
                "未找到可用的自有仓库：已探测 %d 个候选（%s），均不可用；"
                "本次只能按正文与表格重建，待执行脚本仍会输出。" % (len(attempts), tried))
    elif root is None:
        warnings.append(
            "正文只出现第三方/文献列表仓库引用，未发现本论文自有代码；按无代码重建。")

    extra["repo_status"] = repo_status
    extra["repo_status_label"] = _REPO_STATUS_LABELS.get(repo_status, repo_status)
    extra["repo_url"] = (selected.url if selected else
                         (candidates[0].url if candidates else ""))
    extra["repo_candidates"] = [
        {"url": r.url, "role": r.role,
         "confidence": round(float(r.confidence or 0.0), 3),
         "subpath": r.subpath, "reason": r.role_reason}
        for r in paper.repo_candidates()
    ]
    if attempts:
        extra["repo_attempts"] = attempts

    manifest = repo.introspect(root) if root else repo.RepoManifest(
        root="", url=extra["repo_url"], notes=["未找到可用仓库"]
    )
    manifest.url = manifest.url or extra["repo_url"]

    seed = repo.extract_seed_from_readme(manifest.readme) or get_settings().random_seed
    manifest_path = repo.write_manifest(manifest, out_dir, extra={"decision": decision.to_dict()})
    extra["repo_manifest"] = manifest_path

    run_script = repo.build_run_script(manifest, python_exe="python", seed=seed)
    script_path = out_dir / "repro_run.sh"
    script_path.write_text(run_script, encoding="utf-8")
    extra["repro_run_script"] = str(script_path)
    warnings.extend(manifest.notes)

    # 把仓库自带的数据文件登记为「复现输入」
    if root and manifest.data_files:
        extra["repo_data_files"] = manifest.data_files[:20]

    # 若仓库已有结果文件，直接与论文表格对标
    if root:
        result_files = repo.pick_result_files(manifest, root)
        extra["repo_result_files"] = [str(p) for p in result_files[:8]]
        for rf in result_files[:4]:
            rows = compare.pick_repro_rows_from_csv(str(rf))
            if not rows:
                continue
            for table in paper.tables:
                # 只拿「论文正文里报告的表格」做对标；附带的数据文件是被复现的输入，不是基准
                if not table.rows or table.source == "provided":
                    continue
                res = compare.compare_rows(table, rows, get_settings().quality_thresholds())
                if any(c.status in ("pass", "warn", "fail") for c in res):
                    deltas.extend(c.to_dict() for c in res)
        if not deltas:
            warnings.append(
                "仓库内未找到可与论文表格直接对齐的结果文件；已生成 repro_run.sh，"
                "请先执行脚本产出 results/*.csv，再重新运行复现环节即可自动对标。"
            )

    extra["repo_available"] = bool(root)

    # -- M3-② smoke 运行：把「能不能跑」问出来，而不是只留一份待人工执行的脚本 ---- #
    if root:
        smoke_dir = out_dir / "smoke"
        venv_exe = os.environ.get("P2C_REPO_VENV", "").strip()
        # C：首选入口失败（构建脚本 / 示例 demo / ImportError）时自动换下一个候选；
        # 依赖阻塞则不再空转——那是 D 阶段「隔离环境安装」要解决的问题。
        entries = repo.entry_candidates(manifest, limit=3) or [""]
        attempts: List[Dict[str, Any]] = []
        final_plan: Dict[str, Any] = {}
        final_outcome: Dict[str, Any] = {}
        info: Dict[str, Any] = {"dir": str(smoke_dir)}
        for entry in entries:
            plan = repo.build_smoke_plan(manifest, root, smoke_dir, seed=seed, entrypoint=entry)
            # 依赖预检（复用 roadmap M3-⑤ 的解析器）：缺依赖时不硬跑，直接如实标阻塞。
            # 用 plan 里已剔除「仓库自带模块」的清单，避免把 `modules` 之类误报成缺依赖。
            # 若配了 P2C_REPO_VENV，就**按那个 venv** 判定，并让 smoke 也用同一个解释器跑。
            modules = list(plan.get("required_modules") or []) + list(plan.get("declared_requirements") or [])
            override = deps.available_in(venv_exe, modules) if venv_exe else None
            dep = deps.analyze([], modules, available_override=override or None)
            plan["interpreter"] = venv_exe or "current"
            plan["missing_dependencies"] = dep["missing"]
            plan["install_hint"] = dep["install_hint"] if dep["missing"] else ""

            # D：显式开启时，先建隔离 venv 并装依赖；关闭时只生成安装脚本。
            env_info = _provision_repo_env(out_dir, plan, dep, get_settings())
            plan["env_install"] = env_info
            env_ready = bool(env_info.get("ok") and env_info.get("venv_python"))
            if env_ready:
                venv_exe = env_info["venv_python"]
                override = deps.available_in(venv_exe, modules)
                dep = deps.analyze([], modules, available_override=override)
                plan["missing_dependencies"] = dep["missing"]
                plan["install_hint"] = dep["install_hint"] if dep["missing"] else ""
                plan["blockers"] = [b for b in plan["blockers"] if not b.startswith("缺少依赖")]
                if dep["missing"]:
                    plan["blockers"].append(
                        "缺少依赖（隔离 venv 中仍缺）：%s%s"
                        % ("、".join(dep["missing"]),
                           ("；" + dep["manual_hint"]) if dep.get("manual_hint") else ""))
                plan["interpreter"] = venv_exe

            if dep["missing"] and not env_ready:
                plan["blockers"].append(
                    "缺少依赖：%s（%s%s），无法在本机跑 smoke"
                    % ("、".join(dep["missing"]), dep["install_hint"] or "",
                       ("；" + dep["manual_hint"]) if dep.get("manual_hint") else ""))
            # M3-②：入口被「装不上的依赖」或「没有入口」卡住时，尝试 library 模式。
            # 普通缺 PyPI 依赖不切 library——实测会掩盖真正的阻塞原因。
            repo.add_library_mode(plan, root, dep["missing"])
            info = repo.write_smoke(smoke_dir, plan)
            runnable = not plan["blockers"] or plan.get("mode") == "library"
            outcome = _run_repo_smoke(smoke_dir, venv_exe) if runnable else {
                "status": "blocked", "reason": "；".join(plan["blockers"]) or "未尝试"}
            outcome = _normalize_smoke_outcome(outcome, plan)
            attempts.append({
                "entrypoint": plan.get("entrypoint") or "",
                "status": outcome["status"],
                "reason": outcome["reason"][:300],
                "missing_dependencies": outcome["missing_dependencies"],
            })
            final_plan, final_outcome = plan, outcome
            if outcome["status"] == "ran":
                break
            if outcome["status"] in ("blocked_deps", "blocked_env"):
                break  # 换入口也装不上依赖，交给隔离环境安装阶段

        extra["smoke_dir"] = info["dir"]
        extra["smoke_plan"] = final_plan
        extra["smoke_result"] = final_outcome
        extra["smoke_attempts"] = attempts
        extra["env_install"] = final_plan.get("env_install") or {}
        env = extra["env_install"]
        env_status = env.get("status")
        if env_status in ("installed", "reused"):
            warnings.append("隔离依赖环境已就绪（%s）：%s" % (env_status, env.get("venv") or ""))
        elif env_status == "heavy_blocked":
            warnings.append(env.get("error") or "重型依赖未自动安装")
        elif env_status == "failed":
            warnings.append("隔离依赖环境安装失败：%s（日志：%s）"
                            % ((env.get("error") or "")[:250], env.get("log") or ""))
        elif env_status == "not_enabled":
            warnings.append(env.get("note") or "未开启隔离依赖自动安装")
        if final_outcome.get("status") == "ran":
            warnings.append("情形 A smoke 运行**跑通**（缩减规模）；结果见 smoke/result.json。")
        else:
            warnings.append(
                "情形 A smoke 运行未跑通（%s）：%s"
                % (final_outcome.get("status_label") or final_outcome.get("status"),
                   (final_outcome.get("reason") or "")[:300]))
            if len(attempts) > 1:
                warnings.append(
                    "已尝试 %d 个入口候选：%s"
                    % (len(attempts), "；".join(a["entrypoint"] or "（无）" for a in attempts)))
        _write_json(smoke_dir / "result.json", final_outcome)
    return deltas, extra, warnings


#: C：smoke 结果状态的中文标签（区分「代码失败」与「环境/入口没准备好」）
_SMOKE_STATUS_LABELS = {
    "ran": "跑通",
    "failed": "运行失败",
    "blocked_deps": "依赖未就绪，未执行",
    "blocked_entry": "未找到可执行入口",
    "blocked_env": "环境不满足，未执行",
    "skipped_no_repo": "无可用仓库，未执行",
}


def _normalize_smoke_outcome(outcome: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    """统一 smoke 状态并**补全失败原因**。

    旧实现只看 ``outcome["reason"]``；实测 trisds 的 result.json 只有
    ``stderr_tail``，于是报告里出现空白的 ``status=failed：``。这里把
    stderr/stdout 尾部提升为 reason，并把笼统的 ``blocked`` 细分成
    blocked_deps / blocked_entry / blocked_env。
    """
    out = dict(outcome or {})
    status = str(out.get("status") or "failed")
    blockers = "；".join(plan.get("blockers") or [])
    if status == "blocked":
        # 没有入口是更根本的阻塞：即便同时缺依赖，也应先报 blocked_entry
        if "入口" in blockers:
            status = "blocked_entry"
        elif "缺少依赖" in blockers:
            status = "blocked_deps"
        else:
            status = "blocked_env"
    reason = str(out.get("reason") or "").strip()
    if not reason:
        for key in ("stderr_tail", "stdout_tail"):
            tail = str(out.get(key) or "").strip()
            if tail:
                reason = tail[-300:]
                break
    out["status"] = status
    out["status_label"] = _SMOKE_STATUS_LABELS.get(status, status)
    out["reason"] = reason or "smoke 失败，但运行器未给出原因（见 smoke/result.json）"
    out["entrypoint"] = plan.get("entrypoint") or ""
    out["missing_dependencies"] = list(plan.get("missing_dependencies") or [])
    return out


def _read_text(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    try:
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _run_repo_smoke(smoke_dir: Path, python_exe: str = "") -> Dict[str, Any]:
    """执行生成的 smoke 运行器并读回其 ``result.json``。

    ``trusted=True``：``run_smoke.py`` 是本仓库确定性生成器写的「内部运行器」，它必须用
    ``subprocess`` 启动仓库入口——这是有意为之且边界明确（见 ``tests/test_safety.py``）。
    真正不可信的**仓库入口代码**在容器模式下一并跑在容器里，受无网络/只读根/资源上限约束。
    """
    try:
        runner.run_python_script(smoke_dir / "run_smoke.py", smoke_dir,
                                 timeout=int(repo.SMOKE_TIMEOUT_S) + 30, allow_network=False,
                                 python_exe=python_exe or None, trusted=True)
    except Exception as exc:
        if isinstance(exc, control.RunCancelled):
            raise          # M5-①：取消/超时必须穿透（否则 repo smoke 会把取消吞成一条失败记录）
        return {"status": "failed", "reason": "smoke 运行器异常：%s: %s" % (type(exc).__name__, exc)}
    out = smoke_dir / "result.json"
    if not out.exists():
        return {"status": "failed", "reason": "smoke 运行器未写出 result.json"}
    try:
        return json.loads(out.read_text(encoding="utf-8"))
    except ValueError as exc:
        return {"status": "failed", "reason": "result.json 不可解析：%s" % exc}


# --------------------------------------------------------------------------- #
# 情形 C
# --------------------------------------------------------------------------- #
def _run_pseudocode(
    paper: Paper,
    out_dir: Path,
    work_dir: Path,
    provider: LLMProvider,
    run: bool,
) -> tuple:
    warnings: List[str] = []
    extra: Dict[str, Any] = {"algorithms": []}
    deltas: List[Dict[str, Any]] = []

    for algo in paper.algorithms[:4]:
        entry: Dict[str, Any] = {"id": algo.id, "caption": algo.caption[:120], "intent": checks.infer_algorithm_intent(algo)}
        if not run:
            entry["status"] = "skipped"
            extra["algorithms"].append(entry)
            continue

        pr = pseudo2code.execute_pseudocode(paper, algo, provider, work_dir / algo.id, timeout=get_settings().exec_timeout)
        entry.update(
            {
                "status": "ok" if pr.ok else "failed",
                "fn_name": pr.fn_name,
                "input_spec": pr.input_spec,
                "code_path": pr.code_path,
                "error": (pr.error or "")[:500],
            }
        )
        if pr.result is not None:
            out_val = pr.result.get("output")
            entry["output_preview"] = json.dumps(out_val, ensure_ascii=False)[:600]
            inputs = {"data": pseudo2code.make_input(pr.input_spec["kind"], pr.input_spec["n"], pr.input_spec["seed"])}
            pc = checks.run_property_checks(algo, inputs, {"_result": out_val})
            entry["property_checks"] = [c.to_dict() for c in pc]
            deltas.extend(c.to_dict() for c in pc)
        else:
            entry["output_preview"] = ""
            warnings.append(f"算法 {algo.id} 执行未回收结果：{(pr.stderr or pr.error or '')[:200]}")

        # 被注释掉的伪代码行必须让用户看见——这是「翻译覆盖率」的诚实披露，
        # 而不是悄悄降级后宣称复现成功。
        repairs = list((pr.input_spec or {}).get("repairs") or [])
        if repairs:
            entry["untranslated_lines"] = repairs
            warnings.append(
                f"算法 {algo.id} 有 {len(repairs)} 行伪代码无法自动翻译，已注释保留："
                + "；".join(repairs[:3])
                + ("…" if len(repairs) > 3 else "")
            )

        # 参考用翻译产物落到 outputs，便于人工审阅
        try:
            src = Path(pr.code_path)
            if src.exists():
                dst = out_dir / f"translated_{algo.id}.py"
                shutil.copy2(src, dst)
                entry["translated_artifact"] = str(dst)
        except Exception:
            pass

        extra["algorithms"].append(entry)

    if not paper.algorithms:
        warnings.append("未识别到伪代码块，情形 C 无可用输入。")
    if extra["algorithms"]:
        warnings.append(
            "本情形为「逻辑复现」：只校验算法输出的数学性质，未对标论文指标数值。"
        )
    return deltas, extra, warnings


# --------------------------------------------------------------------------- #
# 情形 B
# --------------------------------------------------------------------------- #
def _run_no_code(paper: Paper) -> tuple:
    warnings: List[str] = []
    extra: Dict[str, Any] = {}

    # 列出「无法复现要素」
    missing: List[str] = []
    body = (paper.section_text("experiment", "method") or "")[:6000].lower()
    for name, keys in (
        ("数据集/数据来源", ("dataset", "corpus", "数据", "benchmark", "data availability", "we release", "trace", "log")),
        ("超参数与训练配置", ("learning rate", "batch size", "epoch", "学习率", "超参", "hyperparameter")),
        ("随机种子/重复次数", ("seed", "trials", "随机种子", "重复", "runs")),
        ("预处理与分词细节", ("preprocess", "tokeniz", "预处理", "分词", "filter")),
        ("硬件与运行环境", ("gpu", "cpu", "memory", "硬件", "core", "single-thread")),
    ):
        if not any(k in body for k in keys):
            missing.append(name)
    extra["missing_elements"] = missing
    if missing:
        warnings.append(
            "以下要素在论文中未明确披露，无法复现："
            + "、".join(missing)
            + "。已列入报告，供人工补齐后二次复现。"
        )
    if not paper.tables:
        refs = paper.meta.get("table_refs") if hasattr(paper, "meta") else None
        if refs:
            warnings.append(
                f"正文引用了 `Table N` ×{refs}，但解析阶段未能抽出任何表格"
                "（PDF 表格无可抽取框线或按单元格独立排版），情形 B 无法产出复现结果。"
            )
        else:
            warnings.append("论文未提供可抽取的表格数据，情形 B 无法产出复现结果。")
    return [], extra, warnings


def _to_check(d: Dict[str, Any]) -> checks.CheckResult:
    return checks.CheckResult(
        name=d.get("name", ""),
        scope=d.get("scope", ""),
        status=d.get("status", "skipped"),
        detail=d.get("detail", ""),
        expected=d.get("expected"),
        actual=d.get("actual"),
        rel_delta=d.get("rel_delta"),
    )


# --------------------------------------------------------------------------- #
# HTML 报告
# --------------------------------------------------------------------------- #
_H_STATUS = {
    "pass": ("通过", "#15803d", "#e8f8ee"),
    "warn": ("告警", "#b45309", "#fef4e2"),
    "fail": ("失败", "#b91c1c", "#fdeaea"),
    "skipped": ("跳过", "#5d6b85", "#f1f3f9"),
}

_H_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#f5f7fb;--card:#fff;--ink:#1b2130;--muted:#5d6b85;--line:#e3e8f2;
--brand:#4f6bed;--brand2:#8b5cf6;--accent:#0ea5a4;}
body{background:radial-gradient(1000px 560px at 6% -10%,#e8edff,transparent 60%),
radial-gradient(820px 460px at 104% 0%,#eafaf7,transparent 55%),var(--bg);
color:var(--ink);font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
padding:32px 20px 80px}
.wrap{max-width:1100px;margin:0 auto}
.hero{background:linear-gradient(135deg,#3b4fd8 0%,#6d4de0 48%,#0e9f9a 100%);color:#fff;
border-radius:20px;padding:34px 38px;margin-bottom:22px;box-shadow:0 14px 40px rgba(59,79,216,.24)}
.hero .kicker{font-size:12.5px;letter-spacing:.18em;text-transform:uppercase;opacity:.86;font-weight:700}
.hero h1{font-size:30px;line-height:1.3;font-weight:800;margin:10px 0 14px;letter-spacing:-.02em}
.hero .pills{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}
.hero .pill{background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.28);
border-radius:999px;padding:5px 14px;font-size:13px;font-weight:650}
.hero .reason{opacity:.92;font-size:14px;margin-top:16px;max-width:860px}
.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:24px 28px;
box-shadow:0 8px 26px rgba(24,39,75,.07);margin-bottom:18px}
.card h2{font-size:20px;font-weight:750;margin-bottom:14px;display:flex;align-items:center;gap:10px}
.card h2::before{content:"";width:5px;height:20px;border-radius:3px;
background:linear-gradient(180deg,var(--brand),var(--brand2))}
.card h3{font-size:16.5px;font-weight:700;color:#39457a;margin:18px 0 8px}
table{border-collapse:collapse;width:100%;font-size:13.8px}
th,td{border-bottom:1px solid var(--line);padding:9px 12px;text-align:left;vertical-align:top}
th{background:#eef2ff;font-weight:700;color:#39457a;white-space:nowrap}
tr:nth-child(even) td{background:#fafbff}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:700;white-space:nowrap}
.grid2{display:grid;gap:16px;grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}
.gallery{display:grid;gap:16px;grid-template-columns:repeat(auto-fill,minmax(340px,1fr))}
.shot{border:1px solid var(--line);border-radius:14px;overflow:hidden;background:#fff}
.shot.wide{grid-column:1/-1}
.shot a{display:block}
.shot img{display:block;width:100%;height:auto;background:#fff}
.shot .cap{padding:10px 14px;font-size:12.8px;color:var(--muted);border-top:1px solid var(--line)}
.strategy{background:#f8faff;border:1px solid var(--line);border-left:4px solid var(--brand);
border-radius:0 12px 12px 0;padding:16px 20px;white-space:pre-wrap;font-size:14.5px}
ol.steps{margin:10px 0 0 22px}ol.steps li{margin:5px 0;font-size:14.4px}
ul.notes{margin:8px 0 0 22px}ul.notes li{margin:4px 0;font-size:14.2px;color:#6b5a2c}
.warnbox{background:linear-gradient(180deg,#fffdf5,#fff9e8);border:1px dashed #ecd9a6;
border-radius:12px;padding:14px 18px;font-size:14.2px;color:#6b5a2c}
.muted{color:var(--muted);font-size:13.6px}
.kpi{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));margin-bottom:4px}
.kpi .box{background:#f8faff;border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.kpi .box b{display:block;font-size:23px;font-weight:800;letter-spacing:-.02em}
.kpi .box span{font-size:12.6px;color:var(--muted)}
footer{text-align:center;color:var(--muted);font-size:13px;margin-top:26px}
"""


def _is_wide_image(path: Path, ratio: float = 1.25, min_width: int = 900) -> bool:
    """宽图或多面板大图在画廊里占满整行，否则缩到 340px 卡片里看不清。"""
    try:
        from PIL import Image  # type: ignore

        with Image.open(path) as im:
            return im.width >= min_width or im.width / max(1, im.height) > ratio
    except Exception:
        return False


def _bitmap_item(fig: Any, res: Dict[str, Any]) -> Dict[str, Any]:
    """把位图取数结果压成报告要用的精简结构（数值只留前几个，避免报告过大）。"""
    state = res.get("state")
    panels = res.get("panels") or []
    brief: List[Dict[str, Any]] = []
    for i, p in enumerate(panels, start=1):
        st = p.get("style") or {}
        if p.get("kind") == "bar":
            vals = [b.get("value") for b in p.get("bars", [])]
            brief.append({"panel": i, "kind": "bar", "orientation": p.get("orientation"),
                          "count": len(vals), "values": vals[:6],
                          "gradient": st.get("gradient"), "colors": (st.get("colors") or [])[:3]})
        else:
            pts = p.get("points", [])
            brief.append({"panel": i, "kind": "scatter", "count": len(pts),
                          "values": [[q.get("x"), q.get("y")] for q in pts[:4]],
                          "marker": st.get("marker"), "trend_line": st.get("trend_line"),
                          "reference_lines": st.get("reference_lines")})
    return {
        "figure": getattr(fig, "id", res.get("figure", "")),
        "caption": (getattr(fig, "caption", "") or "")[:120],
        "state": state,
        "chart_type": res.get("chart_type", ""),
        "colorbar": res.get("colorbar"),
        "why": res.get("why", ""),
        "confidence": res.get("confidence"),
        "n_panels": len(panels),
        "panels": brief,
        "files": res.get("files") or {},
        "disclaimer": bitmap.DISCLAIMER,
    }


def _style_summary(it: Dict[str, Any]) -> str:
    """一句话说明「从原图量到的样式」，让用户能核对是否同款。"""
    if it.get("state") == "style_only":
        cb = it.get("colorbar") or {}
        cols = cb.get("colors_high_to_low") or []
        axis = (it.get("x_axis") or {}).get("ticks") or []
        bits = [f"图表类型 {it.get('chart_type') or 'beeswarm'}（纵向蜂群/小提琴）"]
        if cols:
            bits.append("colorbar 配色 " + " → ".join(cols) + "（High→Low）")
        if axis:
            bits.append("x 轴 %.3g ~ %.3g" % (axis[0][1], axis[-1][1]))
        bits.append(f"作图代码 draw_{it.get('figure')}.py（数值不取，不编造）")
        return "；".join(bits)
    parts: List[str] = []
    for p in (it.get("panels") or [])[:3]:
        if p.get("kind") == "scatter":
            m = p.get("marker") or {}
            t = p.get("trend_line") or {}
            seg = []
            if m.get("diameter_px"):
                seg.append("点径 %.1fpx（约 %.1f%% 轴宽）" % (m["diameter_px"],
                                                           (m.get("diameter_ratio") or 0) * 100))
            if m.get("edge_color"):
                seg.append("描边 %s" % m["edge_color"])
            if m.get("fills"):
                seg.append("%d 种填充色 %s" % (len(m["fills"]), "、".join(m["fills"][:4])))
            if t.get("slope") is not None:
                seg.append("红色趋势线 y=%.3fx%+.3f" % (t["slope"], t.get("intercept", 0)))
            if p.get("reference_lines"):
                seg.append("%d 条灰色虚线参考线" % len(p["reference_lines"]))
            parts.append("面板 %d：%s" % (p["panel"], "，".join(seg)))
        elif p.get("gradient"):
            g = p["gradient"]
            parts.append("面板 %d：%d 根柱渐变 %s → %s（%s）"
                         % (p["panel"], p["count"], g.get("from"), g.get("to"),
                            "自上而下" if g.get("axis") == "vertical" else "自左而右"))
        elif p.get("colors"):
            parts.append("面板 %d：%d 根柱同色 %s" % (p["panel"], p["count"], p["colors"][0]))
    return "；".join(parts)


def _bitmap_detail(it: Dict[str, Any]) -> str:
    def fmt(v: Any) -> str:
        if isinstance(v, (int, float)):
            return "%.3g" % v
        if isinstance(v, (list, tuple)) and len(v) == 2:
            a, b = v
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                return "(%.3g, %.3g)" % (a, b)
        return "—"

    chunks = []
    panels = it.get("panels", [])
    for p in panels[:4]:
        vals = p.get("values") or []
        shown = "、".join(fmt(v) for v in vals)
        kind = "横向柱" if p.get("orientation") == "h" else (
            "柱状" if p.get("kind") == "bar" else "散点")
        chunks.append(f"面板 {p['panel']}（{kind}，{p['count']} 个值）：{shown}"
                      + ("…" if p["count"] > len(vals) else ""))
    if len(panels) > 4:
        chunks.append(f"……共 {len(panels)} 个面板，全部数值见 data.csv")
    return "；".join(chunks)


def _state_label(state: str) -> str:
    return {"ok": "已取数", "style_only": "仅样式（代码+配色）"}.get(state, "跳过")


def _bitmap_html(items: List[Dict[str, Any]]) -> str:
    """报告里的「位图图表取值」表格（附强制免责声明 + 样式核对）。"""
    if not items:
        return ""
    rows = []
    for it in items:
        state = it.get("state")
        ok = state == "ok"
        detail = _bitmap_detail(it) if ok else (it.get("why") or "未说明原因")
        style = _style_summary(it)
        confidence = it.get("confidence") if ok else ""
        rows.append(
            f"<tr><td><code>{_h(it.get('figure'))}</code></td>"
            f"<td>{_h(_state_label(state))}</td>"
            f"<td class='num'>{_h(confidence if confidence is not None else '')}</td>"
            f"<td>{_h(detail)}</td>"
            f"<td class='muted'>{_h(style)}</td>"
            f"<td class='muted'>{_h(it.get('caption') or '')}</td></tr>"
        )
    return (
        "<div class='card'><h2>七之二、位图图表取值与样式复现</h2>"
        f"<p class='warnbox'>{_h(bitmap.DISCLAIMER)}。"
        "只有坐标轴能读到 ≥2 个数值刻度、且单调、等距时才出数；"
        "读不出、对数轴、散点/蜂群过于密集的图不编造数值，"
        "但**样式（点径/描边/趋势线/渐变柱/colorbar）照原图复现**，并输出作图代码。"
        "柱状图的分类名（如 PDB ID、特征名）未做文字识别，按图中自上而下/从左到右编号。"
        "逐值附置信度，数据见各图目录下的 <code>data.csv</code>，"
        "同目录 <code>draw_*.py</code>、<code>style.json</code> 可用你自己的数据重画同款图。</p>"
        "<table><thead><tr><th>图</th><th>状态</th><th class='num'>置信度</th>"
        "<th>取值 / 说明</th><th>识别到的样式</th><th>图注</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _bitmap_markdown(items: List[Dict[str, Any]]) -> List[str]:
    if not items:
        return []
    out = [
        "### 位图图表取值与样式复现",
        "",
        f"> {bitmap.DISCLAIMER}。只有坐标轴能读到 ≥2 个数值刻度、且单调、等距时才出数；"
        "读不出、对数轴、散点/蜂群过于密集的图不编造数值，但样式照原图复现并输出作图代码。",
        "",
        "| 图 | 状态 | 置信度 | 取值 / 说明 | 识别到的样式 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for it in items:
        state = it.get("state")
        style = _style_summary(it).replace("|", "/")
        if state == "ok":
            out.append("| `%s` | 已取数 | %s | %s | %s |"
                       % (it.get("figure"), it.get("confidence"),
                          _bitmap_detail(it), style))
        elif state == "style_only":
            out.append("| `%s` | 仅样式 | — | %s | %s |"
                       % (it.get("figure"), (it.get("why") or "").replace("|", "/"), style))
        else:
            out.append("| `%s` | 跳过 | — | %s | — |"
                       % (it.get("figure"), (it.get("why") or "未说明原因").replace("|", "/")))
    out.append("")
    shown = [it for it in items if it.get("state") in ("ok", "style_only")
             and (it.get("files") or {}).get("preview")]
    if shown:
        out += ["### 位图图表预览", ""]
        for it in shown:
            fig = it.get("figure")
            tag = "同款样式示意（合成数据）" if it.get("state") == "style_only" else "位图反推（非原文献数值）"
            out.append(f"![{fig} {tag}](figures/{fig}_bitmap/preview_{fig}.png)")
            out.append("")
    return out


def _h(text: Any) -> str:
    return (
        str(text if text is not None else "")
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _badge(status: str) -> str:
    label, fg, bg = _H_STATUS.get(status, (status, "#5d6b85", "#f1f3f9"))
    return f'<span class="badge" style="color:{fg};background:{bg}">{_h(label)}</span>'


def render_html_report(paper: Paper, plan, result: ReproResult, base_dir: Path) -> str:
    s = result.summary
    dec = s.get("mode_decision", {})
    checks = s.get("checks", {})
    deltas = s.get("deltas_summary", {})

    # -- KPI -- #
    kpi = [
        (str(checks.get("pass", 0)), "通过检查"),
        (str(checks.get("warn", 0)), "告警检查"),
        (str(checks.get("fail", 0)), "失败检查"),
        (str(len(result.figure_files)), "重绘图表"),
        (str(len(result.table_csv)), "数据表"),
    ]
    if deltas.get("n_judged"):
        kpi.append((f"{(deltas.get('pass_rate') or 0) * 100:.0f}%", "数值对标通过率"))

    # -- 目标清单 -- #
    trows = []
    for t in plan.targets[:40]:
        size = (
            f"{t.get('n_rows')}×{t.get('n_cols')}" if t["kind"] == "table"
            else (f"{t.get('n_lines')} 行" if t["kind"] == "algorithm" else "—")
        )
        trows.append(
            f"<tr><td>{_h(t['kind'])}</td><td><code>{_h(t['id'])}</code></td>"
            f"<td>{_h((t.get('caption') or '')[:90])}</td><td class='num'>{_h(size)}</td>"
            f"<td>{_h(t.get('role', ''))}</td></tr>"
        )

    # -- 数据输入 -- #
    drows = []
    for tid, info in (s.get("datasets") or {}).items():
        drows.append(
            f"<tr><td><code>{_h(tid)}</code></td><td class='num'>{info['n_rows']}</td>"
            f"<td class='num'>{info['n_cols']}</td><td>{_h(info['source'])}</td>"
            f"<td>{_h(', '.join(info['numeric_columns'][:6]) or '—')}</td>"
            f"<td>{'是' if info['truncated'] else '否'}</td></tr>"
        )

    # -- 校验明细 -- #
    crows = []
    for d in result.deltas[:200]:
        crows.append(
            f"<tr><td>{_badge(d['status'])}</td><td>{_h(d['name'])}</td>"
            f"<td><code>{_h(d['scope'])}</code></td><td>{_h(d['detail'])}</td></tr>"
        )

    # -- 情形专属区块 -- #
    mode_block = ""
    if plan.mode == MODE_OPEN_SOURCE:
        rows = [
            ("仓库可用", "是" if s.get("repo_available") else "否"),
            ("仓库定位", _h(s.get("repo_status_label") or "—")),
            ("候选仓库", _h("；".join(
                "%s（%s）" % (c.get("url"), c.get("role") or "unclear")
                for c in (s.get("repo_candidates") or [])[:5]) or "—")),
            ("本地代码目录", f"<code>{_h(s.get('local_code_path') or '—')}</code>"),
            ("仓库数据文件", _h(", ".join(s.get("repo_data_files", [])[:8]) or "—")),
            ("采集到的结果文件", _h(", ".join(Path(p).name for p in s.get("repo_result_files", [])[:8]) or "—")),
            ("复现脚本", f"<code>{_h(Path(s.get('repro_run_script', '') or '').name)}</code>"),
            ("smoke 运行", _h((s.get("smoke_result") or {}).get("status_label")
                              or (s.get("smoke_result") or {}).get("status") or "—")
             + ("：" + _h(((s.get("smoke_result") or {}).get("reason") or "")[:200])
                if (s.get("smoke_result") or {}).get("reason") else "")),
            ("入口尝试", _h("；".join(
                "%s → %s" % (a.get("entrypoint") or "（无）", a.get("status"))
                for a in (s.get("smoke_attempts") or [])) or "—")),
            ("依赖环境", _h({
                "installed": "已在隔离 venv 安装", "reused": "复用已有隔离 venv",
                "not_enabled": "未开启自动安装（仅生成脚本）",
                "heavy_blocked": "重型依赖被拦截", "failed": "安装失败",
                "no_deps": "无需安装",
            }.get((s.get("env_install") or {}).get("status"),
                  (s.get("env_install") or {}).get("status") or "—"))),
        ]
        mode_block = (
            "<div class='card'><h2>五、仓库复用结果</h2><table><tbody>"
            + "".join(f"<tr><th style='width:190px'>{_h(k)}</th><td>{v}</td></tr>" for k, v in rows)
            + "</tbody></table></div>"
        )
    elif plan.mode == MODE_PSEUDOCODE:
        blocks = []
        for a in s.get("algorithms", []):
            pcs = "".join(
                f"<li>{_badge(pc['status'])} <b>{_h(pc['name'])}</b>：{_h(pc['detail'])}</li>"
                for pc in a.get("property_checks", [])
            )
            blocks.append(
                f"<h3>{_h(a.get('id'))}：{_h(a.get('caption', ''))}</h3>"
                f"<div class='muted'>识别意图 <code>{_h(a.get('intent'))}</code> · "
                f"执行状态 <b>{_h(a.get('status'))}</b> · 函数 "
                f"<code>{_h(a.get('fn_name'))}</code> · 输入规格 "
                f"<code>{_h(json.dumps(a.get('input_spec', {}), ensure_ascii=False))}</code></div>"
                + (f"<p class='muted'>输出预览：<code>{_h(a.get('output_preview', ''))[:300]}</code></p>" if a.get("output_preview") else "")
                + (f"<p class='muted'>错误：<code>{_h(a.get('error', ''))[:300]}</code></p>" if a.get("error") else "")
                + (f"<ul class='notes'>{pcs}</ul>" if pcs else "")
            )
        mode_block = "<div class='card'><h2>五、伪代码翻译与执行</h2>" + "".join(blocks) + (
            "<p class='muted' style='margin-top:14px'>本情形为<b>逻辑复现</b>：只校验算法输出的数学性质，"
            "未对标论文指标数值。</p></div>"
        )
    else:
        missing = s.get("missing_elements", [])
        mode_block = (
            "<div class='card'><h2>五、无法复现要素清单</h2>"
            + ("<ul class='notes'>" + "".join(f"<li>{_h(m)}</li>" for m in missing) + "</ul>"
               if missing else "<p class='muted'>未发现明显缺失要素。</p>")
            + "</div>"
        )

    # -- 图表画廊 -- #
    shots = []
    for f in result.figure_files:
        p = Path(f)
        rel = f"figures/{p.name}"
        if result.report_path:
            try:
                rel = p.resolve().relative_to(Path(result.report_path).resolve().parent).as_posix()
            except ValueError:
                rel = f"figures/{p.name}"
        note = ""
        spec_file = p.with_suffix(".spec.json")
        if spec_file.exists():
            try:
                spec = json.loads(spec_file.read_text(encoding="utf-8"))
                note = spec.get("note", "")
            except Exception:
                note = ""
        if not note:
            meta_file = p.parent / "meta.json"
            if meta_file.exists():
                try:
                    bm = json.loads(meta_file.read_text(encoding="utf-8"))
                    if bm.get("state") == "style_only":
                        note = ("同款样式模板（合成示意数据，非原文献数值）· 配色 %s"
                                % " → ".join((bm.get("colorbar") or {}).get("colors_high_to_low") or []))
                    else:
                        note = ("位图反推估算 · 置信度 %s · 非原文献数值"
                                % bm.get("confidence", "-"))
                except Exception:
                    note = ""
        caption = (p.parent.name[:-len("_bitmap")] if p.parent.name.endswith("_bitmap")
                   else p.name)
        shots.append(
            f"<div class='shot{' wide' if _is_wide_image(p) else ''}'>"
            f"<a href='{_h(rel)}' target='_blank'>"
            f"<img src='{_h(rel)}' alt='{_h(caption)}'/></a>"
            f"<div class='cap'>{_h(caption)}{(' · ' + _h(note)) if note else ''}</div></div>"
        )
    gallery = (
        f"<div class='gallery'>{''.join(shots)}</div>" if shots
        else "<p class='muted'>无可用数据重绘图表。</p>"
    )

    guide_html = _guide_html(paper, plan, result)
    method_html = _method_html(s)
    bitmap_block = _bitmap_html(s.get("bitmap_digitization") or [])

    warn_block = ""
    if result.warnings:
        warn_block = (
            "<div class='card'><h2>九、注意事项</h2><div class='warnbox'><ul class='notes'>"
            + "".join(f"<li>{_h(w)}</li>" for w in dict.fromkeys(result.warnings))
            + "</ul></div></div>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>复现报告 · {_h(paper.title or paper.id)}</title>
<style>{_H_CSS}</style></head>
<body><div class="wrap">

<div class="hero">
  <div class="kicker">Paper2Code · 论文复现报告</div>
  <h1>{_h(paper.title or paper.id)}</h1>
  <div class="pills">
    <span class="pill">{_h(s.get('mode_label'))}</span>
    <span class="pill">判定置信度 {_h(dec.get('confidence'))}</span>
    <span class="pill">校验结论 {_h(checks.get('verdict'))}</span>
    <span class="pill">论文 ID {_h(paper.id)}</span>
  </div>
  <div class="reason">{_h(dec.get('reason'))}</div>
</div>

<div class="card">
  <h2>总体校验结果</h2>
  <div class="kpi">
    {''.join(f"<div class='box'><b>{_h(v)}</b><span>{_h(k)}</span></div>" for v, k in kpi)}
  </div>
</div>

<div class="card">
  <h2>一、复现策略</h2>
  <div class="strategy">{_h(plan.strategy)}</div>
  <h3>执行步骤</h3>
  <ol class="steps">{''.join(f'<li>{_h(x)}</li>' for x in plan.steps)}</ol>
</div>

<div class="card">
  <h2>二、复现操作手册（照着做即可）</h2>
  {guide_html}
</div>

<div class="card">
  <h2>三、复现目标清单</h2>
  <table><thead><tr><th>类型</th><th>ID</th><th>说明</th><th class="num">规模</th><th>角色</th></tr></thead>
  <tbody>{''.join(trows) or '<tr><td colspan="5" class="muted">无</td></tr>'}</tbody></table>
</div>

<div class="card">
  <h2>四、数据输入</h2>
  <table><thead><tr><th>表</th><th class="num">行数</th><th class="num">列数</th><th>来源</th><th>数值列</th><th>截断</th></tr></thead>
  <tbody>{''.join(drows) or '<tr><td colspan="6" class="muted">未抽取到表格数据</td></tr>'}</tbody></table>
</div>

{mode_block}

<div class="card">
  <h2>六、校验与对标明细</h2>
  <table><thead><tr><th>状态</th><th>检查项</th><th>范围</th><th>说明</th></tr></thead>
  <tbody>{''.join(crows) or '<tr><td colspan="4" class="muted">无可执行的校验项</td></tr>'}</tbody></table>
</div>

<div class="card">
  <h2>七、图表重绘</h2>
  {gallery}
</div>

{bitmap_block}

<div class="card">
  <h2>八、规模约束与扩展性</h2>
  <ul class="notes">{''.join(f'<li>{_h(x)}</li>' for x in plan.scale_notes)}</ul>
</div>

{warn_block}

{method_html}

<footer>本报告由 paper2code 自动生成 · 数值对标阈值：pass ≤ {get_settings().rel_tolerance:.0%}，warn ≤ {get_settings().rel_warn:.0%}</footer>
</div></body></html>
"""
_STATUS_ICON = {"pass": "✅ pass", "warn": "⚠️ warn", "fail": "❌ fail", "skipped": "⏭️ skipped"}


def render_guide(paper: Paper, plan, result: ReproResult) -> str:
    """生成「照着做即可」的分步复现操作手册。

    目标是家具安装说明书那种颗粒度：每一步都写清 **做什么 / 怎么做 / 成功的标志 /
    失败先查什么**，让刚接手这个课题的人不必再问"接下来该干嘛"。
    """
    s = result.summary
    out_rel = f"{paper.id}/reproduce"
    st = get_settings()
    p_thr, w_thr = st.rel_tolerance, st.rel_warn
    L: List[str] = []
    a = L.append

    if plan.mode == MODE_OPEN_SOURCE:
        env_note = "情形 A 需要仓库声明的第三方依赖（见 `repo_manifest.json` 的依赖清单），首次可能要联网安装"
        time_note = "首次跑仓库约 1–10 分钟，取决于仓库规模"
    elif plan.mode == MODE_PSEUDOCODE:
        env_note = "本情形只用 Python 标准库，不需要联网"
        time_note = "秒级"
    else:
        env_note = "本情形只用标准库；画图另需 matplotlib（缺失时自动降级为内置 SVG 渲染）"
        time_note = "秒级"

    a("> 读法：每一步都写清了「做什么 / 怎么做 / 成功的标志 / 失败先查什么」。"
      "按顺序做完，第一次接触这个课题的人也能独立复现。")
    a("")
    a("### 第 0 步 · 开跑前先备好这些")
    a("")
    a(f"- [ ] 论文原文：`{paper.id}/paper.md`（原始 PDF 也可以）")
    a(f"- [ ] 运行环境：Python 3.9 及以上；{env_note}")
    a(f"- [ ] 产出目录：`{out_rel}/` —— 下面所有相对路径都以它为起点")
    a(f"- [ ] 时间预期：{time_note}")
    a("")

    if plan.mode == MODE_OPEN_SOURCE:
        a("### 第 1 步 · 先看清仓库里有什么（这一步不执行任何代码）")
        a("")
        a(f"- **做什么**：打开 `{out_rel}/repo_manifest.json`，确认四件事都不是空的——"
          "入口脚本（entry）、依赖清单（requirements）、数据文件（data_files）、结果文件（result_files）。")
        a("- **成功的标志**：入口脚本与结果文件名都列了出来，说明这个仓库可复用。")
        a("- **失败先查**：若报告里 `repo_available` 为否，是没自动拿到仓库；"
          f"手动 `git clone` 到 `{out_rel}/repo/` 后再重跑一次即可。")
        a("")
        a("### 第 2 步 · 装依赖并跑仓库（隔离环境 + 固定种子）")
        a("")
        a(f"- **做什么**：执行 `{out_rel}/repro_run.sh`。它会在隔离 venv 里装依赖、并用固定随机种子跑一遍。")
        a("```bash")
        a(f"bash {out_rel}/repro_run.sh        # Windows 下按脚本内注释逐条执行")
        a("```")
        a("- **成功的标志**：脚本跑完，目录里出现 `results/*.csv` —— 这就是待对标的产物。")
        a("- **失败先查**：①依赖装不上 / 版本漂移 → 按 `repo_manifest.json` 锁版本；"
          "②下载超时 → 本工具默认断网，确需联网时手动放行。")
        a("")
        a("### 第 3 步 · 把产物与论文表格逐格对标")
        a("")
        a("- **做什么**：重跑一次复现环节（`python -m paper2code run <论文>`），"
          "或直接读本报告「校验与对标明细」。")
        a("- **对齐规则**：按「首列键 + 同名列」，把仓库 CSV 的每一行对到论文表的每一行。")
        a("- **成功的标志**：明细表里出现 pass / warn / fail 条目，而不是空的。")
        a("- **失败先查**：列名对不上 → 核对 `repo_result_files` 与论文表头是否同义不同名；"
          "行对不上 → 确认首列是不是同一把主键。")
        a("")
        a("### 第 4 步 · 读结论与判定口径")
        a("")
        a(f"- 相对误差 **≤{p_thr:.0%} 记 pass**、**≤{w_thr:.0%} 记 warn**、超过记 fail。")
        a("- 结论要连着「判定依据」和 warnings 一起看：只看通过率、不看覆盖面，是最常见的误读。")
        a("")

    elif plan.mode == MODE_PSEUDOCODE:
        aid = (s.get("algorithms") or [{}])[0].get("id", "A1")
        a("### 第 1 步 · 核对翻译产物对不对")
        a("")
        a(f"- **做什么**：打开 `{out_rel}/translated_{aid}.py`，对照论文里的 Algorithm {aid} 逐行读一遍。")
        a("- **成功的标志**：函数名、输入、输出与伪代码一致，且每步都有注释标出对应的伪代码行。")
        a("- **失败先查**：函数签名对不上 → 看「伪代码翻译与执行」里的 `input_spec`（参数是怎么绑定的）。")
        a("")
        a("### 第 2 步 · 自己把它跑起来")
        a("")
        a(f"- **做什么**：直接执行翻译产物（工作副本在 `{out_rel}/work/{aid}/{aid}_main.py`）。")
        a("```bash")
        a(f"python {out_rel}/translated_{aid}.py")
        a("```")
        a("- **成功的标志**：终端打印出一串结果，与报告里的「输出预览」一致。")
        a("- **失败先查**：报 `NameError / IndentationError` → 说明该行没被规则翻译器覆盖，见第 4 步。")
        a("")
        a("### 第 3 步 · 看算法性质校验")
        a("")
        a("- **做什么**：读「校验与对标明细」。本情形校验的是算法输出的**数学性质**"
          "（如 Top-K 与全量排序一致、结果有序、归一化守恒）。")
        a("- **成功的标志**：性质校验为 pass。")
        a("- **失败先查**：边界条件——`k` 大于流长、空输入、重复值并列，是最容易漏测的地方。")
        a("")
        a("### 第 4 步 · 如实对待「没翻过来的行」")
        a("")
        a("- **做什么**：若报告里出现 `untranslated_lines`（被注释掉的伪代码行），逐条人工补译后再跑。")
        a("- **注意**：补译后再跑通，**不等于原算法通过**——报告会如实标注翻译覆盖率，"
          "请不要把它当成论文指标复现。")
        a("")
        a("### 第 5 步 · 结论口径")
        a("")
        a("- 本情形是**逻辑复现**：只证明算法行为等价，**不声称复现了论文指标**"
          "（指标还依赖数据集、超参与硬件）。")
        a("")

    else:
        tids = [t["id"] for t in plan.targets if t.get("kind") == "table"]
        tid = tids[0] if tids else "T1"
        a("### 第 1 步 · 核对输入数据与论文表是否一致")
        a("")
        a(f"- **做什么**：打开 `{out_rel}/data/{tid}.csv`，逐列比对表头、逐行核对数字。")
        a("- **成功的标志**：表头、行数、量级与论文表一致（本工具只把表整理成 tidy 数据，没有改数）。")
        a("- **失败先查**：多级表头、单位混写（nM vs µM）、删失值（`>100`）最容易抽错。")
        a("")
        a("### 第 2 步 · 跑内部一致性校验")
        a("")
        a("- **做什么**：重跑 `python -m paper2code run <论文>`，或直接读「校验与对标明细」。")
        a("- 校验的是表格**内部自洽**：占比列求和是否为 100%、汇总行是否等于明细聚合、"
          "标称最优是否确实取到极值、排名是否合法排列、计数是否非负。")
        a("- **成功的标志**：verdict 为 consistent 且 fail 为 0。")
        a("- **失败先查**：若某列被当成占比而它其实是命中率 → 这是假阳性防护要拦的，核对表头措辞即可。")
        a("")
        a("### 第 3 步 · 看重绘的图")
        a("")
        a(f"- **做什么**：打开 `{out_rel}/figures/*.png`，与论文原图比趋势、量纲、坐标轴。")
        a("- **成功的标志**：趋势一致即可（本情形不重跑实验，绝对数值不要求一致）。")
        a("")
        a("### 第 4 步 · 对待「缺失要素」")
        a("")
        a("- **做什么**：按报告列出的缺失要素清单（数据集、超参、种子、预处理、硬件）逐项补齐来源。")
        a("- **注意**：这些要素论文没披露，就不能声称「复现了结论」，只能声称「数据自洽 + 图趋势一致」。")
        a("")

    csum = s.get("checks", {})
    a("### 收尾 · 怎么判断这次复现「算完成」")
    a("")
    a(f"- **结论一句话**：报告「校验与对标明细」首行的 verdict 当前是 **{csum.get('verdict')}**"
      f"（pass {csum.get('pass', 0)} / warn {csum.get('warn', 0)} / "
      f"fail {csum.get('fail', 0)} / skipped {csum.get('skipped', 0)}）。")
    a("- **完成的定义**：每一格都有明确状态（pass/warn/fail），而不是「没有 fail 就叫完成」；"
      "`skipped` 不等于通过，必须逐条看它跳过的原因。")
    a("- **产物自查清单**：")
    a("- [ ] 报告：`repro_report.md` / `repro_report.html`")
    a("- [ ] 计划：`repro_plan.json`（纯数据，可直接交给别的执行器）")
    a("- [ ] 结果：`repro_result.json`")
    if plan.mode == MODE_PSEUDOCODE:
        a("- [ ] 翻译产物：`translated_*.py`")
    if result.table_csv:
        a("- [ ] tidy 数据：`data/*.csv`")
    if result.figure_files:
        a("- [ ] 重绘图：`figures/*.png|svg`")
    a("")
    return "\n".join(L)


def _guide_html(paper: Paper, plan, result: ReproResult) -> str:
    """把操作手册（Markdown）转成报告用的 HTML 片段。"""
    import re as _re

    parts: List[str] = []
    ul_open = False

    def close_ul() -> None:
        nonlocal ul_open
        if ul_open:
            parts.append("</ul>")
            ul_open = False

    for ln in render_guide(paper, plan, result).split("\n"):
        if ln.startswith("```"):
            continue
        if ln.startswith("### "):
            close_ul()
            parts.append("<h3>%s</h3>" % _h(ln[4:]))
        elif ln.startswith("> "):
            close_ul()
            parts.append("<p class='muted'>%s</p>" % _h(ln[2:]))
        elif ln.startswith("- "):
            item = ln[2:]
            if item.startswith("[ ] "):
                item = "☐ " + item[4:]
            if not ul_open:
                parts.append("<ul class='notes'>")
                ul_open = True
            parts.append("<li>%s</li>" % _h(item))
        elif ln.strip():
            close_ul()
            parts.append("<p>%s</p>" % _h(ln))
    close_ul()
    return _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", "\n".join(parts))


def _method_html(s: Dict[str, Any]) -> str:
    """附录 A：论文方法学自动抽取（数据来源 / 筛选标准 / 流程）。"""
    flow = s.get("workflow") or {}
    steps = flow.get("steps") or []
    crit = s.get("selection_criteria") or []
    src = s.get("data_sources") or []
    out = ["<div class='card'><h2>附录 A · 论文方法学自动抽取</h2>",
           "<p class='muted'>完整说明书见同目录 <code>TECH_GUIDE.md</code>。以下为**确定性抽取**结果"
           "（正则 + 章节结构），每条都附正文出处；官方链接为登记值，与论文引用不一致时以论文为准。</p>"]
    if steps:
        out.append("<h3>方法流程（抽取来源：%s）</h3><ol class='steps'>%s</ol>" % (
            _h(flow.get("source")), "".join("<li><b>%s</b></li>" % _h(x["name"]) for x in steps)))
        if flow.get("note"):
            out.append("<p class='muted'>%s</p>" % _h(flow["note"]))
    else:
        out.append("<p class='muted'>方法流程：%s</p>" % _h(flow.get("note") or "未能自动抽取"))
    if src:
        out.append("<h3>数据来源与官方入口</h3><table><thead><tr><th>资源</th><th>官方入口</th>"
                   "<th>正文出处</th></tr></thead><tbody>"
                   + "".join("<tr><td>%s</td><td><a href='%s' target='_blank'>%s</a></td><td>%s</td></tr>"
                             % (_h(x["name"]), _h(x["url"]), _h(x["url"]), _h(x.get("evidence") or "—"))
                             for x in src)
                   + "</tbody></table>")
    if crit:
        out.append("<h3>关键筛选标准（论文明确写出的）</h3>")
        for c in crit:
            out.append("<p><b>%s</b>：%s<br><span class='muted'>论文原句：%s%s</span></p>"
                       % (_h(c["name"]), _h(c["note"]), _h(c["quote"]),
                          ("（出处：%s）" % _h(c["section"])) if c.get("section") else ""))
    out.append("</div>")
    return "".join(out)


def render_report(paper: Paper, plan, result: ReproResult) -> str:
    s = result.summary
    dec = s.get("mode_decision", {})
    lines: List[str] = []
    add = lines.append

    add(f"# 复现报告：{paper.title or paper.id}")
    add("")
    add(f"- 论文 ID：`{paper.id}`")
    add(f"- 情形判定：**{s.get('mode_label')}**（置信度 {dec.get('confidence')}）")
    add(f"- 判定依据：{dec.get('reason')}")
    for e in dec.get("evidence", []):
        add(f"  - {e}")
    add("")

    add("## 一、复现策略")
    add("")
    add(plan.strategy)
    add("")
    add("### 执行步骤")
    add("")
    for i, st in enumerate(plan.steps, start=1):
        add(f"{i}. {st}")
    add("")

    add("## 二、复现操作手册（照着做即可）")
    add("")
    lines.extend(render_guide(paper, plan, result).split("\n"))
    add("")

    add("## 三、复现目标清单")
    add("")
    add("| 类型 | ID | 说明 | 规模 | 角色 |")
    add("| --- | --- | --- | --- | --- |")
    for t in plan.targets[:40]:
        size = (
            f"{t.get('n_rows')}×{t.get('n_cols')}" if t["kind"] == "table"
            else (f"{t.get('n_lines')} 行" if t["kind"] == "algorithm" else "-")
        )
        add(f"| {t['kind']} | {t['id']} | {(t.get('caption') or '').replace('|', '/')[:60]} | {size} | {t.get('role','')} |")
    add("")

    add("## 四、数据输入")
    add("")
    ds = s.get("datasets", {})
    if ds:
        add("| 表 | 行数 | 列数 | 来源 | 数值列 | 截断 |")
        add("| --- | --- | --- | --- | --- | --- |")
        for tid, info in ds.items():
            add(
                f"| {tid} | {info['n_rows']} | {info['n_cols']} | {info['source']} | "
                f"{', '.join(info['numeric_columns'][:6]) or '-'} | {'是' if info['truncated'] else '否'} |"
            )
    else:
        add("_未抽取到表格数据。_")
    add("")

    # -- 情形专属 --------------------------------------------------------- #
    if plan.mode == MODE_OPEN_SOURCE:
        add("## 五、仓库复用结果")
        add("")
        add(f"- 仓库可用：{'是' if s.get('repo_available') else '否'}")
        if s.get("repo_status_label"):
            add(f"- 仓库定位：{s['repo_status_label']}")
        cands = s.get("repo_candidates") or []
        if cands:
            add("- 候选仓库：" + "；".join(
                "%s（%s）" % (c.get("url"), c.get("role") or "unclear") for c in cands[:5]))
        if s.get("local_code_path"):
            add(f"- 本地代码目录：`{s['local_code_path']}`")
        if s.get("repo_data_files"):
            add(f"- 仓库数据文件（前若干）：{', '.join(s['repo_data_files'][:8])}")
        if s.get("repo_result_files"):
            add(f"- 采集到的结果文件：{', '.join(Path(p).name for p in s['repo_result_files'][:8])}")
        add(f"- 复现脚本：`{Path(s.get('repro_run_script','')).name}`（隔离 venv + 固定种子）")
        sr = s.get("smoke_result") or {}
        if sr:
            add(f"- smoke 运行：{sr.get('status_label') or sr.get('status')}")
            if sr.get("entrypoint"):
                add(f"  - 入口：`{sr['entrypoint']}`")
            if sr.get("reason"):
                add(f"  - 原因：{str(sr['reason'])[:300]}")
            attempts = s.get("smoke_attempts") or []
            if len(attempts) > 1:
                add("- 入口尝试：" + "；".join(
                    "%s → %s" % (a.get("entrypoint") or "（无）", a.get("status"))
                    for a in attempts))
        env = s.get("env_install") or {}
        if env:
            env_label = {
                "installed": "已在隔离 venv 安装", "reused": "复用已有隔离 venv",
                "not_enabled": "未开启自动安装（仅生成脚本）",
                "heavy_blocked": "重型依赖被拦截", "failed": "安装失败",
                "no_deps": "无需安装",
            }.get(env.get("status"), env.get("status") or "—")
            add(f"- 依赖环境：{env_label}")
            scripts = env.get("scripts") or {}
            if scripts:
                add(f"  - 安装脚本：`{Path(scripts.get('ps1','')).name}` / `{Path(scripts.get('sh','')).name}`")
            if env.get("log"):
                add(f"  - 安装日志：`{Path(env['log']).name}`")
        add("")

    elif plan.mode == MODE_PSEUDOCODE:
        add("## 五、伪代码翻译与执行")
        add("")
        for a in s.get("algorithms", []):
            add(f"### 算法 {a.get('id')}：{a.get('caption','')}")
            add("")
            add(f"- 识别意图：`{a.get('intent')}`")
            add(f"- 执行状态：**{a.get('status')}**")
            add(f"- 函数名：`{a.get('fn_name')}`，输入规格：`{json.dumps(a.get('input_spec', {}), ensure_ascii=False)}`")
            if a.get("translated_artifact"):
                add(f"- 翻译产物：`{Path(a['translated_artifact']).name}`")
            if a.get("output_preview"):
                add(f"- 输出预览：`{a['output_preview']}`")
            if a.get("error"):
                add(f"- 错误：`{a['error'][:300]}`")
            for pc in a.get("property_checks", []):
                add(f"  - {_STATUS_ICON.get(pc['status'], pc['status'])} **{pc['name']}**：{pc['detail']}")
            add("")

    elif plan.mode == MODE_NO_CODE:
        add("## 五、无法复现要素清单")
        add("")
        missing = s.get("missing_elements", [])
        if missing:
            for m in missing:
                add(f"- {m}")
        else:
            add("- 未发现明显缺失要素。")
        add("")

    # -- 校验明细 --------------------------------------------------------- #
    add("## 六、校验与对标明细")
    add("")
    csum = s.get("checks", {})
    add(
        f"结论：**{csum.get('verdict')}** ｜ pass {csum.get('pass',0)} / warn {csum.get('warn',0)} / "
        f"fail {csum.get('fail',0)} / skipped {csum.get('skipped',0)} ｜ 得分 {csum.get('score')}"
    )
    add("")
    if result.deltas:
        add("| 状态 | 检查项 | 范围 | 说明 |")
        add("| --- | --- | --- | --- |")
        for d in result.deltas[:60]:
            add(
                f"| {_STATUS_ICON.get(d['status'], d['status'])} | {d['name']} | `{d['scope']}` | "
                f"{str(d['detail']).replace('|', '/')[:120]} |"
            )
    else:
        add("_无可执行的校验项。_")
    add("")

    # -- 图表 ------------------------------------------------------------- #
    add("## 七、图表重绘")
    add("")
    if result.figure_files:
        for f in result.figure_files:
            add(f"- `{Path(f).name}`")
    else:
        add("_无可用数据重绘图表。_")
    add("")
    for line in _bitmap_markdown(s.get("bitmap_digitization") or []):
        add(line)

    # -- 规模与扩展 ------------------------------------------------------- #
    add("## 八、规模约束与扩展性")
    add("")
    for n in plan.scale_notes:
        add(f"- {n}")
    add("")

    # -- 附录 A：论文方法学（自动抽取） ------------------------------------ #
    add("## 附录 A · 论文方法学自动抽取")
    add("")
    add("- 完整说明书：`TECH_GUIDE.md`（与本报告同目录；确定性抽取，每条附正文出处）")
    flow = s.get("workflow") or {}
    if flow.get("steps"):
        add(f"- 方法流程（来源：{flow.get('source')}）："
            + " → ".join(f"**{x['name']}**" for x in flow["steps"]))
        if flow.get("note"):
            add(f"  - 注意：{flow['note']}")
    else:
        add(f"- 方法流程：{flow.get('note') or '未能自动抽取'}")
    ds = s.get("data_sources") or []
    if ds:
        add("- 数据来源与官方入口（链接为登记值，与论文引用不一致时以论文为准）：")
        for x in ds:
            add(f"  - [{x['name']}]({x['url']})" + (f" —— 出处：{x['evidence'][:100]}" if x.get("evidence") else ""))
    for c in (s.get("selection_criteria") or []):
        add(f"- **{c['name']}**：{c['note']}（论文原句：{c['quote'][:120]}）")
    add("")

    if result.warnings:
        add("## 九、注意事项")
        add("")
        for w in dict.fromkeys(result.warnings):
            add(f"- {w}")
        add("")

    add("---")
    add("")
    add("_本报告由 paper2code 自动生成。数值对标使用相对误差阈值 "
        f"pass ≤ {get_settings().rel_tolerance:.0%}、warn ≤ {get_settings().rel_warn:.0%}。_")
    return "\n".join(lines)
