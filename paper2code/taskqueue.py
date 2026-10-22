"""任务队列（roadmap M5-①）：SQLite 起步的后台 worker 与队列。

**为什么要有它**：此前 Web 端用「进程内 dict + 线程」记任务状态——重启即丢、并发无上限、
没法取消，也没法看批量提交的全局视图。这里换成 SQLite 落库的队列：

* **并发限制**：worker 线程数 = ``task_concurrency``，任务按 ``created_at`` 依次领取；
* **任务级超时**：每个任务带 :class:`~paper2code.control.Control`，超时即收尾并落 ``failed``；
* **取消**：`cancel` 对排队任务直接置 ``cancelled``；对运行中任务置取消标记，
  由控制对象在阶段边界与子进程轮询处生效（见 :mod:`paper2code.control`）；
* **全局视图**：`list()/stats()` 供 Web 端渲染队列与批处理面板。

**进程模型**：worker 跑在 `serve` 进程内的线程里（单机足够），DB 在 ``.work/tasks.db``。
任务产物仍按老规矩落在 ``outputs/single/<paper_id>/``，队列只负责「谁在跑、跑到哪、能不能取消」。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import control
from .config import get_settings

PENDING, RUNNING, DONE, FAILED, CANCELLED = "pending", "running", "done", "failed", "cancelled"
TERMINAL = (DONE, FAILED, CANCELLED)

STATUS_ZH = {
    PENDING: "排队中",
    RUNNING: "运行中",
    DONE: "已完成",
    FAILED: "失败",
    CANCELLED: "已取消",
}


def db_path() -> Path:
    return Path(get_settings().work_dir) / "tasks.db"


@dataclass
class Task:
    id: str
    kind: str
    source: str
    label: str = ""
    status: str = PENDING
    params: Dict[str, Any] = None  # type: ignore[assignment]
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    worker: str = ""
    cancel_requested: bool = False
    error: str = ""
    result: Dict[str, Any] = None  # type: ignore[assignment]
    progress: Dict[str, Any] = None  # type: ignore[assignment]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "source": self.source, "label": self.label,
            "status": self.status, "status_zh": STATUS_ZH.get(self.status, self.status),
            "params": self.params or {}, "created_at": self.created_at, "started_at": self.started_at,
            "finished_at": self.finished_at, "worker": self.worker,
            "cancel_requested": bool(self.cancel_requested), "error": self.error,
            "result": self.result or {}, "progress": self.progress or {},
        }


class TaskQueue:
    """SQLite 队列。所有写操作都在 ``BEGIN IMMEDIATE`` 事务里，保证「领取」是原子的。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_db()

    # -- 连接 --------------------------------------------------------------- #
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.Error:
                pass
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    def _init_db(self) -> None:
        c = self._conn()
        c.execute(
            "CREATE TABLE IF NOT EXISTS tasks ("
            " id TEXT PRIMARY KEY, kind TEXT NOT NULL, source TEXT DEFAULT '', label TEXT DEFAULT '',"
            " status TEXT NOT NULL, params_json TEXT, created_at TEXT, started_at TEXT, finished_at TEXT,"
            " worker TEXT, cancel_requested INTEGER DEFAULT 0, error TEXT DEFAULT '',"
            " result_json TEXT, progress_json TEXT)")

    # -- 写 ----------------------------------------------------------------- #
    def enqueue(self, kind: str, source: str = "", params: Optional[Dict[str, Any]] = None,
                label: str = "") -> str:
        tid = uuid.uuid4().hex[:12]
        c = self._conn()
        c.execute(
            "INSERT INTO tasks (id, kind, source, label, status, params_json, created_at,"
            " cancel_requested) VALUES (?,?,?,?,?,?,?,0)",
            (tid, kind, str(source), label or str(source), PENDING,
             json.dumps(params or {}, ensure_ascii=False), _now()))
        return tid

    def claim_next(self, worker: str = "") -> Optional[Task]:
        """原子领取一个排队任务；没有则返回 None。"""
        c = self._conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            row = c.execute(
                "SELECT * FROM tasks WHERE status=? ORDER BY created_at, id LIMIT 1", (PENDING,)).fetchone()
            if row is None:
                c.execute("COMMIT")
                return None
            c.execute("UPDATE tasks SET status=?, started_at=?, finished_at='', worker=?, error='' WHERE id=?",
                      (RUNNING, _now(), worker, row["id"]))
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
        t = self.get(row["id"])
        return t

    def finish(self, task_id: str, status: str, result: Optional[Dict[str, Any]] = None,
               error: str = "", progress: Optional[Dict[str, Any]] = None) -> None:
        c = self._conn()
        c.execute("UPDATE tasks SET status=?, finished_at=?, error=?, result_json=COALESCE(?, result_json),"
                  " progress_json=COALESCE(?, progress_json), cancel_requested=0 WHERE id=?",
                  (status, _now(), error or "",
                   json.dumps(result, ensure_ascii=False) if result is not None else None,
                   json.dumps(progress, ensure_ascii=False) if progress is not None else None, task_id))

    def update_progress(self, task_id: str, progress: Dict[str, Any]) -> None:
        c = self._conn()
        c.execute("UPDATE tasks SET progress_json=? WHERE id=?",
                  (json.dumps(progress, ensure_ascii=False), task_id))

    def request_cancel(self, task_id: str) -> Dict[str, Any]:
        """取消任务：排队中→直接结束；运行中→置标记（由控制对象生效）。"""
        c = self._conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            row = c.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                return {"ok": False, "reason": "任务不存在"}
            st = row["status"]
            if st == PENDING:
                c.execute("UPDATE tasks SET status=?, finished_at=?, error=? WHERE id=?",
                          (CANCELLED, _now(), "提交后未开始即被取消", task_id))
                c.execute("COMMIT")
                return {"ok": True, "status": CANCELLED, "note": "任务尚未开始，已直接取消"}
            if st == RUNNING:
                c.execute("UPDATE tasks SET cancel_requested=1 WHERE id=?", (task_id,))
                c.execute("COMMIT")
                return {"ok": True, "status": RUNNING, "note": "已发出取消请求，运行中的阶段会在边界处中止"}
            c.execute("COMMIT")
            return {"ok": False, "status": st, "reason": "任务已结束（%s），无法取消" % STATUS_ZH.get(st, st)}
        except Exception:
            c.execute("ROLLBACK")
            raise

    def is_cancel_requested(self, task_id: str) -> bool:
        row = self._conn().execute("SELECT cancel_requested, status FROM tasks WHERE id=?",
                                   (task_id,)).fetchone()
        if row is None:
            return True          # 任务被删了 → 视作「别再跑了」
        return bool(row["cancel_requested"]) or row["status"] == CANCELLED

    # -- 读 ----------------------------------------------------------------- #
    def get(self, task_id: str) -> Optional[Task]:
        row = self._conn().execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return _row_to_task(row) if row else None

    def list(self, limit: int = 50, statuses: Optional[Sequence[str]] = None) -> List[Task]:
        sql = "SELECT * FROM tasks"
        args: List[Any] = []
        if statuses:
            sql += " WHERE status IN (%s)" % ",".join("?" * len(statuses))
            args += list(statuses)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        args.append(int(limit))
        return [_row_to_task(r) for r in self._conn().execute(sql, args).fetchall()]

    def stats(self) -> Dict[str, int]:
        rows = self._conn().execute("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status").fetchall()
        out = {s: 0 for s in (PENDING, RUNNING, DONE, FAILED, CANCELLED)}
        for r in rows:
            out[r["status"]] = int(r["n"])
        out["total"] = sum(out.values())
        return out

    def prune(self, keep: int = 200) -> int:
        """只保留最近 ``keep`` 条终态任务，避免 DB 无限增长。"""
        c = self._conn()
        rows = c.execute("SELECT id FROM tasks WHERE status IN (?,?,?) ORDER BY finished_at DESC LIMIT ?",
                         (*TERMINAL, int(keep))).fetchall()
        keep_ids = [r["id"] for r in rows]
        if len(keep_ids) >= keep:
            marks = ",".join("?" * len(keep_ids))
            cur = c.execute("DELETE FROM tasks WHERE status IN (?,?,?) AND id NOT IN (%s)"
                            % marks, (*TERMINAL, *keep_ids))
            return cur.rowcount or 0
        return 0

    def reclaim_orphans(self, reason: str = "") -> int:
        """把「上次进程死掉时还没跑完」的任务**如实记为 failed**。

        单进程模型下，进程一起来就没有任何 worker 在跑，所以 DB 里剩下的 ``running``
        行必然是孤儿——最典型的成因是 uvicorn 热重载 / 手动重启把 worker 一起干掉了。
        不能让它永远停在「运行中」（前端会一直转圈、也没法重跑）；这里写明原因收尾，
        用户可以用「续跑」重新提交，已落盘的阶段产物不会白费。
        """
        c = self._conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            cur = c.execute(
                "UPDATE tasks SET status=?, finished_at=?, cancel_requested=0, error=? WHERE status=?",
                (FAILED, _now(),
                 reason or "服务在任务运行中被重启（热重载或手动重启），本次执行被中断；可用「续跑」重新提交",
                 RUNNING))
            c.execute("COMMIT")
            return int(cur.rowcount or 0)
        except Exception:
            c.execute("ROLLBACK")
            raise

    def delete_for_label(self, label: str) -> int:
        """删掉某个 ``label``（= 一次运行的 paper_id）的全部任务行。

        未结束的任务先置 ``cancel_requested``；行删掉之后 :meth:`is_cancel_requested`
        会返回 True，运行中的 worker 会在下一个阶段边界停下——于是「删除历史记录」
        天然也停掉了它自己的后台任务，不需要额外接口。
        """
        c = self._conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            c.execute("UPDATE tasks SET cancel_requested=1 WHERE label=? AND status IN (?,?)",
                      (str(label), PENDING, RUNNING))
            cur = c.execute("DELETE FROM tasks WHERE label=?", (str(label),))
            c.execute("COMMIT")
            return int(cur.rowcount or 0)
        except Exception:
            c.execute("ROLLBACK")
            raise


def _row_to_task(row: sqlite3.Row) -> Task:
    def _load(v: Any) -> Dict[str, Any]:
        if not v:
            return {}
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return {}
    return Task(id=row["id"], kind=row["kind"], source=row["source"], label=row["label"],
                status=row["status"], params=_load(row["params_json"]),
                created_at=row["created_at"] or "", started_at=row["started_at"] or "",
                finished_at=row["finished_at"] or "", worker=row["worker"] or "",
                cancel_requested=bool(row["cancel_requested"]), error=row["error"] or "",
                result=_load(row["result_json"]), progress=_load(row["progress_json"]))


# --------------------------------------------------------------------------- #
# worker 池
# --------------------------------------------------------------------------- #
def execute_task(task: Task, on_progress: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    """真正执行一个任务（供 worker 池与测试直接调用）。

    :raises paper2code.control.RunCancelled: 取消 / 超时
    """
    from .pipeline import run_paper

    params = dict(task.params or {})
    if task.kind == "mvp":
        from .ingest import load_paper
        from .mvp import build_mvp

        paper = load_paper(task.source)
        out_root = Path(params.get("output_dir") or get_settings().output_dir)
        res = build_mvp(paper, out_root / paper.id / "mvp",
                        execute=bool(params.get("execute", True)),
                        use_llm=params.get("use_llm"),
                        modify=str(params.get("modify") or ""))
        return res.to_dict()
    if task.kind == "paper":
        explain = tuple(params.get("explain") or ())
        manifest = run_paper(task.source, output_dir=params.get("output_dir"),
                             explain=explain, reproduce=bool(params.get("reproduce", True)),
                             audio=bool(params.get("audio", True)),
                             resume=bool(params.get("resume", False)),
                             mvp=params.get("mvp"),
                             on_progress=on_progress)
        return {"paper_id": manifest.get("paper_id"), "ok": True,
                "artifacts": manifest.get("artifacts"), "warnings": manifest.get("warnings", [])}
    raise ValueError("未知任务类型：%s" % task.kind)


class WorkerPool:
    """固定并发数的 worker 池（默认 ``task_concurrency``）。"""

    def __init__(self, queue: TaskQueue, concurrency: int = 0, poll_interval: float = 0.4,
                 timeout_s: float = 0.0) -> None:
        st = get_settings()
        self.queue = queue
        self.concurrency = max(1, int(concurrency or getattr(st, "task_concurrency", 2) or 1))
        self.poll_interval = poll_interval
        self.timeout_s = float(timeout_s or getattr(st, "task_timeout_s", 0) or 0)
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []

    # -- 生命周期 ----------------------------------------------------------- #
    def start(self) -> "WorkerPool":
        for i in range(self.concurrency):
            t = threading.Thread(target=self._loop, name="p2c-worker-%d" % i, daemon=True)
            t.start()
            self._threads.append(t)
        return self

    def stop(self, wait: bool = False) -> None:
        self._stop.set()
        if wait:
            for t in self._threads:
                t.join(timeout=5)

    @property
    def running_workers(self) -> int:
        return sum(1 for t in self._threads if t.is_alive())

    # -- 主循环 ------------------------------------------------------------- #
    def _loop(self) -> None:
        name = threading.current_thread().name
        while not self._stop.is_set():
            task = None
            try:
                task = self.queue.claim_next(worker=name)
            except Exception:
                task = None
            if task is None:
                self._stop.wait(self.poll_interval)
                continue
            self._run_one(task)

    def _run_one(self, task: Task) -> None:
        ctl = control.Control(is_cancelled=lambda: self.queue.is_cancel_requested(task.id),
                              timeout_s=self.timeout_s, label=task.id)
        control.set_current(ctl)
        try:
            ctl.check()
            result = execute_task(task, on_progress=lambda p: self._safe_progress(task, p))
            # 兜底：即便某处把 RunCancelled 吞成了 warning，这里也能纠正任务状态
            ctl.check()
            self.queue.finish(task.id, DONE, result=result, progress=self._read_progress(task))
        except control.RunCancelled as exc:
            self.queue.finish(task.id, FAILED if exc.timed_out else CANCELLED,
                              error=exc.reason, progress=self._read_progress(task))
        except Exception as exc:
            self.queue.finish(task.id, FAILED, error="%s: %s" % (type(exc).__name__, exc),
                              progress=self._read_progress(task))
        finally:
            control.clear_current()

    # -- 进度 --------------------------------------------------------------- #
    def _safe_progress(self, task: Task, snap: Dict[str, Any]) -> None:
        try:
            self.queue.update_progress(task.id, snap or {})
        except Exception:
            pass

    def _read_progress(self, task: Task) -> Dict[str, Any]:
        """worker 结束后回读阶段快照（运行中的实时进度由 Web 端直接读 progress.json）。"""
        from .ingest import paper_id_for_source

        try:
            root = Path((task.params or {}).get("output_dir") or get_settings().output_dir)
            # 必须用 paper_id_for_source（arXiv/URL 的产物目录名与 paper_id_from_path 不同）
            pid = paper_id_for_source(task.source)
            p = root / pid / "progress.json"
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


_pool: Optional[WorkerPool] = None
_pool_lock = threading.Lock()


def get_queue() -> TaskQueue:
    return TaskQueue()


def ensure_workers() -> WorkerPool:
    """进程内单例 worker 池（Web 端首次用到时启动）。"""
    global _pool
    with _pool_lock:
        if _pool is None or not _pool.running_workers:
            _pool = WorkerPool(TaskQueue()).start()
        return _pool


def shutdown_workers(wait: bool = True) -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.stop(wait=wait)
            _pool = None
