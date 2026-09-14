"""文本处理工具（stdlib）：分句、关键词、抽取式摘要。

同时支持中英文，用于「离线降级模式」——即没有配置任何大模型 API 时，
系统依然能产出结构化的讲解材料与复现计划。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Dict, Iterable, List, Sequence, Tuple

# 常见中英文停用词（精简版，够用即可）
STOPWORDS = set(
    """
a an the and or but if then than that this these those of in on at to for from by with without
is are was were be been being do does did doing have has had having will would shall should can
could may might must not no nor so such as it its we our you your they their he she his her
which who whom whose what when where why how all any both each few more most other some only own
same too very s t just don now also however therefore thus hence although though while whereas
about into over under between within during before after above below up down out off again further
one two three four five six seven eight nine ten new using used use based results result show shows
shown proposed method methods approach approaches paper work works model models data set table figure
可以 我们 你们 他们 这个 那个 这些 那些 以及 并且 但是 因为 所以 因此 如果 那么 就是 不是 没有
一个 一种 一些 我们 方法 模型 结果 数据 表格 图 论文 本文 提出 使用 通过 进行 能够 可以 需要 由于
""".split()
)

_SENT_END = re.compile(r"(?<=[.!?。！？；;])\s+|\n{2,}")
_CJK = re.compile(r"[\u4e00-\u9fff]")
_EN_WORD = re.compile(r"[A-Za-z][A-Za-z\-]{1,}")
_CJK_WORD = re.compile(r"[\u4e00-\u9fff]{2,6}")


def split_sentences(text: str) -> List[str]:
    if not text:
        return []
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    raw = _SENT_END.split(text)
    out: List[str] = []
    for s in raw:
        s = s.strip()
        s = re.sub(r"^\s*[-•*\d.]+\s*", "", s)
        if len(s) < 12:
            continue
        if len(s) > 400:
            # 超长句按逗号再切
            out.extend(p.strip() for p in re.split(r"(?<=[,，、])\s*", s) if len(p.strip()) >= 12)
        else:
            out.append(s)
    return out


def tokenize(text: str) -> List[str]:
    low = (text or "").lower()
    tokens = [w.lower() for w in _EN_WORD.findall(low)]
    tokens += _CJK_WORD.findall(low)
    return [t for t in tokens if t not in STOPWORDS and len(t) > 1]


def has_cjk(text: str) -> bool:
    return bool(_CJK.search(text or ""))


# --------------------------------------------------------------------------- #
# 关键词 / 主题
# --------------------------------------------------------------------------- #
def key_terms(texts: Sequence[str] | str, top_k: int = 15) -> List[Tuple[str, float]]:
    if isinstance(texts, str):
        texts = [texts]
    docs = [set(tokenize(t)) for t in texts]
    doc_freq: Counter = Counter()
    for d in docs:
        doc_freq.update(d)
    n_docs = max(1, len(docs))

    all_tokens: Counter = Counter()
    for t in texts:
        all_tokens.update(tokenize(t))
    total = sum(all_tokens.values()) or 1

    scored: List[Tuple[str, float]] = []
    for term, tf in all_tokens.items():
        if len(term) < 3 and not _CJK.search(term):
            continue
        idf = math.log((1 + n_docs) / (1 + doc_freq.get(term, 0))) + 1.0
        scored.append((term, (tf / total) * idf))
    scored.sort(key=lambda kv: (-kv[1], kv[0]))

    # 去重：避免 "accuracy" 与 "accuracies" 同时出现
    picked: List[Tuple[str, float]] = []
    for term, score in scored:
        if any(term in p or p in term for p, _ in picked):
            continue
        picked.append((term, score))
        if len(picked) >= top_k:
            break
    return picked


# --------------------------------------------------------------------------- #
# 抽取式摘要（TextRank 的轻量替代：TF-IDF 句打分 + 位置先验）
# --------------------------------------------------------------------------- #
def rank_sentences(sentences: Sequence[str]) -> List[Tuple[str, float]]:
    if not sentences:
        return []
    tokens_per = [set(tokenize(s)) for s in sentences]
    doc_freq: Counter = Counter()
    for t in tokens_per:
        doc_freq.update(t)
    n_docs = max(1, len(sentences))

    all_tokens: Counter = Counter()
    for s in sentences:
        all_tokens.update(tokenize(s))
    total = sum(all_tokens.values()) or 1

    scores: List[Tuple[str, float]] = []
    for i, sent in enumerate(sentences):
        toks = tokenize(sent)
        if not toks:
            continue
        score = 0.0
        for t in toks:
            idf = math.log((1 + n_docs) / (1 + doc_freq.get(t, 0))) + 1.0
            score += (all_tokens[t] / total) * idf
        score /= math.sqrt(len(toks))
        # 位置先验：越靠前越重要
        score *= 1.15 ** max(0, (5 - i)) if i < 5 else 1.0
        # 长度惩罚：过短或过长都降权
        L = len(sent)
        if L < 30:
            score *= 0.7
        elif L > 260:
            score *= 0.85
        scores.append((sent, score))
    return scores


def summarize(text: str, n: int = 5) -> List[str]:
    sents = split_sentences(text)
    ranked = sorted(rank_sentences(sents), key=lambda kv: -kv[1])[: max(1, n)]
    # 输出保持原文顺序，读起来更顺
    order = {s: i for i, s in enumerate(sents)}
    ranked.sort(key=lambda kv: order.get(kv[0], 0))
    return [s for s, _ in ranked]


def condense(sentence: str, max_len: int = 110) -> str:
    """把长句压缩成适合做幻灯片要点的短句。"""
    s = sentence.strip().rstrip(".。;；,，")
    if len(s) <= max_len:
        return s
    # 优先在逗号处断开
    parts = re.split(r"(?<=[,，、;；])\s*", s)
    out = ""
    for p in parts:
        if len(out) + len(p) > max_len and out:
            break
        out += p
    return (out or s[:max_len]).rstrip(" ,，、;；") + "…"


def dedupe_keep_order(items: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for it in items:
        k = re.sub(r"\W+", "", it.lower())
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def title_case_heading(text: str) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return t[:80] if len(t) > 80 else t


def word_count(text: str) -> int:
    if has_cjk(text):
        return len(_CJK.findall(text)) + len(_EN_WORD.findall(text))
    return len(_EN_WORD.findall(text))
