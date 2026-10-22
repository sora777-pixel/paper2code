# 领域模板 demo（监督回归）

由 paper2code 在**没有可用 LLM** 时按领域模板生成。它提供该领域的骨架
（数据生成器 / 训练或模拟循环 / 指标 / 曲线输出），**不是论文方法的实现**：
论文特有的部分在 `run_demo.py` 的 `run_one()` 里以 `TODO(论文特有)` 标出。

数据来源：synthetic: 合成数据（非论文数值）

```bash
python run_demo.py              # smoke，默认 3 个种子，写 results/metrics.json
python run_demo.py --seeds 5    # 改种子数
```
