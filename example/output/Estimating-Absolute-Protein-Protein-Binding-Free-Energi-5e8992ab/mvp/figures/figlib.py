"""figlib.py - helpers shared by every generated figure script.
Copied verbatim into <out>/figures/figlib.py. Generated scripts must only use this API."""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

PALETTE = ["#305496", "#C55A11", "#548235", "#7030A0", "#BF9000", "#2E75B6", "#C00000", "#595959"]
#: 调色板覆盖（M5-④ 图表风格插件）：生成的脚本调 set_palette([...]) 即可换配色，无需改这里的默认值
PALETTE_OVERRIDE = None
WATERMARK = "MVP toy data - NOT the paper's results"


def set_palette(colors):
    """覆盖配色盘（插件注册的图表风格通过它生效）。"""
    global PALETTE_OVERRIDE
    PALETTE_OVERRIDE = list(colors) if colors else None


def use_style():
    plt.rcParams.update({
        "figure.dpi": 120, "savefig.dpi": 200, "axes.grid": True, "grid.alpha": 0.25,
        "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
        "axes.prop_cycle": matplotlib.cycler(color=PALETTE_OVERRIDE or PALETTE),
        "font.sans-serif": ["Microsoft YaHei", "SimHei", "PingFang SC", "Noto Sans CJK SC", "WenQuanYi Micro Hei", "DejaVu Sans"],
        "axes.unicode_minus": False,
    })


def load_results(results_dir):
    """metrics.json content at top level; every other *.json under its file stem."""
    d = Path(results_dir)
    res = json.loads((d / "metrics.json").read_text(encoding="utf-8"))
    for f in d.glob("*.json"):
        if f.name != "metrics.json":
            res.setdefault(f.stem, json.loads(f.read_text(encoding="utf-8")))
    return res


def get(res, dotted, default=None):
    """get(res, 'curves.train_loss.y') -> value, or default if missing."""
    cur = res
    try:
        for part in dotted.split("."):
            cur = cur[int(part)] if isinstance(cur, list) else cur[part]
        return cur
    except (KeyError, IndexError, ValueError, TypeError):
        return default


def new_fig(w=6.0, h=4.0, nrows=1, ncols=1):
    use_style()
    return plt.subplots(nrows, ncols, figsize=(w, h), squeeze=False)  # axes is always 2-D


def save_fig(fig, out_dir, name, toy=True, title=None):
    """Save PNG+SVG. toy=True stamps a footer so toy-data plots are never mistaken for paper results."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if title:
        fig.suptitle(title, fontsize=11)
    if toy:
        fig.text(0.99, 0.005, WATERMARK, ha="right", va="bottom", fontsize=7, color="#999999")
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(out / f"{name}.png")
    fig.savefig(out / f"{name}.svg")
    plt.close(fig)
    return str(out / f"{name}.png")


def _edge_point(cx, cy, w, h, dx, dy):
    """Point where the ray from the box centre in direction (dx, dy) leaves the box."""
    tx = (w / 2) / abs(dx) if dx else float("inf")
    ty = (h / 2) / abs(dy) if dy else float("inf")
    t = min(tx, ty)
    return cx + t * dx, cy + t * dy


def flow_diagram(ax, nodes, edges, node_w=0.22, node_h=0.14):
    """nodes: [{'id','label','x','y'}] with x,y in [0,1] (optional 'w','h','color');
    edges: [{'from','to','label'(opt)}]. Arrows attach to box edges."""
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    box = {}
    for n in nodes:
        w, h = n.get("w", node_w), n.get("h", node_h)
        box[n["id"]] = (n["x"], n["y"], w, h)
        ax.add_patch(FancyBboxPatch((n["x"] - w / 2, n["y"] - h / 2), w, h,
                                    boxstyle="round,pad=0.005,rounding_size=0.02",
                                    fc=n.get("color", "#DEEAF6"), ec="#305496", lw=1.2))
        ax.text(n["x"], n["y"], n["label"], ha="center", va="center", fontsize=8, wrap=True)
    for e in edges:
        x0, y0, w0, h0 = box[e["from"]]
        x1, y1, w1, h1 = box[e["to"]]
        dx, dy = x1 - x0, y1 - y0
        if dx == 0 and dy == 0:
            continue
        a = _edge_point(x0, y0, w0, h0, dx, dy)
        b = _edge_point(x1, y1, w1, h1, -dx, -dy)
        ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=12, shrinkA=1, shrinkB=1,
                                     lw=1.1, color="#444444"))
        if e.get("label"):
            ax.text((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 + 0.035, e["label"], ha="center", fontsize=7, color="#444444")
