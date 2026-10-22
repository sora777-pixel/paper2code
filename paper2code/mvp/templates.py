"""领域模板库（roadmap M3-①）。

**它解决什么**：离线脚手架此前只会「回放论文表格」，既不是方法、也产不出论文那类曲线。
模板库提供**领域骨架**——数据生成器、训练/模拟循环、指标与曲线输出——让「实现论文那个方法」
从「从零写」变成「填论文特有的那一小块」，从而降低幻觉与失败率。

**诚实边界（务必保留）**：
* 离线模式填不出「论文特有的部分」，因此骨架里凡属论文专有的地方都留 ``TODO(论文特有)`` 标记，
  并且 ``metrics.json.meta`` 里显式写出模板名与数据来源（论文表 / 合成）；
* 模板**不会**声称自己复现了论文数值。用论文自带表格数据时，结果可对标；用合成数据时只反映定性趋势。

模板判定是**确定性**的（关键词加权打分），不依赖 LLM；覆盖不到时回落到原「表格回放」脚手架。
生成的 demo 只用 stdlib，保证任何环境都能跑（依赖选择见 roadmap M3-⑤）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import numutil
from ..models import Paper
from ..reproduce import dataset
from . import scaffold

# --------------------------------------------------------------------------- #
# 模板注册表
# --------------------------------------------------------------------------- #
#: 每个模板：名称、中文标签、匹配关键词。命中越多得分越高；同分按注册顺序。
TEMPLATES: List[Dict[str, Any]] = [
    {
        "name": "systems_cache",
        "label": "系统类（缓存/队列模拟）",
        "hints": ("cache", "hit rate", "命中率", "cdn", "queue", "latency", "throughput",
                  "workload", "request", "byte", "缓存", "队列", "带宽"),
    },
    {
        "name": "supervised_regression",
        "label": "监督回归",
        "hints": ("regression", "rmse", "mae", "r2", "predict", "binding free energy", "affinity",
                  "kcal/mol", "δg", "dg", "score", "回归", "预测"),
    },
    {
        "name": "supervised_classification",
        "label": "监督分类 / 聚类",
        "hints": ("classification", "accuracy", "nmi", "ari", "f1", "precision", "recall",
                  "cluster", "topic", "分类", "聚类", "准确率"),
    },
    {
        "name": "transformer_block",
        "label": "Transformer block",
        "hints": ("transformer", "self-attention", "attention", "bert", "gpt", "token",
                  "multi-head", "注意力"),
    },
    {
        "name": "gnn",
        "label": "图神经网络",
        "hints": ("gnn", "graph neural", "message passing", "node classification", "link prediction",
                  "图神经", "节点分类"),
    },
    {
        "name": "rl_toy_env",
        "label": "RL 玩具环境",
        "hints": ("reinforcement", "q-learning", "policy gradient", "agent", "reward", "mdp",
                  "bandit", "强化学习", "策略"),
    },
    {
        "name": "optimization",
        "label": "优化 / 流式算法",
        "hints": ("top-k", "streaming", "optimization", "greedy", "scheduling", "knapsack",
                  "heap", "throughput", "加速", "流式", "贪心"),
    },
]

#: 生成 demo 的默认种子数（M3-④ 多种子与区间要求 ≥3）
DEFAULT_SEEDS = 3


def template_by_name(name: str) -> Optional[Dict[str, Any]]:
    """按名字取模板（先内置，再插件注册的；见 ``paper2code.plugins``）。"""
    for t in TEMPLATES:
        if t["name"] == name:
            return t
    try:
        from .. import plugins

        tpl = plugins._EXTRA_TEMPLATES.get(name)
        return dict(tpl) if tpl else None
    except Exception:
        return None


def all_templates() -> List[Dict[str, Any]]:
    """内置模板 + 插件模板（M5-④）。插件只贡献「关键词 + 标签」，不改判定语义。"""
    try:
        from .. import plugins

        return plugins.list_templates()
    except Exception:
        return [dict(t) for t in TEMPLATES]


# --------------------------------------------------------------------------- #
# 确定性匹配
# --------------------------------------------------------------------------- #
def _haystack(paper: Paper) -> str:
    parts: List[str] = [(paper.title or "") * 3, (paper.abstract or "") * 2]
    parts += [s.heading for s in paper.sections]
    parts += [s.text[:400] for s in paper.sections[:40]]
    parts += [t.caption or "" for t in paper.tables]
    return re.sub(r"\s+", " ", " ".join(parts)).lower()


def score_templates(paper: Paper) -> List[Tuple[str, int]]:
    """返回 ``[(模板名, 命中数)]``，按分数降序（同分保持注册顺序）。"""
    hay = _haystack(paper)
    scored = [(t["name"], sum(1 for h in t["hints"] if h in hay)) for t in all_templates()]
    return sorted(scored, key=lambda kv: -kv[1])


def select_template(paper: Paper) -> Optional[Dict[str, Any]]:
    """选出得分最高的模板；全部 0 分则返回 ``None``（回落到表格回放脚手架）。"""
    ranked = score_templates(paper)
    if not ranked or ranked[0][1] <= 0:
        return None
    return template_by_name(ranked[0][0])


# --------------------------------------------------------------------------- #
# 输入数据：优先论文自带数值列，其次标注清楚的合成数据
# --------------------------------------------------------------------------- #
_TARGET_HINTS = ("dg", "δg", "energy", "free energy", "affinity", "score", "value",
                 "target", "y", "rmse", "accuracy", "命中率", "能量", "打分")
_SHARE_HINTS = ("share", "percent", "%", "ratio", "fraction", "份额", "占比", "百分比")


def _numeric_table(paper: Paper, column_hints: Tuple[str, ...] = ()) -> Optional[Tuple[Any, List[str], int]]:
    """找一张「数值列最多、且列名命中 hints（若给了）」的表。"""
    best: Optional[Tuple[Any, List[str], int]] = None
    for t in paper.tables:
        cols = [c for c in dataset.numeric_columns(t) if c]
        if not cols:
            continue
        if column_hints:
            hit = sum(1 for c in cols if any(h in str(c).lower() for h in column_hints))
            if not hit:
                continue
        recs = dataset.table_to_records(t)
        if not recs:
            continue
        if best is None or len(cols) > len(best[1]):
            best = (t, cols, len(recs))
    return best


def _columns_payload(table: Any, cols: List[str], limit: int) -> Dict[str, List[float]]:
    recs = dataset.table_to_records(table)[:limit]
    out: Dict[str, List[float]] = {}
    for c in cols:
        vals = [numutil.to_number(r.get(c)) for r in recs]
        clean = [v for v in vals if v is not None]
        if len(clean) >= 2:
            out[str(c)] = [v for v in vals if v is not None]
    return out


def _labels_of(table: Any, limit: int) -> List[str]:
    recs = dataset.table_to_records(table)[:limit]
    if not recs:
        return []
    first = list(recs[0].keys())[0]
    return [str(r.get(first, "")).strip() or "row%d" % (i + 1) for i, r in enumerate(recs)]


def inputs_payload(paper: Paper, template: Dict[str, Any]) -> Dict[str, Any]:
    """按模板准备 ``data/inputs.json``。"""
    name = template["name"]
    limit = 40
    payload: Dict[str, Any] = {"template": name, "provenance": "synthetic: 合成数据（非论文数值）"}

    if name == "supervised_regression":
        picked = _numeric_table(paper, _TARGET_HINTS) or _numeric_table(paper)
        if picked:
            table, cols, _n = picked
            data = _columns_payload(table, cols, limit)
            keyed = sorted(data, key=lambda c: (any(h in c.lower() for h in _TARGET_HINTS), len(data[c])))
            if keyed:
                target = keyed[-1]
                feats = {c: data[c] for c in keyed[:-1]}
                if not feats:
                    # 只有一列数值时，仍**用论文的真实数字**当目标，以行号作特征；
                    # 这比退回合成数据忠实，但只能看趋势，故显式标注。
                    feats = {"row_index": [float(i) for i in range(len(data[target]))]}
                    payload["single_column"] = True
                payload["features"] = feats
                payload["target"] = data[target]
                payload["target_name"] = target
                payload["provenance"] = "论文表 %s：%s" % (table.id, (table.caption or "")[:80])

    elif name == "supervised_classification":
        picked = _numeric_table(paper)
        if picked:
            table, cols, _n = picked
            data = _columns_payload(table, cols, limit)
            if len(data) >= 2:
                payload["features"] = data
                payload["labels"] = _labels_of(table, limit)
                payload["provenance"] = "论文表 %s：%s" % (table.id, (table.caption or "")[:80])

    elif name == "systems_cache":
        picked = _numeric_table(paper, _SHARE_HINTS) or _numeric_table(paper)
        if picked:
            table, cols, _n = picked
            data = _columns_payload(table, cols, limit)
            if data:
                payload["shares"] = data
                payload["labels"] = _labels_of(table, limit)
                payload["provenance"] = "论文表 %s：%s" % (table.id, (table.caption or "")[:80])

    return payload


def write_inputs(paper: Paper, template: Dict[str, Any], demo_dir: Path) -> Dict[str, Any]:
    demo_dir = Path(demo_dir)
    (demo_dir / "data").mkdir(parents=True, exist_ok=True)
    payload = inputs_payload(paper, template)
    (demo_dir / "data" / "inputs.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


# --------------------------------------------------------------------------- #
# demo 代码生成
# --------------------------------------------------------------------------- #
_COMMON = '''
# ---- 通用小工具（stdlib 实现，保证任何环境都能跑） ---- #
def _summarize(values):
    nums = [v for v in values if v is not None]
    if not nums:
        return {"n": 0}
    return {"n": len(nums), "mean": sum(nums) / len(nums), "min": min(nums), "max": max(nums)}


def _tables_block(tables):
    """回放论文自带表格（与离线脚手架同一 shape）。

    性质断言（roadmap M1-④）就挂在 ``tables.<id>.columns.<col>.values`` 上，
    所以模板 demo 必须继续输出这一段，否则断言会静默退化成「无此键」。
    """
    out = {}
    for t in tables:
        cols = {}
        for name, values in (t.get("columns") or {}).items():
            cols[name] = {"labels": t.get("labels") or [], "values": values,
                          "summary": _summarize(values)}
        out[t["id"]] = {"caption": t.get("caption") or "", "columns": cols}
    return out


def _fmean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _corr(xs, ys):
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0
    mx, my = _fmean(xs[:n]), _fmean(ys[:n])
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    dx = math.sqrt(sum((xs[i] - mx) ** 2 for i in range(n)))
    dy = math.sqrt(sum((ys[i] - my) ** 2 for i in range(n)))
    return num / (dx * dy) if dx and dy else 0.0


def _linfit(xs, ys):
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0, _fmean(ys)
    mx, my = _fmean(xs[:n]), _fmean(ys[:n])
    den = sum((xs[i] - mx) ** 2 for i in range(n))
    slope = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / den if den else 0.0
    return slope, my - slope * mx


def _column(payload, key):
    vals = payload.get(key) or []
    return [float(v) for v in vals if v is not None]


def _best_feature(features, target):
    best, best_r = None, -1.0
    for name, vals in features.items():
        if len(vals) < 3 or len(vals) != len(target):
            continue
        r = abs(_corr([float(v) for v in vals], [float(v) for v in target]))
        if r > best_r:
            best, best_r = name, r
    return best, max(best_r, 0.0)
'''


_BODIES: Dict[str, str] = {}

_BODIES["supervised_regression"] = '''
def run_one(inputs, seed):
    """监督回归骨架：单变量最小二乘 + 留出法评估。

    TODO(论文特有)：把这里的单变量线性拟合换成论文的模型（如 super learner /
    梯度提升 / 神经网络），并把特征工程换成论文描述的那一套。
    """
    feats = inputs.get("features") or {}
    target = _column(inputs, "target")
    synthetic = len(target) < 3
    if synthetic:
        # 没有论文数值可对标时才退化为带标注的合成回归任务
        rnd = random.Random(seed)
        target = [3.0 + 0.5 * i + rnd.gauss(0, 0.4) for i in range(40)]
        feats = {"x": [float(i) for i in range(40)]}
    name, r = _best_feature(feats, target)
    if name is None:  # 没有可用特征列：用序号当特征
        name, feats = "row_index", {"row_index": [float(i) for i in range(len(target))]}
    xs = [float(v) for v in feats[name]][:len(target)]
    ys = target[:len(xs)]
    split = max(1, int(len(xs) * 0.8))
    slope, intercept = _linfit(xs[:split], ys[:split])
    pred = [slope * x + intercept for x in xs[split:]]
    obs = ys[split:]
    errs = [p - o for p, o in zip(pred, obs)]
    rmse = math.sqrt(_fmean([e * e for e in errs])) if errs else 0.0
    mae = _fmean([abs(e) for e in errs]) if errs else 0.0
    var = _fmean([(o - _fmean(obs)) ** 2 for o in obs]) if obs else 0.0
    return {"metrics": {"rmse": rmse, "mae": mae, "r2": (1.0 - (rmse * rmse) / var) if var else 0.0,
                        "corr": r, "n_train": float(split), "n_test": float(len(obs)),
                        "single_column": 1.0 if inputs.get("single_column") else 0.0,
                        "synthetic_input": 1.0 if synthetic else 0.0},
            "series": {"pred": pred, "obs": obs}}
'''

_BODIES["supervised_classification"] = '''
def _kmeans(rows, k, seed, iters=12):
    rnd = random.Random(seed)
    cents = [list(rnd.choice(rows)) for _ in range(min(k, len(rows)))]
    assign = [0] * len(rows)
    for _ in range(iters):
        for i, r in enumerate(rows):
            best, bd = 0, None
            for c, cen in enumerate(cents):
                d = sum((r[j] - cen[j]) ** 2 for j in range(len(r)))
                if bd is None or d < bd:
                    best, bd = c, d
            assign[i] = best
        for c in range(len(cents)):
            members = [rows[i] for i in range(len(rows)) if assign[i] == c]
            if members:
                cents[c] = [sum(m[j] for m in members) / len(members) for j in range(len(cents[c]))]
    inertia = sum(sum((rows[i][j] - cents[assign[i]][j]) ** 2 for j in range(len(rows[i])))
                  for i in range(len(rows)))
    return assign, inertia


def run_one(inputs, seed):
    """监督分类 / 聚类骨架：k-means + 轮廓系数。

    TODO(论文特有)：换成论文的聚类/分类器（如锚点约束 NMF、BERTopic、XGBoost），
    并把 k 与预处理换成论文给定的设置。
    """
    feats = inputs.get("features") or {}
    names = [n for n, v in feats.items() if len(v) >= 4]
    if len(names) < 2:
        rnd = random.Random(seed)
        centers = [(0.0, 0.0), (4.0, 4.0), (8.0, 0.0)]
        synth = [[c[0] + rnd.gauss(0, 0.6), c[1] + rnd.gauss(0, 0.6)]
                 for c in centers for _ in range(14)]
        rows = synth
        synthetic = True
    else:
        n = min(len(feats[c]) for c in names)
        rows = [[float(feats[c][i]) for c in names] for i in range(n)]
        synthetic = False
    k = 3 if len(rows) >= 6 else max(2, len(rows) // 2)
    assign, inertia = _kmeans(rows, k, seed)
    # 轮廓系数（frame 内欧氏距离，stdlib 版）
    sil = []
    for i, r in enumerate(rows):
        own = [sum((r[j] - rows[m][j]) ** 2 for j in range(len(r))) ** 0.5
               for m in range(len(rows)) if assign[m] == assign[i] and m != i]
        other = {}
        for m in range(len(rows)):
            if assign[m] != assign[i]:
                other.setdefault(assign[m], []).append(
                    sum((r[j] - rows[m][j]) ** 2 for j in range(len(r))) ** 0.5)
        if not own or not other:
            continue
        a = _fmean(own)
        b = min(_fmean(v) for v in other.values())
        sil.append((b - a) / max(a, b) if max(a, b) else 0.0)
    return {"metrics": {"inertia": inertia, "silhouette": _fmean(sil) if sil else 0.0,
                        "n_samples": float(len(rows)), "k": float(k),
                        "synthetic_input": 1.0 if synthetic else 0.0},
            "series": {"assign": [float(v) for v in assign],
                       "x": [r[0] for r in rows], "y": [r[1] if len(r) > 1 else 0.0 for r in rows]}}
'''

_BODIES["systems_cache"] = '''
def _lru_hits(stream, capacity):
    cache, hits = [], 0
    for page in stream:
        if page in cache:
            hits += 1
            cache.remove(page)
        cache.append(page)
        if len(cache) > capacity:
            cache.pop(0)
    return hits / len(stream) if stream else 0.0


def run_one(inputs, seed):
    """系统类骨架：请求份额分解 + LRU 缓存命中率模拟。

    TODO(论文特有)：把请求流换成论文的负载模型（如实测 trace / Zipf 参数）、
    把替换策略换成论文评估的那一种（LRU / LFU / GDS）。
    """
    rnd = random.Random(seed)
    shares = inputs.get("shares") or {}
    first = next(iter(shares.values()), []) if shares else []
    if len(first) >= 2:
        total = sum(abs(float(v)) for v in first) or 1.0
        top_share = max(abs(float(v)) for v in first) / total
        synthetic = False
        source_n = float(len(first))
    else:
        first = [7.1, 12.0, 20.0, 60.9]
        total = sum(first)
        top_share = max(first) / total
        synthetic = True
        source_n = 0.0
    # Zipf 请求流（α=1.1）：类别越靠前越热
    n_items, n_req = 60, 4000
    weights = [1.0 / ((i + 1) ** 1.1) for i in range(n_items)]
    ztot = sum(weights)
    stream = []
    for _ in range(n_req):
        u = rnd.random() * ztot
        acc = 0.0
        for i, w in enumerate(weights):
            acc += w
            if u <= acc:
                stream.append(i)
                break
    sweep = [2, 4, 8, 16, 32, 64]
    hits = [_lru_hits(stream, c) for c in sweep]
    return {"metrics": {"top_class_share": top_share, "hit_rate_c16": _lru_hits(stream, 16),
                        "hit_rate_c64": _lru_hits(stream, 64),
                        "categories": float(len(first) if not synthetic else 4),
                        "source_points": source_n,
                        "synthetic_input": 1.0 if synthetic else 0.0},
            "series": {"capacity": [float(c) for c in sweep], "hit_rate": hits}}
'''

_BODIES["transformer_block"] = '''
def run_one(inputs, seed):
    """Transformer 骨架：单头自注意力前向（不训练）。

    TODO(论文特有)：补上论文的层数/头数/位置编码与训练循环；本骨架只验证
    注意力的形状与数值稳定性，用来替代「无法复现」。
    """
    rnd = random.Random(seed)
    n, d = 16, 8
    emb = [[rnd.gauss(0, 0.5) for _ in range(d)] for _ in range(n)]
    wq = [[rnd.gauss(0, 0.3) for _ in range(d)] for _ in range(d)]
    wk = [[rnd.gauss(0, 0.3) for _ in range(d)] for _ in range(d)]
    wv = [[rnd.gauss(0, 0.3) for _ in range(d)] for _ in range(d)]

    def proj(w, x):
        return [sum(w[a][b] * x[b] for b in range(d)) for a in range(d)]

    q = [proj(wq, e) for e in emb]
    k = [proj(wk, e) for e in emb]
    v = [proj(wv, e) for e in emb]
    scale = math.sqrt(d)
    ent = []
    out = []
    row_max = []
    for i in range(n):
        logits = [sum(q[i][b] * k[j][b] for b in range(d)) / scale for j in range(n)]
        mx = max(logits)
        exps = [math.exp(x - mx) for x in logits]
        tot = sum(exps)
        att = [e / tot for e in exps]
        ent.append(-sum(p * math.log(p) for p in att if p > 0))
        row_max.append(max(att))
        out.append([sum(att[j] * v[j][b] for j in range(n)) for b in range(d)])
    norm = _fmean([math.sqrt(sum(x * x for x in row)) for row in out])
    return {"metrics": {"seq_len": float(n), "dim": float(d), "params": float(4 * d * d),
                        "attn_entropy": _fmean(ent), "out_norm": norm,
                        "max_attn": max(row_max)},
            "series": {"entropy": ent}}
'''

_BODIES["gnn"] = '''
def run_one(inputs, seed):
    """GNN 骨架：两层消息传递 + 标签传播式节点分类。

    TODO(论文特有)：换成论文的聚合函数（GCN / GAT / GraphSAGE）、层数与特征；
    本骨架用图结构验证消息传递的方向与规模。
    """
    rnd = random.Random(seed)
    n = 40
    edges = []
    for i in range(n):
        for _ in range(2):
            j = rnd.randrange(n)
            if j != i:
                edges.append((i, j))
    adj = {i: [] for i in range(n)}
    for a, b in edges:
        adj[a].append(b)
    labels = {}
    for i in range(0, n, 4):  # 每 4 个节点给一个已知标签
        labels[i] = i % 3
    feat = {i: [rnd.gauss(0, 1.0), rnd.gauss(0, 1.0)] for i in range(n)}
    # 消息传递：邻居特征均值 + 自身（2 层）
    for _ in range(2):
        newf = {}
        for i in range(n):
            acc = list(feat[i])
            for j in adj[i]:
                acc[0] += feat[j][0]
                acc[1] += feat[j][1]
            cnt = 1 + len(adj[i])
            newf[i] = [acc[0] / cnt, acc[1] / cnt]
        feat = newf
    # 用已知标签的质心做最近质心分类
    cents = {}
    for i, lab in labels.items():
        cents.setdefault(lab, []).append(feat[i])
    proto = {lab: [_fmean([p[0] for p in pts]), _fmean([p[1] for p in pts])] for lab, pts in cents.items()}
    correct = 0
    for i in range(n):
        best, bd = None, None
        for lab, cen in proto.items():
            d = (feat[i][0] - cen[0]) ** 2 + (feat[i][1] - cen[1]) ** 2
            if bd is None or d < bd:
                best, bd = lab, d
        if i in labels and best == labels[i]:
            correct += 1
    deg = _fmean([len(adj[i]) for i in range(n)])
    return {"metrics": {"nodes": float(n), "edges": float(len(edges) / 2), "mean_degree": deg,
                        "labeled": float(len(labels)),
                        "label_agreement": correct / max(1, len(labels))},
            "series": {"degree": [float(len(adj[i])) for i in range(n)]}}
'''

_BODIES["rl_toy_env"] = '''
def run_one(inputs, seed):
    """RL 骨架：4x4 网格世界上的表格 Q-learning。

    TODO(论文特有)：换成论文的环境、状态表示与算法（如 PPO / DQN / bandit），
    本骨架验证「奖励随回合上升」这一可检验性质。
    """
    rnd = random.Random(seed)
    n, goal = 4, (3, 3)
    actions = [(0, 1), (0, -1), (1, 0), (-1, 0)]  # 右 左 下 上
    q = {(x, y): {a: 0.0 for a in range(4)} for x in range(n) for y in range(n)}
    alpha, gamma, eps = 0.2, 0.95, 0.2
    ep_returns, ep_steps = [], []
    for ep in range(60):
        s = (0, 0)
        total, steps = 0.0, 0
        for _ in range(60):
            if rnd.random() < eps:
                a = rnd.randrange(4)
            else:
                a = max(q[s], key=lambda k: q[s][k])
            dx, dy = actions[a]
            ns = (min(n - 1, max(0, s[0] + dx)), min(n - 1, max(0, s[1] + dy)))
            r = 10.0 if ns == goal else -0.1
            q[s][a] += alpha * (r + gamma * max(q[ns].values()) - q[s][a])
            s, total, steps = ns, total + r, steps + 1
            if s == goal:
                break
        ep_returns.append(total)
        ep_steps.append(float(steps))
    tail = ep_returns[-10:]
    return {"metrics": {"mean_return_last10": _fmean(tail), "mean_steps_last10": _fmean(ep_steps[-10:]),
                        "episodes": float(len(ep_returns)),
                        "states": float(n * n)},
            "series": {"episode": [float(i) for i in range(len(ep_returns))],
                       "return": ep_returns}}
'''

_BODIES["optimization"] = '''
def run_one(inputs, seed):
    """优化 / 流式算法骨架：单遍 Top-K 选择 vs 全量排序基线。

    TODO(论文特有)：换成论文的算法（例如流式 Top-K 的快速路径与对抗性分数流），
    并保留「正确性一致 + 加速比」这两条可检验性质。
    """
    rnd = random.Random(seed)
    n, k = 20000, 20
    scores = [rnd.gauss(0, 1.0) if (i % 7) else 5.0 + rnd.gauss(0, 0.1) for i in range(n)]
    # 流式：单遍维护长度为 k 的有序小顶序列（stdlib：用插入式定长表代替堆）
    top = []
    for s in scores:
        if len(top) < k:
            top.append(s)
            top.sort(reverse=True)
        elif s > top[-1]:
            top[-1] = s
            top.sort(reverse=True)
    baseline = sorted(scores, reverse=True)[:k]
    agree = _fmean([1.0 if abs(a - b) < 1e-9 else 0.0 for a, b in zip(top, baseline)])
    # 三种规模下比较「单遍代价」与「全量排序代价」（n log n）的相对比较次数
    ks = [1000, 5000, 20000]
    ratio = [(m * math.log(m, 2)) / m for m in ks]  # 归一化到「每次输入」
    return {"metrics": {"k": float(k), "n": float(n), "topk_agreement": agree,
                        "speedup_est": math.log(n, 2)},
            "series": {"n": [float(m) for m in ks], "cost_ratio": ratio}}
'''


def _render(template: Dict[str, Any], provenance: str, seeds: int) -> str:
    body = _BODIES.get(template["name"])
    if not body:
        raise KeyError("未知模板：%s" % template["name"])
    src = _RUNNER.replace("__NAME__", template["name"]).replace("__LABEL__", template["label"])
    src = src.replace("__PROVENANCE__", provenance).replace("__COMMON__", _COMMON)
    src = src.replace("__BODY__", body).replace("__DEFAULT_SEEDS__", str(seeds))
    return src


_RUNNER = '''"""领域模板 demo（roadmap M3-①，由 paper2code.mvp.templates 生成）。

模板：__NAME__（__LABEL__）
数据来源：__PROVENANCE__

**这是领域骨架，不是论文方法的实现**：骨架提供数据生成器、训练/模拟循环、指标与曲线
输出，论文特有的部分在 `run_one` 里标了 `TODO(论文特有)`。不带参数运行 = smoke；
结果写入 `results/metrics.json`（含多种子的均值与区间）。
"""
import argparse
import json
import math
import random
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARK = "__P2C_RESULT__"

#: 95% 双侧 t 临界值（demo 只用 stdlib，所以小表内联，不引 scipy）
_T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306,
        9: 2.262, 10: 2.228, 12: 2.179, 15: 2.131, 20: 2.086, 30: 2.042, 60: 2.0, 100: 1.984}


def _t95(df):
    """t 临界值；df<=0（单次运行）返回 None —— 不给区间。"""
    if df <= 0:
        return None
    if df in _T95:
        return _T95[df]
    ks = sorted(_T95)
    if df > ks[-1]:
        return 1.96
    lo = max(k for k in ks if k < df)
    hi = min(k for k in ks if k > df)
    f = (df - lo) / (hi - lo)
    return _T95[hi] * f + _T95[lo] * (1 - f)

__COMMON__

__BODY__


def _aggregate(runs):
    """多种子聚合（roadmap M3-④ / M6-②）：均值 + 区间。

    单次随机波动不该被当成结论，所以默认跑多个种子，并同时报告 stdev 与 **95% t 区间**
    （n=1 时不给区间——一次运行估不出波动，不假装有区间）。
    """
    keys = sorted({k for r in runs for k in r.get("metrics", {})})
    out = {}
    for k in keys:
        vals = [r["metrics"][k] for r in runs if k in r.get("metrics", {})]
        if not vals:
            continue
        n = len(vals)
        mean = statistics.fmean(vals)
        sd = statistics.stdev(vals) if n > 1 else 0.0
        sem = sd / math.sqrt(n) if n > 1 else 0.0
        t = _t95(n - 1)
        half = (t * sem) if (t is not None and n > 1) else None
        out[k] = {"n": n, "mean": mean, "stdev": sd, "sem": sem,
                  "ci_low": (mean - half) if half is not None else None,
                  "ci_high": (mean + half) if half is not None else None,
                  "ci_confidence": 0.95,
                  "min": min(vals), "max": max(vals)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=__DEFAULT_SEEDS__)
    ap.add_argument("--out", default="results")
    a = ap.parse_args()
    inputs = json.loads((HERE / "data" / "inputs.json").read_text(encoding="utf-8"))
    tables_path = HERE / "data" / "tables.json"
    tables = json.loads(tables_path.read_text(encoding="utf-8")) if tables_path.exists() else []
    n_seeds = max(1, int(a.seeds))
    runs = [run_one(inputs, s) for s in range(n_seeds)]
    metrics = {"meta": {"kind": "template:__NAME__", "label": "__LABEL__",
                        "data_provenance": inputs.get("provenance", "unknown"),
                        "seeds": n_seeds,
                        "note": "领域骨架 demo；论文特有部分见 run_one 的 TODO 标记"},
               "metrics": _aggregate(runs), "series": runs[0].get("series", {}),
               "tables": _tables_block(tables)}
    res = Path(a.out)
    res.mkdir(parents=True, exist_ok=True)
    (res / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
    print("MVP_OK")


if __name__ == "__main__":
    main()
'''

_README = """# 领域模板 demo（{label}）

由 paper2code 在**没有可用 LLM** 时按领域模板生成。它提供该领域的骨架
（数据生成器 / 训练或模拟循环 / 指标 / 曲线输出），**不是论文方法的实现**：
论文特有的部分在 `run_demo.py` 的 `run_one()` 里以 `TODO(论文特有)` 标出。

数据来源：{provenance}

```bash
python run_demo.py              # smoke，默认 {seeds} 个种子，写 results/metrics.json
python run_demo.py --seeds 5    # 改种子数
```
"""


def build_template_demo(paper: Paper, template: Dict[str, Any], demo_dir: Path,
                        seeds: int = DEFAULT_SEEDS) -> Dict[str, Any]:
    """写出模板 demo 到 ``demo_dir``；返回 ``{files, warnings, payload}``。"""
    demo_dir = Path(demo_dir)
    demo_dir.mkdir(parents=True, exist_ok=True)
    payload = write_inputs(paper, template, demo_dir)
    # 同时回放论文自带表格：M1-④ 的性质断言挂在 tables.* 上，模板路径也必须输出
    table_payload = scaffold._tables_payload(paper)
    (demo_dir / "data" / "tables.json").write_text(
        json.dumps(table_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    provenance = str(payload.get("provenance") or "unknown")
    (demo_dir / "run_demo.py").write_text(_render(template, provenance, seeds), encoding="utf-8")
    (demo_dir / "README.md").write_text(
        _README.format(label=template["label"], provenance=provenance, seeds=seeds), encoding="utf-8")

    warnings: List[str] = []
    if provenance.startswith("synthetic"):
        warnings.append(
            "模板 %s 未能在论文里找到可用数值列，demo 使用**合成数据**（只反映定性趋势，不是论文数值）。"
            % template["name"])
    warnings.append(
        "模板 %s 是领域骨架，其中的论文特有部分（见 run_one 的 TODO）尚未实现；"
        "配置 LLM 后可在此骨架上填充以获得忠实的 MVP。" % template["name"])
    files = sorted(str(p.relative_to(demo_dir)).replace("\\", "/")
                   for p in demo_dir.rglob("*") if p.is_file())
    return {"files": files, "warnings": warnings, "payload": payload}


def template_plan(paper: Paper, template: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    """模板路径的 plan（供 M1-③ 计划自检与 required_outputs 校验）。"""
    provenance = str(payload.get("provenance") or "unknown")
    return {
        "framework": "stdlib",
        "core_idea_to_implement": "领域模板 %s：%s" % (template["name"], template["label"]),
        "faithful_parts": [
            "领域骨架（数据生成 / 训练或模拟循环 / 指标 / 曲线输出）由模板确定性生成",
            "多种子聚合后报告均值与区间，不把单次随机波动当结论",
        ],
        "simplified_parts": [
            {"what": "论文方法本身", "why": "离线模式无法理解并实现论文专有的模型与超参",
             "how": "骨架里以 TODO(论文特有) 标出待填位置"},
            {"what": "数据来源", "why": provenance,
             "how": "优先用论文自带数值列；找不到时用合成数据并显式标注"},
        ],
        "toy_data": {"description": provenance, "generator": "data/inputs.json"},
        "demo_files": [{"path": "run_demo.py", "purpose": "领域骨架 + 多种子聚合"},
                       {"path": "data/inputs.json", "purpose": "输入数据（论文表或合成）"},
                       {"path": "data/tables.json", "purpose": "论文自带表格回放（供性质断言）"}],
        "required_outputs": ["meta.kind", "metrics", "tables"],
        "acceptance_checks": ["metrics.json 可解析且无 NaN",
                              "meta.kind 以 template: 开头",
                              "metrics 每项含 n/mean/stdev/min/max",
                              "tables.* 保留论文原始数值，供性质断言"],
        "runtime_budget_s": 30,
        "dependencies": [],
    }
