"""知识播客生成：对白稿 → Markdown 脚本 + SRT 字幕 + 合成音频。"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from ..models import Paper
from ..tts import get_tts
from ..tts.base import TTSEngine


def build_podcast(
    paper: Paper,
    turns: List[Dict[str, str]],
    out_dir: Path,
    audio: bool = True,
) -> Dict[str, Optional[str]]:
    """生成播客三件套，返回 ``{script, srt, audio}``。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    engine: TTSEngine = get_tts()

    script_path = out_dir / "podcast_script.md"
    engine.write_script_md(turns, script_path, title=paper.title)

    srt_text, total = engine.build_srt(turns)
    srt_path = out_dir / "podcast.srt"
    srt_path.write_text(srt_text, encoding="utf-8")

    audio_path: Optional[str] = None
    if audio:
        audio_path = engine.synthesize(turns, out_dir / "podcast.mp3")

    # 元信息放在合成**之后**：engine.name / 音色这些要说「实际用的是谁」，
    # 而 auto 链要等真的合成完才知道落到哪个后端（edge → sapi → 占位）。
    meta_path = out_dir / "podcast_meta.md"
    meta_path.write_text(
        f"""# 播客元信息

- 论文：{paper.title}
- 轮次：{len(turns)} 轮（主持人 / 讲解人 双人对谈）
- 估算时长：约 {total / 60:.1f} 分钟
- 字幕字符/秒：4.6（用于时长估算）
- 音色：主持人 {getattr(engine, 'voice_a', 'n/a')} / 讲解人 {getattr(engine, 'voice_b', 'n/a')}
- 韵律：主持人 rate {getattr(engine, 'rate_a', 'n/a')} pitch {getattr(engine, 'pitch_a', 'n/a')}；
  讲解人 rate {getattr(engine, 'rate_b', 'n/a')} pitch {getattr(engine, 'pitch_b', 'n/a')}；
  问句加大 pitch {getattr(engine, 'q_pitch', 'n/a')}
- 逐句语气：问句抬升、感叹加重、转折压低、结论放慢、列举有节奏、短反应轻快；
  同一轮内逐句调整语速/音高，句间短停、轮次间长停
- 轮次间停顿：{getattr(engine, 'pause', 'n/a')!r}
- 合成引擎：{engine.name}
- 音频产物：{audio_path or '（本次没有生成音频，原因见 manifest.warnings）'}
""",
        encoding="utf-8",
    )

    return {
        "script": str(script_path),
        "srt": str(srt_path),
        "audio": audio_path,
        "meta": str(meta_path),
        "engine": engine.name,
        "warnings": engine.warnings,
        "duration_sec": round(total, 1),
    }
