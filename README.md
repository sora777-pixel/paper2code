# paper2code

把学术论文变成**可讲的课件 + 播客稿 + 可跑的复现实验**的本地学习工具。

支持 PDF / Markdown / HTML 上传，一键生成：

- 中文讲解课件（HTML）
- 双人知识播客脚本与字幕（可接 SiliconFlow / edge-tts 语音）
- 表格重绘 / 伪代码执行等轻量复现报告

## 快速开始（Windows，端口 8010）

```bat
set PYTHONUTF8=1
cd /d E:\cursor-ai-demo\paper2code
.venv\Scripts\python.exe -m paper2code serve --port 8010
```

浏览器打开：<http://127.0.0.1:8010>

- 首页：选择 PDF → **上传并生成解析**
- 设置页：配置 LLM / TTS，并可用 **测试连接**
- 详情页：打开课件、播客稿、复现报告；可生成播客音频

也可使用 `start_demo.bat`（见 `DEMO.md`）。

## 文档

- [项目改进点（完整改进清单）](调研报告/项目改进点.md)
- [演示手册 DEMO.md](DEMO.md)
- [调研报告](调研报告/调研报告.md)
- [PDF 解析改进方案](调研报告/PDF解析改进方案.md)
- [SiliconFlow TTS](调研报告/SiliconFlow-TTS模型.md)

## 关键能力

- PDF **hybrid** 解析（Docling + PyMuPDF，可降级）
- LLM：offline 规则引擎 / DeepSeek / OpenRouter / SiliconFlow / 任意 OpenAI 兼容端点
- 远程 LLM 失败时 **FallbackProvider + Offline** 仍写出课件
- 本地上传按文件名 stem 建 `incoming/<id>/`，支持中文文件名
- Web 删除论文、卡片状态、解析失败红色错误提示
- TTS：SiliconFlow CosyVoice 或 edge-tts；设置页可测连通性

## 目录约定

```
incoming/<paper_id>/paper.pdf   # 上传包
outputs/<paper_id>/explain/     # 课件 + 播客
outputs/<paper_id>/reproduce/   # 复现报告
configs/default.yaml            # 默认配置（勿把密钥提交到公开仓库）
```

## 说明

- API Key 仅存运行时内存或本地配置，**接口响应不会回显密钥**。
- 改代码后请重启 `serve --port 8010`。
- 本仓库文档与演示路径以本机 `E:\cursor-ai-demo\paper2code` 为准。
