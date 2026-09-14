"""本地 Web 服务（FastAPI）。

启动::

    python -m paper2code serve --port 8000

页面：

* ``/``                    首页：上传 + 「生成解析」+ 论文卡片（查看 / 删除）
* ``/paper/{paper_id}``    论文详情：课件 / 播客稿 / 复现建议与 demo
* ``/settings``            API 设置（LLM + TTS，密钥仅内存）
* ``/view?p=<path>``       Markdown 产物阅读器
* ``/files/...``           直接访问 ``outputs/`` 下的任意产物

接口：

* ``GET  /api/runs``           列出全部运行
* ``GET  /api/runs/{pid}``     单个运行的 manifest
* ``POST /api/run``            提交一篇论文（同步执行，MVP 简化处理）
* ``GET  /api/papers``         列出本地论文（incoming/ + outputs/）
* ``DELETE /api/papers/{id}``  删除 incoming + outputs 整树（仅网站）
* ``GET  /api/llm/status``     当前 LLM 状态（不含密钥）
* ``POST /api/llm/settings``   进程内应用 LLM 设置（不落盘）
* ``POST /api/llm/test``       发送极短 chat 探测连通性
* ``POST /api/upload``         上传论文到 incoming/<paper_id>/
* ``GET  /api/tts/status``     TTS 状态（不含密钥）
* ``POST /api/tts/test``       短句语音探测（不返回密钥）
* ``POST /api/tts/settings``   进程内应用 TTS 设置（不落盘）
* ``POST /api/tts/generate``   为已有播客稿合成语音
"""

from __future__ import annotations

import json
import re as _re
import shutil
import time
from datetime import datetime
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import (
    apply_llm_runtime,
    apply_tts_runtime,
    get_settings,
    llm_status,
    tts_status,
)
from .pipeline import list_runs, run_paper

try:  # FastAPI 为可选依赖：没装也不影响 CLI 使用
    from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel

    _HAS_FASTAPI = True
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False

    class BaseModel:  # type: ignore
        pass


def _scrub_secret(text: str, secret: str = "") -> str:
    """Strip api key from error bodies so responses never echo secrets."""
    out = str(text or "")
    if secret and secret in out:
        out = out.replace(secret, "***")
    return out


def _looks_like_filepath_key(value: str) -> bool:
    """Guard against pasting config paths (e.g. default.yaml) into the key box."""
    s = (value or "").strip().strip('"').strip("'")
    if not s:
        return False
    lower = s.lower().replace(chr(92), "/")
    if lower.endswith((".yaml", ".yml", ".json", ".env", ".toml", ".ini")):
        return True
    if "/" in lower:
        if not (s.startswith("sk-") or s.startswith("sk-or-") or s.startswith("Bearer ")):
            return True
    return False


def _maybe_apply_llm(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    llm_mode: Optional[str] = None,
) -> None:
    """Apply only non-empty fields; empty string = ignore."""
    key = api_key if (api_key is not None and str(api_key).strip()) else None
    if key is not None and _looks_like_filepath_key(key):
        key = None
    url = base_url if (base_url is not None and str(base_url).strip()) else None
    mdl = model if (model is not None and str(model).strip()) else None
    mode = llm_mode if (llm_mode is not None and str(llm_mode).strip()) else None
    if key is None and url is None and mdl is None and mode is None:
        return
    apply_llm_runtime(api_key=key, base_url=url, model=mdl, mode=mode)


def _ping_llm(timeout: float = 20.0) -> Dict[str, Any]:
    """Send a tiny chat completion using current settings. Never returns the key."""
    st = get_settings()
    if st.llm_mode == "offline" or not st.llm_api_key:
        return {"ok": False, "error": "当前为离线模式或未配置 API Key"}
    base = (st.llm_base_url or "").rstrip("/")
    model = st.llm_model or "gpt-4o-mini"
    key = st.llm_api_key
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
        "temperature": 0,
    }
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as fh:  # noqa: S310
            raw = fh.read().decode("utf-8", "ignore")
        latency_ms = int((time.perf_counter() - t0) * 1000)
        data = json.loads(raw)
        preview = ""
        try:
            preview = str(data["choices"][0]["message"]["content"] or "")[:120]
        except (KeyError, IndexError, TypeError):
            preview = str(data)[:120]
        preview = _scrub_secret(preview, key)
        return {"ok": True, "latency_ms": latency_ms, "model": model, "preview": preview}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")[:300]
        body = _scrub_secret(body, key)
        return {"ok": False, "error": f"HTTP {exc.code}: {body}"}
    except Exception as exc:
        return {"ok": False, "error": _scrub_secret(f"{type(exc).__name__}: {exc}", key)}


def _sanitize_paper_id(name: str) -> str:
    """Derive a filesystem-safe paper_id from a filename or folder name."""
    raw = Path(name or "paper").name
    p = Path(raw)
    if p.suffix.lower() in (".pdf", ".md", ".html", ".htm", ".txt", ".zip", ".markdown"):
        base = p.stem
    else:
        base = raw
    base = _re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", base, flags=_re.UNICODE).strip("._")
    return (base or "paper")[:120]


def _safe_upload_name(name: str) -> str:
    base = Path(name or "upload.bin").name
    base = _re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", base, flags=_re.UNICODE).strip("._")
    return (base or "upload.bin")[:120]


def _incoming_root() -> Path:
    root = get_settings().workspace / "incoming"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _uploads_legacy_root() -> Path:
    root = _incoming_root() / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _outputs_root() -> Path:
    root = get_settings().output_dir
    root.mkdir(parents=True, exist_ok=True)
    return root


def _validate_paper_id(paper_id: str) -> str:
    """Reject path traversal / empty ids. Returns cleaned id."""
    pid = (paper_id or "").strip()
    if not pid:
        raise ValueError("paper_id 为空")
    if "/" in pid or "\\" in pid or ".." in pid or pid in (".",):
        raise ValueError("非法 paper_id")
    if _re.search(r"[\x00-\x1f]", pid):
        raise ValueError("非法 paper_id")
    if pid.startswith("~") or ":" in pid:
        raise ValueError("非法 paper_id")
    return pid


def _existing_paper_ids() -> set:
    ids: set = set()
    inc = _incoming_root()
    if inc.exists():
        for p in inc.iterdir():
            if p.is_dir() and p.name != "uploads":
                ids.add(p.name)
        up = inc / "uploads"
        if up.is_dir():
            for p in up.iterdir():
                if p.is_dir():
                    ids.add(p.name)
    out = _outputs_root()
    if out.exists():
        for p in out.iterdir():
            if p.is_dir():
                ids.add(p.name)
    return ids


def _allocate_paper_id(stem: str) -> str:
    """Unique paper_id from sanitized stem; append _2, _3 on collision."""
    base = _sanitize_paper_id(stem)
    existing = _existing_paper_ids()
    if base not in existing:
        return base
    n = 2
    while f"{base}_{n}" in existing:
        n += 1
    return f"{base}_{n}"


def _resolve_incoming_dir(paper_id: str) -> Optional[Path]:
    """Find incoming/<id> or legacy incoming/uploads/<id>."""
    pid = _validate_paper_id(paper_id)
    inc = _incoming_root()
    cand = (inc / pid).resolve()
    try:
        cand.relative_to(inc.resolve())
    except ValueError:
        return None
    if cand.is_dir() and cand != inc.resolve() and cand.name != "uploads":
        return cand
    legacy_root = (inc / "uploads").resolve()
    legacy = (legacy_root / pid).resolve()
    try:
        legacy.relative_to(legacy_root)
    except ValueError:
        return None
    if legacy.is_dir():
        return legacy
    return None


def _resolve_outputs_dir(paper_id: str) -> Optional[Path]:
    pid = _validate_paper_id(paper_id)
    out = _outputs_root()
    cand = (out / pid).resolve()
    try:
        cand.relative_to(out.resolve())
    except ValueError:
        return None
    if cand.is_dir() and cand != out.resolve():
        return cand
    return None


def _primary_paper_file(d: Path) -> str:
    for cand in ("paper.pdf", "paper.md", "paper.html", "paper.txt"):
        if (d / cand).exists():
            return str(d / cand)
    files = [f for f in d.iterdir() if f.is_file()]
    if files:
        return str(files[0])
    return str(d)


def _list_papers() -> List[Dict[str, Any]]:
    """Merge incoming (+ legacy uploads) and outputs into a paper list."""
    by_id: Dict[str, Dict[str, Any]] = {}

    def ensure(pid: str) -> Dict[str, Any]:
        if pid not in by_id:
            by_id[pid] = {
                "paper_id": pid,
                "title": pid,
                "incoming_dir": None,
                "outputs_dir": None,
                "source_path": "",
                "has_incoming": False,
                "has_outputs": False,
                "has_courseware": False,
                "has_podcast": False,
                "has_podcast_audio": False,
                "has_repro": False,
                "legacy_upload": False,
                "created_at": "",
                "artifacts": {},
                "stats": {},
                "status": "uploaded",
            }
        return by_id[pid]

    inc = _incoming_root()
    if inc.exists():
        for p in sorted(inc.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if not p.is_dir() or p.name == "uploads":
                continue
            item = ensure(p.name)
            item["incoming_dir"] = str(p)
            item["has_incoming"] = True
            item["source_path"] = str(p)
            item["created_at"] = datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
        up = inc / "uploads"
        if up.is_dir():
            for p in sorted(up.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
                if not p.is_dir():
                    continue
                item = ensure(p.name)
                item["incoming_dir"] = str(p)
                item["has_incoming"] = True
                item["legacy_upload"] = True
                item["source_path"] = str(p)
                item["created_at"] = datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")

    runs = {r.get("paper_id"): r for r in list_runs() if r.get("paper_id")}
    out = _outputs_root()
    if out.exists():
        for p in out.iterdir():
            if not p.is_dir():
                continue
            item = ensure(p.name)
            item["outputs_dir"] = str(p)
            item["has_outputs"] = True
            man = p / "manifest.json"
            if man.exists():
                try:
                    data = json.loads(man.read_text(encoding="utf-8"))
                    item["title"] = data.get("title") or item["title"]
                    item["created_at"] = data.get("created_at") or item["created_at"]
                    item["artifacts"] = data.get("artifacts") or {}
                    item["stats"] = data.get("stats") or {}
                except Exception:
                    pass
            elif p.name in runs:
                r = runs[p.name]
                item["title"] = r.get("title") or item["title"]
                item["created_at"] = r.get("created_at") or item["created_at"]
                item["artifacts"] = r.get("artifacts") or {}
                item["stats"] = r.get("stats") or {}
            if not item["created_at"]:
                item["created_at"] = datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")

    # Fill has_* flags from artifacts and filesystem
    for item in by_id.values():
        a = item.get("artifacts") or {}
        ex = a.get("explain") or {}
        rp = a.get("reproduce") or {}
        out_dir = Path(item["outputs_dir"]) if item.get("outputs_dir") else None
        has_cw = bool(ex.get("courseware"))
        has_ps = bool(ex.get("podcast_script"))
        has_pa = bool(ex.get("podcast_audio"))
        has_rp = bool(rp.get("report_html") or rp.get("report"))
        if out_dir and out_dir.is_dir():
            if (out_dir / "explain" / "courseware.html").exists():
                has_cw = True
            if (out_dir / "explain" / "podcast_script.md").exists():
                has_ps = True
            if (out_dir / "explain" / "podcast.mp3").exists():
                has_pa = True
            if (out_dir / "reproduce" / "repro_report.html").exists() or (
                out_dir / "reproduce" / "repro_report.md"
            ).exists():
                has_rp = True
        item["has_courseware"] = has_cw
        item["has_podcast"] = has_ps
        item["has_podcast_audio"] = has_pa
        item["has_repro"] = has_rp
        if item.get("has_outputs") and (has_cw or has_ps or has_rp):
            item["status"] = "ready"
        elif item.get("has_outputs"):
            item["status"] = "outputs"
        elif item.get("has_incoming"):
            item["status"] = "uploaded"
        else:
            item["status"] = "unknown"

    items = list(by_id.values())
    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return items


def _delete_paper(paper_id: str) -> Dict[str, Any]:
    """Recursively remove incoming + outputs trees for paper_id. Path-traversal safe."""
    pid = _validate_paper_id(paper_id)
    removed: List[str] = []
    missing: List[str] = []

    inc_dir = _resolve_incoming_dir(pid)
    out_dir = _resolve_outputs_dir(pid)

    if inc_dir is None and out_dir is None:
        raise FileNotFoundError(f"未找到论文：{pid}")

    if inc_dir is not None:
        shutil.rmtree(inc_dir)
        removed.append(str(inc_dir))
    else:
        missing.append("incoming")

    if out_dir is not None:
        shutil.rmtree(out_dir)
        removed.append(str(out_dir))
    else:
        missing.append("outputs")

    index_path = _outputs_root() / "index.json"
    if index_path.exists():
        try:
            data = json.loads(index_path.read_text(encoding="utf-8")) or []
            data = [d for d in data if d.get("paper_id") != pid]
            index_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    return {"ok": True, "paper_id": pid, "removed": removed, "missing": missing}


def _normalize_run_source(source: str) -> str:
    """If source is paper.* under incoming/, use parent bundle dir so paper_id = folder stem."""
    try:
        p = Path(source)
        if not p.exists():
            return source
        if p.is_file() and p.name.lower() in (
            "paper.pdf", "paper.md", "paper.html", "paper.htm", "paper.txt", "paper.markdown",
        ):
            parent = p.parent.resolve()
            inc = _incoming_root().resolve()
            try:
                parent.relative_to(inc)
                return str(parent)
            except ValueError:
                pass
    except Exception:
        pass
    return source


def _list_recent_uploads(limit: int = 12) -> list:
    """Recent incoming paper folders (new stem dirs + legacy uploads)."""
    items = []
    for it in _list_papers():
        if not it.get("has_incoming"):
            continue
        d = Path(it["incoming_dir"])
        files = [f.name for f in d.iterdir() if f.is_file()] if d.is_dir() else []
        items.append({
            "dir": it["incoming_dir"],
            "name": it["paper_id"],
            "paper_id": it["paper_id"],
            "files": files,
            "path": it["source_path"] or _primary_paper_file(d),
            "legacy_upload": it.get("legacy_upload", False),
        })
        if len(items) >= limit:
            break
    return items


def _parse_script_turns(script_text: str) -> list:
    turns = []
    for ln in (script_text or "").splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        m = _re.match(r"^\*\*【(主持人|讲解人)】\*\*\s*(.*)$", s)
        if not m:
            m = _re.match(r"^【(主持人|讲解人)】\s*(.*)$", s)
        if m:
            spk = "A" if m.group(1) == "主持人" else "B"
            turns.append({"speaker": spk, "text": m.group(2).strip()})
    return turns


def _maybe_apply_tts(
    api_key=None,
    base_url=None,
    model=None,
    provider=None,
    voice=None,
):
    key = api_key if (api_key is not None and str(api_key).strip()) else None
    if key is not None and _looks_like_filepath_key(key):
        key = None
    url = base_url if (base_url is not None and str(base_url).strip()) else None
    mdl = model if (model is not None and str(model).strip()) else None
    prov = provider if (provider is not None and str(provider).strip()) else None
    voi = voice if (voice is not None and str(voice).strip()) else None
    if key is None and url is None and mdl is None and prov is None and voi is None:
        return
    apply_tts_runtime(api_key=key, base_url=url, model=mdl, provider=prov, voice=voi)



def _resolve_run_source(source: str, paper_id: Optional[str] = None) -> str:
    """Prefer incoming/<paper_id> when given; survive path mojibake on Windows.

    Browser JSON is UTF-8-safe, but some clients (PowerShell, mis-encoded tools)
    corrupt CJK path strings. Resolving by validated paper_id avoids FileNotFound.
    """
    # 1) explicit paper_id
    if paper_id:
        try:
            pid = _validate_paper_id(paper_id)
            found = _resolve_incoming_dir(pid)
            if found is not None:
                return str(found)
        except ValueError:
            pass

    # 2) normalize paper.* file -> parent bundle
    source = _normalize_run_source(source or "")
    try:
        p = Path(source)
        if p.exists():
            return str(p.resolve() if p.is_dir() else p)
    except Exception:
        pass

    # 3) last path component as paper_id (may still be correct unicode)
    try:
        tail = Path(source).name if source else ""
        if tail:
            pid = _validate_paper_id(tail)
            found = _resolve_incoming_dir(pid)
            if found is not None:
                return str(found)
    except Exception:
        pass

    return source


def _ping_tts(timeout: float = 30.0) -> Dict[str, Any]:
    """Tiny /v1/audio/speech probe. Never returns the API key."""
    st = get_settings()
    provider = (getattr(st, "tts_provider", None) or st.tts_mode or "auto").lower()
    key = getattr(st, "tts_api_key", "") or ""
    base = (getattr(st, "tts_base_url", "") or "https://api.siliconflow.cn/v1").rstrip("/")
    model = getattr(st, "tts_model", "") or "FunAudioLLM/CosyVoice2-0.5B"
    voice = getattr(st, "tts_voice", "") or f"{model}:alex"

    if provider in ("edge",) or (not key and provider in ("auto", "edge", "")):
        # Try edge-tts ping without cloud key
        try:
            import edge_tts  # type: ignore
            import asyncio

            async def _edge_ping() -> None:
                comm = edge_tts.Communicate("测试", voice=st.tts_voice_a or "zh-CN-XiaoxiaoNeural")
                # drain a few chunks then cancel — connectivity check
                n = 0
                async for _chunk in comm.stream():
                    n += 1
                    if n >= 2:
                        break

            t0 = time.perf_counter()
            try:
                asyncio.run(_edge_ping())
            except RuntimeError:
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(_edge_ping())
                finally:
                    loop.close()
            latency_ms = int((time.perf_counter() - t0) * 1000)
            return {"ok": True, "latency_ms": latency_ms, "model": "edge-tts", "mode": "edge"}
        except Exception as exc:
            if not key:
                return {"ok": False, "error": "未配置 TTS API Key；edge-tts 探测也失败：" + _scrub_secret(str(exc)), "mode": "edge"}
            # fall through to cloud if key present

    if not key:
        return {"ok": False, "error": "请填写 TTS API Key（或改用 edge-tts 预设）", "mode": provider or "siliconflow"}

    payload = {
        "model": model,
        "input": "测试",
        "voice": voice,
        "response_format": "mp3",
    }
    req = urllib.request.Request(
        f"{base}/audio/speech",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as fh:  # noqa: S310
            audio = fh.read(64)  # discard rest; enough to confirm success
            # drain remaining to free connection
            try:
                fh.read()
            except Exception:
                pass
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "ok": True,
            "latency_ms": latency_ms,
            "model": model,
            "mode": provider or "siliconflow",
            "bytes_preview": len(audio),
        }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")[:300]
        body = _scrub_secret(body, key)
        return {"ok": False, "error": f"HTTP {exc.code}: {body}", "model": model, "mode": provider or "siliconflow"}
    except Exception as exc:
        return {"ok": False, "error": _scrub_secret(f"{type(exc).__name__}: {exc}", key), "model": model, "mode": provider or "siliconflow"}


if _HAS_FASTAPI:
    app = FastAPI(title="paper2code", version="0.1.0", description="论文学习工具：讲解材料 + 复现结果")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    class RunRequest(BaseModel):
        source: str = ""
        paper_id: Optional[str] = None
        explain: str = "courseware,podcast"
        reproduce: bool = True
        audio: bool = False
        api_key: Optional[str] = None
        base_url: Optional[str] = None
        model: Optional[str] = None
        llm_mode: Optional[str] = None

    class LlmSettingsRequest(BaseModel):
        api_key: Optional[str] = None
        base_url: Optional[str] = None
        model: Optional[str] = None
        llm_mode: Optional[str] = None

    @app.get("/api/runs")
    def api_runs() -> List[Dict[str, Any]]:
        return list_runs()

    @app.get("/api/runs/{pid}")
    def api_run(pid: str) -> Dict[str, Any]:
        for r in list_runs():
            if r.get("paper_id") == pid:
                return r
        raise HTTPException(status_code=404, detail=f"未找到运行记录：{pid}")

    @app.post("/api/run")
    def api_run_post(req: RunRequest) -> Dict[str, Any]:
        _maybe_apply_llm(req.api_key, req.base_url, req.model, req.llm_mode)
        explain = tuple(k.strip() for k in req.explain.split(",") if k.strip())
        source = _resolve_run_source(req.source or "", getattr(req, "paper_id", None))
        if not source:
            raise HTTPException(status_code=400, detail="缺少 source / paper_id")
        try:
            if not Path(source).exists():
                raise FileNotFoundError(
                    f"找不到输入：{source}"
                    + (f"（paper_id={req.paper_id}）" if req.paper_id else "")
                    + "。请确认已上传，或改用 paper_id 字段。"
                )
            return run_paper(source, explain=explain, reproduce=req.reproduce, audio=req.audio)
        except Exception as exc:
            detail = _scrub_secret(f"{type(exc).__name__}: {exc}", req.api_key or "")
            raise HTTPException(status_code=400, detail=detail) from exc

    @app.get("/api/papers")
    def api_papers() -> List[Dict[str, Any]]:
        return _list_papers()

    @app.get("/api/papers/{paper_id}")
    def api_paper_one(paper_id: str) -> Dict[str, Any]:
        try:
            pid = _validate_paper_id(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        for it in _list_papers():
            if it.get("paper_id") == pid:
                return it
        raise HTTPException(status_code=404, detail=f"未找到论文：{pid}")

    @app.delete("/api/papers/{paper_id}")
    def api_papers_delete(paper_id: str) -> Dict[str, Any]:
        try:
            return _delete_paper(paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    @app.post("/api/papers/{paper_id}/delete")
    def api_papers_delete_post(paper_id: str) -> Dict[str, Any]:
        return api_papers_delete(paper_id)

    @app.get("/api/llm/status")
    def api_llm_status() -> Dict[str, Any]:
        return llm_status()

    @app.post("/api/llm/settings")
    def api_llm_settings(req: LlmSettingsRequest) -> Dict[str, Any]:
        _maybe_apply_llm(req.api_key, req.base_url, req.model, req.llm_mode)
        st = llm_status()
        return {"ok": True, **st}

    @app.post("/api/llm/test")
    def api_llm_test(req: LlmSettingsRequest) -> Dict[str, Any]:
        _maybe_apply_llm(req.api_key, req.base_url, req.model, req.llm_mode)
        return _ping_llm(timeout=20.0)

    class TtsSettingsRequest(BaseModel):
        api_key: Optional[str] = None
        base_url: Optional[str] = None
        model: Optional[str] = None
        provider: Optional[str] = None
        voice: Optional[str] = None

    class TtsGenerateRequest(BaseModel):
        paper_id: str
        api_key: Optional[str] = None
        base_url: Optional[str] = None
        model: Optional[str] = None
        provider: Optional[str] = None
        voice: Optional[str] = None

    @app.post("/api/upload")
    async def api_upload(file: UploadFile = File(...)):
        """Save uploaded paper under incoming/<paper_id>/paper.* (stem-named folder)."""
        raw_name = file.filename or "paper.pdf"
        safe = _safe_upload_name(raw_name)
        paper_id = _allocate_paper_id(safe)
        dest_dir = _incoming_root() / paper_id
        # path-traversal safe: paper_id has no separators
        dest_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(safe).suffix.lower() or ".bin"
        if suffix in (".pdf", ".md", ".html", ".htm", ".txt"):
            dest_name = f"paper{suffix if suffix != '.htm' else '.html'}"
        else:
            dest_name = safe
        dest = dest_dir / dest_name
        try:
            with dest.open("wb") as fh:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
            if suffix == ".zip":
                import zipfile
                with zipfile.ZipFile(dest, "r") as zf:
                    zf.extractall(dest_dir)
                for cand in ("paper.pdf", "paper.md", "paper.html"):
                    if (dest_dir / cand).exists():
                        dest = dest_dir / cand
                        break
        except Exception as exc:
            # cleanup empty/partial dir on failure
            try:
                if dest_dir.exists() and not any(dest_dir.iterdir()):
                    dest_dir.rmdir()
            except Exception:
                pass
            raise HTTPException(status_code=400, detail=f"上传失败：{type(exc).__name__}: {exc}") from exc
        return {
            "ok": True,
            "paper_id": paper_id,
            "path": str(dest_dir),  # bundle dir → run uses folder stem as paper_id
            "file": str(dest),
            "dir": str(dest_dir),
            "name": paper_id,
            "recent": _list_recent_uploads(),
        }

    @app.get("/api/uploads")
    def api_uploads_list():
        return _list_recent_uploads()

    @app.get("/api/tts/status")
    def api_tts_status():
        return tts_status()

    @app.post("/api/tts/settings")
    def api_tts_settings(req: TtsSettingsRequest):
        _maybe_apply_tts(req.api_key, req.base_url, req.model, req.provider, req.voice)
        return {"ok": True, **tts_status()}

    @app.post("/api/tts/test")
    def api_tts_test(req: TtsSettingsRequest) -> Dict[str, Any]:
        """Probe TTS with a tiny speech request. Never returns the API key."""
        _maybe_apply_tts(req.api_key, req.base_url, req.model, req.provider, req.voice)
        return _ping_tts(timeout=30.0)

    @app.post("/api/tts/generate")
    def api_tts_generate(req: TtsGenerateRequest):
        """Synthesize podcast audio for an existing run; save under outputs/<pid>/explain/."""
        _maybe_apply_tts(req.api_key, req.base_url, req.model, req.provider, req.voice)
        try:
            pid = _validate_paper_id(req.paper_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        st = get_settings()
        explain = st.output_dir / pid / "explain"
        script = explain / "podcast_script.md"
        if not script.exists():
            raise HTTPException(status_code=404, detail=f"未找到播客稿：{pid}/explain/podcast_script.md")
        turns = _parse_script_turns(script.read_text(encoding="utf-8"))
        if len(turns) < 2:
            raise HTTPException(status_code=400, detail="播客稿无法解析为对白轮次")
        from .tts import get_tts

        engine = get_tts(refresh=True)
        out_audio = explain / "podcast.mp3"
        path = engine.synthesize(turns, out_audio)
        status = tts_status()
        if not path:
            err = "; ".join(engine.warnings) or "语音合成失败"
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": err, "engine": engine.name, **status},
            )
        rel = f"{pid}/explain/{Path(path).name}"
        return {
            "ok": True,
            "engine": engine.name,
            "audio": rel,
            "url": f"/files/{rel}",
            "warnings": engine.warnings,
            **status,
        }

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return _DASHBOARD_HTML

    @app.get("/paper/{paper_id}", response_class=HTMLResponse)
    def paper_detail(paper_id: str) -> str:
        return _PAPER_DETAIL_HTML

    @app.get("/view/paper", response_class=HTMLResponse)
    def paper_detail_qs(id: str = Query("", description="paper_id")) -> str:
        return _PAPER_DETAIL_HTML

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page() -> str:
        return _SETTINGS_HTML

    @app.get("/view", response_class=HTMLResponse)
    def view(p: str = Query(..., description="outputs 下的相对路径")) -> str:
        return _VIEWER_HTML

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
.wrap{max-width:980px;margin:0 auto}
header{margin-bottom:26px}
h1{font-size:31px;font-weight:800;letter-spacing:-.02em}
h1 span{background:linear-gradient(120deg,var(--brand),var(--brand2),var(--accent));-webkit-background-clip:text;background-clip:text;color:transparent}
.sub{color:var(--muted);margin-top:8px;font-size:14.5px}
.nav{display:flex;gap:8px;margin-top:14px;flex-wrap:wrap}
.nav a{font-size:13.5px;text-decoration:none;color:var(--muted);border:1px solid var(--line);
padding:7px 14px;border-radius:999px;background:#fff;font-weight:650;transition:.14s}
.nav a:hover,.nav a.active{background:var(--brand);color:#fff;border-color:var(--brand)}
.panel{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px 20px;margin:18px 0 8px;
box-shadow:0 6px 18px rgba(24,39,75,.05)}
.panel .title{font-size:14px;font-weight:700;color:#39457a}
.panel .hint{font-size:12.5px;color:var(--muted);margin-top:4px}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:12px}
input[type=file]{padding:12px 15px;border:1px solid var(--line);border-radius:12px;font-size:14.5px;background:#fff;flex:1;min-width:220px}
button{padding:12px 22px;border:none;border-radius:12px;background:linear-gradient(135deg,var(--brand),var(--brand2));
color:#fff;font-size:14.5px;font-weight:650;cursor:pointer;transition:.16s}
button:hover{transform:translateY(-1px);box-shadow:0 8px 22px rgba(79,107,237,.3)}
button:disabled{opacity:.55;cursor:not-allowed;transform:none;box-shadow:none}
button.danger{background:#fff;color:var(--fail);border:1px solid #f5c2c2;box-shadow:none}
button.danger:hover{background:#fef2f2;box-shadow:0 4px 12px rgba(220,38,38,.12)}
.grid{display:grid;gap:16px;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));margin-top:8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:20px 22px;
box-shadow:0 8px 26px rgba(24,39,75,.07);transition:.18s;position:relative;overflow:hidden;display:flex;flex-direction:column;gap:10px}
.card:hover{transform:translateY(-2px);box-shadow:0 14px 34px rgba(24,39,75,.13)}
.card::before{content:"";position:absolute;inset:0 0 auto 0;height:4px;
background:linear-gradient(90deg,var(--brand),var(--brand2),var(--accent))}
.card h3{font-size:17px;font-weight:750;line-height:1.4;word-break:break-all}
.meta{color:var(--muted);font-size:12.5px}
.badge{display:inline-block;font-size:12px;padding:3px 10px;border-radius:999px;background:#eef2ff;color:#3d4ea8;font-weight:650}
.badge.ready{background:#e8f8ee;color:#15803d}
.badge.uploaded{background:#f1f3f9;color:#5d6b85}
.actions{display:flex;gap:8px;margin-top:6px;flex-wrap:wrap}
.actions a,.actions button{font-size:13px;text-decoration:none;padding:7px 14px;border-radius:9px;font-weight:650}
.actions a.view{background:linear-gradient(135deg,var(--brand),var(--brand2));color:#fff;border:none}
.actions a.view:hover{opacity:.92}
.empty{text-align:center;padding:64px 20px;color:var(--muted);background:#fff;border:1px dashed var(--line);border-radius:18px;grid-column:1/-1}
#toast{margin-top:12px;font-size:13.8px;color:var(--muted);min-height:1.4em}
#runStatus{font-size:13.5px;color:var(--muted);margin-top:10px;min-height:1.4em;font-weight:650}
#runStatus.busy{color:var(--brand)}
#runStatus.ok{color:var(--pass)}
#runStatus.err{color:var(--fail)}
#runError{display:none;margin-top:12px;padding:12px 14px;border-radius:12px;border:1px solid #fecaca;
background:#fef2f2;color:var(--fail);font-size:13.5px;font-weight:650;white-space:pre-wrap;word-break:break-word}
#runError.show{display:block}
#toast.err{color:var(--fail);font-weight:650}
</style></head>
<body><div class="wrap">
<header>
  <h1>paper2code · <span>论文学习工具</span></h1>
  <div class="sub">选择 PDF 后点「上传并生成解析」→ 自动上传并跑讲解+复现。API / TTS 请到「设置」配置。</div>
  <div class="nav">
    <a class="active" href="/">首页</a>
    <a href="/settings">设置</a>
  </div>
</header>

<div class="panel">
  <div class="title">上传并生成解析</div>
  <div class="hint">支持 PDF / Markdown / HTML / ZIP。一步完成：上传到 incoming/&lt;stem&gt;/ 后立即生成解析（讲解+复现，默认不生成语音）。</div>
  <div class="row">
    <input type="file" id="file" accept=".pdf,.md,.html,.htm,.zip,application/pdf,application/zip"/>
    <button type="button" id="doRun">上传并生成解析</button>
  </div>
  <div id="runStatus"></div>
  <div id="runError" role="alert"></div>
</div>
<div id="toast"></div>

<h2 style="font-size:17px;font-weight:750;margin:22px 0 12px;color:#39457a">本地论文</h2>
<div class="grid" id="grid"></div>
</div>
<script>
const grid=document.getElementById('grid'),toast=document.getElementById('toast');
const fileInput=document.getElementById('file'),doRun=document.getElementById('doRun');
const runStatus=document.getElementById('runStatus');

function esc(s){return String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function setStatus(msg, cls){runStatus.textContent=msg||'';runStatus.className=cls||'';}
function setRunError(msg){
  const el=document.getElementById('runError');
  if(!el){toast.textContent=msg||'';toast.className=msg?'err':'';return;}
  if(msg){el.textContent=msg;el.classList.add('show');toast.textContent=msg;toast.className='err';}
  else{el.textContent='';el.classList.remove('show');toast.className='';}
}
function errDetail(d){
  if(!d) return '';
  if(typeof d.detail==='string') return d.detail;
  if(Array.isArray(d.detail)) return d.detail.map(x=>x.msg||JSON.stringify(x)).join('; ');
  return d.error||d.message||JSON.stringify(d);
}

const STATUS_LABEL={ready:'已解析',uploaded:'已上传',outputs:'有产物',unknown:'未知'};

async function load(){
  const papers=await (await fetch('/api/papers')).json();
  if(!papers.length){
    grid.innerHTML='<div class="empty">还没有本地论文。<br>请选择文件后点「上传并生成解析」。</div>';
    return;
  }
  grid.innerHTML=papers.map(r=>{
    const pid=r.paper_id;
    const st=r.status|| (r.has_outputs?'ready':'uploaded');
    const label=STATUS_LABEL[st]||st;
    const badgeCls=st==='ready'?'ready':'uploaded';
    return '<div class="card">'
      +'<h3>'+esc(r.title||pid)+'</h3>'
      +'<div class="meta">'+esc(pid)+(r.created_at?' · '+esc(r.created_at):'')+'</div>'
      +'<div><span class="badge '+badgeCls+'">'+esc(label)+'</span></div>'
      +'<div class="actions">'
      +'<a class="view" href="/paper/'+encodeURIComponent(pid)+'">查看</a>'
      +'<button type="button" class="danger" data-del="'+esc(pid)+'">删除</button>'
      +'</div></div>';
  }).join('');
  grid.querySelectorAll('button[data-del]').forEach(btn=>{
    btn.onclick=async()=>{
      const pid=btn.getAttribute('data-del');
      if(!confirm('确认删除论文「'+pid+'」？\n将同时删除 incoming 与 outputs 下该论文的全部文件，且不可恢复。'))return;
      btn.disabled=true;
      try{
        const r=await fetch('/api/papers/'+encodeURIComponent(pid),{method:'DELETE'});
        const d=await r.json().catch(()=>({}));
        toast.textContent=r.ok?('已删除：'+pid):('失败：'+(d.detail||JSON.stringify(d)));
        if(r.ok) await load();
      }catch(e){toast.textContent='删除失败：'+e}
      btn.disabled=false;
    };
  });
}

doRun.onclick=async()=>{
  if(!fileInput.files||!fileInput.files[0]){setStatus('请先选择文件','err');setRunError('请先选择文件');return}
  doRun.disabled=true;
  setStatus('上传中…','busy');
  setRunError('');
  toast.textContent='';
  let paperId='';
  try{
    const fd=new FormData(); fd.append('file', fileInput.files[0]);
    const ur=await fetch('/api/upload',{method:'POST',body:fd});
    const ud=await ur.json().catch(()=>({}));
    if(!ur.ok||!ud.path){
      const msg='上传失败：'+errDetail(ud);
      setStatus(msg,'err'); setRunError(msg);
      doRun.disabled=false;
      return;
    }
    paperId=ud.paper_id||'';
    // Prefer bundle dir path; fall back to file path (server normalizes paper.* → parent)
    const source=ud.path||ud.dir||ud.file;
    if(!source){
      const msg='上传响应缺少 path';
      setStatus(msg,'err'); setRunError(msg);
      doRun.disabled=false;
      return;
    }
    setStatus('解析中…'+(paperId?'（'+paperId+'）':''),'busy');
    toast.textContent='已上传：'+paperId+'，正在生成解析（请勿关闭，首次 PDF 可能较久）…';
    // Refresh list but NEVER let a load() failure skip /api/run
    try{ await load(); }catch(_e){}
    const body={source:source,paper_id:paperId||undefined,explain:'courseware,podcast',reproduce:true,audio:false};
    const rr=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body)});
    const rd=await rr.json().catch(()=>({}));
    if(rr.ok){
      const doneId=rd.paper_id||paperId;
      if(rd.paper_id && paperId && rd.paper_id!==paperId){
        const msg='完成但 paper_id 不一致：上传='+paperId+'，产物='+rd.paper_id;
        setStatus(msg,'err'); setRunError(msg);
      }else{
        setStatus('完成','ok'); setRunError('');
        toast.textContent='完成：'+doneId+' — 正在打开详情…';
      }
      try{ await load(); }catch(_e){}
      if(doneId) location.href='/paper/'+encodeURIComponent(doneId);
    }else{
      const msg='解析失败：'+errDetail(rd);
      setStatus(msg,'err'); setRunError(msg);
      try{ await load(); }catch(_e){}
    }
  }catch(e){
    const msg='请求失败：'+e;
    setStatus(msg,'err'); setRunError(msg);
    try{ await load(); }catch(_e){}
  }
  doRun.disabled=false;
};
load();
</script></body></html>
"""



_PAPER_DETAIL_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>论文详情 · paper2code</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#f5f7fb;--card:#fff;--ink:#1b2130;--muted:#5d6b85;--line:#e3e8f2;
--brand:#4f6bed;--brand2:#8b5cf6;--accent:#0ea5a4;--pass:#16a34a;--fail:#dc2626;}
body{background:radial-gradient(1000px 560px at 8% -10%,#e8edff,transparent 60%),
radial-gradient(860px 480px at 104% 0%,#eafaf7,transparent 55%),var(--bg);
color:var(--ink);font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
min-height:100vh;padding:34px 22px 70px}
.wrap{max-width:860px;margin:0 auto}
.nav{display:flex;gap:8px;margin-bottom:18px;flex-wrap:wrap}
.nav a{font-size:13.5px;text-decoration:none;color:var(--muted);border:1px solid var(--line);
padding:7px 14px;border-radius:999px;background:#fff;font-weight:650}
.nav a:hover{background:var(--brand);color:#fff;border-color:var(--brand)}
h1{font-size:26px;font-weight:800;letter-spacing:-.02em;word-break:break-all}
.sub{color:var(--muted);margin:8px 0 22px;font-size:13.5px}
.sec{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px 20px;margin-bottom:14px;
box-shadow:0 6px 18px rgba(24,39,75,.05)}
.sec h2{font-size:16px;font-weight:750;color:#39457a;margin-bottom:10px}
.miss{color:#9aa3b5;font-size:13.5px}
.links{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.links a,button.tts{font-size:13px;text-decoration:none;color:var(--brand);border:1px solid #dfe4f3;
padding:7px 13px;border-radius:9px;background:#fbfcff;font-weight:650;cursor:pointer}
.links a:hover,button.tts:hover{background:var(--brand);color:#fff;border-color:var(--brand)}
button.tts{font-family:inherit}
audio{width:100%;margin-top:10px}
#ttsMsg{font-size:13px;color:var(--muted);margin-top:8px}
.empty{padding:40px;text-align:center;color:var(--muted);background:#fff;border:1px dashed var(--line);border-radius:16px}
</style></head>
<body><div class="wrap">
<div class="nav">
  <a href="/">← 返回首页</a>
  <a href="/settings">设置</a>
</div>
<h1 id="title">加载中…</h1>
<div class="sub" id="meta"></div>
<div id="body"></div>
</div>
<script>
function esc(s){return String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function pidFromPath(){
  const parts=location.pathname.split('/').filter(Boolean);
  if(parts[0]==='paper' && parts[1]) return decodeURIComponent(parts[1]);
  return new URLSearchParams(location.search).get('id')||'';
}
const PID=pidFromPath();
function miss(t){return '<div class="miss">尚未生成'+(t?' · '+esc(t):'')+'</div>'}

async function load(){
  if(!PID){document.getElementById('title').textContent='缺少 paper_id';return}
  let r,d;
  try{
    r=await fetch('/api/papers/'+encodeURIComponent(PID));
    d=await r.json();
  }catch(e){
    document.getElementById('title').textContent='加载失败';
    document.getElementById('body').innerHTML='<div class="empty">'+esc(String(e))+'</div>';
    return;
  }
  if(!r.ok){
    document.getElementById('title').textContent='未找到';
    document.getElementById('body').innerHTML='<div class="empty">'+esc(d.detail||PID)+'</div>';
    return;
  }
  document.title=(d.title||PID)+' · paper2code';
  document.getElementById('title').textContent=d.title||PID;
  document.getElementById('meta').textContent=PID+(d.created_at?' · '+d.created_at:'')+(d.status?' · '+d.status:'');

  const a=d.artifacts||{},ex=a.explain||{},rp=a.reproduce||{};
  const hasCw=!!(d.has_courseware||ex.courseware);
  const hasPs=!!(d.has_podcast||ex.podcast_script);
  const hasPa=!!(d.has_podcast_audio||ex.podcast_audio);
  const hasRp=!!(d.has_repro||rp.report_html||rp.report);

  let html='';
  html+='<div class="sec"><h2>课件</h2>';
  if(hasCw){
    html+='<div class="links"><a href="/files/'+encodeURIComponent(PID)+'/explain/courseware.html" target="_blank">打开课件 HTML</a></div>';
  }else html+=miss('courseware.html');
  html+='</div>';

  html+='<div class="sec"><h2>博客 / 播客稿</h2>';
  if(hasPs){
    html+='<div class="links">'
      +'<a href="/view?p='+encodeURIComponent(PID+'/explain/podcast_script.md')+'" target="_blank">查看播客稿</a>'
      +'<button type="button" class="tts" id="ttsGo">生成语音</button>'
      +'</div>';
    html+='<div id="ttsMsg"></div>';
    html+='<audio id="ttsAudio" controls style="'+(hasPa?'':'display:none')+'"'
      +(hasPa?' src="/files/'+encodeURIComponent(PID)+'/explain/podcast.mp3"':'')+'></audio>';
  }else html+=miss('podcast_script.md');
  html+='</div>';

  html+='<div class="sec"><h2>复现建议 / 复现 demo</h2>';
  if(hasRp){
    html+='<div class="links">';
    html+='<a href="/files/'+encodeURIComponent(PID)+'/reproduce/repro_report.html" target="_blank">复现报告 HTML</a>';
    html+='<a href="/view?p='+encodeURIComponent(PID+'/reproduce/repro_report.md')+'" target="_blank">复现报告 Markdown</a>';
    html+='<a href="/files/'+encodeURIComponent(PID)+'/reproduce/" target="_blank">打开 reproduce/ 目录</a>';
    html+='</div>';
  }else html+=miss('repro_report');
  html+='</div>';

  html+='<div class="sec"><h2>其他产物</h2><div class="links">';
  if(d.has_outputs){
    html+='<a href="/view?p='+encodeURIComponent(PID+'/manifest.json')+'" target="_blank">manifest.json</a>';
    html+='<a href="/files/'+encodeURIComponent(PID)+'/" target="_blank">打开 outputs/'+esc(PID)+'/</a>';
  }else{
    html+=miss('outputs');
  }
  html+='</div></div>';

  document.getElementById('body').innerHTML=html;

  // If artifacts not ready yet (user opened detail while /api/run still running),
  // poll until ready so the page does not stay stuck on 尚未生成.
  if(!hasCw && !hasPs && !hasRp && (d.status==='uploaded' || !d.has_outputs)){
    setTimeout(load, 2500);
  }

  const go=document.getElementById('ttsGo');
  if(go){
    const msg=document.getElementById('ttsMsg');
    const audio=document.getElementById('ttsAudio');
    if(hasPa){msg.textContent='已有音频，可直接播放或重新生成。'}
    go.onclick=async()=>{
      go.disabled=true; msg.textContent='合成中…（需在设置页配置 TTS Key，或依赖 edge-tts）';
      try{
        const body={paper_id:PID, provider:'siliconflow', base_url:'https://api.siliconflow.cn/v1', model:'FunAudioLLM/CosyVoice2-0.5B'};
        const rr=await fetch('/api/tts/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
        const dd=await rr.json();
        if(dd.ok&&dd.url){
          audio.src=dd.url+'?t='+Date.now(); audio.style.display='block'; audio.play().catch(()=>{});
          msg.textContent='完成 · 引擎 '+(dd.engine||'');
        }else{
          msg.textContent='失败：'+(dd.error||dd.detail||JSON.stringify(dd));
        }
      }catch(e){msg.textContent='请求失败：'+e}
      go.disabled=false;
    };
  }
}
load();
</script></body></html>
"""


_SETTINGS_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>设置 · paper2code</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#f5f7fb;--card:#fff;--ink:#1b2130;--muted:#5d6b85;--line:#e3e8f2;
--brand:#4f6bed;--brand2:#8b5cf6;--accent:#0ea5a4;--pass:#16a34a;--warn:#d97706;--fail:#dc2626;}
body{background:radial-gradient(1000px 560px at 8% -10%,#e8edff,transparent 60%),
radial-gradient(860px 480px at 104% 0%,#eafaf7,transparent 55%),var(--bg);
color:var(--ink);font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
min-height:100vh;padding:34px 22px 70px}
.wrap{max-width:760px;margin:0 auto}
header{margin-bottom:26px}
h1{font-size:31px;font-weight:800;letter-spacing:-.02em}
h1 span{background:linear-gradient(120deg,var(--brand),var(--brand2),var(--accent));-webkit-background-clip:text;background-clip:text;color:transparent}
.sub{color:var(--muted);margin-top:8px;font-size:14.5px}
.nav{display:flex;gap:8px;margin-top:14px;flex-wrap:wrap}
.nav a{font-size:13.5px;text-decoration:none;color:var(--muted);border:1px solid var(--line);
padding:7px 14px;border-radius:999px;background:#fff;font-weight:650;transition:.14s}
.nav a:hover,.nav a.active{background:var(--brand);color:#fff;border-color:var(--brand)}
input[type=text],input[type=password],select{width:100%;padding:12px 15px;border:1px solid var(--line);border-radius:12px;font-size:14.5px;background:#fff}
input[type=text]:focus,input[type=password]:focus,select:focus{outline:none;border-color:var(--brand);box-shadow:0 0 0 3px rgba(79,107,237,.12)}
button{padding:12px 22px;border:none;border-radius:12px;background:linear-gradient(135deg,var(--brand),var(--brand2));
color:#fff;font-size:14.5px;font-weight:650;cursor:pointer;transition:.16s}
button:hover{transform:translateY(-1px);box-shadow:0 8px 22px rgba(79,107,237,.3)}
button:disabled{opacity:.6;cursor:not-allowed;transform:none}
button.secondary{background:#fff;color:var(--brand);border:1px solid #dfe4f3;box-shadow:none}
button.secondary:hover{background:#f5f7ff;box-shadow:0 4px 12px rgba(79,107,237,.12)}
.llm-panel{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px 22px;margin:18px 0 8px;
box-shadow:0 6px 18px rgba(24,39,75,.05)}
.llm-panel .title{font-size:14px;font-weight:700;color:#39457a}
.llm-panel .hint{font-size:12.5px;color:var(--muted);margin-top:4px;margin-bottom:14px}
.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px 16px;align-items:start}
@media (max-width:640px){.form-grid{grid-template-columns:1fr}}
.llm-panel label{font-size:12.5px;color:var(--muted);font-weight:650;display:block;margin-bottom:6px}
.llm-panel .field{min-width:0;width:100%}
.actions-row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;justify-content:flex-start;margin-top:16px}
#llmStatus,#ttsStatus{font-size:13.5px;min-height:1.4em}
#llmStatus.ok,#ttsStatus.ok{color:var(--pass);font-weight:650}
#llmStatus.err,#ttsStatus.err{color:var(--fail);font-weight:650}
#llmStatus.muted,#ttsStatus.muted{color:var(--muted)}
</style></head>
<body><div class="wrap">
<header>
  <h1>paper2code · <span>设置</span></h1>
  <div class="sub">LLM 与 TTS 配置仅保存在本进程内存，<strong>不会写入磁盘</strong>。也可使用环境变量 / configs/default.yaml（手动编辑）。</div>
  <div class="nav">
    <a href="/">首页</a>
    <a class="active" href="/settings">设置</a>
  </div>
</header>

<div class="llm-panel">
  <div class="title">LLM 设置（仅本进程生效，密钥不写入磁盘）</div>
  <div class="hint">空 Key 时保持离线默认；也可设环境变量 P2C_LLM_API_KEY。可选：在 configs/default.yaml 中手动填写（本页不会改该文件）。</div>
  <div class="form-grid">
    <div class="field narrow">
      <label for="llmPreset">预设</label>
      <select id="llmPreset">
        <option value="offline">离线 offline</option>
        <option value="deepseek">DeepSeek</option>
        <option value="openrouter">OpenRouter</option>
        <option value="siliconflow">SiliconFlow 硅基流动</option>
        <option value="custom">自定义 OpenAI 兼容</option>
      </select>
    </div>
    <div class="field" id="keyField">
      <label for="llmKey">API Key</label>
      <input type="password" id="llmKey" placeholder="API Key（粘贴后仅存内存）" autocomplete="off"/>
    </div>
    <div class="field" id="urlField">
      <label for="llmUrl">Base URL</label>
      <input type="text" id="llmUrl" placeholder="https://api.deepseek.com/v1"/>
    </div>
    <div class="field narrow" id="modelField">
      <label for="llmModel">模型</label>
      <input type="text" id="llmModel" placeholder="deepseek-chat"/>
    </div>
  </div>
  <div class="actions-row">
    <button type="button" class="secondary" id="llmTest">测试连接</button>
    <button type="button" class="secondary" id="llmApply">保存到内存</button>
    <span id="llmStatus" class="muted"></span>
  </div>
</div>

<div class="llm-panel">
  <div class="title">TTS 语音（SiliconFlow，密钥仅内存，不写磁盘）</div>
  <div class="hint">环境变量 P2C_TTS_API_KEY；无 Key 时播客仍可用 edge-tts 离线兜底。播客稿页也可点「生成语音」。</div>
  <div class="form-grid">
    <div class="field narrow">
      <label for="ttsPreset">预设</label>
      <select id="ttsPreset">
        <option value="siliconflow">SiliconFlow CosyVoice</option>
        <option value="openai_speech">自定义 OpenAI 兼容 speech</option>
        <option value="edge">edge-tts（离线兜底）</option>
      </select>
    </div>
    <div class="field">
      <label for="ttsKey">TTS API Key</label>
      <input type="password" id="ttsKey" placeholder="TTS Key（与 LLM Key 分开）" autocomplete="off"/>
    </div>
    <div class="field">
      <label for="ttsUrl">TTS Base URL</label>
      <input type="text" id="ttsUrl" placeholder="https://api.siliconflow.cn/v1"/>
    </div>
    <div class="field narrow">
      <label for="ttsModel">TTS 模型</label>
      <input type="text" id="ttsModel" placeholder="FunAudioLLM/CosyVoice2-0.5B"/>
    </div>
  </div>
  <div class="actions-row">
    <button type="button" class="secondary" id="ttsTest">测试连接</button>
    <button type="button" class="secondary" id="ttsApply">保存到内存</button>
    <span id="ttsStatus" class="muted" style="font-size:13px"></span>
  </div>
</div>
</div>
<script>
const preset=document.getElementById('llmPreset'),llmKey=document.getElementById('llmKey');
const llmUrl=document.getElementById('llmUrl'),llmModel=document.getElementById('llmModel');
const llmTest=document.getElementById('llmTest'),llmApply=document.getElementById('llmApply'),llmStatus=document.getElementById('llmStatus');
const ttsPreset=document.getElementById('ttsPreset'),ttsKey=document.getElementById('ttsKey');
const ttsUrl=document.getElementById('ttsUrl'),ttsModel=document.getElementById('ttsModel');
const ttsApply=document.getElementById('ttsApply'),ttsTest=document.getElementById('ttsTest'),ttsStatusEl=document.getElementById('ttsStatus');

function applyPreset(){
  const v=preset.value;
  const offline=v==='offline';
  llmKey.disabled=offline; llmUrl.disabled=offline; llmModel.disabled=offline;
  document.getElementById('keyField').style.opacity=offline?.45:1;
  document.getElementById('urlField').style.opacity=offline?.45:1;
  document.getElementById('modelField').style.opacity=offline?.45:1;
  const fill=(url,model)=>{
    if(!llmUrl.value||llmUrl.dataset.auto==='1'){llmUrl.value=url;llmUrl.dataset.auto='1'}
    if(!llmModel.value||llmModel.dataset.auto==='1'){llmModel.value=model;llmModel.dataset.auto='1'}
  };
  if(v==='deepseek'){
    fill('https://api.deepseek.com/v1','deepseek-chat');
  }else if(v==='openrouter'){
    fill('https://openrouter.ai/api/v1','openai/gpt-4o-mini');
  }else if(v==='siliconflow'){
    fill('https://api.siliconflow.cn/v1','deepseek-ai/DeepSeek-V3');
  }else if(v==='custom'){
    llmUrl.dataset.auto='0'; llmModel.dataset.auto='0';
    if(!llmUrl.value) llmUrl.placeholder='https://api.openai.com/v1';
    if(!llmModel.value) llmModel.placeholder='gpt-4o-mini';
  }else{
    llmStatus.textContent='离线模式：不调用远程模型';llmStatus.className='muted';
  }
}
preset.onchange=applyPreset;
llmUrl.addEventListener('input',()=>{llmUrl.dataset.auto='0'});
llmModel.addEventListener('input',()=>{llmModel.dataset.auto='0'});
function llmPayload(){
  const v=preset.value;
  if(v==='offline') return {llm_mode:'offline'};
  const body={llm_mode:'openai'};
  const k=llmKey.value.trim(); if(k) body.api_key=k;
  const u=llmUrl.value.trim(); if(u) body.base_url=u;
  const m=llmModel.value.trim(); if(m) body.model=m;
  return body;
}
llmApply.onclick=async()=>{
  llmApply.disabled=true; llmStatus.className='muted'; llmStatus.textContent='应用中…';
  try{
    const r=await fetch('/api/llm/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(llmPayload())});
    const d=await r.json();
    if(d.ok){llmStatus.className='ok'; llmStatus.textContent='已应用 · '+(d.mode||'')+' · '+(d.model||'')+(d.has_key?' · 已配置 Key':' · 无 Key');}
    else {llmStatus.className='err'; llmStatus.textContent=d.error||JSON.stringify(d);}
  }catch(e){llmStatus.className='err';llmStatus.textContent='失败：'+e}
  llmApply.disabled=false;
};
llmTest.onclick=async()=>{
  llmTest.disabled=true;
  llmStatus.className='muted'; llmStatus.textContent='测试中…';
  try{
    const r=await fetch('/api/llm/test',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(llmPayload())});
    const d=await r.json();
    if(d.ok){
      llmStatus.className='ok';
      llmStatus.textContent='连接成功 · '+(d.latency_ms||'?')+' ms · '+(d.model||'')+(d.preview?' · '+String(d.preview).slice(0,40):'');
    }else{
      llmStatus.className='err';
      llmStatus.textContent='连接失败：'+(d.error||JSON.stringify(d));
    }
  }catch(e){llmStatus.className='err';llmStatus.textContent='请求失败：'+e}
  llmTest.disabled=false;
};
function applyTtsPreset(){
  const v=ttsPreset.value;
  if(v==='siliconflow'){
    if(!ttsUrl.value||ttsUrl.dataset.auto==='1'){ttsUrl.value='https://api.siliconflow.cn/v1';ttsUrl.dataset.auto='1'}
    if(!ttsModel.value||ttsModel.dataset.auto==='1'){ttsModel.value='FunAudioLLM/CosyVoice2-0.5B';ttsModel.dataset.auto='1'}
  }else if(v==='openai_speech'){
    ttsUrl.dataset.auto='0'; ttsModel.dataset.auto='0';
    if(!ttsUrl.value) ttsUrl.placeholder='https://api.openai.com/v1';
  }
}
ttsPreset.onchange=applyTtsPreset; applyTtsPreset();
ttsUrl.addEventListener('input',()=>{ttsUrl.dataset.auto='0'});
ttsModel.addEventListener('input',()=>{ttsModel.dataset.auto='0'});
function ttsPayload(){
  const body={provider:ttsPreset.value};
  const k=ttsKey.value.trim(); if(k) body.api_key=k;
  const u=ttsUrl.value.trim(); if(u) body.base_url=u;
  const m=ttsModel.value.trim(); if(m) body.model=m;
  return body;
}

ttsTest.onclick=async()=>{
  ttsTest.disabled=true;
  ttsStatusEl.className='muted';
  ttsStatusEl.textContent='测试中…';
  try{
    const r=await fetch('/api/tts/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(ttsPayload())});
    const d=await r.json();
    if(d.ok){
      ttsStatusEl.className='ok';
      ttsStatusEl.textContent='连接成功 · '+(d.latency_ms||'?')+' ms · '+(d.model||d.mode||'');
    }else{
      ttsStatusEl.className='err';
      ttsStatusEl.textContent='测试失败：'+(d.error||JSON.stringify(d));
    }
  }catch(e){ttsStatusEl.className='err';ttsStatusEl.textContent='测试失败：'+e}
  ttsTest.disabled=false;
};

ttsApply.onclick=async()=>{
  ttsApply.disabled=true; ttsStatusEl.textContent='应用中…';
  try{
    const r=await fetch('/api/tts/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(ttsPayload())});
    const d=await r.json();
    ttsStatusEl.textContent=d.ok?('已应用 · '+(d.provider||'')+' · '+(d.model||'')+(d.has_key?' · 已配置 Key':' · 无 Key')):(d.error||JSON.stringify(d));
  }catch(e){ttsStatusEl.textContent='失败：'+e}
  ttsApply.disabled=false;
};
applyPreset();
(async()=>{
  try{
    const s=await (await fetch('/api/llm/status')).json();
    if(s&&s.mode==='openai'&&s.has_key){
      const bu=(s.base_url||'');
      preset.value=bu.includes('deepseek.com')?'deepseek':bu.includes('openrouter')?'openrouter':bu.includes('siliconflow')?'siliconflow':'custom';
      if(s.base_url){llmUrl.value=s.base_url;llmUrl.dataset.auto='0'}
      if(s.model){llmModel.value=s.model;llmModel.dataset.auto='0'}
      applyPreset();
      llmStatus.className='muted';
      llmStatus.textContent='已从环境/进程加载：'+s.mode+' · '+(s.model||'')+(s.has_key?' · 已配置 Key':'');
    }
  }catch(e){}
})();
(async()=>{
  try{
    const s=await (await fetch('/api/tts/status')).json();
    if(s){
      if(s.provider==='edge') ttsPreset.value='edge';
      else if(s.provider==='openai_speech') ttsPreset.value='openai_speech';
      else ttsPreset.value='siliconflow';
      if(s.base_url){ttsUrl.value=s.base_url;ttsUrl.dataset.auto='0'}
      if(s.model){ttsModel.value=s.model;ttsModel.dataset.auto='0'}
      applyTtsPreset();
      ttsStatusEl.textContent=(s.has_key?'已配置 TTS Key':'未配置 TTS Key')+' · '+(s.provider||'')+' · '+(s.model||'');
    }
  }catch(e){}
})();

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
<div id="ttsBar" style="display:none;margin:0 0 18px;padding:14px 16px;background:#f8faff;border:1px solid #e3e8f2;border-radius:12px">
  <div style="font-weight:700;margin-bottom:8px">播客语音（SiliconFlow / edge-tts）</div>
  <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
    <input type="password" id="vTtsKey" placeholder="TTS API Key（可选，仅内存）" style="flex:1;min-width:180px;padding:8px 12px;border:1px solid #e3e8f2;border-radius:10px"/>
    <input type="text" id="vTtsModel" placeholder="FunAudioLLM/CosyVoice2-0.5B" style="flex:1;min-width:180px;padding:8px 12px;border:1px solid #e3e8f2;border-radius:10px"/>
    <button type="button" id="vTtsGo" style="padding:8px 16px;border:none;border-radius:10px;background:linear-gradient(135deg,#4f6bed,#8b5cf6);color:#fff;font-weight:650;cursor:pointer">生成语音</button>
  </div>
  <div id="vTtsMsg" style="margin-top:8px;font-size:13px;color:#5d6b85"></div>
  <audio id="vTtsAudio" controls style="width:100%;margin-top:10px;display:none"></audio>
</div>
<div id="out">加载中…</div>
</div>
<script>
const P=new URLSearchParams(location.search).get('p');
document.getElementById('path').innerHTML='产物：<code>'+P+'</code> · <a href="/">返回首页</a> · <a href="/settings">设置</a>';
(function(){
  if(P && /explain\/podcast_script\.md$/i.test(P)){
    document.getElementById('ttsBar').style.display='block';
    const pid=P.split('/')[0];
    const go=document.getElementById('vTtsGo');
    const msg=document.getElementById('vTtsMsg');
    const audio=document.getElementById('vTtsAudio');
    // try existing audio
    fetch('/files/'+pid+'/explain/podcast.mp3',{method:'HEAD'}).then(r=>{
      if(r.ok){audio.src='/files/'+pid+'/explain/podcast.mp3';audio.style.display='block';msg.textContent='已有音频，可直接播放或重新生成。'}
    }).catch(()=>{});
    go.onclick=async()=>{
      go.disabled=true; msg.textContent='合成中…';
      const body={paper_id:pid, provider:'siliconflow', base_url:'https://api.siliconflow.cn/v1'};
      const k=document.getElementById('vTtsKey').value.trim(); if(k) body.api_key=k;
      const m=document.getElementById('vTtsModel').value.trim(); if(m) body.model=m;
      else body.model='FunAudioLLM/CosyVoice2-0.5B';
      try{
        const r=await fetch('/api/tts/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
        const d=await r.json();
        if(d.ok&&d.url){
          audio.src=d.url+'?t='+Date.now(); audio.style.display='block'; audio.play().catch(()=>{});
          msg.textContent='完成 · 引擎 '+(d.engine||'')+(d.warnings&&d.warnings.length?' · '+d.warnings.join('; '):'');
        }else{
          msg.textContent='失败：'+(d.error||d.detail||JSON.stringify(d));
        }
      }catch(e){msg.textContent='请求失败：'+e}
      go.disabled=false;
    };
  }
})();
function esc(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function md(src){
  const lines=src.split(/\\r?\\n/);let out=[],i=0,inCode=false,codeBuf=[],list=null,table=[];
  function flushList(){if(list){out.push('</'+list+'>');list=null}}
  function flushTable(){if(table.length){let h=table[0],rows=table.slice(2);
    out.push('<table><thead><tr>'+h.map(c=>'<th>'+esc(c)+'</th>').join('')+'</tr></thead><tbody>'
    +rows.map(r=>'<tr>'+r.map(c=>'<td>'+esc(c)+'</td>').join('')+'</tr>').join('')+'</tbody></table>');table=[]}}
  function inline(t){
    return esc(t).replace(/`([^`]+)`/g,'<code>$1</code>')
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
