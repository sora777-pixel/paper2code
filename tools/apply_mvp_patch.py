#!/usr/bin/env python
"""把 MVP 能力接入现有 paper2code 代码（基于锚点文本的增量补丁，保留 CRLF/LF，幂等，可回滚）。

用法（在仓库根目录）::

    python tools/apply_mvp_patch.py --check      # 只检查锚点是否都能匹配，不改文件
    python tools/apply_mvp_patch.py              # 应用补丁（原文件备份为 *.mvp.bak）
    python tools/apply_mvp_patch.py --revert     # 从 *.mvp.bak 还原

任何锚点缺失或不唯一都会整体中止、不写任何文件——绝不会把仓库改成一半。
"""
import argparse
import shutil
import sys
from pathlib import Path

P = []  # (file, id, anchor, text, where)  where: after | before


def add(file, pid, anchor, text, where="after"):
    # --skip-api：本仓库的 api.py 为精简版 Dashboard（无详情页 / _job_snapshot 等），
    # 其 5 处锚点不匹配；跳过它们，api.py 的 MVP 接线由人工完成。
    if file.endswith("api.py") and "--skip-api" in sys.argv:
        return
    P.append((file, pid, anchor, text, where))


# ------------------------------------------------------------------ llm ---- #
add("paper2code/llm/base.py", "llm-base",
    '        """对单张表格做一句话解读（可选能力）。"""\n        return ""\n',
    '''
    # MVP-PATCH:llm-base
    def complete(self, system: str, user: str, stage: str = "") -> str:
        """自由文本补全（MVP 代码 / 图表说明生成用）。

        默认不提供：离线 Provider 会抛 NotImplementedError，mvp 层据此降级到离线脚手架。
        """
        raise NotImplementedError("该 Provider 不提供自由文本生成（离线模式）")
''')

add("paper2code/llm/openai_compat.py", "llm-openai",
    "    # -- 上下文摘要 ------------------------------------------------------- #\n",
    '''    # MVP-PATCH:llm-openai
    def complete(self, system: str, user: str, stage: str = "") -> str:
        """自由文本补全（MVP 生成用）：代码生成比讲解慢，超时放宽到至少 300s；失败直接抛出，由 mvp 层降级。"""
        old = self.timeout
        self.timeout = max(old, 300)
        try:
            return self._chat(system, user)
        finally:
            self.timeout = old

''', "before")

add("paper2code/llm/router.py", "llm-router",
    '    def explain_table(self, table: Table) -> str:\n        return self._call("explain_table", table)\n',
    '''
    # MVP-PATCH:llm-router
    def complete(self, system: str, user: str, stage: str = "") -> str:
        return self._call("complete", system, user, stage)
''')

# --------------------------------------------------------------- config ---- #
add("paper2code/config.py", "config",
    '    style: str = "teaching"  # teaching | executive | exam\n',
    '''
    # -- MVP 自动生成（# MVP-PATCH:config） --------------------------------- #
    mvp_enabled: bool = True       # 处理论文时同时生成 MVP demo + 图表绘制说明
    mvp_use_llm: bool = True       # False = 只用离线脚手架
    mvp_max_fix: int = 3           # demo 运行失败后的 LLM 修复轮数
    mvp_max_fix_fig: int = 2       # 单张图绘制脚本的修复轮数
    mvp_smoke_timeout: int = 90    # smoke 运行超时（秒）
''')

# ------------------------------------------------------------- pipeline ---- #
add("paper2code/pipeline.py", "pipeline-sig",
    '    audio: bool = True,\n) -> Dict[str, Any]:\n    """处理单篇论文，返回 manifest 字典。"""\n',
    '    audio: bool = True,\n    mvp: Optional[bool] = None,  # MVP-PATCH:pipeline-sig（None=按 settings.mvp_enabled）\n) -> Dict[str, Any]:\n    """处理单篇论文，返回 manifest 字典。"""\n',
    "replace")

add("paper2code/pipeline.py", "pipeline-run",
    '    manifest["warnings"] = list(dict.fromkeys(w for w in manifest["warnings"] if w))\n    (pdir / "manifest.json")',
    '''    # ---------------- MVP demo + 图表绘制说明 ---------------- #  # MVP-PATCH:pipeline-run
    if (settings.mvp_enabled if mvp is None else mvp):
        try:
            from .mvp import build_mvp

            mres = build_mvp(paper, pdir / "mvp", provider=provider)
            manifest["artifacts"]["mvp"] = {
                "ok": mres.ok,
                "generator": mres.generator,
                "demo_ok": mres.demo_ok,
                "report": mres.report_path,
                "figures_md": str(pdir / "mvp" / mres.figures_md) if mres.figures_md else "",
                "n_figure_specs": len(mres.figure_specs),
                "n_figures_drawn": sum(1 for v in mres.figures.values() if v.get("files")),
            }
            manifest["warnings"].extend(mres.warnings)
        except Exception as exc:  # MVP 失败不能拖垮讲解与复现产物
            manifest["warnings"].append(f"MVP 阶段失败（不影响讲解与复现产物）：{type(exc).__name__}: {exc}")

''', "before")

# ------------------------------------------------------------------ cli ---- #
add("paper2code/cli.py", "cli-run-flag",
    '    r.add_argument("--no-pseudo-run", action="store_true", help="情形 C 只翻译不执行")\n',
    '    r.add_argument("--no-mvp", action="store_true", help="跳过 MVP demo / 图表绘制说明生成  # MVP-PATCH:cli-run-flag")\n')
add("paper2code/cli.py", "cli-batch-flag",
    '    b.add_argument("--no-pseudo-run", action="store_true")\n',
    '    b.add_argument("--no-mvp", action="store_true")  # MVP-PATCH:cli-batch-flag\n')
add("paper2code/cli.py", "cli-mvp-parser",
    "    # -- list / info ------------------------------------------------------ #\n",
    '''    # -- mvp（# MVP-PATCH:cli-mvp-parser）---------------------------------- #
    m = sub.add_parser("mvp", help="只对一篇论文生成 MVP demo + 图表绘制说明（paper_id 或论文路径）")
    m.add_argument("source", help="论文文件 / 论文包目录 / arXiv ID / incoming/ 下的 paper_id")
    m.add_argument("-o", "--out", type=Path, default=None, help="输出根目录（默认 outputs/）")
    m.add_argument("--explain", default="", help=argparse.SUPPRESS)  # 共享后续的参数解析，不使用
    m.add_argument("--no-run", action="store_true", help="只生成代码，不执行")
    m.add_argument("--no-llm", action="store_true", help="只用离线脚手架")
    m.add_argument("--llm", choices=["offline", "openai"], default=None)
    m.add_argument("--model", default=None)
    m.add_argument("--base-url", default=None)
    m.add_argument("--api-key", default=None, help="一次性测试用；优先用环境变量 P2C_LLM_API_KEY")
    m.add_argument("--offline", action="store_true", help="Alias for --llm offline")
    m.add_argument("--pdf-backend", choices=["hybrid", "docling", "pymupdf"], default=None)

''', "before")
add("paper2code/cli.py", "cli-run-call",
    '            audio=not args.no_audio,\n        )\n        if not args.quiet:',
    '            audio=not args.no_audio,\n            mvp=False if args.no_mvp else None,  # MVP-PATCH:cli-run-call\n        )\n        if not args.quiet:',
    "replace")
add("paper2code/cli.py", "cli-batch-call",
    '            audio=not args.no_audio,\n        )\n        for m in results:',
    '            audio=not args.no_audio,\n            mvp=False if args.no_mvp else None,  # MVP-PATCH:cli-batch-call\n        )\n        for m in results:',
    "replace")
add("paper2code/cli.py", "cli-mvp-cmd",
    '    if cmd == "run":\n        manifest = run_paper(',
    '''    if cmd == "mvp":  # MVP-PATCH:cli-mvp-cmd
        from .ingest import load_paper
        from .mvp import build_mvp

        src = args.source
        if not Path(src).exists() and (settings.workspace / "incoming" / src).exists():
            src = str(settings.workspace / "incoming" / src)
        paper = load_paper(src)
        out_root = Path(args.out) if args.out else settings.output_dir
        pdir = out_root / paper.id
        pdir.mkdir(parents=True, exist_ok=True)
        res = build_mvp(paper, pdir / "mvp", execute=not args.no_run, use_llm=False if args.no_llm else None)
        print(f"\\n=== {paper.id} | MVP ===")
        print(f"  生成方式：{res.generator} · demo 运行：{'未执行' if res.demo_ok is None else ('通过' if res.demo_ok else '失败')}")
        print(f"  图表说明 {len(res.figure_specs)} 张 · 已出图 {sum(1 for v in res.figures.values() if v.get('files'))} 张")
        print(f"  报告：{res.report_path}")
        for w in res.warnings[:6]:
            print(f"    * {w}")
        return 0 if res.ok else 2

    if cmd == "run":
        manifest = run_paper(''', "replace")
add("paper2code/cli.py", "cli-print",
    '    if m.get("warnings") and not brief:\n',
    '''    mv = art.get("mvp")  # MVP-PATCH:cli-print
    if mv:
        print(
            f"  MVP：{mv.get('generator')} · demo {'通过' if mv.get('demo_ok') else ('未执行' if mv.get('demo_ok') is None else '失败')} · "
            f"图表说明 {mv.get('n_figure_specs')} 张 / 已出图 {mv.get('n_figures_drawn')} 张"
        )
        if not brief and mv.get("report"):
            print(f"    - report: {mv['report']}")
''', "before")

# ------------------------------------------------------------------ api ---- #
add("paper2code/api.py", "api-flag-init",
    '                "has_repro": False,\n',
    '                "has_mvp": False,  # MVP-PATCH:api-flag-init\n')
add("paper2code/api.py", "api-flag-fill",
    '        item["has_repro"] = has_rp\n',
    '        item["has_mvp"] = bool(out_dir and (out_dir / "mvp" / "MVP_REPORT.md").exists())  # MVP-PATCH:api-flag-fill\n')
add("paper2code/api.py", "api-endpoints",
    '    @app.get("/api/papers")\n    def api_papers() -> List[Dict[str, Any]]:\n',
    '''    # MVP-PATCH:api-endpoints
    @app.post("/api/mvp/{paper_id}")
    def api_mvp_start(paper_id: str) -> Dict[str, Any]:
        """后台（重新）生成某篇论文的 MVP demo + 图表绘制说明。"""
        try:
            pid = _validate_paper_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        source = _resolve_run_source("", pid)
        if not source or not Path(source).exists():
            raise HTTPException(status_code=404, detail=f"找不到论文输入：{pid}")
        key = "mvp:" + pid
        with _RUN_LOCK:
            if (_RUN_JOBS.get(key) or {}).get("status") == "running":
                return {"ok": True, "accepted": False, "status": "running", "paper_id": pid}
            _RUN_JOBS[key] = {"status": "running", "paper_id": pid}

        def _bg() -> None:
            try:
                from .ingest import load_paper
                from .mvp import build_mvp

                paper = load_paper(source)
                res = build_mvp(paper, _outputs_root() / paper.id / "mvp")
                rec = {"status": "ok", "paper_id": pid, "ok": res.ok, "generator": res.generator}
            except Exception as exc:
                rec = {"status": "error", "paper_id": pid, "detail": _scrub_secret(f"{type(exc).__name__}: {exc}")}
            with _RUN_LOCK:
                _RUN_JOBS[key] = rec

        threading.Thread(target=_bg, daemon=True, name=f"p2c-mvp-{pid}").start()
        return {"ok": True, "accepted": True, "status": "running", "paper_id": pid}

    @app.get("/api/mvp/{paper_id}/status")
    def api_mvp_status(paper_id: str) -> Dict[str, Any]:
        try:
            pid = _validate_paper_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _job_snapshot("mvp:" + pid)

''', "before")
add("paper2code/api.py", "api-detail-html",
    "  html+='<div class=\"sec\"><h2>其他产物</h2><div class=\"links\">';\n",
    '''  /* MVP-PATCH:api-detail-html */
  html+='<div class="sec"><h2>MVP 演示 / 图表绘制说明</h2>';
  if(d.has_mvp){
    html+='<div class="links">'
      +'<a href="/view?p='+encodeURIComponent(PID+'/mvp/MVP_REPORT.md')+'" target="_blank">MVP 报告</a>'
      +'<a href="/view?p='+encodeURIComponent(PID+'/mvp/figures/FIGURES.md')+'" target="_blank">图表绘制说明</a>'
      +'<a href="/files/'+encodeURIComponent(PID)+'/mvp/demo/" target="_blank">demo 代码</a>'
      +'<a href="/files/'+encodeURIComponent(PID)+'/mvp/figures/out/" target="_blank">图表产物</a>'
      +'<button type="button" class="tts" id="mvpGo">重新生成</button></div><div id="mvpMsg"></div>';
  }else if(d.has_outputs){
    html+=miss('mvp/MVP_REPORT.md')+'<div class="links"><button type="button" class="tts" id="mvpGo">生成 MVP</button></div><div id="mvpMsg"></div>';
  }else html+=miss('mvp');
  html+='</div>';

''', "before")
add("paper2code/api.py", "api-detail-js",
    "  document.getElementById('body').innerHTML=html;\n",
    '''
  /* MVP-PATCH:api-detail-js */
  const mg=document.getElementById('mvpGo');
  if(mg){
    const mm=document.getElementById('mvpMsg');
    mg.onclick=async()=>{
      mg.disabled=true; mm.textContent='生成中…（离线几秒，接大模型约 1–3 分钟）';
      try{
        const rr=await fetch('/api/mvp/'+encodeURIComponent(PID),{method:'POST'});
        const dd=await rr.json();
        if(!rr.ok){mm.textContent='失败：'+(dd.detail||'');mg.disabled=false;return}
        const poll=async()=>{
          const s=await (await fetch('/api/mvp/'+encodeURIComponent(PID)+'/status')).json();
          if(s.status==='ok'){load();return}
          if(s.status==='error'){mm.textContent='失败：'+(s.detail||'');mg.disabled=false;return}
          setTimeout(poll,2000);
        };
        poll();
      }catch(e){mm.textContent='请求失败：'+e;mg.disabled=false}
    };
  }
''')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".", help="仓库根目录（含 paper2code/ 包）")
    ap.add_argument("--check", action="store_true", help="只检查锚点")
    ap.add_argument("--revert", action="store_true", help="从 *.mvp.bak 还原")
    ap.add_argument("--skip-api", action="store_true", help="跳过 api.py 的 5 处锚点（精简版 Dashboard 需人工接线）")
    a = ap.parse_args()
    root = Path(a.repo).resolve()
    files = sorted({f for f, *_ in P})

    if a.revert:
        n = 0
        for f in files:
            bak = root / (f + ".mvp.bak")
            if bak.exists():
                shutil.copy2(bak, root / f)
                bak.unlink()
                n += 1
                print("还原", f)
        print("已还原 %d 个文件" % n)
        return 0

    state = {}
    for f in files:
        path = root / f
        if not path.exists():
            print("缺少文件：%s（--repo 是否指向仓库根目录？）" % f)
            return 2
        raw = path.read_bytes().decode("utf-8")
        crlf = "\r\n" in raw
        state[f] = [raw.replace("\r\n", "\n"), crlf, raw]

    errors, plan, skipped = [], [], 0
    for f, pid, anchor, text, where in P:
        src = state[f][0]
        if "MVP-PATCH:" + pid in src:
            skipped += 1
            continue
        n = src.count(anchor)
        if n != 1:
            errors.append("%s [%s]：锚点匹配 %d 处（需要恰好 1 处）——文件可能已被改动" % (f, pid, n))
            continue
        plan.append((f, pid, anchor, text, where))
    if errors:
        print("补丁未应用（没有改动任何文件）：")
        for e in errors:
            print("  -", e)
        return 1
    if a.check:
        print("检查通过：%d 处待应用，%d 处已存在。" % (len(plan), skipped))
        return 0

    for f, pid, anchor, text, where in plan:
        src = state[f][0]
        if where == "before":
            new = src.replace(anchor, text + anchor, 1)
        elif where == "replace":
            new = src.replace(anchor, text, 1)
        else:
            new = src.replace(anchor, anchor + text, 1)
        state[f][0] = new
    for f in files:
        src, crlf, orig = state[f]
        out = src.replace("\n", "\r\n") if crlf else src
        if out != orig:
            bak = root / (f + ".mvp.bak")
            if not bak.exists():
                shutil.copy2(root / f, bak)
            (root / f).write_bytes(out.encode("utf-8"))
            print("已修改", f)
    print("完成：应用 %d 处，已存在 %d 处。回滚：python tools/apply_mvp_patch.py --revert" % (len(plan), skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
