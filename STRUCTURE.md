# 项目目录说明（STRUCTURE）

> 更新时间：2026-10-22（测试输入集中、调研文档本地化、临时文件清理后）。
> 目的：一眼分清「运行代码 / 配置 / 测试输入 / 测试输出 / 本地文档 / 可删文件」。
> 判断规矩：根目录只放入口文件和高一级目录；一次性脚本与日志不再留在项目内。

## 一、根目录总览

| 路径 | 放什么 | 能不能删 | 是否入库 |
| --- | --- | --- | --- |
| `paper2code/` | **项目运行代码**（唯一源码目录） | 不能 | 是 |
| `configs/` | 运行配置：`default.yaml`、`eval_set.json`、`plugins.yaml`、`datasets.yaml`、`api_keys.yaml`（含密钥） | 不能 | 配置文件入库，密钥不入库 |
| `tests/README.md` | 测试输入/输出映射与运行命令 | 不能 | 是 |
| `tests/inputs/single/` | 2 篇单篇测试输入（每篇一个目录，含 PDF） | 谨慎 | 否（gitignore） |
| `tests/inputs/batch/` | 19 篇批量测试输入 PDF | 谨慎 | 否（gitignore） |
| `tests/inputs/eval/` | 评测输入映射说明（实际输入仍在 `samples/`、`_test_paper/`） | 不能 | 是 |
| `samples/` | 3 个**合成**论文包，评测集 L1 用例来源，也是 CLI 示例输入 | 谨慎（删了 eval/示例缺用例） | 是 |
| `_test_paper/` | 3 个**真实**论文测试包，评测集 L2 用例来源 | 谨慎（删了 eval 缺用例） | 否（gitignore） |
| `outputs/single/` | 单篇测试输出（2 篇 + `index.json`） | 谨慎 | 否（gitignore） |
| `outputs/batch/patch2/` | 批量测试输出（19 篇 + `index.json`） | 谨慎 | 否（gitignore） |
| `outputs/eval/` | 评测输出（基线、榜单、review sheet、`_runs/`、各轮子目录） | 谨慎 | 否（gitignore） |
| `example/` | **演示案例**：仅 `output/` 复现结果（原文 PDF 在论文目录内） | 不能（用于上传） | 是 |
| `local_docs/research/` | 调研方案文档（总体方案、改进清单、诊断方案、文献清单） | 可整体归档 | 否（gitignore） |
| `local_docs/archive/` | 历史清理记录、语料清单、一次性分析脚本 | 可整体归档 | 否（gitignore） |
| `docs/` | 代码相关文档：`eval_baseline.md`、`mvp_baseline.md` | 不能 | 是 |
| `docs/history/` | 历史补丁差异 `CHANGES.diff` | 可归档 | 是 |
| `tools/` | 长期维护工具：补丁、样例 PDF、语料基线、密钥扫描、打包、演示图 | 不能 | 是 |
| `.work/` | 运行状态：仅保留后台任务队列 `tasks.db` | 可清空（会丢任务历史） | 否（gitignore） |
| `.workbuddy/memory/` | 本地项目记忆（偏好、决策、事故记录） | 谨慎 | 否（gitignore） |
| `.github/workflows/` | CI：密钥扫描 `ci.yml` | 不能 | 是 |

> 不再存在的目录：`incoming/`（上传时会自动重建）、`_corpus/`（已归档清单）、
> `_test_paper_si/`、`_incoming_files_10_19/`、`tests/` 下的测试代码、`.trae-html-share-packages/`。
> 当前 `tests/` 只放测试输入与说明，不再是单元测试代码目录。

## 二、根目录文件

| 文件 | 用途 |
| --- | --- |
| `README.md` | 项目总说明与产物结构 |
| `README_MVP.md` | MVP 自动生成补丁包说明 |
| `STRUCTURE.md` | 本文件，目录与测试资产约定 |
| `requirements.txt` | 运行依赖 |
| `start_demo.bat` | 一键启动本地 Web UI（自动探测可用解释器） |
| `.gitignore` | 忽略产物、测试输入、本地文档、密钥、缓存 |
| `.gitleaks.toml` | 密钥扫描规则 |
| `.pre-commit-config.yaml` | 提交前检查 |

## 三、测试输入与输出的对应关系

测试输入集中在 `tests/inputs/`，测试输出集中在 `outputs/`：

| 测试类型 | 输入 | 输出 | 数量 |
| --- | --- | --- | ---: |
| 单篇 | `tests/inputs/single/<paper_id>/` | `outputs/single/<paper_id>/` | 2 |
| 批量 | `tests/inputs/batch/<paper_id>.pdf` | `outputs/batch/patch2/<paper_id>/` | 19 |
| 评测 | `samples/`、`_test_paper/` | `outputs/eval/` | 6 |

评测输入没有物理移动到 `tests/inputs/eval/`，因为 `configs/eval_set.json` 直接引用
`samples/...` 与 `_test_paper/...`；移动会破坏 `python -m paper2code eval`。
该目录只放映射说明，完整命令见 `tests/README.md`。

## 四、`outputs/` 内部布局

```
outputs/
├── single/
│   ├── Disruption-of-Hsp70.14-BAG2-Protein-Protein-interaction-0f68d6a2/
│   ├── Estimating-Absolute-Protein-Protein-Binding-Free-Energies-by-a-Super-Learner-Model/
│   └── index.json                     ← 单篇总索引（Dashboard 与 CLI list 的数据源）
├── batch/
│   └── patch2/
│       ├── <19 个 paper_id>/          ← 批量产物
│       └── index.json
└── eval/
    ├── latest.md / latest.json        ← 离线评测基线
    ├── benchmark.json                 ← 复现榜单
    ├── baseline_m3.json
    ├── review_sheet.md / .json
    ├── _runs/                         ← 离线评测逐用例产物
    ├── container/                     ← 容器沙箱轮
    ├── llm/                           ← LLM 轮
    └── llm1m/                         ← 放宽预算的 LLM 轮
```

运行规则：

- `run` / Web 上传 / `mvp` 默认写 `outputs/single/`。
- `batch` 不指定 `-o` 时写 `outputs/batch/<批次目录名>_<时间戳>/`；
  复现现有批量测试请显式用 `-o outputs/batch/patch2`。
- `eval` / `review-sheet` 默认读写 `outputs/eval/`。

## 五、一篇论文的「输入 → 产物 → 日志」

```
tests/inputs/single/Estimating-Absolute-Protein-Protein-Binding-Free-Energies-by-a-Super-Learner-Model/
    └── Estimating Absolute ... .pdf     ← 测试输入原件

outputs/single/Estimating-Absolute-Protein-Protein-Binding-Free-Energies-by-a-Super-Learner-Model/
    ├── manifest.json                    ← 总清单（产物绝对路径 + 相对路径 + 输入来源）
    ├── paper.json / paper.md            ← 结构化论文对象
    ├── progress.json                    ← 阶段进度（--resume 用）
    ├── VERIFICATION.md / verification.json
    ├── figures/                         ← 论文原图资产
    ├── explain/                         ← 课件 HTML、播客稿、字幕、音频
    ├── reproduce/
    │   ├── repro_report.html / .md      ← 复现报告（主入口）
    │   ├── repro_result.json
    │   ├── TECH_GUIDE.md
    │   ├── data/*.csv
    │   └── figures/<FIG>/               ← 逐图数据、样式、绘制脚本、预览
    └── mvp/                             ← MVP demo、作图说明、notebook、导出

.work/
    └── tasks.db                         ← 后台任务队列（Web「历史」读这个）
```

判断某篇跑没跑完：看对应输出目录下 `progress.json` 的 `stages[].status`；
`failed` / `running` 就是没跑完，用 `--resume` 续跑。

## 六、演示案例 `example/`

`example/` 顶层严格只有一项：`output/`，即完整复现结果。原文 PDF 随结果一起
放在论文目录内，不再保留独立的 demo 代码副本。

```
example/
└── output/Estimating-.../
    ├── paper.pdf                   ← 原文 PDF（演示输入，随结果保存）
    ├── manifest.json               ← 总清单（相对路径）
    ├── paper.json / paper.md       ← 结构化论文对象
    ├── explain/                    ← 课件 HTML、播客稿、字幕、音频
    ├── reproduce/                  ← 复现报告、图表、数据、绘制脚本
    └── mvp/                        ← MVP demo、图表说明、notebook
```

运行演示改用根目录的 `start_demo.bat`：

```bat
start_demo.bat                 :: 起 Web UI，浏览器里上传 example/output/.../paper.pdf
start_demo.bat --offline       :: 离线模式（零 Key、不联网）
```

`output/` 内路径均为相对路径（源 PDF 为 `./paper.pdf`，产物为 `./explain/...`）；
失败的 `reproduce/repo` 克隆未包含，`repo_manifest.json` 已标记 `exists: false`。
`output/` 内不放任何运行时缓存、临时目录或绝对路径。

> 原 `example/code/`（框架副本、两个演示脚本、配置、`start_demo.bat`）已移出
> `example/`，归档在 `local_docs/archive/example_code_20261022/code/`；根目录
> `paper2code/` 是唯一在用的源码。

## 七、常用命令

```bat
start_demo.bat                 :: 双击起 Web UI（默认 8000 端口）
start_demo.bat --offline       :: 离线模式（零 Key、不联网）

python -m paper2code run tests\inputs\single\<paper_id> --llm offline   :: 单篇测试
python -m paper2code batch tests\inputs\batch --llm offline             :: 批量测试（19 篇）
python -m paper2code batch tests\inputs\batch --llm offline -o outputs\batch\patch2
python -m paper2code list                                              :: 已生成的单篇 run
python -m paper2code info                                              :: 当前配置与路径
python -m paper2code eval                                              :: 评测（6 个用例，写 outputs/eval）
```

示例论文仍可直接用根目录 `samples/`：

```bat
python -m paper2code run samples\01_open_source --llm offline
```

> 单元测试代码目录已在 2026-10-22 清理中删除；当前功能验证以 `list`、`eval`、
> 单篇/批量 smoke 与产物检查为准。CI 的 `tests` job 也已同步移除，只保留密钥扫描。

## 八、本地文档与清理约定

- `local_docs/` 已加入 `.gitignore`，调研方案与历史归档不会上传 GitHub。
- `tests/inputs/single/`、`tests/inputs/batch/` 已加入 `.gitignore`；19 篇批量 PDF 来自
  `E:\cc\git_work\paper2code\v2.0\test-patch\patch2` 的恢复副本。
- **不要删**：`configs/api_keys.yaml`、`samples/`、`_test_paper/`、
  `tests/inputs/`、`outputs/single/`、`outputs/batch/patch2/`、`outputs/eval/`。
- 可重新生成：`outputs/eval`、`.work/tasks.db`（丢失任务历史）、`__pycache__`。
- 本次清理释放约 467 MB（含 355 MB 的 `outputs/_envs`、81.8 MB 的 `.work` 缓存与日志、
  18.6 MB 分享包）；明细见 `local_docs/archive/cleanup_20261022_reorg.md`。
