"""TTS 接口与实现选择。

* ``EdgeTTSEngine``：使用 ``edge-tts``（微软 Edge 朗读服务），**免费、无需
  API Key**，中文音色质量好，是本地 MVP 的首选。
* ``NullTTSEngine``：无网络/未安装依赖时的降级实现，仍然产出字幕（SRT）与
  文本稿，只是不生成音频，并把原因写入 warnings。
"""

from __future__ import annotations

import asyncio
import re
import wave
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..config import get_settings


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
    name = "edge"

    def __init__(self, config: Optional[Dict] = None) -> None:
        super().__init__(config)
        cfg = config or {}
        self.voice_a = str(cfg.get("voice_a") or "zh-CN-YunxiNeural")
        self.voice_b = str(cfg.get("voice_b") or "zh-CN-XiaoxiaoNeural")
        self.rate = str(cfg.get("rate") or "+0%")
        self._mod = None

    def available(self) -> bool:
        try:
            import edge_tts  # type: ignore  # noqa: F401

            self._mod = edge_tts
            return True
        except Exception:
            return False

    def _voice_for(self, speaker: Optional[str]) -> str:
        return self.voice_a if (speaker or "A").upper().startswith("A") else self.voice_b

    async def _one(self, text: str, voice: str, out: Path) -> bool:
        try:
            comm = self._mod.Communicate(text, voice=voice, rate=self.rate)
            await comm.save(str(out))
            return out.exists() and out.stat().st_size > 0
        except Exception as exc:
            self.warnings.append(f"edge-tts 合成失败：{exc}")
            return False

    def synthesize(self, turns: List[Dict[str, str]], out_path: Path) -> Optional[str]:
        if not self.available():
            self.warnings.append("未安装 edge-tts，跳过音频合成（pip install edge-tts）")
            return None

        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = out_path.parent / "_tts_parts"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        async def run() -> List[Path]:
            parts: List[Path] = []
            for i, turn in enumerate(turns):
                text = (turn.get("text") or "").strip()
                if not text:
                    continue
                p = tmp_dir / f"{i:04d}.mp3"
                ok = await self._one(text, self._voice_for(turn.get("speaker")), p)
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
            self.warnings.append("音频合成为空（可能网络受限），已仅保留字幕与脚本")
            return None

        # MP3 帧自包含，直接字节拼接即可被播放器正确解析
        with out_path.open("wb") as fh:
            for p in parts:
                fh.write(p.read_bytes())
        for p in parts:
            try:
                p.unlink()
            except OSError:
                pass
        try:
            tmp_dir.rmdir()
        except OSError:
            pass
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
class OpenAICompatSpeechEngine(TTSEngine):
    """OpenAI-compatible `/v1/audio/speech` (SiliconFlow / custom). Stub-friendly."""

    name = "openai_speech"

    def __init__(self, config=None) -> None:
        super().__init__(config)
        cfg = config or {}
        self.base_url = str(cfg.get("base_url") or "https://api.siliconflow.cn/v1").rstrip("/")
        self.api_key = str(cfg.get("api_key") or "")
        self.model = str(cfg.get("model") or "FunAudioLLM/CosyVoice2-0.5B")
        self.voice_a = str(cfg.get("voice") or cfg.get("voice_a") or f"{self.model}:alex")
        self.voice_b = str(cfg.get("voice_alt") or cfg.get("voice_b") or f"{self.model}:diana")
        self.response_format = str(cfg.get("response_format") or "mp3")
        self.timeout = float(cfg.get("timeout") or 120)

    def available(self) -> bool:
        return bool(self.api_key and self.base_url)

    def _voice_for(self, speaker):
        return self.voice_a if (speaker or "A").upper().startswith("A") else self.voice_b

    def _one(self, text: str, voice: str) -> bytes:
        import json
        import urllib.error
        import urllib.request

        payload = {
            "model": self.model,
            "input": text,
            "voice": voice,
            "response_format": self.response_format,
        }
        # Some SiliconFlow CosyVoice setups require voice like "Model:name"
        req = urllib.request.Request(
            f"{self.base_url}/audio/speech",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as fh:  # noqa: S310
                return fh.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "ignore")[:240]
            # never echo key
            if self.api_key and self.api_key in body:
                body = body.replace(self.api_key, "***")
            raise RuntimeError(f"HTTP {exc.code}: {body}") from exc

    def synthesize(self, turns, out_path: Path):
        if not self.available():
            self.warnings.append("未配置 TTS API Key，跳过云端语音合成")
            return None
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        chunks = []
        for turn in turns:
            text = (turn.get("text") or "").strip()
            if not text:
                continue
            # SiliconFlow input length limits — chunk long turns
            pieces = [text[i : i + 400] for i in range(0, len(text), 400)] or [text]
            voice = self._voice_for(turn.get("speaker"))
            for piece in pieces:
                try:
                    chunks.append(self._one(piece, voice))
                except Exception as exc:
                    self.warnings.append(f"云端 TTS 失败：{exc}")
                    return None
        if not chunks:
            self.warnings.append("云端 TTS 无有效对白")
            return None
        with out_path.open("wb") as fh:
            for c in chunks:
                fh.write(c)
        return str(out_path)


class SiliconFlowTTSEngine(OpenAICompatSpeechEngine):
    name = "siliconflow"

    def __init__(self, config=None) -> None:
        cfg = dict(config or {})
        cfg.setdefault("base_url", "https://api.siliconflow.cn/v1")
        cfg.setdefault("model", "FunAudioLLM/CosyVoice2-0.5B")
        super().__init__(cfg)


# --------------------------------------------------------------------------- #
def get_tts(refresh: bool = False) -> TTSEngine:
    settings = get_settings()
    mode = (settings.tts_mode or "auto").lower()
    provider = (getattr(settings, "tts_provider", None) or "auto").lower()
    cfg = {
        "voice_a": settings.tts_voice_a,
        "voice_b": settings.tts_voice_b,
        "api_key": getattr(settings, "tts_api_key", "") or "",
        "base_url": getattr(settings, "tts_base_url", "") or "https://api.siliconflow.cn/v1",
        "model": getattr(settings, "tts_model", "") or "FunAudioLLM/CosyVoice2-0.5B",
        "voice": getattr(settings, "tts_voice", "") or "",
        "voice_alt": getattr(settings, "tts_voice_alt", "") or "",
    }

    if mode == "off":
        return NullTTSEngine(cfg, reason="TTS 已被配置关闭（tts_mode=off）")

    # Cloud first when key present
    want_cloud = provider in ("siliconflow", "openai_speech", "custom") or (
        provider == "auto" and bool(cfg["api_key"])
    ) or mode in ("siliconflow", "openai_speech")
    if want_cloud and cfg["api_key"]:
        if provider == "openai_speech" or mode == "openai_speech":
            eng = OpenAICompatSpeechEngine(cfg)
        else:
            eng = SiliconFlowTTSEngine(cfg)
        if eng.available():
            return eng

    edge = EdgeTTSEngine(cfg)
    if edge.available() and mode in ("auto", "edge") and provider in ("auto", "edge", ""):
        return edge
    if edge.available() and not cfg["api_key"]:
        return edge

    if mode == "edge":
        return NullTTSEngine(cfg, reason="配置要求 edge-tts，但未安装：pip install edge-tts")
    if want_cloud and not cfg["api_key"]:
        # fall back to edge if possible
        if edge.available():
            return edge
        return NullTTSEngine(cfg, reason="未配置 P2C_TTS_API_KEY，且 edge-tts 不可用")
    return SineTTSEngine(cfg)
