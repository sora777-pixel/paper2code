"""阶段状态与运行进度（roadmap M1-⑤）。

两个目的，对应路线图那一条的两半：

* **可见**：每个阶段的「状态 / 耗时 / 失败原因 / 产物」落盘成 `progress.json`，
  Web 端轮询它就能显示阶段进度，而不是一个转圈；
* **可续跑**：`--resume` 跳过「上次已完成、且产物仍在」的阶段，省掉重复的 LLM 调用与执行开销。

**诚实性约定**（沿用本项目的口径）：
* `--resume` 只跳过**产物仍然存在**的阶段；被跳过的阶段一律写进 `manifest.warnings`，
  绝不静默复用陈旧结果；
* 状态用词区分 `done`（本次真跑了）与 `reused`（本次跳过、复用了上次产物）——两者不能混为一谈。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

#: 流水线的可见阶段（解析在建立产物目录之前完成，故不计入）
STAGES: Sequence[str] = ("explain", "reproduce", "mvp")

STATUS_ZH = {
    "pending": "待执行",
    "running": "进行中",
    "done": "已完成",
    "reused": "已复用上次产物（跳过）",
    "failed": "失败",
}


@dataclass
class StageState:
    name: str
    status: str = "pending"
    elapsed_s: float = 0.0
    note: str = ""
    artifacts: List[str] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RunState:
    """一个 run 的阶段状态机；每次状态变化都落盘（文件很小，便于轮询）。"""

    def __init__(self, path: Path, paper_id: str = "", stages: Sequence[str] = STAGES,
                 resume: bool = False, on_change: Optional[Any] = None) -> None:
        self.path = Path(path)
        self.paper_id = paper_id
        self.resume = bool(resume)
        self.stages: Dict[str, StageState] = {n: StageState(n) for n in stages}
        self._t0: Dict[str, float] = {}
        self._prev: Dict[str, Any] = {}
        self.notes: List[str] = []
        #: M5-①：每次状态落盘都回调一次（队列任务用它把进度回写数据库，Web 端就能实时看到阶段）
        self.on_change = on_change

    # -- 载入上次状态 ------------------------------------------------------- #
    def load_existing(self) -> "RunState":
        if not self.path.exists():
            return self
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return self
        # snapshot() 里 stages 是**有序列表**（便于 UI 渲染），这里按 name 索引回来。
        prev = data.get("stages")
        if isinstance(prev, dict):
            self._prev = prev
        elif isinstance(prev, list):
            self._prev = {str(s.get("name")): s for s in prev
                          if isinstance(s, dict) and s.get("name")}
        return self

    def can_resume(self, name: str) -> bool:
        """能否跳过该阶段：上次 done/reused + 记录的产物全都还在。"""
        if not self.resume:
            return False
        p = self._prev.get(name) or {}
        if p.get("status") not in ("done", "reused"):
            return False
        arts = [a for a in (p.get("artifacts") or []) if a]
        if not arts:
            return False
        return all(Path(a).exists() for a in arts)

    def mark_reused(self, name: str, note: str = "") -> None:
        st = self.stages[name]
        st.status = "reused"
        st.note = note or "沿用上次运行的同名产物（--resume）"
        st.artifacts = [a for a in ((self._prev.get(name) or {}).get("artifacts") or []) if a]
        self.notes.append("阶段 %s 已跳过（复用上次产物）" % name)
        self.save()

    # -- 状态迁移 ----------------------------------------------------------- #
    def begin(self, name: str) -> None:
        import time

        st = self.stages[name]
        st.status = "running"
        st.started_at = _now()
        st.note = ""
        self._t0[name] = time.time()
        self.save()

    def finish(self, name: str, artifacts: Iterable[Optional[str]] = (), note: str = "") -> None:
        import time

        st = self.stages[name]
        st.status = "done"
        st.elapsed_s = round(time.time() - self._t0.get(name, time.time()), 2)
        st.artifacts = [str(a) for a in artifacts if a]
        st.note = note
        st.finished_at = _now()
        self.save()

    def fail(self, name: str, error: str) -> None:
        import time

        st = self.stages[name]
        st.status = "failed"
        st.elapsed_s = round(time.time() - self._t0.get(name, time.time()), 2)
        st.note = error[:400]
        st.finished_at = _now()
        self.save()

    # -- 快照 --------------------------------------------------------------- #
    def current(self) -> str:
        for st in self.stages.values():
            if st.status == "running":
                return st.name
        return ""

    def reused(self) -> List[str]:
        return [n for n, st in self.stages.items() if st.status == "reused"]

    def failed(self) -> List[str]:
        return [n for n, st in self.stages.items() if st.status == "failed"]

    def snapshot(self) -> Dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "updated_at": _now(),
            "resume": self.resume,
            "current": self.current(),
            "failed": self.failed(),
            "reused": self.reused(),
            "stages": [self.stages[n].to_dict() for n in self.stages],
            "notes": list(self.notes),
        }

    def save(self) -> None:
        """直接覆写（不用 临时文件 + os.replace：该原子写法在本机被宿主的
        删除/替换钩子拦下 → PermissionError）。读取端对半截文件已做容错。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
        self.emit(self.on_change)

    def emit(self, cb: Optional[Any] = None) -> None:
        if cb is not None:
            try:
                cb(self.snapshot())
            except Exception:  # 进度回调不能拖垮主流程
                pass

    def reused_warning(self) -> List[str]:
        return ["本次为 --resume 运行，已跳过 %d 个已完成阶段（%s），复用的是上次的同名产物。"
                % (len(self.reused()), "、".join(self.reused()))] if self.reused() else []


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def read_snapshot(path: Path) -> Optional[Dict[str, Any]]:
    """给 Web 端读用：返回进度快照；文件不存在返回 None（= 还没开始）。"""
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
