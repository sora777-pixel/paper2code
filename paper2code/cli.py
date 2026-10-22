"""命令行入口。

用法示例::

    python -m paper2code run samples/01_open_source           # 处理一篇（论文包目录）
    python -m paper2code run https://arxiv.org/abs/1706.03762 # 直接给 arXiv
    python -m paper2code batch samples                         # 批量跑 samples 下所有论文包
    python -m paper2code serve --port 8000                     # 启动本地 Web UI
    python -m paper2code info                                  # 查看当前配置
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from . import __version__
from .config import get_settings
from .pipeline import list_runs, run_batch, run_paper


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="paper2code",
        description="论文学习工具：生成讲解材料（课件/播客）并复现论文中的表格与图表",
    )
    p.add_argument("--version", action="version", version=f"paper2code {__version__}")
    sub = p.add_subparsers(dest="command")

    # -- run -------------------------------------------------------------- #
    r = sub.add_parser("run", help="处理一篇论文")
    r.add_argument("source", help="论文文件 / 论文包目录 / arXiv ID 或链接")
    r.add_argument("-o", "--out", type=Path, default=None,
                   help="输出根目录（默认 outputs/single/）")
    r.add_argument(
        "--explain", default="courseware,podcast",
        help="讲解材料类型，逗号分隔：courseware,podcast（留空表示不生成）",
    )
    r.add_argument("--no-reproduce", action="store_true", help="跳过复现环节")
    r.add_argument("--no-audio", action="store_true", help="只出播客脚本与字幕，不合成音频")
    r.add_argument("--no-pseudo-run", action="store_true", help="情形 C 只翻译不执行")
    r.add_argument("--no-mvp", action="store_true", help="跳过 MVP demo / 图表绘制说明生成  # MVP-PATCH:cli-run-flag")
    r.add_argument("--resume", action="store_true",
                   help="跳过上次已完成且产物仍在的阶段（阶段缓存，见 progress.json）")
    r.add_argument("--llm", choices=["offline", "openai"], default=None, help="大模型模式")
    r.add_argument("--model", default=None, help="模型名（openai 模式）")
    r.add_argument("--base-url", default=None, help="OpenAI 兼容端点（如 https://api.deepseek.com/v1）")
    r.add_argument("--max-records", type=int, default=None, help="数据规模上限（默认 10000）")
    r.add_argument("--install-repo-deps", action="store_true",
                   help="为论文仓库创建隔离 venv 并安装依赖（默认关闭；等价于 P2C_REPO_VENV_AUTO=1）")
    r.add_argument("--quiet", action="store_true")

    # -- batch ------------------------------------------------------------ #
    b = sub.add_parser("batch", help="批量处理一个目录下的所有论文包")
    b.add_argument("root", type=Path, help="包含若干论文包目录的根目录")
    b.add_argument("-o", "--out", type=Path, default=None,
                   help="批量产物根目录（默认 outputs/batch/<批次目录名>_<时间戳>/）")
    b.add_argument("--explain", default="courseware,podcast")
    b.add_argument("--no-reproduce", action="store_true")
    b.add_argument("--no-audio", action="store_true")
    b.add_argument("--no-pseudo-run", action="store_true")
    b.add_argument("--no-mvp", action="store_true")  # MVP-PATCH:cli-batch-flag
    b.add_argument("--resume", action="store_true")
    b.add_argument("--max-records", type=int, default=None)
    b.add_argument("--install-repo-deps", action="store_true",
                   help="为论文仓库创建隔离 venv 并安装依赖（默认关闭）")
    b.add_argument("--llm", choices=["offline", "openai"], default=None)

    # -- serve ------------------------------------------------------------ #
    s = sub.add_parser("serve", help="启动本地 Web UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    # M5-⑤：热更新默认开（改代码不用重启服务）；要关掉用 --no-reload
    s.add_argument("--reload", dest="reload", action="store_true", default=True,
                   help="改动代码自动重载（默认开）")
    s.add_argument("--no-reload", dest="reload", action="store_false", help="关闭热更新")

    # -- mvp（# MVP-PATCH:cli-mvp-parser）---------------------------------- #
    m = sub.add_parser("mvp", help="只对一篇论文生成 MVP demo + 图表绘制说明（paper_id 或论文路径）")
    m.add_argument("source", help="论文文件 / 论文包目录 / arXiv ID / incoming 或 tests/inputs/single 下的 paper_id")
    m.add_argument("-o", "--out", type=Path, default=None,
                   help="输出根目录（默认 outputs/single/）")
    m.add_argument("--explain", default="", help=argparse.SUPPRESS)  # 共享后续的参数解析，不使用
    m.add_argument("--no-run", action="store_true", help="只生成代码，不执行")
    m.add_argument("--no-llm", action="store_true", help="只用离线脚手架")
    m.add_argument("--llm", choices=["offline", "openai"], default=None)
    m.add_argument("--model", default=None)
    m.add_argument("--base-url", default=None)
    m.add_argument("--api-key", default=None, help="一次性测试用；优先用环境变量 P2C_LLM_API_KEY")
    m.add_argument("--offline", action="store_true", help="Alias for --llm offline")
    m.add_argument("--pdf-backend", choices=["hybrid", "docling", "pymupdf"], default=None)

    # -- eval（评测闭环，roadmap M1） -------------------------------------- #
    e = sub.add_parser("eval", help="跑评测集并汇总指标（L1 合成 / L2 真实 分层报告）")
    e.add_argument("--set", default=None, help="评测集 JSON（默认 configs/eval_set.json）")
    e.add_argument("--out", type=Path, default=None, help="输出目录（默认 outputs/eval）")
    e.add_argument("--compare", type=Path, default=None, help="与基线 latest.json 对比")
    e.add_argument("--llm", action="store_true", help="使用已配置的 LLM 端点（默认离线、确定性）")

    # -- sandbox（执行沙箱，roadmap M4） ----------------------------------- #
    sb = sub.add_parser("sandbox", help="查看执行沙箱策略与容器镜像状态；--build 预构建沙箱镜像")
    sb.add_argument("--build", action="store_true",
                    help="构建缺失的沙箱镜像（幂等；构建期需要联网，运行期不联网）")

    # -- tasks（任务队列，roadmap M5-①） ----------------------------------- #
    t = sub.add_parser("tasks", help="查看后台任务队列（SQLite）；--cancel 取消一个任务")
    t.add_argument("--limit", type=int, default=20, help="列出最近多少个任务")
    t.add_argument("--cancel", default=None, help="取消指定 task_id")
    t.add_argument("--prune", action="store_true", help="清理陈旧任务记录（保留最近 200 条）")

    # -- export（导出，roadmap M5-③） -------------------------------------- #
    x = sub.add_parser("export", help="把某篇论文的 MVP 导出为 ipynb + Streamlit 小演示")
    x.add_argument("paper_id", help="论文 id（outputs/single/ 下的目录名）")
    x.add_argument("-o", "--out", type=Path, default=None,
                   help="输出根目录（默认 outputs/single/）")
    x.add_argument("--repo", default=None, help="托管 notebook 的 GitHub 仓库 owner/repo（用于一键 Colab 链接）")

    # -- datasets（数据集，roadmap M6-③） ---------------------------------- #
    d = sub.add_parser("datasets", help="审查数据集声明（只审不下载：许可 + 校验和元数据）")
    d.add_argument("--spec", type=Path, default=None, help="声明文件（默认 configs/datasets.yaml）")
    d.add_argument("--allow-unknown-license", action="store_true", help="放行许可不在白名单的数据源")

    # -- llm-check（真实端点预检，roadmap M1/M6 的 LLM 路径验证） ---------- #
    lc = sub.add_parser("llm-check", help="预检真实 LLM 端点：只花一次调用，打印延迟/用量/真实报错原因")
    lc.add_argument("--model", default=None)
    lc.add_argument("--base-url", default=None)
    lc.add_argument("--api-key", default=None, help="仅本次会话用；优先环境变量与 configs/api_keys.yaml")

    # -- review-sheet（人工复核，roadmap M1 人工评语） ---------------------- #
    rs = sub.add_parser("review-sheet", help="生成/写回「人工复核表」（保真度 1–5 分的初判与证据）")
    rs.add_argument("--from", dest="from_dir", type=Path, default=None,
                    help="已跑完的评测目录（默认 outputs/eval；LLM 轮用 outputs/eval/llm）")
    rs.add_argument("--set", dest="eval_set", type=Path, default=None, help="评测集（默认 configs/eval_set.json）")
    rs.add_argument("--apply", type=Path, default=None, help="把填好的人工分数写回评测集（先演练，加 --yes 才落盘）")
    rs.add_argument("--yes", action="store_true", help="确认落盘（不加则只打印 diff）")

    # -- plugins（插件，roadmap M5-④） ------------------------------------- #
    sub.add_parser("plugins", help="列出插件注册的领域模板 / 图表风格 / 执行器，以及导入错误")

    # -- list / info ------------------------------------------------------ #
    sub.add_parser("list", help="列出已生成的 run")
    sub.add_parser("info", help="打印当前配置")
    return p


def _apply_llm_flags(args: argparse.Namespace):
    overrides = {}
    if getattr(args, "llm", None):
        overrides["llm_mode"] = args.llm
    # `--offline` 是 `--llm offline` 的别名；它属于「显式指定」，必须压过
    # 「有 Key 自动升级到 openai」那条规则（roadmap M0-2）。
    if getattr(args, "offline", False):
        overrides["llm_mode"] = "offline"
    if getattr(args, "model", None):
        overrides["llm_model"] = args.model
    if getattr(args, "base_url", None):
        overrides["llm_base_url"] = args.base_url
    if getattr(args, "max_records", None):
        overrides["max_records"] = args.max_records
    if getattr(args, "install_repo_deps", False):
        # D：显式开启隔离环境安装；绝不由默认配置静默联网装包
        overrides["repo_venv_auto"] = True
    return get_settings(refresh=True, **overrides)


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    cmd = args.command or "info"

    if cmd == "info":
        st = get_settings()
        print(json.dumps(st.to_dict(), ensure_ascii=False, indent=2))
        return 0

    if cmd == "list":
        runs = list_runs()
        if not runs:
            print("尚无运行记录。试试：python -m paper2code run samples/01_open_source")
            return 0
        for r in runs:
            art = r.get("artifacts", {})
            mode = (art.get("reproduce") or {}).get("mode_label", "-")
            print(f"[{r.get('paper_id'):<28}] {mode:<18} {r.get('title','')[:60]}")
        return 0

    if cmd == "serve":
        try:
            import uvicorn  # type: ignore
        except ImportError:
            print("需要安装 Web 依赖：pip install fastapi uvicorn", file=sys.stderr)
            return 2
        extra = {}
        if args.reload:
            # 热重载**只盯源码包**。默认它盯住整个仓库，而一次运行会把论文自带的 repo
            # 复制进 ``outputs/single/<pid>/reproduce/repo/**``（全是 .py）——WatchFiles 一看到
            # 就 Reloading，正在跑的 worker 连同任务被连根重启（实测踩过这个坑）。
            extra = {
                "reload_dirs": [str(Path(__file__).resolve().parent)],
                "reload_excludes": ["*.work*", "*outputs*", "*incoming*", "*samples*",
                                    "*_test_paper*", "*_corpus*", "*_incoming_files*"],
            }
        uvicorn.run("paper2code.api:app", host=args.host, port=args.port,
                    reload=args.reload, **extra)
        return 0

    if cmd == "eval":
        from .eval import run_eval

        default_set = Path(__file__).resolve().parent.parent / "configs" / "eval_set.json"
        run_eval(Path(args.set) if args.set else default_set, args.out, args.compare, use_llm=args.llm)
        return 0

    if cmd == "sandbox":  # M4：执行沙箱策略 + 容器镜像（本地模式与容器模式都用得上）
        from .reproduce import runner

        if args.build:
            err = runner.ensure_image(str(runner.image_status()["image"]))
            print("镜像构建：%s" % ("成功（已就绪）" if not err else "失败：%s" % err))
        pol = runner.resolve_policy()
        print(json.dumps({"policy": pol.to_dict(), "image": runner.image_status(),
                          "notice": runner.sandbox_notice()}, ensure_ascii=False, indent=2))
        return 0 if not (args.build and not runner.image_status()["present"]) else 2

    if cmd == "tasks":  # M5-①：队列状态与取消（无 Web 端时也能操作）
        from .taskqueue import STATUS_ZH, get_queue

        q = get_queue()
        if args.cancel:
            r = q.request_cancel(args.cancel)
            print("取消 %s：%s" % (args.cancel, r.get("note") or r.get("reason") or r))
            return 0 if r.get("ok") else 2
        if args.prune:
            print("已清理陈旧任务记录 %d 条（保留最近 200 条）" % q.prune(keep=200))
        st = q.stats()
        cfg = get_settings().queue_config()
        print("任务队列：并发上限 %s · 单任务超时 %ss · DB %s"
              % (cfg["concurrency"], cfg["timeout_s"], cfg["db"]))
        counts = " · ".join("%s %d" % (STATUS_ZH.get(k, k), v) for k, v in st.items() if k != "total" and v)
        print("计数：%s" % (counts or "（空）"))
        for t in q.list(limit=args.limit):
            note = ("　%s" % t.error[:80]) if t.error else ""
            cur = (t.progress or {}).get("current", "") if t.status == "running" else ""
            prog = ("　当前阶段 %s" % cur) if cur else ""
            print("[%s] %-11s %-9s %s%s%s" % (t.id, t.status, t.kind, t.label or t.source, prog, note))
        return 0

    if cmd == "export":  # M5-③：导出 ipynb / Streamlit
        from .mvp.export import export_mvp

        out_root = Path(args.out) if args.out else get_settings().output_dir
        mvp_dir = out_root / args.paper_id / "mvp"
        title = args.paper_id
        try:
            manifest = json.loads((out_root / args.paper_id / "manifest.json").read_text(encoding="utf-8"))
            title = manifest.get("title") or title
        except (OSError, ValueError):
            pass
        repo = args.repo if args.repo is not None else str(getattr(get_settings(), "notebook_repo", "") or "")
        info = export_mvp(mvp_dir, args.paper_id, title=title, repo=repo)
        if not info.get("ok"):
            print("导出失败：%s" % info.get("reason"), file=sys.stderr)
            return 2
        print("已导出到 %s" % info["dir"])
        print("  notebook：%s（%s 个单元格）" % (info["notebook"], info["n_cells"]))
        print("  Streamlit：%s" % info["streamlit"])
        print("  用法说明：%s" % info["readme"])
        if info.get("colab_url"):
            print("  一键 Colab：%s" % info["colab_url"])
        else:
            print("  一键 Colab：未配置 notebook_repo，先按 README 上传 notebook 再打开（不给假链接）")
        return 0

    if cmd == "datasets":  # M6-③：先审后取（只审不下载）
        from . import datasets

        st = get_settings()
        spec_path = Path(args.spec) if args.spec else Path(st.workspace) / str(st.datasets_spec or "")
        sources = datasets.load_spec(spec_path)
        rep = datasets.check_spec(sources, allow_unknown_license=args.allow_unknown_license)
        print("数据源声明：%s" % spec_path)
        print("共 %d 个：可下载 %d、被拦 %d" % (rep["n_total"], rep["n_allowed"], rep["n_blocked"]))
        for r in rep["sources"]:
            mark = "✅" if r["license_ok"] else "⛔"
            print("  %s %-24s 许可 %-16s %s" % (mark, r["name"], r["license"] or "（未声明）",
                                                r["license_check"]))
            print("      校验和：%s" % r["checksum_check"])
        if not rep["n_total"]:
            print("  （没有声明任何数据源——跑论文时也不会去下载任何东西）")
        for w in rep["warnings"][:5]:
            print("  ⚠️ %s" % w)
        return 0

    if cmd == "llm-check":  # 真实端点预检：只花一次调用，避免盲跑整轮评测
        import time as _time

        from .config import API_KEYS_FILE
        from .llm import get_provider

        if args.api_key:
            os.environ.setdefault("P2C_LLM_API_KEY", args.api_key)
        st = get_settings(refresh=True, llm_mode="openai" if args.api_key else None,
                          llm_model=args.model, llm_base_url=args.base_url)
        cfg = st.llm_config()
        print("模式=%s ｜ 端点=%s ｜ 模型=%s ｜ Key=%s"
              % (cfg["mode"], cfg["base_url"], cfg["model"],
                 "已配置(%d 字符)" % len(cfg["api_key"]) if cfg["api_key"] else "缺失"))
        if cfg["mode"] != "openai" or not cfg["api_key"]:
            print("没有可用的 openai 配置。二选一：\n"
                  "  1) 写 %s（已被 gitignore 与打包排除）：\n"
                  "       llm_mode: openai\n       llm_base_url: https://api.deepseek.com/v1\n"
                  "       llm_model: deepseek-chat\n       llm_api_key: sk-...\n"
                  "  2) 设环境变量 P2C_LLM_MODE=openai / P2C_LLM_BASE_URL / P2C_LLM_MODEL / P2C_LLM_API_KEY"
                  % API_KEYS_FILE, file=sys.stderr)
            return 2
        provider = get_provider(refresh=True)
        t0 = _time.time()
        try:
            out = provider.complete("你只回复 JSON。", '只回复这个 JSON：{"ok": true}', stage="llm_check")
        except Exception as exc:
            print("调用失败（%.1fs）：%s: %s" % (_time.time() - t0, type(exc).__name__, str(exc)[:200]),
                  file=sys.stderr)
            why = next((w for w in reversed(getattr(provider, "warnings", [])) if "降级" in str(w)), "")
            if why:
                print("真实原因：%s" % why, file=sys.stderr)
            return 1
        usage = provider.get_usage().to_dict() if hasattr(provider, "get_usage") else {}
        print("调用成功 ｜ 用时 %.1fs ｜ 回复 %r" % (_time.time() - t0, (out or "")[:120]))
        print("用量：%s ｜ 护栏：%s" % (json.dumps(usage, ensure_ascii=False),
                                    provider.budget_tripped() if hasattr(provider, "budget_tripped") else "-"))
        print("预检通过，可以跑：python -m paper2code eval --llm")
        return 0

    if cmd == "review-sheet":  # 人工复核：生成初判表 / 把人工分数写回评测集
        from . import annotate
        from .eval import load_eval_set

        set_path = Path(args.eval_set) if args.eval_set else (Path(__file__).resolve().parent.parent
                                                             / "configs" / "eval_set.json")
        if args.apply:
            filled = json.loads(Path(args.apply).read_text(encoding="utf-8"))
            res = annotate.merge_into_eval_set(set_path, filled, apply=bool(args.yes))
            print(annotate.render_merge(res))
            return 0 if res.get("ok") else 2

        run_dir = Path(args.from_dir) if args.from_dir else get_settings().eval_dir
        latest = run_dir / "latest.json"
        if not latest.exists():
            print("找不到评测结果：%s（先跑 `python -m paper2code eval`）" % latest, file=sys.stderr)
            return 2
        # 直接用已落盘的评测条目重建复核表（不需要重跑流水线）
        from .eval import EntryResult

        payload = json.loads(latest.read_text(encoding="utf-8"))
        results = []
        for e in payload.get("entries", []):
            d = {k: v for k, v in e.items() if k in EntryResult.__dataclass_fields__}
            if not d.get("run_dir"):
                # 旧的 latest.json 里没记 run_dir：按评测目录的实际层级补一下（否则证据路径会指错）
                for cand in (run_dir / "_runs" / str(d.get("id") or "") / str(d.get("id") or ""),
                             run_dir / "_runs" / str(d.get("id") or ""),
                             run_dir / str(d.get("id") or "")):
                    if cand.exists():
                        d["run_dir"] = str(cand)
                        break
            results.append(EntryResult(**d))
        sheet = annotate.build(load_eval_set(set_path), results)
        paths = annotate.write(sheet, run_dir)
        print("人工复核表（初判 %d 篇，**未写回评测集**）" % len(sheet["entries"]))
        print("  Markdown：%s" % paths["markdown"])
        print("  待填 JSON：%s" % paths["json"])
        print("  填好后：python -m paper2code review-sheet --apply %s --yes" % paths["json"])
        return 0

    if cmd == "plugins":  # M5-④：插件注册点与导入错误
        from . import plugins

        info = plugins.ensure_loaded()
        rep = plugins.PluginReport.current()
        print("插件模块：%s" % ("、".join(info["modules"]) or "（未配置，见 configs/plugins.yaml）"))
        if info["errors"]:
            print("导入错误：")
            for e in info["errors"]:
                print("  ✗ %s" % e)
        print("领域模板 %d 个：" % len(plugins.list_templates()))
        for t in plugins.list_templates():
            kind = "插件" if t["name"] in info["templates"] else "内置"
            print("  - [%s] %s（%s）：%s" % (kind, t["name"], t.get("label", ""),
                                          "、".join(list(t.get("hints", []))[:6])))
        styles = plugins.list_styles()
        print("图表风格 %d 个：%s" % (len(styles), "、".join(s["name"] for s in styles) or "（无）"))
        ex = plugins.list_executors()
        print("执行器：")
        for name, meta in ex.items():
            print("  - [%s] %s ｜ 可用：%s ｜ 风险：%s" % (meta["kind"], name,
                                                          "是" if meta["available"] else "否", meta["risk"]))
        print("摘要：%s" % rep.summary())
        return 0

    settings = _apply_llm_flags(args)
    explain = tuple(k.strip() for k in (args.explain or "").split(",") if k.strip())

    if cmd == "mvp":  # MVP-PATCH:cli-mvp-cmd
        from .ingest import load_paper
        from .mvp import build_mvp

        src = args.source
        if not Path(src).exists():
            for base in (settings.workspace / "incoming", settings.single_input_dir):
                cand = base / src
                if cand.exists():
                    src = str(cand)
                    break
        paper = load_paper(src)
        out_root = Path(args.out) if args.out else settings.output_dir
        pdir = out_root / paper.id
        pdir.mkdir(parents=True, exist_ok=True)
        # M2-2：先落盘图资产，FIGURES.md 才能给出「原图 vs 重绘」
        from .ingest import figassets as _figassets
        _figassets.export_for_paper(paper, pdir / "figures")
        res = build_mvp(paper, pdir / "mvp", execute=not args.no_run, use_llm=False if args.no_llm else None)
        print(f"\n=== {paper.id} | MVP ===")
        print(f"  生成方式：{res.generator} · demo 运行：{'未执行' if res.demo_ok is None else ('通过' if res.demo_ok else '失败')}")
        sb = res.sandbox or {}
        print(f"  执行沙箱：{sb.get('mode', '-')}（{sb.get('isolation', '-')}，风险："
              f"{'高' if sb.get('risk') == 'high' else '低'}{'，已降级' if sb.get('degraded') else ''}）")
        print(f"  图表说明 {len(res.figure_specs)} 张 · 已出图 {sum(1 for v in res.figures.values() if v.get('files'))} 张")
        print(f"  报告：{res.report_path}")
        for w in res.warnings[:6]:
            print(f"    * {w}")
        return 0 if res.ok else 2

    if cmd == "run":
        manifest = run_paper(
            args.source,
            output_dir=args.out,
            explain=explain,
            reproduce=not args.no_reproduce,
            run_pseudocode=not args.no_pseudo_run,
            audio=not args.no_audio,
            mvp=False if args.no_mvp else None,  # MVP-PATCH:cli-run-call
            resume=args.resume,
        )
        if not args.quiet:
            _print_manifest(manifest)
        return 0

    if cmd == "batch":
        root = args.root
        if not root.is_dir():
            print(f"目录不存在：{root}", file=sys.stderr)
            return 2
        # 以 ``_`` 开头的目录/文件视为临时草稿（如 _pdfcheck/），批量时跳过。
        # 这条约定让「用真 PDF 试跑」不会污染 batch 的结果表。
        def _scratch(p: Path) -> bool:
            return p.name.startswith(("_", "."))

        targets: List[Path] = sorted(
            d for d in root.iterdir() if d.is_dir() and not _scratch(d)
        )
        if not targets:
            targets = sorted(
                p for p in root.iterdir()
                if p.suffix.lower() in (".md", ".pdf", ".html", ".txt") and not _scratch(p)
            )
        if not targets:
            print(f"目录下没有可处理的论文：{root}", file=sys.stderr)
            return 2
        out_root = args.out
        if out_root is None:
            out_root = settings.batch_dir / f"{root.name}_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        results = run_batch(
            targets,
            output_dir=out_root,
            explain=explain,
            reproduce=not args.no_reproduce,
            run_pseudocode=not args.no_pseudo_run,
            audio=not args.no_audio,
            mvp=False if args.no_mvp else None,  # MVP-PATCH:cli-batch-call
            resume=args.resume,
        )
        for m in results:
            if "error" in m:
                print(f"❌ {m['source']}: {m['error']}")
            else:
                _print_manifest(m, brief=True)
        print(f"\n共处理 {len(results)} 篇，产物目录：{Path(out_root).resolve()}")
        return 0

    parser.print_help()
    return 1


def _print_manifest(m: dict, brief: bool = False) -> None:
    art = m.get("artifacts", {})
    print(f"\n=== {m.get('paper_id')} | {m.get('title','')[:70]} ===")
    st = m.get("stats", {})
    print(
        f"  结构：章节 {st.get('sections')} · 表格 {st.get('tables')} · 图 {st.get('figures')} · "
        f"伪代码 {st.get('algorithms')} · 代码链接 {st.get('code_refs')}"
    )
    if st.get("parse_quality") is not None:
        mark = "⚠️ 偏低" if st.get("parse_quality_level") == "low" else "ok"
        extra = st.get("parse_quality_failed") or []
        suffix = f"（未读出：{'、'.join(extra)}）" if extra else ""
        print(f"  解析质量：{st['parse_quality']:.2f} {mark}{suffix}")
    ex = art.get("explain")
    if ex:
        print(f"  讲解：{ex.get('n_slides')} 页课件 · 播客 {ex.get('n_turns')} 轮 · provider={ex.get('generator')}")
        if brief:
            pass
        else:
            for k in ("courseware", "podcast_script", "podcast_audio"):
                if ex.get(k):
                    print(f"    - {k}: {ex[k]}")
    rp = art.get("reproduce")
    if rp:
        ck = rp.get("checks", {})
        print(
            f"  复现：{rp.get('mode_label')} · 校验 "
            f"pass {ck.get('pass',0)}/warn {ck.get('warn',0)}/fail {ck.get('fail',0)} · "
            f"重绘图表 {len(rp.get('figures', []))} 张"
        )
        if not brief and rp.get("report"):
            print(f"    - report: {rp['report']}")
    mv = art.get("mvp")  # MVP-PATCH:cli-print
    if mv:
        print(
            f"  MVP：{mv.get('generator')} · demo {'通过' if mv.get('demo_ok') else ('未执行' if mv.get('demo_ok') is None else '失败')} · "
            f"图表说明 {mv.get('n_figure_specs')} 张 / 已出图 {mv.get('n_figures_drawn')} 张"
        )
        if not brief and mv.get("report"):
            print(f"    - report: {mv['report']}")
    if m.get("warnings") and not brief:
        print("  注意：")
        for w in m["warnings"][:6]:
            print(f"    * {w}")


if __name__ == "__main__":
    raise SystemExit(main())
