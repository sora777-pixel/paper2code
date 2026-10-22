"""由 paper2code 生成的同风格重绘脚本（F3）。

⚠️ 数据来自 data.csv —— 它是**从 PDF 位图反推的估算值**，不是论文原始数据。
   从 PDF 中读取或识别的数值不等同于原文献数值（位图反推，仅供绘图对照）

STYLE 里是**从原图实际测量**的样式（柱色/点径/描边/趋势线），换数据后仍保持同款风格。

用法：
    python draw_F3.py                 # 读同目录 data.csv，输出 preview_panel*.png
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
    "kind": "scatter",
    "orientation": "",
    "marker": {
      "diameter_px": 13.0,
      "diameter_ratio": 0.0366,
      "edge_color": "#020304",
      "edge_present": true,
      "fills": [
        "#6aaed6",
        "#45718c",
        "#325063",
        "#08306b"
      ],
      "n_series": 4
    },
    "trend_line": {
      "kind": "trend",
      "color": "#ff0000",
      "width_px": 5.0,
      "slope_px": -0.4059,
      "x_span_px": [
        158,
        483
      ],
      "slope": 0.499,
      "intercept": -4.9501,
      "x_range": [
        -16.442,
        -0.817
      ]
    },
    "reference_lines": [
      {
        "orientation": "h",
        "style": "dashed",
        "value": -11.012
      },
      {
        "orientation": "v",
        "style": "dashed",
        "value": -10.913
      }
    ]
  },
  {
    "kind": "scatter",
    "orientation": "",
    "marker": {
      "diameter_px": 13.0,
      "diameter_ratio": 0.0366,
      "edge_color": "#020405",
      "edge_present": true,
      "fills": [
        "#6aaed6",
        "#6b98b1",
        "#46748e",
        "#325264",
        "#08306b"
      ],
      "n_series": 5
    },
    "trend_line": {
      "kind": "trend",
      "color": "#ff0000",
      "width_px": 5.0,
      "slope_px": -0.3345,
      "x_span_px": [
        158,
        483
      ],
      "slope": 0.3486,
      "intercept": -6.8189,
      "x_range": [
        -16.442,
        -0.817
      ]
    },
    "reference_lines": [
      {
        "orientation": "h",
        "style": "dashed",
        "value": -10.929
      },
      {
        "orientation": "v",
        "style": "dashed",
        "value": -11.01
      }
    ]
  },
  {
    "kind": "scatter",
    "orientation": "",
    "marker": {
      "diameter_px": 13.0,
      "diameter_ratio": 0.0366,
      "edge_color": "#020406",
      "edge_present": true,
      "fills": [
        "#6aaed6",
        "#6994aa",
        "#203642",
        "#08306b"
      ],
      "n_series": 4
    },
    "trend_line": {
      "kind": "trend",
      "color": "#ff0000",
      "width_px": 5.0,
      "slope_px": -0.3394,
      "x_span_px": [
        688,
        1013
      ],
      "slope": 0.361,
      "intercept": -6.6968,
      "x_range": [
        -16.429,
        -0.952
      ]
    },
    "reference_lines": [
      {
        "orientation": "h",
        "style": "dashed",
        "value": -10.918
      },
      {
        "orientation": "v",
        "style": "dashed",
        "value": -10.952
      }
    ]
  },
  {
    "kind": "scatter",
    "orientation": "",
    "marker": {
      "diameter_px": 13.0,
      "diameter_ratio": 0.0367,
      "edge_color": "#020404",
      "edge_present": true,
      "fills": [
        "#6aaed6",
        "#6f98bc",
        "#5083a1",
        "#08306b"
      ],
      "n_series": 4
    },
    "trend_line": {
      "kind": "trend",
      "color": "#ff0000",
      "width_px": 5.0,
      "slope_px": -0.3798,
      "x_span_px": [
        1219,
        1544
      ],
      "slope": 0.4436,
      "intercept": -5.6962,
      "x_range": [
        -16.49,
        -0.865
      ]
    },
    "reference_lines": [
      {
        "orientation": "h",
        "style": "dashed",
        "value": -10.909
      },
      {
        "orientation": "v",
        "style": "dashed",
        "value": -11.01
      }
    ]
  },
  {
    "kind": "scatter",
    "orientation": "",
    "marker": {
      "diameter_px": 13.0,
      "diameter_ratio": 0.0366,
      "edge_color": "#020405",
      "edge_present": true,
      "fills": [
        "#6aaed6",
        "#507e9a",
        "#08306b",
        "#101b22"
      ],
      "n_series": 4
    },
    "trend_line": {
      "kind": "trend",
      "color": "#ff0000",
      "width_px": 5.0,
      "slope_px": -0.2913,
      "x_span_px": [
        1750,
        2075
      ],
      "slope": 0.4424,
      "intercept": -5.8877,
      "x_range": [
        -16.442,
        -0.817
      ]
    },
    "reference_lines": [
      {
        "orientation": "h",
        "style": "dashed",
        "value": -10.956
      },
      {
        "orientation": "v",
        "style": "dashed",
        "value": -10.913
      }
    ]
  },
  {
    "kind": "scatter",
    "orientation": "",
    "marker": {
      "diameter_px": 13.0,
      "diameter_ratio": 0.0366,
      "edge_color": "#020405",
      "edge_present": true,
      "fills": [
        "#6aaed6",
        "#477690",
        "#1f3340",
        "#08306b"
      ],
      "n_series": 4
    },
    "trend_line": {
      "kind": "trend",
      "color": "#ff0000",
      "width_px": 5.0,
      "slope_px": -0.1217,
      "x_span_px": [
        158,
        483
      ],
      "slope": 0.0389,
      "intercept": -9.7142,
      "x_range": [
        -16.442,
        -0.817
      ]
    },
    "reference_lines": [
      {
        "orientation": "h",
        "style": "dashed",
        "value": -10.923
      },
      {
        "orientation": "v",
        "style": "dashed",
        "value": -10.913
      }
    ]
  },
  {
    "kind": "scatter",
    "orientation": "",
    "marker": {
      "diameter_px": 13.0,
      "diameter_ratio": 0.0366,
      "edge_color": "#020405",
      "edge_present": true,
      "fills": [
        "#6aaed6",
        "#5991a6",
        "#426d86",
        "#32525e",
        "#08306b"
      ],
      "n_series": 5
    },
    "trend_line": {
      "kind": "trend",
      "color": "#ff0000",
      "width_px": 5.0,
      "slope_px": -0.2372,
      "x_span_px": [
        688,
        1014
      ],
      "slope": 0.4563,
      "intercept": -5.6981,
      "x_range": [
        -16.429,
        -0.905
      ]
    },
    "reference_lines": [
      {
        "orientation": "h",
        "style": "dashed",
        "value": -11.019
      },
      {
        "orientation": "v",
        "style": "dashed",
        "value": -10.952
      }
    ]
  },
  {
    "kind": "scatter",
    "orientation": "",
    "marker": {
      "diameter_px": 13.0,
      "diameter_ratio": 0.0367,
      "edge_color": "#020203",
      "edge_present": true,
      "fills": [
        "#6aaed6",
        "#5b96b5",
        "#426c85",
        "#243c49",
        "#08306b"
      ],
      "n_series": 5
    },
    "trend_line": {
      "kind": "trend",
      "color": "#ff0000",
      "width_px": 5.0,
      "slope_px": -0.2709,
      "x_span_px": [
        1219,
        1544
      ],
      "slope": 0.3146,
      "intercept": -6.8065,
      "x_range": [
        -16.49,
        -0.865
      ]
    },
    "reference_lines": [
      {
        "orientation": "h",
        "style": "dashed",
        "value": -11.011
      },
      {
        "orientation": "v",
        "style": "dashed",
        "value": -10.962
      }
    ]
  },
  {
    "kind": "scatter",
    "orientation": "",
    "marker": {
      "diameter_px": 13.0,
      "diameter_ratio": 0.0366,
      "edge_color": "#020405",
      "edge_present": true,
      "fills": [
        "#6aaed6",
        "#5183a2",
        "#08306b"
      ],
      "n_series": 3
    },
    "trend_line": {
      "kind": "trend",
      "color": "#ff0000",
      "width_px": 5.0,
      "slope_px": -0.2985,
      "x_span_px": [
        1750,
        2075
      ],
      "slope": 0.3022,
      "intercept": -7.241,
      "x_range": [
        -16.435,
        -0.885
      ]
    },
    "reference_lines": [
      {
        "orientation": "h",
        "style": "dashed",
        "value": -10.99
      },
      {
        "orientation": "v",
        "style": "dashed",
        "value": -10.933
      }
    ]
  },
  {
    "kind": "scatter",
    "orientation": "",
    "marker": {
      "diameter_px": 13.0,
      "diameter_ratio": 0.0366,
      "edge_color": "#020304",
      "edge_present": true,
      "fills": [
        "#6aaed6",
        "#35586b",
        "#08306b"
      ],
      "n_series": 3
    },
    "trend_line": {
      "kind": "trend",
      "color": "#ff0000",
      "width_px": 5.0,
      "slope_px": -0.1125,
      "x_span_px": [
        158,
        483
      ],
      "slope": 0.1025,
      "intercept": -9.6962,
      "x_range": [
        -16.442,
        -0.817
      ]
    },
    "reference_lines": [
      {
        "orientation": "h",
        "style": "dashed",
        "value": -10.921
      },
      {
        "orientation": "v",
        "style": "dashed",
        "value": -10.913
      }
    ]
  },
  {
    "kind": "scatter",
    "orientation": "",
    "marker": {
      "diameter_px": 13.0,
      "diameter_ratio": 0.0366,
      "edge_color": "#020405",
      "edge_present": true,
      "fills": [
        "#6aaed6",
        "#497894",
        "#08306b"
      ],
      "n_series": 3
    },
    "trend_line": {
      "kind": "trend",
      "color": "#ff0000",
      "width_px": 5.0,
      "slope_px": -0.0457,
      "x_span_px": [
        688,
        1014
      ],
      "slope": 0.6357,
      "intercept": -56.3327,
      "x_range": [
        -16.429,
        -0.905
      ]
    },
    "reference_lines": [
      {
        "orientation": "h",
        "style": "dashed",
        "value": -9.868
      },
      {
        "orientation": "v",
        "style": "dashed",
        "value": -10.952
      }
    ]
  },
  {
    "kind": "scatter",
    "orientation": "",
    "marker": {
      "diameter_px": 13.0,
      "diameter_ratio": 0.0367,
      "edge_color": "#020304",
      "edge_present": true,
      "fills": [
        "#6aaed6",
        "#1d303a",
        "#08306b"
      ],
      "n_series": 3
    },
    "trend_line": {
      "kind": "trend",
      "color": "#ff0000",
      "width_px": 5.0,
      "slope_px": -0.3948,
      "x_span_px": [
        1219,
        1544
      ],
      "slope": 0.4357,
      "intercept": -5.5279,
      "x_range": [
        -16.49,
        -0.865
      ]
    },
    "reference_lines": [
      {
        "orientation": "h",
        "style": "dashed",
        "value": -10.957
      },
      {
        "orientation": "v",
        "style": "dashed",
        "value": -10.962
      }
    ]
  },
  {
    "kind": "scatter",
    "orientation": "",
    "marker": {
      "diameter_px": 13.0,
      "diameter_ratio": 0.0366,
      "edge_color": "#020406",
      "edge_present": true,
      "fills": [
        "#6aaed6",
        "#6a9cb4",
        "#3e677e",
        "#08306b"
      ],
      "n_series": 4
    },
    "trend_line": {
      "kind": "trend",
      "color": "#ff0000",
      "width_px": 5.0,
      "slope_px": -0.3593,
      "x_span_px": [
        1750,
        2075
      ],
      "slope": 0.3439,
      "intercept": -6.6627,
      "x_range": [
        -16.435,
        -0.885
      ]
    },
    "reference_lines": [
      {
        "orientation": "h",
        "style": "dashed",
        "value": -10.972
      },
      {
        "orientation": "v",
        "style": "dashed",
        "value": -10.981
      }
    ]
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
