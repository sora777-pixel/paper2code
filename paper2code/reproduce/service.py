"""复现编排：按三种情形分别执行，输出复现产物与报告。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import get_settings
from ..llm import LLMProvider, get_provider
from ..models import MODE_NO_CODE, MODE_OPEN_SOURCE, MODE_PSEUDOCODE, Paper, ReproResult
from . import charting, checks, classifier, compare, dataset, planner, pseudo2code, repo, runner


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

    # -- 6. 汇总 ---------------------------------------------------------- #
    summary = {
        "mode": decision.mode,
        "mode_label": classifier.mode_label(decision.mode),
        "mode_decision": decision.to_dict(),
        "datasets": {tid: i.to_dict() for tid, i in infos.items()},
        "checks": checks.summarize_checks([_to_check(d) for d in deltas]),
        "deltas_summary": compare.summarize_deltas([_to_check(d) for d in deltas]),
        "figures_redrawn": len(figure_files),
        **extra,
    }

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
def _run_open_source(paper: Paper, out_dir: Path, infos: Dict[str, dataset.DatasetInfo], decision) -> tuple:
    warnings: List[str] = []
    extra: Dict[str, Any] = {}
    deltas: List[Dict[str, Any]] = []

    local = classifier.detect_local_repo(paper)
    root = Path(local) if local else None

    if root is None and paper.repo_urls():
        url = paper.repo_urls()[0]
        dest = out_dir / "repo"
        clone = repo.try_clone(url, dest)
        extra["clone"] = clone
        if clone.get("ok"):
            root = Path(clone["path"])
        else:
            warnings.append(f"无法自动获取仓库（{clone.get('error', '未知原因')}），已输出待执行脚本。")
    elif root is None:
        warnings.append("未定位到本地仓库目录，已输出待执行脚本。")

    manifest = repo.introspect(root) if root else repo.RepoManifest(
        root="", url=(paper.repo_urls() or [""])[0], notes=["仓库不可用"]
    )
    manifest.url = manifest.url or (paper.repo_urls() or [""])[0]

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
    return deltas, extra, warnings


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
            ("本地代码目录", f"<code>{_h(s.get('local_code_path') or '—')}</code>"),
            ("仓库数据文件", _h(", ".join(s.get("repo_data_files", [])[:8]) or "—")),
            ("采集到的结果文件", _h(", ".join(Path(p).name for p in s.get("repo_result_files", [])[:8]) or "—")),
            ("复现脚本", f"<code>{_h(Path(s.get('repro_run_script', '') or '').name)}</code>"),
        ]
        mode_block = (
            "<div class='card'><h2>四、仓库复用结果</h2><table><tbody>"
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
        mode_block = "<div class='card'><h2>四、伪代码翻译与执行</h2>" + "".join(blocks) + (
            "<p class='muted' style='margin-top:14px'>本情形为<b>逻辑复现</b>：只校验算法输出的数学性质，"
            "未对标论文指标数值。</p></div>"
        )
    else:
        missing = s.get("missing_elements", [])
        mode_block = (
            "<div class='card'><h2>四、无法复现要素清单</h2>"
            + ("<ul class='notes'>" + "".join(f"<li>{_h(m)}</li>" for m in missing) + "</ul>"
               if missing else "<p class='muted'>未发现明显缺失要素。</p>")
            + "</div>"
        )

    # -- 图表画廊 -- #
    shots = []
    for f in result.figure_files:
        p = Path(f)
        rel = f"figures/{p.name}"
        note = ""
        spec_file = p.with_suffix(".spec.json")
        if spec_file.exists():
            try:
                spec = json.loads(spec_file.read_text(encoding="utf-8"))
                note = spec.get("note", "")
            except Exception:
                note = ""
        shots.append(
            f"<div class='shot'><img src='{_h(rel)}' alt='{_h(p.name)}'/>"
            f"<div class='cap'>{_h(p.name)}{(' · ' + _h(note)) if note else ''}</div></div>"
        )
    gallery = (
        f"<div class='gallery'>{''.join(shots)}</div>" if shots
        else "<p class='muted'>无可用数据重绘图表。</p>"
    )

    warn_block = ""
    if result.warnings:
        warn_block = (
            "<div class='card'><h2>八、注意事项</h2><div class='warnbox'><ul class='notes'>"
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
  <h2>二、复现目标清单</h2>
  <table><thead><tr><th>类型</th><th>ID</th><th>说明</th><th class="num">规模</th><th>角色</th></tr></thead>
  <tbody>{''.join(trows) or '<tr><td colspan="5" class="muted">无</td></tr>'}</tbody></table>
</div>

<div class="card">
  <h2>三、数据输入</h2>
  <table><thead><tr><th>表</th><th class="num">行数</th><th class="num">列数</th><th>来源</th><th>数值列</th><th>截断</th></tr></thead>
  <tbody>{''.join(drows) or '<tr><td colspan="6" class="muted">未抽取到表格数据</td></tr>'}</tbody></table>
</div>

{mode_block}

<div class="card">
  <h2>五、校验与对标明细</h2>
  <table><thead><tr><th>状态</th><th>检查项</th><th>范围</th><th>说明</th></tr></thead>
  <tbody>{''.join(crows) or '<tr><td colspan="4" class="muted">无可执行的校验项</td></tr>'}</tbody></table>
</div>

<div class="card">
  <h2>六、图表重绘</h2>
  {gallery}
</div>

<div class="card">
  <h2>七、规模约束与扩展性</h2>
  <ul class="notes">{''.join(f'<li>{_h(x)}</li>' for x in plan.scale_notes)}</ul>
</div>

{warn_block}

<footer>本报告由 paper2code 自动生成 · 数值对标阈值：pass ≤ {get_settings().rel_tolerance:.0%}，warn ≤ {get_settings().rel_warn:.0%}</footer>
</div></body></html>
"""
_STATUS_ICON = {"pass": "✅ pass", "warn": "⚠️ warn", "fail": "❌ fail", "skipped": "⏭️ skipped"}


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

    add("## 二、复现目标清单")
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

    add("## 三、数据输入")
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
        add("## 四、仓库复用结果")
        add("")
        add(f"- 仓库可用：{'是' if s.get('repo_available') else '否'}")
        if s.get("local_code_path"):
            add(f"- 本地代码目录：`{s['local_code_path']}`")
        if s.get("repo_data_files"):
            add(f"- 仓库数据文件（前若干）：{', '.join(s['repo_data_files'][:8])}")
        if s.get("repo_result_files"):
            add(f"- 采集到的结果文件：{', '.join(Path(p).name for p in s['repo_result_files'][:8])}")
        add(f"- 复现脚本：`{Path(s.get('repro_run_script','')).name}`（隔离 venv + 固定种子）")
        add("")

    elif plan.mode == MODE_PSEUDOCODE:
        add("## 四、伪代码翻译与执行")
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
        add("## 四、无法复现要素清单")
        add("")
        missing = s.get("missing_elements", [])
        if missing:
            for m in missing:
                add(f"- {m}")
        else:
            add("- 未发现明显缺失要素。")
        add("")

    # -- 校验明细 --------------------------------------------------------- #
    add("## 五、校验与对标明细")
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
    add("## 六、图表重绘")
    add("")
    if result.figure_files:
        for f in result.figure_files:
            add(f"- `{Path(f).name}`")
    else:
        add("_无可用数据重绘图表。_")
    add("")

    # -- 规模与扩展 ------------------------------------------------------- #
    add("## 七、规模约束与扩展性")
    add("")
    for n in plan.scale_notes:
        add(f"- {n}")
    add("")

    if result.warnings:
        add("## 八、注意事项")
        add("")
        for w in dict.fromkeys(result.warnings):
            add(f"- {w}")
        add("")

    add("---")
    add("")
    add("_本报告由 paper2code 自动生成。数值对标使用相对误差阈值 "
        f"pass ≤ {get_settings().rel_tolerance:.0%}、warn ≤ {get_settings().rel_warn:.0%}。_")
    return "\n".join(lines)
