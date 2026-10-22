"""本地 Web 服务（FastAPI）。

启动::

    python -m paper2code serve --port 8000

页面：

* ``/``              运行总览 Dashboard
* ``/view?p=<path>`` Markdown 产物阅读器（复现报告、播客脚本、大纲）
* ``/files/...``     直接访问 ``outputs/single/`` 下的任意产物（课件 HTML、音频、图表）

接口：

* ``GET  /api/runs``           列出全部运行
* ``GET  /api/runs/{pid}``     单个运行的 manifest
* ``POST /api/run``            提交一篇论文（同步执行，MVP 简化处理）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import get_settings
from .ingest import KIND_LABELS, classify_source, paper_id_for_source
from .pipeline import list_runs
from .progress import read_snapshot
from .taskqueue import PENDING, RUNNING, ensure_workers, get_queue

try:  # FastAPI 为可选依赖：没装也不影响 CLI 使用
    from fastapi import FastAPI, HTTPException, Query, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel

    _HAS_FASTAPI = True
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False

    class BaseModel:  # type: ignore
        pass


def queue_snapshot(limit: int = 30) -> Dict[str, Any]:
    """队列视图（roadmap M5-①）：任务列表 + 计数 + 并发上限 + 数据库位置。"""
    q = get_queue()
    cfg = get_settings().queue_config()
    return {
        "ok": True,
        "stats": q.stats(),
        "concurrency": cfg["concurrency"],
        "timeout_s": cfg["timeout_s"],
        "db": cfg["db"],
        "tasks": [t.to_dict() for t in q.list(limit=limit)],
    }


def confirm_required() -> str:
    """M4-⑤「首次运行需确认」：返回必须让用户先确认的理由（空串 = 无需确认）。

    放在模块级（而不是 FastAPI 块内）是为了**不装 fastapi 也能测**这条安全语义。
    """
    from .reproduce import runner

    if not get_settings().sandbox_require_confirm:
        return ""
    pol = runner.resolve_policy()
    if pol.mode == "container":
        return ""
    return ("执行沙箱为「%s」（%s，风险：%s）。运行生成的论文代码 = 在本机执行任意代码，"
            "请先确认；或把 sandbox_mode 设为 container 使用容器隔离。"
            % (pol.mode, pol.isolation, "高" if pol.risk == "high" else "低"))


if _HAS_FASTAPI:
    app = FastAPI(title="paper2code", version="0.1.0", description="论文学习工具：讲解材料 + 复现结果")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    class RunRequest(BaseModel):
        source: str
        explain: str = "courseware,podcast"
        reproduce: bool = True
        audio: bool = True
        resume: bool = False   # M1-⑤：跳过上次已完成且产物仍在的阶段
        confirm: bool = False  # M4-⑤：非容器模式下的「首次运行需确认」

    def _confirm_gate(confirm: bool) -> None:
        """「首次运行需确认」（roadmap M4-⑤）：本地模式意味着生成的代码真的会在本机执行。"""
        reason = confirm_required()
        if reason and not confirm:
            raise HTTPException(status_code=409, detail=reason)

    @app.get("/api/runs")
    def api_runs() -> List[Dict[str, Any]]:
        return list_runs()

    @app.get("/api/sandbox")
    def api_sandbox() -> Dict[str, Any]:
        """当前执行沙箱策略 + 风险提示（供页面横幅渲染）。"""
        from .reproduce import runner

        pol = runner.resolve_policy()
        return {"ok": True, "policy": pol.to_dict(), "notice": runner.sandbox_notice(),
                "require_confirm": get_settings().sandbox_require_confirm}

    @app.get("/api/runs/{pid}")
    def api_run(pid: str) -> Dict[str, Any]:
        for r in list_runs():
            if r.get("paper_id") == pid:
                return r
        raise HTTPException(status_code=404, detail=f"未找到运行记录：{pid}")

    def _source_for(paper_id: str) -> str:
        """论文输入的定位（run 记录 → incoming → tests/inputs/single）。"""
        rec = next((r for r in list_runs() if r.get("paper_id") == paper_id), None)
        source = (rec or {}).get("source") or ""
        if not Path(source).exists():
            for base in (get_settings().workspace / "incoming", get_settings().single_input_dir):
                cand = base / paper_id
                if cand.exists():
                    source = str(cand)
                    break
        if not source or not Path(source).exists():
            raise HTTPException(status_code=404, detail=f"找不到论文输入：{paper_id}")
        return source

    # -- MVP 自动生成（图表绘制说明 + demo 代码） ---------------------------- #
    @app.post("/api/mvp/{paper_id}")
    def api_mvp_start(paper_id: str, confirm: bool = False, execute: bool = True) -> Dict[str, Any]:
        """把「重新生成 MVP」作为一个队列任务提交（M5-①：有并发上限、可取消、重启不丢）。"""
        _confirm_gate(confirm)
        source = _source_for(paper_id)
        q = get_queue()
        if any(t.kind == "mvp" and t.status in (PENDING, RUNNING)
               for t in q.list(limit=50) if t.label == paper_id):
            return {"ok": True, "accepted": False, "status": "running", "paper_id": paper_id}
        tid = q.enqueue("mvp", source, {"execute": execute}, label=paper_id)
        ensure_workers()
        return {"ok": True, "accepted": True, "task_id": tid, "status": PENDING, "paper_id": paper_id}

    class ModifyRequest(BaseModel):
        instruction: str
        confirm: bool = False

    @app.post("/api/mvp/{paper_id}/modify")
    def api_mvp_modify(paper_id: str, req: ModifyRequest) -> Dict[str, Any]:
        """「改一次」（M5-②）：提交一条修改指令；上一版会被存档，并生成版本间 diff。"""
        _confirm_gate(req.confirm)
        instruction = (req.instruction or "").strip()
        if not instruction:
            raise HTTPException(status_code=400, detail="instruction 不能为空")
        source = _source_for(paper_id)
        tid = get_queue().enqueue("mvp", source, {"modify": instruction}, label=paper_id)
        ensure_workers()
        return {"ok": True, "accepted": True, "task_id": tid, "status": PENDING,
                "paper_id": paper_id, "instruction": instruction}

    @app.get("/api/mvp/{paper_id}/versions")
    def api_mvp_versions(paper_id: str) -> Dict[str, Any]:
        """版本清单（M5-②）：当前版本 + 历史版本 + 各版 diff 路径。"""
        from .mvp import iterate

        out = get_settings().output_dir / paper_id / "mvp"
        return {"ok": True, "paper_id": paper_id, "versions": iterate.summarize(out)}

    @app.get("/api/mvp/{paper_id}/status")
    def api_mvp_status(paper_id: str) -> Dict[str, Any]:
        tasks = [t for t in get_queue().list(limit=50) if t.kind == "mvp" and t.label == paper_id]
        if not tasks:
            return {"status": "idle", "paper_id": paper_id}
        t = tasks[0]
        return {"status": t.status, "paper_id": paper_id, "task_id": t.id,
                "error": t.error, "result": t.result}

    @app.post("/api/run")
    def api_run_post(req: RunRequest) -> Dict[str, Any]:
        """把一篇论文作为队列任务提交，立刻返回 task_id（M5-①：队列 + 并发上限 + 可取消）。"""
        _confirm_gate(req.confirm)
        pid = paper_id_for_source(req.source)     # 与 loader 完全同一套规则（arXiv/DOI/URL 才不会对不上）
        explain = tuple(k.strip() for k in req.explain.split(",") if k.strip())
        tid = get_queue().enqueue("paper", req.source,
                                  {"explain": explain, "reproduce": req.reproduce,
                                   "audio": req.audio, "resume": req.resume}, label=pid)
        ensure_workers()
        return {"ok": True, "accepted": True, "task_id": tid, "status": PENDING, "paper_id": pid}

    class BatchRequest(BaseModel):
        sources: List[str]
        explain: str = "courseware,podcast"
        reproduce: bool = True
        audio: bool = True
        resume: bool = False
        confirm: bool = False

    @app.post("/api/batch")
    def api_batch_post(req: BatchRequest) -> Dict[str, Any]:
        """批量提交（M5-① 的批处理视图）：一次入队多篇，排队执行、可逐个取消。"""
        _confirm_gate(req.confirm)
        if not req.sources:
            raise HTTPException(status_code=400, detail="sources 不能为空")
        explain = tuple(k.strip() for k in req.explain.split(",") if k.strip())
        q = get_queue()
        ids: List[Dict[str, Any]] = []
        for src in req.sources:
            src = (src or "").strip()
            if not src:
                continue
            tid = q.enqueue("paper", src, {"explain": explain, "reproduce": req.reproduce,
                                           "audio": req.audio, "resume": req.resume},
                            label=paper_id_for_source(src))
            ids.append({"task_id": tid, "source": src})
        if ids:
            ensure_workers()
        return {"ok": True, "accepted": True, "n": len(ids), "tasks": ids}

    # -- 来源识别 / 文件上传 / 删除历史（M5 后的 UI 整理） ------------------- #
    @app.get("/api/classify")
    def api_classify(source: str = Query(..., description="待识别的来源串")) -> Dict[str, Any]:
        """识别输入形态：arXiv / DOI / 直链 PDF / 网页 / 本机路径 / 论文包目录。

        前端用它显示「识别为：xxx」，并保证 /api/run 与产物目录用的是同一个 paper_id。
        """
        kind = classify_source(source)
        out: Dict[str, Any] = {"ok": True, "source": source, "kind": kind,
                               "kind_label": KIND_LABELS.get(kind, kind)}
        if kind != "empty":
            out["paper_id"] = paper_id_for_source(source)
        if kind == "doi":
            out["notice"] = "DOI 会先试 doi.org 内容协商取 PDF；被出版商订阅墙挡住时会明确报错。"
        elif kind == "arxiv":
            out["notice"] = "arXiv 优先抓 HTML 全文，退回 PDF。"
        elif kind == "path_missing":
            out["notice"] = "本机没有这个路径；要上传文件请用「选择文件 / 拖拽」。"
        return out

    @app.post("/api/upload")
    async def api_upload(request: Request, filename: str = Query(..., description="原始文件名"),
                         confirm: bool = False, resume: bool = False) -> Dict[str, Any]:
        """上传文件（PDF / md / html / txt / zip）→ 落盘 ``incoming/<paper_id>/`` → 入队。

        走**裸 body**（``await request.body()``）而非 multipart：这样不引入
        ``python-multipart`` 依赖，离线环境零新增安装。
        """
        from .ingest import save_upload

        _confirm_gate(confirm)
        data = await request.body()
        if not data:
            raise HTTPException(status_code=400, detail="上传内容为空")
        try:
            pid, dest = save_upload(filename, data, get_settings().workspace / "incoming")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            # 写盘失败（典型：Windows MAX_PATH 越界）是可解释的输入/环境问题，
            # 必须回到 400 并说明原因，不能变成只有「HTTP 500」的空白报错。
            raise HTTPException(status_code=400, detail="写入上传文件失败：%s" % exc) from exc
        tid = get_queue().enqueue("paper", str(dest),
                                  {"explain": ("courseware", "podcast"), "reproduce": True,
                                   "audio": True, "resume": resume}, label=pid)
        ensure_workers()
        return {"ok": True, "accepted": True, "task_id": tid, "status": PENDING,
                "paper_id": pid, "saved": str(dest), "bytes": len(data)}

    @app.delete("/api/runs/{pid}")
    def api_delete_run(pid: str) -> Dict[str, Any]:
        """删除一次运行：产物目录 + index 记录 + 队列任务 + 上传原件。"""
        from .pipeline import delete_run

        res = delete_run(pid)
        if not res.get("ok"):
            raise HTTPException(status_code=400, detail=res.get("reason") or "删除失败")
        return res

    @app.get("/api/tasks")
    def api_tasks(limit: int = 30) -> Dict[str, Any]:
        return queue_snapshot(limit=limit)

    @app.get("/api/tasks/{task_id}")
    def api_task(task_id: str) -> Dict[str, Any]:
        """单个任务：状态 + 阶段进度（运行中直接读 progress.json，不经过数据库）。"""
        t = get_queue().get(task_id)
        if t is None:
            raise HTTPException(status_code=404, detail=f"未找到任务：{task_id}")
        snap = None
        if t.status == RUNNING:
            snap = read_snapshot(get_settings().output_dir / str(t.label) / "progress.json")
        return {"ok": True, "task": t.to_dict(), "progress": snap or t.progress}

    @app.post("/api/tasks/{task_id}/cancel")
    def api_task_cancel(task_id: str) -> Dict[str, Any]:
        r = get_queue().request_cancel(task_id)
        if not r.get("ok"):
            raise HTTPException(status_code=409, detail=r.get("reason") or "无法取消")
        return {"ok": True, **r}

    @app.get("/api/run/{paper_id}/progress")
    def api_run_progress(paper_id: str) -> Dict[str, Any]:
        """阶段进度：`progress.json` 快照 + 该论文最近一个队列任务的状态。"""
        tasks = [t for t in get_queue().list(limit=50) if t.label == paper_id and t.kind == "paper"]
        job = ({"status": "idle"} if not tasks else
               {"status": tasks[0].status, "task_id": tasks[0].id, "detail": tasks[0].error})
        snap = read_snapshot(get_settings().output_dir / paper_id / "progress.json")
        return {"ok": True, "paper_id": paper_id, "job": job, "progress": snap,
                "tasks": [t.to_dict() for t in tasks[:5]]}

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return _DASHBOARD_HTML

    @app.get("/view", response_class=HTMLResponse)
    def view(p: str = Query(..., description="outputs 下的相对路径")) -> str:
        return _VIEWER_HTML

    @app.on_event("startup")
    def _on_startup() -> None:
        """M5-①/④：启动时收尾孤儿任务、裁掉陈旧记录、拉起 worker 池、加载插件。

        服务重启后排队任务不会「没人跑」；上一次进程死掉时还停在 ``running`` 的任务会被
        如实记为失败（原因写明是重启中断，而不是让它永远转圈）；插件导入失败只记 warning。
        """
        try:
            get_queue().reclaim_orphans()
            get_queue().prune(keep=200)
            ensure_workers()
        except Exception:
            pass
        try:
            from . import plugins

            plugins.ensure_loaded()
        except Exception:
            pass

    settings = get_settings().ensure_dirs()
    app.mount("/files", StaticFiles(directory=str(settings.output_dir), html=True), name="files")


# --------------------------------------------------------------------------- #
_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>paper2code · 论文学习工具</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#f5f7fb;--card:#fff;--ink:#1b2130;--muted:#5d6b85;--line:#e3e8f2;
--brand:#4f6bed;--brand2:#8b5cf6;--accent:#0ea5a4;--pass:#16a34a;--warn:#d97706;--fail:#dc2626;}
body{background:radial-gradient(1000px 560px at 8% -10%,#e8edff,transparent 60%),
radial-gradient(860px 480px at 104% 0%,#eafaf7,transparent 55%),var(--bg);
color:var(--ink);font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
min-height:100vh;padding:34px 22px 70px}
.wrap{max-width:1180px;margin:0 auto}
header{margin-bottom:26px}
h1{font-size:31px;font-weight:800;letter-spacing:-.02em}
h1 span{background:linear-gradient(120deg,var(--brand),var(--brand2),var(--accent));-webkit-background-clip:text;background-clip:text;color:transparent}
.sub{color:var(--muted);margin-top:8px;font-size:14.5px}
.submit{display:flex;gap:10px;margin:22px 0 30px;flex-wrap:wrap}
input[type=text]{flex:1;min-width:280px;padding:12px 15px;border:1px solid var(--line);border-radius:12px;font-size:14.5px;background:#fff}
input[type=text]:focus{outline:none;border-color:var(--brand);box-shadow:0 0 0 3px rgba(79,107,237,.12)}
button{padding:12px 22px;border:none;border-radius:12px;background:linear-gradient(135deg,var(--brand),var(--brand2));
color:#fff;font-size:14.5px;font-weight:650;cursor:pointer;transition:.16s}
button:hover{transform:translateY(-1px);box-shadow:0 8px 22px rgba(79,107,237,.3)}
button:disabled{opacity:.6;cursor:not-allowed;transform:none}
.grid{display:grid;gap:18px;grid-template-columns:repeat(auto-fill,minmax(360px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:22px 24px;
box-shadow:0 8px 26px rgba(24,39,75,.07);transition:.18s;position:relative;overflow:hidden}
.card:hover{transform:translateY(-2px);box-shadow:0 14px 34px rgba(24,39,75,.13)}
.card::before{content:"";position:absolute;inset:0 0 auto 0;height:4px;
background:linear-gradient(90deg,var(--brand),var(--brand2),var(--accent))}
.card h3{font-size:18px;font-weight:750;line-height:1.4;margin-bottom:6px}
.meta{color:var(--muted);font-size:13px;margin-bottom:14px}
.tags{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:15px}
.tag{font-size:12px;padding:4px 11px;border-radius:999px;background:#eef2ff;color:#3d4ea8;font-weight:650}
.tag.pass{background:#e8f8ee;color:#15803d}.tag.warn{background:#fef4e2;color:#b45309}
.tag.fail{background:#fdeaea;color:#b91c1c}.tag.gray{background:#f1f3f9;color:#5d6b85}
.links{display:flex;gap:9px;flex-wrap:wrap;margin-top:4px}
.links a{font-size:13px;text-decoration:none;color:var(--brand);border:1px solid #dfe4f3;
padding:6px 12px;border-radius:9px;background:#fbfcff;font-weight:600;transition:.14s}
.links a:hover{background:var(--brand);color:#fff;border-color:var(--brand)}
.empty{text-align:center;padding:76px 20px;color:var(--muted);background:#fff;border:1px dashed var(--line);border-radius:18px}
.empty code{background:#f1f3f9;padding:3px 8px;border-radius:7px;font-size:13.5px;color:#39457a}
#toast{margin-top:14px;font-size:13.8px;color:var(--muted)}
#prog{margin:0 0 22px}
.chk{display:flex;align-items:center;gap:6px;font-size:13.5px;color:var(--muted);white-space:nowrap}
.stage{font-size:13.5px;padding:6px 12px;border-radius:9px;background:#f8faff;
border:1px solid var(--line);margin-bottom:6px}
.err{color:var(--fail)}
.banner{margin:0 0 18px;padding:13px 18px;border-radius:14px;font-size:13.6px;line-height:1.65;border:1px solid}
.banner.ok{background:#eef7ff;border-color:#cfe4ff;color:#1d4f8a}
.banner.risk{background:#fff7e8;border-color:#ffe0b0;color:#9a5b00}
.panel{background:#fff;border:1px solid var(--line);border-radius:16px;padding:16px 18px;margin:0 0 20px;
box-shadow:0 6px 18px rgba(24,39,75,.05)}
.panel h4{font-size:15px;font-weight:750;margin-bottom:10px}
.qrow{display:flex;align-items:center;gap:10px;font-size:13.4px;padding:7px 0;border-top:1px solid #f1f3f9;flex-wrap:wrap}
.qrow:first-of-type{border-top:none}
.qstat{font-size:11.5px;padding:3px 9px;border-radius:999px;font-weight:700;background:#eef2ff;color:#3d4ea8}
.qstat.pending{background:#f1f3f9;color:#5d6b85}.qstat.running{background:#e8f1ff;color:#1d4f8a}
.qstat.done{background:#e8f8ee;color:#15803d}.qstat.failed{background:#fdeaea;color:#b91c1c}
.qstat.cancelled{background:#fef4e2;color:#b45309}
.qbtn{font-size:12px;padding:4px 10px;border-radius:8px;border:1px solid #dfe4f3;background:#fbfcff;
color:#3d4ea8;cursor:pointer;font-weight:600}
.qbtn:hover{background:#eef2ff}
textarea{width:100%;min-height:64px;padding:10px 12px;border:1px solid var(--line);border-radius:12px;
font:13.5px/1.6 ui-monospace,Menlo,Consolas,monospace;resize:vertical}
.tabs{display:flex;gap:8px;margin:4px 0 22px;flex-wrap:wrap}
.tabs button{background:#fff;color:var(--muted);border:1px solid var(--line);border-radius:12px;
padding:10px 18px;font-size:14.5px;font-weight:650;cursor:pointer;transition:.16s}
.tabs button:hover{background:#f7f9ff;transform:none;box-shadow:none}
.tabs button.on{background:linear-gradient(135deg,var(--brand),var(--brand2));color:#fff;border-color:transparent}
.drop{border:2px dashed var(--line);border-radius:14px;padding:20px;text-align:center;color:var(--muted);
background:#fbfcff;margin:12px 0 6px;font-size:13.8px;cursor:pointer;transition:.16s}
.drop:hover,.drop.hot{border-color:var(--brand);background:#eef2ff;color:#3d4ea8}
.hint{font-size:13px;color:var(--muted);margin-top:8px;line-height:1.7}
.hint b{color:#3d4ea8}
.hint code{background:#f1f3f9;padding:2px 6px;border-radius:6px;font-size:12.5px}
.ghost{background:#fff!important;color:#3d4ea8!important;border:1px solid #dfe4f3!important}
.danger{color:#b91c1c!important;border-color:#f3d7d7!important;background:#fff!important}
</style></head>
<body><div class="wrap">
<header>
  <h1>paper2code · <span>论文学习工具</span></h1>
  <div class="sub">提交论文 → 自动生成讲解材料（课件 / 知识播客）与复现结果（表格与图表重现）。支持链接 / 本机路径 / 上传文件，支持单篇与批量。</div>
</header>
<div id="banner"></div>
<div class="tabs">
  <button data-tab="single" class="on">① 单篇提交</button>
  <button data-tab="batch">② 批量提交</button>
  <button data-tab="history">③ 历史记录</button>
</div>

<section id="tab-single">
  <div class="panel">
    <h4>提交一篇文章</h4>
    <div class="meta">三种输入方式任选其一：① 直接填链接 / 本机路径；② 拖拽文件到下面的框；③ 点击框选择文件。</div>
    <div class="submit" style="margin:14px 0 4px">
      <input type="text" id="src" placeholder="https://doi.org/10.1021/acs.jcim.4c01641 · 1706.03762 · samples/01_open_source"/>
      <button id="go">生成</button>
    </div>
    <div class="hint" id="cls"></div>
    <div class="drop" id="drop">把 PDF / md / html / txt / zip 拖到这里，或 <b>点击选择文件</b>
      <input type="file" id="file" accept=".pdf,.md,.markdown,.html,.htm,.txt,.tex,.zip" style="display:none"/>
    </div>
    <div class="submit" style="margin:4px 0 0">
      <label class="chk"><input type="checkbox" id="resume"/> 续跑（跳过上次已完成的阶段）</label>
      <label class="chk" id="confirmWrap"><input type="checkbox" id="confirm"/> 我确认在本机运行生成的代码</label>
    </div>
    <div class="hint" id="confwarn" style="display:none;color:#b91c1c"></div>
    <div id="toast"></div>
    <div id="prog"></div>
  </div>
</section>

<section id="tab-batch" hidden>
  <div class="panel">
    <h4>批量提交</h4>
    <div class="meta">每行一条来源（链接 / 本机路径 / arXiv 编号）；也可以载入一个 txt 列表文件，或一次选多个文件上传。</div>
    <textarea id="batch" style="min-height:130px" placeholder="https://doi.org/10.1021/acs.jcim.4c01641&#10;1706.03762&#10;samples/01_open_source"></textarea>
    <div class="submit" style="margin:10px 0 0">
      <button id="batchgo">提交列表</button>
      <button id="txtbtn" class="ghost">载入 txt 列表</button>
      <button id="pdfbtn" class="ghost">选择多个文件上传</button>
      <label class="chk"><input type="checkbox" id="bresume"/> 续跑</label>
      <label class="chk" id="bconfirmWrap"><input type="checkbox" id="bconfirm"/> 我确认在本机运行生成的代码</label>
    </div>
    <div class="hint" id="bconfwarn" style="display:none;color:#b91c1c"></div>
    <input type="file" id="txtfile" accept=".txt,.csv,.list,.md" style="display:none"/>
    <input type="file" id="pdfs" multiple accept=".pdf,.md,.markdown,.html,.htm,.txt,.tex,.zip" style="display:none"/>
    <div id="btoast" class="hint"></div>
  </div>
</section>

<section id="tab-history" hidden>
  <div class="panel" id="queue"></div>
  <div id="htoast" class="hint" style="margin-bottom:14px"></div>
  <div id="hprog"></div>
  <div class="grid" id="grid"></div>
</section>
</div>
<script>
const grid=document.getElementById('grid'),toast=document.getElementById('toast'),go=document.getElementById('go');
function esc(s){return String(s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function tag(t,c){return '<span class="tag '+(c||'')+'">'+esc(t)+'</span>'}
function base(p){return String(p||'').split(/[\\\\/]/).pop()}
function figrel(p){const s=String(p||'').replace(/\\\\/g,'/'),i=s.indexOf('/reproduce/');return i>=0?s.slice(i+11):base(s)}
async function load(){
  const runs=await (await fetch('/api/runs')).json();
  if(!runs.length){grid.innerHTML='<div class="empty" style="grid-column:1/-1">还没有生成任何结果。<br><br>先用命令行跑一篇：<code>python -m paper2code run samples/01_open_source</code><br><br>或直接在上方输入框提交。</div>';return}
  grid.innerHTML=runs.map(r=>{
    const a=r.artifacts||{},ex=a.explain,rp=a.reproduce,st=r.stats||{};
    const ck=(rp&&rp.checks)||{};
    const pid=r.paper_id;
    let links='';
    if(ex&&ex.courseware)links+='<a href="/files/'+pid+'/explain/courseware.html" target="_blank">📊 课件</a>';
    if(ex&&ex.podcast_audio)links+='<a href="/files/'+pid+'/explain/'+esc(base(ex.podcast_audio))+'" target="_blank">🎧 播客音频</a>';
    if(ex&&ex.podcast_script)links+='<a href="/view?p='+encodeURIComponent(pid+'/explain/podcast_script.md')+'" target="_blank">📝 播客稿</a>';
    if(rp&&rp.report_html)links+='<a href="/files/'+pid+'/reproduce/repro_report.html" target="_blank">🔬 复现报告</a>';
    else if(rp&&rp.report)links+='<a href="/view?p='+encodeURIComponent(pid+'/reproduce/repro_report.md')+'" target="_blank">🔬 复现报告</a>';
    if(rp&&rp.tech_guide)links+='<a href="/view?p='+encodeURIComponent(pid+'/reproduce/TECH_GUIDE.md')+'" target="_blank">📘 技术说明书</a>';
    links+='<a href="/view?p='+encodeURIComponent(pid+'/manifest.json')+'" target="_blank">🧾 manifest</a>';
    const figs=(rp&&rp.figures)||[];
    figs.slice(0,3).forEach((f,i)=>{links+='<a href="/files/'+pid+'/reproduce/'+esc(figrel(f))+'" target="_blank">📈 图'+(i+1)+'</a>'});
    const mv=a.mvp||{};
    if(mv.report)links+='<a href="/view?p='+encodeURIComponent(pid+'/mvp/MVP_REPORT.md')+'" target="_blank">🧪 MVP 报告</a>';
    if(mv.figures_md)links+='<a href="/view?p='+encodeURIComponent(pid+'/mvp/figures/FIGURES.md')+'" target="_blank">🎨 作图说明</a>';
    if(mv.report)links+='<a href="/view?p='+encodeURIComponent(pid+'/mvp/demo/README.md')+'" target="_blank">🧩 demo 代码</a>';
    if(mv.notebook)links+='<a href="/files/'+pid+'/mvp/'+mv.notebook+'" target="_blank">📓 Notebook</a>';
    if(mv.streamlit_app)links+='<a href="/view?p='+encodeURIComponent(pid+'/mvp/'+mv.streamlit_app)+'" target="_blank">🖥 Streamlit</a>';
    const modRow='<div class="qrow" style="border:none;padding-top:10px">'
      +'<input type="text" id="mod-'+esc(pid)+'" placeholder="改一次：例如「把优化器换成 Adam」" '
      +'style="flex:1;min-width:190px;padding:8px 10px;border:1px solid var(--line);border-radius:9px;font-size:13px"/>'
      +'<button class="qbtn" data-mod="'+esc(pid)+'">改一次</button>'
      +'<button class="qbtn" data-vers="'+esc(pid)+'">版本</button>'
      +'<button class="qbtn danger" data-del="'+esc(pid)+'">删除</button></div>';
    return '<div class="card"><h3>'+esc(r.title||pid)+'</h3>'
      +'<div class="meta">'+esc(pid)+' · '+(r.created_at||'')+'</div>'
      +'<div class="tags">'
      +(rp?tag(rp.mode_label||rp.mode,''):'')
      +tag('章节 '+st.sections,' gray')+tag('表格 '+st.tables,' gray')
      +tag('图 '+st.figures,' gray')+tag('伪代码 '+st.algorithms,' gray')
      +(ck.pass?tag('pass '+ck.pass,' pass'):'')
      +(ck.warn?tag('warn '+ck.warn,' warn'):'')
      +(ck.fail?tag('fail '+ck.fail,' fail'):'')
      +'</div><div class="links">'+links+'</div>'+modRow+'</div>';
  }).join('');
  grid.querySelectorAll('[data-mod]').forEach(b=>{b.onclick=()=>modifyMvp(b.getAttribute('data-mod'))});
  grid.querySelectorAll('[data-vers]').forEach(b=>{b.onclick=()=>showVersions(b.getAttribute('data-vers'))});
  grid.querySelectorAll('[data-del]').forEach(b=>{b.onclick=()=>deleteRun(b.getAttribute('data-del'))});
}
async function modifyMvp(pid){
  const el=document.getElementById('mod-'+pid);
  const instr=(el&&el.value||'').trim();
  const ht=document.getElementById('htoast');
  if(!instr){ ht.textContent='请先填「改一次」的内容'; return }
  try{
    const r=await fetch('/api/mvp/'+encodeURIComponent(pid)+'/modify',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({instruction:instr,confirm:document.getElementById('confirm').checked})});
    const d=await r.json();
    ht.textContent=r.ok?('已入队修改（'+d.task_id+'）：'+instr):('提交失败：'+friendlyErr(r.status,d.detail||'','confirmWrap'));
    if(el) el.value='';
  }catch(e){ ht.textContent='提交失败：'+e }
  loadQueue();
}
async function showVersions(pid){
  const prog=document.getElementById('hprog');
  try{
    const d=await (await fetch('/api/mvp/'+encodeURIComponent(pid)+'/versions')).json();
    const v=d.versions||{}, live=v.live||{}, hist=v.history||[];
    let html='<div class="meta">版本 · '+esc(pid)+' · 当前 v'+(v.current||1)
      +(live.instruction?('（上次指令：'+esc(live.instruction)+'）'):'')+'</div>';
    if(!hist.length) html+='<div class="stage">还没有历史版本。点「改一次」会存档当前版本并生成 diff。</div>';
    hist.forEach(e=>{
      html+='<div class="stage">v'+esc(e.n)+' · '+esc(e.archived_at||'')
        +(e.instruction?(' · 指令：'+esc(e.instruction)):'')
        +' · '+(e.diff_lines?esc(e.diff_lines)+' 行 diff':'无 diff')
        +(e.diff?' · <a href="/view?p='+encodeURIComponent(pid+'/mvp/'+e.diff)+'" target="_blank">看 diff</a>':'')
        +'</div>';
    });
    prog.innerHTML=html;
  }catch(e){ prog.innerHTML='<div class="err">版本读取失败：'+esc(e)+'</div>' }
}
const prog=document.getElementById('prog');
const ICON={pending:'○',running:'◐',done:'●',reused:'◍',failed:'✗'};
const SZH={pending:'待执行',running:'进行中',done:'已完成',reused:'复用上次产物（跳过）',failed:'失败'};
function stageList(p){
  if(!p||!p.stages)return '<div class="meta">尚无阶段记录</div>';
  return p.stages.map(s=>'<div class="stage">'+(ICON[s.status]||'·')+' <b>'+esc(s.name)+'</b> '
    +esc(SZH[s.status]||s.status)+(s.elapsed_s?' · '+s.elapsed_s+'s':'')
    +(s.note?' · <span class="err">'+esc(String(s.note).slice(0,180))+'</span>':'')+'</div>').join('');
}
async function pollRun(pid){
  for(let i=0;i<400;i++){
    let r;
    try{ r=await (await fetch('/api/run/'+encodeURIComponent(pid)+'/progress')).json(); }
    catch(e){ toast.textContent='进度查询失败：'+e; return; }
    const cur=(r.progress&&r.progress.current)?('（'+r.progress.current+'）'):'';
    prog.innerHTML='<div class="meta">阶段进度 · '+esc(r.paper_id)+esc(cur)+'</div>'+stageList(r.progress);
    if(r.job&&(r.job.status==='ok'||r.job.status==='done')){toast.textContent='完成：'+r.paper_id;await load();return}
    if(r.job&&(r.job.status==='error'||r.job.status==='failed')){
      toast.textContent='失败：'+(r.job.detail||'');
      prog.innerHTML+='<div class="err">'+esc(r.job.detail||'')+'</div>';return;
    }
    await new Promise(s=>setTimeout(s,1200));
  }
  toast.textContent='进度轮询超时（任务可能仍在后台跑，稍后刷新即可）';
}
let confirmRequired=false;
function syncConfirmHints(){
  [['confirm','confwarn','confirmWrap'],['bconfirm','bconfwarn','bconfirmWrap']].forEach(function(pair){
    const box=document.getElementById(pair[0]),warn=document.getElementById(pair[1]),wrap=document.getElementById(pair[2]);
    if(!box||!warn) return;
    if(wrap) wrap.style.display=confirmRequired?'flex':'none';
    const missing=confirmRequired&&!box.checked;
    warn.style.display=missing?'block':'none';
    warn.textContent=missing?'⚠ 还没勾选「我确认在本机运行生成的代码」——现在点「生成」/拖文件/提交都会被直接拒绝。':'';
  });
}
function friendlyErr(status,detail,wrapId){
  if(status===409){
    const el=document.getElementById(wrapId);
    if(el){
      el.style.outline='2px solid #dc2626'; el.style.borderRadius='9px'; el.style.padding='3px 8px';
      setTimeout(function(){el.style.outline='';el.style.padding='';},3000);
    }
    return '需要先勾选「我确认在本机运行生成的代码」——在本机跑生成的论文代码等于执行任意代码，所以这一步得你手动确认；勾上再点一次即可。';
  }
  if(status===413) return '文件太大，超过 200MB 上限。';
  if(status===400) return detail||'请求被拒绝。';
  return detail||'请求失败';
}
async function loadSandbox(){
  try{
    const d=await (await fetch('/api/sandbox')).json();
    const p=d.policy||{}, low=p.risk==='low';
    document.getElementById('banner').innerHTML='<div class="banner '+(low?'ok':'risk')+'">'
      +'执行沙箱：<b>'+esc(p.mode)+'</b> · '+esc(p.isolation||'')+' · 风险：'+(low?'低':'高')
      +' · 网络：'+esc(p.network||'-')
      +'<br>'+esc(d.notice||'')
      +(d.require_confirm&&p.mode!=='container'?'<br><b>提交前请先勾选下方「我确认在本机运行生成的代码」。</b>':'')
      +'</div>';
    confirmRequired=!!(d.require_confirm&&p.mode!=='container');
    syncConfirmHints();
  }catch(e){}
}
loadSandbox();
go.onclick=async()=>{
  const src=document.getElementById('src').value.trim();
  if(!src){toast.textContent='请输入链接 / 本机路径，或把文件拖进上面的框';return}
  go.disabled=true;
  document.getElementById('prog').innerHTML='';toast.textContent='已提交，后台处理中…';
  try{
    const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({source:src,explain:'courseware,podcast',reproduce:true,audio:true,
        resume:document.getElementById('resume').checked,confirm:document.getElementById('confirm').checked})});
    const d=await r.json();
    if(!r.ok||!d.paper_id){toast.textContent='无法提交：'+friendlyErr(r.status,d.detail||JSON.stringify(d),'confirmWrap');go.disabled=false;return}
    await pollRun(d.paper_id);
  }catch(e){toast.textContent='请求失败：'+e+'（服务还在运行吗？双击 start_demo.bat 可重启）'}
  go.disabled=false;
};
async function loadQueue(){
  const q=document.getElementById('queue');
  let d;
  try{ d=await (await fetch('/api/tasks?limit=15')).json(); }
  catch(e){ q.innerHTML='<h4>任务队列</h4><div class="meta">队列读取失败：'+esc(e)+'</div>'; return; }
  const st=d.stats||{}, rows=d.tasks||[];
  const chips=['pending','running','done','failed','cancelled']
    .filter(k=>st[k]).map(k=>'<span class="qstat '+k+'">'+esc(k)+' '+st[k]+'</span>').join('')
    || '<span class="qstat">空</span>';
  const head='<h4>任务队列 · 并发上限 '+d.concurrency+' · 单任务超时 '
    +(d.timeout_s?d.timeout_s+'s':'不限')+'</h4>'
    +'<div style="display:flex;gap:7px;flex-wrap:wrap;margin-bottom:10px">'+chips+'</div>';
  const body=rows.length?rows.map(t=>{
    const live=t.status==='running'&&t.progress&&t.progress.current?(' · 当前阶段 '+esc(t.progress.current)):'';
    const err=t.error?(' · <span class="err">'+esc(String(t.error).slice(0,120))+'</span>'
      +'<button class="qbtn" data-copy="'+esc(t.id)+'" title="复制完整错误">复制错误</button>'):'';
    const cancel=(t.status==='pending'||t.status==='running')
      ?'<button class="qbtn" data-cancel="'+esc(t.id)+'">取消</button>':'';
    return '<div class="qrow"><span class="qstat '+esc(t.status)+'">'+esc(t.status_zh||t.status)+'</span>'
      +'<span>'+esc(t.kind)+'</span><b>'+esc(t.label||t.source)+'</b>'
      +'<span class="meta" style="margin:0">'+esc(t.created_at)+live+err+'</span>'+cancel+'</div>';
  }).join(''):'<div class="meta">还没有任务。到「单篇提交」或「批量提交」发起一个吧。</div>';
  q.innerHTML=head+body;
  q.querySelectorAll('[data-cancel]').forEach(b=>{b.onclick=()=>cancelTask(b.getAttribute('data-cancel'))});
  q.querySelectorAll('[data-copy]').forEach(b=>{b.onclick=()=>copyError(b.getAttribute('data-copy'))});
  if((st.pending||0)+(st.running||0)>0 && !window.__qpoll){ window.__qpoll=setTimeout(()=>{window.__qpoll=null;loadQueue()},2000); }
}
async function copyError(id){
  try{
    const d=await (await fetch('/api/tasks/'+encodeURIComponent(id))).json();
    const t=(d||{}).task||{};
    const text=['task '+id, 'status '+t.status, 'kind '+t.kind, 'source '+t.source,
                'error '+(t.error||''), '', JSON.stringify(d.progress||{},null,2)].join('\\n');
    if(navigator.clipboard&&navigator.clipboard.writeText){ await navigator.clipboard.writeText(text).catch(()=>{}); }
    document.getElementById('hprog').innerHTML='<div class="meta">完整错误（可手动复制）· task '+esc(id)+'</div><pre style="white-space:pre-wrap;font-size:12.5px;background:#f8faff;border:1px solid #e8ecf6;border-radius:10px;padding:10px">'+esc(text)+'</pre>';
  }catch(e){ document.getElementById('htoast').textContent='错误读取失败：'+e }
}
async function cancelTask(id){
  try{ const r=await fetch('/api/tasks/'+encodeURIComponent(id)+'/cancel',{method:'POST'});
    const d=await r.json();
    document.getElementById('htoast').textContent=r.ok?('取消：'+(d.note||d.status)):('取消失败：'+(d.detail||'')); }
  catch(e){ document.getElementById('htoast').textContent='取消失败：'+e }
  loadQueue();
}
async function deleteRun(pid){
  if(!window.confirm('删除「'+pid+'」？会同时删除产物目录、运行记录、队列任务与上传原件，不可恢复。'))return;
  try{
    const r=await fetch('/api/runs/'+encodeURIComponent(pid),{method:'DELETE'});
    const d=await r.json();
    if(!r.ok){ document.getElementById('htoast').textContent='删除失败：'+(d.detail||''); return }
    const rm=d.removed||{};
    document.getElementById('htoast').textContent='已删除 '+pid
      +'（产物目录 '+(rm.outputs?'有':'无')+'、记录 '+rm.records+' 条、队列任务 '+(rm.tasks||0)+' 个'
      +(rm.incoming?'、上传原件已删':'')+'）';
  }catch(e){ document.getElementById('htoast').textContent='删除失败：'+e }
  load(); loadQueue();
}
async function batchSubmit(){
  const bt=document.getElementById('btoast');
  const lines=document.getElementById('batch').value.replace(/\\r/g,'').split('\\n').map(s=>s.trim()).filter(Boolean);
  if(!lines.length){ bt.textContent='请每行填一条来源（链接 / 本机路径 / arXiv 编号）'; return }
  bt.textContent='提交中…';
  try{
    const r=await fetch('/api/batch',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({sources:lines,explain:'courseware,podcast',reproduce:true,audio:true,
        resume:document.getElementById('bresume').checked,confirm:document.getElementById('bconfirm').checked})});
    const d=await r.json();
    bt.textContent=r.ok?('已入队 '+d.n+' 篇（排队执行；切到「历史记录」看进度）'):('提交失败：'+friendlyErr(r.status,d.detail||'','bconfirmWrap'));
  }catch(e){ bt.textContent='请求失败：'+e+'（服务还在运行吗？双击 start_demo.bat 可重启）' }
  loadQueue();
}

// -- 页签 / 来源识别 / 文件上传 ------------------------------------------------ #
document.querySelectorAll('.tabs button').forEach(b=>{
  b.onclick=()=>{
    document.querySelectorAll('.tabs button').forEach(x=>{x.classList.toggle('on',x===b)});
    ['single','batch','history'].forEach(t=>{document.getElementById('tab-'+t).hidden=(t!==b.dataset.tab)});
    if(b.dataset.tab==='history'){ load(); loadQueue(); }
  };
});
let clsTimer=null;
async function classifyHint(){
  const el=document.getElementById('cls');
  const v=document.getElementById('src').value.trim();
  if(!v){ el.innerHTML=''; return }
  try{
    const d=await (await fetch('/api/classify?source='+encodeURIComponent(v))).json();
    el.innerHTML='识别为：<b>'+esc(d.kind_label||d.kind)+'</b>'
      +(d.paper_id?(' · 产物目录 <code>'+esc(d.paper_id)+'</code>'):'')
      +(d.notice?(' · '+esc(d.notice)):'');
  }catch(e){ el.innerHTML='' }
}
document.getElementById('src').addEventListener('input',()=>{clearTimeout(clsTimer);clsTimer=setTimeout(classifyHint,350)});
['confirm','bconfirm'].forEach(id=>{const el=document.getElementById(id); if(el) el.addEventListener('change',syncConfirmHints)});

async function uploadOne(file,pollIt,confirmId,resumeId){
  const qs='?filename='+encodeURIComponent(file.name)
    +'&confirm='+(document.getElementById(confirmId).checked?'true':'false')
    +'&resume='+(document.getElementById(resumeId).checked?'true':'false');
  const r=await fetch('/api/upload'+qs,{method:'POST',body:file});
  const d=await r.json().catch(function(){return {}});
  if(!r.ok){
    const err=new Error(d.detail||('HTTP '+r.status));
    err.status=r.status;
    throw err;
  }
  if(pollIt) await pollRun(d.paper_id);
  return d;
}
const drop=document.getElementById('drop'),fileIn=document.getElementById('file');
drop.onclick=()=>fileIn.click();
fileIn.onchange=()=>{const f=fileIn.files[0];fileIn.value='';if(f)handleSingleFile(f)};
['dragenter','dragover'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.add('hot')}));
['dragleave','drop'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.remove('hot')}));
drop.addEventListener('drop',e=>{const f=e.dataTransfer&&e.dataTransfer.files[0];if(f)handleSingleFile(f)});
async function handleSingleFile(f){
  toast.textContent='上传中：'+f.name;
  document.getElementById('prog').innerHTML='';
  try{ await uploadOne(f,true,'confirm','resume') }
  catch(e){ toast.textContent='上传失败：'+friendlyErr(e.status,e.message,'confirmWrap') }
}
document.getElementById('txtbtn').onclick=()=>document.getElementById('txtfile').click();
document.getElementById('txtfile').onchange=()=>{
  const f=document.getElementById('txtfile').files[0]; document.getElementById('txtfile').value='';
  if(!f) return;
  const rd=new FileReader();
  rd.onload=()=>{
    document.getElementById('batch').value=String(rd.result||'').replace(/\\r/g,'').trim();
    document.getElementById('btoast').textContent='已载入列表：'+f.name;
  };
  rd.readAsText(f);
};
document.getElementById('pdfbtn').onclick=()=>document.getElementById('pdfs').click();
document.getElementById('pdfs').onchange=async()=>{
  const inp=document.getElementById('pdfs'); const files=[].slice.call(inp.files); inp.value='';
  if(!files.length) return;
  const bt=document.getElementById('btoast'); let ok=0; const bad=[];
  for(let i=0;i<files.length;i++){
    bt.textContent='上传中…（'+(i+1)+'/'+files.length+'）'+files[i].name;
    try{ await uploadOne(files[i],false,'bconfirm','bresume'); ok++ }
    catch(e){ bad.push(files[i].name+'：'+friendlyErr(e.status,e.message,'bconfirmWrap')) }
  }
  bt.textContent='已上传 '+ok+' 个文件（已入队）'+(bad.length?('；失败 '+bad.length+'：'+bad.join('；')):'');
  loadQueue();
};
loadQueue();
load();
</script></body></html>
"""


_VIEWER_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>产物阅读器 · paper2code</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#f5f7fb;color:#1b2130;font:15.5px/1.75 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;padding:32px 20px 80px}
.wrap{max-width:960px;margin:0 auto;background:#fff;border:1px solid #e3e8f2;border-radius:18px;padding:40px 46px;box-shadow:0 10px 30px rgba(24,39,75,.08)}
h1{font-size:29px;font-weight:800;margin:0 0 18px;letter-spacing:-.02em}
h2{font-size:22px;font-weight:750;margin:34px 0 12px;padding-bottom:8px;border-bottom:1px solid #eef1f8}
h3{font-size:17.5px;font-weight:700;margin:24px 0 10px;color:#39457a}
p{margin:10px 0}
ul,ol{margin:10px 0 10px 26px}li{margin:5px 0}
code{background:#f1f3f9;padding:2px 7px;border-radius:6px;font-size:13.6px;color:#39457a;font-family:ui-monospace,Menlo,Consolas,monospace}
pre{background:#f8faff;border:1px solid #e8ecf6;border-radius:12px;padding:16px 18px;overflow:auto;margin:14px 0}
pre code{background:none;padding:0}
table{border-collapse:collapse;width:100%;font-size:14px;margin:14px 0;border-radius:10px;overflow:hidden}
th,td{border-bottom:1px solid #e8ecf6;padding:9px 12px;text-align:left}
th{background:#eef2ff;font-weight:700;color:#39457a}
tr:nth-child(even) td{background:#fafbff}
blockquote{border-left:4px solid #dfe4f3;padding:6px 16px;color:#5d6b85;margin:14px 0;background:#fafbff;border-radius:0 10px 10px 0}
hr{border:none;border-top:1px solid #eef1f8;margin:30px 0}
a{color:#4f6bed}
.top{margin-bottom:16px;font-size:13.5px;color:#5d6b85}
</style></head>
<body><div class="wrap">
<div class="top" id="path"></div>
<div id="out">加载中…</div>
</div>
<script>
const P=new URLSearchParams(location.search).get('p');
document.getElementById('path').innerHTML='产物：<code>'+P+'</code> · <a href="/">返回总览</a>';
function esc(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
const DIR=P.slice(0,P.lastIndexOf('/')+1);
function md(src){
  const lines=src.split(/\\r?\\n/);let out=[],i=0,inCode=false,codeBuf=[],list=null,table=[];
  function flushList(){if(list){out.push('</'+list+'>');list=null}}
  function flushTable(){if(table.length){let h=table[0],rows=table.slice(2);
    out.push('<table><thead><tr>'+h.map(c=>'<th>'+esc(c)+'</th>').join('')+'</tr></thead><tbody>'
    +rows.map(r=>'<tr>'+r.map(c=>'<td>'+esc(c)+'</td>').join('')+'</tr>').join('')+'</tbody></table>');table=[]}}
  function inline(t){
    return esc(t).replace(/`([^`]+)`/g,'<code>$1</code>')
      .replace(/!\\[([^\\]]*)\\]\\(([^)]+)\\)/g,(m,alt,u)=>'<img alt="'+alt+'" src="'
        +(/^(https?:|\\/)/.test(u)?u:'/files/'+DIR+u)
        +'" style="max-width:100%;display:block;margin:14px 0;border:1px solid #e8ecf6;border-radius:10px"/>')
      .replace(/\\*\\*([^*]+)\\*\\*/g,'<strong>$1</strong>')
      .replace(/\\[([^\\]]+)\\]\\((http[^)]+)\\)/g,'<a href="$2" target="_blank">$1</a>');
  }
  for(;i<lines.length;i++){
    const ln=lines[i];
    if(/^\\s*```/.test(ln)){flushList();flushTable();
      if(inCode){out.push('<pre><code>'+esc(codeBuf.join('\\n'))+'</code></pre>');codeBuf=[];inCode=false}
      else inCode=true;continue}
    if(inCode){codeBuf.push(ln);continue}
    if(!ln.trim()){flushList();flushTable();continue}
    if(/^\\|/.test(ln)){table.push(ln.split('|').slice(1,-1).map(s=>s.trim()));continue}
    flushTable();
    let m;
    if(m=ln.match(/^(#{1,6})\\s+(.*)$/)){flushList();out.push('<h'+m[1].length+'>'+inline(m[2])+'</h'+m[1].length+'>');continue}
    if(/^\\s*(-{3,}|\\*{3,})\\s*$/.test(ln)){flushList();out.push('<hr/>');continue}
    if(m=ln.match(/^\\s*>\\s?(.*)$/)){flushList();out.push('<blockquote>'+inline(m[1])+'</blockquote>');continue}
    if(m=ln.match(/^\\s*[-*]\\s+(.*)$/)){if(list!=='ul'){flushList();out.push('<ul>');list='ul'}out.push('<li>'+inline(m[1])+'</li>');continue}
    if(m=ln.match(/^\\s*(\\d+)[.)]\\s+(.*)$/)){if(list!=='ol'){flushList();out.push('<ol>');list='ol'}out.push('<li>'+inline(m[2])+'</li>');continue}
    flushList();out.push('<p>'+inline(ln)+'</p>');
  }
  flushList();flushTable();
  if(inCode&&codeBuf.length)out.push('<pre><code>'+esc(codeBuf.join('\\n'))+'</code></pre>');
  return out.join('\\n');
}
(async()=>{
  try{
    const r=await fetch('/files/'+P);
    const t=await r.text();
    document.getElementById('out').innerHTML = P.endsWith('.json')
      ? '<pre><code>'+esc(t)+'</code></pre>' : md(t);
  }catch(e){document.getElementById('out').textContent='加载失败：'+e}
})();
</script></body></html>
"""
