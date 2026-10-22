# paper2code · 论文学习工具

提交一篇技术论文，系统产出两类内容：

1. **论文讲解材料** —— 课件（自包含 HTML 幻灯片）与知识播客（双人对白脚本 + SRT 字幕 + 音频），可二选一或同时生成；
2. **论文复现结果** —— 重现论文中的统计表格与图表，输入数据来自论文中提及或附带的数据。

覆盖三种论文情形，并针对每种情形采用不同的复现策略：

| 情形 | 判定依据 | 复现路径 | 复现深度 |
| --- | --- | --- | --- |
| **A 有开源代码** | 论文给出仓库链接，或论文包内附带 `code/` | 复用仓库：扫描入口与依赖 → 生成隔离复现脚本 → 采集产物 → 与论文表格**逐格对标** | 指标复现（相对误差 ≤5% 记 pass） |
| **C 仅含伪代码** | 有算法块，无仓库链接 | 伪代码 → Python 翻译 → 小规模执行 → **算法性质校验** | 逻辑复现（不对标指标） |
| **B 无任何代码** | 既无代码也无伪代码 | 以论文表格/附带数据为输入 → 重建 tidy 数据 → **内部一致性校验** + 图表重绘 | 数据自洽性复现 |

判定优先级为 A > C > B：只要发现仓库链接，即使论文里也有伪代码，也走情形 A（复用优先于重写）。

---

## 快速开始

```bash
# 1) 建议使用虚拟环境
python -m venv .venv
.venv/Scripts/activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# 2) 安装依赖（全部为可选增强，核心流程仅需标准库）
pip install -r requirements.txt

# 3) 跑一篇论文（论文包目录 / 单个文件 / arXiv ID 都行）
python -m paper2code run samples/01_open_source     # 情形 A
python -m paper2code run samples/03_pseudocode      # 情形 C
python -m paper2code run samples/02_no_code         # 情形 B
python -m paper2code run 1706.03762                 # 直接给 arXiv 编号

# 4) 批量跑 samples 下所有论文包
python -m paper2code batch samples

# 5) 打开本地 Web UI 浏览结果
python -m paper2code serve --port 8000
# 浏览器访问 http://127.0.0.1:8000

# Windows 也可以双击/命令行一键启动（自动探测 Python，不依赖本机路径）
start_demo.bat          # 默认 8000 端口
start_demo.bat 8080     # 指定端口
```

### 试试 PDF 输入

真实论文大多是 PDF。`tools/make_sample_pdf.py` 可以把示例论文渲染成一份
**像真论文的 PDF**（去掉 Markdown 痕迹、嵌入 Unicode 字体），用来验证 PDF 链路：

```bash
python tools/make_sample_pdf.py samples/03_pseudocode/paper.md /tmp/paper.pdf
python -m paper2code run /tmp/paper.pdf
```

### 评测模式（可选，接大模型）

不配置任何 API Key 时走 `offline` 模式：用抽取式摘要 + 规则翻译器，
保证零成本、零网络也能端到端跑通。若想显著提升讲解稿质量与伪代码翻译质量：

```bash
# OpenAI
export P2C_LLM_MODE=openai
export OPENAI_API_KEY=sk-xxx

# 或任何 OpenAI 兼容端点（DeepSeek / 通义 / 本地 vLLM / Ollama）
python -m paper2code run samples/01_open_source \
  --llm openai --base-url https://api.deepseek.com/v1 --model deepseek-chat
```

远程调用失败时会**自动降级**为离线模式，并在产物 `warnings` 中写明原因。

---

## 论文包约定（推荐输入形态）

一个目录，把「正文 / 附带数据 / 附带代码」三者解耦，正好对应三种情形：

```
samples/01_open_source/
├── paper.md          # 或 paper.pdf / paper.html —— 论文正文
├── data/             # 可选：论文附带的数据（*.csv）
└── code/             # 可选：论文附带的代码仓库副本
    ├── requirements.txt
    ├── train.py
    └── results/
        ├── metrics.csv    ← 与论文 Table 2 对齐的复现基准
        └── ablation.csv
```

---

## 产物结构

```
outputs/single/<paper_id>/
├── manifest.json                  # 本次运行的总清单（UI 与 CLI 都读它）
├── paper.json / paper.md          # 结构化的论文对象
├── explain/
│   ├── courseware.html            # 自包含课件（← → 翻页，可打印成 PDF）
│   ├── courseware.md              # 可编辑的讲解大纲
│   ├── podcast_script.md          # 双人播客脚本
│   ├── podcast.srt                # 字幕
│   ├── podcast.mp3                # 音频（edge-tts；缺失时降级并说明）
│   └── podcast_meta.md
└── reproduce/
    ├── repro_plan.json            # 复现计划（纯数据，可被其他执行器消费）
    ├── repro_report.md            # 复现报告（策略 / 目标 / 校验明细 / 局限）
    ├── repro_result.json
    ├── repro_run.sh               # 情形 A 生成：隔离 venv + 固定种子的复现脚本
    ├── repo_manifest.json         # 情形 A：仓库扫描结果
    ├── translated_A1.py           # 情形 C：伪代码翻译产物
    ├── data/*.csv                 # 重建出的 tidy 数据
    └── figures/*.png|svg          # 重绘的图表
```

---

## 架构

```
paper2code/
├── config.py              配置（CLI > 环境变量 > yaml > 默认值）
├── models.py              领域模型（Paper / Table / Slide / ReproPlan …）
├── numutil.py             数值解析与误差判定
├── textutil.py            中英文分句、关键词、抽取式摘要
├── ingest/                摄取层
│   ├── loader.py            统一入口：文件 / 目录 / URL
│   ├── mdparse.py           Markdown·HTML 解析（零依赖主力链路）
│   ├── pdfparse.py          PDF 解析（PyMuPDF + pdfplumber，可选）
│   ├── extract.py           表格 / 图 / 算法块 / 代码链接抽取
│   └── arxiv.py             arXiv HTML/PDF 抓取与元数据
├── llm/                   可插拔大模型层（语义化能力，而非裸 complete）
│   ├── base.py              能力接口
│   ├── offline.py           规则实现（大纲 / 幻灯片 / 对白 / 伪代码翻译）
│   ├── openai_compat.py     提示词实现（OpenAI 兼容）
│   └── router.py            选择与自动降级
├── explain/               讲解层
│   ├── courseware.py        自包含 HTML 课件渲染
│   └── podcast.py           脚本 → SRT → 音频
├── tts/                   语音合成适配层（edge-tts / 降级）
├── reproduce/             复现层
│   ├── classifier.py        三情形判定（带证据留痕）
│   ├── planner.py           复现计划
│   ├── dataset.py           tidy 数据集构建 + 规模保护
│   ├── repo.py              情形 A：仓库扫描 / 复现脚本 / 结果对齐
│   ├── pseudo2code.py       情形 C：翻译 → 骨架 → 执行 → 回收
│   ├── checks.py            一致性 / 性质校验
│   ├── compare.py           数值对标（逐格相对误差）
│   ├── charting.py          图表重绘（matplotlib + 内置 SVG 双后端）
│   ├── runner.py            本地执行沙箱（单一可替换入口）
│   └── service.py           复现编排与报告生成
├── pipeline.py            端到端编排
├── cli.py                 命令行入口
└── api.py                 本地 Web 服务 + Dashboard
```

### 三个关键设计

**1）LLM 层暴露的是「能力」而不是 `complete()`。**
`outline / slides / dialogue / pseudocode_to_python / repro_strategy` 是语义化能力，
离线实现用规则填充，在线实现用提示词填充。因此上层完全不需要知道当前用的是哪种 provider，
切换模型不需要改任何业务代码。

**2）执行器与计划解耦，为扩展算力留口。**
`reproduce/planner.py` 产出纯数据的 `repro_plan.json`，`reproduce/runner.py` 是唯一的
执行入口。要把某个步骤从「本地 subprocess」换成「提交到集群」，只需替换 runner，
计划与校验逻辑不动；`scale_guard` 已经把 `max_records / timeout / allow_network / seed`
显式写进计划，便于上层调度器读取。

**3）把「文本脏数据」当成一等公民来处理。**
PDF 抽取出来的文本里藏着大量不可见字符，它们是复现链路真正的杀手。实测中一次性撞上：

| 症状 | 元凶 | 处理 |
| --- | --- | --- |
| `def f(S, k)` 报 `U+00A0` 非法字符 | 空格被抽成 **NBSP**（5 页 PDF 里出现 1100 次） | `textutil.normalize_unicode()` 统一空白变体 |
| `min▯heap` 标识符被劈开 | **软连字符 U+00AD**（LaTeX 断词产物）+ `U+2011` | 删除 / 归一 |
| `heap ← []` 变成 `heap · []` | 赋值箭头在字体替换后退化成**中点** | `_normalize_arrows()` 归一 `← ⟵ ⇐ ≔ := <- · ⋅ ∙ •` |
| 一行翻不动 → **整份文件**语法错误 | 规则翻译器覆盖不了全部伪代码写法 | `repair_syntax()` 收敛式修复：注释掉坏行并**在报告里显式披露** |

这些处理都只作用于**结构性位置**（语句首赋值、for 头、参数表），
因此表达式内部的 `·`（乘法）和中文行文里的全角标点都不会被误伤。

---

## 当前边界

* **复现规模**：单机、单次数据量 ≤ 10,000 条记录（`max_records` 可调），超限会确定性采样并在报告中声明；
* **沙箱强度**：`runner` 是 best-effort 隔离（独立工作目录 + 超时 + 断网），**不是安全沙箱**；
  请只对可信论文/仓库使用，执行不可信代码请替换为容器后端；
* **情形 C 的复现深度**：只做逻辑（性质）校验，不声称复现了论文指标；
* **情形 B 的复现深度**：只做数据自洽性与可视化复刻，未披露的数据集/超参/种子会列成清单；
* **成本护栏**：每篇论文的 LLM **调用次数 / token / 耗时**上限由 `configs/default.yaml` 的
  `cost_max_calls` / `cost_max_tokens` / `cost_max_seconds` 控制（`0` = 不限制）；超限即停用 LLM 路径
  并降级为离线脚手架，且把触发原因写进 `warnings` 与报告的「成本护栏」一节——绝不静默超支；
  用量在 `mvp/mvp_result.json` 的 `usage` 里可查；
* **TTS**：默认用 `edge-tts`（免费、免 Key，需要联网）；完全离线时会产出字幕与脚本并给出说明；
* **PDF 章节切分**：真实 PDF 没有 `#` 标记，章节识别依赖编号式标题与版面启发式，
  准确率低于 Markdown 输入（实测 5 页 PDF 识别出 4 个章节）。精修需引入字体/字号分析或 MinerU；
* **伪代码翻译覆盖率**：规则翻译器不可能覆盖全部写法，翻不动的行会被注释掉
  **并在报告的 warnings 中列出**——宁可见光，不悄悄降级。

---

## 常用命令

```bash
python -m paper2code info                      # 查看当前配置
python -m paper2code list                      # 列出已生成的运行
python -m paper2code serve --port 8000         # 本地 Dashboard
python -m paper2code serve --port 8000 --reload  # 开发模式：改动代码自动重载（M5 起为默认）
python -m paper2code eval                      # 跑评测集并汇总指标（L1 合成 / L2 真实 分层报告）
                                               # 报告含：复现榜单（M6）+ 度量仪表盘（路线图常驻六项）
python -m paper2code run <src> --resume         # 续跑：跳过上次已完成且产物仍在的阶段（阶段缓存）
python -m paper2code run <src> --no-reproduce  # 只要讲解材料
python -m paper2code run <src> --no-audio      # 只要播客脚本与字幕
python -m paper2code run <src> --explain courseware
python -m paper2code run <src> --no-pseudo-run # 情形 C 只翻译不执行
python -m paper2code run <src> --max-records 500
python -m paper2code sandbox                   # 查看执行沙箱策略与容器镜像状态（M4）
python -m paper2code sandbox --build           # 预构建沙箱镜像（幂等；构建期联网，运行期不联网）
python -m paper2code tasks                     # 查看后台任务队列（SQLite）；--cancel <id> 取消
python -m paper2code export <paper_id>         # 导出 ipynb + Streamlit 小演示（M5）
python -m paper2code plugins                   # 列出插件注册的模板 / 图表风格 / 执行器（M5）
python -m paper2code datasets                  # 审查数据集声明：只审不下载（许可 + 校验和，M6）
python -m paper2code llm-check                 # 真实 LLM 端点预检：只花一次调用（延迟/用量/真实报错）
python -m paper2code serve --no-reload         # 关闭热更新（默认是开着的）
```

> 想用真实模型：把凭据写进 **`configs/api_keys.yaml`**（该文件已被 `.gitignore` 与打包脚本排除，
> 代码会真的读它；`llm_api_key` 一填就会自动切到 openai 模式）。先 `llm-check` 预检，再 `eval --llm`。

> M6 起，每篇论文的产物里多一份 **`VERIFICATION.md`**：把「论文每条主张 → 证据 → 状态
> （已复现/部分/不可复现/未检验）」逐条列出来。**回放论文自带数字一律记「未检验」**——
> 拿论文的数字跟论文的数字比不是验证。评分口径见 `outputs/eval/benchmark.json`。
