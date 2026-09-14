"""情形判定：论文属于「有开源代码 / 仅伪代码 / 无代码」中的哪一类。

判定优先级（与需求一致）：

1. **有开源代码**：正文出现 GitHub（或其他代码托管）仓库链接，或论文包目录里
   直接附带了 ``code/``。这是最强的复现条件，优先采用。
2. **仅伪代码**：没有仓库链接，但存在被识别为伪代码的算法块。
3. **无代码**：两者都没有，只能依据正文描述与表格数据重建。

判定结果会带上 ``evidence``，写进复现报告，保证可追溯、可人工复核。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from ..models import MODE_NO_CODE, MODE_OPEN_SOURCE, MODE_PSEUDOCODE, Paper

#: 常见代码托管 / 代码分发站点（非 GitHub）
_OTHER_CODE_HOSTS = (
    "gitlab.com", "bitbucket.org", "gitee.com", "codeberg.org",
    "sourceforge.net", "huggingface.co/spaces", "colab.research.google.com",
    "openreview.net/attachment", "zenodo.org/record",
)


@dataclass
class ModeDecision:
    mode: str = MODE_NO_CODE
    confidence: float = 0.0
    reason: str = ""
    evidence: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "evidence": self.evidence,
        }


def detect_local_repo(paper: Paper) -> str:
    """论文包目录里是否直接附带了代码。"""
    p = paper.meta.get("local_code_path")
    if p and Path(str(p)).exists():
        return str(p)
    return ""


def classify(paper: Paper) -> ModeDecision:
    evidence: List[str] = []

    # -- 1) 本地附带的代码 ------------------------------------------------- #
    local = detect_local_repo(paper)
    if local:
        files = _count_code_files(Path(local))
        evidence.append(f"论文包内附带代码目录：{local}（{files} 个代码文件）")
        if paper.code_refs:
            evidence.append(f"正文另有 {len(paper.code_refs)} 个代码链接")
        return ModeDecision(MODE_OPEN_SOURCE, 0.98, "论文包内直接附带可读代码，按「有开源代码」处理", evidence)

    # -- 2) 正文中的仓库链接 ----------------------------------------------- #
    repo_refs = [r for r in paper.code_refs if r.kind == "repo"]
    other_refs = [
        r for r in paper.code_refs
        if r.kind == "other" and any(h in r.url.lower() for h in _OTHER_CODE_HOSTS)
    ]
    if repo_refs or other_refs:
        for r in (repo_refs + other_refs)[:5]:
            evidence.append(f"代码链接：{r.url}" + (f"（出处：{r.evidence[:80]}…）" if r.evidence else ""))
        stmt = paper.meta.get("code_statement") or ""
        if stmt:
            evidence.append(f"正文表述：{stmt[:160]}")
        return ModeDecision(
            MODE_OPEN_SOURCE,
            0.9 if repo_refs else 0.75,
            "论文正文给出代码仓库链接，按「有开源代码」处理",
            evidence,
        )

    # -- 3) 仅伪代码 ------------------------------------------------------- #
    if paper.algorithms:
        for a in paper.algorithms[:4]:
            evidence.append(f"算法块 {a.id}：{a.caption[:90]}（{len(a.lines)} 行伪代码）")
        return ModeDecision(
            MODE_PSEUDOCODE,
            0.85,
            f"未发现代码链接，但识别到 {len(paper.algorithms)} 段伪代码，按「仅伪代码」处理",
            evidence,
        )

    # -- 4) 无代码 --------------------------------------------------------- #
    evidence.append("未发现代码链接，也未识别出伪代码算法块")
    if paper.tables:
        evidence.append(f"可用输入：论文中的 {len(paper.tables)} 张表格 + 附带数据文件")
    if paper.meta.get("code_statement"):
        evidence.append(f"正文相关表述：{str(paper.meta['code_statement'])[:160]}")
    return ModeDecision(MODE_NO_CODE, 0.7, "无代码、无伪代码，只能依据正文与表格数据重建", evidence)


def _count_code_files(root: Path, limit: int = 500) -> int:
    exts = {".py", ".ipynb", ".sh", ".r", ".jl", ".cpp", ".cc", ".java", ".js", ".ts", ".yaml", ".yml", ".toml"}
    n = 0
    try:
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() in exts:
                n += 1
                if n >= limit:
                    break
    except Exception:
        pass
    return n


MODE_LABELS = {
    MODE_OPEN_SOURCE: "情形 A · 附带开源代码",
    MODE_PSEUDOCODE: "情形 C · 仅含伪代码",
    MODE_NO_CODE: "情形 B · 无任何代码",
}


def mode_label(mode: str) -> str:
    return MODE_LABELS.get(mode, mode)
