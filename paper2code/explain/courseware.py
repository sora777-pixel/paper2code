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
from .. import textutil

_CLOSE_HEAD = re.compile(
    r"(?im)^\s*(?:(?:section|chapter|第)\s*)?"
    r"(?:(?:\d+|[ivxlcdm]+|[一二三四五六七八九十]+)[\.、.:：)]?\s+)?"
    r"(?:"
    r"conclusions?(?:\s+and\s+outlook)?"
    r"|discussion"
    r"|limitations?"
    r"|outlook"
    r"|future\s+works?"
    r"|结论(?:与展望)?"
    r"|讨论"
    r"|展望"
    r"|不足"
    r"|局限"
    r")\s*$"
)
_STOP_HEAD = re.compile(
    r"(?im)^\s*(?:(?:section|chapter|第)\s*)?"
    r"(?:(?:\d+|[ivxlcdm]+|[一二三四五六七八九十]+)[\.、.:：)]?\s+)?"
    r"(?:methods?|references?|acknowledg(?:e?ments?)?|bibliograph(?:y|ies)|"
    r"appendix|supplementary(?:\s+material)?|data\s+availability|"
    r"code\s+availability|方法|参考文献|致谢|附录|数据可用性)\s*$"
)
_NEXT_HEAD = re.compile(
    r"(?im)^\s*(?:(?:section|chapter|第)\s*)?"
    r"(?:\d+|[ivxlcdm]+|[一二三四五六七八九十]+)[\.、.:：)]?\s+"
    r"[A-Za-z\u4e00-\u9fff]"
)
_OUTLOOK_RE = re.compile(
    r"recommend|future work|in the future|outlook|we (?:will|plan)|should report|"
    r"open (?:problem|question)|pave the way|opens up|new applications|"
    r"建议|展望|未来|下一步|开放问题",
    re.I,
)
_LIMIT_RE = re.compile(
    r"limitations?|bottleneck|further improved|more research|"
    r"cannot|unable|not (?:be )?released|drawback|caveat|"
    r"only (?:a )?single|不足|局限|未能|无法|缺点|偏差|无法公开",
    re.I,
)

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
  padding:28px 40px 28px;min-height:0;height:calc(100vh - 96px);max-height:calc(100vh - 96px);
  position:relative;overflow:hidden;flex-direction:column;
}
.slide-body{
  flex:1 1 auto;min-height:0;overflow:auto;padding-right:4px;
  -webkit-overflow-scrolling:touch;
}
.slide.active{display:flex;flex-direction:column;animation:in .32s cubic-bezier(.2,.7,.3,1)}
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
.slide.title-slide{display:none;background:linear-gradient(135deg,#3b4fd8 0%,#6d4de0 46%,#0e9f9a 100%);color:#fff;border:none;height:calc(100vh - 96px);max-height:calc(100vh - 96px);overflow:hidden}
.slide.title-slide.active{display:flex;flex-direction:column}
.slide.title-slide::after{display:none}
.slide.title-slide .slide-body{
  display:flex;flex-direction:column;justify-content:center;gap:4px;
  overflow:hidden;height:100%;max-height:100%;padding-right:0;
}
.slide.title-slide .kicker{color:rgba(255,255,255,.85);margin-bottom:14px}
.slide.title-slide h1{
  font-size:clamp(22px, 3.2vw, 34px);line-height:1.28;letter-spacing:-.02em;
  overflow-wrap:anywhere;word-break:break-word;
  display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:3;overflow:hidden;
}
.slide.title-slide .title-meta{
  margin-top:14px;font-size:16px;font-weight:600;opacity:.92;
  overflow-wrap:anywhere;word-break:break-word;
  display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden;
}
.slide.title-slide .title-lead{
  margin-top:16px;font-size:16.5px;line-height:1.55;max-width:38em;opacity:.95;
  display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:4;overflow:hidden;
}
.slide.title-slide .sub{
  color:rgba(255,255,255,.86);margin-top:18px;font-size:14px;
  display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden;
}
.slide.title-slide ul.bullets,.slide.title-slide ul.bullets li{display:none!important}
.takeaway .pill{margin-right:8px}
.takeaway h3{font-size:15.5px;font-weight:750;color:#39457a;margin:14px 0 8px}
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
figure img{max-width:100%;max-height:min(55vh,420px);object-fit:contain;border-radius:12px;border:1px solid var(--line)}
.notes{
  margin-top:24px;padding:15px 18px;background:linear-gradient(180deg,#fffdf5,#fff9e8);
  border:1px dashed #ecd9a6;border-radius:12px;font-size:14.6px;color:#6b5a2c;
}
.notes b{color:#a06a12}
.pill{display:inline-block;padding:3px 10px;border-radius:999px;font-size:12.5px;font-weight:700;background:#eef2ff;color:#3d4ea8;margin-right:6px}
.takeaway ul.bullets li::before{background:linear-gradient(135deg,var(--accent),#22c55e)}
.eq{margin:18px 0 8px;padding:16px 18px;background:#f4f6ff;border:1px solid #d9e0f5;border-radius:12px;font:16px/1.55 ui-monospace,Consolas,monospace;white-space:pre-wrap;overflow-x:auto}
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

.table-wrap{position:relative;margin-top:8px;max-width:100%}
.table-scroll{
  max-width:100%;max-height:min(42vh,380px);overflow-x:auto;overflow-y:auto;-webkit-overflow-scrolling:touch;
  border:1px solid var(--line);border-radius:12px;background:#fff;
  scrollbar-width:thin;scrollbar-color:var(--brand2) #eef2ff;
}
.table-scroll::-webkit-scrollbar{height:10px}
.table-scroll::-webkit-scrollbar-track{background:#eef2ff;border-radius:0 0 12px 12px}
.table-scroll::-webkit-scrollbar-thumb{
  background:linear-gradient(90deg,var(--brand),var(--brand2));border-radius:99px;
}
table.data{
  border-collapse:separate;border-spacing:0;width:max-content;min-width:100%;
  font-size:14.4px;margin:0;border-radius:0;overflow:visible;
}
table.data th,table.data td{
  border-bottom:1px solid var(--line);padding:9px 12px;text-align:left;
  white-space:nowrap;vertical-align:middle;
}
table.data th{background:#eef2ff;font-weight:700;color:#39457a}
table.data tr:nth-child(even) td{background:#fafbff}
table.data td.num{text-align:right;font-variant-numeric:tabular-nums}
table.data th:first-child,table.data td:first-child{
  position:sticky;left:0;z-index:2;background:#eef2ff;
  box-shadow:2px 0 0 var(--line);
}
table.data tr:nth-child(even) td:first-child{background:#f3f6ff}
table.data tbody tr:hover td{background:#f0f4ff}
table.data tbody tr:hover td:first-child{background:#e8edff}
figure{margin:16px 0 0;text-align:center;position:relative}
figure img{
  max-width:100%;max-height:min(55vh,420px);width:auto;height:auto;object-fit:contain;
  border-radius:12px;border:1px solid var(--line);cursor:zoom-in;display:block;margin:0 auto;
}
.zoomable{cursor:zoom-in}
.zoomable:hover .zoom-hint,.table-wrap:hover .zoom-hint{opacity:1}
.zoom-hint{
  position:absolute;top:10px;right:10px;z-index:3;opacity:0;transition:opacity .15s;
  border:none;cursor:pointer;pointer-events:none;
  padding:4px 10px;border-radius:999px;font-size:12px;font-weight:700;
  color:#fff;background:linear-gradient(135deg,var(--brand),var(--brand2));
  box-shadow:0 4px 12px rgba(79,107,237,.35);
}
.table-wrap .zoom-hint{pointer-events:auto;cursor:pointer}
.bar{z-index:20}
.hint{z-index:20}
.lightbox{
  display:none;position:fixed;inset:0;z-index:1000;
  background:rgba(20,24,40,.78);backdrop-filter:blur(6px);
  padding:28px 20px 40px;align-items:center;justify-content:center;
}
.lightbox.open{display:flex}
.lightbox-inner{
  position:relative;max-width:min(96vw,1400px);max-height:90vh;width:100%;
  background:var(--card);border-radius:16px;border:1px solid rgba(255,255,255,.2);
  box-shadow:0 24px 64px rgba(0,0,0,.35);overflow:auto;padding:18px;
}
.lightbox-inner img{
  display:block;max-width:100%;max-height:calc(90vh - 48px);width:auto;height:auto;
  object-fit:contain;margin:0 auto;border-radius:8px;
}
.lightbox-inner .table-scroll{max-height:calc(90vh - 48px);overflow:auto;border-radius:12px}
.lightbox-inner table.data{font-size:15px}
.lightbox-close{
  position:fixed;top:16px;right:18px;z-index:1001;
  width:40px;height:40px;border-radius:50%;border:none;cursor:pointer;
  font-size:22px;line-height:1;color:#fff;
  background:linear-gradient(135deg,var(--brand),var(--brand2));
  box-shadow:0 6px 18px rgba(79,107,237,.45);
}
.lightbox-caption{margin-top:12px;text-align:center;color:var(--muted);font-size:13.5px}
body.lb-open .zone,body.lb-open .hint{pointer-events:none}

@media print{
  body{background:#fff}
  .slide,.slide.title-slide{display:block!important;page-break-after:always;box-shadow:none;margin-bottom:16px;min-height:auto;height:auto;max-height:none;overflow:visible}
  .bar,.zone,.hint,.lightbox,.zoom-hint{display:none!important}
  .table-scroll{overflow:visible!important;border:none}
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
  var lb=document.getElementById('lightbox');
  var lbBody=document.getElementById('lightbox-body');
  var lbCap=document.getElementById('lightbox-caption');
  var lbClose=document.getElementById('lightbox-close');
  if(total) total.textContent=slides.length;
  function show(k){
    if(document.body.classList.contains('lb-open')) return;
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
  function openLightbox(html, caption){
    if(!lb||!lbBody) return;
    lbBody.innerHTML=html;
    if(lbCap) lbCap.textContent=caption||'\u70b9\u51fb\u80cc\u666f\u6216\u6309 Esc \u5173\u95ed';
    lb.classList.add('open');
    document.body.classList.add('lb-open');
  }
  function closeLightbox(){
    if(!lb||!lbBody) return;
    lb.classList.remove('open');
    document.body.classList.remove('lb-open');
    lbBody.innerHTML='';
  }
  if(prev) prev.onclick=function(){show(i-1)};
  if(next) next.onclick=function(){show(i+1)};
  document.querySelector('.zone.left').onclick=function(){show(i-1)};
  document.querySelector('.zone.right').onclick=function(){show(i+1)};
  document.addEventListener('keydown',function(e){
    if(document.body.classList.contains('lb-open')){
      if(e.key==='Escape'){e.preventDefault();closeLightbox()}
      return;
    }
    if(e.key==='ArrowRight'||e.key===' '||e.key==='PageDown'){e.preventDefault();show(i+1)}
    if(e.key==='ArrowLeft'||e.key==='PageUp'){e.preventDefault();show(i-1)}
    if(e.key==='Home')show(0);
    if(e.key==='End')show(slides.length-1);
  });
  document.addEventListener('click',function(e){
    var t=e.target;
    if(!t) return;
    if(t.closest && t.closest('#lightbox-close')){closeLightbox();return}
    if(lb && lb.classList.contains('open') && t===lb){closeLightbox();return}
    var img = t.closest ? t.closest('figure img') : null;
    if(img && !document.body.classList.contains('lb-open')){
      e.preventDefault(); e.stopPropagation();
      var fig = img.closest('figure');
      var capEl = fig ? fig.querySelector('figcaption') : null;
      var cap = (capEl && capEl.textContent) || img.getAttribute('alt') || '';
      openLightbox('<img src="'+img.getAttribute('src')+'" alt=""/>', cap);
      return;
    }
    var wrap = t.closest ? t.closest('.table-wrap.zoomable') : null;
    if(wrap && !document.body.classList.contains('lb-open')){
      e.preventDefault(); e.stopPropagation();
      var scroll = wrap.querySelector('.table-scroll');
      if(!scroll) return;
      openLightbox(scroll.cloneNode(true).outerHTML, '\u5bbd\u8868\u653e\u5927 \u00b7 \u53ef\u6a2a\u5411/\u7eb5\u5411\u6eda\u52a8');
    }
  }, true);
  if(lbClose) lbClose.onclick=function(e){e.stopPropagation();closeLightbox()};
  var m=/[?&]p=(\d+)/.exec(location.search);
  show(m?Math.max(0,Math.min(slides.length-1,parseInt(m[1],10)-1)):0);
})();
"""


def _fix_text(text: str) -> str:
    """Cheap display fixes for common PDF/OCR encoding glitches."""
    s = str(text or "")
    s = re.sub(r"L[\u00a8¨]\s*[Oo]wdin", "Löwdin", s)
    s = re.sub(r"L''\s*[Oo]wdin", "Löwdin", s)
    return s


def _esc(text: str) -> str:
    return html.escape(_fix_text(text), quote=True)


def _is_numeric_cell(value: str) -> bool:
    from .. import numutil

    return numutil.to_number(value) is not None


def render_table_html(table: Table, max_rows: int = 14) -> str:
    if not table.header and not table.rows:
        return '<p class="caption">（未抽取到表格内容）</p>'
    head = table.header or [f"col{i+1}" for i in range(table.n_cols)]
    width = max(len(head), max((len(r) for r in table.rows), default=0))
    head = head + [""] * (width - len(head))

    out = [
        '<div class="table-wrap zoomable" title="点击放大">',
        '<button type="button" class="zoom-hint" tabindex="-1">点击放大</button>',
        '<div class="table-scroll">',
        '<table class="data"><thead><tr>',
    ]
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
    out.append("</tbody></table></div></div>")
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


def _clean_bullets(items: Optional[List[str]]) -> List[str]:
    out: List[str] = []
    for raw in items or []:
        b = re.sub(r"\s+", " ", str(raw or "")).strip().strip('"').strip("'")
        if len(b) < 2:
            continue
        out.append(b)
    return out


def _fmt_authors(paper: Paper, limit: int = 8) -> str:
    names: List[str] = []
    for a in paper.authors or []:
        a = str(a).strip().strip('"').strip("'")
        a = re.split(r"\b(?:authors?|title)\s*:", a, maxsplit=1, flags=re.I)[0].strip().strip('"')
        pieces = [p.strip().strip('"') for p in re.split(r"\s*,\s*", a) if p.strip()] if a.count(",") >= 1 and len(a) > 48 else [a]
        for p in pieces:
            p = re.sub(r"\d+$", "", p).strip()
            if len(p) < 2 or re.match(r"^\d", p):
                continue
            if p.lower() not in {x.lower() for x in names}:
                names.append(p)
    if not names and (paper.raw_text or ""):
        for line in (paper.raw_text or "").splitlines()[:6]:
            line = line.strip()
            if not line or line.lower().startswith(("title", "abstract", "where does")):
                continue
            if re.search(r"\d", line):
                continue
            bits = [p.strip() for p in re.split(r"\s*,\s*", line) if p.strip()]
            if 1 <= len(bits) <= 8:
                names = bits
                break
    return ", ".join(names[:limit])


def _full_title(paper: Paper) -> str:
    title = re.sub(r"\s+", " ", (paper.title or "").strip())
    from_id = re.sub(r"\s+", " ", (paper.id or "").replace("_", " ")).strip()
    if from_id and title and from_id.lower().startswith(title.lower()) and len(from_id) > len(title) + 2:
        title = from_id
    if not title and (paper.raw_text or ""):
        title = (paper.raw_text.splitlines()[0] or "").strip()
    return title


def _title_lead(paper: Paper) -> str:
    text = (paper.abstract or "").strip()
    if len(text) < 40:
        blob = paper.raw_text or ""
        m = re.search(
            r"(?is)(?:^|\n)\s*abstract\b[:\s]*\n?(.*?)(?=\n\s*(?:\d+[\.\s]+)?(?:introduction|引言)\b)",
            blob,
        )
        if m:
            text = re.sub(r"\s+", " ", m.group(1)).strip()
    if len(text) < 40:
        for kind in ("abstract", "introduction", "discussion"):
            chunk = paper.section_text(kind)
            if len((chunk or "").strip()) >= 40:
                text = chunk
                break
    if len(text) < 40:
        return ""
    sents = textutil.split_sentences(text)
    return textutil.condense(sents[0] if sents else text, 160)


def title_bits(paper: Paper) -> List[str]:
    return [x for x in (_fmt_authors(paper), _title_lead(paper)) if x]


def _blob(paper: Paper) -> str:
    parts = [paper.raw_text or "", paper.abstract or ""]
    for s in paper.sections:
        heading = (s.heading or "").strip()
        if heading or s.text:
            parts.append(heading + "\n" + (s.text or ""))
    return "\n".join(parts)


def _classify_close_heading(heading: str) -> str:
    h = (heading or "").lower()
    if any(k in h for k in ("不足", "局限", "limitation")):
        return "limitation"
    if any(k in h for k in ("展望", "outlook", "future")) and not any(
        k in h for k in ("conclusion", "结论")
    ):
        return "outlook"
    if any(k in h for k in ("讨论", "discussion")):
        return "discussion"
    return "conclusion"


def _chunk_after_headings(blob: str) -> Dict[str, str]:
    buckets: Dict[str, List[str]] = {
        "conclusion": [],
        "outlook": [],
        "limitation": [],
        "discussion": [],
    }
    empty = {k: "" for k in buckets}
    if not (blob or "").strip():
        return empty
    current = ""
    buf: List[str] = []

    def flush() -> None:
        text = " ".join(x.strip() for x in buf if x.strip())
        if current and text:
            buckets[current].append(text)

    for line in blob.splitlines():
        raw = line.strip()
        m = _CLOSE_HEAD.match(raw)
        if m:
            flush()
            buf = []
            current = _classify_close_heading(m.group(0))
            continue
        if current and (
            (_NEXT_HEAD.match(raw) and not _CLOSE_HEAD.match(raw)) or _STOP_HEAD.match(raw)
        ):
            flush()
            buf = []
            current = ""
            continue
        if current:
            buf.append(line)
    flush()
    return {k: " ".join(v) for k, v in buckets.items()}


def _pick_sents(
    text: str,
    n: int,
    prefer: Optional[re.Pattern] = None,
    fill: bool = True,
    lead: bool = False,
) -> List[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) < 12:
        return []
    sents = textutil.split_sentences(text)
    matched = [s for s in sents if prefer.search(s)] if prefer is not None else []
    picked: List[str] = []
    if lead and sents:
        picked.append(sents[0])
    for s in matched:
        if s not in picked:
            picked.append(s)
    if len(picked) < n and (fill or not matched):
        for s in textutil.summarize(text, n=max(n, 2)):
            if s not in picked:
                picked.append(s)
    out: List[str] = []
    seen = set()
    for s in picked:
        bit = textutil.condense(s, 108)
        key = bit.lower()
        if len(bit) < 8 or key in seen:
            continue
        seen.add(key)
        out.append(bit)
        if len(out) >= n:
            break
    return out


def closing_bullets(paper: Paper) -> List[str]:
    """从论文末尾抽取结论 / 展望 / 不足，保证最后一页有可讲的内容。"""
    chunks = _chunk_after_headings(_blob(paper))
    conc = (paper.section_text("conclusion") or "").strip() or chunks.get("conclusion") or ""
    disc = (paper.section_text("discussion") or "").strip() or chunks.get("discussion") or ""
    outlook = chunks.get("outlook") or ""
    limit = chunks.get("limitation") or ""
    tail = (paper.raw_text or paper.abstract or "")[-2200:]
    if len(conc) < 40:
        conc = disc or tail
    if len(outlook) < 40:
        outlook = disc or tail
    if len(limit) < 40:
        limit = f"{disc} {tail}".strip() or disc
    labeled: List[str] = []
    seen = set()
    mapping = [
        ("结论", conc, None, True, True),
        ("展望", outlook, _OUTLOOK_RE, True, False),
        ("不足", limit, _LIMIT_RE, False, False),
    ]
    want_n = {"结论": 2, "展望": 2, "不足": 2}
    for label, text, prefer, fill, lead in mapping:
        for bit in _pick_sents(text, want_n[label], prefer, fill=fill, lead=lead):
            key = bit.lower()
            if key in seen:
                continue
            seen.add(key)
            labeled.append(f"{label}：{bit}")
    if not any(x.startswith("结论") for x in labeled) and (paper.abstract or tail):
        bit = textutil.condense(paper.abstract or tail, 120)
        if len(bit) >= 8:
            labeled.insert(0, "结论：" + bit)
    if len(labeled) < 3:
        for sent in textutil.summarize(tail, n=4):
            bit = textutil.condense(sent, 108)
            if len(bit) < 8 or bit.lower() in seen:
                continue
            seen.add(bit.lower())
            if not any(x.startswith("结论") for x in labeled):
                tagged = "结论"
            elif not any(x.startswith("展望") for x in labeled):
                tagged = "展望"
            else:
                tagged = "不足"
            labeled.append(f"{tagged}：{bit}")
            if len(labeled) >= 5:
                break
    return labeled[:6] or ["结论：原文末尾未抽到独立结论段，建议对照论文最后一节人工补充。"]


def polish_slides(paper: Paper, slides: List[Slide]) -> List[Slide]:
    """去掉封面空框，并把最后一页补成结论 / 展望 / 不足。"""
    slides = list(slides or [])
    if not slides:
        return slides
    first = slides[0]
    if first.kind == "title":
        full = _full_title(paper)
        if full:
            first.title = full
        bits = title_bits(paper)
        first.bullets = bits or _clean_bullets(first.bullets)[:2]
    filled = closing_bullets(paper)
    last = slides[-1]
    close_title = any(
        k in (last.title or "") for k in ("小结", "结论", "展望", "不足", "takeaway", "可复现")
    )
    weak = not _clean_bullets(last.bullets) or all(
        ("未抽取" in b or "人工补充" in b) for b in _clean_bullets(last.bullets)
    )
    if last.kind == "takeaway" or close_title or weak:
        last.title = "结论、展望与不足"
        last.kind = "takeaway"
        last.bullets = filled
    else:
        slides.append(Slide(title="结论、展望与不足", bullets=filled, kind="takeaway"))
    for s in slides:
        s.bullets = _clean_bullets(s.bullets)
    return slides


def render_courseware(
    paper: Paper,
    slides: List[Slide],
    out_path: Path,
    generator: str = "offline",
) -> str:
    """渲染为单文件 HTML 课件，返回文件路径。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    slides = polish_slides(paper, list(slides))
    fig_map: Dict[str, Figure] = {f.id: f for f in paper.figures}
    tab_map: Dict[str, Table] = {t.id: t for t in paper.tables}

    parts: List[str] = []
    for idx, slide in enumerate(slides):
        cls = "slide"
        if slide.kind == "title":
            cls += " title-slide"
        if slide.kind == "takeaway":
            cls += " takeaway"
        parts.append(f'<section class="{cls}"><div class="slide-body">')

        if slide.kind == "title":
            parts.append('<div class="kicker">Paper2Code · 论文精讲</div>')
            parts.append(f"<h1>{_esc(_full_title(paper) or slide.title)}</h1>")
            authors = _fmt_authors(paper)
            lead = _title_lead(paper)
            bullets = _clean_bullets(slide.bullets)
            if not authors and bullets:
                authors = bullets[0]
                bullets = bullets[1:]
            if not lead:
                for cand in bullets:
                    if cand != authors:
                        lead = cand
                        break
            if authors:
                parts.append(f'<p class="title-meta">{_esc(authors)}</p>')
            if lead:
                parts.append(f'<p class="title-lead">{_esc(lead)}</p>')
            parts.append(
                f'<p class="sub">自动生成（provider: {_esc(generator)}）· '
                f'共 {len(slides)} 页 · 使用 ← → 翻页，打印可导出 PDF</p>'
            )
        elif slide.kind == "agenda":
            parts.append('<div class="kicker">Agenda</div>')
            parts.append(f"<h2>{_esc(slide.title)}</h2><div class='rule'></div>")
            parts.append('<div class="agenda">')
            for i, b in enumerate(_clean_bullets(slide.bullets), start=1):
                label = re.sub(r"^\d+[.、]\s*", "", b)
                if not label:
                    continue
                parts.append(
                    f'<div class="item"><span class="num">{i}</span><span>{_esc(label)}</span></div>'
                )
            parts.append("</div>")
        else:
            parts.append(f'<div class="kicker">第 {idx + 1} 页 / 共 {len(slides)} 页</div>')
            parts.append(f"<h2>{_esc(slide.title)}</h2><div class='rule'></div>")
            if slide.kind == "formula" and slide.bullets:
                parts.append(f'<pre class="eq">{_esc(slide.bullets[0])}</pre>')
                rest = slide.bullets[1:]
                if rest:
                    parts.append('<ul class="bullets">')
                    for b in rest:
                        parts.append(f"<li>{_esc(b)}</li>")
                    parts.append("</ul>")
            elif slide.bullets:
                parts.append('<ul class="bullets">')
                for b in _clean_bullets(slide.bullets):
                    m = re.match(r"^(结论|展望|不足)[：:]\s*(.+)$", b)
                    if slide.kind == "takeaway" and m:
                        parts.append(
                            f'<li><span class="pill">{_esc(m.group(1))}</span>{_esc(m.group(2))}</li>'
                        )
                    else:
                        parts.append(f"<li>{_esc(b)}</li>")
                parts.append("</ul>")

        if slide.kind not in ("title", "agenda") and slide.figure_id and slide.figure_id in fig_map:
            fig = fig_map[slide.figure_id]
            rel = _copy_asset(fig.image_path or "", out_path.parent, paper)
            if rel:
                parts.append(
                    f'<figure class="zoomable" title="点击放大">'
                    f'<button type="button" class="zoom-hint" tabindex="-1">点击放大</button>'
                    f'<img src="{_esc(rel)}" alt="{_esc(fig.caption)}"/>'
                    f'<figcaption class="caption">{_esc(fig.caption or fig.id)}</figcaption></figure>'
                )
            else:
                parts.append(
                    f'<p class="caption"><span class="pill">图 {_esc(fig.id)}</span>'
                    f"{_esc(fig.caption or '（该图未能从源文件提取到图像，见原文）')}</p>"
                )

        if slide.kind not in ("title", "agenda") and slide.table_id and slide.table_id in tab_map:
            parts.append(render_table_html(tab_map[slide.table_id]))

        if slide.notes:
            parts.append(f'<div class="notes"><b>讲解提示：</b>{_esc(slide.notes)}</div>')
        parts.append("</div></section>")

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
<div class="hint">← → 翻页 · 点击图表可放大</div>
<div class="bar"><div class="bar-inner">
  <button id="prev">上一页</button>
  <div class="track"><div class="fill" id="fill"></div></div>
  <span class="count"><b id="cur">1</b> / <span id="total">-</span></span>
  <button id="next">下一页</button>
</div></div>
<div id="lightbox" class="lightbox" role="dialog" aria-modal="true" aria-label="放大预览">
  <button type="button" id="lightbox-close" class="lightbox-close" title="关闭" aria-label="关闭">×</button>
  <div class="lightbox-inner">
    <div id="lightbox-body"></div>
    <div id="lightbox-caption" class="lightbox-caption"></div>
  </div>
</div>
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
