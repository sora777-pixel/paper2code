"""情形 A 的 library-mode smoke（由 paper2code 生成）。

入口脚本跑不动时（实测 PBEE 的 `pbee.py` 需要 pyrosetta——非 PyPI 且要 Rosetta
许可），改为**直接调用仓库自带的训练 API**，在一份缩规模输入上确认它的 ML 核心
能在这个环境里跑起来。

**这是代码路径 smoke，不是论文数值复现**：输入是合成小样本，真实特征仍需论文/仓库
各自的预处理（PBEE 需要 pyrosetta 计算 Rosetta 描述符）。
"""
import importlib
import json
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARK = "__P2C_RESULT__"


def _tiny_dataset(n=120, d=4, seed=0):
    """确定性小样本：y 是 x 的线性组合 + 噪声（可校验拟合质量）。"""
    import random
    rnd = random.Random(seed)
    xs, ys = [], []
    for _ in range(n):
        row = [rnd.gauss(0, 1) for _ in range(d)]
        ys.append(1.5 * row[0] - 0.7 * row[1] + 0.3 * row[2] + rnd.gauss(0, 0.05))
        xs.append(row)
    return xs, ys


def _default_models():
    """仓库自带的 train 函数通常接收一个 estimator 列表（如 PBEE 的 train_base_models）。

    优先用 sklearn；**没有 sklearn 时退化为内置的 stdlib estimator**，这样 library
    smoke 在任何环境都能跑（「离线永远可用」是本项目的硬约束），只是拟合能力弱一些。
    """
    try:
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.linear_model import LinearRegression, Ridge
        from sklearn.tree import DecisionTreeRegressor
        return [LinearRegression(), Ridge(alpha=1.0), DecisionTreeRegressor(random_state=0),
                RandomForestRegressor(n_estimators=8, random_state=0)]
    except Exception:
        return [_MeanModel(), _LinModel(0), _LinModel(1)]


class _MeanModel:
    """stdlib 兜底：常数均值预测。"""

    def fit(self, x, y):
        self.mean = (sum(y) / len(y)) if len(y) else 0.0
        return self

    def predict(self, x):
        return [self.mean for _ in range(len(x))]


class _LinModel:
    """stdlib 兜底：拿第 ``col`` 列做最小二乘。"""

    def __init__(self, col=0):
        self.col = col

    def fit(self, x, y):
        pairs = [(float(row[self.col]), float(t)) for row, t in zip(x, y)]
        n = len(pairs)
        if n < 2:
            self.a, self.b = 0.0, (sum(t for _, t in pairs) / n if n else 0.0)
            return self
        mx = sum(p for p, _ in pairs) / n
        my = sum(t for _, t in pairs) / n
        den = sum((p - mx) ** 2 for p, _ in pairs)
        self.a = (sum((p - mx) * (t - my) for p, t in pairs) / den) if den else 0.0
        self.b = my - self.a * mx
        return self

    def predict(self, x):
        return [self.a * float(row[self.col]) + self.b for row in x]


def _rmse(pred, obs):
    return (sum((p - o) ** 2 for p, o in zip(pred, obs)) / max(1, len(obs))) ** 0.5


def main():
    cfg = json.loads((HERE / "smoke_config.json").read_text(encoding="utf-8"))
    api = cfg.get("library") or {}
    repo = str(cfg.get("cwd") or ".")
    sys.path.insert(0, repo)
    out = {"status": "failed", "mode": "library", "metrics": {},
           "note": "代码路径 smoke（合成输入），不是论文数值复现"}
    try:
        import numpy as np
        mod = importlib.import_module(api["module"])
        train = getattr(mod, api["train"])
        predict = getattr(mod, api.get("predict") or "", None) if api.get("predict") else None

        seed = int((cfg.get("env") or {}).get("P2C_SEED") or 0)
        xs, ys = _tiny_dataset(seed=seed)
        X, y = np.array(xs, dtype=float), np.array(ys, dtype=float)
        split = int(len(X) * 0.8)
        Xtr, ytr, Xte, yte = X[:split], y[:split], X[split:], y[split:]

        if len(api.get("train_params") or []) >= 3:
            base = train(Xtr, ytr, _default_models())          # 仓库自己的训练函数
        else:
            base = train(Xtr, ytr)
        out["trained_models"] = len(base) if isinstance(base, (list, tuple, dict)) else 1

        metrics = {"n_train": float(split), "n_test": float(len(Xte)),
                   "trained_models": float(out["trained_models"])}
        if predict is not None and isinstance(base, list) and len(api.get("predict_params") or []) == 3:
            try:
                from sklearn.linear_model import LinearRegression
                meta = LinearRegression().fit(np.column_stack([m.predict(Xtr) for _, m in base]), ytr)
                meta_kind = "sklearn.LinearRegression"
            except Exception:                       # 没有 sklearn：用内置均值模型当元模型
                meta = _MeanModel().fit(None, list(np.asarray(
                    [m.predict(Xtr) for _, m in base]).mean(axis=0)))
                meta_kind = "stdlib.mean"
            metrics["meta_model"] = 1.0 if meta_kind.startswith("sklearn") else 0.0
            predictions = predict(Xte, base, meta)   # 仓库自己的预测函数
            if isinstance(predictions, dict) and "SuperLearner" in predictions:
                pred = np.asarray(predictions["SuperLearner"], dtype=float).ravel()
                metrics["rmse_superlearner"] = _rmse(list(pred[:len(yte)]), list(yte))
                # 只留可 JSON 化的预览：ndarray 直接进 json.dumps 会抛 TypeError
                metrics["pred_preview"] = [float(x) for x in pred[:3]]
        out["status"] = "ran"
        out["metrics"] = metrics
    except Exception as exc:
        out["reason"] = "%s: %s" % (type(exc).__name__, exc)
        out["traceback"] = traceback.format_exc()[-1500:]
    # 序列化本身也可能失败（实测 ndarray 漏进来会抛 TypeError 并让结果文件消失），
    # 所以这一步必须自带兜底：宁可写成「序列化失败」，也不能什么都留不下。
    try:
        (HERE / "result.json").write_text(
            json.dumps(out, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
    except Exception as exc:
        (HERE / "result.json").write_text(json.dumps(
            {"status": "failed", "mode": "library",
             "reason": "结果序列化失败：%s: %s" % (type(exc).__name__, exc)},
            ensure_ascii=False, indent=2), encoding="utf-8")
    print("SMOKE_DONE")


if __name__ == "__main__":
    main()
