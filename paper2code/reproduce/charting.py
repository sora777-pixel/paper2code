"""图表重绘：把复现出的数据渲染成图。

双后端设计，保证在任何环境下都能出图：

* ``matplotlib`` 可用 → 输出 PNG（矢量可选 SVG）；
* 不可用 → 使用内置的**纯 stdlib SVG 渲染器**输出 SVG。

这样即使在不装任何科学计算库的机器上，MVP 的「图表重现」环节也不会空转。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import numutil
from ..models import Table

PALETTE = ["#4f6bed", "#8b5cf6", "#0ea5a4", "#f59e0b", "#e11d48", "#2563eb", "#7c3aed", "#059669"]

_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif"


@dataclass
class ChartSpec:
    kind: str = "bar"            # bar | grouped_bar | line
    title: str = ""
    xlabel: str = ""
    ylabel: str = ""
    categories: List[str] = field(default_factory=list)
    series: List[Dict[str, Any]] = field(default_factory=list)  # {name, values}
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind, "title": self.title, "xlabel": self.xlabel,
            "ylabel": self.ylabel, "categories": self.categories,
            "series": self.series, "note": self.note,
        }


# --------------------------------------------------------------------------- #
# 从表格推断图表
# --------------------------------------------------------------------------- #
_ID_COL_HINTS = ("method", "model", "approach", "name", "方法", "模型", "system", "dataset", "variant", "type")


def infer_spec(table: Table, title: str = "", max_series: int = 4, max_cats: int = 12) -> Optional[ChartSpec]:
    """从一张表格推断出可绘制的图表规格。

    规则：第一列视为类别轴；其余列中，取值可解析为数字的列作为系列。
    """
    if not table.rows or not table.header:
        return None

    header = list(table.header)
    ncols = max(len(header), max((len(r) for r in table.rows), default=0))
    header += [f"col{i+1}" for i in range(len(header), ncols)]

    # 类别列：优先使用表头含 id 提示的列，否则用第 0 列
    cat_idx = 0
    for i, h in enumerate(header):
        if any(k in str(h).lower() for k in _ID_COL_HINTS):
            cat_idx = i
            break

    numeric_cols: List[Tuple[int, str]] = []
    for ci in range(ncols):
        if ci == cat_idx:
            continue
        vals = [numutil.to_number(r[ci]) if ci < len(r) else None for r in table.rows]
        good = [v for v in vals if v is not None]
        if len(good) >= max(2, int(0.6 * len(table.rows))):
            numeric_cols.append((ci, header[ci] or f"col{ci+1}"))
    if not numeric_cols:
        return None

    numeric_cols = numeric_cols[:max_series]
    categories: List[str] = []
    rows = []
    for r in table.rows[:max_cats]:
        label = str(r[cat_idx]).strip() if cat_idx < len(r) else ""
        if not label:
            label = f"row{len(categories)+1}"
        categories.append(_short(label))
        rows.append(r)

    series = []
    for ci, name in numeric_cols:
        values = [numutil.to_number(r[ci]) if ci < len(r) else None for r in rows]
        if all(v is None for v in values):
            continue
        series.append({"name": _short(name), "values": [0.0 if v is None else v for v in values]})
    if not series:
        return None

    series = _drop_incomparable_series(series)

    # 量纲守卫：一张图里同时出现「总请求数 6.3e7」和「命中率 0.83」这类差若干数量级
    # 的列，画出来只会误导人。这里直接拒绝，请人工拆成两张图。
    for s in series:
        vals = [abs(v) for v in s["values"] if v]
        if not vals:
            continue
        lo, hi = min(vals), max(vals)
        if lo > 0 and hi / lo > 1e4:
            return None
    all_vals = [abs(v) for s in series for v in s["values"] if v]
    if all_vals and min(all_vals) > 0 and max(all_vals) / min(all_vals) > 1e4:
        return None

    kind = "line" if (len(categories) >= 7 and len(series) == 1) else (
        "grouped_bar" if len(series) > 1 else "bar"
    )
    return ChartSpec(
        kind=kind,
        title=title or table.caption or f"表 {table.id} 数据重绘",
        xlabel=_short(header[cat_idx]),
        ylabel=series[0]["name"] if len(series) == 1 else "数值",
        categories=categories,
        series=series,
        note=f"数据来源：论文表 {table.id}（{table.source}）",
    )


def _short(text: str, n: int = 18) -> str:
    t = str(text or "").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def _drop_incomparable_series(series: List[Dict[str, Any]], factor: float = 100.0) -> List[Dict[str, Any]]:
    """丢掉量纲与其余系列差两个数量级以上的列。

    例如一张「数据集统计」表里同时有 Documents(万级) 和 Avg. tokens(十级)，
    画在一张柱状图上后者会完全不可见。这里以各系列量级的中位数为准做过滤。
    """
    if len(series) <= 1:
        return series
    scales = sorted(max((abs(v) for v in s["values"]), default=0.0) for s in series)
    median = scales[len(scales) // 2]
    if median <= 0:
        return series
    kept = [
        s for s in series
        if median / factor <= max((abs(v) for v in s["values"]), default=0.0) <= median * factor
    ]
    return kept or series


# --------------------------------------------------------------------------- #
# 渲染入口
# --------------------------------------------------------------------------- #
def render(spec: ChartSpec, out_dir: Path, stem: str = "chart", prefer_png: bool = True) -> Optional[str]:
    """渲染图表，返回产物路径。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{stem}.spec.json").write_text(
        json.dumps(spec.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if prefer_png and _matplotlib_available():
        p = _render_matplotlib(spec, out_dir / f"{stem}.png", vector=True)
        if p:
            return p
    return _render_svg(spec, out_dir / f"{stem}.svg")


def _matplotlib_available() -> bool:
    try:
        import matplotlib  # noqa: F401

        return True
    except Exception:
        return False


def _render_matplotlib(spec: ChartSpec, out_path: Path, vector: bool = True) -> Optional[str]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None

    try:
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "PingFang SC", "SimHei", "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False

        fig, ax = plt.subplots(figsize=(9.2, 4.8), dpi=150)
        n = len(spec.categories)
        xs = list(range(n))

        if spec.kind == "line":
            for i, s in enumerate(spec.series):
                ax.plot(xs, s["values"], marker="o", linewidth=2.4, color=PALETTE[i % len(PALETTE)], label=s["name"])
        else:
            w = 0.8 / max(1, len(spec.series))
            for i, s in enumerate(spec.series):
                offs = [x - 0.4 + w / 2 + i * w for x in xs]
                ax.bar(offs, s["values"], width=w * 0.92, color=PALETTE[i % len(PALETTE)], label=s["name"], zorder=3)

        ax.set_title(spec.title, fontsize=13, fontweight="bold", pad=12)
        ax.set_xlabel(spec.xlabel, fontsize=10.5)
        ax.set_ylabel(spec.ylabel, fontsize=10.5)
        ax.set_xticks(xs)
        ax.set_xticklabels(spec.categories, rotation=28 if n > 6 else 0, ha="right" if n > 6 else "center", fontsize=9.5)
        ax.grid(axis="y", linestyle="--", alpha=0.35, zorder=0)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        if len(spec.series) > 1:
            ax.legend(fontsize=9.5, frameon=False)
        fig.tight_layout()
        fig.savefig(out_path, bbox_inches="tight", facecolor="white")
        if vector:
            fig.savefig(out_path.with_suffix(".svg"), bbox_inches="tight", facecolor="white")
        plt.close(fig)
        return str(out_path)
    except Exception:
        try:
            plt.close("all")
        except Exception:
            pass
        return None


# --------------------------------------------------------------------------- #
# 纯 stdlib SVG 渲染器
# --------------------------------------------------------------------------- #
_SVG_W, _SVG_H = 820, 460
_M = {"l": 74, "r": 26, "t": 56, "b": 96}


def _render_svg(spec: ChartSpec, out_path: Path) -> Optional[str]:
    try:
        svg = build_svg(spec)
        out_path.write_text(svg, encoding="utf-8")
        return str(out_path)
    except Exception:
        return None


def build_svg(spec: ChartSpec, width: int = _SVG_W, height: int = _SVG_H) -> str:
    plot_w = width - _M["l"] - _M["r"]
    plot_h = height - _M["t"] - _M["b"]

    all_vals = [v for s in spec.series for v in s["values"]] or [0.0]
    vmin = min(0.0, min(all_vals))
    vmax = max(all_vals) or 1.0
    if vmax == vmin:
        vmax = vmin + 1.0
    pad = (vmax - vmin) * 0.12
    vmax += pad

    def y_of(v: float) -> float:
        return _M["t"] + plot_h * (1 - (v - vmin) / (vmax - vmin))

    n = max(1, len(spec.categories))
    band = plot_w / n

    out: List[str] = []
    out.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'font-family="{_FONT}">'
    )
    out.append(f'<rect width="{width}" height="{height}" fill="#ffffff"/>')
    out.append(
        f'<text x="{_M["l"]}" y="30" font-size="16" font-weight="700" fill="#1b2130">{_x(spec.title)}</text>'
    )
    if spec.note:
        out.append(
            f'<text x="{_M["l"]}" y="47" font-size="11.5" fill="#5d6b85">{_x(spec.note)}</text>'
        )

    # 网格 + y 轴刻度
    ticks = 5
    for i in range(ticks + 1):
        v = vmin + (vmax - vmin) * i / ticks
        y = y_of(v)
        out.append(
            f'<line x1="{_M["l"]}" y1="{y:.1f}" x2="{_M["l"] + plot_w}" y2="{y:.1f}" stroke="#e8ecf6" stroke-width="1"/>'
        )
        out.append(
            f'<text x="{_M["l"] - 10}" y="{y + 4:.1f}" font-size="10.5" fill="#5d6b85" text-anchor="end">{_fmt(v)}</text>'
        )

    # 坐标轴
    out.append(
        f'<line x1="{_M["l"]}" y1="{_M["t"]}" x2="{_M["l"]}" y2="{_M["t"] + plot_h}" stroke="#c9d2e6" stroke-width="1.2"/>'
    )
    out.append(
        f'<line x1="{_M["l"]}" y1="{_M["t"] + plot_h}" x2="{_M["l"] + plot_w}" y2="{_M["t"] + plot_h}" stroke="#c9d2e6" stroke-width="1.2"/>'
    )

    # 数据
    if spec.kind == "line":
        for si, s in enumerate(spec.series):
            color = PALETTE[si % len(PALETTE)]
            pts = []
            for i, v in enumerate(s["values"][:n]):
                cx = _M["l"] + band * (i + 0.5)
                pts.append(f"{cx:.1f},{y_of(v):.1f}")
            out.append(
                f'<polyline fill="none" stroke="{color}" stroke-width="2.6" stroke-linejoin="round" points="{" ".join(pts)}"/>'
            )
            for pt in pts:
                cx, cy = pt.split(",")
                out.append(f'<circle cx="{cx}" cy="{cy}" r="3.6" fill="{color}"/>')
    else:
        nser = max(1, len(spec.series))
        bw = band * 0.78 / nser
        for si, s in enumerate(spec.series):
            color = PALETTE[si % len(PALETTE)]
            for i, v in enumerate(s["values"][:n]):
                x = _M["l"] + band * i + band * 0.11 + si * bw
                y = y_of(v)
                h = max(1.0, y_of(vmin) - y)
                out.append(
                    f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw * 0.94:.1f}" height="{h:.1f}" rx="3" fill="{color}" opacity="0.92"/>'
                )
                if nser == 1 and n <= 8:
                    out.append(
                        f'<text x="{x + bw * 0.47:.1f}" y="{y - 5:.1f}" font-size="10" fill="#39457a" text-anchor="middle">{_fmt(v)}</text>'
                    )

    # x 轴标签
    for i, cat in enumerate(spec.categories[:n]):
        cx = _M["l"] + band * (i + 0.5)
        out.append(
            f'<text x="{cx:.1f}" y="{_M["t"] + plot_h + 18}" font-size="10.5" fill="#39457a" text-anchor="end" '
            f'transform="rotate(-26 {cx:.1f} {_M["t"] + plot_h + 18})">{_x(cat)}</text>'
        )

    # 轴标题
    out.append(
        f'<text x="{_M["l"] + plot_w / 2:.1f}" y="{height - 14}" font-size="11.5" fill="#5d6b85" text-anchor="middle">{_x(spec.xlabel)}</text>'
    )
    out.append(
        f'<text x="16" y="{_M["t"] + plot_h / 2:.1f}" font-size="11.5" fill="#5d6b85" text-anchor="middle" '
        f'transform="rotate(-90 16 {_M["t"] + plot_h / 2:.1f})">{_x(spec.ylabel)}</text>'
    )

    # 图例
    if len(spec.series) > 1:
        lx = _M["l"] + plot_w - 8
        for si, s in enumerate(spec.series):
            color = PALETTE[si % len(PALETTE)]
            ly = _M["t"] + 4 + si * 20
            out.append(f'<rect x="{lx - 84}" y="{ly - 9}" width="12" height="12" rx="3" fill="{color}"/>')
            out.append(
                f'<text x="{lx - 66}" y="{ly + 1}" font-size="11" fill="#39457a" text-anchor="start">{_x(s["name"])}</text>'
            )

    out.append("</svg>")
    return "\n".join(out)


def _x(text: str) -> str:
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _fmt(v: float) -> str:
    if v == 0:
        return "0"
    a = abs(v)
    if a >= 1000:
        return f"{v:,.0f}"
    if a >= 10:
        return f"{v:.1f}".rstrip("0").rstrip(".")
    if a >= 1:
        return f"{v:.2f}".rstrip("0").rstrip(".")
    return f"{v:.3g}"
