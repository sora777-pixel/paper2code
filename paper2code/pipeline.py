"""端到端编排：一篇论文进来，两类产出出去。"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import control, plugins
from .config import get_settings
from .explain import build_podcast, render_courseware
from .ingest import figassets, load_paper
from .llm import LLMProvider, get_provider
from .models import ExplainBundle, Paper
from .progress import RunState
from .reproduce import reproduce_paper

EXPLAIN_COURSEWARE = "courseware"
EXPLAIN_PODCAST = "podcast"


def run_paper(
    source: str | Path,
    output_dir: Optional[Path] = None,
    explain: Sequence[str] = (EXPLAIN_COURSEWARE, EXPLAIN_PODCAST),
    reproduce: bool = True,
    provider: Optional[LLMProvider] = None,
    run_pseudocode: bool = True,
    audio: bool = True,
    mvp: Optional[bool] = None,  # MVP-PATCH:pipeline-sig（None=按 settings.mvp_enabled）
    resume: bool = False,        # M1-⑤：跳过「上次已完成且产物仍在」的阶段
    on_progress: Optional[Any] = None,  # M5-①：阶段快照回调（队列任务用来回写进度）
) -> Dict[str, Any]:
    """处理单篇论文，返回 manifest 字典。

    `resume=True` 时按阶段缓存续跑：只跳过**产物仍然存在**的阶段，并把跳过的事实
    写进 `manifest.warnings`（绝不静默复用陈旧结果）。

    **取消与超时（M5-①）**：每个阶段边界都调一次 :func:`paper2code.control.check`——
    队列任务被取消或超时就在这里抛 `RunCancelled`，不会把半途的产物当成成功结果。
    """
    settings = get_settings().ensure_dirs()
    provider = provider or get_provider()
    provider.reset_run()         # 每篇论文独立记账/降级状态（批量复用同一 Provider）
    plugins.ensure_loaded()      # M5-④：插件只需加载一次（坏了不影响主流程）
    control.check()

    paper = load_paper(source)
    # 产物目录**一律解析成绝对路径**：情形 A 的 smoke 会用「仓库根目录」当 cwd 执行，
    # 若这里留着相对路径，它会被重新解释到样本源目录里去（实测把
    # samples/<pkg>/code/outputs/<名字>/ 写了出来，既污染样本、又让 golden 测试读到脏数据）。
    out_root = (Path(output_dir) if output_dir else Path(settings.output_dir)).resolve()
    pdir = out_root / paper.id
    pdir.mkdir(parents=True, exist_ok=True)

    state = RunState(pdir / "progress.json", paper_id=paper.id, resume=resume,
                     on_change=on_progress).load_existing()
    state.save()
    # M2-2：把 PDF 位图落盘并与图题配对。图资产是课件插图、FIGURES.md 的「原图」栏
    # 与 digitize 取点的共同输入，必须在写 paper.json 之前完成，否则 image_path 恒空。
    figassets.export_for_paper(paper, pdir / "figures")
    prev_manifest: Dict[str, Any] = {}
    if resume:
        try:
            prev_manifest = json.loads((pdir / "manifest.json").read_text(encoding="utf-8"))
        except (ValueError, OSError):
            prev_manifest = {}

    (pdir / "paper.json").write_text(paper.to_json(), encoding="utf-8")
    (pdir / "paper.md").write_text(_paper_to_markdown(paper), encoding="utf-8")
    attachments = paper.meta.get("pdf_attachments") or []
    if attachments:
        apath = pdir / "attachments.json"
        apath.write_text(json.dumps(attachments, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest: Dict[str, Any] = {
        "paper_id": paper.id,
        "title": paper.title,
        "authors": paper.authors,
        "source": str(source),
        "source_kind": paper.source_kind,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "provider": provider.describe(),
        "explain_mode": list(explain),
        "artifacts": {},
        "stats": {
            "sections": len(paper.sections),
            "tables": len(paper.tables),
            "table_strategy": paper.meta.get("table_strategy"),
            "figures": len(paper.figures),
            "algorithms": len(paper.algorithms),
            "code_refs": len(paper.code_refs),
            "provided_data": len(paper.meta.get("provided_data", []) or []),
            "parse_quality": (paper.meta.get("parse_quality") or {}).get("score"),
            "parse_quality_level": (paper.meta.get("parse_quality") or {}).get("level"),
            "parse_quality_failed": (paper.meta.get("parse_quality") or {}).get("failed", []),
        },
        "warnings": list(paper.meta.get("warnings", []) or []),
    }
    if attachments:
        manifest["artifacts"]["attachments"] = {
            "count": len(attachments),
            "file": str(pdir / "attachments.json"),
            "names": [a.get("name", "") for a in attachments],
        }

    # ---------------- M6-③ 数据集（先审后取；没有声明就跳过） ---------------- #
    try:
        data_info = _datasets_stage(paper, pdir, settings)
        if data_info:
            manifest["artifacts"]["datasets"] = data_info
            manifest["warnings"].extend(data_info.get("warnings") or [])
            manifest["warnings"].append("数据集：" + data_info["note"])
    except control.RunCancelled:
        raise
    except Exception as exc:
        manifest["warnings"].append("数据集获取环节失败（不影响其他产物）：%s: %s"
                                    % (type(exc).__name__, exc))

    # ---------------- 讲解材料 ---------------- #
    if explain:
        control.check()
        if state.can_resume("explain"):
            state.mark_reused("explain")
            _reuse_artifacts(manifest, prev_manifest, "explain", pdir)
            _warn_missing_audio(manifest, pdir)
        else:
            state.begin("explain")
            try:
                bundle = _build_explain(paper, pdir / "explain", provider, list(explain), audio=audio)
            except Exception as exc:
                state.fail("explain", "%s: %s" % (type(exc).__name__, exc))
                raise
            manifest["artifacts"]["explain"] = {
                "generator": bundle.generator,
                "outline": bundle.outline,
                "courseware": bundle.courseware_path,
                "podcast_script": bundle.podcast_script_path,
                "podcast_srt": bundle.podcast_srt_path,
                "podcast_audio": bundle.podcast_audio_path,
                "n_slides": len(bundle.slides),
                "n_turns": len(bundle.transcript),
            }
            manifest["warnings"].extend(bundle.warnings)
            state.finish("explain", artifacts=(bundle.courseware_path, bundle.podcast_script_path,
                                               bundle.podcast_audio_path))

    # ---------------- 复现结果 ---------------- #
    if reproduce:
        control.check()
        if state.can_resume("reproduce"):
            state.mark_reused("reproduce")
            _reuse_artifacts(manifest, prev_manifest, "reproduce", pdir)
        else:
            state.begin("reproduce")
            try:
                res = reproduce_paper(paper, pdir / "reproduce", provider=provider, run_pseudocode=run_pseudocode)
            except Exception as exc:
                state.fail("reproduce", "%s: %s" % (type(exc).__name__, exc))
                raise
            manifest["artifacts"]["reproduce"] = {
                "mode": res.mode,
                "mode_label": res.summary.get("mode_label"),
                "ok": res.ok,
                "report": res.report_path,
                "report_html": str(pdir / "reproduce" / "repro_report.html"),
                "plan": str(pdir / "reproduce" / "repro_plan.json"),
                "tables_csv": res.table_csv,
                "figures": res.figure_files,
                "checks": res.summary.get("checks", {}),
                # 论文方法学自动抽取的说明书（数据来源官方链接 / 筛选标准 / 流程）
                "tech_guide": str(pdir / "reproduce" / "TECH_GUIDE.md"),
            }
            manifest["warnings"].extend(res.warnings)
            state.finish("reproduce", artifacts=(res.report_path,))

    # ---------------- MVP demo + 图表绘制说明 ---------------- #  # MVP-PATCH:pipeline-run
    if (settings.mvp_enabled if mvp is None else mvp):
        control.check()
        if state.can_resume("mvp"):
            state.mark_reused("mvp")
            _reuse_artifacts(manifest, prev_manifest, "mvp", pdir)
        else:
            state.begin("mvp")
            try:
                from .mvp import build_mvp

                mres = build_mvp(paper, pdir / "mvp", provider=provider)
                manifest["artifacts"]["mvp"] = {
                    "ok": mres.ok,
                    "generator": mres.generator,
                    "demo_ok": mres.demo_ok,
                    "report": mres.report_path,
                    "figures_md": str(pdir / "mvp" / mres.figures_md) if mres.figures_md else "",
                    "n_figure_specs": len(mres.figure_specs),
                    "n_figures_drawn": sum(1 for v in mres.figures.values() if v.get("files")),
                    # M5-②/③：版本与导出（供 Web 卡片直接给链接）
                    "version": (mres.version or {}).get("current", 1),
                    "notebook": (mres.exported or {}).get("notebook_rel", ""),
                    "streamlit_app": (mres.exported or {}).get("streamlit_rel", ""),
                }
                manifest["warnings"].extend(mres.warnings)
                # 产物路径必须是绝对路径：resume 时靠它判断"产物还在不在"
                state.finish("mvp", artifacts=(mres.report_path,
                                               str(pdir / "mvp" / mres.figures_md) if mres.figures_md else ""))
            except control.RunCancelled:
                state.fail("mvp", "任务被取消或超时")
                raise
            except Exception as exc:  # MVP 失败不能拖垮讲解与复现产物
                state.fail("mvp", "%s: %s" % (type(exc).__name__, exc))
                manifest["warnings"].append(f"MVP 阶段失败（不影响讲解与复现产物）：{type(exc).__name__}: {exc}")

    manifest["warnings"].extend(state.reused_warning())
    # ---------------- M6-① 声明级核验报告 ---------------- #
    try:
        ver = _verification_stage(paper, pdir, manifest, settings)
        if ver:
            manifest["artifacts"]["verification"] = ver
    except control.RunCancelled:
        raise
    except Exception as exc:  # 核验失败不能拖垮主产物
        manifest["warnings"].append("声明级核验报告生成失败（不影响其他产物）：%s: %s"
                                    % (type(exc).__name__, exc))
    manifest["warnings"] = list(dict.fromkeys(w for w in manifest["warnings"] if w))
    # 可搬移的路径副本（绝对路径是内部约定：smoke 会用仓库根当 cwd，相对路径会被错解到
    # 样本源目录；但 manifest 一旦要分享/搬走，绝对路径就没法用，故**双写**一份相对形式）。
    manifest["paths_rel"] = _portable_paths(manifest, pdir, out_root, settings.workspace, source)
    manifest["progress"] = state.snapshot()
    (pdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    state.save()
    _update_index(out_root, manifest)
    return manifest


def _verification_stage(paper: Paper, pdir: Path, manifest: Dict[str, Any],
                        settings: Any) -> Optional[Dict[str, Any]]:
    """M6-①：把「论文每条主张 → 证据 → 状态」写成 `VERIFICATION.md` + `verification.json`。

    证据只从**已经落盘的产物**里读（repro_result.json / mvp_result.json / metrics.json / specs），
    因此核验本身是纯读 + 判定的纯函数，可以随时重算，也不会拖慢主流程。
    """
    from . import verify

    mvp_dir = pdir / "mvp"
    repro_dir = pdir / "reproduce"
    mvp = _read_json(mvp_dir / "mvp_result.json")
    repro = _read_json(repro_dir / "repro_result.json")
    metrics = _read_json(mvp_dir / "demo" / "results" / "metrics.json")
    specs: List[Dict[str, Any]] = []
    scdir = mvp_dir / "figures" / "specs"
    if scdir.exists():
        for p in sorted(scdir.glob("*.json")):
            data = _read_json(p)
            if data:
                specs.append(data)
    if not (mvp or repro or metrics or specs):
        return None      # 什么都没跑过：不生成一份全是「未检验」的空报告

    generator = str(mvp.get("generator") or "")
    echo = generator.startswith("offline-scaffold")
    provenance = str(((metrics or {}).get("meta") or {}).get("data_provenance") or "")
    note = ""
    if not echo and provenance and "论文" in provenance:
        note = "骨架用的是论文自带数值（%s）：趋势可信、数值量级不作为独立验证" % provenance[:80]

    from .mvp.textio import describe_results

    evidence: Dict[str, Any] = {
        "demo_generator": generator,
        "demo_metrics": metrics or None,
        "metric_keys": describe_results(metrics) if metrics else [],
        "echo_of_paper": echo,
        "independence_note": note,
        "demo_claims": (mvp.get("claims") or {}),
        "figure_specs": specs,
        "drawn_figures": {k: (v.get("files") or []) for k, v in (mvp.get("figures") or {}).items()},
        "n_figure_specs": len(specs),
        "cell_deltas": (repro.get("deltas") or []),
        "repo_smoke": ((repro.get("summary") or {}).get("smoke_result") or None),
        "seeds": (((metrics or {}).get("meta") or {}).get("seeds")),
        "counts": {"tables": list(paper.tables), "figures": list(paper.figures),
                   "algorithms": list(paper.algorithms)},
        "thresholds": (settings.rel_tolerance, settings.rel_warn),
    }
    report = verify.build(paper, evidence)
    paths = verify.write_report(report, pdir)
    summary = report.get("summary") or {}
    manifest["warnings"].append("声明级核验：%s（明细见 VERIFICATION.md）" % summary.get("verdict_line", ""))
    return {"markdown": paths["markdown"], "json": paths["json"], **summary}


def _datasets_stage(paper: Paper, pdir: Path, settings: Any) -> Optional[Dict[str, Any]]:
    """M6-③：按 ``configs/datasets.yaml`` 获取数据集（**先审许可、后验校验和**）。

    没有声明就返回 None（不产生任何副作用）；有声明但获取失败也照实记 warning，
    并把 `DATA_SOURCES.json` 留在 ``outputs/single/<id>/data/`` 供审计。
    """
    from . import datasets

    spec_path = Path(str(getattr(settings, "datasets_spec", "") or ""))
    if spec_path and not spec_path.is_absolute():
        spec_path = Path(settings.workspace) / spec_path
    sources = datasets.load_spec(spec_path) if spec_path else []
    if not sources:
        return None
    res = datasets.acquire(sources, pdir / "data",
                           allow_network=bool(settings.allow_network),
                           allow_unknown_license=bool(getattr(settings, "datasets_allow_unknown_license", False)))
    warnings = list(res.get("warnings") or [])
    note = "数据源 %d 个：成功 %d、失败 %d（明细见 DATA_SOURCES.json）" % (
        res["n_ok"] + res["n_failed"], res["n_ok"], res["n_failed"])
    return {"manifest": res["manifest"], "n_ok": res["n_ok"], "n_failed": res["n_failed"],
            "note": note, "warnings": warnings}


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _reuse_artifacts(manifest: Dict[str, Any], prev_manifest: Dict[str, Any], key: str,
                     pdir: Optional[Path] = None) -> None:
    """`--resume` 跳过某阶段时，把上次的产物清单接回来（否则 UI 上的链接会消失）。

    上次的 manifest 可能**根本不存在**（例如上一次运行在写 manifest 之前就被中断了：
    服务热重载、手动重启、崩溃）。这时不能只留一句「链接可能缺失」——按磁盘上真实存在的
    文件重建清单，并把「有哪一项其实没生成」如实写成 warning。
    """
    got = (prev_manifest.get("artifacts") or {}).get(key)
    if not got and pdir is not None:
        got = _artifacts_from_disk(Path(pdir), key)
        if got:
            manifest["warnings"].append(
                "阶段 %s 被 --resume 跳过，且上次 manifest 里没有它的产物清单；已按磁盘上实际存在的"
                "文件重建（缺失项已在下列 warning 中标注）。" % key)
    if got:
        manifest["artifacts"][key] = got
    else:
        manifest["warnings"].append(
            "阶段 %s 被 --resume 跳过，但磁盘上也没找到可复用的产物，相关链接可能缺失。" % key)


def _posix_rel(target: str, base: Path) -> str:
    """把 ``target`` 写成相对 ``base`` 的 POSIX 路径；不在 base 之下就原样返回。"""
    try:
        return Path(str(target)).resolve().relative_to(Path(base).resolve()).as_posix()
    except Exception:
        return str(target)


def _rel_tree(obj: Any, base: Path) -> Any:
    """递归把「落在 base 之下的绝对路径字符串」换成相对路径；其他值原样保留。"""
    if isinstance(obj, str):
        return _posix_rel(obj, base)
    if isinstance(obj, dict):
        return {k: _rel_tree(v, base) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_rel_tree(v, base) for v in obj]
    return obj


def _portable_paths(manifest: Dict[str, Any], pdir: Path, out_root: Path,
                    workspace: Path, source: Any) -> Dict[str, Any]:
    """给 manifest 附一份「相对运行目录 / 相对工作区」的路径副本（便于搬移与分享）。

    绝对路径照旧保留（内部既定约定，见 :func:`_update_index` 与 smoke 的 cwd 问题），
    这里只是**多写一份**：
    ``paths_rel.root`` 是运行目录相对 ``outputs/``；``paths_rel.artifacts`` 与运行目录内
    文件一一对应；``paths_rel.source`` 是输入相对工作区（URL / arXiv 编号等非路径来源原样保留）。
    """
    src = str(source)
    src_rel = _posix_rel(src, workspace) if Path(src).exists() else src
    return {
        "root": _posix_rel(str(pdir), out_root),
        "source": src_rel,
        "artifacts": _rel_tree(manifest.get("artifacts") or {}, pdir),
    }


def _warn_missing_audio(manifest: Dict[str, Any], pdir: Path) -> None:
    """复用 explain 时如果没音频，**必须写明原因**，不能只让链接消失。"""
    ex = manifest["artifacts"].get("explain") or {}
    if ex.get("podcast_audio"):
        return
    msg = ex.pop("_missing_audio_warning", "")
    if msg:
        manifest["warnings"].append(msg)
    else:
        manifest["warnings"].append("阶段 explain 复用的产物里没有音频（podcast_audio 为空）。")


def _artifacts_from_disk(pdir: Path, key: str) -> Dict[str, Any]:
    """按运行目录里**实际存在**的文件重建某个阶段的产物清单（不存在的不写进去）。"""
    def hit(rel: str) -> str:
        p = pdir / rel
        return str(p) if p.exists() else ""

    if key == "explain":
        ex = pdir / "explain"
        audio = ""
        for pat in ("podcast.mp3", "podcast_placeholder.wav", "podcast*.mp3", "podcast*.wav"):
            found = sorted(ex.glob(pat))
            if found:
                audio = str(found[0])
                break
        got: Dict[str, Any] = {
            "generator": "(复用上次产物，未在本次重跑)",
            "courseware": hit("explain/courseware.html"),
            "podcast_script": hit("explain/podcast_script.md"),
            "podcast_srt": hit("explain/podcast.srt"),
            "podcast_audio": audio,
        }
        if not audio:
            got_missing = ("复用阶段 explain 时**没有找到音频产物**（上次运行没生成出 podcast.mp3）。"
                           "常见原因是 edge-tts 的免费朗读接口被服务端拒绝（实测 403 WSServerHandshakeError）；"
                           "重新运行且**不勾「续跑」**会重试合成，届时原因会写进 warnings。")
            got["_missing_audio_warning"] = got_missing
        return got if (got["courseware"] or got["podcast_script"] or audio) else {}

    if key == "reproduce":
        got = {"report": hit("reproduce/repro_report.md"),
               "report_html": hit("reproduce/repro_report.html"),
               "plan": hit("reproduce/repro_plan.json"),
               "tech_guide": hit("reproduce/TECH_GUIDE.md")}
        return got if got["report"] else {}

    if key == "mvp":
        got = {"report": hit("mvp/MVP_REPORT.md"), "figures_md": hit("mvp/figures/FIGURES.md")}
        return got if got["report"] else {}
    return {}


# --------------------------------------------------------------------------- #
def _build_explain(
    paper: Paper,
    out_dir: Path,
    provider: LLMProvider,
    kinds: List[str],
    audio: bool = True,
) -> ExplainBundle:
    out_dir.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    bundle = ExplainBundle(paper_id=paper.id, generator=provider.describe())

    outline = provider.outline(paper, settings.max_slides)
    bundle.outline = outline
    slides = provider.slides(paper, outline)
    bundle.slides = slides

    if EXPLAIN_COURSEWARE in kinds:
        path = render_courseware(paper, slides, out_dir / "courseware.html", generator=provider.describe())
        bundle.courseware_path = path

    if EXPLAIN_PODCAST in kinds:
        turns = provider.dialogue(paper)
        bundle.transcript = turns
        info = build_podcast(paper, turns, out_dir, audio=audio)
        bundle.podcast_script_path = info.get("script")
        bundle.podcast_srt_path = info.get("srt")
        bundle.podcast_audio_path = info.get("audio")
        bundle.warnings.extend(info.get("warnings") or [])

    bundle.warnings.extend(provider.warnings)
    bundle.warnings = list(dict.fromkeys(w for w in bundle.warnings if w))
    return bundle


def _paper_to_markdown(paper: Paper) -> str:
    lines = [f"# {paper.title}", ""]
    if paper.authors:
        lines += ["**作者**：" + ", ".join(paper.authors), ""]
    if paper.abstract:
        lines += ["## 摘要", "", paper.abstract, ""]
    for s in paper.sections:
        if s.kind == "abstract":
            continue
        lines += [f"{'#' * min(6, s.level + 1)} {s.heading}", "", s.text, ""]
    if paper.algorithms:
        lines += ["## 抽取到的算法块", ""]
        for a in paper.algorithms:
            lines += [f"### {a.id}. {a.caption}", "", "```", *a.lines, "```", ""]
    return "\n".join(lines)


def _update_index(out_root: Path, manifest: Dict[str, Any]) -> None:
    index_path = out_root / "index.json"
    data: List[Dict[str, Any]] = []
    if index_path.exists():
        try:
            data = json.loads(index_path.read_text(encoding="utf-8")) or []
        except Exception:
            data = []
    data = [d for d in data if d.get("paper_id") != manifest["paper_id"]]
    data.insert(0, manifest)
    index_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def list_runs(out_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    out_root = Path(out_root) if out_root else get_settings().output_dir
    index_path = out_root / "index.json"
    if not index_path.exists():
        return []
    try:
        return json.loads(index_path.read_text(encoding="utf-8")) or []
    except Exception:
        return []


def delete_run(paper_id: str, out_root: Optional[Path] = None,
               incoming_root: Optional[Path] = None) -> Dict[str, Any]:
    """彻底删除一次运行：**产物目录 + index 记录 + 队列任务 + 上传原件**。

    ``paper_id`` 只接受单层目录名（挡 ``../`` / 绝对路径），避免删除逃出 ``outputs/``。
    队列里还没跑完的同名任务会先被标记取消再删行——行一删，``is_cancel_requested``
    就返回 True，运行中的 worker 会在下一个阶段边界停下（沿用既有语义）。
    """
    pid = str(paper_id or "").strip()
    if not pid or pid in (".", "..") or re.search(r"[\\/]", pid) or Path(pid).is_absolute():
        return {"ok": False, "reason": "非法的 paper_id：%r" % (paper_id,)}

    settings = get_settings()
    out_root = Path(out_root) if out_root else Path(settings.output_dir)
    removed: Dict[str, Any] = {}

    pdir = out_root / pid
    if pdir.is_dir():
        shutil.rmtree(pdir, ignore_errors=True)
        removed["outputs"] = str(pdir)

    index_path = out_root / "index.json"
    n_record = 0
    if index_path.exists():
        try:
            data = json.loads(index_path.read_text(encoding="utf-8")) or []
        except Exception:
            data = []
        kept = [d for d in data if str((d or {}).get("paper_id")) != pid]
        n_record = len(data) - len(kept)
        if n_record:
            index_path.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
    removed["records"] = n_record

    try:
        from .taskqueue import get_queue

        removed["tasks"] = get_queue().delete_for_label(pid)
    except Exception as exc:  # 删队列失败不该阻止「产物已经被删」这件事照实上报
        removed["tasks"] = 0
        removed["tasks_error"] = "%s: %s" % (type(exc).__name__, exc)

    inc = Path(incoming_root) if incoming_root else Path(settings.workspace) / "incoming"
    idir = inc / pid
    if idir.is_dir():
        shutil.rmtree(idir, ignore_errors=True)
        removed["incoming"] = str(idir)

    return {"ok": True, "paper_id": pid, "removed": removed,
            "note": "已删除产物目录、index 记录、该论文的队列任务与上传原件"}


def run_batch(
    sources: Sequence[str | Path],
    output_dir: Optional[Path] = None,
    **kwargs: Any,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if output_dir is None:
        # 批量产物与单篇分开：默认落到 outputs/batch/<时间戳>/，避免混进 outputs/single/。
        output_dir = (Path(get_settings().batch_dir)
                      / f"batch_{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    for i, src in enumerate(sources, start=1):
        try:
            out.append(run_paper(src, output_dir=output_dir, **kwargs))
        except Exception as exc:
            out.append({"source": str(src), "error": f"{type(exc).__name__}: {exc}"})
    return out
