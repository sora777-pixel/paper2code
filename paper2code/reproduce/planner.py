"""复现计划：把「要复现什么」和「怎么复现」显式化。

计划是纯数据（可序列化为 ``repro_plan.json``），执行器只消费计划，
因此后续接入更大算力时——比如把某个步骤从「本地函数调用」替换为
「提交到集群」——只需换执行器，不必改计划生成逻辑。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import get_settings
from ..models import MODE_NO_CODE, MODE_OPEN_SOURCE, MODE_PSEUDOCODE, Paper, ReproPlan
from . import classifier

# --------------------------------------------------------------------------- #
_MODE_STEPS: Dict[str, List[str]] = {
    MODE_OPEN_SOURCE: [
        "定位代码仓库（论文包 code/ 目录优先，否则用正文链接浅克隆）",
        "静态扫描仓库：入口脚本、依赖声明、数据文件、已有结果文件",
        "生成隔离环境复现脚本 repro_run.sh（锁定依赖 + 固定随机种子）",
        "在单机沙箱内执行入口脚本，采集 results/*.csv 等产物",
        "把仓库产出与论文表格逐格对齐，计算相对误差并给出 pass/warn/fail",
        "重绘论文中的图表，供人工目视比对",
    ],
    MODE_PSEUDOCODE: [
        "抽取论文中的算法块，确认输入/输出定义",
        "伪代码 → Python 翻译（离线规则翻译器或在线大模型）",
        "按论文描述的实验规模合成小规模输入数据（上限 1 万条）",
        "在本地沙箱执行，回收输出",
        "对输出做算法性质校验（有序性 / Top-K 正确性 / 归一化守恒等）",
        "明确标注：本情形只能做到「逻辑复现」，不对标论文指标",
    ],
    MODE_NO_CODE: [
        "抽取论文中的全部表格与附带数据文件，整理为 tidy CSV",
        "对论文数据做内部一致性校验（占比求和、汇总行、最优性声明、排名合法性）",
        "以正文描述为线索重绘论文中的图表，做风格复刻",
        "输出「无法复现要素清单」：缺失的数据集、超参、随机种子、预处理细节",
    ],
}

_MODE_STRATEGY = {
    MODE_OPEN_SOURCE: (
        "论文附带开源代码，复现的第一原则是**复用而非重写**：以仓库自带的脚本与数据为准，"
        "我们只在外部做环境隔离、种子固定、产物采集与逐格对标。判据是数字级一致"
        "（默认相对误差 ≤5% 记为 pass）。失败通常来自依赖版本漂移或未固定的随机性。"
    ),
    MODE_PSEUDOCODE: (
        "论文只给了伪代码，说明作者有意披露算法骨架但未提供工程实现。"
        "此时复现的目标是**逻辑等价**而非指标对标：先把伪代码翻译为可执行 Python，"
        "再用小规模合成输入验证其数学性质（有序性、Top-K 正确性、归一化守恒等）。"
        "报告必须显式声明这一局限，避免把「逻辑跑通」误读为「结果复现」。"
    ),
    MODE_NO_CODE: (
        "论文既无代码也无伪代码，可复现的上限是**数据自洽性与可视化复刻**。"
        "我们以论文表格与附带数据为唯一事实来源，重建 tidy 数据、校验其内部一致性"
        "（占比求和、汇总行、最优性声明），并重绘图表。数据集、超参、预处理等"
        "未披露要素会被列成清单，供人工补齐后二次复现。"
    ),
}


def build_plan(
    paper: Paper,
    decision: Optional[classifier.ModeDecision] = None,
    inputs: Optional[Dict[str, Any]] = None,
    provider: Any = None,
) -> ReproPlan:
    settings = get_settings()
    decision = decision or classifier.classify(paper)
    mode = decision.mode

    targets: List[Dict[str, Any]] = []
    for t in paper.tables:
        if not t.rows:
            continue
        targets.append(
            {
                "kind": "table",
                "id": t.id,
                "caption": (t.caption or "")[:160],
                "n_rows": t.n_rows,
                "n_cols": t.n_cols,
                "source": t.source,
                "role": "对标基准" if mode == MODE_OPEN_SOURCE else "复现输入与校验对象",
            }
        )
    for f in paper.figures:
        if f.kind == "unknown" and not f.caption:
            continue
        targets.append(
            {
                "kind": "figure",
                "id": f.id,
                "caption": (f.caption or "")[:160],
                "figure_kind": f.kind,
                "role": "图表重绘" if f.kind == "plot" else "讲解引用",
            }
        )
    for a in paper.algorithms:
        targets.append(
            {
                "kind": "algorithm",
                "id": a.id,
                "caption": (a.caption or "")[:160],
                "n_lines": len(a.lines),
                "role": "翻译为可执行代码" if mode == MODE_PSEUDOCODE else "讲解引用",
            }
        )

    strategy = _MODE_STRATEGY[mode]
    if provider is not None:
        try:
            extra = provider.repro_strategy(paper, mode, targets)
            if extra:
                strategy = strategy + "\n\n【模型补充】" + extra.strip()
        except Exception:
            pass

    warnings: List[str] = []
    if decision.confidence < 0.8:
        warnings.append(
            f"情形判定置信度较低（{decision.confidence:.2f}），建议人工确认；"
            f"判定依据：{decision.reason}"
        )
    if not paper.tables:
        warnings.append("论文中未抽取到任何表格，复现目标将退化为「算法/图表」层面。")
    if mode == MODE_NO_CODE and not paper.tables:
        warnings.append("既无代码也无表格数据，本次复现只能产出「复现方案」而不能产出结果。")

    return ReproPlan(
        paper_id=paper.id,
        mode=mode,
        mode_reason=decision.reason,
        strategy=strategy,
        targets=targets,
        inputs=inputs or {},
        steps=_MODE_STEPS[mode],
        scale_guard={
            **settings.scale_guard(),
            "rationale": "单机 MVP：全部计算在本地完成，单次执行数据量上限由 max_records 控制",
        },
        scale_notes=[
            f"本次复现的数据规模上限：{settings.max_records} 条记录",
            f"单次执行超时：{settings.exec_timeout}s",
            "架构预留：执行器（reproduce.runner）与计划（reproduce.planner）解耦，"
            "后续可替换为容器/集群后端而无需改动用例定义",
        ],
        warnings=warnings,
    )
