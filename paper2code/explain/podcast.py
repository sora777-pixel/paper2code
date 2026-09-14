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

    meta_path = out_dir / "podcast_meta.md"
    meta_path.write_text(
        f"""# 播客元信息

- 论文：{paper.title}
- 轮次：{len(turns)} 轮（主持人 / 讲解人 双人对谈）
- 估算时长：约 {total / 60:.1f} 分钟
- 字幕字符/秒：4.6（用于时长估算）
- 音色：主持人 {getattr(engine, 'voice_a', 'n/a')} / 讲解人 {getattr(engine, 'voice_b', 'n/a')}
- 合成引擎：{engine.name}
""",
        encoding="utf-8",
    )

    audio_path: Optional[str] = None
    if audio:
        audio_path = engine.synthesize(turns, out_dir / "podcast.mp3")

    return {
        "script": str(script_path),
        "srt": str(srt_path),
        "audio": audio_path,
        "meta": str(meta_path),
        "engine": engine.name,
        "warnings": engine.warnings,
        "duration_sec": round(total, 1),
    }
