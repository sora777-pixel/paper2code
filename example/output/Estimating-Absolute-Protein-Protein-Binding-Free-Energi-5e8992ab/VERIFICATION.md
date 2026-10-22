# 声明级核验报告 · Estimating Absolute Protein−Protein Binding Free Energies by a Super Learner Model

> 生成时间：2026-10-22T19:49:44 ｜ 论文 `Estimating-Absolute-Protein-Protein-Binding-Free-Energi-5e8992ab`

**结论先行**：已复现 1 ｜ 部分 0 ｜ 不可复现 7 ｜ 未检验 0（共 8 条主张）

> 四档含义：**已复现**=有独立证据支持；**部分复现**=方向对量级不对或只有定性证据；**不可复现**=有证据反对或原因明确；**未检验**=没有产出可用证据（**回放论文自带数字不算验证**）。

## 逐条主张

| 状态 | 主张 | 证据 | 说明 |
|---|---|---|---|
| **已复现** | 论文中声明的 7 张图被正确抽出并配对图注 | 解析层实测抽出 7 项 | 论文声明的结构确实存在 |
| **不可复现** | 图 F1 的结论：Schematic representation of the SL pipeline, which undergoes three steps. (A) The full training set is used to train 10 different ML | 图标注为不可复现 | 示意/架构图：离线模式无法还原图中内容；启用 LLM 后可依据图注与方法章节生成方框-箭头示意 |
| **不可复现** | 图 F2 的结论：PBEE flowchart for the protein-protein complex structure PDB ID 1XGU, corresponding to antibody (HyHEL-63) that binds to | 图标注为不可复现 | 需要真实实验数据；可让 demo 写出对应曲线/扫参结果（见 plan.required_outputs）后再绘制 |
| **不可复现** | 图 F3 的结论：Regressions on the validation set using PRODIGY, Rosetta, and the trained ML and SL models (RMSE: root-mean-square error; R: | 图标注为不可复现 | 需要真实实验数据；可让 demo 写出对应曲线/扫参结果（见 plan.required_outputs）后再绘制 |
| **不可复现** | 图 F4 的结论：Comparison of PBEE prediction accuracy by ML method to Rosetta and PRODIGY, for the three protein-protein complex data sets. | 图标注为不可复现 | 需要真实实验数据；可让 demo 写出对应曲线/扫参结果（见 plan.required_outputs）后再绘制 |
| **不可复现** | 图 F5 的结论：PBEE processing time as a function of the number of atoms | 图标注为不可复现 | 需要真实实验数据；可让 demo 写出对应曲线/扫参结果（见 plan.required_outputs）后再绘制 |
| **不可复现** | 图 F6 的结论：Feature importance is based on SHAP-values. The violin | 图标注为不可复现 | 需要真实实验数据；可让 demo 写出对应曲线/扫参结果（见 plan.required_outputs）后再绘制 |
| **不可复现** | 论文仓库 https://github.com/chavesejf/PBEE 的入口能在这台机器上跑通（缩减规模） | smoke status=failed | ModuleNotFoundError: No module named 'joblib' |

## 证据来源与强度

- 生成方式：`offline-template:supervised_regression (llm demo failed)`
- 多种子：3
- 仓库 smoke：failed
- 图表说明：6 条
