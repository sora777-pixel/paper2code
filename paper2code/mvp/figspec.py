"""图表「绘图说明」(figure drawing spec)：生成、合并、解析数据、渲染、写说明文档。

一份 spec 就是「怎么把这张图画出来」的完整信息：类型、坐标轴、系列→数据键、版式、风格、
（示意图的）节点与箭头，以及这张图能否复现、为什么。离线模式也能产出（来自论文表格与图注）。
"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..models import Paper
from ..reproduce import charting, digitize
from .textio import get_path, slug

KINDS = {"line", "bar", "grouped_bar", "scatter", "heatmap", "diagram"}
REPRO = {"from_paper_data", "from_demo", "schematic", "not_reproducible"}
ARCH_HINTS = ("architecture", "overview", "framework", "pipeline", "diagram", "schematic", "illustration",
              "workflow", "model structure", "结构", "框架", "流程", "示意", "概览")
PLOT_HINTS = ("curve", "accuracy", "loss", "versus", " vs", "trend", "over time", "scaling", "comparison",
              "ablation", "performance", "speedup", "throughput", "latency", "recall", "precision", "share", "曲线")
TOY_NOTE = "MVP 玩具数据，仅反映定性趋势，并非论文结果"


def _num(raw: Any) -> str:
    """'F2' / 'Figure 2' / 'T3' / 'Table3' -> '2' / '2' / '3' / '3'（取不到数字则原样返回）。"""
    m = re.search(r"(\d+)", str(raw))
    return m.group(1) if m else str(raw)


def classify_figure(fig: Any) -> str:
    if fig.kind == "architecture":
        return "diagram"
    if fig.kind == "plot":
        return "plot"
    cap = (fig.caption or "").lower()
    if any(h in cap for h in ARCH_HINTS):
        return "diagram"
    if any(h in cap for h in PLOT_HINTS):
        return "plot"
    return "unknown"


def _base(fid: str, ref: str, caption: str, kind: str, repro: str,
          original: Optional[str] = None) -> Dict[str, Any]:
    return {"id": fid, "paper_ref": ref, "caption": caption, "kind": kind, "reproducibility": repro,
            "data_keys": [], "axes": {}, "series": [], "chart": None, "layout": "1x1", "style_notes": "",
            "diagram": None, "mermaid": None, "demo_change_needed": None, "source_note": "",
            # M2-2：论文原图（PDF 位图已落盘）的绝对路径，供 FIGURES.md 做「原图 vs 重绘」对照
            "original_image": original}


def baseline_specs(paper: Paper) -> List[Dict[str, Any]]:
    """确定性基线（无需 LLM）：表格 → 可重绘图；图注 → 分类并关联相关表格。"""
    specs: List[Dict[str, Any]] = []
    table_specs: Dict[str, Dict[str, Any]] = {}
    for t in paper.tables:
        if not t.rows:
            continue
        cs = charting.infer_spec(t, title=t.caption or ("表 %s" % t.id))
        if cs is None:
            continue
        s = _base("fig_tab_%s" % slug(t.id), "Table %s" % t.id, t.caption, cs.kind, "from_paper_data")
        s["chart"] = cs.to_dict()
        s["axes"] = {"x": cs.xlabel, "y": cs.ylabel}
        s["source_note"] = "数据来自论文表 %s（%s），这是论文自己的数字" % (t.id, t.source)
        specs.append(s)
        table_specs[_num(t.id)] = s
    for f in paper.figures:
        # M2-2：「挂不上图题的位图」只作为原图保留，不进重绘目标
        # （否则 _num("E1_173") 会把它错标成「Figure 1」）
        if f.kind == "embedded":
            continue
        cls = classify_figure(f)
        fid = "fig_%s" % slug(f.id)
        # M2-2：原图落盘后，FIGURES.md 可以把「原图」与「重绘」并排给出
        try:
            orig = digitize.resolve_image_path(paper, f)
        except Exception:
            orig = None
        original = str(orig) if orig else None
        # 先试「从论文原图数字化取点」：矢量图能反推出接近真实的数据，
        # 这样 fig_f1 这类「只有图没有表」的图也能重绘（roadmap M2-4）。
        if cls != "diagram":
            try:
                dig = digitize.digitize_figure(paper, f)
            except Exception:
                dig = None
            if dig is not None:
                s = _base(fid, "Figure %s" % _num(f.id), f.caption, dig.kind, "from_paper_data", original)
                s["chart"] = dig.to_chart()
                s["axes"] = {"x": dig.xlabel, "y": dig.ylabel}
                s["source_note"] = "从论文原图数字化取点得到（%s，置信度 %.2f）%s" % (
                    dig.source, dig.confidence,
                    ("；" + "；".join(dig.notes)) if dig.notes else "")
                specs.append(s)
                continue
        if cls == "diagram":
            s = _base(fid, "Figure %s" % _num(f.id), f.caption, "diagram", "not_reproducible", original)
            s["demo_change_needed"] = "示意/架构图：离线模式无法还原图中内容；启用 LLM 后可依据图注与方法章节生成方框-箭头示意"
        else:
            s = _base(fid, "Figure %s" % _num(f.id), f.caption, "bar" if cls == "unknown" else "line",
                      "not_reproducible", original)
            m = re.search(r"\b(?:table|tab\.?|表)\s*(\d+)", f.caption or "", re.I)
            ref_tab = table_specs.get(m.group(1)) if m else None
            if ref_tab:
                s["reproducibility"] = "from_paper_data"
                s["kind"] = ref_tab["kind"]
                s["chart"] = ref_tab["chart"]
                s["source_note"] = "图注引用了 %s，已用该表数据近似重绘（图的原始数据未随论文提供）" % ref_tab["paper_ref"]
            else:
                s["demo_change_needed"] = "需要真实实验数据；可让 demo 写出对应曲线/扫参结果（见 plan.required_outputs）后再绘制"
        specs.append(s)
    return specs


def normalize_llm_specs(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for f in (raw or {}).get("figures", []) or []:
        if not isinstance(f, dict):
            continue
        ref = str(f.get("paper_ref") or f.get("id") or "x")
        sl = slug(ref)
        sl = sl[4:] if sl.startswith("fig_") else sl
        while sl in seen:
            sl += "_b"
        seen.add(sl)
        s = _base("fig_%s" % sl, ref, str(f.get("caption") or ""), "line", "not_reproducible")
        k = f.get("kind")
        s["kind"] = k if k in KINDS else "line"
        r = f.get("reproducibility")
        s["reproducibility"] = r if r in REPRO else "not_reproducible"
        for key in ("axes", "layout", "style_notes", "mermaid", "demo_change_needed"):
            if f.get(key) is not None:
                s[key] = f[key]
        s["series"] = [x for x in (f.get("series") or []) if isinstance(x, dict) and x.get("key")]
        s["data_keys"] = [x["key"] for x in s["series"]]
        d = f.get("diagram")
        if isinstance(d, dict) and d.get("nodes"):
            s["diagram"] = {"nodes": d["nodes"], "edges": d.get("edges") or []}
        out.append(s)
    return out


def merge_specs(base: List[Dict[str, Any]], llm: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_ref = {s["paper_ref"]: s for s in base}
    for s in llm:
        old = by_ref.get(s["paper_ref"])
        if old and old["reproducibility"] == "from_paper_data" and s["reproducibility"] == "not_reproducible":
            continue  # 不用「不可复现」覆盖已有的论文数据重绘
        by_ref[s["paper_ref"]] = s
    order = [s["paper_ref"] for s in base] + [s["paper_ref"] for s in llm if s["paper_ref"] not in {b["paper_ref"] for b in base}]
    return [by_ref[r] for r in dict.fromkeys(order)]


# --------------------------------------------------------------------------- #
def _series_xy(results: Dict[str, Any], key: str) -> Tuple[List[str], List[float]]:
    v = get_path(results, key)
    if isinstance(v, dict) and "y" in v:
        ys, xs = v["y"], v.get("x") or list(range(len(v["y"])))
    elif isinstance(v, dict) and "values" in v:
        ys, xs = v["values"], v.get("names") or v.get("labels") or list(range(len(v["values"])))
    elif isinstance(v, list):
        ys, xs = v, list(range(len(v)))
    else:
        raise KeyError(key)
    vals = [float(y) for y in ys]
    if not all(math.isfinite(y) for y in vals):
        raise ValueError("non-finite values in %s" % key)
    def lab(x: Any) -> str:
        return ("%.4g" % x) if isinstance(x, float) else str(x)
    return [lab(x) for x in xs][:len(vals)], vals


def resolve_chart(spec: Dict[str, Any], results: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """把 from_demo 的 series→键 解析成可渲染的 ChartSpec 字典；失败返回 None 并写入 spec['resolve_error']。"""
    if spec.get("chart"):
        return spec["chart"]
    if spec["reproducibility"] != "from_demo" or not results or spec["kind"] not in ("line", "bar", "grouped_bar"):
        return None
    cats: List[str] = []
    series: List[Dict[str, Any]] = []
    try:
        for s in spec["series"]:
            xs, ys = _series_xy(results, s["key"])
            if not cats:
                cats = xs
            n = min(len(cats), len(ys))
            series.append({"name": str(s.get("name") or s["key"]), "values": ys[:n]})
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        spec["resolve_error"] = "%s: %s" % (type(exc).__name__, exc)
        return None
    if not series:
        return None
    n = min(len(s["values"]) for s in series)
    cats = cats[:n]
    series = [{"name": s["name"], "values": s["values"][:n]} for s in series]
    if spec["kind"] == "line" and n > 60:  # 曲线点太多时等间隔抽样，保证内置渲染器可读
        step = int(math.ceil(n / 60.0))
        cats = cats[::step]
        series = [{"name": s["name"], "values": s["values"][::step]} for s in series]
    kind = spec["kind"] if spec["kind"] != "bar" or len(series) == 1 else "grouped_bar"
    ax = spec.get("axes") or {}
    return {"kind": kind, "title": spec["caption"] or spec["paper_ref"], "xlabel": ax.get("x", ""),
            "ylabel": ax.get("y", ""), "categories": cats, "series": series, "note": TOY_NOTE}


def render_builtin(spec: Dict[str, Any], out_dir: Path) -> Optional[str]:
    """用仓库自带 charting（matplotlib 或纯 SVG 后备）渲染；没有可渲染数据时返回 None。"""
    c = spec.get("chart")
    if not c:
        return None
    try:
        cs = charting.ChartSpec(kind=c["kind"], title=c.get("title", ""), xlabel=c.get("xlabel", ""),
                                ylabel=c.get("ylabel", ""), categories=c["categories"], series=c["series"],
                                note=c.get("note", ""))
        return charting.render(cs, out_dir, stem=spec["id"])
    except Exception:
        return None


# --------------------------------------------------------------------------- #
_CHART_TMPL = '''"""由 paper2code.mvp 自动生成：依据 specs/__ID__.json 绘制 __REF__。"""
import json
from pathlib import Path

from figlib import *


def main(results_dir, out_dir):
    spec = json.loads((Path(__file__).parent / "specs" / "__ID__.json").read_text(encoding="utf-8"))
    chart = spec.get("chart")
    if not chart:
        return
    fig, axes = new_fig(6.4, 4.2)
    ax = axes[0][0]
    cats, series = chart["categories"], chart["series"]
    xs = list(range(len(cats)))
    if chart["kind"] == "line":
        for s in series:
            ax.plot(xs, s["values"], marker="o", ms=3, label=s["name"])
    else:
        n = max(len(series), 1)
        w = 0.8 / n
        for i, s in enumerate(series):
            ax.bar([j + i * w for j in xs], s["values"], w, label=s["name"])
        xs = [j + 0.4 - w / 2 for j in xs]
    step = max(1, len(cats) // 10)
    ax.set_xticks(xs[::step])
    ax.set_xticklabels(cats[::step], rotation=30, ha="right")
    ax.set_xlabel(chart.get("xlabel", ""))
    ax.set_ylabel(chart.get("ylabel", ""))
    if len(series) > 1:
        ax.legend()
    save_fig(fig, out_dir, "__ID__", toy=__TOY__, title=chart.get("title") or spec.get("caption"))
'''

_DIAGRAM_TMPL = '''"""由 paper2code.mvp 自动生成：依据 specs/__ID__.json 绘制 __REF__（示意图）。"""
import json
from pathlib import Path

from figlib import *


def main(results_dir, out_dir):
    spec = json.loads((Path(__file__).parent / "specs" / "__ID__.json").read_text(encoding="utf-8"))
    d = spec.get("diagram")
    if not d:
        return
    fig, axes = new_fig(8, 3.6)
    flow_diagram(axes[0][0], d["nodes"], d.get("edges", []))
    save_fig(fig, out_dir, "__ID__", toy=False, title=spec.get("caption"))
'''


def template_script(spec: Dict[str, Any]) -> Optional[str]:
    """确定性绘图脚本（离线 / LLM 脚本失败时的后备）；没有可画内容时返回 None。"""
    if spec.get("chart"):
        toy = "False" if spec["reproducibility"] == "from_paper_data" else "True"
        code = _CHART_TMPL.replace("__ID__", spec["id"]).replace("__REF__", spec["paper_ref"]).replace("__TOY__", toy)
        return _inject_style(code)
    if spec.get("diagram"):
        return _inject_style(_DIAGRAM_TMPL.replace("__ID__", spec["id"]).replace("__REF__", spec["paper_ref"]))
    return None


def _inject_style(code: str) -> str:
    """把「当前图表风格」（M5-④ 插件注册的 style）注入确定性绘图脚本。

    只对**我们自己的模板脚本**做这件事：模板的第一行固定是 ``from figlib import *``，
    在它后面插一行 ``set_palette([...])`` 即可。LLM 写的脚本不做文本注入（不敢猜它的结构）。
    没有配置风格时原样返回——不改变既有产物。
    """
    try:
        from .. import plugins
        from ..config import get_settings

        name = str(getattr(get_settings(), "chart_style", "") or "")
        if not name:
            return code
        style = dict(plugins._EXTRA_STYLES.get(name) or {})
        palette = style.get("palette")
        if not palette:
            return code
        anchor = "from figlib import *\n"
        if anchor not in code:
            return code
        return code.replace(anchor, anchor + "set_palette(%r)\n" % (list(palette),), 1)
    except Exception:
        return code


_REPRO_ZH = {"from_paper_data": "用论文自带数据重绘", "from_demo": "用 demo 玩具数据定性复现",
             "schematic": "示意图（无数据）", "not_reproducible": "无法复现"}


def _rel_link(target: Optional[str], path: Path) -> str:
    """把产物路径换算成相对 FIGURES.md 的 **URL 风格** 链接（正斜杠）。"""
    if not target:
        return ""
    try:
        p = Path(target)
        rel = str(p.relative_to(path.parent)) if not p.is_absolute() else os.path.relpath(p, path.parent)
    except (ValueError, OSError):
        try:
            rel = os.path.relpath(Path(target), path.parent)
        except (ValueError, OSError):
            return ""
    # Windows 的 os.path.relpath 返回反斜杠，写进 <img src> 在浏览器里解析不了
    return rel.replace("\\", "/")


def write_figures_md(specs: List[Dict[str, Any]], status: Dict[str, Dict[str, Any]],
                     path: Path, out_root: Optional[Path] = None) -> None:
    out = ["# 图表绘制说明（figure drawing spec）\n",
           "每个条目都是可直接落地的绘图信息：类型、坐标轴、系列→数据键、版式、风格。标注「demo 玩具数据」的图只反映"
           "**定性趋势，不是论文数值**；「无法复现」的图会写明原因与改法，而不是伪造。\n",
           "> 凡能从 PDF 抽出原图的条目都会给出「论文原图」；若同时有重绘产物，则**并排**对照，便于逐图核对差异。\n"]
    root = Path(out_root) if out_root else path.parent.parent.parent
    for s in specs:
        st = status.get(s["id"], {})
        out.append("## %s ← %s　[%s · %s]\n" % (s["id"], s["paper_ref"], _REPRO_ZH.get(s["reproducibility"], ""), st.get("state", "?")))
        out.append("- **图注**：%s" % (s.get("caption") or ""))
        out.append("- **类型 / 版式**：%s / %s" % (s["kind"], s.get("layout", "")))
        ax = s.get("axes") or {}
        if ax:
            out.append("- **坐标轴**：x = %s（%s），y = %s（%s）" % (ax.get("x", ""), ax.get("xscale", "linear"), ax.get("y", ""), ax.get("yscale", "linear")))
        if s.get("series"):
            out.append("- **系列**：" + "；".join("%s ← `%s`" % (x.get("name"), x.get("key")) for x in s["series"]))
        elif s.get("chart"):
            out.append("- **系列**：" + "；".join(x["name"] for x in s["chart"]["series"]) + "（数据已内嵌于 specs/%s.json）" % s["id"])
        if s.get("source_note"):
            out.append("- **数据来源**：%s" % s["source_note"])
        if s.get("style_notes"):
            out.append("- **风格**：%s" % s["style_notes"])
        if s.get("demo_change_needed"):
            out.append("- **要真正复现需要**：%s" % s["demo_change_needed"])
        if s.get("resolve_error"):
            out.append("- **数据键解析失败**：%s" % s["resolve_error"])
        if st.get("files"):
            out.append("- **产物**：" + "、".join("`%s`" % f for f in st["files"]))

        orig = _rel_link(s.get("original_image"), path)
        drawn = _rel_link(str(root / st["files"][0]), path) if st.get("files") else ""
        if orig and drawn:
            out.append("")
            out.append('<p><img src="%s" alt="论文原图" width="46%%"> '
                       '<img src="%s" alt="本次重绘" width="46%%"></p>' % (orig, drawn))
            out.append("<p align=\"center\">左：论文原图　·　右：本次重绘</p>")
        elif orig:
            out.append("")
            out.append('<p><img src="%s" alt="论文原图" width="60%%"></p>' % orig)
            out.append("<p align=\"center\">论文原图（无重绘产物：%s）</p>"
                       % _REPRO_ZH.get(s["reproducibility"], "见上方说明"))

        if s.get("mermaid"):
            out.append("\n```mermaid\n%s\n```" % s["mermaid"].strip())
        out.append("")
    path.write_text("\n".join(out), encoding="utf-8")
