# SiliconFlow 文本转语音（TTS）模型整理

> 文档日期：2026-09-14  
> 项目：paper2code 播客稿 → 语音  
> 依据：[硅基流动 TTS 用户指南](https://docs.siliconflow.cn/cn/userguide/capabilities/text-to-speech)、[Create Speech API](https://api-docs.siliconflow.cn/docs/api/audio-speech-post)

---

## 1. 先分清：TTS 不是聊天 LLM

SiliconFlow 上的 **DeepSeek / Qwen 等聊天模型不能直接念稿**。文本转语音要走语音合成模型，接口：

```
POST https://api.siliconflow.cn/v1/audio/speech
Authorization: Bearer <SILICONFLOW_API_KEY>
```

计费按输入文本的 UTF-8 字节数。模型列表会变动，以控制台 [Models → Speech](https://cloud.siliconflow.cn/models?types=speech) 为准。

纸面默认建议：

| 场景 | 推荐模型 ID |
|------|-------------|
| 单人念播客稿 | `FunAudioLLM/CosyVoice2-0.5B` |
| 双人播客（`[S1]` / `[S2]` 换人） | `fnlp/MOSS-TTSD-v0.5` |

paper2code 当前「生成语音」先接 SiliconFlow；不填 Key 时回退本机 edge-tts。

---

## 2. 当前主推模型

### 2.1 `FunAudioLLM/CosyVoice2-0.5B`

- 单人朗读、多语种（中 / 英 / 日 / 韩）、方言（粤语、四川话、上海话、郑州、长沙、天津等）
- 情绪与节奏：可用自然语言指令，指令与正文之间加 `<|endofprompt|>`；正文里可用 `[laughter]`、`[breath]`
- 支持系统预置音色、用户上传音色、动态克隆（自定义音色需实名）
- 流式延迟约 150 ms（官方宣传）

**请求要点**

| 字段 | 示例 |
|------|------|
| `model` | `FunAudioLLM/CosyVoice2-0.5B` |
| `voice` | `FunAudioLLM/CosyVoice2-0.5B:alex`（必须带模型前缀） |
| `input` | 要念的中文稿；可加情绪前缀 |
| `response_format` | `mp3`（还可 `wav` / `pcm` / `opus`） |
| `speed` | `1.0`，范围 0.25–4.0 |
| `gain` | `0`，范围 -10–10 |

```json
{
  "model": "FunAudioLLM/CosyVoice2-0.5B",
  "input": "请用平稳的语速朗读。<|endofprompt|>这篇论文想解决的问题是……",
  "voice": "FunAudioLLM/CosyVoice2-0.5B:alex",
  "response_format": "mp3",
  "speed": 1.0
}
```

### 2.2 `fnlp/MOSS-TTSD-v0.5`（播客更合适）

- 中英双语对话合成，专为 **AI 播客 / 双人稿** 设计
- 稿件用 `[S1]`、`[S2]` 标记说话人
- 支持零样本双音色克隆、较长一次合成
- 双人时 **不要用 `voice` 传两个音色**：用 `references` 传两段参考音频（`voice` 与 `references` 互斥）

```json
{
  "model": "fnlp/MOSS-TTSD-v0.5",
  "input": "[S1]这篇工作的研究目的是什么？[S2]作者想用神经网络直接预测分子波函数。[S1]实验是怎么设计的？",
  "voice": "fnlp/MOSS-TTSD-v0.5:alex",
  "response_format": "mp3"
}
```

双人克隆示例（官方）：`input` 带 `[S1]…[S2]…`，`references` 数组两项，各含 `audio`（URL 或 base64）和对应 `text`。

---

## 3. 系统预置音色（8 个）

`voice` 写法：`{模型ID}:{音色名}`。

| 音色 | 性别 | 风格 |
|------|------|------|
| `alex` | 男 | 稳健 |
| `benjamin` | 男 | 低沉 |
| `charles` | 男 | 磁性 |
| `david` | 男 | 活泼 |
| `anna` | 女 | 稳健 |
| `bella` | 女 | 热情 |
| `claire` | 女 | 柔和 |
| `diana` | 女 | 活泼 |

示例：

- `FunAudioLLM/CosyVoice2-0.5B:claire`
- `fnlp/MOSS-TTSD-v0.5:david`

---

## 4. 在 paper2code 里怎么填

Dashboard / 播客页「生成语音」：

1. TTS 来源选 **SiliconFlow**
2. Base URL：`https://api.siliconflow.cn/v1`
3. 模型：上面的模型 ID（推荐播客用 `fnlp/MOSS-TTSD-v0.5`）
4. API Key：硅基流动控制台密钥（只放页面或 `P2C_TTS_API_KEY`，不要提交 git）
5. 音频落到：`outputs/<paper_id>/explain/podcast.mp3`

环境变量：`P2C_TTS_API_KEY`。不要把 Key 写入 `configs/default.yaml` 后提交。

---

## 5. 输出格式与采样率

| `response_format` | 采样率 |
|-------------------|--------|
| `mp3` | 32000 / 44100（默认 44100） |
| `wav` / `pcm` | 8000、16000、24000、32000、44100（默认 44100） |
| `opus` | 仅 48000 |

`input` 不要随便加空格；参考音频建议 8–10 秒、单人、无底噪，小于 30 秒。

---

## 6. 旧模型（不要当默认）

官方「支持的模型」章节目前只写 CosyVoice2 与 MOSS-TTSD。下列 ID 仍出现在旧 API schema / 第三方插件中，且有插件标 **deprecated**，上线前请在 Speech 标签页确认是否还在架：

| 模型 ID | 说明 |
|---------|------|
| `fishaudio/fish-speech-1.5` | 旧 Fish Speech；音色同样 `:alex` 等 |
| `RVC-Boss/GPT-SoVITS` | 旧 GPT-SoVITS |

代码示例注释里仍写「支持 fishaudio / GPT-SoVITS / CosyVoice2」，以控制台在架列表为准。

---

## 7. 自定义音色（可选）

- 上传：`POST https://api.siliconflow.cn/v1/uploads/audio/voice`（需实名）
- 列表：`GET https://api.siliconflow.cn/v1/audio/voice/list`
- 删除：`POST https://api.siliconflow.cn/v1/audio/voice/deletions`
- 返回的 `uri` 形如 `speech:your-voice-name:…`，直接作为 `voice` 使用

---

## 8. 参考链接

- TTS 指南：https://docs.siliconflow.cn/cn/userguide/capabilities/text-to-speech
- Create Speech：https://api-docs.siliconflow.cn/docs/api/audio-speech-post
- 控制台模型（Speech）：https://cloud.siliconflow.cn/models?types=speech
- Key：https://cloud.siliconflow.cn/account/ak
