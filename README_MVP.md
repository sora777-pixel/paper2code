# paper2code · MVP 自动生成（补丁包）

**一句话**：下载一篇论文，`run` / 上传时自动多出一个 `outputs/single/<paper_id>/mvp/`：**可运行的 MVP demo**、**每张图的绘制说明**（类型、坐标轴、系列→数据键、风格）和**绘图脚本**，并用你自己的 `runner` 真的跑一遍校验、失败自动修复。

> 本包基于你上传的 `paper2code.zip` 的真实代码结构编写（Python 3.9 兼容、核心只用标准库、复用 `LLMProvider` / `runner` / `charting` / `pseudo2code`）。

## 1. 安装（3 步）

```bat
:: 在仓库根目录，把本 zip 解压覆盖到仓库根（只新增文件，不覆盖已有文件）
python tools\apply_mvp_patch.py --check     :: 先检查 18 处锚点都能匹配
python tools\apply_mvp_patch.py             :: 应用（原文件备份为 *.mvp.bak；--revert 可回滚）
```

补丁只改 7 个现有文件，且**全部是追加/插入**（完整差异见 `CHANGES.diff`）；锚点缺失或不唯一时整体中止、不写任何文件；保留你的 CRLF 换行；可重复运行（幂等）。

## 2. 使用

| 方式 | 做法 |
|---|---|
| 一键（默认开启） | `python -m paper2code run <论文>` → 讲解 + 复现 + **MVP** 一起生成；不想要加 `--no-mvp` |
| 只做 MVP | `python -m paper2code mvp <论文路径 / arXiv ID / incoming 或 tests/inputs/single 下的 paper_id>`；`--no-llm` 只用离线脚手架；`--no-run` 只生成不执行 |
| Web | 论文详情页新增「MVP 演示 / 图表绘制说明」：报告、绘制说明、demo 代码、图表产物链接，以及「生成 / 重新生成」按钮（后台线程 + 轮询） |
| 配置 | `configs/default.yaml` 可设 `mvp_enabled` / `mvp_use_llm` / `mvp_max_fix`（默认 3）/ `mvp_max_fix_fig`（默认 2）/ `mvp_smoke_timeout`（默认 90s）。**执行沙箱**（roadmap M4）：`sandbox_mode`（`local` 默认｜`auto`｜`container`）、`sandbox_image` / `sandbox_base` / `sandbox_pip_index`、`sandbox_cpus` / `sandbox_memory` / `sandbox_pids`、`sandbox_scan`、`sandbox_dep_policy`（`warn`｜`block`）、`sandbox_require_confirm`；也可用同名环境变量（`P2C_SANDBOX_MODE` 等）覆盖 |

## 3. 产物

```
outputs/single/<id>/mvp/
  MVP_REPORT.md            一眼看结论：生成方式、运行结果、简化说明、图表清单、注意事项
  demo/run_demo.py …       无参数运行 = smoke；写 results/metrics.json（无 NaN、含必需键）
  figures/FIGURES.md       每张图的「绘图信息」
  figures/specs/*.json     机器可读的绘图说明（含内嵌数据）
  figures/draw_*.py        绘图脚本（LLM 生成，失败回退模板）  figures/figlib.py  run_figures.py
  figures/out/*.png|svg    出图（demo 玩具数据的图带水印）
  artifacts/plan.json      MVP 计划（含「简化了什么」）
  demo_llm_failed/         （仅当 LLM 版 demo 多轮仍失败时）保留失败现场
  mvp_result.json
```

## 4. 两条路径（行为不同，报告里都会写明）

**LLM 路径**（配置了可用的 Key）：`plan`（只做 MVP 能忠实实现的核心）→ `code`（`<<<FILE>>>` 块，路径白名单）→ 在你的 `runner` 子进程里运行 → 校验（退出码、`metrics.json` 合法/无 NaN/含必需键）→ 失败则把报错交给模型修复，最多 3 轮 → 仍失败则**回退到离线脚手架**并保留现场。图表：论文表格 → 用你已有的 `charting.infer_spec` 直接重绘；论文图 → 模型给出绘图说明，只允许引用 demo 里**真实存在**的数据键；每张图脚本单独运行、修复、失败回退模板。

**离线路径**（无 Key / 调用失败，**永远可用**）：脚手架 demo = 回放论文表格（数值内嵌）+ 执行你已有的离线伪代码翻译器产物；图表说明来自表格与图注（图注引用了某张表则用该表数据近似重绘）。它**不是论文方法的实现**，报告顶部会明说。

## 5. 诚实性规则（写死在代码里）

- demo 玩具数据出的图一律加水印「MVP toy data – NOT the paper's results」，只代表定性趋势。
- 需要真实数据的图标记为 `not_reproducible`，写明原因与「demo 要怎么改才能复现」，绝不伪造。
- 每个 demo 的「简化说明」进入报告；LLM 生成失败的情况会作为 warning 如实记录。

## 6. 已验证 / 未验证

**已验证（本机，Python 3.12，针对你仓库的真实代码）**：18 处补丁锚点匹配且幂等；所有文件通过 Python 3.9 语法解析；13 个测试全过——含脚本化 LLM 路径（demo 首次 `NameError`→修复、图表脚本首次索引错误→修复、LLM 失败降级、LLM demo 永远修不好→回退并保留现场）、`run_paper` 集成、`mvp` 子命令；在 `samples/` 三种情形上离线端到端跑通并检查了出图。

**未验证（需要你在自己机器上跑一次）**：真实 DeepSeek / OpenAI 兼容端点的生成质量与耗时（本机无网络、也没有使用你的 Key）；Windows 路径与 PyMuPDF 解析的真实 PDF；Web 页面的按钮交互（只做了补丁与语法检查，没有浏览器端实测）。建议先用 `mvp --no-run` 看生成的代码，再正式运行。

## 7. 安全提醒

- **你上传的 zip 里 `configs/api_keys.yaml` 含有真实的 LLM 与 TTS Key**（尽管文件已被 gitignore，压缩包是整个目录打的）。请**立即轮换这两个 Key**；本包没有读取或使用它们，工作副本里已删除该文件。今后分享代码请排除 `configs/api_keys.yaml`、`.venv/`、`.work/`、`outputs/`、`incoming/`。
- **凭据放哪儿**：写进 `configs/api_keys.yaml`（`.gitignore` 与 `tools/pack_repo.py` 都排除它，且**代码真的会读它**——这是本轮修的一个坏约定：以前这个文件被排除，但没人读它，放进去等于没放）。优先级：命令行 > 环境变量 > `api_keys.yaml` > `default.yaml` > 内置默认值；填了 `llm_api_key` 会自动切到 openai 模式，但 `--offline` 仍压过它。
- **接真实端点前先预检**：`python -m paper2code llm-check`（只花一次调用，打印延迟与用量；失败时打印**真实原因**——降级路径会把原始 HTTP 报错写进 warnings，这个命令会把它捞出来）。预检过了再 `python -m paper2code eval --llm`。
- 本功能会**执行模型生成的代码**。roadmap M4 已落地的三层防护：①**静态扫描**（`paper2code/safety.py`）在执行前拦下 `os.system`/`subprocess`/`socket`/`ctypes`/`multiprocessing`、读写工作目录之外、读家目录的代码，命中即**不执行**并记录理由；②**容器执行器**（`sandbox_mode=container`）默认无网络、根文件系统只读、只挂载工作目录、CPU/内存/pids/墙钟时间都有上限；③**执行日志 + 产物哈希**落在 `.work/exec_logs/`，每次运行可事后审计。
- `sandbox_mode` 默认仍是 `local`（本机进程，**低隔离 / 高风险**：只有超时与独立工作目录）。任务会让你选「强制容器」还是「默认本地、可选容器」；CLI 与 Web 页面都会把当前隔离级别与风险**如实显示**。`--no-run` 只生成不执行；Web 端在非容器模式下必须勾选「我确认在本机运行生成的代码」才会放行（后端 409 拦截）。
- **本机实测的坑（已自动兜底，无需手工配）**：Docker Hub 直连不通时拉不到 `python:3.12-slim`，执行器会**自动改用内置镜像源**（`docker.m.daocloud.io/library/python:3.12-slim`）与 pip 源兜底，构建成功后把实际用的组合记进日志与 `python -m paper2code sandbox` 的输出。要指定别的源仍可用 `sandbox_base` / `sandbox_pip_index`（或 `P2C_SANDBOX_BASE` / `P2C_SANDBOX_PIP_INDEX`）。
- 预构建镜像：`python -m paper2code sandbox --build`（幂等；已有镜像时只做检查）。**本地模式与容器模式可以并存**：默认 `local`，需要隔离时把 `sandbox_mode` 设为 `container`（或 `auto`：有容器就用容器）。
- 拿不到容器时**不会假装隔离**：策略降级为 local 并把原因写进 report/warnings。

## 7.5 产品化（roadmap M5）

| 能力 | 用法 | 诚实边界 |
|---|---|---|
| **任务队列**（SQLite + worker 池） | Web 提交后进队列；`python -m paper2code tasks` 看状态；`--cancel <id>` 取消 | 并发上限与超时可配（`task_concurrency` / `task_timeout_s`）。排队中取消=立即结束；运行中取消=在**阶段边界**与**子进程轮询**处生效（最长约 0.25s 响应） |
| **批处理视图** | Web 页面上「批处理」框，每行一个路径 → 一次入队 | 逐篇排队、可逐个取消 |
| **迭代式 MVP（改一次）** | Web 卡片上的输入框 + 「改一次」按钮；或 `POST /api/mvp/{id}/modify` | 上一版存档到 `mvp/versions/vN/`，并生成 `vN_DIFF.md`；**改不动时不假装改好**——保留上一版并写明原因（离线模式无法改，会如实说明「指令未生效」） |
| **导出** | 每篇自动导出；`python -m paper2code export <paper_id>` 可重跑 | notebook 是**自包含**的（demo 与绘图脚本写进单元格，可在 Colab 整段运行）；**一键 Colab 链接需要先把 notebook 托管出去**，没配 `notebook_repo` 时不给假链接，只在 README 里写清怎么上传 |
| **插件接口** | `configs/plugins.yaml` 的 `plugins: [my_pkg.plugins]`；模块里调 `register_template/register_style/register_executor`；`python -m paper2code plugins` 查看 | 插件只加能力、不改判定语义；内置模板名不可被静默覆盖；插件导入失败只记 warning |
| **错误可见可复制** | 队列里失败任务带「复制错误」按钮（含阶段快照） | 错误永远落在 `tasks.db` 与 `outputs/single/<id>/progress.json`，刷新不丢 |
| **热更新** | `serve` **默认开**热更新；要关用 `--no-reload` | — |

**M5 未做（如实标注）**：多语言界面、账号与分享链接。后者需要服务端与鉴权，与「离线永远可用」的硬约束冲突——当前全部功能不依赖任何联网服务。

## 7.6 科研级复现（roadmap M6）

| 能力 | 用法 | 诚实边界 |
|---|---|---|
| **声明级核验报告** | 每篇自动产出 `outputs/single/<id>/VERIFICATION.md` + `verification.json`；主张来源＝结构/表格极值/图注/公式性质/仓库 smoke | 四档：已复现／部分复现／不可复现／未检验。**回放论文自带数字一律记「未检验」**；另有「实跑对标」（复现产物 vs 论文表格逐格）作为最强证据 |
| **多种子统计与区间** | 模板 demo 默认 3 种子，`metrics.<k>` 带 `mean/stdev/sem/ci_low/ci_high` | `n=1` **不给区间**（一次运行估不出波动）；论文值落在 CI 内→一致，落在区间外但同量级→部分 |
| **数据集自动获取** | `configs/datasets.yaml` 里登记 `url + sha256 + license`；`python -m paper2code datasets` 先审后取 | 许可不在白名单（含未声明）→ **拒绝下载**；sha256 不符 → 删文件并记失败；全程写 `data/DATA_SOURCES.json` |
| **远程执行器（远程 GPU）** | `remote_host` / `remote_dir`（+ 可选 `ssh`/`scp` 命令模板）；注册后 `executor="remote"` | **指了远端却不可用就拒绝执行**，绝不偷偷退回本机跑你的代码。本机无远端，这条链路只做了机制与本地自测 |
| **可复现性基准 / 复现榜单** | `python -m paper2code eval` → 报告新增「可复现性基准（复现榜单）」+ `outputs/eval/benchmark.json` | 榜单口径写在文件里：**已判定率低的论文没有资格谈已复现率** |

## 8. 变更清单

| 文件 | 改动 |
|---|---|
| `paper2code/mvp/*`（新增） | 编排、离线脚手架、图表说明、提示词、报告、figlib |
| `llm/base.py` / `openai_compat.py` / `router.py` | 新增可选能力 `complete()`（离线 Provider 抛 NotImplementedError → 触发降级）；OpenAI 兼容端点超时放宽到 ≥300s |
| `config.py` | 5 个 `mvp_*` 设置项 |
| `pipeline.py` | `run_paper(..., mvp=None)`，在复现之后生成 MVP；失败只记 warning，不影响讲解与复现 |
| `cli.py` | `--no-mvp`；新子命令 `mvp`；`_print_manifest` 增加 MVP 行 |
| `api.py` | `has_mvp` 标志；`POST /api/mvp/{id}` 与 `GET /api/mvp/{id}/status`；详情页新增区块与按钮 |

## 9. 已知限制

- 离线脚手架不理解方法；LLM 路径的忠实度取决于模型与论文，必须读报告里的「简化说明」。
- 你们的离线伪代码翻译器对部分算法会产生语法错误（`01_open_source` 的 A1），脚手架会跳过并写入 warning。
- PDF 解析弱时（章节/表格/图抽取不到），MVP 的输入也会变弱——见路线图 M2。
- 仅执行 smoke 模式；`--full` 需手动运行 `python run_demo.py --full`。
- 在没有 CJK 字体的机器上图中文会显示为方框；Windows/macOS 默认字体已在 `figlib` 的回退链里。
