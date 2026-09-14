"""运行配置。

优先级：显式参数 > 环境变量 > ``configs/default.yaml``（若存在）> 内置默认值。
YAML 为可选依赖，缺失时静默跳过。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
API_KEYS_FILE = ROOT / "configs" / "api_keys.yaml"
API_KEYS_EXAMPLE = ROOT / "configs" / "api_keys.yaml.example"

_SOURCE_LABEL = {
    "file": "configs/api_keys.yaml",
    "env": "环境变量",
    "memory": "本进程内存",
}


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore
    except Exception:
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:
        return {}


@dataclass
class Settings:
    """全局设置。"""

    workspace: Path = field(default_factory=lambda: ROOT)
    output_dir: Path = field(default_factory=lambda: ROOT / "outputs")
    samples_dir: Path = field(default_factory=lambda: ROOT / "samples")
    work_dir: Path = field(default_factory=lambda: ROOT / ".work")

    # -- LLM --------------------------------------------------------------- #
    llm_mode: str = "offline"  # offline | openai
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout: int = 120
    llm_temperature: float = 0.3

    # -- 语音 -------------------------------------------------------------- #
    tts_mode: str = "auto"  # auto | edge | siliconflow | openai_speech | off
    tts_voice_a: str = "zh-CN-YunxiNeural"
    tts_voice_b: str = "zh-CN-XiaoxiaoNeural"
    tts_provider: str = "auto"  # auto | siliconflow | edge | openai_speech | custom
    tts_api_key: str = ""
    tts_base_url: str = "https://api.siliconflow.cn/v1"
    tts_model: str = "FunAudioLLM/CosyVoice2-0.5B"
    tts_voice: str = "FunAudioLLM/CosyVoice2-0.5B:alex"
    tts_voice_alt: str = "FunAudioLLM/CosyVoice2-0.5B:diana"

    # -- 复现约束（单机 / 小数据集） ---------------------------------------- #
    max_records: int = 10_000
    exec_timeout: int = 120
    allow_network: bool = False
    rel_tolerance: float = 0.05   # 相对误差 <=5% 记为 pass
    rel_warn: float = 0.20        # <=20% 记为 warn，超过为 fail
    random_seed: int = 0

    # -- 讲解 -------------------------------------------------------------- #
    max_slides: int = 14
    style: str = "teaching"  # teaching | executive | exam

    extra: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls, config_file: Optional[Path] = None, **overrides: Any) -> "Settings":
        data: Dict[str, Any] = {}
        data.update(_load_yaml(ROOT / "configs" / "default.yaml"))
        # 专用密钥文件 configs/api_keys.yaml（不入库）：高于 default.yaml，低于环境变量。
        keys_dir = ROOT / "configs"
        if not API_KEYS_FILE.exists() and not (keys_dir / "api_keys.yml").exists():
            if API_KEYS_EXAMPLE.exists():
                try:
                    API_KEYS_FILE.write_text(API_KEYS_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
                except OSError:
                    pass
        for _secret_name in ("api_keys.yaml", "api_keys.yml", "secrets.local.yaml"):
            _secret_path = keys_dir / _secret_name
            if _secret_path.exists():
                data.update(_load_yaml(_secret_path))
                break
        if config_file:
            data.update(_load_yaml(Path(config_file)))

        inst = cls()
        for key, value in data.items():
            if hasattr(inst, key):
                setattr(inst, key, value)

        # 环境变量
        if os.environ.get("P2C_LLM_MODE"):
            inst.llm_mode = os.environ["P2C_LLM_MODE"]
        if os.environ.get("OPENAI_API_KEY"):
            inst.llm_api_key = os.environ["OPENAI_API_KEY"]
        if os.environ.get("P2C_LLM_API_KEY"):
            inst.llm_api_key = os.environ["P2C_LLM_API_KEY"]
        if os.environ.get("P2C_LLM_BASE_URL"):
            inst.llm_base_url = os.environ["P2C_LLM_BASE_URL"]
        if os.environ.get("P2C_LLM_MODEL"):
            inst.llm_model = os.environ["P2C_LLM_MODEL"]
        if os.environ.get("P2C_TTS_MODE"):
            inst.tts_mode = os.environ["P2C_TTS_MODE"]
        if os.environ.get("P2C_TTS_API_KEY"):
            inst.tts_api_key = os.environ["P2C_TTS_API_KEY"]
        if os.environ.get("P2C_TTS_BASE_URL"):
            inst.tts_base_url = os.environ["P2C_TTS_BASE_URL"]
        if os.environ.get("P2C_TTS_MODEL"):
            inst.tts_model = os.environ["P2C_TTS_MODEL"]
        if os.environ.get("P2C_TTS_PROVIDER"):
            inst.tts_provider = os.environ["P2C_TTS_PROVIDER"]

        for key, value in overrides.items():
            if value is None:
                continue
            if hasattr(inst, key):
                setattr(inst, key, value)

        # 若提供了 key 而模式仍为 offline，则自动升级到 openai
        if inst.llm_mode == "offline" and inst.llm_api_key:
            inst.llm_mode = "openai"

        bu = str(inst.llm_base_url or "").rstrip("/")
        if bu in ("https://api.deepseek.com", "http://api.deepseek.com"):
            inst.llm_base_url = bu + "/v1"

        # 路径统一为 Path
        for name in ("workspace", "output_dir", "samples_dir", "work_dir"):
            setattr(inst, name, Path(getattr(inst, name)))
        return inst

    # ------------------------------------------------------------------ #
    def ensure_dirs(self) -> "Settings":
        for name in ("output_dir", "work_dir"):
            Path(getattr(self, name)).mkdir(parents=True, exist_ok=True)
        return self

    def paper_dir(self, paper_id: str) -> Path:
        d = self.output_dir / paper_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def llm_config(self) -> Dict[str, Any]:
        return {
            "mode": self.llm_mode,
            "base_url": self.llm_base_url,
            "api_key": self.llm_api_key,
            "model": self.llm_model,
            "timeout": self.llm_timeout,
            "temperature": self.llm_temperature,
        }

    def scale_guard(self) -> Dict[str, Any]:
        return {
            "max_records": self.max_records,
            "exec_timeout": self.exec_timeout,
            "allow_network": self.allow_network,
            "random_seed": self.random_seed,
        }

    def quality_thresholds(self) -> Dict[str, float]:
        return {"pass": self.rel_tolerance, "warn": self.rel_warn}

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for k, v in self.__dict__.items():
            out[k] = str(v) if isinstance(v, Path) else v
        out["llm_api_key"] = "***" if self.llm_api_key else ""
        out["tts_api_key"] = "***" if self.tts_api_key else ""
        return out


_settings: Optional[Settings] = None


def get_settings(refresh: bool = False, **overrides: Any) -> Settings:
    """进程级单例。"""
    global _settings
    if _settings is None or refresh or overrides:
        _settings = Settings.load(**overrides)
    return _settings


def set_settings(settings: Settings) -> None:
    global _settings
    _settings = settings


def _key_hint(secret: str) -> str:
    """Mask a secret as ••••last4. Never returns the raw key."""
    s = str(secret or "").strip()
    if not s:
        return ""
    tail = re.sub(r"[^A-Za-z0-9]", "", s)[-4:]
    return f"••••{tail}" if tail else "已保存"


def _file_secret(field: str) -> str:
    data = _load_yaml(API_KEYS_FILE)
    if not data and (ROOT / "configs" / "api_keys.yml").exists():
        data = _load_yaml(ROOT / "configs" / "api_keys.yml")
    return str(data.get(field) or "").strip()


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return json.dumps("" if value is None else str(value), ensure_ascii=False)


def persist_api_keys(updates: Dict[str, Any]) -> Path:
    """Merge fields into configs/api_keys.yaml (gitignored). Empty values skipped."""
    clean: Dict[str, Any] = {}
    for key, raw in (updates or {}).items():
        if raw is None:
            continue
        val = str(raw).strip()
        if not val:
            continue
        if key == "tts_base_url":
            val = val.rstrip("/")
            if val.endswith("/audio/speech"):
                val = val[: -len("/audio/speech")].rstrip("/")
        if key == "llm_base_url":
            val = val.rstrip("/")
            if val in ("https://api.deepseek.com", "http://api.deepseek.com"):
                val = val + "/v1"
        clean[key] = val
    if not clean:
        return API_KEYS_FILE

    API_KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    text = ""
    if API_KEYS_FILE.exists():
        text = API_KEYS_FILE.read_text(encoding="utf-8")
    elif API_KEYS_EXAMPLE.exists():
        text = API_KEYS_EXAMPLE.read_text(encoding="utf-8")
    if not text.strip():
        text = "# paper2code 专用密钥文件（请勿提交）\n"

    found: set = set()
    out_lines: List[str] = []
    for line in text.splitlines():
        m = re.match(r"^(\s*)([A-Za-z_][\w]*)\s*:", line)
        if m:
            name = m.group(2)
            if name in clean:
                indent = m.group(1)
                comment = ""
                rest = line.split(":", 1)[1]
                hash_at = rest.find(" #")
                if hash_at >= 0:
                    comment = rest[hash_at:]
                out_lines.append(f"{indent}{name}: {_yaml_scalar(clean[name])}{comment}")
                found.add(name)
                continue
        out_lines.append(line)
    missing = [k for k in clean if k not in found]
    if missing:
        if out_lines and out_lines[-1].strip():
            out_lines.append("")
        for name in missing:
            out_lines.append(f"{name}: {_yaml_scalar(clean[name])}")
    API_KEYS_FILE.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return API_KEYS_FILE


def _llm_key_source(st: Settings) -> str:
    if not st.llm_api_key:
        return ""
    if os.environ.get("P2C_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY"):
        return "env"
    if _file_secret("llm_api_key"):
        return "file"
    return "memory"


def _tts_key_source(st: Settings) -> str:
    if not st.tts_api_key:
        return ""
    if os.environ.get("P2C_TTS_API_KEY"):
        return "env"
    if _file_secret("tts_api_key"):
        return "file"
    return "memory"


def apply_llm_runtime(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    mode: Optional[str] = None,
    persist: bool = False,
) -> Settings:
    """Update live Settings. Empty strings are ignored.

    If persist=True, write non-empty fields into configs/api_keys.yaml so the
    next restart (and later visits) reuse the key without re-typing.
    """
    st = get_settings()
    if api_key is not None and str(api_key).strip():
        st.llm_api_key = str(api_key).strip()
    if base_url is not None and str(base_url).strip():
        st.llm_base_url = str(base_url).strip().rstrip("/")
    # DeepSeek OpenAI-compat needs /v1; bare host would 404/flake on /chat/completions
    bu = (st.llm_base_url or "").rstrip("/")
    if bu in ("https://api.deepseek.com", "http://api.deepseek.com"):
        st.llm_base_url = bu + "/v1"
    if model is not None and str(model).strip():
        st.llm_model = str(model).strip()
    forced_mode = None
    if mode is not None and str(mode).strip():
        forced_mode = str(mode).strip()
        st.llm_mode = forced_mode
    # Auto-upgrade offline→openai when key present, unless user forced offline
    if st.llm_mode == "offline" and st.llm_api_key and forced_mode != "offline":
        st.llm_mode = "openai"
    set_settings(st)
    if persist:
        persist_api_keys(
            {
                "llm_api_key": st.llm_api_key,
                "llm_base_url": st.llm_base_url,
                "llm_model": st.llm_model,
            }
        )
    try:
        from .llm.router import get_provider

        get_provider(refresh=True)
    except Exception:
        pass
    return st


def llm_status(settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Public LLM status — never includes the raw key."""
    st = settings or get_settings()
    source = _llm_key_source(st)
    return {
        "mode": st.llm_mode,
        "base_url": st.llm_base_url,
        "model": st.llm_model,
        "has_key": bool(st.llm_api_key),
        "key_hint": _key_hint(st.llm_api_key),
        "key_source": source,
        "key_source_label": _SOURCE_LABEL.get(source, ""),
        "persisted": source == "file",
    }


def apply_tts_runtime(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    voice: Optional[str] = None,
    voice_alt: Optional[str] = None,
    mode: Optional[str] = None,
    persist: bool = False,
) -> Settings:
    """Update live TTS settings. persist=True writes configs/api_keys.yaml."""
    st = get_settings()
    if api_key is not None and str(api_key).strip():
        st.tts_api_key = str(api_key).strip()
    if base_url is not None and str(base_url).strip():
        url = str(base_url).strip().rstrip("/")
        if url.endswith("/audio/speech"):
            url = url[: -len("/audio/speech")].rstrip("/")
        st.tts_base_url = url
    if model is not None and str(model).strip():
        st.tts_model = str(model).strip()
    if provider is not None and str(provider).strip():
        st.tts_provider = str(provider).strip()
    if voice is not None and str(voice).strip():
        st.tts_voice = str(voice).strip()
    if voice_alt is not None and str(voice_alt).strip():
        st.tts_voice_alt = str(voice_alt).strip()
    if mode is not None and str(mode).strip():
        st.tts_mode = str(mode).strip()
    # Prefer cloud TTS when key present
    if st.tts_api_key and st.tts_provider in ("", "auto"):
        st.tts_provider = "siliconflow"
    if st.tts_api_key and st.tts_mode in ("auto", "edge"):
        st.tts_mode = "siliconflow"
    set_settings(st)
    if persist:
        persist_api_keys(
            {
                "tts_api_key": st.tts_api_key,
                "tts_base_url": st.tts_base_url,
                "tts_model": st.tts_model,
                "tts_provider": st.tts_provider,
                "tts_voice": st.tts_voice,
                "tts_voice_alt": st.tts_voice_alt,
            }
        )
    try:
        from .tts.base import get_tts

        get_tts(refresh=True)
    except Exception:
        pass
    return st


def tts_status(settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Public TTS status — never includes the raw key."""
    st = settings or get_settings()
    source = _tts_key_source(st)
    return {
        "provider": st.tts_provider,
        "mode": st.tts_mode,
        "base_url": st.tts_base_url,
        "model": st.tts_model,
        "voice": st.tts_voice,
        "has_key": bool(st.tts_api_key),
        "key_hint": _key_hint(st.tts_api_key),
        "key_source": source,
        "key_source_label": _SOURCE_LABEL.get(source, ""),
        "persisted": source == "file",
    }

