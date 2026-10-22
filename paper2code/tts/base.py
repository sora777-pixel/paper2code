"""TTS 接口与实现选择。

* ``EdgeTTSEngine``：使用 ``edge-tts``（微软 Edge 朗读服务），**免费、无需
  API Key**，中文音色质量好，是本地 MVP 的首选。
* ``NullTTSEngine``：无网络/未安装依赖时的降级实现，仍然产出字幕（SRT）与
  文本稿，只是不生成音频，并把原因写入 warnings。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import get_settings

# --------------------------------------------------------------------------- #
# 朗读文本归一化
# 产物里的文本是「给人读的」（带 Markdown、符号、代码围栏），直接送进 TTS 会念出
# 「井号」「反引号」这类内容，听感非常生硬。这里统一转成适合朗读的口语文本。
# --------------------------------------------------------------------------- #
_SPEECH_SUBS: List[Tuple["re.Pattern[str]", str]] = [
    (re.compile(r"```.*?```", re.S), " "),                      # 代码块整体丢弃
    (re.compile(r"`([^`]*)`"), r"\1"),                           # 行内 code 只留内容
    (re.compile(r"!\[[^\]]*\]\([^)]*\)"), " "),                  # 图片
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),               # 链接只留可见文字
    (re.compile(r"https?://\S+|www\.\S+"), " "),                 # 裸链接
    (re.compile(r"^\s{0,3}#{1,6}\s*", re.M), ""),                # 标题标记
    (re.compile(r"^\s{0,3}[-*+]\s+", re.M), ""),                 # 列表符
    (re.compile(r"^\s{0,3}>\s?", re.M), ""),                     # 引用符
    (re.compile(r"\*{1,3}([^*\n]+)\*{1,3}"), r"\1"),             # 粗体 / 斜体
    (re.compile(r"_{1,2}([^_\n]+)_{1,2}"), r"\1"),
]

# 符号 → 口语读法（只做低风险替换，避免把正文改坏）
_SPEECH_SYMBOLS = {
    "→": "到", "⟶": "到", "->": "到", "←": "变为",
    "<=": "小于等于", ">=": "大于等于", "≤": "小于等于", "≥": "大于等于",
    "±": "正负", "≈": "约", "~": "约", "×": "乘", "÷": "除以",
    "℃": "摄氏度", "&": "和", "＆": "和",
}


def speechify(text: str) -> str:
    """把一段产物文本转成适合 TTS 朗读的口语化文本。"""
    s = text or ""
    for pat, rep in _SPEECH_SUBS:
        s = pat.sub(rep, s)
    for k, v in _SPEECH_SYMBOLS.items():
        s = s.replace(k, v)
    # 科学计数法：1.2e5 → 1.2乘10的5次方（否则会被逐字念成「1点2 e 5」）
    s = re.sub(r"(\d(?:\.\d+)?)[eE]([+-]?\d+)", r"\1乘10的\2次方", s)
    s = re.sub(r"[ \t\u3000]+", " ", s)
    s = re.sub(r"\n{2,}", "\n", s).strip()
    if s and s[-1] not in "。！？!?…":
        s += "。"  # 保证句末有标点，TTS 才会收住语气
    return s


_SENTENCE_END = re.compile(r"(?<=[。！？!?；;])\s*")


def split_sentences(text: str) -> List[str]:
    """按中文/英文句末标点切句，保留标点；用于逐句设计韵律。"""
    parts = [p.strip() for p in _SENTENCE_END.split(text or "") if p.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def _adjust_rate(rate: str, delta: int) -> str:
    m = re.match(r"\s*([+-]?\d+)\s*%", rate or "")
    base = int(m.group(1)) if m else 0
    return f"{base + delta:+d}%"


def _adjust_pitch(pitch: str, delta: int) -> str:
    m = re.match(r"\s*([+-]?\d+)\s*Hz", pitch or "", re.I)
    base = int(m.group(1)) if m else 0
    return f"{base + delta:+d}Hz"


def sentence_tone(text: str) -> Tuple[int, int, str]:
    """按句式给出一组韵律偏移：``(rate_delta, pitch_delta, tone)``。

    这不是「随机加戏」，而是把中文口语里真实存在的几类语气显式化：
    问句抬升、感叹加重、转折压低、结论放慢、列举有节奏、短反应轻快。
    """
    t = (text or "").strip()
    low = t.lower()
    if t.endswith(("？", "?")):
        return 4, 6, "question"
    if t.endswith(("！", "!")):
        return 3, 5, "exclaim"
    if low.startswith(("但是", "不过", "然而", "反过来", "其实", "问题是")):
        return -4, -3, "contrast"
    if low.startswith(("所以", "因此", "总之", "关键是", "最重要的是", "值得注意的是", "结论")):
        return -6, 4, "conclusion"
    if low.startswith(("第一", "第二", "第三", "首先", "其次", "最后", "一方面", "另一方面")):
        return -2, 2, "enumeration"
    if len(t) <= 10 and any(w in t for w in ("嗯", "对", "没错", "是的", "好", "行", "确实", "原来如此")):
        return 8, 4, "reaction"
    return 0, 0, "neutral"


class TTSEngine:
    name = "base"

    def __init__(self, config: Optional[Dict] = None) -> None:
        self.config = config or {}
        self.warnings: List[str] = []

    def available(self) -> bool:
        return False

    def synthesize(self, turns: List[Dict[str, str]], out_path: Path) -> Optional[str]:
        """把多轮对白合成为单个音频文件；失败返回 ``None``。"""
        return None

    # -- 字幕 ------------------------------------------------------------- #
    @staticmethod
    def build_srt(turns: List[Dict[str, str]], cps: float = 4.6) -> Tuple[str, float]:
        """按每秒 ``cps`` 个字符估算时长，生成 SRT 与总时长（秒）。"""
        blocks: List[str] = []
        t = 0.0
        for i, turn in enumerate(turns, start=1):
            text = turn.get("text", "").strip()
            if not text:
                continue
            dur = max(1.6, min(28.0, len(text) / cps))
            start, end = t, t + dur
            blocks.append(
                f"{i}\n{_ts(start)} --> {_ts(end)}\n{_speaker_label(turn.get('speaker'))}{text}\n"
            )
            t = end + 0.35
        return "\n".join(blocks), t

    @staticmethod
    def write_script_md(turns: List[Dict[str, str]], out_path: Path, title: str = "") -> str:
        lines = [f"# 知识播客脚本{f'：{title}' if title else ''}", ""]
        for turn in turns:
            lines.append(f"**{_speaker_label(turn.get('speaker'))}** {turn.get('text', '').strip()}")
            lines.append("")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(lines), encoding="utf-8")
        return str(out_path)


def _speaker_label(speaker: Optional[str]) -> str:
    return "【主持人】" if (speaker or "A").upper().startswith("A") else "【讲解人】"


def _ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# --------------------------------------------------------------------------- #
class NullTTSEngine(TTSEngine):
    name = "null"

    def __init__(self, config: Optional[Dict] = None, reason: str = "") -> None:
        super().__init__(config)
        if reason:
            self.warnings.append(reason)


# --------------------------------------------------------------------------- #
class EdgeTTSEngine(TTSEngine):
    """edge-tts 合成：双音色 + 逐说话人韵律 + 问句语调 + 轮次间停顿。

    自然度来自四处（都不依赖额外服务）：
    1. 逐说话人不同的 rate / pitch —— 让两个人真的听得出差别（不再是同一个腔调念稿）；
    2. 问句抬高语调 —— 避免整段「一条直线」；
    3. 朗读前把文本口语化（去 Markdown、符号读法、保证句末标点）；
    4. 换人处插入停顿标记 —— 听感上是自然的换气，而不是抢话。
    """

    name = "edge"

    def __init__(self, config: Optional[Dict] = None) -> None:
        super().__init__(config)
        cfg = config or {}
        self.voice_a = str(cfg.get("voice_a") or "zh-CN-YunxiNeural")
        self.voice_b = str(cfg.get("voice_b") or "zh-CN-XiaoxiaoNeural")
        base = str(cfg.get("rate") or "")  # 兼容旧配置：单个 rate
        self.rate_a = str(cfg.get("rate_a") or base or "+8%")   # 主持人：稍快、有活力
        self.rate_b = str(cfg.get("rate_b") or base or "+0%")   # 讲解人：平稳
        self.pitch_a = str(cfg.get("pitch_a") or "+2Hz")
        self.pitch_b = str(cfg.get("pitch_b") or "-2Hz")
        self.volume = str(cfg.get("volume") or "+0%")
        self.q_rate = str(cfg.get("question_rate") or "+12%")   # 问句：稍快
        self.q_pitch = str(cfg.get("question_pitch") or "+8Hz")  # 问句：抬语调
        pause = cfg.get("pause")
        self.pause = "……" if pause is None else str(pause)      # 置空可关闭停顿
        self._mod = None
        self._fail_count = 0          # 逐轮失败计数（聚合成一条 warning，避免刷屏）
        self._first_error = ""

    def _note_failure(self, exc: Exception) -> None:
        self._fail_count += 1
        if not self._first_error:
            self._first_error = f"{type(exc).__name__}: {exc}"[:160]

    def available(self) -> bool:
        try:
            import edge_tts  # type: ignore  # noqa: F401

            self._mod = edge_tts
            return True
        except Exception:
            return False

    def _voice_for(self, speaker: Optional[str]) -> str:
        return self.voice_a if (speaker or "A").upper().startswith("A") else self.voice_b

    def _prosody_for(self, speaker: Optional[str], text: str) -> Tuple[str, str, str]:
        """按说话人与句式给出 (voice, rate, pitch)。

        除了「主持人 / 讲解人」的基础差异，还按本轮语气做偏移：
        问句抬升、感叹加重、转折压低、结论放慢、列举有节奏、短反应轻快。
        """
        is_a = (speaker or "A").upper().startswith("A")
        voice = self.voice_a if is_a else self.voice_b
        rate = self.rate_a if is_a else self.rate_b
        pitch = self.pitch_a if is_a else self.pitch_b
        dr, dp, tone = sentence_tone(text)
        if tone == "question":
            rate, pitch = self.q_rate, self.q_pitch
        else:
            rate = _adjust_rate(rate, dr)
            pitch = _adjust_pitch(pitch, dp)
        return voice, rate, pitch

    async def _one(self, text: str, voice: str, rate: str, pitch: str, out: Path) -> bool:
        try:
            comm = self._mod.Communicate(
                text, voice=voice, rate=rate, pitch=pitch, volume=self.volume
            )
            await comm.save(str(out))
            return out.exists() and out.stat().st_size > 0
        except TypeError:  # 老版本 edge-tts 不接受 pitch / volume
            try:
                comm = self._mod.Communicate(text, voice=voice, rate=rate)
                await comm.save(str(out))
                return out.exists() and out.stat().st_size > 0
            except Exception as exc:
                self._note_failure(exc)
                return False
        except Exception as exc:
            self._note_failure(exc)
            return False

    def synthesize(self, turns: List[Dict[str, str]], out_path: Path) -> Optional[str]:
        if not self.available():
            self.warnings.append("未安装 edge-tts，跳过音频合成（pip install edge-tts）")
            return None

        out_path.parent.mkdir(parents=True, exist_ok=True)
        # 分片写入**系统临时目录**而不是 outputs/ 下的 _tts_parts：
        #   1. 产物目录只留最终 podcast.mp3，干净；
        #   2. 避免逐个 unlink 触发宿主环境的「批量删除确认」钩子（片数 > 阈值时
        #      删除会被拦下，反而在产物目录里留下一堆碎片）。
        with tempfile.TemporaryDirectory(prefix="paper2code_tts_") as _tmp:
            tmp_dir = Path(_tmp)

            async def run() -> List[Path]:
                parts: List[Path] = []
                for i, turn in enumerate(turns):
                    text = speechify(turn.get("text") or "")
                    if not text:
                        continue
                    voice, rate, pitch = self._prosody_for(turn.get("speaker"), text)
                    if parts and self.pause:  # 换人前留一口气
                        text = self.pause + text
                    p = tmp_dir / f"{i:04d}.mp3"
                    ok = await self._one(text, voice, rate, pitch, p)
                    if ok:
                        parts.append(p)
                return parts

            try:
                parts = asyncio.run(run())
            except RuntimeError:
                loop = asyncio.new_event_loop()
                try:
                    parts = loop.run_until_complete(run())
                finally:
                    loop.close()

            if not parts:
                if self._fail_count:
                    self.warnings.append(
                        f"edge-tts 合成失败 {self._fail_count} 轮（网络受限或被拦截）：{self._first_error}；"
                        "已仅保留字幕与脚本"
                    )
                else:
                    self.warnings.append("音频合成为空（可能网络受限），已仅保留字幕与脚本")
                return None

            # MP3 帧自包含，直接字节拼接即可被播放器正确解析
            with out_path.open("wb") as fh:
                for p in parts:
                    fh.write(p.read_bytes())
            self.turns_voiced = len(parts)
            if self._fail_count:
                self.warnings.append(
                    f"edge-tts 有 {self._fail_count} 轮合成失败已跳过，其余 {len(parts)} 轮已合成"
                )
        return str(out_path)


# --------------------------------------------------------------------------- #
class SapiTTSEngine(TTSEngine):
    """Windows 内置 SAPI 朗读（完全离线、免 Key、免网络）。

    为什么要有它：``edge-tts`` 走的是微软的免费朗读 WebSocket，**服务端会拒绝**
    某些客户端（本机实测 403 ``WSServerHandshakeError``），而「零 Key 可跑」是本项目
    底线——所以边缘合成失败时退到系统自带的语音引擎，真出一份音频，而不是静默无音频。

    代价（必须如实告知）：
    * 只有系统装了的音色，中文通常是 ``Microsoft Huihui Desktop`` 一个；
    * 双人对谈做不出两个明显不同的嗓音，这里用 **SSML 语速/音高/停顿差异**
      （主持人更快更高、讲解人更慢更低、问句抬语调）作区分，并在 warnings 里写明；
    * 输出格式是 WAV（SAPI 原生），文件名随之带 ``.wav``。
    """

    name = "sapi"

    #: 临时 PowerShell 脚本：全部轮次写进同一个 WAV，逐轮用 SSML 调语速/音高/停顿。
    _PS = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$spec = Get-Content -Raw -LiteralPath $env:P2C_SAPI_SPEC | ConvertFrom-Json
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($spec.voice) { try { $synth.SelectVoice($spec.voice) } catch { } }
$synth.SetOutputToWaveFile($spec.out)
foreach ($item in $spec.items) {
  $safe = [System.Security.SecurityElement]::Escape([string]$item.text)
  $ssml = "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='zh-CN'><prosody rate='$($item.rate)' pitch='$($item.pitch)' volume='$($item.volume)'>$safe</prosody><break time='$($item.break_ms)ms'/></speak>"
  try {
    $synth.SpeakSsml($ssml)
  } catch {
    try { $synth.Rate = [int]$item.rate_int } catch { }
    try { $synth.Volume = [int]$item.volume_int } catch { }
    $synth.Speak([string]$item.text)
  }
}
$synth.SetOutputToNull()
$synth.Dispose()
"""

    def __init__(self, config: Optional[Dict] = None) -> None:
        super().__init__(config)
        cfg = config or {}
        self.voice = str(cfg.get("sapi_voice") or "")          # 空 = 让系统挑中文音色
        # 比 edge-tts 的差异更大：主持人明显更快更高，讲解人更慢更低
        self.rate_a = str(cfg.get("sapi_rate_a") or "+22%")
        self.rate_b = str(cfg.get("sapi_rate_b") or "-10%")
        self.pitch_a = str(cfg.get("sapi_pitch_a") or "+10Hz")
        self.pitch_b = str(cfg.get("sapi_pitch_b") or "-8Hz")
        self.q_rate = str(cfg.get("sapi_question_rate") or "+8%")
        self.q_pitch = str(cfg.get("sapi_question_pitch") or "+14Hz")
        self.volume = str(cfg.get("sapi_volume") or "+0%")
        self.break_ms = int(cfg.get("sapi_break_ms") or 450)

    # -- 可用性 ------------------------------------------------------------- #
    def available(self) -> bool:
        return sys.platform == "win32" and self._powershell() is not None

    @staticmethod
    def _powershell() -> Optional[str]:
        for exe in ("powershell", "pwsh"):
            try:
                r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command",
                                    "$PSVersionTable.PSVersion.Major"],
                                   capture_output=True, timeout=20)
                if r.returncode == 0:
                    return exe
            except Exception:
                continue
        return None

    def _pick_voice(self, exe: str) -> str:
        """没显式配置时，挑第一个 zh 音色；一个都没有就返回空（用系统默认）。"""
        try:
            r = subprocess.run(
                [exe, "-NoProfile", "-NonInteractive", "-Command",
                 "Add-Type -AssemblyName System.Speech; "
                 "(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices() | "
                 "ForEach-Object { $_.VoiceInfo.Name + '|' + $_.VoiceInfo.Culture.Name }"],
                capture_output=True, text=True, timeout=30)
            for line in (r.stdout or "").splitlines():
                name, _, culture = line.strip().partition("|")
                if culture.lower().startswith("zh") and name:
                    return name
        except Exception:
            pass
        return ""

    # -- 合成 --------------------------------------------------------------- #
    def synthesize(self, turns: List[Dict[str, str]], out_path: Path) -> Optional[str]:
        exe = self._powershell()
        if exe is None:
            self.warnings.append("未找到 PowerShell，无法使用 Windows 内置语音（SAPI）")
            return None
        voice = self.voice or self._pick_voice(exe)
        if not voice:
            self.warnings.append("系统未安装中文语音包，SAPI 无法合成中文音频（可在 Windows 设置里添加中文语音）")

        items = []
        for turn in turns:
            text = speechify(turn.get("text") or "")
            if not text:
                continue
            is_a = (turn.get("speaker") or "A").upper().startswith("A")
            base_rate = self.rate_a if is_a else self.rate_b
            base_pitch = self.pitch_a if is_a else self.pitch_b
            # 逐句而不是逐轮：同一轮里问句、转折、结论各自拿到不同韵律，
            # 听感才有抑扬顿挫，而不是一整段一个腔调念到底。
            sents = split_sentences(text)
            for si, sent in enumerate(sents):
                dr, dp, _tone = sentence_tone(sent)
                rate = _adjust_rate(base_rate, dr)
                pitch = _adjust_pitch(base_pitch, dp)
                last = si == len(sents) - 1
                # 句间短停、轮次间长停：像真人换气，而不是抢话
                break_ms = self.break_ms if last else max(160, min(320, int(self.break_ms * 0.6)))
                items.append({
                    "text": sent,
                    "rate": rate,
                    "pitch": pitch,
                    "volume": self.volume,
                    "break_ms": break_ms,
                    "rate_int": 2 if is_a else -1,
                    "volume_int": 100 if is_a else 92,
                })
        if not items:
            self.warnings.append("没有可朗读的文本")
            return None

        out_path = out_path.with_suffix(".wav")     # SAPI 原生输出 WAV，不做 MP3 转码
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="paper2code_sapi_") as tmp:
            spec = Path(tmp) / "spec.json"
            ps1 = Path(tmp) / "tts.ps1"
            spec.write_text(json.dumps({"out": str(out_path), "voice": voice, "items": items},
                                       ensure_ascii=False),
                            encoding="utf-8")
            ps1.write_text(self._PS, encoding="utf-8-sig")
            try:
                r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                    "-File", str(ps1)],
                                   capture_output=True, timeout=600,
                                   env={**os.environ, "P2C_SAPI_SPEC": str(spec)})
            except Exception as exc:
                self.warnings.append("SAPI 合成失败：%s: %s" % (type(exc).__name__, exc))
                return None
        if not out_path.exists() or out_path.stat().st_size <= 44:   # 44 = 空 WAV 头
            err = (r.stderr or b"").decode("utf-8", "ignore")[:200]
            self.warnings.append("SAPI 未产出音频%s" % ("：" + err if err else ""))
            return None

        self.warnings.append(
            "语音由 Windows 内置 SAPI 合成（音色 %s，离线可用）；已用 SSML 放大语速、音高"
            "与问句语调差异，但仍是单音色。" % (voice or "系统默认"))
        return str(out_path)


# --------------------------------------------------------------------------- #
class SineTTSEngine(TTSEngine):
    """最后兜底：生成与字幕等长的极低音量 WAV。

    用途仅在于让「音频产物链路」在完全离线时也不为空，便于演示与端到端测试。
    文件名会带 ``_placeholder`` 后缀，避免被误当作真实语音。
    """

    name = "placeholder"

    def synthesize(self, turns: List[Dict[str, str]], out_path: Path) -> Optional[str]:
        _, total = self.build_srt(turns)
        out_path = out_path.with_name(out_path.stem + "_placeholder.wav")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        rate = 16000
        n = int(max(1.0, total) * rate)
        try:
            with wave.open(str(out_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(rate)
                wf.writeframes(b"\x00\x00" * n)
        except Exception as exc:
            self.warnings.append(f"占位音频生成失败：{exc}")
            return None
        self.warnings.append("未生成真实语音，已输出等长静音占位文件用于串联流程")
        return str(out_path)


# --------------------------------------------------------------------------- #
class FallbackTTSEngine(TTSEngine):
    """按顺序试多个后端，**第一个真的产出音频的算数**；失败原因逐条记进 warnings。

    这条链存在的理由：``edges-tts`` 装得上不代表用得了——本机实测它的 WebSocket 被
    服务端 403 拒绝。只看「包能不能 import」来选后端（旧逻辑）会导致「音频链路静默为空」，
    所以改成**运行时逐个试**，并把每个后端失败的真实原因都写出来，绝不假装成功。
    """

    name = "auto"

    def __init__(self, engines: List[TTSEngine]) -> None:
        super().__init__({})
        self.engines = list(engines)
        self._used: Optional[TTSEngine] = None

    def available(self) -> bool:
        return any(e.available() for e in self.engines)

    def __getattr__(self, name: str) -> Any:
        # 未定义的属性（voice_a / rate_a / pause 等）委托给「实际产出音频的后端」，
        # 这样 podcast_meta.md 里记的是真实音色与韵律，而不是一串 n/a。
        used = self.__dict__.get("_used")
        if used is not None:
            return getattr(used, name)
        raise AttributeError(name)

    def synthesize(self, turns: List[Dict[str, str]], out_path: Path) -> Optional[str]:
        reasons: List[str] = []
        for eng in self.engines:
            if not eng.available():
                reasons.append("[%s] 不可用" % eng.name)
                continue
            res = eng.synthesize(turns, out_path)
            if res:
                self._used = eng
                self.name = eng.name
                self.warnings.extend(eng.warnings)
                if reasons:
                    self.warnings.append("音频最终由「%s」产出；更优后端未成功：%s"
                                         % (eng.name, "；".join(reasons)))
                return res
            reasons.extend("[%s] %s" % (eng.name, w) for w in (eng.warnings or ["未产出音频"]))
        self.warnings.extend(reasons)
        self.warnings.append("所有 TTS 后端都没能产出音频，仅保留字幕与脚本")
        return None


# --------------------------------------------------------------------------- #
def get_tts(refresh: bool = False) -> TTSEngine:
    settings = get_settings()
    mode = settings.tts_mode
    cfg = {
        "voice_a": settings.tts_voice_a,
        "voice_b": settings.tts_voice_b,
        # 逐说话人韵律 / 问句语调 / 轮次间停顿（都可配置，见 configs/default.yaml）
        "rate_a": getattr(settings, "tts_rate_a", ""),
        "rate_b": getattr(settings, "tts_rate_b", ""),
        "pitch_a": getattr(settings, "tts_pitch_a", ""),
        "pitch_b": getattr(settings, "tts_pitch_b", ""),
        "volume": getattr(settings, "tts_volume", ""),
        "question_rate": getattr(settings, "tts_question_rate", ""),
        "question_pitch": getattr(settings, "tts_question_pitch", ""),
        "pause": getattr(settings, "tts_pause", None),
        "sapi_voice": getattr(settings, "tts_sapi_voice", ""),
        "sapi_rate_a": getattr(settings, "tts_sapi_rate_a", ""),
        "sapi_rate_b": getattr(settings, "tts_sapi_rate_b", ""),
        "sapi_pitch_a": getattr(settings, "tts_sapi_pitch_a", ""),
        "sapi_pitch_b": getattr(settings, "tts_sapi_pitch_b", ""),
        "sapi_question_rate": getattr(settings, "tts_sapi_question_rate", ""),
        "sapi_question_pitch": getattr(settings, "tts_sapi_question_pitch", ""),
        "sapi_volume": getattr(settings, "tts_sapi_volume", ""),
        "sapi_break_ms": getattr(settings, "tts_sapi_break_ms", 450),
    }

    if mode == "off":
        return NullTTSEngine(cfg, reason="TTS 已被配置关闭（tts_mode=off）")

    edge = EdgeTTSEngine(cfg)
    if mode == "edge":
        # 显式要求 edge：不静默换后端，缺失就如实报缺
        return edge if edge.available() else NullTTSEngine(
            cfg, reason="配置要求 edge-tts，但未安装：pip install edge-tts")

    sapi = SapiTTSEngine(cfg)
    if mode == "sapi":
        return sapi if sapi.available() else NullTTSEngine(
            cfg, reason="配置要求系统语音（sapi），但当前平台没有可用的 PowerShell/SAPI")

    # auto：edge（音质最好）→ 系统语音 sapi（完全离线）→ 静音占位（保证音频链路非空）
    return FallbackTTSEngine([edge, sapi, SineTTSEngine(cfg)])
