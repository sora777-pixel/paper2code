> 本文件是 `python -m paper2code eval` 的基线快照（离线、确定性），供改动前后对比。
> 复算：`python -m paper2code eval`；对比：`python -m paper2code eval --compare outputs/eval/latest.json`。
> **正文（一～五）是 M4 落地时刷新的快照**；此后每个里程碑的数字与归因记在文末「六～八」，
> 最新一次的完整输出永远在 `outputs/eval/latest.md`（含 M6 新增的「可复现性基准 / 复现榜单」）。

# 评测基线（python -m paper2code eval）

- 评测集：`E:\WorkBuddy\paper2code\configs\eval_set.json`（v1，2026-10-20）
- 运行时间：2026-10-20T09:06:01 ｜ 端点：离线（`offline-scaffold`，0 token）｜ 沙箱：`local`（执行前静态扫描；离线路径的确定性生成物标记 trusted）

## 一、总体（**分层报告，禁止只看平均**）

| 层 | n | 成功跑完 | smoke 通过率 | 情形判定正确率 | 校验期望命中率 | 结构抽取命中率 | 图表出图率 | 保真度均分 | 均耗时 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **L1 合成** | 3 | 3 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 2.00 | 5.1s |
| **L2 真实** | 3 | 3 | 100.0% | 100.0% | 0.0% | 86.7% | 33.3% | 1.00 | 5.3s |
| **全部** | 6 | 6 | 100.0% | 100.0% | 50.0% | 93.3% | 66.7% | 1.50 | 5.2s |

> 路线图 M1 目标：smoke 通过率 ≥90% ✅ ｜ 图表出图率 ≥70% ⚠️ ｜ 保真度均分 ≥3.5 ⚠️
> `required_outputs 覆盖率` = -（离线脚手架不实现论文方法，只有接 LLM 后该项才有意义）
> 路线图 M3-① 验收：领域模板覆盖 6/6 = 100.0% ✅（目标 ≥60%）

## 二、逐条明细

| 用例 | 层 | 情形(期望) | 校验 | 结构(表/图/算法/链接) | demo | 出图 | 断言(pass/fail/skip) | 保真度 | 耗时 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `01_open_source` | L1-synthetic | open_source ✅ | consistent ✅ | 3/1/1/1 vs 需 3/1/1/1 | 通过 | 4/4 | 0/0/0 | 2 | 7.5s | offline-template:supervised_classification · warn 3 |
| `02_no_code` | L1-synthetic | no_code ✅ | consistent ✅ | 3/1/0/0 vs 需 3/1/0/0 | 通过 | 3/3 | 0/0/0 | 2 | 4.8s | offline-template:systems_cache · warn 3 |
| `03_pseudocode` | L1-synthetic | pseudocode_only ✅ | consistent ✅ | 1/1/1/0 vs 需 1/1/1/0 | 通过 | 2/2 | 0/0/0 | 2 | 3.1s | offline-template:optimization · warn 4 |
| `01_hsp70_bag2` | L2-real | no_code ✅ | not_verifiable ❌ | 0/13/0/0 vs 需 4/12/0/0 | 通过 | 0/12 | 0/0/0 | 1 | 5.5s | offline-template:supervised_regression · warn 6 |
| `02_pbee_superlearner` | L2-real | open_source ✅ | not_verifiable ❌ | 0/7/0/4 vs 需 1/6/0/1 | 通过 | 0/6 | 0/0/0 | 1 | 4.4s | offline-template:supervised_regression · warn 6 |
| `03_pbee_si` | L2-real | no_code ✅ | not_verifiable ❌ | 4/0/0/0 vs 需 3/0/0/0 | 通过 | 2/2 | 0/0/0 | 1 | 6.0s | offline-template:supervised_regression · warn 5 |

## 三、人工标注（评测集自带，供复核）

- **01_open_source**（L1-synthetic）：核心机制 —— 锚点谱主题发现（AS-Topic）：用锚点词约束非负矩阵分解，在短文本上做主题聚类
  - 可定性复现的结论：AS-Topic 在 Accuracy/NMI/ARI 三项上均优于 K-Means/NMF/BERTopic
  - 应出现的图：Figure 1 聚类性能对比（Accuracy/NMI/ARI 分组柱状）
- **02_no_code**（L1-synthetic）：核心机制 —— 边缘 CDN 缓存命中率的经验分解（请求份额 vs 字节份额）
  - 可定性复现的结论：视频类只占 7.1% 的请求，却占 68.4% 的字节
  - 应出现的图：Figure 1 请求份额 vs 字节份额（分组柱状）
- **03_pseudocode**（L1-synthetic）：核心机制 —— 单遍常数内存的流式 Top-K 选择，抗对抗性分数流
  - 可定性复现的结论：快速路径带来约 3 倍加速
  - 应出现的图：Figure 1 STREAMTOPK 相对朴素基线的吞吐
- **01_hsp70_bag2**（L2-real）：核心机制 —— 深度学习驱动的肽设计 + 分子模拟，破坏 Hsp70.14–BAG2 的蛋白-蛋白相互作用
  - 可定性复现的结论：设计的肽能够结合并破坏 Hsp70.14–BAG2 界面
  - 应出现的图：Hsp70.14 3D 结构验证；HADDOCK 打分；RMSD/Rg/SASA 等 MD 标准图集
- **02_pbee_superlearner**（L2-real）：核心机制 —— 用廉价的 Rosetta 能量项 + 集成学习（super learner）逼近绝对蛋白-蛋白结合自由能 ΔG
  - 可定性复现的结论：PBEE 仅用少量廉价特征即可逼近昂贵的增强采样 ΔG
  - 应出现的图：SL pipeline 示意；PBEE flowchart；验证集回归散点
- **03_pbee_si**（L2-real）：核心机制 —— 上述论文的补充材料（以表格为主，含与正文一致的基准数值）
  - 可定性复现的结论：SI 表格数值与正文结论一致（作为正文抽取失败时的对照）

## 四、诚实边界

- L2 真实论文的 `sections/tables/figures` 期望值来自人工标注的**下限**，不是精确真值。
- 离线路径 0 token；真实 LLM 端点的 token/耗时**尚未统计**（provider 未做用量记账）。
- `missing` 表示评测集登记的语料不在本机（真实论文不入库）。

## 五、与基线对比（`--compare`）

| 指标 | 基线 | 当前 | 变化 | 判定 |
| --- | --- | --- | --- | --- |
| smoke_pass_rate | 1.00 | 1.00 | → +0.0000 | 持平 |
| mode_ok_rate | 1.00 | 1.00 | → +0.0000 | 持平 |
| verdict_ok_rate | 0.50 | 0.50 | → +0.0000 | 持平 |
| structure_hit_mean | 0.93 | 0.93 | → +0.0000 | 持平 |
| figure_draw_rate | 0.67 | 0.67 | → +0.0000 | 持平 |
| fidelity_mean | 1.50 | 1.50 | → +0.0000 | 持平 |
| elapsed_mean_s（越小越好） | 5.32 | 5.22 | ↓ -0.1078 | ✅ 改善 |

> 标记为「退步」的指标必须解释或回滚；这是路线图「每个里程碑结束做一次评测对比，退步则不合并」的落点。

## 六、M4 记录（沙箱与安全）

命令：`P2C_SANDBOX_MODE=container python -m paper2code eval --out outputs/eval/container --compare outputs/eval/latest.json`

**① 容器模式（Docker 执行器）与本地模式的对比 —— 验收要求「通过率下降 ≤5 个百分点」**

| 指标 | 本地模式 | 容器模式 | 变化 | 判定 |
| --- | --- | --- | --- | --- |
| smoke_pass_rate | 1.00 | 1.00 | → 0.0000 | ✅ 持平（下降 0.0pp ≤ 5pp） |
| mode_ok_rate | 1.00 | 1.00 | → 0.0000 | ✅ 持平 |
| structure_hit_mean | 0.93 | 0.93 | → 0.0000 | ✅ 持平 |
| figure_draw_rate | 0.67 | 0.67 | → 0.0000 | ✅ 持平 |
| fidelity_mean | 1.50 | 1.50 | → 0.0000 | ✅ 持平 |
| elapsed_mean_s（越小越好） | 5.22 | 10.47 | ↑ +5.2489 | ⚠️ 退步（已归因，见 ③） |

6 条评测用例**全部真的跑在容器里**（`mvp_result.json.sandbox.mode = container`、`risk = low`、`degraded = false`），
demo 全通过、出图数不变 —— 即容器化没有牺牲质量，只增加了固定开销。

**② 红队用例（红队样本 → 全部被拦截并记录，且从未执行）**

`python -m unittest tests.test_sandbox`（15→18 条）：联网（`socket`）、fork 炸弹（`os.fork`/`multiprocessing`）、
读家目录（`os.path.expanduser`/`Path.home`/`HOME`）、写工作目录之外（绝对路径字面量 / `..` 越界）——全部在执行前
被拦下（`blocked=true`、`returncode=-1`、写出 `.work/exec_logs/*.json` 记录），并用 canary 文件证明**代码确实没有跑**。
容器侧另有一组「隔离事实」断言：容器内 `/sys/fs/cgroup/pids.max=128`、`memory.max=1073741824`、
根文件系统 `ro`、真正联网被拒、宿主路径不可见。

**③ 均耗时的归因（为什么本地模式一度显示 +0.5s）**

本地模式连续两次评测为 6.17s / 5.82s（对比 M3 基线 5.32s 报「退步」）。逐项微基准显示 M4 新增的
代码开销只有 **~5ms/篇**（离线路径的确定性生成物标记 trusted，静态扫描被跳过；执行日志 ~0.4ms、产物哈希 ~0.1ms）。
把为我做容器验证而临时拉起的 Docker Desktop 停掉后复跑，均耗时回到 **5.365s / 5.755s**，最终一轮为 **5.22s（改善）**：
结论是那 0.5s 来自 Docker Desktop 的 Linux 虚拟机占用的 CPU/内存，**不是 M4 代码引入的退步**。
容器模式的 **+5.2s/篇**是容器创建、绑定挂载与容器内冷启动 matplotlib 的固定成本（跑第二遍会明显下降），
属于该模式的固有代价，不是质量问题。

**④ 本机环境事实（供复现）**

* Docker Hub 直连在本机不通（`registry-1.docker.io:443` 超时），**执行器会自动兜底**：先按 `sandbox_base`
  拉基础镜像，失败就依次试内置镜像源（`docker.m.daocloud.io/library/python:3.12-slim`），再按 pip 源兜底构建。
  实测删掉镜像后不设任何环境变量执行 `python -m paper2code sandbox --build`，自动选中 daocloud 镜像源构建成功
  （`image.base` 记录了实际用的那一组）。预期外要指定别的源仍可用 `sandbox_base` / `sandbox_pip_index`。
* 拉不到镜像时**不假装隔离**：策略降级为 `local`，`degraded=true`、原因写入 warnings 与 MVP 报告「执行沙箱」一节。
* 沙箱镜像 `p2c-sandbox:latest`（python 3.12 + numpy 2.5.4 + matplotlib 3.11.2）；容器模式下 `samples/01_open_source`
  端到端跑通（demo 通过、4/4 张图在容器内绘出）。

## 七、M5 记录（产品化与协作）

**① 验收目标 vs 实测**

| 路线图 M5 验收 | 实测 | 证据 |
| --- | --- | --- |
| 新手 5 分钟内完成「上传 → 看到 MVP 报告 → 改一次 → 再看」 | ✅ 端到端跑通（对真实运行的服务打 HTTP） | 见 ② 的验收输出 |
| 崩溃/卡死投诉归零（任务有超时与取消） | ✅ 队列 + 任务级超时 + 取消（排队直接结束、运行中在阶段边界/子进程轮询生效） | `tests/test_taskqueue.py` 17 条 |

**② 验收演示（真实服务，`serve --port 8124`）**

```
1. 队列初始状态：并发上限 2 ｜ 单任务超时 1800s
2. 上传（未确认）→ HTTP 409（沙箱确认闸）；确认后 → task=pending
   … 运行中：当前阶段 explain → reproduce → mvp（阶段进度实时可见）
   结果：done ｜ 阶段 explain=done、reproduce=done、mvp=done
3. 看到 MVP 报告：offline-template:systems_cache ｜ demo 通过 ｜ 出图 3 张
   导出：notebook=export/mvp_demo.ipynb ｜ streamlit=export/streamlit_app.py
   版本：当前 v1 ｜ 历史 0 条
4. 改一次（本机无 LLM 端点）：任务 done ｜ 生成方式 previous-version（「改一次」未生效，保留改动前的版本）
   如实说明：「改一次」失败（该 Provider 不提供自由文本生成（离线模式））：模型没能给出可用的修改，已保留改动前的版本。
   再看：当前 v2 ｜ 上次指令「把优化器换成 Adam」｜ demo True
5. 批处理 + 取消：一次入队 2 篇；对排队中的一篇取消 → 「任务尚未开始，已直接取消」
6. 队列终态：done 3 ｜ cancelled 7（含此前遗留）｜ pending 0 ｜ running 0
```

**③ 均耗时的归因（为什么显示 +0.10s）**

M5 新增代码实测开销：`export.export_mvp()` 14.8ms、`iterate.finish_build()` 2.2ms、
插件加载/模板打分/风格注入合计 <0.1ms —— **每篇合计约 17ms**，与 +0.10s（100ms）差一个数量级。
连续三次评测为 5.675 / 5.324 / 5.423s，逐条 delta 在 −1.59s ~ +1.69s 之间来回摆动（同一台机器上的
matplotlib 渲染与磁盘抖动），说明这项差异**主要是测量噪声**，不是 M5 引入的退步。
本轮起：**功能指标（smoke / mode / structure / figure / fidelity / 模板覆盖 100%）全部持平**。

**④ 本轮未做（按路线图原文如实标注）**

* **多语言**：界面与产物仍为中文（路线图把「多语言」列为体验项，未指定语种与范围）。
* **可选账号与分享链接**：未做——本项目是单机本地工具，账号体系需要服务端与鉴权，
  与「离线永远可用」的硬约束冲突（当前没有任何联网依赖）。
* **任务队列**：按路线图用 SQLite 起步（`work_dir/tasks.db`），未做跨机分发与优先级抢占。

## 八、M6 记录（科研级复现）

**① 交付物与验收**

| 路线图 M6 交付物 | 落点 | 实测 |
| --- | --- | --- |
| 声明级核验报告（主张 → 证据 → 状态：已复现/部分/不可复现/未检验） | `paper2code/verify.py`，产物 `outputs/single/<id>/VERIFICATION.md` + `verification.json` | ✅ 本轮评测共 **40 条主张**：已复现 9 ／ 部分 0 ／ 不可复现 18 ／ 未检验 13 |
| 多种子统计复现与置信区间 | `paper2code/stats.py`（t 区间）+ 模板 demo 的 `_aggregate` 输出 `ci_low/ci_high/sem`（n=1 时**不给区间**） | ✅ 区间判定用于「论文值 vs demo 观测」的三档判据 |
| 数据集自动获取（校验和 + 许可） | `paper2code/datasets.py` + `configs/datasets.yaml` + `python -m paper2code datasets`（只审不下载） | ✅ 许可不在白名单/未声明 → **拒绝下载**；sha256 不符 → 删文件并记失败（16 条测试覆盖） |
| 远程执行器（远程 GPU） | `paper2code/remote.py`，走 M5 的插件执行器接口；`remote_dir` 配了才注册 | ⚠️ 机制完成并本地自测通过；**真·远程 GPU 链路本机无法验证**（无远端、无 GPU） |
| 可复现性基准与复现榜单 | `eval.py` 新增「三、可复现性基准（复现榜单）」+ `outputs/eval/benchmark.json` | ✅ 见 ② |

**② 复现榜单（本轮实测，`outputs/eval/benchmark.json`）**

| 排名 | 用例 | 主张数 | 已复现 | 部分 | 不可复现 | 未检验 | 已复现率 | 已判定率 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `03_pseudocode` | 6 | 3 | 0 | 0 | 3 | 100.0% | 50.0% |
| 2 | `03_pbee_si` | 4 | 1 | 0 | 0 | 3 | 100.0% | 25.0% |
| 3 | `02_no_code` | 9 | 2 | 0 | 0 | 7 | 100.0% | 22.2% |
| 4 | `02_pbee_superlearner` | 8 | 2 | 0 | 6 | 0 | 25.0% | 100.0% |
| 5 | `01_hsp70_bag2` | 13 | 1 | 0 | 12 | 0 | 7.7% | 100.0% |
| — | **基准汇总** | **40** | **9** | **0** | **18** | **13** | **66.5%** | **59.4%** |

**怎么读这张表**（口径写死在 `benchmark.json.metric_definitions` 里）：

* 合成样本（1–3 名）排名高，是因为它们的**结构主张与公式性质**被独立验证了（解析实测抽出、
  性质断言 pass），而**数值主张绝大多数记「未检验」**——demo 是「回放论文自带数字」，
  拿它跟论文数字比是自证，不算验证（**已判定率 22%–50%** 就是这个意思）；
* 两篇真实论文（4–5 名）**已判定率 100%**（所有主张都有证据），但**大部分被判「不可复现」**——
  正文表格抽不出来 → 结构主张失败；图表需要真实数据集/大规模实验 → 标注不可复现。
  这才是这两篇论文在这台机器上的真实状态，**不是失败，而是「如实」**。

**③ 本轮修掉的一个老 bug（eval 侧）**

`eval.py` 早期版本把 `mvp_result.json` 的路径写成 `<out>/mvp/…`，而流水线实际落在 `<out>/<paper_id>/mvp/…`。
后果：**修复轮次 / 性质断言 / token / 成本护栏这几列长期静默显示为 0**，没人发现（因为 0 看起来「正常」）。

修法是**不再猜目录**：改为**以 manifest 里记录的绝对路径为准**（`artifacts.mvp.report` /
`artifacts.verification.json`），拿不到才退回「按用例 id 猜 → 取唯一子目录」（`_resolve_dirs()`）。
新增 `tests/test_eval.py::TestPathResolution` 三条用例把这条契约锁住（含一条「真跑一篇样本、
断言主张数必须 > 0」的端到端形状检查）。修复后本轮实测：

| 用例 | first_pass | fix_rounds | 断言(pass/fail/skip) |
| --- | --- | --- | --- |
| `01_open_source` | True | 0 | **4/0/0** |
| `02_no_code` | True | 0 | **7/0/0** |
| `03_pseudocode` | True | 0 | **1/0/0** |
| `03_pbee_si` | True | 0 | **2/0/2** |

**④ 度量仪表盘（路线图「M1 起常驻」）已补齐**

此前报告只对 smoke/出图率/保真度三项做了目标对比，路线图仪表盘要求的**另外两项一直缺**。
本轮补上「三之二、度量仪表盘」一节，六项逐条对目标（未达标显式标 ⚠️，不粉饰）：

| 指标 | 实测 | 目标 | 判定 |
| --- | --- | --- | --- |
| smoke 最终通过率（含回退） | 100.0% | ≥90.0% | ✅ |
| 不依赖回退的通过率 | 100.0% | ≥60%（M1）/ ≥80%（M3） | ✅ |
| 保真度均分（人工标注） | 1.50 | ≥3.5（M1）/ ≥4.0（M3） | ⚠️（人工标注偏低，与路线图目标有差距） |
| 图表出图率 | 66.7% | ≥70.0% | ⚠️（差 3.3 个百分点） |
| 单篇耗时 / 成本 | 6.5s ｜ 0 tokens | 上限 1800s/篇 | ✅ |
| 离线路径可用率 | 100.0% | 100% | ✅ |

新增口径说明：**不依赖回退的通过率**＝demo 通过 **且** 生成方式非「纯回放脚手架」的比例；
**离线路径可用率**＝跑过的用例里产出完整 manifest 的比例（离线路径永不因无 Key/无网而拿不到产物）。

**⑤ 均耗时的 A/B 归因（这次做了对照实验，不只是解释）**

评测均耗时从 M5 的 5.42s 漂到 6.54s（+1.2s，报「退步」）。为此做了**两次对照实验**
（同一台机器、同一条命令、只切换 M6 的两段代码）：

| 用例 | M6 两段全开 | 关掉 M6 两段 | 差 |
| --- | --- | --- | --- |
| `samples/03_pseudocode`（快用例） | 3.468s | 3.494s | **−26ms** |
| `samples/01_open_source`（评测里最慢的一条） | 6.64s | 6.94s | **−296ms** |

结论：**M6 新增代码的开销在噪声之内（甚至测出负值）**；单品实测 `verification_stage` 27.5ms、
`datasets.load_spec` 0.5ms、`plugins.ensure_loaded` 1.3ms。那 +1.2s 来自机器状态
（这段时间反复起停 Docker 虚拟机、跑容器测试与多轮评测，子进程密集型用例的耗时本身就有 ±1s 抖动：
同一份代码在 M3 基线与本轮之间，`01_open_source` 从 5.82s 漂到 6.6–6.9s）。
因此**按路线图「功能指标为准、退步需解释或回滚」的口径，本轮功能指标全部持平，予以合并**。

**⑥ 本轮未做 / 无法验证（如实标注）**

* **真·远程 GPU 执行器**：机制、命令模板、拒绝语义与本地自测都完成，但本机没有远端与 GPU，
  「在远端 GPU 上真跑一遍论文」这条链路**没有验证**，需要你在自己的机器上按 `remote.py` 的模板配一次；
* **数据集自动获取**：机制与测试完成，但本机**无外网**，真实下载（HTTPS + 大文件 + 校验和）未跑过；
  `python -m paper2code datasets` 的「先审后取」审查路径已实测；
* **公开榜单**：已产出可发布的 `benchmark.json`，但「公开一个复现性基准」还需要把它挂到某个可访问的地方
  （本轮不做外部发布）。

## 九、M1 验收：真实 LLM 端点跑一轮（deepseek-flash）

**命令**：`python -m paper2code eval --llm --out outputs/eval/llm1m`（对照 `outputs/eval`＝离线基线）。

**配置**（写在 gitignored 的 `configs/api_keys.yaml`）：`llm_mode=openai`、模型 `deepseek-flash`、
护栏放宽到 `cost_max_calls=60 / cost_max_tokens=1,000,000 / cost_max_seconds=1800`
（放宽依据：上一轮 300k 档位下 4/6 篇被中途掐断，见本轮「成本」一节）。

**总体**：smoke 通过率 **100%**（6/6）｜ **LLM 留存 4/6**（上一轮仅 1/6）｜ 总 **3,107,088 tokens** ｜
均耗时 **440.4s/篇**（离线 6.5s，上限 1800s 内）｜ 首轮通过 6/6。

| 用例 | 生成方式 | 首轮/修复 | 断言(p/f/s) | 出图 | tokens | 核验 |
| --- | --- | --- | --- | --- | --- | --- |
| `01_open_source` | offline-template（降级） | 是/2 | 4/0/0 | 4/4 | 122,260 | 已复现 4 ｜ 未检验 7 |
| `02_no_code` | **llm** | 是/0 | 16/0/7 | 3/3 | 241,379 | 已复现 2 ｜ 部分 1 ｜ 未检验 6 |
| `03_pseudocode` | **llm** | 是/0 | 9/0/1 | 2/2 | 346,506 | 已复现 3 ｜ 部分 1 ｜ 未检验 2 |
| `01_hsp70_bag2` | **llm** | 是/0 | 20/0/0 | **10/13** | 652,955 | 已复现 1 ｜ **部分 9** ｜ 不可复现 3 |
| `02_pbee_superlearner` | **llm** | 是/0 | 12/0/0 | **6/7** | 868,375 | 已复现 2 ｜ **部分 6** |
| `03_pbee_si` | offline-template（降级） | 是/0 | 2/0/2 | 2/2 | 875,613 | 已复现 1 ｜ 未检验 3 |

**与离线基线相比，最实质的三处变化**

1. **真实论文的图真的画出来了**：`01_hsp70_bag2` 出图 **0/12 → 10/13**、`02_pbee_superlearner` **0/6 → 6/7**
   ——LLM 写的 demo 会产出对应曲线，于是这些原标 `not_reproducible` 的图变成了可定性重绘；
2. **核验状态升级**：这两篇的图主张从「**不可复现**」（离线：12 条 / 6 条）变成「**部分复现**」
   （本轮：部分 9 条 / 6 条），判定率仍 100%——即「不再是没数据就画不出来」，而是「定性可复现」；
3. **保真度初判 3.67**（{4:4 篇, 3:2 篇}）——**首次越过路线图 M1 的 ≥3.5 门槛**
   （离线路径下评测集人工标注仅 1.5）。

**M1 验收对照**：smoke 最终通过率 ≥90% → **100%** ✅ ｜ 不依赖回退的通过率 ≥60% → **4/6 = 66.7%** ✅ ｜
保真度均分 ≥3.5 → 初判 **3.67**，**待人工复核确认**（初判是确定性规则，不是人工判断）⚠️→✅

**两篇降级的确切原因（刻意分开说，不含糊）**

* `01_open_source`：**smoke 契约通过了，但论文的性质断言不成立** → 判失败并降级（122k tokens）。
  —— 这正是本轮修好的「降级原因说错」的落点：以前这里会含糊地说「多轮修复后仍失败」，会把人引向错方向；
* `03_pbee_si`：**`cost_max_calls` 60 次用尽**（不是 token 撞顶，875k < 1M）→ 降级。
  —— 说明「调用次数」这条护栏也在真实端点上吃紧；要再跑可把它一起放宽。

**成本与诚实边界**

* 本轮实花 **3,107,088 tokens**（单篇 12–87 万），比离线路径多 5 个数量级（离线 0 token）；
* `required_outputs 覆盖率` 仍为 `None`：**评测集里所有条目的 `expect.required_outputs` 都是空的**，
  这一项在当前评测集下无法判分（不是 demo 的问题，是标注缺口，已在复核表里列为「证据缺口」）；
* 图是**定性重绘**（带 `MVP toy data` 水印），**不是论文数值**；核验报告里的「部分复现」就是这个意思；
* 初判分数由确定性规则给出（demo 状态 / 断言 / 生成方式），**未写回评测集**——人工填好后用
  `python -m paper2code review-sheet --apply outputs/eval/llm1m/review_sheet.json --yes` 落盘。
