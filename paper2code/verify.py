"""声明级核验报告（roadmap M6-①）。

**目标**：把「这篇论文复现到什么程度」从一句主观评语，变成**逐条主张 + 证据 + 状态**的可核对清单。

状态四档（与路线图一致）：

| 状态 | 含义 | 判据 |
|---|---|---|
| `已复现` | 有**独立**证据支持该主张 | demo/仓库实跑产出的数值落在论文值的 95% CI 内（或相对偏差 ≤ 容差）；性质断言 pass |
| `部分复现` | 方向对、量级不对，或只有定性证据 | 相对偏差在 warn 区间；或只有“重绘图/定性趋势”这类证据 |
| `不可复现` | 有证据**反对**该主张，或证据不可得且原因明确 | 相对偏差超限；性质断言 fail；图表标注 `not_reproducible`；仓库 smoke 失败 |
| `未检验` | 没有产出可用证据 | 只回放了论文自带数字（**没有独立验证**）、缺指标键、演示脚本未执行 |

**最关键的一条纪律**：demo 若只是「回放论文表格」（离线脚手架 / from_paper_data），
它产出的数字**就是论文自己的数字**，因此对「数值主张」一律记 **未检验**——
这不是失败，而是「我们没验证过」。把回放当复现是最容易骗过自己的地方。

主张来源（离线可用的确定性来源优先）：

* **表格**：极值主张（「表 T 的 Acc 列最高是 C」）、占比/计数结构主张；
* **公式**：性质主张（归一化/非负/求和）——复用 M3-③；
* **图**：结论主张（图注文字）+ 可复现性标注；
* **仓库**：情形 A 的「仓库在这台机器上能跑通吗」；
* **LLM（可选）**：从正文抽取的量化主张（离线时跳过并如实标注）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import numutil, stats

REPRODUCED, PARTIAL, NOT_REPRODUCIBLE, UNTESTED = "reproduced", "partial", "not_reproducible", "untested"
STATUS_ZH = {REPRODUCED: "已复现", PARTIAL: "部分复现", NOT_REPRODUCIBLE: "不可复现", UNTESTED: "未检验"}
STATUS_ORDER = [REPRODUCED, PARTIAL, NOT_REPRODUCIBLE, UNTESTED]
#: 证据强度（用于汇总时取「最强/最弱」）
_SEVERITY = {REPRODUCED: 0, PARTIAL: 1, NOT_REPRODUCIBLE: 2, UNTESTED: 3}


@dataclass
class Claim:
    """一条待核验主张。"""

    id: str
    text: str
    source: str = "table"          # table | formula | figure | repo | llm
    kind: str = "value"            # value | property | figure | repo | structural
    metric: str = ""              # demo 指标键或表格列（value 类）
    paper_value: Optional[float] = None
    table_id: str = ""
    ref: str = ""                  # 表/图/公式编号
    spec_id: str = ""              # 图对应的绘图说明 id（如 fig_f1）
    quote: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class Verdict:
    claim: Claim
    status: str = UNTESTED
    evidence: str = ""
    reason: str = ""
    strength: str = ""            # independent | echo | qualitative | none

    def to_dict(self) -> Dict[str, Any]:
        return {"claim": self.claim.to_dict(), "status": self.status,
                "status_zh": STATUS_ZH.get(self.status, self.status),
                "evidence": self.evidence, "reason": self.reason, "strength": self.strength}


# --------------------------------------------------------------------------- #
# 主张抽取（确定性，离线可用）
# --------------------------------------------------------------------------- #
def _numeric_columns(table: Any) -> Dict[str, List[Tuple[str, float]]]:
    """``{列名: [(行标签, 数值), ...]}``（只保留能转成数字的格）。"""
    out: Dict[str, List[Tuple[str, float]]] = {}
    rows = getattr(table, "rows", []) or []
    if not rows:
        return out
    for ci, col in enumerate((getattr(table, "header", []) or [])[1:], start=1):
        pairs: List[Tuple[str, float]] = []
        for row in rows:
            if ci >= len(row):
                continue
            val = numutil.to_number(row[ci])
            if val is not None:
                pairs.append((str(row[0])[:24] or "row%d" % (len(pairs) + 1), float(val)))
        if len(pairs) >= 2:
            out[str(col)] = pairs
    return out


def table_claims(paper: Any, per_table: int = 2) -> List[Claim]:
    """从每张表派生**极值主张**（「哪一行最高/最低」）——论文里最常见的量化结论形态。"""
    out: List[Claim] = []
    for t in getattr(paper, "tables", []) or []:
        cols = _numeric_columns(t)
        made = 0
        for col, pairs in cols.items():
            if made >= per_table:
                break
            hi = max(pairs, key=lambda kv: kv[1])
            lo = min(pairs, key=lambda kv: kv[1])
            key = "tables.%s.columns.%s.values" % (getattr(t, "id", "?"), col)
            out.append(Claim(
                id="table:%s:%s:max" % (getattr(t, "id", "?"), col),
                text="表 %s 的「%s」列最高的是 %s（%s）" % (getattr(t, "id", "?"), col, hi[0], _fmt(hi[1])),
                source="table", kind="value", metric=key, paper_value=hi[1],
                table_id=str(getattr(t, "id", "")), ref=str(getattr(t, "id", "")),
                quote="%s … %s" % (col, hi[0])))
            made += 1
            if made >= per_table:
                break
            if lo[0] != hi[0]:
                out.append(Claim(
                    id="table:%s:%s:min" % (getattr(t, "id", "?"), col),
                    text="表 %s 的「%s」列最低的是 %s（%s）" % (getattr(t, "id", "?"), col, lo[0], _fmt(lo[1])),
                    source="table", kind="value", metric=key, paper_value=lo[1],
                    table_id=str(getattr(t, "id", "")), ref=str(getattr(t, "id", "")),
                    quote="%s … %s" % (col, lo[0])))
                made += 1
    return out


def figure_claims(paper: Any, specs: Optional[Sequence[Dict[str, Any]]] = None) -> List[Claim]:
    """图主张：图注就是论文的定性结论；状态由该图的 `reproducibility` 决定。"""
    out: List[Claim] = []
    by_id = {str(s.get("id", "")).lower(): s for s in (specs or [])}
    for f in getattr(paper, "figures", []) or []:
        fid = str(getattr(f, "id", "?"))
        caption = (getattr(f, "caption", "") or "").strip()
        if not caption:
            continue
        # demo 的 spec id 统一是 ``fig_<图id>``，大小写与标点都要归一后才对得上
        spec = by_id.get(("fig_" + fid).lower()) or by_id.get(_slug(fid).lower()) or {}
        out.append(Claim(id="figure:%s" % fid, text="图 %s 的结论：%s" % (fid, caption[:160]),
                         source="figure", kind="figure", ref=fid,
                         spec_id=str(spec.get("id") or ""),
                         metric=str(spec.get("reproducibility") or ""),
                         quote=caption[:200]))
    return out


def formula_claims(paper: Any, known_keys: Sequence[str] = ()) -> List[Claim]:
    """公式性质主张（复用 M3-③ 的机械化判定）。"""
    from .mvp import claims as mvp_claims

    out: List[Claim] = []
    for c in mvp_claims.formula_claims(paper, known_keys):
        out.append(Claim(id="formula:%s" % (c.get("desc") or len(out)),
                         text=c.get("desc") or "公式性质", source="formula", kind="property",
                         metric=str(c.get("key") or ""), quote=str(c.get("quote") or ""),
                         ref=str(c.get("key") or "")))
    return out


def repo_claim(paper: Any, smoke: Optional[Dict[str, Any]]) -> Optional[Claim]:
    """情形 A：「这篇论文的仓库能在这台机器上跑起来吗」。"""
    if not getattr(paper, "code_refs", None) and not smoke:
        return None
    url = ""
    for r in getattr(paper, "code_refs", []) or []:
        if getattr(r, "kind", "repo") == "repo":
            url = str(getattr(r, "url", ""))
            break
    return Claim(id="repo:smoke", text="论文仓库 %s 的入口能在这台机器上跑通（缩减规模）" % (url or "(未给出链接)"),
                 source="repo", kind="repo", ref=url, quote=(smoke or {}).get("command", ""))


def structural_claims(paper: Any) -> List[Claim]:
    """结构主张：论文声明存在的表/图/算法确实被抽取出来了（这是「读得对不对」的底线）。"""
    out: List[Claim] = []
    if getattr(paper, "tables", None):
        out.append(Claim(id="struct:tables", text="论文中声明的 %d 张表被正确抽出" % len(paper.tables),
                         source="table", kind="structural", ref="tables"))
    if getattr(paper, "figures", None):
        out.append(Claim(id="struct:figures", text="论文中声明的 %d 张图被正确抽出并配对图注" % len(paper.figures),
                         source="figure", kind="structural", ref="figures"))
    if getattr(paper, "algorithms", None):
        out.append(Claim(id="struct:algorithms", text="论文中的 %d 段伪代码被正确抽取" % len(paper.algorithms),
                         source="table", kind="structural", ref="algorithms"))
    return out


def collect_claims(paper: Any, evidence: Dict[str, Any], llm_claims: Optional[Sequence[Dict[str, Any]]] = None) -> List[Claim]:
    """汇总所有主张来源（确定性来源在前；LLM 可选）。"""
    out: List[Claim] = []
    out += structural_claims(paper)
    out += table_claims(paper)
    out += figure_claims(paper, evidence.get("figure_specs"))
    out += formula_claims(paper, evidence.get("metric_keys") or [])
    rc = repo_claim(paper, evidence.get("repo_smoke"))
    if rc:
        out.append(rc)
    for c in llm_claims or ():
        try:
            out.append(Claim(id="llm:%s" % len(out), text=str(c.get("text") or c.get("desc") or "")[:200],
                             source="llm", kind="value", metric=str(c.get("key") or ""),
                             paper_value=(float(c["value"]) if isinstance(c.get("value"), (int, float)) else None),
                             quote=str(c.get("quote") or "")[:200]))
        except Exception:
            continue
    return [c for c in out if c.text]


# --------------------------------------------------------------------------- #
# 判定
# --------------------------------------------------------------------------- #
def _fmt(v: Optional[float]) -> str:
    if v is None:
        return "-"
    return "%.6g" % v


def _text(v: Any) -> str:
    """把任意值安全地变成字符串：**dict/list 也要能渲染**。

    踩过的坑：情形 A 的 smoke ``command`` 是 list（``["train.py", "--epochs", "1"]``），
    以前直接当 ``reason`` 塞进 verdict，渲染 Markdown 时 ``.replace`` 抛 AttributeError，
    整份核验报告生成失败——而且**只有「带 repo smoke 的用例」才会中招**，很难发现。
    """
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, (list, tuple)):
        return " ".join(_text(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _independent_metrics(claim: Claim, evidence: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """能作为**独立证据**的指标块；返回 None 表示「没有独立产出」。

    两种「不独立」必须挡住：

    * 整个 demo 就是回放（``echo_of_paper``，离线脚手架一类）；
    * 该主张指向 ``tables.*`` —— 那是 demo 把**论文自己的表格**抄了一遍，
      拿它跟论文数值对比必然「通过」，属于自证，不构成验证。
    """
    if not evidence.get("demo_metrics") or evidence.get("echo_of_paper"):
        return None
    if str(claim.metric or "").startswith("tables."):
        return None
    return evidence["demo_metrics"]


def assess(claim: Claim, evidence: Dict[str, Any]) -> Verdict:
    """按主张类型判定状态。**默认未检验**，只有拿到证据才升级。"""
    if claim.kind == "structural":
        n = {"tables": len(getattr_from(evidence, "counts", "tables")),
             "figures": len(getattr_from(evidence, "counts", "figures")),
             "algorithms": len(getattr_from(evidence, "counts", "algorithms"))}.get(claim.ref, 0)
        if n:
            return Verdict(claim, REPRODUCED, "解析层实测抽出 %d 项" % n, "论文声明的结构确实存在", "independent")
        return Verdict(claim, NOT_REPRODUCIBLE, "解析层没抽出该项", "论文声明与实际抽取不一致", "independent")

    if claim.kind == "repo":
        smoke = evidence.get("repo_smoke") or {}
        st = str(smoke.get("status") or "")
        if st == "ran":
            return Verdict(claim, REPRODUCED, "smoke status=ran（缩减规模跑通）",
                           _text(smoke.get("command"))[:200], "independent")
        if st in ("blocked", "blocked_deps", "blocked_entry", "blocked_env", "failed"):
            return Verdict(claim, NOT_REPRODUCIBLE, "smoke status=%s" % st,
                           _text(smoke.get("reason"))[:300], "independent")
        return Verdict(claim, UNTESTED, "没有 smoke 运行记录", "情形判定未走到 smoke 运行", "none")

    if claim.kind == "figure":
        specs = {str(s.get("id")): s for s in (evidence.get("figure_specs") or [])}
        # 先按收集阶段定下来的 spec id 精确取；取不到再按「Figure 1 / F1」这类写法归一后匹配
        spec = specs.get(claim.spec_id) or next(
            (s for s in specs.values() if _same_ref(str(s.get("paper_ref") or ""), claim.ref)), None)
        drawn = evidence.get("drawn_figures") or {}
        repro = str((spec or {}).get("reproducibility") or claim.metric)
        if repro == "not_reproducible":
            why = (spec or {}).get("demo_change_needed") or "需要真实数据集/大规模实验"
            return Verdict(claim, NOT_REPRODUCIBLE, "图标注为不可复现", str(why)[:300], "qualitative")
        if repro in ("from_demo", "schematic", "from_paper_data"):
            fid = str((spec or {}).get("id") or claim.spec_id or "")
            files = drawn.get(fid) or [f for k, v in drawn.items() if k.lower() == fid.lower() for f in v]
            if files:
                strength = "independent" if repro == "from_demo" else "qualitative"
                note = {"from_demo": "用 demo 产物重绘，只支持定性趋势（玩具数据）",
                        "schematic": "按论文结构重绘示意图（非数值）",
                        "from_paper_data": "用论文自带数据重绘（数值即论文数字，不构成独立验证）"}[repro]
                st = PARTIAL if repro != "from_paper_data" else UNTESTED
                return Verdict(claim, st, "已出图：%s" % "、".join(Path(f).name for f in files[:3]), note, strength)
            return Verdict(claim, UNTESTED, "spec 说可复现但本次没有出图产物",
                           "绘图脚本未执行或全部失败", "none")
        return Verdict(claim, UNTESTED, "该图没有可判定的绘图说明",
                       "本次没有产生该图的 spec（或 spec 缺少 reproducibility）", "none")

    if claim.kind == "property":
        cl = evidence.get("demo_claims") or {}
        for r in cl.get("results") or []:
            c = r.get("claim") or {}
            if claim.metric and c.get("key") != claim.metric:
                continue
            if r.get("status") == "pass":
                return Verdict(claim, REPRODUCED, "性质断言 pass", str(r.get("detail") or "")[:200], "independent")
            if r.get("status") == "fail":
                return Verdict(claim, NOT_REPRODUCIBLE, "性质断言 fail", str(r.get("detail") or "")[:200], "independent")
            return Verdict(claim, UNTESTED, "性质断言 skip", str(r.get("detail") or "")[:200], "none")
        return Verdict(claim, UNTESTED, "没有对应的性质断言", "该公式未接入 demo", "none")

    # ---- value：核心判据 -------------------------------------------------- #
    # ① 最强的证据：情形 A/C 的**实跑对标**（复现产物 vs 论文表格逐格）
    hit = _matching_delta(claim, evidence)
    if hit is not None:
        st = {"pass": REPRODUCED, "warn": PARTIAL, "fail": NOT_REPRODUCIBLE}.get(str(hit.get("status")), UNTESTED)
        return Verdict(claim, st, "实跑对标：%s" % str(hit.get("detail") or "")[:200],
                       "对标范围 %s" % str(hit.get("scope") or ""), "independent")

    # ② 独立 demo 指标（非「回放论文数字」）
    metrics = _independent_metrics(claim, evidence)
    if metrics is None:
        why = ("demo 只是回放论文自带数值（from_paper_data）：产出的数字就是论文的数字，"
               "**不构成独立验证**" if evidence.get("echo_of_paper")
               else "该主张对应的是「回放论文表格」的数值，没有独立产出")
        return Verdict(claim, UNTESTED, "无独立数值证据", why, "echo")

    iv = _interval_for(claim, metrics, evidence)
    if iv is None or iv.n == 0:
        return Verdict(claim, UNTESTED, "demo 指标里没有键 `%s`" % claim.metric,
                       "该主张没有对应的数值产出", "none")
    verdict = stats.classify_vs_paper(iv, claim.paper_value, *evidence.get("thresholds", (0.05, 0.20)))
    note = str((evidence.get("independence_note") or ""))
    return Verdict(claim,
                   {**{"pass": REPRODUCED, "warn": PARTIAL, "fail": NOT_REPRODUCIBLE}, "skip": UNTESTED}[verdict["status"]],
                   iv.label() + " ｜ " + verdict["detail"], note, "independent")


def _matching_delta(claim: Claim, evidence: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """该主张是否能被某个「逐格对标」结果直接支持。

    对标结果（``CellDelta``）来自情形 A/C 的**实跑**，是本项目里强度最高的证据：
    ``scope`` 形如 ``T1:方法C/Acc``，``col``/``table_id`` 与主张的表/列对应即算命中。
    """
    if claim.source != "table" or not claim.table_id:
        return None
    col = claim.metric.split(".")[-2] if claim.metric.count(".") >= 2 else ""
    for d in evidence.get("cell_deltas") or []:
        if str(d.get("table_id") or "") != claim.table_id:
            continue
        if d.get("status") not in ("pass", "warn", "fail"):
            continue
        scope = str(d.get("scope") or "")
        if not col or col in scope or col.lower() in scope.lower():
            # 极值主张：对标的是**该行**的格子；行标签写在 scope 里，主张文本里含行标签则更精确
            if claim.text and scope and scope.split(":")[-1].split("/")[0].strip() not in claim.text:
                continue
            return d
    return None


def getattr_from(evidence: Dict[str, Any], key: str, sub: str) -> List[Any]:
    counts = evidence.get(key) or {}
    v = counts.get(sub)
    return list(v) if isinstance(v, (list, tuple)) else ([v] if v else [])


def _interval_for(claim: Claim, metrics: Dict[str, Any], evidence: Dict[str, Any]) -> Optional["stats.Interval"]:
    """取该主张对应的观测区间：demo 的多种子聚合 → 区间；单值 → n=1（无区间）。"""
    from .mvp.textio import get_path

    try:
        v = get_path(metrics, claim.metric)
    except (KeyError, IndexError, ValueError):
        v = None
    if v is None and claim.table_id:
        v = (metrics.get("tables") or {}).get(claim.table_id)
        if isinstance(v, dict):
            v = ((v.get("columns") or {}).get(claim.metric.split(".")[-2:][0]) or {}).get("values")
    if isinstance(v, dict) and "mean" in v:
        return stats.values_from_aggregate(v)      # 模板 demo 的多种子聚合
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return stats.Interval(n=1, mean=float(v), values=[float(v)])
    if claim.table_id:
        pairs = (metrics.get("tables") or {}).get(claim.table_id)
        if isinstance(pairs, dict):
            for col, block in (pairs.get("columns") or {}).items():
                if claim.metric.endswith(str(col)):
                    vals = [float(x) for x in (block.get("values") or []) if isinstance(x, (int, float))]
                    # 极值主张对的是**极值**本身，不是均值：用该极值作为单点观测（n=1）
                    if vals:
                        target = claim.paper_value
                        cand = max(vals, key=lambda x: abs(x - (target if target is not None else 0)))
                        return stats.Interval(n=1, mean=cand, values=vals)
    if isinstance(v, list):
        vals = [float(x) for x in v if isinstance(x, (int, float))]
        if vals:
            return stats.Interval(n=1, mean=vals[max(range(len(vals)), key=lambda i: abs(vals[i]))], values=vals)
    return None


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
def build(paper: Any, evidence: Dict[str, Any], llm_claims: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """生成核验报告数据（claims + verdicts + 汇总）。"""
    claims = collect_claims(paper, evidence, llm_claims=llm_claims)
    verdicts = [assess(c, evidence) for c in claims]
    return {"paper_id": getattr(paper, "id", ""), "title": getattr(paper, "title", ""),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "evidence": {k: evidence.get(k) for k in
                         ("demo_generator", "echo_of_paper", "repo_smoke", "n_figure_specs", "seeds")},
            "verdicts": [v.to_dict() for v in verdicts],
            "summary": summarize([v.to_dict() for v in verdicts])}


def summarize(verdicts: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    counts = {s: 0 for s in STATUS_ORDER}
    for v in verdicts:
        counts[v.get("status", UNTESTED)] = counts.get(v.get("status", UNTESTED), 0) + 1
    judged = counts[REPRODUCED] + counts[PARTIAL] + counts[NOT_REPRODUCIBLE]
    n = len(verdicts)
    return {"n_claims": n, **{("n_" + k): v for k, v in counts.items()},
            "n_judged": judged,
            "reproduced_rate": round(counts[REPRODUCED] / judged, 4) if judged else None,
            "tested_rate": round(judged / n, 4) if n else None,
            "verdict_line": "已复现 %d ｜ 部分 %d ｜ 不可复现 %d ｜ 未检验 %d（共 %d 条主张）" % (
                counts[REPRODUCED], counts[PARTIAL], counts[NOT_REPRODUCIBLE], counts[UNTESTED], n)}


def render_markdown(report: Dict[str, Any]) -> str:
    s = report.get("summary") or {}
    L = ["# 声明级核验报告 · %s\n" % (report.get("title") or report.get("paper_id")),
         "> 生成时间：%s ｜ 论文 `%s`\n" % (report.get("generated_at"), report.get("paper_id")),
         "**结论先行**：%s\n" % (s.get("verdict_line") or ""),
         "> 四档含义：**已复现**=有独立证据支持；**部分复现**=方向对量级不对或只有定性证据；"
         "**不可复现**=有证据反对或原因明确；**未检验**=没有产出可用证据"
         "（**回放论文自带数字不算验证**）。\n",
         "## 逐条主张\n",
         "| 状态 | 主张 | 证据 | 说明 |", "|---|---|---|---|"]
    ev = report.get("evidence") or {}
    for v in report.get("verdicts") or []:
        c = v.get("claim") or {}
        L.append("| **%s** | %s | %s | %s |" % (
            v.get("status_zh", v.get("status")), _text(c.get("text")).replace("|", "/"),
            _text(v.get("evidence") or "-").replace("|", "/"),
            _text(v.get("reason")).replace("|", "/")[:200] or "-"))
    L += ["", "## 证据来源与强度\n",
          "- 生成方式：`%s`%s" % (ev.get("demo_generator") or "-",
                                  "（**回放式**：数值来自论文自身，不构成独立验证）" if ev.get("echo_of_paper") else ""),
          "- 多种子：%s" % (ev.get("seeds") or "未记录"),
          "- 仓库 smoke：%s" % ((ev.get("repo_smoke") or {}).get("status") or "未尝试"),
          "- 图表说明：%s 条" % (ev.get("n_figure_specs") or 0)]
    return "\n".join(L) + "\n"


def write_report(report: Dict[str, Any], out_dir: Path | str) -> Dict[str, str]:
    """把报告写成 ``VERIFICATION.md`` + ``verification.json``。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md = out / "VERIFICATION.md"
    js = out / "verification.json"
    md.write_text(render_markdown(report), encoding="utf-8")
    js.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"markdown": str(md), "json": str(js)}


def _slug(text: str) -> str:
    import re

    return "fig_" + re.sub(r"\W+", "_", str(text or "").lower()).strip("_")


def _same_ref(paper_ref: str, ref: str) -> bool:
    """``"Figure 1"`` 与 ``"F1"`` 这类「同一个图的两种写法」要能对上。"""
    import re

    def norm(s: str) -> str:
        s = str(s or "").lower()
        s = re.sub(r"\b(figure|fig|table)\b", "", s)
        return re.sub(r"\W+", "", s)

    a, b = norm(paper_ref), norm(ref)
    return bool(a) and (a == b or a.endswith(b) or b.endswith(a))
