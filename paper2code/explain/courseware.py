"""课件生成：把 :class:`Slide` 列表渲染为**自包含**的 HTML 幻灯片。

产物特性：
* 单文件、零外部依赖（CSS/JS 全部内联），双击即可放映；
* 卡片式版式 + 渐变色标题页，响应式，浅色主题；
* 键盘 ←/→/空格、点击左右热区、进度条、页码；
* ``?print=1`` 或浏览器打印时展开为全部页面，便于导出 PDF 讲义。
"""

from __future__ import annotations

import html
import json
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from ..models import Figure, Paper, Slide, Table

_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#f5f7fb; --card:#ffffff; --ink:#1b2130; --muted:#5d6b85;
  --line:#e3e8f2; --brand:#4f6bed; --brand2:#8b5cf6; --accent:#0ea5a4;
  --warn:#d97706; --fail:#dc2626; --pass:#16a34a;
  --radius:18px; --shadow:0 10px 30px rgba(24,39,75,.10);
}
html,body{height:100%}
body{
  background:radial-gradient(1100px 620px at 12% -8%,#e8edff 0%,transparent 60%),
             radial-gradient(900px 520px at 108% 4%,#eafaf7 0%,transparent 55%),
             var(--bg);
  color:var(--ink);
  font:16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased;
}
.deck{max-width:1120px;margin:0 auto;padding:28px 20px 76px}
.slide{
  display:none;background:var(--card);border:1px solid var(--line);
  border-radius:var(--radius);box-shadow:var(--shadow);
  padding:40px 46px 34px;min-height:600px;position:relative;overflow:hidden;
}
.slide.active{display:block;animation:in .32s cubic-bezier(.2,.7,.3,1)}
@keyframes in{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}
.slide::after{
  content:"";position:absolute;inset:0 0 auto 0;height:5px;
  background:linear-gradient(90deg,var(--brand),var(--brand2),var(--accent));
}
.kicker{font-size:12.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--brand);font-weight:700;margin-bottom:10px}
h1{font-size:40px;line-height:1.25;letter-spacing:-.02em;font-weight:800}
h2{font-size:27px;line-height:1.3;letter-spacing:-.01em;font-weight:750;margin-bottom:6px}
h2+.rule{height:3px;width:64px;border-radius:3px;background:linear-gradient(90deg,var(--brand),var(--brand2));margin:14px 0 24px}
.sub{color:var(--muted);font-size:15.5px;margin-top:14px}
ul.bullets{list-style:none;display:grid;gap:13px;margin-top:6px}
ul.bullets li{
  position:relative;padding:14px 18px 14px 44px;background:#f8faff;
  border:1px solid var(--line);border-radius:12px;font-size:16.2px;
}
ul.bullets li::before{
  content:"";position:absolute;left:18px;top:22px;width:9px;height:9px;border-radius:50%;
  background:linear-gradient(135deg,var(--brand),var(--brand2));
}
.slide.title-slide{display:none;background:linear-gradient(135deg,#3b4fd8 0%,#6d4de0 46%,#0e9f9a 100%);color:#fff;border:none}
.slide.title-slide.active{display:block}
.slide.title-slide::after{display:none}
.slide.title-slide .kicker{color:rgba(255,255,255,.85)}
.slide.title-slide .sub{color:rgba(255,255,255,.9)}
.slide.title-slide ul.bullets li{background:rgba(255,255,255,.13);border-color:rgba(255,255,255,.24);color:#fff}
.slide.title-slide ul.bullets li::before{background:#fff}
.agenda{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px;margin-top:8px}
.agenda .item{
  background:#f8faff;border:1px solid var(--line);border-radius:12px;padding:13px 16px;font-size:15.4px;
  display:flex;gap:12px;align-items:flex-start;
}
.agenda .num{
  flex:0 0 26px;height:26px;border-radius:8px;display:grid;place-items:center;font-size:13px;font-weight:700;
  color:#fff;background:linear-gradient(135deg,var(--brand),var(--brand2));
}
table.data{border-collapse:collapse;width:100%;font-size:14.4px;margin-top:8px;border-radius:12px;overflow:hidden}
table.data th,table.data td{border-bottom:1px solid var(--line);padding:9px 12px;text-align:left}
table.data th{background:#eef2ff;font-weight:700;color:#39457a;white-space:nowrap}
table.data tr:nth-child(even) td{background:#fafbff}
table.data td.num{text-align:right;font-variant-numeric:tabular-nums}
.caption{font-size:13.6px;color:var(--muted);margin-top:12px;padding-left:12px;border-left:3px solid var(--line)}
figure{margin:16px 0 0;text-align:center}
figure img{max-width:100%;max-height:430px;border-radius:12px;border:1px solid var(--line)}
.notes{
  margin-top:24px;padding:15px 18px;background:linear-gradient(180deg,#fffdf5,#fff9e8);
  border:1px dashed #ecd9a6;border-radius:12px;font-size:14.6px;color:#6b5a2c;
}
.notes b{color:#a06a12}
.pill{display:inline-block;padding:3px 10px;border-radius:999px;font-size:12.5px;font-weight:700;background:#eef2ff;color:#3d4ea8;margin-right:6px}
.takeaway ul.bullets li::before{background:linear-gradient(135deg,var(--accent),#22c55e)}
.bar{position:fixed;left:0;right:0;bottom:0;background:rgba(255,255,255,.92);border-top:1px solid var(--line);backdrop-filter:blur(8px)}
.bar-inner{max-width:1120px;margin:0 auto;padding:10px 20px;display:flex;align-items:center;gap:16px}
.bar button{
  border:1px solid var(--line);background:#fff;color:var(--ink);border-radius:10px;
  padding:8px 16px;font-size:14.5px;font-weight:600;cursor:pointer;transition:.15s;
}
.bar button:hover{border-color:var(--brand);color:var(--brand);transform:translateY(-1px)}
.bar button:disabled{opacity:.4;cursor:not-allowed}
.track{flex:1;height:7px;background:#e7ebf5;border-radius:99px;overflow:hidden}
.fill{height:100%;width:0;background:linear-gradient(90deg,var(--brand),var(--brand2));transition:width .25s}
.count{font-size:13.5px;color:var(--muted);font-variant-numeric:tabular-nums;min-width:64px;text-align:right}
.zone{position:fixed;top:0;bottom:52px;width:22%;cursor:pointer;z-index:5}
.zone.left{left:0}.zone.right{right:0}
.hint{position:fixed;bottom:64px;right:22px;font-size:12.5px;color:var(--muted);background:rgba(255,255,255,.9);border:1px solid var(--line);padding:6px 12px;border-radius:99px}
@media print{
  body{background:#fff}
  .slide,.slide.title-slide{display:block!important;page-break-after:always;box-shadow:none;margin-bottom:16px;min-height:auto}
  .bar,.zone,.hint{display:none!important}
  .slide.title-slide{color:#fff}
}
@media (max-width:720px){
  .slide{padding:26px 20px;min-height:auto}
  h1{font-size:28px}h2{font-size:21px}
  ul.bullets li{font-size:15px}
}
"""

_JS = """
(function(){
  var slides=[].slice.call(document.querySelectorAll('.slide'));
  var i=0;
  var fill=document.getElementById('fill');
  var cur=document.getElementById('cur');
  var total=document.getElementById('total');
  var prev=document.getElementById('prev');
  var next=document.getElementById('next');
  if(total) total.textContent=slides.length;
  function show(k){
    if(k<0||k>=slides.length) return;
    slides[i].classList.remove('active');
    i=k;
    slides[i].classList.add('active');
    if(cur) cur.textContent=(i+1);
    if(fill) fill.style.width=((i+1)/slides.length*100)+'%';
    if(prev) prev.disabled=(i===0);
    if(next) next.disabled=(i===slides.length-1);
    window.scrollTo({top:0,behavior:'smooth'});
  }
  if(prev) prev.onclick=function(){show(i-1)};
  if(next) next.onclick=function(){show(i+1)};
  document.querySelector('.zone.left').onclick=function(){show(i-1)};
  document.querySelector('.zone.right').onclick=function(){show(i+1)};
  document.addEventListener('keydown',function(e){
    if(e.key==='ArrowRight'||e.key===' '||e.key==='PageDown'){e.preventDefault();show(i+1)}
    if(e.key==='ArrowLeft'||e.key==='PageUp'){e.preventDefault();show(i-1)}
    if(e.key==='Home')show(0);
    if(e.key==='End')show(slides.length-1);
  });
  var m=/[?&]p=([0-9]+)/.exec(location.search);
  show(m?Math.max(0,Math.min(slides.length-1,parseInt(m[1],10)-1)):0);
})();
"""


def _esc(text: str) -> str:
    return html.escape(str(text or ""), quote=True)


def _is_numeric_cell(value: str) -> bool:
    from .. import numutil

    return numutil.to_number(value) is not None


def render_table_html(table: Table, max_rows: int = 14) -> str:
    if not table.header and not table.rows:
        return '<p class="caption">（未抽取到表格内容）</p>'
    head = table.header or [f"col{i+1}" for i in range(table.n_cols)]
    width = max(len(head), max((len(r) for r in table.rows), default=0))
    head = head + [""] * (width - len(head))

    out = ['<table class="data"><thead><tr>']
    for h in head:
        out.append(f"<th>{_esc(h)}</th>")
    out.append("</tr></thead><tbody>")
    for row in table.rows[:max_rows]:
        row = list(row) + [""] * (width - len(row))
        out.append("<tr>")
        for ci, cell in enumerate(row):
            cls = ' class="num"' if ci > 0 and _is_numeric_cell(cell) else ""
            out.append(f"<td{cls}>{_esc(cell)}</td>")
        out.append("</tr>")
    out.append("</tbody></table>")
    if len(table.rows) > max_rows:
        out.append(f'<p class="caption">仅展示前 {max_rows} 行，共 {table.n_rows} 行（完整数据见复现产物 CSV）。</p>')
    if table.caption:
        out.append(f'<p class="caption">{_esc(table.caption)}</p>')
    return "".join(out)


def _copy_asset(src: str, dest_dir: Path, paper: Paper) -> Optional[str]:
    """把论文引用的图片复制到 ``dest_dir/assets/``，返回**相对**引用路径。

    返回值必须与真实落盘位置一致，否则课件里的 ``<img src>`` 会 404。
    """
    src = (src or "").strip()
    if not src or src.startswith(("http://", "https://", "data:")):
        return src or None

    candidates = []
    base = paper.meta.get("bundle_dir") or ""
    if base:
        candidates.append(Path(base) / src)
    if paper.source_path:
        candidates.append(Path(paper.source_path).parent / src)
    candidates.append(Path(src))

    for cand in candidates:
        try:
            if cand.exists() and cand.is_file():
                assets = dest_dir / "assets"
                assets.mkdir(parents=True, exist_ok=True)
                target = assets / cand.name
                if not target.exists():
                    shutil.copy2(cand, target)
                return f"assets/{target.name}"
        except Exception:
            continue
    return None


def render_courseware(
    paper: Paper,
    slides: List[Slide],
    out_path: Path,
    generator: str = "offline",
) -> str:
    """渲染为单文件 HTML 课件，返回文件路径。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fig_map: Dict[str, Figure] = {f.id: f for f in paper.figures}
    tab_map: Dict[str, Table] = {t.id: t for t in paper.tables}

    parts: List[str] = []
    for idx, slide in enumerate(slides):
        cls = "slide"
        if slide.kind == "title":
            cls += " title-slide"
        if slide.kind == "takeaway":
            cls += " takeaway"
        parts.append(f'<section class="{cls}">')

        if slide.kind == "title":
            parts.append('<div class="kicker">Paper2Code · 论文精讲</div>')
            parts.append(f"<h1>{_esc(slide.title)}</h1>")
            if slide.bullets:
                parts.append('<ul class="bullets" style="margin-top:26px">')
                for b in slide.bullets:
                    parts.append(f"<li>{_esc(b)}</li>")
                parts.append("</ul>")
            parts.append(
                f'<p class="sub">自动生成（provider: {_esc(generator)}）· '
                f'共 {len(slides)} 页 · 使用 ← → 翻页，打印可导出 PDF</p>'
            )
        elif slide.kind == "agenda":
            parts.append('<div class="kicker">Agenda</div>')
            parts.append(f"<h2>{_esc(slide.title)}</h2><div class='rule'></div>")
            parts.append('<div class="agenda">')
            for i, b in enumerate(slide.bullets, start=1):
                label = re.sub(r"^\d+[.、]\s*", "", b)
                parts.append(
                    f'<div class="item"><span class="num">{i}</span><span>{_esc(label)}</span></div>'
                )
            parts.append("</div>")
        else:
            if slide.kind == "takeaway":
                parts.append('<div class="kicker">总结 · 要点回顾</div>')
                parts.append(f"<h2>{_esc(slide.title)}</h2><div class='rule'></div>")
                parts.append(
                    '<p class="sub">按「问题 → 方法 → 结果 → 局限 → 复现」的顺序，'
                    '用五句话收束全篇。</p>'
                )
            else:
                parts.append(f'<div class="kicker">第 {idx + 1} 页 / 共 {len(slides)} 页</div>')
                parts.append(f"<h2>{_esc(slide.title)}</h2><div class='rule'></div>")
            if slide.bullets:
                parts.append('<ul class="bullets">')
                for b in slide.bullets:
                    parts.append(f"<li>{_esc(b)}</li>")
                parts.append("</ul>")

        if slide.figure_id and slide.figure_id in fig_map:
            fig = fig_map[slide.figure_id]
            rel = _copy_asset(fig.image_path or "", out_path.parent, paper)
            if rel:
                parts.append(
                    f'<figure><img src="{_esc(rel)}" alt="{_esc(fig.caption)}"/>'
                    f'<figcaption class="caption">{_esc(fig.caption or fig.id)}</figcaption></figure>'
                )
            else:
                parts.append(
                    f'<p class="caption"><span class="pill">图 {_esc(fig.id)}</span>'
                    f"{_esc(fig.caption or '（该图未能从源文件提取到图像，见原文）')}</p>"
                )

        if slide.table_id and slide.table_id in tab_map:
            parts.append(render_table_html(tab_map[slide.table_id]))

        if slide.notes:
            parts.append(f'<div class="notes"><b>讲解提示：</b>{_esc(slide.notes)}</div>')
        parts.append("</section>")

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{_esc(paper.title or '论文讲解')} · 课件</title>
<style>{_CSS}</style>
</head>
<body>
<div class="deck">{''.join(parts)}</div>
<div class="zone left"></div><div class="zone right"></div>
<div class="hint">← → 翻页</div>
<div class="bar"><div class="bar-inner">
  <button id="prev">上一页</button>
  <div class="track"><div class="fill" id="fill"></div></div>
  <span class="count"><b id="cur">1</b> / <span id="total">-</span></span>
  <button id="next">下一页</button>
</div></div>
<script>{_JS}</script>
</body>
</html>
"""
    out_path.write_text(doc, encoding="utf-8")

    # 同时输出可编辑的大纲 Markdown，便于人工二次加工
    md = [f"# {paper.title} · 讲解大纲", ""]
    for i, s in enumerate(slides, start=1):
        md.append(f"## {i}. {s.title}")
        for b in s.bullets:
            md.append(f"- {b}")
        if s.notes:
            md.append(f"\n> 讲解提示：{s.notes}")
        md.append("")
    out_path.with_suffix(".md").write_text("\n".join(md), encoding="utf-8")
    return str(out_path)
