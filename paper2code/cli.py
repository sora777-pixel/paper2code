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
import sys
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
    r.add_argument("-o", "--out", type=Path, default=None, help="输出根目录（默认 outputs/）")
    r.add_argument(
        "--explain", default="courseware,podcast",
        help="讲解材料类型，逗号分隔：courseware,podcast（留空表示不生成）",
    )
    r.add_argument("--no-reproduce", action="store_true", help="跳过复现环节")
    r.add_argument("--no-audio", action="store_true", help="只出播客脚本与字幕，不合成音频")
    r.add_argument("--no-pseudo-run", action="store_true", help="情形 C 只翻译不执行")
    r.add_argument("--llm", choices=["offline", "openai"], default=None, help="大模型模式")
    r.add_argument("--model", default=None, help="模型名（openai 模式）")
    r.add_argument("--base-url", default=None, help="OpenAI 兼容端点（如 https://api.deepseek.com/v1）")
    r.add_argument(
        "--api-key", default=None,
        help="API Key（一次性测试用；优先用环境变量 P2C_LLM_API_KEY，勿写入配置文件）",
    )
    r.add_argument("--max-records", type=int, default=None, help="数据规模上限（默认 10000）")
    r.add_argument("--quiet", action="store_true")
    r.add_argument(
        "--pdf-backend", choices=["hybrid", "docling", "pymupdf"], default=None,
        help="PDF parser backend (also env P2C_PDF_BACKEND)",
    )
    r.add_argument(
        "--offline", action="store_true",
        help="Alias for --llm offline",
    )

    # -- batch ------------------------------------------------------------ #
    b = sub.add_parser("batch", help="批量处理一个目录下的所有论文包")
    b.add_argument("root", type=Path, help="包含若干论文包目录的根目录")
    b.add_argument("-o", "--out", type=Path, default=None)
    b.add_argument("--explain", default="courseware,podcast")
    b.add_argument("--no-reproduce", action="store_true")
    b.add_argument("--no-audio", action="store_true")
    b.add_argument("--no-pseudo-run", action="store_true")
    b.add_argument("--max-records", type=int, default=None)
    b.add_argument("--llm", choices=["offline", "openai"], default=None)
    b.add_argument("--model", default=None, help="模型名（openai 模式）")
    b.add_argument("--base-url", default=None, help="OpenAI 兼容端点")
    b.add_argument(
        "--api-key", default=None,
        help="API Key（一次性测试用；优先用环境变量 P2C_LLM_API_KEY）",
    )
    b.add_argument(
        "--pdf-backend", choices=["hybrid", "docling", "pymupdf"], default=None,
        help="PDF parser backend (also env P2C_PDF_BACKEND)",
    )
    b.add_argument("--offline", action="store_true", help="Alias for --llm offline")

    # -- serve ------------------------------------------------------------ #
    s = sub.add_parser("serve", help="启动本地 Web UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--reload", action="store_true")

    # -- list / info ------------------------------------------------------ #
    sub.add_parser("list", help="列出已生成的 run")
    sub.add_parser("info", help="打印当前配置")
    return p


def _apply_llm_flags(args: argparse.Namespace):
    overrides = {}
    if getattr(args, "llm", None):
        overrides["llm_mode"] = args.llm
    if getattr(args, "model", None):
        overrides["llm_model"] = args.model
    if getattr(args, "base_url", None):
        overrides["llm_base_url"] = args.base_url
    if getattr(args, "api_key", None):
        overrides["llm_api_key"] = args.api_key
    if getattr(args, "max_records", None):
        overrides["max_records"] = args.max_records
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
        uvicorn.run("paper2code.api:app", host=args.host, port=args.port, reload=args.reload)
        return 0

    # Honour --offline / --pdf-backend without breaking existing commands
    if getattr(args, "offline", False) and not getattr(args, "llm", None):
        args.llm = "offline"
    if getattr(args, "pdf_backend", None):
        import os as _os
        _os.environ["P2C_PDF_BACKEND"] = args.pdf_backend

    settings = _apply_llm_flags(args)
    explain = tuple(k.strip() for k in (args.explain or "").split(",") if k.strip())

    if cmd == "run":
        manifest = run_paper(
            args.source,
            output_dir=args.out,
            explain=explain,
            reproduce=not args.no_reproduce,
            run_pseudocode=not args.no_pseudo_run,
            audio=not args.no_audio,
        )
        if not args.quiet:
            _print_manifest(manifest)
        return 0

    if cmd == "batch":
        root = args.root
        if not root.is_dir():
            print(f"目录不存在：{root}", file=sys.stderr)
            return 2
        targets: List[Path] = sorted(d for d in root.iterdir() if d.is_dir())
        if not targets:
            targets = sorted(
                p for p in root.iterdir()
                if p.suffix.lower() in (".md", ".pdf", ".html", ".txt")
            )
        if not targets:
            print(f"目录下没有可处理的论文：{root}", file=sys.stderr)
            return 2
        results = run_batch(
            targets,
            output_dir=args.out,
            explain=explain,
            reproduce=not args.no_reproduce,
            run_pseudocode=not args.no_pseudo_run,
            audio=not args.no_audio,
        )
        for m in results:
            if "error" in m:
                print(f"❌ {m['source']}: {m['error']}")
            else:
                _print_manifest(m, brief=True)
        print(f"\n共处理 {len(results)} 篇，产物目录：{settings.output_dir}")
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
    if m.get("warnings") and not brief:
        print("  注意：")
        for w in m["warnings"][:6]:
            print(f"    * {w}")


if __name__ == "__main__":
    raise SystemExit(main())
