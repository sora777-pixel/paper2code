# paper2code

把学术论文变成**可讲的课件 + 播客稿 + 可跑的复现实验**的本地学习工具。

支持 PDF / Markdown / HTML 上传，一键生成：

- 中文讲解课件（HTML）
- 双人知识播客脚本与字幕（可接 SiliconFlow / edge-tts 语音）
- 表格重绘 / 伪代码执行等轻量复现报告

## 快速开始（Windows，端口 8010）

```bat
set PYTHONUTF8=1
cd /d <本仓库根目录>
.venv\Scripts\python.exe -m paper2code serve --host 127.0.0.1 --port 8010
```

或双击 `start_demo.bat`。浏览器打开：<http://127.0.0.1:8010>

- 首页：选择 PDF → **上传并生成解析**
- 设置页：配置 LLM / TTS；密钥写入 `configs/api_keys.yaml` 后会记住，不必每次粘贴
- 详情页：打开课件、播客稿、复现报告；可生成播客音频

首次使用：复制 `configs/api_keys.yaml.example` 为 `configs/api_keys.yaml`，填入 DeepSeek / SiliconFlow 等 Key。该文件已被 gitignore，**不要提交到 GitHub**。

## 本次更新（2026-09-15）

- **专用密钥文件**：`configs/api_keys.yaml` 启动时自动加载；设置页保存/测试成功后回写；界面只显示脱敏 `••••last4`，留空表示沿用已保存 Key。
- **上传即可解析**：默认 PDF 后端改为 **pymupdf**（秒级），避免 Docling hybrid 卡死；`POST /go` 原生表单与文件 `change` 均可启动流水线；修复仪表盘 JS 字符串被 Python 三引号拆断导致按钮无响应。
- **课件封面重排**：第一页不再用空的白色要点框（空 `<li>` 会溢出渐变底）；改为作者 + 一句导读，长标题截断在画面内。
- **课件末页填内容**：最后一页固定为「结论、展望与不足」，从论文 Conclusion / Discussion / 展望 / 局限 / 数据可用性等末尾段落抽取要点。
- **超长文件名上传**：Windows 路径上限下，长标题 PDF 不再截断掉 `.pdf`；论文 ID 最长 72 字符并附短哈希，文件一律存为 `incoming/<id>/paper.pdf`。
- **离线与仓库克隆**：`allow_network=false` 时跳过 git clone；Docling / pdfplumber 增加超时，解析失败可降级。

样例产物见 `test/`（一份已跑通的 PDF + 课件 / 播客稿 / 复现报告；音频 `*.mp3` 不入库）。

## 需要改进

- **PDF 结构抽取仍然偏弱**：不少论文 `sections` 为空、标题被截断、作者行夹杂 `authors:` 脏数据、摘要抽不到；末页与封面只能从 `raw_text` 启发式补全。
- **课件质量依赖 LLM**：中间页仍可能空泛或只盯表格数字；英文论文的结论页目前多为英文原句，缺少稳定中文改写。
- **Docling hybrid 未作为默认真源**：精度更高但首次/无缓存时可能数分钟；超时与子进程路径仍绑在本机 bakeoff 环境，换机器需改 `start_demo.bat`。
- **复现链路偏浅**：表格常只有 caption、对标数据不全；无代码论文的「可跑实验」仍是轻量占位。
- **工程债**：`start_demo.bat` 写死了本机 Python / Docling 路径；改代码必须重启服务才生效；缺少自动测试与 CI；超长历史 `incoming/<102字符id>/` 目录仍偏长。

## 文档

- [项目改进点（完整改进清单）](调研报告/项目改进点.md)
- [演示手册 DEMO.md](DEMO.md)
- [调研报告](调研报告/调研报告.md)
- [PDF 解析改进方案](调研报告/PDF解析改进方案.md)
- [SiliconFlow TTS](调研报告/SiliconFlow-TTS模型.md)

## 关键能力

- PDF 解析：默认 **pymupdf**；可选 `P2C_PDF_BACKEND=hybrid|docling`（Docling + PyMuPDF，可降级）
- LLM：offline 规则引擎 / DeepSeek / OpenRouter / SiliconFlow / 任意 OpenAI 兼容端点
- 远程 LLM 失败时 **FallbackProvider + Offline** 仍写出课件
- 本地上传：`incoming/<paper_id>/paper.pdf`，支持中文与超长英文文件名
- Web 删除论文、卡片状态、解析失败红色错误提示
- TTS：SiliconFlow CosyVoice 或 edge-tts；设置页可测连通性

## 目录约定

```
incoming/<paper_id>/paper.pdf   # 上传包（运行时目录，不入库）
outputs/<paper_id>/explain/     # 课件 + 播客（运行时目录，不入库）
outputs/<paper_id>/reproduce/   # 复现报告（运行时目录，不入库）
test/                           # 一份已生成的样例产物
configs/api_keys.yaml           # 专用 API Key（git 忽略；从 api_keys.yaml.example 复制）
configs/default.yaml            # 默认配置（勿把密钥提交到公开仓库）
```

## 说明

- API Key 请写在 `configs/api_keys.yaml`（已 gitignore；模板见 `configs/api_keys.yaml.example`），也可仅存运行时内存。**接口响应不会回显密钥**。留空 Key 时离线模式仍可上传 PDF 并生成课件 / 播客稿 / 复现报告。
- 改代码后请重启 `serve --port 8010`。
- 默认不要把 `incoming/`、`outputs/`、真实 Key 推送到公开仓库。
