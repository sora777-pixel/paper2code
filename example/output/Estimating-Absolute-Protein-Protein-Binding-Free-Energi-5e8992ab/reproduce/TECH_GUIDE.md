# 技术操作说明书：Estimating Absolute Protein−Protein Binding Free Energies by a Super Learner Model

> **怎么来的**：以下内容由 paper2code 从论文正文**确定性抽取**（正则 + 章节结构），不猜、不编。每条都附出处，便于你回原文核对；抽不到的地方会明确写「未能自动抽取」。
> **官方链接**是登记值（只登记官方站点），与论文引用不一致时**以论文为准**。

## 一、方法流程（论文给出的执行链路）

抽取来源：方法部分段首的「小节标题 + 正文」写法（此刊把方法阶段嵌在段落开头）

1. **Data Sets**
2. **Structure Preprocessing**
3. **Structure Postprocessing**
4. **Geometry Optimization**
5. **Descriptors of Protein-Protein Association**

> 这些阶段名取自段首的小节标题（作者用的「标题式短语 + 句号」写法），顺序按正文出现次序；个别阶段可能被拆到别的段落里，请回原文核对完整性。

## 二、数据来源与官方入口

| 资源 | 官方入口 | 正文出处 |
| --- | --- | --- |
| PDBbind | http://www.pdbbind.org.cn/ | PDBbind,27 (ii) PRODIGY,18 (iii) SKEMPI2,28 and (iv) |
| Docking Benchmark (5.5) | https://zlab.umassmed.edu/benchmark/ | protein-protein docking benchmark version 5 |
| SKEMPI / SKEMPI2 | https://life.bsc.es/pid/skempi2 | PDBbind,27 (ii) PRODIGY,18 (iii) SKEMPI2,28 and (iv) |
| PRODIGY | https://wenmr.science.uu.nl/prodigy/ | PDBbind,27 (ii) PRODIGY,18 (iii) SKEMPI2,28 and (iv) |

## 三、关键筛选标准（论文明确写出的）

### 结构分辨率门槛

- 论文原句：> gaps or missing heavy atoms and with a maximum resolution of
2.5 Å（出处：METHODS）
- 它在筛什么：**晶体结构质量门槛**：分辨率数值越小、电子密度越清楚。用它筛掉低质量结构，是 X 射线结构数据集的标准做法；换数据源时必须沿用同一门槛，否则不可比。
- 复现时要核对的量：**分辨率（Å）**

### 去冗余

- 论文原句：> Size of Nonredundant Data Set of Protein-Protein（出处：METHODS）
- 它在筛什么：在相似结构/复合物之间只保留一条，防止数据泄漏。
- 复现时要核对的量：**去冗余规则**

### 实验结构类型限制

- 论文原句：> We have initially selected 603 X-ray resolved（出处：METHODS）
- 它在筛什么：只取**实验解析**的晶体结构（区别于 NMR、冷冻电镜与预测模型），决定了数据可获取性与误差量级。
- 复现时要核对的量：**结构来源类型**


## 四、工具链

| 工具 | 用途 | 正文出处 |
| --- | --- | --- |
| Rosetta | 蛋白结构建模与能量计算 | using the Rosetta software according to the following protocol: |
| PyRosetta | Rosetta 的 Python 接口 | It uses PyRosetta to |
| XGBoost | 梯度提升树 | XGBoost |

## 五、缺口与执行前提

- 上面「方法流程」若来源于小节标题，**只反映作者的组织方式**，不代表完整执行顺序；
  中间参数（阈值、软件版本、随机种子、硬件）请逐节回原文核对。
- 数据来源与官方入口只覆盖本模块登记的公开资源；论文自建/私有数据不在此列。
- 若要真正跑通整条链路，需要按「二」里的入口准备数据、按「三」的门槛筛结构，并补齐论文未披露的参数——缺哪一项，复现结论就只能限定在「数据自洽」层面。
