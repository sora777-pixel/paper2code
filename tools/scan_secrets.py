"""仓库密钥扫描（纯标准库，零依赖，可直接用于 CI / pre-commit）。

为什么还要一个自研扫描器：`gitleaks` 需要额外安装二进制，而本项目的底线是
"零第三方硬依赖也能跑通"。这个脚本覆盖最常见的密钥形态，作为第一道闸门；
CI 里再叠加 gitleaks（见 `.github/workflows/ci.yml`）做第二道。

用法::

    python tools/scan_secrets.py              # 扫描整个仓库（跳过产物/语料/虚拟环境）
    python tools/scan_secrets.py --staged      # 只扫 git 暂存区（非 git 仓库时退化为全量）
    python tools/scan_secrets.py --path docs   # 只扫某个目录

退出码：0 = 未发现；1 = 发现疑似密钥。
"""
from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Iterator, List, Tuple

ROOT = Path(__file__).resolve().parent.parent

# 这些目录不进版本库、也不该被扫（产物 / 语料 / 虚拟环境 / 工具缓存）
SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    ".work", ".workbuddy", "_corpus", "_incoming_files_10_19", "incoming",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    # 测试输入/输出与本地文档：体积大、含论文 id 等高熵字符串，不属于源码
    "outputs", "local_docs", "tests", "samples", "_test_paper", "_test_paper_si",
    ".trae-html-share-packages",
}
# 论文 id / 目录名：形如 `Disruption-of-Hsp70.14-BAG2-...-0f68d6a2`，
# 由多个连字符词段组成，高熵但明显不是密钥。
PAPER_ID = re.compile(r"[A-Za-z0-9]+(?:[-.][A-Za-z0-9]+){4,}")
# 二进制或体积大且无意义的扩展名
SKIP_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico",
    ".pdf", ".zip", ".gz", ".7z", ".tar",
    ".mp3", ".wav", ".m4a", ".flac",
    ".svg", ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".pt", ".pth", ".bin", ".onnx", ".so", ".dll", ".exe",
    # Office 压缩容器（zip+XML），按文本读会产出乱码与误报
    ".xlsx", ".xlsm", ".xls", ".docx", ".pptx", ".ods", ".odp", ".odt",
}
MAX_BYTES = 2 * 1024 * 1024

# 明确的高危形态（命中即报）
RULES: List[Tuple[str, "re.Pattern[str]"]] = [
    ("OpenAI/兼容端点密钥", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")),
    ("Anthropic 密钥", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("AWS Access Key ID", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Google API Key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("GitHub Token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("Slack Token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("私钥文件", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("带字面量的 api_key 赋值", re.compile(
        r"""(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token|client[_-]?secret)\b\s*[:=]\s*["'][^"'\s]{16,}["']""")),
    ("Bearer 字面量令牌", re.compile(r"""Bearer\s+[A-Za-z0-9_\-\.]{24,}""")),
]
# 明显是占位符的，不算命中
PLACEHOLDER = re.compile(
    r"(?i)(xxx+|your[_-]?|<[^>]{0,40}>|example|placeholder|redacted|dummy|fake|test[_-]?key|\.\.\.)")
# 包名 / 制品文件名（pip 日志、wheel 名等），不属于密钥
ARTIFACT_NAME = re.compile(
    r"(?i)(-py\d|-none-any|manylinux|win_amd64|win32|macosx|\.whl|\.tar\.gz|\.metadata|-cp\d\d|-abi3)")


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = {c: s.count(c) for c in set(s)}
    return -sum((n / len(s)) * math.log2(n / len(s)) for n in counts.values())


def _iter_files(path: Path) -> Iterator[Path]:
    if path.is_file():
        yield path
        return
    for p in path.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() in SKIP_EXTS:
            continue
        try:
            if p.stat().st_size > MAX_BYTES:
                continue
        except OSError:
            continue
        yield p


def _staged_files() -> List[Path] | None:
    try:
        out = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=20,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return [ROOT / ln.strip() for ln in out.stdout.splitlines() if ln.strip()]


def _mask(token: str) -> str:
    return token[:6] + "…" + token[-4:] if len(token) > 12 else token[:3] + "…"


def scan(files: Iterable[Path]) -> List[Tuple[Path, int, str, str]]:
    findings: List[Tuple[Path, int, str, str]] = []
    for p in files:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            # URL 里的路径段（Colab /drive/<id>、AWS 预签名等）有高熵也会被误判，
            # 但它们不是密钥。这里先算出行内的 URL 区间，落在其中的 token 一律跳过。
            url_spans = [(m.start(), m.end()) for m in re.finditer(r"\w+://\S+", line)]

            def _in_url(pos: int) -> bool:
                return any(a <= pos < b for a, b in url_spans)

            for label, pat in RULES:
                m = pat.search(line)
                if not m:
                    continue
                hit = m.group(0)
                if PLACEHOLDER.search(hit) or PLACEHOLDER.search(line):
                    continue
                if _in_url(m.start()):
                    continue
                findings.append((p, i, label, _mask(hit)))
            # 高熵长串兜底：要求**同时含大写、小写、数字**（真实密钥几乎都是这种混排），
            # 否则会把化学名（`4-AMINOBUTYRIC-ACID`，全大写）、包名
            # （`pdfminer_six-20260107-py3-none-any`，无大写）这类正常文本误判成密钥。
            for mt in re.finditer(r"[A-Za-z0-9_\-]{32,}", line):
                tok = mt.group(0)
                if PLACEHOLDER.search(line):
                    continue
                if not (re.search(r"[A-Z]", tok) and re.search(r"[a-z]", tok) and re.search(r"\d", tok)):
                    continue
                if ARTIFACT_NAME.search(tok) or PAPER_ID.fullmatch(tok) or _in_url(mt.start()):
                    continue
                if _entropy(tok) >= 3.6:
                    findings.append((p, i, "高熵长串", _mask(tok)))
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description="paper2code 密钥扫描（纯标准库）")
    ap.add_argument("--path", default=None, help="只扫描该路径（默认整个仓库）")
    ap.add_argument("--staged", action="store_true", help="只扫描 git 暂存区")
    a = ap.parse_args()

    if a.path:
        files: Iterable[Path] = _iter_files(Path(a.path))
    elif a.staged:
        staged = _staged_files()
        files = staged if staged is not None else _iter_files(ROOT)
    else:
        files = _iter_files(ROOT)

    raw = scan(files)
    seen = set()
    findings = []  # 同一 token 可能同时命中「规则」与「高熵」，去重避免噪音
    for p, i, label, masked in raw:
        key = (str(p), i, masked)
        if key in seen:
            continue
        seen.add(key)
        findings.append((p, i, label, masked))

    print(f"[scan_secrets] 已扫描 {ROOT if not a.path else a.path}")
    if not findings:
        print("[scan_secrets] 未发现疑似密钥。")
        return 0

    print(f"[scan_secrets] 发现 {len(findings)} 处疑似密钥：")
    for p, i, label, masked in findings:
        try:
            rel = p.relative_to(ROOT)
        except ValueError:
            rel = p
        print(f"  - {rel}:{i}  {label}  {masked}")
    print("\n处理建议：① 若为真实密钥，立刻到对应平台轮换（作废旧的）；"
          "② 改为从环境变量读取，不要写进文件；③ 若是误报，加入 .gitleaks.toml 的 allowlists。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
