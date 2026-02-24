"""
Victor-OS Task Queue
SQLite-backed async job queue with worker thread, retry, and progress tracking.
"""

import concurrent.futures
import os
import json
import time
import uuid
import hashlib
import sqlite3
import threading
from dataclasses import dataclass, field
from typing import Any, Callable
from enum import Enum

from config import get_config
from logging_config import get_logger

logger = get_logger("task_queue")


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    id: str
    task_type: str
    payload: dict[str, Any]
    status: TaskStatus
    channel: str
    user_id: str
    created_at: float
    updated_at: float
    started_at: float | None = None
    completed_at: float | None = None
    result: str | None = None
    error: str | None = None
    retries: int = 0
    max_retries: int = 3
    progress: int = 0
    priority: int = 3
    metadata: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
    lease_expires_at: float | None = None


class TaskQueue:
    """SQLite-backed async task queue."""

    def __init__(self, db_path: str | None = None):
        cfg = get_config()
        self._db_path = db_path or os.path.join(
            cfg.base_dir, "memory_store", "victor_tasks.db"
        )
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._init_db()
        self._handlers: dict[str, Callable] = {}
        self._running = False
        self._worker_thread: threading.Thread | None = None
        self._notify_callback: Callable[[str, str, str], None] | None = None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                task_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                channel TEXT NOT NULL DEFAULT 'system',
                user_id TEXT NOT NULL DEFAULT 'ceejay',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                started_at REAL,
                completed_at REAL,
                result TEXT,
                error TEXT,
                retries INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 3,
                progress INTEGER DEFAULT 0,
                priority INTEGER DEFAULT 3,
                metadata TEXT DEFAULT '{}',
                idempotency_key TEXT,
                lease_expires_at REAL
            )
        """)
        self._migrate_schema(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_status ON tasks(status, priority)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_type ON tasks(task_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_idempotency ON tasks(idempotency_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_lease ON tasks(status, lease_expires_at)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS delivery_ledger (
                id TEXT PRIMARY KEY,
                destination TEXT NOT NULL,
                dedupe_key TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_delivery_dedupe
            ON delivery_ledger(destination, dedupe_key, payload_hash)
            """
        )
        conn.commit()
        conn.close()

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()]
        if "idempotency_key" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN idempotency_key TEXT")
        if "lease_expires_at" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN lease_expires_at REAL")

    def register_handler(self, task_type: str, handler: Callable[[dict[str, Any]], str]) -> None:
        """Register a handler function for a task type."""
        self._handlers[task_type] = handler
        logger.info(f"Registered task handler: {task_type}")

    def set_notify_callback(self, callback: Callable[[str, str, str], None]) -> None:
        """Set callback(user_id, channel, message) for progress notifications."""
        self._notify_callback = callback

    def enqueue(
        self,
        task_type: str,
        payload: dict[str, Any],
        channel: str = "system",
        user_id: str = "ceejay",
        priority: int = 3,
        max_retries: int = 3,
        idempotency_key: str | None = None,
    ) -> str:
        """Add a task to the queue. Returns task ID."""
        if idempotency_key:
            existing = self._find_active_by_idempotency(idempotency_key)
            if existing:
                logger.info(
                    f"Reusing task {existing} for idempotency_key={idempotency_key}"
                )
                return existing

        task_id = uuid.uuid4().hex[:16]
        now = time.time()
        conn = self._connect()
        conn.execute(
            """INSERT INTO tasks (id, task_type, payload, status, channel, user_id,
               created_at, updated_at, priority, max_retries, idempotency_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, task_type, json.dumps(payload), TaskStatus.PENDING,
             channel, user_id, now, now, priority, max_retries, idempotency_key),
        )
        conn.commit()
        conn.close()
        logger.info(f"Enqueued task {task_id} type={task_type}")
        return task_id

    def _find_active_by_idempotency(self, idempotency_key: str) -> str | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT id FROM tasks
                WHERE idempotency_key=?
                  AND status IN (?, ?, ?)
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (
                    idempotency_key,
                    TaskStatus.PENDING,
                    TaskStatus.RUNNING,
                    TaskStatus.COMPLETED,
                ),
            ).fetchone()
            return row["id"] if row else None
        finally:
            conn.close()

    def get_task(self, task_id: str) -> Task | None:
        conn = self._connect()
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        conn.close()
        if not row:
            return None
        return self._row_to_task(row)

    def get_pending_tasks(self, limit: int = 10) -> list[Task]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status=? AND updated_at<=? ORDER BY priority ASC, created_at ASC LIMIT ?",
            (TaskStatus.PENDING, time.time(), limit),
        ).fetchall()
        conn.close()
        return [self._row_to_task(r) for r in rows]

    def update_progress(self, task_id: str, progress: int, message: str = "") -> None:
        conn = self._connect()
        conn.execute(
            "UPDATE tasks SET progress=?, updated_at=? WHERE id=?",
            (progress, time.time(), task_id),
        )
        conn.commit()
        conn.close()
        if self._notify_callback and message:
            task = self.get_task(task_id)
            if task:
                self._notify_deduped(
                    user_id=task.user_id,
                    channel=task.channel,
                    dedupe_key=f"{task_id}:progress:{int(progress)}",
                    message=f"[Task {task_id[:8]}] {message} ({progress}%)",
                )

    def _complete_task(self, task_id: str, result: str) -> None:
        now = time.time()
        conn = self._connect()
        conn.execute(
            "UPDATE tasks SET status=?, result=?, completed_at=?, updated_at=?, progress=100, lease_expires_at=NULL WHERE id=?",
            (TaskStatus.COMPLETED, result, now, now, task_id),
        )
        conn.commit()
        conn.close()

    def _fail_task(self, task_id: str, error: str, retries: int) -> None:
        now = time.time()
        conn = self._connect()
        conn.execute(
            "UPDATE tasks SET status=?, error=?, retries=?, updated_at=?, lease_expires_at=NULL WHERE id=?",
            (TaskStatus.FAILED, error, retries, now, task_id),
        )
        conn.commit()
        conn.close()

    def _row_to_task(self, row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            task_type=row["task_type"],
            payload=json.loads(row["payload"]),
            status=TaskStatus(row["status"]),
            channel=row["channel"],
            user_id=row["user_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            result=row["result"],
            error=row["error"],
            retries=row["retries"],
            max_retries=row["max_retries"],
            progress=row["progress"],
            priority=row["priority"],
            metadata=json.loads(row["metadata"] or "{}"),
            idempotency_key=row["idempotency_key"],
            lease_expires_at=row["lease_expires_at"],
        )

    def _process_one(self) -> bool:
        """Pick and process one pending task. Returns True if a task was processed."""
        tasks = self.get_pending_tasks(limit=1)
        if not tasks:
            return False

        task = tasks[0]
        handler = self._handlers.get(task.task_type)
        if not handler:
            logger.warning(f"No handler for task type '{task.task_type}', failing task {task.id}")
            self._fail_task(task.id, f"No handler for type '{task.task_type}'", 0)
            return True

        # Mark as running
        conn = self._connect()
        conn.execute(
            "UPDATE tasks SET status=?, started_at=?, updated_at=?, lease_expires_at=? WHERE id=?",
            (TaskStatus.RUNNING, time.time(), time.time(), time.time() + 600, task.id),
        )
        conn.commit()
        conn.close()

        try:
            handler_payload = dict(task.payload or {})
            handler_payload.setdefault("_task_id", task.id)
            handler_payload.setdefault("_channel", task.channel)
            handler_payload.setdefault("_user_id", task.user_id)
            _handler_timeout = int(os.getenv("TASK_HANDLER_TIMEOUT_SEC", str(task.timeout_seconds if hasattr(task, "timeout_seconds") else 300)))
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _pool:
                _future = _pool.submit(handler, handler_payload)
                try:
                    result = _future.result(timeout=_handler_timeout)
                except concurrent.futures.TimeoutError:
                    raise RuntimeError(f"Handler timed out after {_handler_timeout}s")
            self._complete_task(task.id, result)
            logger.info(f"Task {task.id} completed")
            if self._notify_callback:
                self._notify_deduped(
                    user_id=task.user_id,
                    channel=task.channel,
                    dedupe_key=f"{task.id}:completed",
                    message=f"Task completed: {result[:200]}",
                )
            return True
        except Exception as e:
            retries = task.retries + 1
            if retries < task.max_retries:
                base_delay = float(os.getenv("TASK_RETRY_BASE_DELAY_SEC", "5"))
                max_delay = float(os.getenv("TASK_RETRY_MAX_DELAY_SEC", "120"))
                retry_delay = min(max_delay, base_delay * (2 ** max(0, retries - 1)))
                conn = self._connect()
                conn.execute(
                    "UPDATE tasks SET status=?, retries=?, updated_at=?, lease_expires_at=NULL WHERE id=?",
                    (TaskStatus.PENDING, retries, time.time() + retry_delay, task.id),
                )
                conn.commit()
                conn.close()
                logger.warning(f"Task {task.id} failed (attempt {retries}), re-queued in {retry_delay:.1f}s: {e}")
            else:
                self._fail_task(task.id, str(e), retries)
                logger.error(f"Task {task.id} permanently failed after {retries} attempts: {e}")
                if self._notify_callback:
                    self._notify_deduped(
                        user_id=task.user_id,
                        channel=task.channel,
                        dedupe_key=f"{task.id}:failed",
                        message=f"Task failed: {str(e)[:200]}",
                    )
            return True

    def _notify_deduped(self, user_id: str, channel: str, dedupe_key: str, message: str) -> None:
        if not self._notify_callback:
            return
        destination = f"{channel}:{user_id}"
        payload_hash = hashlib.sha256(str(message).encode("utf-8")).hexdigest()
        conn = self._connect()
        try:
            existing = conn.execute(
                """
                SELECT 1 FROM delivery_ledger
                WHERE destination=? AND dedupe_key=? AND payload_hash=?
                LIMIT 1
                """,
                (destination, dedupe_key, payload_hash),
            ).fetchone()
            if existing:
                logger.info(
                    f"Suppressed duplicate notification destination={destination} dedupe_key={dedupe_key}"
                )
                return
        finally:
            conn.close()

        # Only persist dedupe record after successful delivery callback.
        self._notify_callback(user_id, channel, message)
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO delivery_ledger(id, destination, dedupe_key, payload_hash, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (uuid.uuid4().hex, destination, dedupe_key, payload_hash, time.time()),
            )
            conn.commit()
        finally:
            conn.close()

    def start_worker(self, poll_interval: float = 5.0) -> None:
        """Start background worker thread."""
        if self._running:
            return
        self.recover_stale_tasks()
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop, args=(poll_interval,), daemon=True
        )
        self._worker_thread.start()
        logger.info("Task queue worker started")

    def _worker_loop(self, poll_interval: float) -> None:
        while self._running:
            try:
                processed = self._process_one()
                if not processed:
                    time.sleep(poll_interval)
            except Exception as e:
                logger.error(f"Worker loop error: {e}")
                time.sleep(poll_interval)

    def stop_worker(self) -> None:
        self._running = False
        if self._worker_thread:
            self._worker_thread.join(timeout=10)
        logger.info("Task queue worker stopped")

    def recover_stale_tasks(self, stale_seconds: float = 900.0) -> int:
        """Requeue stale running tasks to pending."""
        cutoff = time.time() - stale_seconds
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                UPDATE tasks
                SET status=?, updated_at=?, lease_expires_at=NULL
                WHERE status=? AND (
                    started_at <= ? OR
                    (lease_expires_at IS NOT NULL AND lease_expires_at <= ?)
                )
                """,
                (TaskStatus.PENDING, time.time(), TaskStatus.RUNNING, cutoff, time.time()),
            )
            conn.commit()
            recovered = cur.rowcount or 0
            if recovered:
                logger.warning(f"Recovered {recovered} stale running task(s)")
            return recovered
        finally:
            conn.close()

    def cancel_task(self, task_id: str) -> bool:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT status FROM tasks WHERE id=?",
                (task_id,),
            ).fetchone()
            if not row:
                return False
            if row["status"] in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                return False
            conn.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                (TaskStatus.CANCELLED, time.time(), task_id),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def list_recent_tasks(self, limit: int = 5) -> list[Task]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
            return [self._row_to_task(r) for r in rows]
        finally:
            conn.close()

    def run_crash_recovery_drill(self, stale_seconds: float = 900.0) -> dict[str, Any]:
        """
        Drill:
        1) Insert synthetic stale running task.
        2) Trigger stale-task recovery.
        3) Verify synthetic task returns to pending.
        """
        now = time.time()
        task_id = f"drill_{uuid.uuid4().hex[:12]}"
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO tasks(
                    id, task_type, payload, status, channel, user_id,
                    created_at, updated_at, started_at, retries, max_retries,
                    progress, priority, metadata, lease_expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    "drill_recovery",
                    json.dumps({"drill": "crash_recovery"}),
                    TaskStatus.RUNNING,
                    "system",
                    "drill_runner",
                    now - (stale_seconds + 120),
                    now - (stale_seconds + 120),
                    now - (stale_seconds + 120),
                    0,
                    1,
                    10,
                    9,
                    json.dumps({"drill": True}),
                    now - 60,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        recovered_count = self.recover_stale_tasks(stale_seconds=stale_seconds)
        post = self.get_task(task_id)
        passed = bool(post and post.status == TaskStatus.PENDING)
        return {
            "ok": passed,
            "task_id": task_id,
            "recovered_count": recovered_count,
            "post_status": post.status.value if post else "missing",
            "checked_at": time.time(),
        }
