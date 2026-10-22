# MVP 自动生成报告

**论文**：Estimating Absolute Protein−Protein Binding Free Energies by a Super Learner Model（`Estimating-Absolute-Protein-Protein-Binding-Free-Energi-5e8992ab`）

## 一眼看结论

- **生成方式**：offline-template:supervised_regression (llm demo failed)
- **运行结果**：smoke 运行 **通过**，共尝试 4 次（ok）
- **图表**：共 6 张说明，其中 0 张已出图；其余均写明了无法复现的原因

## 这个 demo 实现了什么

领域模板 supervised_regression：监督回归

**忠实于论文的部分**：领域骨架（数据生成 / 训练或模拟循环 / 指标 / 曲线输出）由模板确定性生成；多种子聚合后报告均值与区间，不把单次随机波动当结论

## 简化说明（信任任何数字之前先读这里）

- **论文方法本身** —— 离线模式无法理解并实现论文专有的模型与超参（做法：骨架里以 TODO(论文特有) 标出待填位置）
- **数据来源** —— synthetic: 合成数据（非论文数值）（做法：优先用论文自带数值列；找不到时用合成数据并显式标注）

## 计划自检（写码前对照论文核对计划是否诚实）

- **判定**：**ok**（诚实） ｜ 方式：确定性规则（离线）
- **发现的问题**：
  - `low` **论文有 7 张图，但 required_outputs 里没有可绘图的数据键** —— demo 不会产出曲线/消融/扫参这类键，论文的图无法被定性复现（建议：按图清单补 required_outputs（如 curves.<name>.{x,y}、ablation.{names,values}））

## 性质断言（从论文的「可检验声明」生成；fail 即判本 demo 失败）

- 本次没有可求值的断言。

## 成本护栏（每篇论文的硬上限）

- **用量**：LLM 调用 11 次 · prompt 68086 + completion 149683 = **217769 tokens** · 本次耗时 507.4s
- **上限**：调用 60 次 / 1000000 tokens / 1800.0 秒
- **结论**：未触发（未超限）

## 执行沙箱（这段代码跑在哪、隔离到什么程度）

- **执行器**：`local` ｜ **隔离级别**：低隔离（本机进程，无隔离边界） ｜ **风险**：高
- **网络**：无网络（清空代理环境变量）
- 说明：配置为 local：本机进程执行，无隔离边界

## 版本与改动（「改一次」的历史）

- **当前版本**：v1 ｜ 生成方式：`offline-template:supervised_regression (llm demo failed)` ｜ 本次指令：（首次生成） ｜ 更新时间：2026-10-22T19:49:44

> 每一版都留在 `mvp/versions/vN/`；diff 是文本级的，二进制产物只列增删/大小变化。

## 导出（Colab / Streamlit）

- **自包含 notebook**：`export/mvp_demo.ipynb`（37 个单元格；demo 与绘图脚本都写在 notebook 里，可整段运行）
- **Streamlit 小演示**：`export/streamlit_app.py`（只读已跑出来的产物，不重新执行代码）
- 用法与「一键 Colab」的说明见 `export/README.md`

## 怎么自己跑

```bash
cd demo
python run_demo.py          # 无参数 = smoke，写出 results/metrics.json
cd ../figures
python run_figures.py      # 重新绘制所有图（需要 matplotlib；不需要 LLM）
```

## 图表清单

| 图 | 论文位置 | 数据来源 | 状态 | 产物 |
|---|---|---|---|---|
| fig_f1 | Figure 1 | 无法复现 | 仅有说明（未出图） | — |
| fig_f2 | Figure 2 | 无法复现 | 仅有说明（未出图） | — |
| fig_f3 | Figure 3 | 无法复现 | 仅有说明（未出图） | — |
| fig_f4 | Figure 4 | 无法复现 | 仅有说明（未出图） | — |
| fig_f5 | Figure 5 | 无法复现 | 仅有说明（未出图） | — |
| fig_f6 | Figure 6 | 无法复现 | 仅有说明（未出图） | — |

逐图的绘制信息（类型、坐标轴、系列→数据键、风格）见 `figures/FIGURES.md`。demo 玩具数据的图带有水印，**只反映定性趋势，不是论文数值**。

## 注意事项

- 执行沙箱为**本地模式**（低隔离 / 高风险）：生成的代码在本机进程里运行，只有超时与独立工作目录约束；要真正的隔离请开启容器执行器（sandbox_mode=container）。
- demo 的基础契约通过，但**论文声明的性质断言失败** → 判定该 demo 失败（明细见 MVP_REPORT.md「性质断言」）。
- LLM 生成的 demo 未通过（论文声明的性质断言不成立（smoke 契约本身是通过的）；现场已保留在 demo_llm_failed/），改为输出离线脚手架。
- 模板 supervised_regression 未能在论文里找到可用数值列，demo 使用**合成数据**（只反映定性趋势，不是论文数值）。
- 模板 supervised_regression 是领域骨架，其中的论文特有部分（见 run_one 的 TODO）尚未实现；配置 LLM 后可在此骨架上填充以获得忠实的 MVP。
