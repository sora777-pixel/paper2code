"""端到端编排：一篇论文进来，两类产出出去。"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .config import get_settings
from .explain import build_podcast, render_courseware
from .ingest import load_paper
from .llm import LLMProvider, get_provider
from .models import ExplainBundle, Paper
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
) -> Dict[str, Any]:
    """处理单篇论文，返回 manifest 字典。"""
    settings = get_settings().ensure_dirs()
    provider = provider or get_provider()

    paper = load_paper(source)
    out_root = Path(output_dir) if output_dir else settings.output_dir
    pdir = out_root / paper.id
    pdir.mkdir(parents=True, exist_ok=True)

    (pdir / "paper.json").write_text(paper.to_json(), encoding="utf-8")
    (pdir / "paper.md").write_text(_paper_to_markdown(paper), encoding="utf-8")

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
            "figures": len(paper.figures),
            "algorithms": len(paper.algorithms),
            "code_refs": len(paper.code_refs),
            "provided_data": len(paper.meta.get("provided_data", []) or []),
        },
        "warnings": list(paper.meta.get("warnings", []) or []),
    }

    # ---------------- 讲解材料 ---------------- #
    if explain:
        bundle = _build_explain(paper, pdir / "explain", provider, list(explain), audio=audio)
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

    # ---------------- 复现结果 ---------------- #
    if reproduce:
        res = reproduce_paper(paper, pdir / "reproduce", provider=provider, run_pseudocode=run_pseudocode)
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
        }
        manifest["warnings"].extend(res.warnings)

    manifest["warnings"] = list(dict.fromkeys(w for w in manifest["warnings"] if w))
    (pdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _update_index(out_root, manifest)
    return manifest


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

    try:
        outline = provider.outline(paper, settings.max_slides)
        slides = provider.slides(paper, outline)
    except Exception as exc:
        from .llm.offline import OfflineProvider
        bundle.warnings.append(f"explain remote failed, offline courseware: {exc}")
        offline = OfflineProvider()
        outline = offline.outline(paper, settings.max_slides)
        slides = offline.slides(paper, outline)
        provider = offline

    bundle.outline = outline
    bundle.slides = slides

    if EXPLAIN_COURSEWARE in kinds:
        path = render_courseware(paper, slides, out_dir / "courseware.html", generator=provider.describe())
        bundle.courseware_path = path

    if EXPLAIN_PODCAST in kinds:
        try:
            turns = provider.dialogue(paper)
        except Exception as exc:
            from .llm.offline import OfflineProvider
            bundle.warnings.append(f"podcast remote failed, offline dialogue: {exc}")
            turns = OfflineProvider().dialogue(paper)
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


def run_batch(
    sources: Sequence[str | Path],
    output_dir: Optional[Path] = None,
    **kwargs: Any,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i, src in enumerate(sources, start=1):
        try:
            out.append(run_paper(src, output_dir=output_dir, **kwargs))
        except Exception as exc:
            out.append({"source": str(src), "error": f"{type(exc).__name__}: {exc}"})
    return out
