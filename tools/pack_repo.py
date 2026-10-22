"""把仓库打包成 zip，排除敏感 / 体积大的内容（roadmap M0-1）。

上次分享出去的 260 MB 压缩包，绝大部分体积来自 `.venv/`；本脚本把这些目录、
密钥文件、补丁备份与临时草稿一律剔除，默认目标 < 20 MB。

用法::

    python tools/pack_repo.py                  # 输出到仓库的上一级目录
    python tools/pack_repo.py -o dist.zip      # 指定输出路径
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 整个目录剔除
EXCLUDE_DIRS = {
    ".git", ".venv", "venv", "env", "__pycache__", "node_modules",
    ".work", ".workbuddy", "outputs", "incoming",
    "_corpus", "_incoming_files_10_19", "_test_paper", "_test_paper_si",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".idea", ".vscode",
}
# 密钥文件（按仓库相对路径精确匹配）
EXCLUDE_PATHS = {"configs/api_keys.yaml"}
# 按文件名模式剔除
EXCLUDE_GLOBS = (
    "*.mvp.bak", "*.bak", "*.orig", "*.rej",
    "*.zip", "*.pdf", "*.mp3", "*.wav", "*.m4a",
    "_*.log", "_*.txt", "_probe_*.py", "_diag*.py", "_unzip*.py",
)
SIZE_WARN_BYTES = 20 * 1024 * 1024


def main() -> int:
    ap = argparse.ArgumentParser(description="paper2code 仓库打包（自动排除敏感/大体积内容）")
    ap.add_argument("-o", "--out", default=None, help="输出 zip 路径（默认写到仓库外）")
    a = ap.parse_args()

    out = Path(a.out) if a.out else ROOT.parent / f"paper2code_{date.today():%Y%m%d}.zip"
    out = out.resolve()

    kept = 0
    dropped_dirs = set()
    dropped_files = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(ROOT.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(ROOT)
            hit_dir = next((d for d in rel.parts if d in EXCLUDE_DIRS), None)
            if hit_dir:
                dropped_dirs.add(hit_dir)
                continue
            if rel.as_posix() in EXCLUDE_PATHS or any(rel.match(g) for g in EXCLUDE_GLOBS):
                dropped_files += 1
                continue
            z.write(p, rel.as_posix())
            kept += 1

    size = out.stat().st_size
    print(f"[pack_repo] 打包完成：{out}")
    print(f"[pack_repo] 文件 {kept} 个 · 体积 {size / 1024 / 1024:.2f} MB")
    if dropped_dirs:
        print(f"[pack_repo] 已剔除目录：{', '.join(sorted(dropped_dirs))}")
    if dropped_files:
        print(f"[pack_repo] 已剔除密钥/备份/草稿文件 {dropped_files} 个")
    if size > SIZE_WARN_BYTES:
        print(f"[pack_repo] ⚠️ 超过 {SIZE_WARN_BYTES // 1024 // 1024} MB 目标，"
              "请检查是否漏排除大目录（.venv / outputs / _corpus）")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
