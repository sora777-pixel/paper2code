"""领域模板 demo（roadmap M3-①，由 paper2code.mvp.templates 生成）。

模板：supervised_regression（监督回归）
数据来源：synthetic: 合成数据（非论文数值）

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
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--out", default="results")
    a = ap.parse_args()
    inputs = json.loads((HERE / "data" / "inputs.json").read_text(encoding="utf-8"))
    tables_path = HERE / "data" / "tables.json"
    tables = json.loads(tables_path.read_text(encoding="utf-8")) if tables_path.exists() else []
    n_seeds = max(1, int(a.seeds))
    runs = [run_one(inputs, s) for s in range(n_seeds)]
    metrics = {"meta": {"kind": "template:supervised_regression", "label": "监督回归",
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
