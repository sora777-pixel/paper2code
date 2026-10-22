# Estimating Absolute Protein−Protein Binding Free Energies by a Super Learner Model · MVP 导出（论文 `Estimating-Absolute-Protein-Protein-Binding-Free-Energi-5e8992ab`）

本目录是 `outputs/single/Estimating-Absolute-Protein-Protein-Binding-Free-Energi-5e8992ab/mvp/export/`，由 `python -m paper2code export Estimating-Absolute-Protein-Protein-Binding-Free-Energi-5e8992ab` 生成。

## 1. 在 Colab / Jupyter 里跑

- 文件：`export/mvp_demo.ipynb`（**自包含**：demo 与绘图脚本都写在 notebook 单元格里）。
- **一键 Colab 链接需要先把 notebook 托管出去**（GitHub / Google Drive）——本机离线环境无法替你上传，所以这里不给一个点了会 404 的假链接。
  1. 把 `export/mvp_demo.ipynb` 上传到你的 GitHub 仓库或 Google Drive；
  2. Colab → 文件 → 打开笔记本 → 上传 / GitHub；
  3. 想自动带上链接：设置 `P2C_NOTEBOOK_REPO=<owner>/<repo>` 后重新导出。

## 2. 本地小演示（Streamlit）

```bash
pip install streamlit
streamlit run export/streamlit_app.py
```
它**只读**已跑出来的产物（`demo/results/metrics.json`、`figures/out/*.png`），不重新执行代码、不联网。

## 3. 当前版本

- v1 ｜ 生成方式 `offline-template:supervised_regression (llm demo failed)` ｜ 本次指令：（首次生成）
- demo 运行：通过

## 4. 诚实边界

- notebook 里的图带 `MVP toy data – NOT the paper's results` 水印：**只反映定性趋势**。
- demo 用玩具/合成数据实现论文的核心机制；相对论文简化了什么，见 `../MVP_REPORT.md`。
- Colab 上的依赖（numpy/matplotlib）由 notebook 第一个单元格自动安装。
