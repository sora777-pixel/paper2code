"""运行配置。

优先级：显式参数 > 环境变量 > ``configs/default.yaml``（若存在）> 内置默认值。
YAML 为可选依赖，缺失时静默跳过。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent

#: 凭据叠加层文件名。``.gitignore`` 与 ``tools/pack_repo.py`` 都把它当凭据文件排除，
#: 所以这里必须真的读它——否则「把 Key 放这个文件」这条约定就是坏的（会静默不生效）。
API_KEYS_FILE = "configs/api_keys.yaml"


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


def _load_api_keys() -> Dict[str, Any]:
    """读 ``configs/api_keys.yaml``（**凭据文件**，已被 gitignore 与打包脚本排除）。

    它叠在 ``configs/default.yaml`` 之上、环境变量与命令行参数之下：
    ``命令行 > 环境变量 > api_keys.yaml > default.yaml > 内置默认值``。
    这样「把 Key 写进 gitignored 的文件」既能生效，又不会被提交出去。
    """
    return _load_yaml(ROOT / API_KEYS_FILE)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    """全局设置。"""

    workspace: Path = field(default_factory=lambda: ROOT)
    #: 单篇运行的产物根目录（``outputs/single/<paper_id>/``）
    output_dir: Path = field(default_factory=lambda: ROOT / "outputs" / "single")
    #: 批量运行的产物根目录（``outputs/batch/<批次名>/<paper_id>/``）
    batch_dir: Path = field(default_factory=lambda: ROOT / "outputs" / "batch")
    #: 评测产物根目录（``outputs/eval/``；container/llm/llm1m 为其子目录）
    eval_dir: Path = field(default_factory=lambda: ROOT / "outputs" / "eval")
    #: 单篇测试输入根目录（``tests/inputs/single/<paper_id>/``）
    single_input_dir: Path = field(default_factory=lambda: ROOT / "tests" / "inputs" / "single")
    samples_dir: Path = field(default_factory=lambda: ROOT / "samples")
    work_dir: Path = field(default_factory=lambda: ROOT / ".work")

    # -- LLM --------------------------------------------------------------- #
    llm_mode: str = "offline"  # offline | openai
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout: int = 120
    llm_temperature: float = 0.3

    # -- 成本护栏（每篇论文的硬上限；0 = 不限制；roadmap M1-⑥） ------------- #
    cost_max_calls: int = 40          # LLM 调用次数上限
    cost_max_tokens: int = 120_000    # 累计 token 上限
    cost_max_seconds: int = 600       # 累计耗时上限（秒）

    # -- 语音 -------------------------------------------------------------- #
    tts_mode: str = "auto"  # auto | edge | off
    tts_voice_a: str = "zh-CN-YunxiNeural"
    tts_voice_b: str = "zh-CN-XiaoxiaoNeural"
    # 逐说话人韵律 / 问句语调 / 轮次间停顿：让双人对谈不再「同一个腔调念稿」
    tts_rate_a: str = "+8%"          # 主持人语速（稍快、有活力）
    tts_rate_b: str = "+0%"          # 讲解人语速（平稳）
    tts_pitch_a: str = "+2Hz"        # 主持人音高
    tts_pitch_b: str = "-2Hz"        # 讲解人音高
    tts_volume: str = "+0%"
    tts_question_rate: str = "+12%"  # 问句语速
    tts_question_pitch: str = "+8Hz"  # 问句抬语调，避免一条直线
    tts_pause: str = "……"            # 轮次间停顿标记（置空则关闭）

    # -- 系统语音（SAPI）韵律：单音色时用更大的语速/音高差区分角色 ---------- #
    tts_sapi_voice: str = ""          # 空 = 自动挑第一个中文音色
    tts_sapi_rate_a: str = "+22%"     # 主持人：明显更快
    tts_sapi_rate_b: str = "-10%"     # 讲解人：更慢
    tts_sapi_pitch_a: str = "+10Hz"   # 主持人：更高
    tts_sapi_pitch_b: str = "-8Hz"    # 讲解人：更低
    tts_sapi_question_rate: str = "+8%"
    tts_sapi_question_pitch: str = "+14Hz"
    tts_sapi_volume: str = "+0%"
    tts_sapi_break_ms: int = 450      # 每轮后的停顿

    # -- 复现约束（单机 / 小数据集） ---------------------------------------- #
    max_records: int = 10_000
    exec_timeout: int = 120
    allow_network: bool = False
    rel_tolerance: float = 0.05   # 相对误差 <=5% 记为 pass
    rel_warn: float = 0.20        # <=20% 记为 warn，超过为 fail
    random_seed: int = 0

    # -- 执行沙箱（roadmap M4） -------------------------------------------- #
    #: ``local`` 本机进程执行（低隔离、高风险，默认，保持既有行为）；``auto`` 能用容器就用容器；
    #: ``container`` 强制容器（拿不到容器即如实降级并告警，不假装隔离）。
    sandbox_mode: str = "local"
    sandbox_image: str = "p2c-sandbox:latest"
    #: 构建沙箱镜像的基础镜像。某些网络（如本机）到 Docker Hub 不通，可换成可用镜像源，
    #: 例如 ``docker.m.daocloud.io/library/python:3.12-slim``。
    sandbox_base: str = "python:3.12-slim"
    sandbox_pip_index: str = ""      # 构建时的 pip 源（空 = 用默认 PyPI），如国内镜像
    sandbox_cpus: float = 1.0        # 容器 CPU 上限
    sandbox_memory: str = "1g"       # 容器内存上限
    sandbox_pids: int = 128          # 容器进程数上限（挡 fork 炸弹）
    sandbox_scan: bool = True        # 执行前做静态扫描（M4-①）
    sandbox_dep_policy: str = "warn"  # 依赖白名单命中策略：warn | block
    sandbox_require_confirm: bool = True  # 非容器模式下「首次运行需确认」（Web UI 强制）

    # -- 仓库依赖隔离环境（D：显式开启，默认不动用户环境） ------------------ #
    #: 为论文仓库创建 ``outputs/single/<id>/reproduce/venv`` 并安装依赖；默认关闭，
    #: 避免静默联网/装包破坏「离线永远可用」这条硬约束。
    repo_venv_auto: bool = False
    #: 连 torch / tensorflow 等重型依赖也安装（默认只安装轻量依赖）
    repo_install_heavy: bool = False
    #: 隔离 venv 的 pip 源（空 = 沿用 sandbox_pip_index）
    repo_pip_index: str = ""
    #: pip 安装墙钟超时（秒）
    repo_install_timeout: int = 900

    # -- 任务队列（roadmap M5-①） ------------------------------------------ #
    task_concurrency: int = 2        # 后台 worker 并发上限
    task_timeout_s: int = 1800       # 单任务墙钟超时（秒）；0 = 不限制（超时即收尾为 failed）

    # -- 导出（roadmap M5-③） ---------------------------------------------- #
    #: 托管 notebook 的 GitHub 仓库（``owner/repo``）。填了才会生成真正的「一键 Colab」链接；
    #: 留空就只在导出 README 里写清「先上传再打开」——不给点了会 404 的假链接。
    notebook_repo: str = ""

    # -- 插件（roadmap M5-④） ---------------------------------------------- #
    #: 选用的图表风格名（由插件注册，见 configs/plugins.yaml 与 paper2code.plugins）
    chart_style: str = ""

    # -- 远程执行器（roadmap M6-④，远程 GPU） -------------------------------- #
    remote_host: str = ""            # 形如 user@host；留空 + remote_dir 有值 = 「远端即本地目录」
    remote_dir: str = ""             # 远端工作目录（配了它才会注册 remote 执行器）
    remote_python: str = "python3"
    remote_timeout_s: int = 900

    # -- 数据集自动获取（roadmap M6-③） ------------------------------------- #
    #: 数据源声明文件（相对仓库根或绝对路径）；没有声明就不做任何事
    datasets_spec: str = "configs/datasets.yaml"
    #: 是否放行「许可不在白名单」的数据源（默认 False：宁可不下载）
    datasets_allow_unknown_license: bool = False

    # -- 讲解 -------------------------------------------------------------- #
    max_slides: int = 14
    style: str = "teaching"  # teaching | executive | exam

    # -- MVP 自动生成（# MVP-PATCH:config） --------------------------------- #
    mvp_enabled: bool = True       # 处理论文时同时生成 MVP demo + 图表绘制说明
    mvp_use_llm: bool = True       # False = 只用离线脚手架
    mvp_max_fix: int = 3           # demo 运行失败后的 LLM 修复轮数
    mvp_max_fix_fig: int = 2       # 单张图绘制脚本的修复轮数
    mvp_smoke_timeout: int = 90    # smoke 运行超时（秒）

    extra: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls, config_file: Optional[Path] = None, **overrides: Any) -> "Settings":
        data: Dict[str, Any] = {}
        data.update(_load_yaml(ROOT / "configs" / "default.yaml"))
        data.update(_load_api_keys())          # 凭据叠加层（gitignored）
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
        if os.environ.get("P2C_SANDBOX_MODE"):
            inst.sandbox_mode = os.environ["P2C_SANDBOX_MODE"]
        if os.environ.get("P2C_SANDBOX_IMAGE"):
            inst.sandbox_image = os.environ["P2C_SANDBOX_IMAGE"]
        if os.environ.get("P2C_SANDBOX_BASE"):
            inst.sandbox_base = os.environ["P2C_SANDBOX_BASE"]
        if os.environ.get("P2C_SANDBOX_PIP_INDEX"):
            inst.sandbox_pip_index = os.environ["P2C_SANDBOX_PIP_INDEX"]
        if os.environ.get("P2C_REPO_VENV_AUTO"):
            inst.repo_venv_auto = _env_bool("P2C_REPO_VENV_AUTO")
        if os.environ.get("P2C_REPO_INSTALL_HEAVY"):
            inst.repo_install_heavy = _env_bool("P2C_REPO_INSTALL_HEAVY")
        if os.environ.get("P2C_REPO_PIP_INDEX"):
            inst.repo_pip_index = os.environ["P2C_REPO_PIP_INDEX"]
        if os.environ.get("P2C_REPO_INSTALL_TIMEOUT"):
            try:
                inst.repo_install_timeout = int(os.environ["P2C_REPO_INSTALL_TIMEOUT"])
            except ValueError:
                pass
        if os.environ.get("P2C_NOTEBOOK_REPO"):
            inst.notebook_repo = os.environ["P2C_NOTEBOOK_REPO"]
        if os.environ.get("P2C_REMOTE_HOST"):
            inst.remote_host = os.environ["P2C_REMOTE_HOST"]
        if os.environ.get("P2C_REMOTE_DIR"):
            inst.remote_dir = os.environ["P2C_REMOTE_DIR"]
        if os.environ.get("P2C_REMOTE_PYTHON"):
            inst.remote_python = os.environ["P2C_REMOTE_PYTHON"]

        for key, value in overrides.items():
            if value is None:
                continue
            if hasattr(inst, key):
                setattr(inst, key, value)

        # 有 Key 就自动升级到 openai —— 但**只在不与用户显式选择冲突时**（roadmap M0-2）：
        # 显式 `--llm offline` / `--offline` / `P2C_LLM_MODE=offline` / 配置文件里的 llm_mode
        # 都必须压过这条自动升级；否则在配了 Key 的机器上，用户永远拿不到真正的 --offline。
        #
        # 注意：「显式」= 传进来一个**真实的值**。调用方常把 CLI 的 `llm_mode=None` 一起塞进
        # overrides（None 会被下面的赋值循环忽略），若把它也算作「显式」，就会把自动升级压掉
        # ——表现为「Key 明明配好了，模式却还是 offline」，极难排查。
        explicit_mode = (
            bool(os.environ.get("P2C_LLM_MODE"))
            or any(k == "llm_mode" and v is not None for k, v in overrides.items())
            or bool(config_file and "llm_mode" in _load_yaml(Path(config_file)))
        )
        if not explicit_mode and inst.llm_mode == "offline" and inst.llm_api_key:
            inst.llm_mode = "openai"

        # 路径统一为 Path
        for name in ("workspace", "output_dir", "batch_dir", "eval_dir",
                     "single_input_dir", "samples_dir", "work_dir"):
            setattr(inst, name, Path(getattr(inst, name)))
        return inst

    # ------------------------------------------------------------------ #
    def ensure_dirs(self) -> "Settings":
        for name in ("output_dir", "batch_dir", "eval_dir", "work_dir"):
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

    def sandbox_config(self) -> Dict[str, Any]:
        """执行沙箱配置（M4）：模式、资源上限、扫描策略。"""
        return {
            "mode": self.sandbox_mode,
            "image": self.sandbox_image,
            "base": self.sandbox_base,
            "pip_index": self.sandbox_pip_index,
            "cpus": self.sandbox_cpus,
            "memory": self.sandbox_memory,
            "pids": self.sandbox_pids,
            "scan": self.sandbox_scan,
            "dep_policy": self.sandbox_dep_policy,
            "require_confirm": self.sandbox_require_confirm,
        }

    def quality_thresholds(self) -> Dict[str, float]:
        return {"pass": self.rel_tolerance, "warn": self.rel_warn}

    def queue_config(self) -> Dict[str, Any]:
        """任务队列配置（M5-①）。"""
        return {"concurrency": self.task_concurrency, "timeout_s": self.task_timeout_s,
                "db": str(Path(self.work_dir) / "tasks.db")}

    def cost_budget(self) -> "Budget":
        """每篇论文的 LLM 成本硬上限（M1-⑥）。"""
        from .budget import Budget

        return Budget(max_calls=self.cost_max_calls, max_tokens=self.cost_max_tokens,
                      max_seconds=float(self.cost_max_seconds or 0))

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for k, v in self.__dict__.items():
            out[k] = str(v) if isinstance(v, Path) else v
        out["llm_api_key"] = "***" if self.llm_api_key else ""
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
