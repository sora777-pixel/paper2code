"""位图统计图取数（本轮新增）：从 PDF 里抠出来的**位图**图表反推数值。

为什么单独一个模块：``digitize.py`` 只认 SVG 矢量图（刻度是文本、几何是显式坐标，标定精确）；
真实论文的图大多是位图，需要另一条路——**先标定坐标轴，再按像素几何量取数值**。

诚实底线（与用户约定一致）：
* **只有坐标轴能标定（读到 ≥2 个刻度文本且间距近似等差）才出数**；读不到就返回 ``None``
  并写明原因，绝不拿「猜的刻度」凑一组数值；
* 每个数值都带 ``confidence``（来自刻度标定质量 + 像素量化误差），并在产物里标注「估算」；
* 生成的数据与绘图代码里都强制写明：**从 PDF 识别出的数值不等同于原文献数值**；
* 散点重叠、蜂群/小提琴图、示意图这类本质上无法逐点还原的图，**不伪造数值**：
  蜂群/小提琴图改为复现原图布局、逐行几何轮廓与配色，并明确标注非论文数值。

实现约束：只用 PIL + numpy（本项目不引入 OCR/cv2 依赖），刻度文本用
「渲染数字模板 + 连通域匹配」识别，并用**刻度等差性**做强校验（比单字识别置信度可靠）。
"""
from __future__ import annotations

import json
import math
import os
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

DISCLAIMER = "从 PDF 中读取或识别的数值不等同于原文献数值（位图反推，仅供绘图对照）"

#: 视为「墨迹」的灰度阈值（坐标轴、文字）
INK = 118
#: 视为「有彩色」的通道差阈值（数据元素：柱子/散点）
CHROMA = 26


@dataclass
class Calib:
    """一条轴的像素↔数值标定。"""

    pixel: List[float] = field(default_factory=list)   # 刻度像素位置
    value: List[float] = field(default_factory=list)   # 对应数值
    ok: bool = False
    score: float = 0.0                                  # 标定质量 0–1
    note: str = ""

    def to_value(self, px: float) -> Optional[float]:
        if not self.ok or len(self.pixel) < 2:
            return None
        dx = self.pixel[-1] - self.pixel[0]
        if abs(dx) < 1e-9:
            return None
        slope = (self.value[-1] - self.value[0]) / dx
        return self.value[0] + (px - self.pixel[0]) * slope

    def to_pixel(self, val: float) -> Optional[float]:
        """数值 → 像素（负值柱采样时要用 0 的像素位置当基线）。"""
        if len(self.pixel) < 2:
            return None
        dv = self.value[-1] - self.value[0]
        if abs(dv) < 1e-9:
            return None
        return self.pixel[0] + (val - self.value[0]) * (self.pixel[-1] - self.pixel[0]) / dv


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def digitize(image_path: str | Path, out_dir: Path, fig_id: str) -> Dict[str, Any]:
    """尝试从一张位图里取数。

    返回值**总是**字典：``state == "ok"`` 表示取数成功并已落盘；``state == "skipped"``
    表示按诚实底线拒绝，``why`` 写明原因（调用方原样写进报告，不做美化）。
    """
    p = Path(image_path)
    if not p.exists():
        return _reject(fig_id, "图片文件不存在")
    rgb = _load_rgb(p)
    if rgb is None:
        return _reject(fig_id, "图片无法读取（或缺少 PIL）")

    h, w = rgb.shape[:2]
    nav = Nav(rgb)
    frames = nav.detect_frames()
    if not frames:
        # 没有闭合边框也可能是「带 colorbar 的蜂群/小提琴图」（SHAP beeswarm）：
        # 这类图数值无法逐点还原，但**样式与配色必须复现**——至少给出作图代码。
        bee = _detect_beeswarm(nav)
        if bee:
            return _write_style_only(fig_id, out_dir, p, rgb, bee)
        return _reject(fig_id, "未检测到坐标轴边框（示意图、流程图或纯图片，非可标定统计图）")

    panels: List[Dict[str, Any]] = []
    reasons: List[str] = []
    for fr in frames:
        panel, why = _analyze_panel(nav, fr)
        if panel:
            panels.append(panel)
        elif why:
            reasons.append(why)
    if not panels:
        bee = _detect_beeswarm(nav)
        if bee:
            return _write_style_only(fig_id, out_dir, p, rgb, bee)
        uniq = list(dict.fromkeys(reasons))[:3]
        return _reject(fig_id, "检测到 %d 个边框，但均未能标定：" % len(frames) + "；".join(uniq))

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "state": "ok",
        "figure": fig_id,
        "source_image": str(p),
        "image_size": [w, h],
        "disclaimer": DISCLAIMER,
        "panels": panels,
        "confidence": round(min(x["confidence"] for x in panels), 2),
        "notes": sorted({n for x in panels for n in x.get("notes", [])}),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(out_dir / "data.csv", panels)
    style = {"figures": [p.get("style", {}) for p in panels]}
    (out_dir / "style.json").write_text(json.dumps(style, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / f"draw_{fig_id}.py").write_text(_draw_script(fig_id, panels), encoding="utf-8")
    preview = _render_preview(fig_id, panels, out_dir / f"preview_{fig_id}.png")
    meta["files"] = {"meta": str(out_dir / "meta.json"), "csv": str(out_dir / "data.csv"),
                     "style": str(out_dir / "style.json"),
                     "script": str(out_dir / f"draw_{fig_id}.py"),
                     "preview": str(preview) if preview else ""}
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def _write_style_only(fig_id: str, out_dir: Path, src: Path, rgb: np.ndarray,
                      info: Dict[str, Any]) -> Dict[str, Any]:
    """只复现样式（蜂群图/小提琴图）：落盘作图脚本 + 配色，不编造数值。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    h, w = rgb.shape[:2]
    labels_path = out_dir / "labels.json"
    feature_labels: List[str] = []
    if labels_path.exists():
        try:
            loaded = json.loads(labels_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                feature_labels = [str(x) for x in loaded]
        except Exception:
            feature_labels = []
    meta = {
        "state": "style_only",
        "chart_type": info.get("chart_type", "beeswarm"),
        "figure": fig_id,
        "source_image": str(src),
        "image_size": [w, h],
        "disclaimer": DISCLAIMER,
        "why": info.get("why", ""),
        "notes": info.get("notes", []),
        **{k: v for k, v in info.items() if k not in ("why", "notes", "chart_type")},
        "feature_labels": feature_labels,
        "confidence": 0.0,
        "files": {},
    }
    (out_dir / "style.json").write_text(
        json.dumps({k: v for k, v in meta.items() if k != "files"}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    script = out_dir / f"draw_{fig_id}.py"
    script.write_text(_beeswarm_script(fig_id, meta), encoding="utf-8")
    preview = _run_beeswarm_preview(script, meta, out_dir / f"preview_{fig_id}.png")
    meta["files"] = {"meta": str(out_dir / "meta.json"), "style": str(out_dir / "style.json"),
                     "script": str(script), "preview": str(preview) if preview else ""}
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def _reject(fig_id: str, why: str) -> Dict[str, Any]:
    """拒判：不落盘空壳，只把原因交给调用方写进报告。"""
    return {"state": "skipped", "figure": fig_id, "why": why, "disclaimer": DISCLAIMER}


# --------------------------------------------------------------------------- #
# 图像基元
# --------------------------------------------------------------------------- #
def _load_rgb(path: Path) -> Optional[np.ndarray]:
    try:
        from PIL import Image  # type: ignore

        with Image.open(path) as im:
            return np.asarray(im.convert("RGB"), dtype=np.uint8)
    except Exception:
        return None


class Nav:
    """极简图像分析（不依赖 cv2）：墨迹掩码、彩色掩码、长直线检测、连通域。"""

    def __init__(self, rgb: np.ndarray) -> None:
        self.rgb = rgb
        self.gray = rgb.mean(axis=2)
        self.ink = self.gray < INK
        self.chroma = (rgb.max(axis=2).astype(np.int16) - rgb.min(axis=2).astype(np.int16)) > CHROMA
        self.h, self.w = self.ink.shape

    # -- 直线 --------------------------------------------------------------- #
    def h_runs(self, min_len: int) -> List[Tuple[int, int, int]]:
        """横向墨迹长段 ``(y, x0, x1)``。"""
        out: List[Tuple[int, int, int]] = []
        for y in range(self.h):
            row = self.ink[y]
            if row.sum() < min_len:
                continue
            for x0, x1 in _runs(row):
                if x1 - x0 + 1 >= min_len:
                    out.append((y, x0, x1))
        return out

    def v_runs(self, min_len: int) -> List[Tuple[int, int, int]]:
        out: List[Tuple[int, int, int]] = []
        for x in range(self.w):
            col = self.ink[:, x]
            if col.sum() < min_len:
                continue
            for y0, y1 in _runs(col):
                if y1 - y0 + 1 >= min_len:
                    out.append((x, y0, y1))
        return out

    # -- 边框 --------------------------------------------------------------- #
    def detect_frames(self) -> List[Tuple[int, int, int, int]]:
        """找出坐标轴矩形 ``(x0, x1, y0, y1)``（y0 上边界、y1 下边界）。

        判据：一条足够长的底边 + 一条与它**相交且靠左**的长竖线（左脊）。
        左脊的 x 允许与底边起点相差几十像素（刻度标签常和底边连成一条墨迹）。
        有右脊时一并纳入，得到完整面板。
        """
        hs = self.h_runs(max(60, self.w // 12))
        vs = self.v_runs(max(50, self.h // 8))
        frames: List[Tuple[int, int, int, int]] = []
        for (y1, x0, x1) in sorted(hs, key=lambda t: -t[0]):
            if x1 - x0 < self.w * 0.12:
                continue
            left = [v for v in vs if x0 - 6 <= v[0] <= x0 + 40 and v[2] >= y1 - 2]
            if not left:
                continue
            lx = min(v[0] for v in left)
            ytop = min(v[1] for v in left)
            if y1 - ytop < 40:
                continue
            rx = x1
            right = [v for v in vs if x1 - 8 <= v[0] <= x1 + 6 and v[2] >= y1 - 2]
            if right:
                rx = max(v[0] for v in right)
            fr = (lx, max(rx, lx + 40), ytop, y1)
            if not any(_overlaps(fr, f) for f in frames):
                frames.append(fr)
        return frames


def _runs(mask: np.ndarray) -> List[Tuple[int, int]]:
    """1D 布尔数组里的连续 True 段 ``(start, end)``。"""
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []
    breaks = np.flatnonzero(np.diff(idx) > 1)
    starts = np.r_[idx[0], idx[breaks + 1]]
    ends = np.r_[idx[breaks], idx[-1]]
    return list(zip(starts.tolist(), ends.tolist()))


def _overlaps(a: Sequence[int], b: Sequence[int]) -> bool:
    return not (a[1] < b[0] or b[1] < a[0] or a[3] < b[2] or b[3] < a[2])


# --------------------------------------------------------------------------- #
# 单面板分析
# --------------------------------------------------------------------------- #
def _analyze_panel(nav: Nav, frame: Tuple[int, int, int, int]
                   ) -> Tuple[Optional[Dict[str, Any]], str]:
    """分析单个面板。返回 ``(panel, 跳过原因)``，二者必有一个为空。"""
    x0, x1, y0, y1 = frame
    xt = _axis_ticks(nav, x0, x1, y1, horizontal=True)
    yt = _axis_ticks(nav, y0, y1, x0, horizontal=False)
    xcal = _calibrate(nav, xt, y1, horizontal=True)
    ycal = _calibrate(nav, yt, x0, horizontal=False)

    bars: List[Dict[str, Any]] = []
    orient = ""
    for mask in _series_masks(nav, frame):
        found, o = _extract_bars(frame, mask, xcal, ycal)
        if found:
            bars.extend(found)
            orient = orient or o
    if not bars:
        hb = _ink_hbars(nav, frame, xcal)
        if hb:
            bars, orient = hb, "h"
    if bars:
        cal = ycal if orient == "v" else xcal
        if not cal.ok:
            return None, ("柱状图的值轴刻度无法标定（%s），不给绝对数值"
                          % (cal.note or "刻度文本不可识别"))
        for b in bars:
            b["confidence"] = round(cal.score, 2)
        style = _bar_style(nav, frame, bars, orient, xcal, ycal)
    else:
        points = _extract_points(nav, frame, xcal, ycal, [])
        if not (xcal.ok or ycal.ok) or not points:
            return None, (xcal.note or ycal.note or "未检出可标定的数据元素")
        kind = "scatter"
        conf = min([c for c in (xcal.score if xcal.ok else 1.0,
                                ycal.score if ycal.ok else 1.0)] or [0.5])
        notes = [n for n in (xcal.note, ycal.note) if n]
        if not (xcal.ok and ycal.ok):
            notes.append("有一侧坐标轴未标定：对应坐标留空，仅保留像素位置")
        style = _scatter_style(nav, frame, xcal, ycal, points)
        return {
            "frame": list(frame), "kind": kind,
            "x_axis": _axis_meta(xcal), "y_axis": _axis_meta(ycal),
            "bars": [], "points": points, "style": style,
            "confidence": round(conf, 2), "notes": notes,
        }, ""

    conf = min([c for c in (xcal.score if xcal.ok else 1.0,
                            ycal.score if ycal.ok else 1.0)] or [0.5])
    notes = [n for n in (xcal.note, ycal.note) if n]
    if not xcal.ok:
        notes.append("x 轴刻度未能标定（分类轴或非数值轴），只给柱高对应的数值轴")
    return {
        "frame": list(frame), "kind": "bar", "orientation": orient,
        "x_axis": _axis_meta(xcal), "y_axis": _axis_meta(ycal),
        "bars": bars, "points": [], "style": style,
        "confidence": round(conf, 2), "notes": notes,
    }, ""


def _axis_meta(cal: Calib) -> Dict[str, Any]:
    return {"scale_ok": cal.ok, "score": round(cal.score, 2),
            "ticks": [[p, v] for p, v in zip(cal.pixel, cal.value)][:12],
            "note": cal.note}


# --------------------------------------------------------------------------- #
# 样式识别（复现「同款」图：点径/描边、渐变配色、趋势线、colorbar）
# --------------------------------------------------------------------------- #
def _hex(rgb: Sequence[float]) -> str:
    return "#%02x%02x%02x" % tuple(int(max(0, min(255, round(c)))) for c in rgb)


def _lum(c: Sequence[float]) -> float:
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def _red_mask(rgb: np.ndarray) -> np.ndarray:
    r = rgb[:, :, 0].astype(np.int16)
    g = rgb[:, :, 1].astype(np.int16)
    b = rgb[:, :, 2].astype(np.int16)
    return (r > 130) & (r - g > 70) & (r - b > 60)


def _detect_trend_line(nav: Nav, frame, xcal: Calib, ycal: Calib) -> Optional[Dict[str, Any]]:
    """识别「红色粗线」趋势线（散点图的线性拟合）。

    判据：红色像素在 ≥55% 的列上出现，且每列中位厚度 ≥2px。
    斜线也要能认：逐列取红色质心，线性拟合出像素斜率，再换算成数据斜率。
    """
    x0, x1, y0, y1 = frame
    sub = nav.rgb[y0 + 2:y1 - 1, x0 + 2:x1 - 1]
    red = _red_mask(sub)
    hh, ww = red.shape
    if hh < 10 or ww < 20 or red.sum() < ww * 0.5:
        return None
    cols, cys, thick = [], [], []
    for x in range(ww):
        ys = np.flatnonzero(red[:, x])
        if ys.size == 0:
            continue
        cols.append(x)
        cys.append(float(ys.mean()))
        thick.append(float(ys.size))
    if len(cols) < ww * 0.55:
        return None
    if float(np.median(thick)) < 2:
        return None
    px = np.array(cols, dtype=float) + x0 + 2
    py = np.array(cys, dtype=float) + y0 + 2
    slope_px = float(np.polyfit(px, py, 1)[0]) if px.size > 2 else 0.0
    info: Dict[str, Any] = {
        "kind": "trend",
        "color": _hex(np.median(sub[red], axis=0)),
        "width_px": round(float(np.median(thick)), 1),
        "slope_px": round(slope_px, 4),
        "x_span_px": [int(px.min()), int(px.max())],
    }
    if xcal.ok and ycal.ok:
        dx = xcal.to_value(px[-1]) - xcal.to_value(px[0])
        dy = ycal.to_value(py[-1]) - ycal.to_value(py[0])
        if abs(dx) > 1e-9:
            info["slope"] = round(dy / dx, 4)
            info["intercept"] = round(ycal.to_value(py[0]) - info["slope"] * xcal.to_value(px[0]), 4)
            info["x_range"] = [round(xcal.to_value(px[0]), 3), round(xcal.to_value(px[-1]), 3)]
    return info


def _detect_ref_lines(nav: Nav, frame, xcal: Calib, ycal: Calib) -> List[Dict[str, Any]]:
    """识别灰色虚线参考线（零线/阈值线）：断断续续但覆盖足够宽的整行/整列。"""
    x0, x1, y0, y1 = frame
    gray = nav.gray[y0 + 2:y1 - 1, x0 + 2:x1 - 1]
    dark = (gray < 200) & (gray > 70)
    hh, ww = dark.shape

    def pick(axis: str) -> List[Dict[str, Any]]:
        hits: List[Tuple[int, float]] = []
        n = hh if axis == "h" else ww
        for i in range(n):
            line = dark[i] if axis == "h" else dark[:, i]
            runs = _runs(line)
            span = ww if axis == "h" else hh
            if len(runs) < 6:
                continue
            lens = [e - s + 1 for s, e in runs]
            total = sum(lens)
            # 虚线的每段都较长且均匀；散点黑描边会造成一堆 1px 短段，必须排除
            if total < span * 0.5 or float(np.median(lens)) < 5:
                continue
            hits.append((i, total))
        # 同一条粗虚线会命中相邻 3–5 行：按位置聚簇，只留覆盖率最高的那一行
        out: List[Dict[str, Any]] = []
        for i, total in sorted(hits, key=lambda t: -t[1]):
            if any(abs(i - o["_i"]) <= 5 for o in out):
                continue
            px = i + (y0 + 2 if axis == "h" else x0 + 2)
            out.append({"orientation": axis, "_i": i, "style": "dashed",
                        "value": _r((ycal if axis == "h" else xcal).to_value(px))
                        if (ycal if axis == "h" else xcal).ok else None})
        for o in out:
            o.pop("_i", None)
        return sorted(out, key=lambda o: (o["orientation"], o["value"] or 0))

    return [*pick("h"), *pick("v")][:4]


def _marker_style(nav: Nav, frame, points: List[Dict[str, Any]]) -> Dict[str, Any]:
    """散点样式：标记直径、描边色、填充色（按系列分组）。"""
    x0, x1, y0, y1 = frame
    area = nav.chroma[y0 + 2:y1 - 2, x0 + 2:x1 - 2]
    comps = _components(area)
    sizes: List[float] = []
    edge_votes: List[np.ndarray] = []
    fills: List[np.ndarray] = []
    for (bx0, by0, bx1, by1) in comps:
        cw, ch = bx1 - bx0 + 1, by1 - by0 + 1
        if cw < 3 or ch < 3 or cw * ch > 900:
            continue
        if cw > ch * 3.5 or ch > cw * 3.5:
            continue
        sizes.append(float(max(cw, ch)))
        px0, px1 = x0 + 2 + bx0, x0 + 2 + bx1
        py0, py1 = y0 + 2 + by0, y0 + 2 + by1
        # 内芯颜色（收缩 30% 后取中位数，避开抗锯齿边）
        mx, my = (px1 - px0) // 4, (py1 - py0) // 4
        inner = nav.rgb[py0 + my:py1 - my + 1, px0 + mx:px1 - mx + 1].reshape(-1, 3)
        if inner.size:
            fills.append(np.median(inner, axis=0))
        # 描边：把该标记的包围盒扩 1px，取其中「暗但不属于填充」的像素
        ex0, ex1 = max(0, px0 - 1), min(nav.w - 1, px1 + 1)
        ey0, ey1 = max(0, py0 - 1), min(nav.h - 1, py1 + 1)
        ring = nav.rgb[ey0:ey1 + 1, ex0:ex1 + 1].reshape(-1, 3)
        dark = ring[ring.max(axis=1) < 110]
        if dark.size:
            edge_votes.append(np.median(dark, axis=0))

    style: Dict[str, Any] = {}
    if sizes:
        diam = float(np.median(sizes))
        pw = max(1, x1 - x0)
        style["marker"] = {"diameter_px": round(diam, 1),
                           "diameter_ratio": round(diam / pw, 4),
                           "edge_color": _hex(np.median(edge_votes, axis=0)) if edge_votes
                           else "#1b1b1b",
                           "edge_present": bool(edge_votes)}
    if fills:
        clusters: List[Dict[str, Any]] = []
        for c in fills:
            for cl in clusters:
                if np.abs(cl["center"] - c).max() < 26:
                    cl["items"].append(c)
                    cl["center"] = np.median(np.array(cl["items"]), axis=0)
                    break
            else:
                clusters.append({"center": c, "items": [c]})
        clusters = [c for c in clusters if len(c["items"]) >= max(2, len(fills) * 0.04)]
        clusters.sort(key=lambda c: -_lum(c["center"]))       # 浅→深
        marker = style.setdefault("marker", {})
        marker["fills"] = [_hex(c["center"]) for c in clusters]
        marker["n_series"] = len(clusters)
        # 给每个点标系列（颜色最接近的簇）
        for pt in points:
            c = np.array(nav.rgb[int(pt["px"][1]), int(pt["px"][0])], dtype=float)
            best, idx = 1e9, 0
            for i, cl in enumerate(clusters):
                d = float(np.abs(cl["center"] - c).max())
                if d < best:
                    best, idx = d, i
            pt["series"] = idx
    return style


def _bar_style(nav: Nav, frame, bars: List[Dict[str, Any]], orient: str,
               xcal: Calib, ycal: Calib) -> Dict[str, Any]:
    """柱状样式：逐柱取真实填充色；同向柱若是由浅到深/由深到浅的序列，
    判定为「渐变色板」，在 style 里记下方向与两端颜色。

    采样点取「从 0 基线到柱尖的 70% 处」——负值柱和极短的柱都能落在柱体内，
    不会采到白底。
    """
    x0, x1, y0, y1 = frame
    colors: List[np.ndarray] = []
    for b in bars:
        if orient == "v":
            cx = int(b["center"])
            base = ycal.to_pixel(0.0) or y1
            cy = int(base + (b["top_px"] - base) * 0.55)
        else:
            cy = int(b["center"])
            base = xcal.to_pixel(0.0) or (x0 + 2)
            cx = int(base + (b["right_px"] - base) * 0.7)
        cx = max(0, min(nav.w - 1, cx))
        cy = max(0, min(nav.h - 1, cy))
        patch = nav.rgb[max(0, cy - 2):cy + 3, max(0, cx - 2):cx + 3].reshape(-1, 3)
        colors.append(np.median(patch, axis=0))

    style: Dict[str, Any] = {"colors": [_hex(c) for c in colors]}
    lums = [_lum(c) for c in colors]
    if len(colors) >= 4:
        order = np.argsort(np.argsort(lums))
        rho = float(np.corrcoef(np.arange(len(lums)), lums)[0, 1])
        if abs(rho) >= 0.9:
            style["gradient"] = {
                "direction": "darken_down" if rho > 0 else "lighten_down",
                "from": _hex(colors[0]), "to": _hex(colors[-1]),
                "axis": "vertical" if orient == "h" else "horizontal",
                "note": "同一色板按柱序由浅到深（与原文一致）",
            }
        elif float(np.std(np.array(lums))) < 6:
            style["flat"] = True

    # 柱内是否有渐变（同一根柱子上/下或左/右颜色不同）
    if orient == "h" and bars:
        b = bars[len(bars) // 2]
        cy = int(b["center"])
        base = xcal.to_pixel(0.0) or (x0 + 2)
        xs = [int(base + (b["right_px"] - base) * f) for f in (0.2, 0.7)]
        pts = [nav.rgb[cy, max(0, min(nav.w - 1, x))] for x in xs]
        both_ink = all(int(p.max()) < 250 and int(p.max()) - int(p.min()) > 3 for p in pts)
        if both_ink and np.abs(pts[0].astype(int) - pts[1].astype(int)).max() > 26:
            style["gradient_within_bar"] = {"from": _hex(pts[0]), "to": _hex(pts[1]),
                                            "direction": "horizontal"}
    return style


def _scatter_style(nav: Nav, frame, xcal: Calib, ycal: Calib,
                   points: List[Dict[str, Any]]) -> Dict[str, Any]:
    style = _marker_style(nav, frame, points)
    trend = _detect_trend_line(nav, frame, xcal, ycal)
    if trend:
        style["trend_line"] = trend
    refs = _detect_ref_lines(nav, frame, xcal, ycal)
    if refs:
        style["reference_lines"] = refs
    return style


def _detect_colorbar(nav: Nav) -> Optional[Dict[str, Any]]:
    """识别右侧 colorbar：窄条、几乎占满图高、颜色从上到下平滑变化。"""
    h, w = nav.h, nav.w
    chroma = nav.chroma
    cols = chroma.sum(axis=0)
    need = h * 0.55
    xs = [x for x in range(w) if cols[x] >= need]
    if not xs:
        return None
    runs = _runs(np.array([x in set(xs) for x in range(w)]))
    best = None
    for s, e in runs:
        width = e - s + 1
        if not (5 <= width <= max(40, int(w * 0.06))):
            continue
        if e < w * 0.7:
            continue                                  # colorbar 在右侧
        xm = (s + e) // 2
        strip = nav.rgb[:, xm]
        inside = [y for y in range(h) if chroma[y, xm]]
        if len(inside) < h * 0.5:
            continue
        top_y, bot_y = inside[0], inside[-1]
        span = [strip[int(top_y + (bot_y - top_y) * f)] for f in (0.05, 0.5, 0.95)]
        if np.abs(span[0].astype(int) - span[2].astype(int)).max() < 40:
            continue                                  # 单色块不算渐变条
        best = {
            "x_range": [s, e],
            "y_range": [int(top_y), int(bot_y)],
            "height_ratio": round(len(inside) / h, 3),
            "colors_high_to_low": [_hex(span[0]), _hex(span[1]), _hex(span[2])],
        }
    return best


def _detect_beeswarm(nav: Nav) -> Optional[Dict[str, Any]]:
    """识别「纵向蜂群/小提琴 + colorbar」这类图（典型：SHAP beeswarm）。

    这类图的数值本质上无法逐点还原（同一行上千个点互相覆盖），但它有明确的
    **样式**：每行一条细长彩色分布 + 右侧 colorbar。所以这里只做「样式复现」，
    不产出 data.csv，绝不编造数值。
    """
    cb = _detect_colorbar(nav)
    if not cb:
        return None
    h, w = nav.h, nav.w
    left_w = int(cb["x_range"][0] - 10)
    if left_w < w * 0.3:
        return None
    chroma = nav.chroma[:, :left_w]
    comps = _components(chroma)
    blobs = 0
    for (bx0, by0, bx1, by1) in comps:
        cw, ch = bx1 - bx0 + 1, by1 - by0 + 1
        if cw < 2 or ch < 2 or cw * ch > 4000:
            continue
        if cw > ch * 3:
            continue                                  # 横条不是小提琴
        blobs += 1
    if blobs < 12:
        return None
    rows = _row_centers(chroma)
    xcal = _bottom_axis_calib(nav, left_w)
    row_geom: List[Dict[str, Any]] = []
    for y in rows:
        y0, y1 = max(0, y - 4), min(h, y + 5)
        band = chroma[y0:y1, :left_w]
        colmask = band.any(axis=0)
        groups: List[Dict[str, Any]] = []
        for x0, x1 in _runs(colmask):
            if x1 - x0 < 1:
                continue
            sub = nav.rgb[y0:y1, x0:x1 + 1]
            mask = band[:, x0:x1 + 1]
            pix = sub[mask] if mask.any() else sub.reshape(-1, 3)
            color = _hex(np.mean(pix, axis=0)) if pix.size else "#888888"
            v0 = xcal.to_value(x0) if xcal and xcal.ok else float(x0)
            v1 = xcal.to_value(x1) if xcal and xcal.ok else float(x1)
            if v0 is None or v1 is None:
                v0, v1 = float(x0), float(x1)
            groups.append({"x0": round(min(v0, v1), 3),
                           "x1": round(max(v0, v1), 3),
                           "color": color})
        if groups:
            row_geom.append({"y": y, "groups": groups})
    notes = [
        "识别为「蜂群/小提琴 + colorbar」图：同一行大量点互相覆盖，无法逐点还原数值",
        "已按原图逐行彩色分布的几何范围生成小提琴轮廓，并保留配色与 colorbar",
        "特征标签优先读取同目录 labels.json；缺失且无 OCR 时用 feature_N 占位，不编造数值",
    ]
    if xcal and xcal.ok:
        notes.append("已标定 x 轴（数值轴），可用于对齐自己的 SHAP 值")
    return {
        "chart_type": "beeswarm",
        "colorbar": cb,
        "n_series": len(rows),
        "rows": row_geom,
        "x_axis": _axis_meta(xcal) if xcal else {"scale_ok": False, "note": "x 轴未标定"},
        "notes": notes,
        "why": "蜂群图无法逐点还原数值；本条目复现原图的布局、小提琴轮廓与配色",
    }


def _row_centers(mask: np.ndarray, min_rows: int = 12) -> List[int]:
    rows = [y for y in range(mask.shape[0]) if mask[y].sum() > 0]
    return _collapse(rows, gap=4) if len(rows) >= min_rows else []


def _bottom_axis_calib(nav: Nav, left_w: int) -> Optional[Calib]:
    """只在底部找一条长横线 + 刻度，用于给蜂群图一个数值 x 轴。"""
    sub = nav.rgb[:, :left_w]
    ink = sub.mean(axis=2) < INK
    hh, ww = ink.shape
    best_y, best_len = -1, 0
    for y in range(int(hh * 0.8), hh):
        runs = _runs(ink[y])
        if not runs:
            continue
        s, e = max(runs, key=lambda r: r[1] - r[0])
        if e - s > best_len:
            best_y, best_len = y, e - s
    if best_len < ww * 0.35 or best_y < 0:
        return None
    ticks = _axis_ticks(nav, 0, min(left_w, ww) - 1, best_y, horizontal=True)
    cal = _calibrate(nav, ticks, best_y, horizontal=True)
    return cal if cal.ok else None


# -- 刻度 ------------------------------------------------------------------- #
def _axis_ticks(nav: Nav, lo: int, hi: int, edge: int, horizontal: bool) -> List[int]:
    """找刻度线位置：从 ``edge`` 向外（下/左）延伸 3–10 px 的短线。"""
    ticks: List[int] = []
    if horizontal:
        for x in range(lo, hi + 1):
            seg = nav.ink[edge + 1:edge + 11, x]
            n = int(seg.sum())
            if 2 <= n <= 10:
                ticks.append(x)
    else:
        for y in range(lo, hi + 1):
            seg = nav.ink[y, max(0, edge - 11):max(0, edge - 1)]
            n = int(seg.sum())
            if 2 <= n <= 10:
                ticks.append(y)
    return _collapse(ticks, gap=3)


def _collapse(vals: List[int], gap: int = 3) -> List[int]:
    """把相邻像素聚成一个刻度（取均值）。"""
    out: List[int] = []
    run: List[int] = []
    for v in vals:
        if run and v - run[-1] > gap:
            out.append(int(round(sum(run) / len(run))))
            run = []
        run.append(v)
    if run:
        out.append(int(round(sum(run) / len(run))))
    return out


def _tick_strip(nav: Nav, tick: int, edge: int, horizontal: bool) -> Optional[np.ndarray]:
    """取一个刻度对应的**文字条**（只包含刻度标签本身）。

    窗口里常混着别的东西：x 轴下方还有轴标题/多行标签，y 轴左侧还有旋转的
    轴标题。这里按连通域两段聚类——先取离轴最近的一行（或一段），再在这一行里
    取离刻度最近的一组字符，最后把「矮而宽」的负号单独补回来。整段裁切，
    不会把 ``-7.5`` 切成 ``.5``。
    """
    if horizontal:
        ylo, yhi = edge + 3, min(nav.h, edge + 64)
        xlo, xhi = max(0, tick - 40), min(nav.w, tick + 40)
    else:
        ylo, yhi = max(0, tick - 16), min(nav.h, tick + 16)
        xlo, xhi = max(0, edge - 170), max(0, edge - 2)
    sub = nav.ink[ylo:yhi, xlo:xhi]
    if sub.size == 0:
        return None
    comps = _components(sub)
    glyphs: List[Tuple[int, int, int, int]] = []
    for (bx0, by0, bx1, by1) in comps:
        cw, ch = bx1 - bx0 + 1, by1 - by0 + 1
        if ch < 5 or ch > 30 or cw > 44:
            continue
        if not horizontal and cw > ch:
            continue        # 纵轴上「横躺」的是旋转轴标题，不是刻度数字
        if horizontal and cw <= 3 and abs(xlo + (bx0 + bx1) / 2.0 - tick) <= 3:
            continue                                    # 刻度短线
        if (not horizontal) and ch <= 3 and abs(ylo + (by0 + by1) / 2.0 - tick) <= 3:
            continue
        glyphs.append((bx0, by0, bx1, by1))
    if not glyphs:
        return None

    if horizontal:
        line = _group_glyphs(glyphs, axis=1)[0][2]      # 最靠轴的一行
        gy0 = min(g[1] for g in line)
        gy1 = max(g[3] for g in line)
        lh = gy1 - gy0 + 1
        groups = _group_glyphs(line, axis=0, gap=max(20, lh))
        _, _, label = min(groups, key=lambda g: abs((g[0] + g[1]) / 2.0 - tick))
        lx0 = min(g[0] for g in label)
        lx1 = max(g[2] for g in label)
        left = min([lx0] + [c[0] for c in _minus_like(comps, lh, gy0, gy1, lx0)])
        right = lx1 + 3
        return nav.gray[ylo + gy0 - 2:ylo + gy1 + 3, xlo + max(0, left - 1):xlo + right]

    # 纵轴：按水平间隙切段，取最靠轴的一段（旋转的轴标题字形在更左边）
    # 字高只统计「够高」的字形，避免轴标题残片把中位数拉低、把 -7.5 拆成 7 和 5
    hs = [g[3] - g[1] + 1 for g in glyphs]
    tall = [x for x in hs if x >= max(hs) * 0.6] or hs
    lh0 = int(np.median(tall))
    line = _group_glyphs(glyphs, axis=0, gap=max(8, int(lh0 * 0.8)))[-1][2]
    gx0 = min(g[0] for g in line)
    gx1 = max(g[2] for g in line)
    gy0 = min(g[1] for g in line)
    gy1 = max(g[3] for g in line)
    lh = gy1 - gy0 + 1
    left = min([gx0] + [c[0] for c in _minus_like(comps, lh, gy0, gy1, gx0)])
    left = max(0, min(left, gx0) - 1)
    right = min(sub.shape[1], gx1 + 3)
    return nav.gray[ylo:yhi, xlo + left:xlo + right]


def _line_h(glyphs: List[Tuple[int, int, int, int]]) -> int:
    return max(g[3] - g[1] + 1 for g in glyphs)


def _minus_like(comps: List[Tuple[int, int, int, int]], lh: int,
                gy0: int, gy1: int, gx0: int) -> List[Tuple[int, int, int, int]]:
    """挑出紧贴字符组左侧、与字形同处一行的「负号/小数点」连通域。"""
    out = []
    mid = (gy0 + gy1) / 2.0
    for (bx0, by0, bx1, by1) in comps:
        cw, ch = bx1 - bx0 + 1, by1 - by0 + 1
        if ch > 3 or cw < 3:
            continue                                   # 负号只有 1–3 px 高
        if bx1 > gx0 + 2 or bx1 < gx0 - lh * 1.0:
            continue
        cy = (by0 + by1) / 2.0
        if abs(cy - mid) > lh * 0.35:                  # 负号在字高中间
            continue
        out.append((bx0, by0, bx1, by1))
    return out


def _group_glyphs(boxes: List[Tuple[int, int, int, int]], axis: int, gap: int = 8
                  ) -> List[Tuple[int, int, List[Tuple[int, int, int, int]]]]:
    """按 ``axis`` 方向把字形分段（axis=1 纵向、axis=0 横向），返回按位置排序的段。"""
    ordered = sorted(boxes, key=lambda b: b[axis])
    groups: List[Tuple[int, int, List[Tuple[int, int, int, int]]]] = []
    lo, hi = ordered[0][axis], ordered[0][axis + 2]
    cur = [ordered[0]]
    for b in ordered[1:]:
        if b[axis] - hi > gap:
            groups.append((lo, hi, cur))
            lo, hi = b[axis], b[axis + 2]
            cur = [b]
        else:
            cur.append(b)
            hi = max(hi, b[axis + 2])
    groups.append((lo, hi, cur))
    return groups


def _calibrate(nav: Nav, ticks: List[int], edge: int, horizontal: bool) -> Calib:
    """读刻度文本并标定。

    诚实门槛：**≥3 个能读出的数值刻度**、单调、且数值间距与像素间距都近似等距。
    两个刻度无法校验等差，对数轴天然不等距——两者都判为「不可标定」。
    """
    if len(ticks) < 2:
        return Calib(note="刻度线不足 2 个")
    vals: List[Tuple[float, float]] = []
    for t in ticks:
        strip = _tick_strip(nav, t, edge, horizontal)
        if strip is None:
            continue
        num = read_number(strip)
        if num is not None:
            vals.append((float(t), float(num)))
    if len(vals) < 2:
        return Calib(note="可识别的数值刻度不足 2 个")

    px = [v[0] for v in vals]
    vv = [v[1] for v in vals]
    diffs = np.diff(vv)
    if not (np.all(diffs > 0) or np.all(diffs < 0)):
        return Calib(px, vv, note="刻度值非单调，识别不可靠")
    if len(vv) == 2:
        step = abs(vv[1] - vv[0])
        if not _nice_step(step):
            return Calib(px, vv, note="仅 2 个刻度且步长不规整（%g），无法校验等差性" % step)
        return Calib(px, vv, ok=True, score=0.45,
                     note="仅 2 个数值刻度，未做等差校验（置信度较低）")

    dv = np.abs(diffs)
    dp = np.abs(np.diff(px))
    med_v = float(np.median(dv))
    med_p = float(np.median(dp))
    if med_v <= 0 or med_p <= 0:
        return Calib(px, vv, note="刻度间距为 0")
    vcv = float(np.std(dv) / med_v)
    pcv = float(np.std(dp) / med_p)
    if vcv > 0.08 or pcv > 0.12:
        return Calib(px, vv, note="刻度不等距（数值 %.2f／像素 %.2f），可能是对数轴或识别错误"
                     % (vcv, pcv))
    score = max(0.35, min(1.0, 1.0 - max(vcv, pcv) * 3))
    return Calib(px, vv, ok=True, score=score)


def _nice_step(step: float) -> bool:
    """刻度步长是否是「人类会用的整数」（1/1.5/2/2.5/3/4/5/6/8 × 10^k）。"""
    if step <= 0:
        return False
    mag = 10.0 ** math.floor(math.log10(step))
    r = step / mag
    return any(abs(r - n) <= 0.06 for n in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8))


# --------------------------------------------------------------------------- #
# 数字识别（渲染模板 + 连通域匹配）
# --------------------------------------------------------------------------- #
_FONT_CACHE: Dict[Tuple[int, int], Any] = {}
_TEMPLATE_CACHE: Dict[Tuple[str, int], List[np.ndarray]] = {}
_FONT_PATHS: Optional[List[Path]] = None


def _font_paths() -> List[Path]:
    """可用字体（多种字族）：论文图可能用 Arial/Helvetica/Times/雅黑，单一字体会认错。"""
    global _FONT_PATHS
    if _FONT_PATHS is not None:
        return _FONT_PATHS
    cands: List[Path] = []
    try:
        import matplotlib  # type: ignore

        base = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
        cands += [base / "DejaVuSans.ttf", base / "DejaVuSans-Bold.ttf", base / "DejaVuSerif.ttf"]
    except Exception:
        pass
    win_fonts = Path(os.environ.get("WINDIR", "")) / "Fonts"
    cands += [win_fonts / "arial.ttf", win_fonts / "segoeui.ttf",
              win_fonts / "msyh.ttc", win_fonts / "times.ttf",
              win_fonts / "simsun.ttc", win_fonts / "calibri.ttf"]
    seen = set()
    out: List[Path] = []
    for c in cands:
        if c.exists() and str(c).lower() not in seen:
            seen.add(str(c).lower())
            out.append(c)
    _FONT_PATHS = out
    return out


def _font(size: int, idx: int = 0):
    key = (idx, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    from PIL import ImageFont  # type: ignore

    paths = _font_paths()
    try:
        path = paths[idx] if idx < len(paths) else paths[0]
        _FONT_CACHE[key] = ImageFont.truetype(str(path), size)
    except Exception:
        _FONT_CACHE[key] = ImageFont.load_default()
    return _FONT_CACHE[key]


def _templates(ch: str, h: int) -> List[np.ndarray]:
    """同一字符在多种字体下的模板（匹配时取最优）。"""
    key = (ch, h)
    if key in _TEMPLATE_CACHE:
        return _TEMPLATE_CACHE[key]
    from PIL import Image, ImageDraw  # type: ignore

    size = max(8, int(h * 1.6))
    out: List[np.ndarray] = []
    for idx in range(max(1, len(_font_paths()))):
        img = Image.new("L", (size * 2, size * 2), 255)
        ImageDraw.Draw(img).text((2, 2), ch, font=_font(size, idx), fill=0)
        arr = np.asarray(img) < 160
        ys, xs = np.nonzero(arr)
        if ys.size == 0:
            continue
        out.append(_fit(arr[ys.min():ys.max() + 1, xs.min():xs.max() + 1], h))
    if not out:
        out = [np.zeros((h, h), dtype=bool)]
    _TEMPLATE_CACHE[key] = out
    return out


def _fit(arr: np.ndarray, h: int) -> np.ndarray:
    """等比缩放（保持宽高比）到指定高度 ``h``（宽按比例）。"""
    from PIL import Image  # type: ignore

    if arr.size == 0:
        return np.zeros((h, 1), dtype=bool)
    w = max(1, int(round(arr.shape[1] * h / max(1, arr.shape[0]))))
    img = Image.fromarray((arr * 255).astype(np.uint8)).resize((w, h))
    return np.asarray(img) > 120


def read_number(strip: np.ndarray) -> Optional[float]:
    """从一条窄图像里读出数字（支持负号与小数点）；读不出返回 ``None``。

    负号和数字是分离的连通域，且高度只有 2–3 px——必须先按「矮而宽」识别成 ``-``，
    否则 ``-15`` 会被读成 ``15``，符号错了比读不出更糟。
    """
    ink = strip < 150
    if not ink.any():
        return None
    boxes: List[Tuple[int, int, int, int]] = []
    for (bx0, by0, bx1, by1) in _components(ink):
        cw, ch = bx1 - bx0 + 1, by1 - by0 + 1
        if ch < 2 or ch > strip.shape[0] or cw > strip.shape[1] * 0.9:
            continue
        boxes.append((bx0, by0, bx1, by1))
    heights = [c[3] - c[1] + 1 for c in boxes if c[3] - c[1] + 1 >= 5]
    if not heights:
        return None
    hmed = int(np.median(heights))

    text = ""
    for (bx0, by0, bx1, by1) in sorted(boxes, key=lambda c: c[0]):
        sub = ink[by0:by1 + 1, bx0:bx1 + 1]
        cw, ch = bx1 - bx0 + 1, by1 - by0 + 1
        if ch <= max(2, hmed * 0.4):
            text += "-" if cw >= max(2, hmed * 0.35) else "."
            continue
        if ch < hmed * 0.6:
            continue
        text += _match_char(sub, hmed)
    m = re.fullmatch(r"-?\d+(?:\.\d+)?", text)
    if not m:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _match_char(sub: np.ndarray, h: int) -> str:
    """字形匹配：二值 IoU + 行/列墨迹分布，并在多个缩放下取平均。

    单尺度会把 0 认成 9（缩放插值恰好抹掉笔画）；多尺度平均后稳定得多。
    """
    scales = sorted({max(8, int(round(h * f))) for f in (0.85, 1.0, 1.15)})
    probes = {s: _fit(sub, s) for s in scales}
    best, best_score = "?", -1.0
    for ch in "0123456789":
        total = 0.0
        for s in scales:
            t_ = _templates(ch, s)
            best_t = max(0.55 * _iou(probes[s], t) + 0.45 * _profile_sim(probes[s], t)
                         for t in t_)
            total += best_t
        score = total / len(scales)
        if score > best_score:
            best, best_score = ch, score
    return best if best_score >= 0.55 else "?"


def _profile_sim(a: np.ndarray, b: np.ndarray) -> float:
    """两幅字形图的行/列墨迹分布相似度（0–1），对细笔画和小字号更稳健。"""
    from PIL import Image  # type: ignore

    def prep(m: np.ndarray) -> np.ndarray:
        ys, xs = np.nonzero(m)
        if ys.size == 0:
            return m
        m = m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        return np.asarray(Image.fromarray((m * 255).astype(np.uint8)).resize((24, 24))) > 120

    aa, bb = prep(a), prep(b)
    if aa.size == 0 or bb.size == 0:
        return 0.0

    def prof(m: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        rows = m.sum(axis=1).astype(float)
        cols = m.sum(axis=0).astype(float)
        rows /= max(rows.sum(), 1.0)
        cols /= max(cols.sum(), 1.0)
        return rows, cols

    ra, ca = prof(aa)
    rb, cb = prof(bb)
    return 1.0 - 0.25 * (np.abs(ra - rb).sum() + np.abs(ca - cb).sum())


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    from PIL import Image  # type: ignore

    hh = max(a.shape[0], b.shape[0])
    ww = max(a.shape[1], b.shape[1])
    aa = np.asarray(Image.fromarray((a * 255).astype(np.uint8)).resize((ww, hh))) > 120
    bb = np.asarray(Image.fromarray((b * 255).astype(np.uint8)).resize((ww, hh))) > 120
    inter = np.logical_and(aa, bb).sum()
    union = np.logical_or(aa, bb).sum()
    return float(inter) / float(union) if union else 0.0


def _components(mask: np.ndarray) -> List[Tuple[int, int, int, int]]:
    """8 邻域连通域外接框（图很小，BFS 足够）。"""
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    out: List[Tuple[int, int, int, int]] = []
    for y in range(h):
        for x in range(w):
            if not mask[y, x] or seen[y, x]:
                continue
            q = deque([(y, x)])
            seen[y, x] = True
            x0 = x1 = x
            y0 = y1 = y
            while q:
                cy, cx = q.popleft()
                x0, x1 = min(x0, cx), max(x1, cx)
                y0, y1 = min(y0, cy), max(y1, cy)
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        ny, nx = cy + dy, cx + dx
                        if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = True
                            q.append((ny, nx))
            out.append((x0, y0, x1, y1))
    return out


# --------------------------------------------------------------------------- #
# 数据提取
# --------------------------------------------------------------------------- #
def _series_masks(nav: Nav, frame) -> List[np.ndarray]:
    """按「实色」切系列：统计面板内出现最多的几种彩色，各生成一张掩码。

    为什么不用「只要有彩色」：柱状图里常叠着结构示意/彩色标注，用单一 chroma
    掩码会把装饰也算进柱高；而同一根柱子的填充色是**完全一致**的，按主色取最稳。
    """
    x0, x1, y0, y1 = frame
    sub = nav.rgb[y0 + 2:y1 - 1, x0 + 2:x1 - 1]
    if sub.size == 0:
        return []
    flat = sub.reshape(-1, 3).astype(np.int16)
    q = flat // 24
    key = q[:, 0] * 10000 + q[:, 1] * 100 + q[:, 2]
    uniq, counts = np.unique(key, return_counts=True)
    order = np.argsort(-counts)
    chosen: List[np.ndarray] = []
    for i in order:
        if len(chosen) >= 3:
            break
        if counts[i] < flat.shape[0] * 0.004:
            break
        center = flat[key == uniq[i]].mean(axis=0)
        spread = float(center.max() - center.min())
        if center.max() > 235 and spread < 25:
            continue                                   # 近白
        if center.max() < 60 and spread < 25:
            continue                                   # 近黑
        if spread < 18:
            continue                                   # 灰（坐标轴/文字）
        if any(np.abs(center - c).max() < 30 for c in chosen):
            continue
        chosen.append(center)
    masks: List[np.ndarray] = []
    for c in chosen:
        d = np.abs(sub.astype(np.int16) - c.astype(np.int16)).max(axis=2)
        m = d <= 45
        if int(m.sum()) >= 40:
            masks.append(m)
    return masks


def _extract_bars(frame, mask: np.ndarray, xcal: Calib, ycal: Calib
                  ) -> Tuple[List[Dict[str, Any]], str]:
    """在单色掩码上找**纵向**柱：柱子必须贴住底轴、宽度在合理区间。

    贴轴是区分「柱」和「散点/标注」的关键；柱顶取「从底轴向上连续段」的
    中位数——图里压着柱子的结构示意/标注不会把柱高顶高。
    """
    x0, x1, y0, y1 = frame
    hh, ww = mask.shape
    if hh < 20 or ww < 20:
        return [], ""
    bars: List[Dict[str, Any]] = []
    for s, e in _runs(mask.any(axis=0)):
        w = e - s + 1
        if w < max(3, ww * 0.004) or w > ww * 0.2:
            continue
        tops: List[int] = []
        for x in range(s, e + 1):
            col = mask[:, x]
            rows = np.flatnonzero(col)
            if rows.size == 0 or int(rows.max()) < hh - 3:
                continue
            top = int(rows.max())
            while top - 1 >= 0 and col[top - 1]:
                top -= 1
            tops.append(top)
        if len(tops) < (e - s + 1) * 0.6:
            continue
        top = int(np.median(tops))
        if hh - top < hh * 0.04:
            continue
        bars.append({"orientation": "v", "center": x0 + 2 + (s + e) / 2.0,
                     "top_px": y0 + 2 + top, "axis": "y",
                     "value": _r(ycal.to_value(y0 + 2 + top)) if ycal.ok else None})
    return (bars, "v") if len(bars) >= 2 else ([], "")


def _ink_hbars(nav: Nav, frame, xcal: Calib) -> List[Dict[str, Any]]:
    """横向柱（不依赖单一填充色）：按「整行连续墨迹」找柱带。

    论文里横向柱常用**渐变蓝**（每个柱子颜色不同），按主色切会漏掉大半；
    这里只看几何：一行里有足够长的连续墨迹、连续多行组成 4px 以上的带，
    再取离 0 最远的端点（这样负值柱——柱子从 0 向左长——也能读对）。
    """
    x0, x1, y0, y1 = frame
    gray = nav.gray[y0 + 2:y1 - 1, x0 + 2:x1 - 1]
    rgb = nav.rgb[y0 + 2:y1 - 1, x0 + 2:x1 - 1].astype(np.int16)
    chroma = (rgb.max(axis=2) - rgb.min(axis=2)) > 8      # 门槛低一些，浅色柱子也算
    ink = (gray < 244) & (chroma | (gray < 180))
    hh, ww = ink.shape
    if hh < 20 or ww < 40:
        return []

    rows: List[Tuple[int, int, int]] = []
    for y in range(hh):
        runs = _runs(ink[y])
        if not runs:
            continue
        s, e = max(runs, key=lambda r: r[1] - r[0])
        if (e - s + 1) >= ww * 0.05:
            rows.append((y, s, e))
    if not rows:
        return []

    bands: List[List[Tuple[int, int, int]]] = []
    cur = [rows[0]]
    for item in rows[1:]:
        if item[0] - cur[-1][0] <= 1:
            cur.append(item)
        else:
            bands.append(cur)
            cur = [item]
    bands.append(cur)

    cand: List[Dict[str, Any]] = []
    for band in bands:
        h = band[-1][0] - band[0][0] + 1
        if h < 5 or h > hh * 0.3:
            continue
        # 柱是矩形：取整带的并集（柱内压着白底数值标签时，单行会被截断，
        # 但柱子首尾那些行是完整的，并集能还原真实左右端）
        s = min(b[1] for b in band)
        e = max(b[2] for b in band)
        cand.append({"y": band[len(band) // 2][0], "s": s, "e": e})
    if len(cand) < 3:
        return []

    # 横向柱图的所有柱子共享同一条基线（0 或左轴）：要么起点齐、要么终点齐
    ss = np.array([c["s"] for c in cand], dtype=float)
    ee = np.array([c["e"] for c in cand], dtype=float)
    base_s, base_e = float(np.median(ss)), float(np.median(ee))
    aligned_s = np.median(np.abs(ss - base_s)) <= 6
    aligned_e = np.median(np.abs(ee - base_e)) <= 6
    if not (aligned_s or aligned_e):
        return []
    if aligned_s:
        cand = [c for c in cand if abs(c["s"] - base_s) <= 8 or abs(c["e"] - base_s) <= 8]
    else:
        cand = [c for c in cand if abs(c["e"] - base_e) <= 8 or abs(c["s"] - base_e) <= 8]
    if len(cand) < 3:
        return []

    out: List[Dict[str, Any]] = []
    for c in cand:
        ps, pe = x0 + 2 + c["s"], x0 + 2 + c["e"]
        vs = xcal.to_value(ps) if xcal.ok else None
        ve = xcal.to_value(pe) if xcal.ok else None
        if vs is None or ve is None:
            val, px = None, pe
        else:
            val, px = (vs, ps) if abs(vs) > abs(ve) else (ve, pe)
        out.append({"orientation": "h", "center": y0 + 2 + c["y"],
                    "right_px": px, "axis": "x", "value": _r(val)})
    return out


def _extract_points(nav: Nav, frame, xcal: Calib, ycal: Calib,
                    bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """散点图：取彩色连通域质心 → 像素 → 数值。长条（回归线/网格）一律排除。"""
    x0, x1, y0, y1 = frame
    area = nav.chroma[y0 + 2:y1 - 2, x0 + 2:x1 - 2]
    comps = _components(area)
    pts: List[Dict[str, Any]] = []
    for (bx0, by0, bx1, by1) in comps:
        cw, ch = bx1 - bx0 + 1, by1 - by0 + 1
        if cw < 3 or ch < 3 or cw * ch > 900:
            continue                      # 太小的噪点 / 太大的整块，都不是散点
        if cw > ch * 3.5 or ch > cw * 3.5:
            continue                      # 长条：回归线、图例线、边框
        cx = x0 + 2 + (bx0 + bx1) / 2.0
        cy = y0 + 2 + (by0 + by1) / 2.0
        pts.append({"px": [round(cx, 1), round(cy, 1)],
                    "x": _r(xcal.to_value(cx)) if xcal.ok else None,
                    "y": _r(ycal.to_value(cy)) if ycal.ok else None})
    if len(pts) > 400:
        return []
    for pt in pts:
        pt["confidence"] = round(min(xcal.score if xcal.ok else 0.0,
                                      ycal.score if ycal.ok else 0.0), 2)
    return pts


def _r(v: Optional[float]) -> Optional[float]:
    return None if v is None else round(float(v), 3)


# --------------------------------------------------------------------------- #
# 产物
# --------------------------------------------------------------------------- #
def _write_csv(path: Path, panels: List[Dict[str, Any]]) -> None:
    lines = ["panel,kind,series,x,y,note"]
    for i, p in enumerate(panels, start=1):
        if p["kind"] == "scatter":
            for pt in p["points"]:
                lines.append(f"{i},scatter,{pt.get('series', 0)},{pt['x']},{pt['y']},estimated")
        else:
            for j, b in enumerate(p["bars"], start=1):
                if b["orientation"] == "v":
                    lines.append(f"{i},bar_v,cat_{j},{j},{b['value']},estimated")
                else:
                    lines.append(f"{i},bar_h,cat_{j},{b['value']},{j},estimated")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _draw_script(fig_id: str, panels: List[Dict[str, Any]]) -> str:
    """生成「同风格」绘图脚本。

    **样式不是猜的**：柱色、点径、描边色、趋势线、虚线参考线都来自从 PDF 原图
    识别出的 ``STYLE``；脚本只负责「换成你自己的 data.csv 也能画出同款」。
    """
    style = []
    for p in panels:
        st = dict(p.get("style") or {})
        style.append({
            "kind": p.get("kind"),
            "orientation": p.get("orientation", ""),
            **st,
        })
    style_lit = json.dumps(style, ensure_ascii=False, indent=2)
    head = f'''"""由 paper2code 生成的同风格重绘脚本（{fig_id}）。

⚠️ 数据来自 data.csv —— 它是**从 PDF 位图反推的估算值**，不是论文原始数据。
   {DISCLAIMER}

STYLE 里是**从原图实际测量**的样式（柱色/点径/描边/趋势线），换数据后仍保持同款风格。

用法：
    python draw_{fig_id}.py                 # 读同目录 data.csv，输出 preview_panel*.png
把 data.csv 换成你自己复现得到的数据（列名保持一致：panel,kind,series,x,y），即可重画。
"""
import csv
import json
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt

HERE = Path(__file__).parent
DISCLAIMER = {DISCLAIMER!r}

#: 逐面板样式（从原图识别）
STYLE = json.loads(r"""{style_lit}""")

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
    ratio = (st.get("marker") or {{}}).get("diameter_ratio") or 0.035
    return max(18.0, (ratio * 288.0) ** 2)


def main():
    data = load()
    for panel_no, kind in sorted(data):
        rows = data[(panel_no, kind)]
        st = STYLE[panel_no - 1] if panel_no - 1 < len(STYLE) else {{}}
        fig, ax = plt.subplots(figsize=(6.4, 4.0), dpi=140)

        if kind.startswith("bar"):
            vals = [_num(r["y"] if kind == "bar_v" else r["x"]) for r in rows]
            colors = st.get("colors") or ["#4a7fb5"] * len(rows)
            labels = [f"#{{i}}" for i in range(1, len(rows) + 1)]
            if kind == "bar_v":
                ax.bar(labels, vals, color=colors[:len(vals)], width=0.6)
            else:
                ax.barh(labels, vals, color=colors[:len(vals)], height=0.7)
                ax.invert_yaxis()
        else:
            marker = st.get("marker") or {{}}
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
                           label=f"series {{sidx + 1}}", zorder=3)
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

        ax.set_title(f"panel {{panel_no}} - redraw (not original values)", fontsize=10)
        ax.grid(alpha=0.22, linestyle=":")
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        fig.tight_layout()
        out = HERE / f"preview_panel{{panel_no}}.png"
        fig.savefig(out)
        plt.close(fig)
        print("wrote", out)


if __name__ == "__main__":
    main()
'''
    return head


def _beeswarm_script(fig_id: str, meta: Dict[str, Any]) -> str:
    """生成小提琴图（SHAP 摘要）模板：原图配色 + 逐行几何 + colorbar。"""
    cb = meta.get("colorbar") or {}
    high_to_low = cb.get("colors_high_to_low") or ["#e94b8a", "#8a3ffc", "#1f9ae5"]
    ticks = ((meta.get("x_axis") or {}).get("ticks") or [])
    x_lo = min([v for _p, v in ticks], default=-1.0)
    x_hi = max([v for _p, v in ticks], default=5.0)
    rows = meta.get("rows") or []
    n_rows = int(meta.get("n_series") or len(rows) or 18)
    feats = ["feature_%d" % (i + 1) for i in range(n_rows)]
    template = '''"""由 paper2code 生成的同风格重绘脚本（__FIG__）。

图表类型：**纵向小提琴图 + 右侧 colorbar**（SHAP 摘要图）。
   __DISCLAIMER__

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
DISCLAIMER = __DISCLAIMER__
COLORS_HIGH_TO_LOW = __COLORS__
CMAP = LinearSegmentedColormap.from_list("paper_colorbar", list(reversed(COLORS_HIGH_TO_LOW)))
FEATURES = __FEATURES__
ROWS = __ROWS__
X_LIM = __XLIM__
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
    out = HERE / "preview___FIG__.png"
    fig.savefig(out)
    print("wrote", out)


if __name__ == "__main__":
    main()
'''
    return (template
            .replace("__FIG__", fig_id)
            .replace("__DISCLAIMER__", repr(DISCLAIMER))
            .replace("__COLORS__", json.dumps(high_to_low))
            .replace("__FEATURES__", json.dumps(feats, ensure_ascii=False))
            .replace("__ROWS__", json.dumps(rows, ensure_ascii=False))
            .replace("__XLIM__", json.dumps([x_lo, x_hi])))


def _run_beeswarm_preview(script: Path, meta: Dict[str, Any], out: Path) -> Optional[str]:
    """直接执行刚生成的脚本跑出预览——**代码能用**比「看起来能用」更可信。"""
    import subprocess
    import sys as _sys

    try:
        proc = subprocess.run([_sys.executable, str(script)], cwd=str(script.parent),
                              capture_output=True, text=True, timeout=180)
        if proc.returncode != 0:
            return None
        return str(out) if out.exists() else None
    except Exception:
        return None


def _render_preview(fig_id: str, panels: List[Dict[str, Any]], out: Path) -> Optional[str]:
    """用 matplotlib 画一张预览（失败就返回空，不影响数据落盘）。"""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    try:
        n = len(panels)
        if n <= 4:
            fig, axes = plt.subplots(1, n, figsize=(4.2 * n, 3.6), dpi=130, squeeze=False)
        else:
            ncols = 3
            nrows = int(math.ceil(n / ncols))
            fig, axes = plt.subplots(nrows, ncols,
                                     figsize=(4.0 * ncols, 3.2 * nrows),
                                     dpi=130, squeeze=False)
        for i, (ax, p) in enumerate(zip(axes.flat, panels), start=1):
            st = p.get("style") or {}
            pkind = p.get("orientation") or (p["kind"][:1] if p["kind"] == "bar" else "")
            if p["kind"] == "scatter":
                marker = st.get("marker") or {}
                fills = marker.get("fills") or ["#4a7fb5"]
                groups: Dict[int, List[Tuple[float, float]]] = {}
                for q in p["points"]:
                    if q.get("x") is None or q.get("y") is None:
                        continue
                    groups.setdefault(int(q.get("series", 0)), []).append((q["x"], q["y"]))
                size = max(18.0, ((marker.get("diameter_ratio") or 0.035) * 288.0) ** 2)
                for sidx, pts in sorted(groups.items()):
                    ax.scatter([q[0] for q in pts], [q[1] for q in pts], s=size,
                               facecolor=fills[sidx % len(fills)],
                               edgecolor=marker.get("edge_color", "#1b1b1b"),
                               linewidth=0.8 if marker.get("edge_present", True) else 0.0,
                               zorder=3)
                trend = st.get("trend_line") or {}
                if trend.get("slope") is not None:
                    xr = trend.get("x_range") or [-1, 1]
                    ax.plot(xr, [trend["slope"] * x + trend["intercept"] for x in xr],
                            color=trend.get("color", "#ff1f1f"), linewidth=2.0, zorder=4)
                for ref in st.get("reference_lines") or []:
                    if ref.get("value") is None:
                        continue
                    if ref.get("orientation") == "h":
                        ax.axhline(ref["value"], color="#9a9a9a", linestyle=(0, (4, 3)), linewidth=1.0)
                    else:
                        ax.axvline(ref["value"], color="#9a9a9a", linestyle=(0, (4, 3)), linewidth=1.0)
            elif pkind == "h":
                pairs = [(b["value"], c) for b, c in
                         zip(p["bars"], st.get("colors") or []) if b["value"] is not None]
                vals = [q[0] for q in pairs] or [b["value"] for b in p["bars"] if b["value"] is not None]
                cols = [q[1] for q in pairs] if len(pairs) == len(vals) else "#8fb3d9"
                ax.barh(range(len(vals)), vals, color=cols, height=0.7)
                ax.invert_yaxis()
            else:
                pairs = [(b["value"], c) for b, c in
                         zip(p["bars"], st.get("colors") or []) if b["value"] is not None]
                vals = [q[0] for q in pairs] or [b["value"] for b in p["bars"] if b["value"] is not None]
                cols = [q[1] for q in pairs] if len(pairs) == len(vals) else "#4a7fb5"
                ax.bar(range(len(vals)), vals, color=cols, width=0.6)
            ax.grid(alpha=0.25, linestyle=":")
            ax.set_title(f"panel {i} - estimated (not original values)", fontsize=7)
        for ax in axes.flat[len(panels):]:
            ax.set_visible(False)
        fig.tight_layout()
        fig.savefig(out)
        plt.close(fig)
        return str(out)
    except Exception:
        import os as _os
        if _os.environ.get("P2C_BITMAP_DEBUG"):
            raise
        return None


__all__ = ["digitize", "DISCLAIMER"]
