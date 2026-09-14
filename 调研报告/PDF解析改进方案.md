# paper2code PDF 解析功能改进方案

> 文档日期：2026-09-14  
> 样例论文：SchNOrb（arXiv 2005.06979），路径 `incoming\schnorb_qao_2020`  
> 依据：当日 bakeoff 结果 + 当前 hybrid 接线现状

---

## 1. 背景与目标

### 1.1 业务目标

paper2code 从学术 PDF 抽出结构化内容，供下游课件/代码生成使用。解析需按学术排版准确抽出：

| 字段 | 要求 |
|------|------|
| 标题 / 作者 | 完整、可读（含变音符号时尽量正确） |
| 分节 | 层级与编号与原文一致 |
| 图表 | 图文件可落盘；题注可关联 |
| 表格 | 真实单元格网格（非纯文本糊成一块） |
| 公式 | **带编号**，且正文中有解释句；课件中文讲解可引用编号 |

开源仓库识别：能判 **Mode A**（存在可克隆的官方/论文关联 GitHub 等）。

### 1.2 样例与验收锚点

- PDF / 目录：`incoming\schnorb_qao_2020`（SchNOrb）
- 期望：title 完整、分节齐全、TABLE I 等有 cell grid、编号公式可被讲解引用、Mode A + SchNOrb GitHub

### 1.3 非目标（本节不展开）

见第 7 节：不改 handbook-ai-tutor；不强制 CUDA / llama-server。

---

## 2. 现状（已落地）

### 2.1 默认后端：hybrid

当前默认 `P2C_PDF_BACKEND=hybrid`，职责拆分如下：

| 通道 | 负责内容 |
|------|----------|
| **Docling**（bakeoff venv） | 标题、分节、`TABLE` 单元格网格 |
| **PyMuPDF** | 公式编号 + 正文解释句、图文件落盘、`code_refs` |

环境变量：

- `P2C_PDF_BACKEND`：`hybrid` | `docling` | `pymupdf`
- `P2C_DOCLING_PYTHON` → `C:\Users\lenovo\paper2code-xfer\pdf-bakeoff\.venv\Scripts\python.exe`

关键文件（主仓）：

- `docling_backend.py` — 主进程侧调用与合并
- `_docling_convert.py` — bakeoff Python 侧转换入口
- 缓存产物：`paper.docling.md`（同论文目录旁/约定缓存路径）

### 2.2 Bakeoff（2026-09-14）摘要

| 引擎 | 版本/状态 | 结构 | 表格 | 公式 | 备注 |
|------|-----------|------|------|------|------|
| Docling | 2.126.0 | 强 | 好 | 弱 | `formula-not-decoded` ×34 |
| Marker | 需 llama-server | — | — | — | 未作为默认；依赖重 |
| MinerU | 未装 | — | — | — | 内存成本高，不默认接 |
| Nougat | 未装 | — | — | — | 不装 |

结论：Docling 适合结构与表；公式通道继续靠 PyMuPDF；Marker/MinerU 不进默认路径。

### 2.3 SchNOrb 复跑结果（hybrid）

- 标题：完整命中
- 分节：13 sections
- 表：5 张 Docling 表；**TABLE I** 为 **11×5** 单元格网格
- 公式编号（PyMuPDF 侧）：1 / 5 / 7 / 12 / 21 / 23 等
- 仓库模式：**Mode A**（关联 SchNOrb GitHub）

说明：Docling 首次转换约 **~7 min / ~1.5 GB** 峰值内存；有缓存后明显快。

---

## 3. 仍存在的问题

1. **公式无真 LaTeX**：Docling 输出多为 `<!-- formula-not-decoded -->` 占位，不能直接当可编译公式。
2. **公式表达式 ↔ 编号对齐仍粗糙**：编号与邻近数学 span / 「Eq. n」解释句的配对规则偏启发式，偶发错配或漏配。
3. **复杂多栏表偶发错列**：pipe-table → 内部 `Paper.Table` 时，合并单元格/多栏头可能列错位。
4. **图与 FIG. 题注关联弱**：图文件能抽，但 caption（FIG. n）按页/顺序配对不稳定。
5. **首次 Docling 成本高**：~7 min、~1.5 GB；对交互式流水线不友好（依赖缓存）。
6. **双 venv 运维成本**：Docling 在 `pdf-bakeoff\.venv`，与 paper2code 主 `.venv` 分离；路径靠 `P2C_DOCLING_PYTHON` 绑定。
7. **作者变音符号编码噪声**：部分姓名/元数据在 UTF-8 边界上仍可能出现乱码或替换字符。

---

## 4. 改进路线（P0 / P1 / P2）

### P0（建议先做，1–2 天）

**目标**：稳定 hybrid 行为、可测、Docling 失败可回退。

1. **公式通道加固（PyMuPDF）**
   - 以 span 级文本 + 邻近句匹配「Eq. n / (n) / Equation n」为主通道。
   - 过滤误抽：过短碎片、页眉页脚、纯数字噪声。
   - 与课件中文讲解约定对齐：讲解文案引用「公式 (n)」时，解析侧保证 `formulas[].number` 可对齐。

2. **Docling 缓存策略写清**
   - 约定缓存文件：`paper.docling.md`（及必要 sidecar，若有）。
   - 环境变量：`P2C_DOCLING_CACHE`（on/off 或目录覆盖，按现有实现补文档）。
   - **失败自动回退**：Docling 进程失败 / 超时 / 空结构 → 整篇或按字段回退 `pymupdf`，保证流水线可跑通。

3. **合并策略单测**
   - 覆盖 `title` / `sections` / `tables` / `formulas` 的字段优先级（hybrid 合并规则）。
   - Fixture：SchNOrb 相关最小样例或 golden JSON 片段，防止回归时 Docling/PyMuPDF 互相覆盖错字段。

### P1（质量，跟进 3–5 天量级）

1. **表格**
   - Docling pipe-table → `Paper.Table` 单元格规范化（行列一致、空单元格保留）。
   - caption 与表体对齐：`TABLE I` / `TABLE II` 等。
   - 丢弃垃圾表：无 grid、列数异常、明显页眉噪声表。

2. **图**
   - `FIG.` / `Figure` 题注与 image 按 **页码 + 出现顺序** 配对；无法配对时显式 `caption=None`，避免错挂。

3. **可选公式回填**
   - 对 Docling 中的 `<!-- formula-not-decoded -->`，用同页 bbox 再抽一次 PyMuPDF 数学 span，填回表达式文本（仍非完整 LaTeX，但优于占位符）。

### P2（可选增强，按需）

1. **Marker OCR**：仅当用户本机已提供可用 `llama-server` 时再评估；不写入默认安装/默认 backend。
2. **MinerU**：不默认接入（内存与运维成本）；专项需求再单独立项。
3. **真 LaTeX**：评估轻量公式识别模型，或 Mathpix 类 API（需密钥）。**离线优先则不做 API**；不以「真 LaTeX」阻塞 Mode A / 表 / 编号公式主路径。

---

## 5. 取舍说明

### 5.1 后端对比

| 后端 | 结构 | 表格子 | 公式 | 成本 | 建议 |
|------|------|--------|------|------|------|
| Docling | 优 | 优 | 差（占位） | 首次高、有缓存可接受 | 结构/表主力；独立 venv |
| PyMuPDF | 中 | 弱 | 中（编号+邻近句） | 低 | 公式/图/`code_refs` 主力 |
| Marker | 潜在优 | 潜在优 | 依赖 OCR 栈 | 需 llama-server | 用户自备服务后再评 |
| MinerU | 潜在优 | 潜在优 | 视版本 | 内存高 | 不默认 |
| **hybrid** | **优（合）** | **优（合）** | **中（合）** | 中（可缓存） | **默认** |

### 5.2 结论

- **继续以 hybrid 为默认**（`P2C_PDF_BACKEND=hybrid`）。
- **不把 Docling 打进 paper2code 主 `.venv`**：保持 `P2C_DOCLING_PYTHON` 指向 bakeoff venv，降低主环境冲突面。
- 公式以「编号 + 正文解释可引用」为产品验收线；真 LaTeX 列为 P2，不阻塞本迭代。

---

## 6. 验收标准（SchNOrb）

对 `incoming\schnorb_qao_2020` 跑通 hybrid（及回退路径）后，应满足：

| # | 标准 | 说明 |
|---|------|------|
| 1 | 标题全文命中 | 与 SchNOrb 论文题一致，无截断 |
| 2 | Mode A + SchNOrb GitHub | 开源仓库判 Mode A，链接可用 |
| 3 | ≥1 表有真实 cell grid | 至少 TABLE I 保留行列单元格（参考 11×5） |
| 4 | 编号公式 + 讲解引用 | 抽出若干「正文有解释」的编号公式；课件中文讲解引用这些编号 |
| 5 | Docling 不可用仍可跑 | 关掉 Docling / 错 `P2C_DOCLING_PYTHON` 时，回退 `pymupdf` 整篇可完成 |

回归建议：P0 合并单测 + 上述 5 条手工/脚本检查清单。

---

## 7. 不在本次范围

- **不修改** `handbook-ai-tutor` 仓库或与其耦合的接口约定（除非双方另开任务）。
- **不强制**安装 CUDA、不强制部署 `llama-server`；Marker 等仅作可选评估。
- 不将 MinerU / Nougat 纳入默认依赖安装。
- 不以商用公式 API 为默认必选项（见 P2）。

---

## 附录 A：关键路径速查

```
paper2code/
  调研报告/PDF解析改进方案.md       ← 本文档
  …/docling_backend.py
  …/_docling_convert.py
incoming/schnorb_qao_2020/         ← 样例
C:\Users\lenovo\paper2code-xfer\pdf-bakeoff\.venv\Scripts\python.exe
                                   ← P2C_DOCLING_PYTHON
```

环境变量速查：`P2C_PDF_BACKEND`、`P2C_DOCLING_PYTHON`、`P2C_DOCLING_CACHE`。

## 附录 B：建议实施顺序

1. P0 公式过滤与 Eq. 邻近句规则 + 单测  
2. P0 缓存文档化 + 失败回退  
3. P1 表 caption/grid 规范化 + 垃圾表丢弃  
4. P1 图–题注配对  
5. P1（可选）formula-not-decoded 页内回填  
6. P2 按需评估 Marker / 真 LaTeX  

---

*本文档为工程改进方案，以可落地项与 SchNOrb 验收为准；实现细节以仓库内代码与单测为准。*
