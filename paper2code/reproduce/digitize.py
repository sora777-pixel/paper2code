"""论文原图数字化取点（roadmap M2-4）：把「只有图、没有表」的图，从原图反推出近似数据。

为什么先做**矢量图（SVG）**再做位图：

* 矢量图里每个图元都带显式几何（轴网格线的 y、柱子的 rect、折线的 points），
  坐标轴刻度值又是配对的文本标签 —— 于是「像素 → 数值」的标定是**精确**的，
  不需要 OCR，也就不会有 OCR 的二次不确定性。
* 位图（扫描/截图）的刻度值必须靠 OCR 才能读到，否则无法标定；本模块对位图
  一律返回 ``None`` 并在报告里写明原因（**宁可不画，不可错画**）。
  PDF 里的矢量图可以先由解析层导出为 SVG 再走本模块（后续接入）。

诚实性：数字化结果只在通过置信度门槛时才输出；未通过的图仍标记为 `not_reproducible`。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

# 数字文本（允许 1 / 1.5 / .5 / 1e3 / -2）
_NUM_TEXT = re.compile(r"^\s*[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?\s*$")
_STROKE_AXIS = re.compile(r"^#(?:c9d2e6|8892a8|999999)$", re.I)
_LIGHT_FILL = {"#ffffff", "#fff", "white", "none", "transparent"}


@dataclass
class Digitized:
    """从原图取点得到的近似数据。"""

    kind: str                      # bar | grouped_bar | line
    categories: List[str] = field(default_factory=list)
    series: List[Dict[str, Any]] = field(default_factory=list)  # [{name, values}]
    xlabel: str = ""
    ylabel: str = ""
    source: str = ""               # 例如 "figures/figure1.svg（矢量图取点）"
    unit_note: str = ""            # y 轴量纲说明
    confidence: float = 0.0
    notes: List[str] = field(default_factory=list)

    def to_chart(self) -> Dict[str, Any]:
        return {
            "kind": self.kind, "title": "", "xlabel": self.xlabel, "ylabel": self.ylabel,
            "categories": self.categories, "series": self.series,
            "note": "从论文原图数字化取点（%s），非玩具数据、非重跑实验" % self.source,
        }


# --------------------------------------------------------------------------- #
# SVG 解析基础
# --------------------------------------------------------------------------- #
def _local(tag: str) -> str:
    return tag.split("}")[-1]


def _f(el: ET.Element, name: str) -> Optional[float]:
    v = el.get(name)
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _color(v: Optional[str]) -> str:
    return (v or "").strip().lower()


def _text_of(el: ET.Element) -> str:
    return "".join(el.itertext()).strip()


def _rotate_x(el: ET.Element) -> Optional[float]:
    """取 `transform="rotate(a x y)"` 里的 x（用于定位旋转过的类别标签）。"""
    tr = el.get("transform") or ""
    m = re.search(r"rotate\(\s*[-+]?\d*\.?\d+\s+([-+]?\d*\.?\d+)", tr)
    return float(m.group(1)) if m else None


def _fit_linear(pts: Sequence[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
    """最小二乘拟合 y = a*x + b；pts = [(x, y)]。"""
    n = len(pts)
    if n < 2:
        return None
    if n == 2:
        (x1, y1), (x2, y2) = pts
        if abs(x1 - x2) < 1e-9:
            return None
        a = (y2 - y1) / (x2 - x1)
        return a, y1 - a * x1
    sx = sum(p[0] for p in pts)
    sy = sum(p[1] for p in pts)
    sxx = sum(p[0] ** 2 for p in pts)
    sxy = sum(p[0] * p[1] for p in pts)
    den = n * sxx - sx * sx
    if abs(den) < 1e-9:
        return None
    a = (n * sxy - sx * sy) / den
    return a, (sy - a * sx) / n


def _polyline_points(el: ET.Element) -> List[Tuple[float, float]]:
    raw = (el.get("points") or "").replace(",", " ").split()
    out: List[Tuple[float, float]] = []
    for i in range(0, len(raw) - 1, 2):
        try:
            out.append((float(raw[i]), float(raw[i + 1])))
        except ValueError:
            continue
    return out


def _path_points(d: str) -> Tuple[List[Tuple[float, float]], int]:
    """只解析绝对 M/L 命令；返回 (点序列, 未支持的曲线命令数)。"""
    pts: List[Tuple[float, float]] = []
    unsupported = 0
    for m in re.finditer(r"([MLmlCcSsQqTtAaHhVvZz])([^MLmlCcSsQqTtAaHhVvZz]*)", d):
        cmd, body = m.group(1), m.group(2)
        nums = [float(x) for x in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", body)]
        if cmd in "ML":
            for i in range(0, len(nums) - 1, 2):
                pts.append((nums[i], nums[i + 1]))
        elif cmd in "ml":
            unsupported += 1
        elif cmd not in "Zz":
            unsupported += 1
    return pts, unsupported


# --------------------------------------------------------------------------- #
# 主解析
# --------------------------------------------------------------------------- #
def digitize_svg(svg_text: str, source: str = "") -> Optional[Digitized]:
    """从矢量图 SVG 反推近似数据；置信度不足时返回 ``None``。"""
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError:
        return None

    elems = list(root.iter())
    lines = [e for e in elems if _local(e.tag) == "line"]
    rects = [e for e in elems if _local(e.tag) == "rect"]
    texts = [e for e in elems if _local(e.tag) == "text"]
    polys = [e for e in elems if _local(e.tag) == "polyline"]
    paths = [e for e in elems if _local(e.tag) == "path"]

    # 画布尺寸（用于剔除背景矩形）
    canvas_w, canvas_h = 0.0, 0.0
    vb = root.get("viewBox")
    if vb:
        parts = re.split(r"[\s,]+", vb.strip())
        if len(parts) == 4:
            canvas_w, canvas_h = float(parts[2]), float(parts[3])
    canvas_w = canvas_w or (_f(root, "width") or 0.0)
    canvas_h = canvas_h or (_f(root, "height") or 0.0)

    # -- 坐标轴位置与基线 -- #
    horiz = [l for l in lines if abs((_f(l, "y1") or 0) - (_f(l, "y2") or 0)) < 0.75]
    vert = [l for l in lines if abs((_f(l, "x1") or 0) - (_f(l, "x2") or 0)) < 0.75]
    if not horiz:
        return None
    baseline_y = max((_f(l, "y1") or 0.0) for l in horiz)
    axis_x = min((_f(l, "x1") or 0.0) for l in vert) if vert else 0.0

    # -- y 轴标定：网格线 y ↔ 左侧数值标签 -- #
    label_texts = [
        (t, _f(t, "x"), _f(t, "y"), _text_of(t))
        for t in texts if _NUM_TEXT.match(_text_of(t) or "")
    ]
    calib_pts: List[Tuple[float, float]] = []
    for l in horiz:
        ly = _f(l, "y1")
        if ly is None:
            continue
        best: Optional[Tuple[float, float, float]] = None  # (dy, x, value)
        for _t, tx, ty, s in label_texts:
            if tx is None or ty is None or tx > axis_x + 2:
                continue
            dy = abs(ty - ly)
            if dy <= 10 and (best is None or dy < best[0]):
                best = (dy, tx, float(s))
        if best:
            calib_pts.append((ly, best[2]))
    if len(calib_pts) < 2 or len({round(p[0], 3) for p in calib_pts}) < 2:
        return None
    fit = _fit_linear(calib_pts)  # 像素 y → 数值；屏幕坐标向下为增大，故斜率应为负
    if fit is None or fit[0] >= 0:
        return None
    slope, intercept = fit

    def val_at(py: float) -> float:
        return slope * py + intercept

    ys_sorted = sorted(p[0] for p in calib_pts)
    y_top_val, y_bottom_val = val_at(ys_sorted[0]), val_at(ys_sorted[-1])

    # -- 图例：小色块 + 右侧文字 -- #
    legend: Dict[str, str] = {}
    for r in rects:
        w, h = _f(r, "width") or 0.0, _f(r, "height") or 0.0
        if not (6 <= w <= 24 and 6 <= h <= 24):
            continue
        rx, ry = _f(r, "x") or 0.0, _f(r, "y") or 0.0
        col = _color(r.get("fill"))
        if col in _LIGHT_FILL:
            continue
        best: Optional[Tuple[float, str]] = None
        for _t, tx, ty, s in (
            (t, _f(t, "x"), _f(t, "y"), _text_of(t)) for t in texts if _text_of(t)
        ):
            if tx is None or ty is None or tx < rx + w - 2:
                continue
            dy = abs(ty - (ry + h / 2.0))
            if dy <= 12 and (best is None or dy < best[0]):
                best = (dy, s)
        if best and best[1]:
            legend[col] = best[1]

    # -- 类别标签：旋转过的文本，按 x 排序 -- #
    cat_items: List[Tuple[float, str]] = []
    ylabel = ""
    for t in texts:
        s = _text_of(t)
        if not s:
            continue
        tx, ty = _f(t, "x"), _f(t, "y")
        rot = _rotate_x(t)
        if rot is not None and tx is not None:
            if ty is not None and ty > baseline_y and tx > axis_x:      # 类别轴标签
                cat_items.append((rot, s))
            elif tx < axis_x - 10:                                       # 旋转的 y 轴标题
                ylabel = ylabel or s
    cat_items.sort(key=lambda p: p[0])
    categories = [s for _x, s in cat_items]

    # -- x 轴标题：底部居中的普通文本 -- #
    xlabel = ""
    for t in texts:
        s = _text_of(t)
        ty = _f(t, "y")
        if not s or ty is None or _rotate_x(t) is not None:
            continue
        if ty > baseline_y + 10 and (t.get("text-anchor") or "") == "middle":
            xlabel = xlabel or s

    # -- 柱子 -- #
    bars: List[Tuple[str, float, float, float]] = []  # (color, center_x, top_y, value)
    for r in rects:
        w, h = _f(r, "width") or 0.0, _f(r, "height") or 0.0
        if w <= 24 or h <= 3:
            continue
        if canvas_w and canvas_h and w > 0.9 * canvas_w and h > 0.9 * canvas_h:
            continue  # 背景
        col = _color(r.get("fill"))
        if col in _LIGHT_FILL or _STROKE_AXIS.match(col or ""):
            continue
        rx, ry = _f(r, "x") or 0.0, _f(r, "y") or 0.0
        value = val_at(ry)
        # 容差：超出轴范围一个刻度步长以上，判为可疑（可能是色块/装饰）
        span = abs(y_bottom_val - y_top_val) or 1.0
        if value < min(y_top_val, y_bottom_val) - 0.5 * span or value > max(y_top_val, y_bottom_val) + 0.5 * span:
            continue
        bars.append((col, rx + w / 2.0, ry, value))

    series_map: Dict[str, List[Tuple[float, float]]] = {}  # color -> [(center_x, value)]
    for col, cx, _top, value in bars:
        series_map.setdefault(col, []).append((cx, value))
    for col in series_map:
        series_map[col].sort(key=lambda p: p[0])

    # -- 折线 -- #
    line_map: Dict[str, List[Tuple[float, float]]] = {}
    curve_notes: List[str] = []
    for el in polys + paths:
        if _local(el.tag) == "polyline":
            pts = _polyline_points(el)
            unsupported = 0
        else:
            pts, unsupported = _path_points(el.get("d") or "")
            if unsupported:
                curve_notes.append("路径含 %d 个未支持的曲线命令（只解析了 M/L）" % unsupported)
        if len(pts) < 2:
            continue
        col = _color(el.get("stroke"))
        if col in _LIGHT_FILL:
            continue
        line_map.setdefault(col, []).extend(pts)

    # -- 组装 -- #
    if series_map and len(categories) < 2:
        # 没有类别标签时退化用序号（置信度降低，仍然可画）
        n = max(len(v) for v in series_map.values())
        categories = [str(i + 1) for i in range(n)]
    if not series_map and not line_map:
        return None

    series: List[Dict[str, Any]] = []
    if series_map:
        ncat = len(categories)
        for col, pts in series_map.items():
            values: List[Optional[float]] = [None] * ncat
            for cx, value in pts:
                idx = min(range(ncat), key=lambda i: abs(cat_items[i][0] - cx)) if cat_items else 0
                if values[idx] is None:
                    values[idx] = value
            if any(v is None for v in values):
                continue
            series.append({"name": legend.get(col, col), "values": [float(v) for v in values]})
    for col, pts in line_map.items():
        pts = sorted(pts, key=lambda p: p[0])
        series.append({"name": legend.get(col, col), "values": [float(val_at(y)) for _x, y in pts]})
        if not cat_items and pts:
            categories = ["%.4g" % x for _x, _y in pts]
        break  # 折线场景下取第一条可用折线，避免把多个图元混进一张图

    if not series:
        return None
    n = min(len(s["values"]) for s in series)
    if n < 2:
        return None
    series = [{"name": s["name"], "values": s["values"][:n]} for s in series]
    if categories:
        categories = categories[:n]

    kind = "line" if line_map else ("grouped_bar" if len(series) > 1 else "bar")
    notes: List[str] = []
    if not cat_items:
        notes.append("未识别到类别轴标签，已用序号代替")
    notes.extend(curve_notes)
    if not legend:
        notes.append("未识别到图例，系列名用颜色代码代替")

    # -- 置信度门槛（宁可不画，不可错画） -- #
    conf = 0.0
    conf += min(0.4, 0.2 * len(calib_pts))            # 标定点越多越可信
    conf += 0.2 if len(legend) >= max(1, len(series)) else 0.0
    conf += 0.2 if cat_items else 0.0
    conf += 0.2 if not curve_notes else 0.0
    if conf < 0.6:
        return None

    return Digitized(
        kind=kind, categories=categories, series=series,
        xlabel=xlabel, ylabel=ylabel,
        source=source or "SVG",
        unit_note="y 轴范围约 %.4g ~ %.4g" % (min(y_top_val, y_bottom_val), max(y_top_val, y_bottom_val)),
        confidence=round(conf, 2), notes=notes,
    )


# --------------------------------------------------------------------------- #
def resolve_image_path(paper: Any, figure: Any) -> Optional[Path]:
    """把 ``Figure.image_path`` 解析成真实存在的文件（相对论文包目录）。"""
    ip = getattr(figure, "image_path", None)
    if not ip:
        return None
    cand = Path(ip)
    if cand.is_absolute() and cand.exists():
        return cand
    meta = getattr(paper, "meta", {}) or {}
    # M2-2：PDF 图资产落在产物目录的 figures/ 下（paper.meta["assets_dir"]）
    assets = meta.get("assets_dir")
    if assets and (Path(assets) / ip).exists():
        return Path(assets) / ip
    bundle = meta.get("bundle_dir")
    if bundle and (Path(bundle) / ip).exists():
        return Path(bundle) / ip
    sp = getattr(paper, "source_path", "") or ""
    if sp and (Path(sp).parent / ip).exists():
        return Path(sp).parent / ip
    return None


def digitize_figure(paper: Any, figure: Any) -> Optional[Digitized]:
    """尝试从论文原图取点。位图 / 无原图 / 低置信度一律返回 ``None``（如实降级）。"""
    path = resolve_image_path(paper, figure)
    if path is None:
        return None
    suffix = path.suffix.lower()
    rel = str(path.name)
    if suffix == ".svg":
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
        return digitize_svg(text, source="%s（矢量图取点）" % rel)
    # 位图：刻度值需 OCR 才能标定，本模块不做「猜数值」的处理
    return None
