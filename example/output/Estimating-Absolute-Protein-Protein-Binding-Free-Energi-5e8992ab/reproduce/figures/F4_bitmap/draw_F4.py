"""由 paper2code 生成的同风格重绘脚本（F4）。

⚠️ 数据来自 data.csv —— 它是**从 PDF 位图反推的估算值**，不是论文原始数据。
   从 PDF 中读取或识别的数值不等同于原文献数值（位图反推，仅供绘图对照）

STYLE 里是**从原图实际测量**的样式（柱色/点径/描边/趋势线），换数据后仍保持同款风格。

用法：
    python draw_F4.py                 # 读同目录 data.csv，输出 preview_panel*.png
把 data.csv 换成你自己复现得到的数据（列名保持一致：panel,kind,series,x,y），即可重画。
"""
import csv
import json
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt

HERE = Path(__file__).parent
DISCLAIMER = '从 PDF 中读取或识别的数值不等同于原文献数值（位图反推，仅供绘图对照）'

#: 逐面板样式（从原图识别）
STYLE = json.loads(r"""[
  {
    "kind": "bar",
    "orientation": "h",
    "colors": [
      "#ebf2f8",
      "#dee9f3",
      "#d2e0ed",
      "#c0d5e5",
      "#accadb",
      "#93bbd2",
      "#78aac8",
      "#629abe",
      "#4e8ab3",
      "#3c78a8",
      "#2c669b",
      "#1e558d",
      "#184477"
    ],
    "gradient": {
      "direction": "lighten_down",
      "from": "#ebf2f8",
      "to": "#184477",
      "axis": "vertical",
      "note": "同一色板按柱序由浅到深（与原文一致）"
    }
  },
  {
    "kind": "bar",
    "orientation": "h",
    "colors": [
      "#ebf2f8",
      "#dee9f3",
      "#d2e0ed",
      "#c0d5e5",
      "#accadb",
      "#93bbd2",
      "#78aac8",
      "#629abe",
      "#4e8ab3",
      "#3c78a8",
      "#2c669b",
      "#1e558d",
      "#184477"
    ],
    "gradient": {
      "direction": "lighten_down",
      "from": "#ebf2f8",
      "to": "#184477",
      "axis": "vertical",
      "note": "同一色板按柱序由浅到深（与原文一致）"
    }
  },
  {
    "kind": "bar",
    "orientation": "h",
    "colors": [
      "#ebf2f8",
      "#d2e0ed",
      "#c0d5e5",
      "#accadb",
      "#93bbd2",
      "#78aac8",
      "#4e7a97",
      "#3c78a8",
      "#2c669b",
      "#1e558d",
      "#184477"
    ],
    "gradient": {
      "direction": "lighten_down",
      "from": "#ebf2f8",
      "to": "#184477",
      "axis": "vertical",
      "note": "同一色板按柱序由浅到深（与原文一致）"
    }
  }
]""")

#: 参考线：原图里的灰色虚线（零线/阈值线）
DASH = (0, (4, 3))


def load():
    rows = defaultdict(list)
    with (HERE / "data.csv").open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows[(int(r["panel"]), r["kind"])].append(r)
    return rows


def _num(v):
    return None if v in ("", "None", None) else float(v)


def _marker_size(st):
    # 点径换算成 matplotlib 的 s（点^2）：按「点径/轴宽」比例还原视觉大小
    ratio = (st.get("marker") or {}).get("diameter_ratio") or 0.035
    return max(18.0, (ratio * 288.0) ** 2)


def main():
    data = load()
    for panel_no, kind in sorted(data):
        rows = data[(panel_no, kind)]
        st = STYLE[panel_no - 1] if panel_no - 1 < len(STYLE) else {}
        fig, ax = plt.subplots(figsize=(6.4, 4.0), dpi=140)

        if kind.startswith("bar"):
            vals = [_num(r["y"] if kind == "bar_v" else r["x"]) for r in rows]
            colors = st.get("colors") or ["#4a7fb5"] * len(rows)
            labels = [f"#{i}" for i in range(1, len(rows) + 1)]
            if kind == "bar_v":
                ax.bar(labels, vals, color=colors[:len(vals)], width=0.6)
            else:
                ax.barh(labels, vals, color=colors[:len(vals)], height=0.7)
                ax.invert_yaxis()
        else:
            marker = st.get("marker") or {}
            fills = marker.get("fills") or ["#4a7fb5"]
            groups = defaultdict(list)
            for r in rows:
                x, y = _num(r["x"]), _num(r["y"])
                if x is None or y is None:
                    continue
                groups[int(r.get("series") or 0)].append((x, y))
            for sidx, pts in sorted(groups.items()):
                ax.scatter([q[0] for q in pts], [q[1] for q in pts],
                           s=_marker_size(st),
                           facecolor=fills[sidx % len(fills)],
                           edgecolor=marker.get("edge_color", "#1b1b1b"),
                           linewidth=0.8 if marker.get("edge_present", True) else 0.0,
                           label=f"series {sidx + 1}", zorder=3)
            if len(groups) > 1:
                ax.legend(frameon=False, fontsize=9)
            trend = st.get("trend_line")
            if trend and trend.get("slope") is not None:
                xr = trend.get("x_range") or [-1, 1]
                xs = [xr[0], xr[1]]
                ax.plot(xs, [trend["slope"] * x + trend["intercept"] for x in xs],
                        color=trend.get("color", "#ff1f1f"),
                        linewidth=max(1.5, trend.get("width_px", 4) * 0.55),
                        solid_capstyle="round", zorder=4)
            for ref in st.get("reference_lines") or []:
                if ref.get("value") is None:
                    continue
                if ref.get("orientation") == "h":
                    ax.axhline(ref["value"], color="#9a9a9a", linestyle=DASH, linewidth=1.2, zorder=1)
                else:
                    ax.axvline(ref["value"], color="#9a9a9a", linestyle=DASH, linewidth=1.2, zorder=1)

        ax.set_title(f"panel {panel_no} - redraw (not original values)", fontsize=10)
        ax.grid(alpha=0.22, linestyle=":")
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        fig.tight_layout()
        out = HERE / f"preview_panel{panel_no}.png"
        fig.savefig(out)
        plt.close(fig)
        print("wrote", out)


if __name__ == "__main__":
    main()
