"""解析质量打分。

把「这篇论文到底读进来多少」压成一个 0–1 的分数，低于阈值时在报告与 UI 里
**明示**，避免下游（讲解 / 复现 / 播客）在一份残缺的解析结果上继续跑而不自知。

设计原则（沿用本项目的「抽不到 ≠ 原文没有」）：**表格一项只要求「正文有引用
就必须抽到」，而不是要求论文一定有表格**；正文过短的样本也不会因为「没有表格」
而被扣分。这样分数反映的是*解析器自己的失败*，而不是论文的体裁。
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..models import Paper

#: 低于该分数判为「解析质量偏低」，必须明示
QUALITY_THRESHOLD = 0.75

#: 检查项 -> 中文名（用于告警文案）
_LABELS = {
    "title": "标题",
    "abstract": "摘要",
    "authors": "作者",
    "sections": "语义章节",
    "tables": "表格",
    "text": "正文文本",
}


def parse_checks(paper: Paper) -> Dict[str, bool]:
    """逐项检查解析结果是否可用。"""
    refs = int(paper.meta.get("table_refs") or 0)
    semantic = [s for s in paper.sections if s.kind != "section"]
    return {
        "title": len((paper.title or "").strip()) >= 8,
        "abstract": len((paper.abstract or "").strip()) >= 100,
        "authors": bool(paper.authors),
        "sections": len(semantic) >= 1,
        "tables": refs == 0 or len(paper.tables) > 0,
        "text": len(paper.raw_text or "") >= 2000,
    }


def score_parse(paper: Paper) -> Dict[str, Any]:
    """返回 ``{score, level, checks, failed, threshold}``。"""
    checks = parse_checks(paper)
    passed = sum(1 for ok in checks.values() if ok)
    score = passed / len(checks) if checks else 0.0
    failed: List[str] = [k for k, ok in checks.items() if not ok]
    return {
        "score": round(score, 3),
        "level": "ok" if score >= QUALITY_THRESHOLD else "low",
        "checks": checks,
        "failed": failed,
        "threshold": QUALITY_THRESHOLD,
    }


def quality_warning(quality: Dict[str, Any]) -> str:
    """低分时的点名式告警文案（合格则返回空串）。"""
    failed = quality.get("failed") or []
    if quality.get("level") == "ok" or not failed:
        return ""
    names = "、".join(_LABELS.get(k, k) for k in failed)
    return (
        "解析质量偏低（%.2f < %.2f）：%s 未能从原文读出。"
        "后续讲解/复现结论的可信度受限，请对照原文复核。"
        % (quality.get("score", 0.0), quality.get("threshold", QUALITY_THRESHOLD), names)
    )


def apply_quality(paper: Paper) -> Dict[str, Any]:
    """给 ``paper.meta`` 写入 ``parse_quality``，并在低分时挂上告警。

    幂等：重复调用不会重复追加告警（论文包会先解析文件、再补数据表，
    中途可能打两次分）。
    """
    quality = score_parse(paper)
    warning = quality_warning(quality)
    quality["warning"] = warning

    warnings = list(paper.meta.get("warnings") or [])
    previous = (paper.meta.get("parse_quality") or {}).get("warning")
    if previous and previous in warnings:
        warnings.remove(previous)
    if warning:
        warnings.append(warning)

    paper.meta["parse_quality"] = quality
    paper.meta["warnings"] = warnings
    return quality
