# paper2code Demo（本机）

目录：`E:\cursor-ai-demo\paper2code`

提交一篇技术论文，系统同时产出：

1. **讲解**：自包含 HTML 课件 + 双人播客脚本 / SRT（有 edge-tts 时再合成音频）
2. **复现**：按论文情形分派 A / C / B，重绘表格图，并给出可对标的校验报告

不配 API Key 也能跑通（`offline` 抽取式）。三种样例已经跑过一遍，产物在 `outputs/`。

## 一键打开 Dashboard

双击 `start_demo.bat`。它会：

- 用 `D:\Python311\python.exe` 在本目录建 `.venv`（不改系统 PATH）
- 按需安装依赖（走 ASCII 包清单，避开 Windows pip 按 GBK 读 UTF-8 `requirements.txt` 的问题）
- 启动本地页（默认 `http://127.0.0.1:8010`，避开 handbook-ai-tutor 的 8000）

关掉黑色窗口即停服务。数据都在 `outputs/`，不会被清掉。

## Dashboard 页面

- **首页** `/`：选择文件 → 一键「上传并生成解析」（讲解+复现，默认不合成语音）→ 论文卡片仅「查看 / 删除」。
- **详情** `/paper/{paper_id}`：课件、博客/播客稿（含「生成语音」）、复现建议/demo；缺失显示「尚未生成」。
- **设置** `/settings`：LLM 预设/Key/Base/Model + TTS SiliconFlow Key/模型 + 测试连接

LLM / TTS Key 仅存本进程内存，**不写入磁盘/配置**（本页不会改 `configs/default.yaml`）。预设含：离线 / DeepSeek / OpenRouter / SiliconFlow / 自定义 OpenAI 兼容。也可用环境变量 `P2C_LLM_API_KEY`（以及 `P2C_LLM_BASE_URL` / `P2C_LLM_MODEL`）；空 Key 时仍默认 offline。


## 命令行（三种情形）

```bat
cd /d E:\cursor-ai-demo\paper2code
.venv\Scripts\python.exe -m paper2code list
.venv\Scripts\python.exe -m paper2code run samples\01_open_source
.venv\Scripts\python.exe -m paper2code run samples\03_pseudocode
.venv\Scripts\python.exe -m paper2code run samples\02_no_code
```

| 样例 | 情形 | 看什么 |
| --- | --- | --- |
| `samples/01_open_source` | A 有代码 | `outputs/01_open_source/explain/courseware.html` 与 `reproduce/repro_report.html`（21 个数相对误差 0%） |
| `samples/03_pseudocode` | C 仅伪代码 | 逻辑复现：Top-K 与全量排序一致，**不对标论文指标** |
| `samples/02_no_code` | B 无代码 | 占比列求和为 1，图表重绘，**不重跑实验** |

判定优先级 **A > C > B**：有仓库就复用，不重写。

## 论文包怎么放

```
my_paper/
  paper.md          # 或 paper.pdf / paper.html
  data/*.csv        # 可选，论文附带数据
  code/             # 可选，开源仓库副本（走情形 A）
```

然后：

```bat
.venv\Scripts\python.exe -m paper2code run path\to\my_paper
```

可选接 DeepSeek（失败自动回落 offline）：

```bat
.venv\Scripts\python.exe -m paper2code run samples\01_open_source --llm openai --base-url https://api.deepseek.com/v1 --model deepseek-chat
```

## 本机约束

- Python：`D:\Python311`，包只进 `.venv`
- 单机、默认最多 1 万条记录、执行超时 120 秒、复现脚本默认断网
- runner 是 best-effort 隔离，**只对可信论文/仓库使用**
- 讲解与复现都是可选增强；Demo 默认 offline

更完整的调研、模块对照和实测表见 `调研报告/调研报告.md`。


## PDF 解析后端（hybrid / Docling）

- 默认：`P2C_PDF_BACKEND=hybrid`（`start_demo.bat` 已写入；若 bakeoff Docling Python 不存在则回退 `pymupdf`）
- **hybrid** = Docling 结构（标题/章节/带单元格的表格） + PyMuPDF 公式 span / 图路径 / 代码链接
- Docling 跑在独立 bakeoff venv（`P2C_DOCLING_PYTHON`），**不**装进 paper2code `.venv`（torch 很大）
- 首次 Docling 转换约 **7 分钟 / ~1.5GB**；可用旁边的 `paper.docling.md` 或 `P2C_DOCLING_CACHE` 复用
- Marker / MinerU **未接入**（Marker 需 llama-server OCR）
- CLI：`--pdf-backend hybrid|docling|pymupdf`；环境变量 `P2C_PDF_BACKEND`、`P2C_DOCLING_PYTHON`


## 本地上传论文

Dashboard 支持 **文件选择上传**（`.pdf` / `.md` / `.html` / `.zip`）：

1. 选择文件 → 点「上传并生成解析」（一步完成上传 + 解析）
2. 文件保存到 `incoming/<paper_id>/paper.pdf`，其中 `paper_id` = 上传文件名主干（已消毒）；重名则 `xxx_2`、`xxx_3`…
3. 上传后立即解析；产物写入 `outputs/<paper_id>/`（与上传文件夹同名）
4. 上传成功后立即 `POST /api/run`（讲解+复现，默认不含语音）；无需再填路径文本框
5. **删除仅通过网站**：论文卡片上的「删除」会同时删掉该 `paper_id` 的 `incoming/` 与 `outputs/` 整树（`DELETE /api/papers/{paper_id}`）

旧版 `incoming/uploads/<时间戳>_…/` 仍会出现在列表中，可照常打开或删除。

## TTS 语音（SiliconFlow）

- **设置页** `/settings`「TTS 语音」面板：单独的 API Key（与 LLM Key 分离）、预设 SiliconFlow Base URL、可编辑模型
- 环境变量：`P2C_TTS_API_KEY`（以及可选 `P2C_TTS_BASE_URL` / `P2C_TTS_MODEL` / `P2C_TTS_PROVIDER`）
- Key **只在内存**生效，UI **不会**写入 `configs/default.yaml`
- 调用：`POST https://api.siliconflow.cn/v1/audio/speech`（OpenAI 兼容），默认模型 `FunAudioLLM/CosyVoice2-0.5B`
- 播客稿阅读页（`/view?p=.../podcast_script.md`）有「生成语音」按钮；音频落到 `outputs/<paper_id>/explain/podcast.mp3`
- 无 TTS Key 时仍可用 **edge-tts** 作为离线兜底；也可选「自定义 OpenAI 兼容 speech」URL

接口：`POST /api/tts/settings`、`POST /api/tts/generate`、`GET /api/tts/status`（状态不含密钥）

## 播客稿写作导读（全中文）

播客/对白不再做公式问答或图表读法教学，而是引导作者/读者理清：

- 研究目的（Introduction）
- 行文思路
- 实验设计（Methods）
- 结论与不足/改进（Results / Discussion）

课件中的公式页可保留；播客路径独立按上述 brief 生成。

