"""由 paper2code 生成的同风格重绘脚本（F6）。

图表类型：**纵向小提琴图 + 右侧 colorbar**（SHAP 摘要图）。
   '从 PDF 中读取或识别的数值不等同于原文献数值（位图反推，仅供绘图对照）'

原图是位图，逐点 SHAP 值无法还原；脚本改用原图逐行彩色分布的几何范围
生成小提琴轮廓，并保留原图配色、布局与 colorbar。数据来自 data.csv 时优先使用；
没有 data.csv 时只画几何轮廓，不编造论文数值。
"""
import csv
import json
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap

HERE = Path(__file__).parent
DISCLAIMER = '从 PDF 中读取或识别的数值不等同于原文献数值（位图反推，仅供绘图对照）'
COLORS_HIGH_TO_LOW = ["#fe005c", "#9c23ad", "#0083f8"]
CMAP = LinearSegmentedColormap.from_list("paper_colorbar", list(reversed(COLORS_HIGH_TO_LOW)))
FEATURES = ["feature_1", "feature_2", "feature_3", "feature_4", "feature_5", "feature_6", "feature_7", "feature_8", "feature_9", "feature_10", "feature_11", "feature_12", "feature_13", "feature_14", "feature_15", "feature_16", "feature_17", "feature_18", "feature_19", "feature_20"]
ROWS = [{"y": 62, "groups": [{"x0": 4.779, "x1": 5.025, "color": "#973aba"}]}, {"y": 116, "groups": [{"x0": -0.803, "x1": -0.631, "color": "#aa34b5"}]}, {"y": 168, "groups": [{"x0": 0.291, "x1": 0.525, "color": "#2d7cec"}]}, {"y": 222, "groups": [{"x0": 0.254, "x1": 0.475, "color": "#7a5cd3"}]}, {"y": 276, "groups": [{"x0": -0.484, "x1": -0.189, "color": "#af25ad"}, {"x0": -0.078, "x1": 0.156, "color": "#fa256e"}]}, {"y": 329, "groups": [{"x0": -0.078, "x1": 0.033, "color": "#f65083"}, {"x0": 0.057, "x1": 0.328, "color": "#d71696"}]}, {"y": 382, "groups": [{"x0": -0.189, "x1": 0.156, "color": "#d72c88"}]}, {"y": 436, "groups": [{"x0": -0.139, "x1": 0.07, "color": "#9c41bd"}]}, {"y": 489, "groups": [{"x0": -0.053, "x1": 0.131, "color": "#b32ba7"}]}, {"y": 543, "groups": [{"x0": -0.041, "x1": 0.033, "color": "#c831a5"}]}, {"y": 596, "groups": [{"x0": -0.041, "x1": 0.033, "color": "#7267d7"}]}, {"y": 649, "groups": [{"x0": -0.041, "x1": 0.033, "color": "#6a72de"}]}, {"y": 704, "groups": [{"x0": -0.041, "x1": 0.033, "color": "#db2e9b"}]}, {"y": 756, "groups": [{"x0": -0.041, "x1": 0.033, "color": "#8266d5"}]}, {"y": 810, "groups": [{"x0": -0.041, "x1": 0.033, "color": "#a43cb4"}]}, {"y": 863, "groups": [{"x0": -0.041, "x1": 0.033, "color": "#9641b9"}]}, {"y": 918, "groups": [{"x0": -0.041, "x1": 0.033, "color": "#c92fa5"}]}, {"y": 970, "groups": [{"x0": -0.041, "x1": 0.033, "color": "#8c61cf"}]}, {"y": 1024, "groups": [{"x0": -0.041, "x1": 0.033, "color": "#da2d9a"}]}, {"y": 1078, "groups": [{"x0": -0.041, "x1": 0.033, "color": "#d82195"}]}]
X_LIM = [-1.0, 5.0]
COLORBAR_LABEL = "Feature value"
X_LABEL = "SHAP value (impact on model output)"


def _labels():
    """可选 sidecar：labels.json 按原图行顺序给出特征名（OCR 不可用时的精确覆盖）。"""
    p = HERE / "labels.json"
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [str(x) for x in data]
        except Exception:
            pass
    return FEATURES


def _load():
    path = HERE / "data.csv"
    if not path.exists():
        return {}, True
    groups = defaultdict(list)
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            v = r.get("value")
            if v not in ("", None):
                groups[r["feature"]].append(float(v))
    return groups, False


def _violin(ax, y, vals, color, width=0.42):
    vals = np.asarray(vals, dtype=float)
    if vals.size < 2 or float(np.nanmax(vals) - np.nanmin(vals)) < 1e-9:
        return
    vp = ax.violinplot([vals], positions=[y], vert=False, widths=width,
                       showmeans=False, showextrema=False)
    body = vp["bodies"][0]
    body.set_facecolor(color)
    body.set_edgecolor(color)
    body.set_alpha(0.85)


def main():
    data, demo = _load()
    rng = np.random.default_rng(20261022)
    if data:
        labels = list(data.keys())
        n = len(labels)
    else:
        labels = _labels()[:len(ROWS)] if ROWS else _labels()
        n = len(labels)

    fig, ax = plt.subplots(figsize=(8.2, 7.8), dpi=150)
    if data:
        means = []
        for i, name in enumerate(labels):
            vals = np.asarray(data[name], dtype=float)
            means.append(float(np.nanmean(vals)))
            color = CMAP((means[-1] - X_LIM[0]) / max(X_LIM[1] - X_LIM[0], 1e-9))
            _violin(ax, i, vals, color)
        vmin, vmax = X_LIM
    else:
        for i, row in enumerate(ROWS):
            for g in (row.get("groups") or []):
                x0, x1 = float(g.get("x0", 0.0)), float(g.get("x1", 0.0))
                if x1 - x0 < 1e-6:
                    x0, x1 = x0 - 0.05, x1 + 0.05
                center = (x0 + x1) / 2.0
                scale = max((x1 - x0) / 5.0, 0.02)
                vals = np.clip(rng.normal(center, scale, 260), x0, x1)
                _violin(ax, i, vals, g.get("color") or "#888888")
        vmin, vmax = X_LIM

    ax.set_yticks(range(n))
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    ax.axvline(0, color="#8a8a8a", linewidth=1.0, zorder=1)
    ax.grid(axis="y", alpha=0.18, linestyle=":")
    ax.set_xlabel(X_LABEL)
    ax.set_xlim(*X_LIM)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)

    sm = plt.cm.ScalarMappable(cmap=CMAP, norm=plt.Normalize(vmin=vmin, vmax=vmax))
    cbar = fig.colorbar(sm, ax=ax, pad=0.02, aspect=40, fraction=0.035)
    cbar.set_label(COLORBAR_LABEL, rotation=90, labelpad=12)
    cbar.set_ticks([vmin, vmax])
    cbar.set_ticklabels(["Low", "High"])

    if demo:
        ax.set_title("style preview (violin geometry from bitmap; not paper values)", fontsize=10)
    fig.tight_layout()
    out = HERE / "preview_F6.png"
    fig.savefig(out)
    print("wrote", out)


if __name__ == "__main__":
    main()
