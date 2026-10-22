# 情形 A：仓库 smoke 运行

由 paper2code 自动生成。目标是**用缩小规模**跑通仓库入口一次，从而知道
「仓库在这台机器上到底能不能跑」，而不是只给一份待人工执行的脚本。

- 模式：library
- 命令来源：readme-example
- 入口：`pbee.py`
- smoke 命令：`pbee.py --ipdb ./reproduce\repo\tests\pdbs\1xgu.pdb --partner1 AB --partner2 C --odir ./reproduce\smoke\outputs`

```bash
python run_smoke.py     # 结果写入 result.json
```

阻塞项：缺少依赖：pandas、pyrosetta、scipy、sklearn、xgboost（pip install pandas scikit-learn scipy xgboost；pyrosetta 不是 PyPI 包：需按官方说明安装（`python -c "import pyrosetta_installer; pyrosetta_installer.install_pyrosetta()"`），且需要 Rosetta 许可），无法在本机跑 smoke
备注：smoke 命令取自 README 已文档化的示例（不猜 flag）；多/通配输入缩成单个样本：1xgu.pdb；输出目录改到 smoke 工作目录（不污染仓库）；入口不可用（缺少依赖：pandas、pyrosetta、scipy、sklearn、xgboost（pip install pandas scikit-learn scipy xgboost；pyrosetta 不是 PyPI 包：需按官方说明安装（`），改走 library 模式：直接调用仓库自带的 `modules.superlearner.train_base_models` —— 这只是**代码路径 smoke**，不是论文数值复现。
