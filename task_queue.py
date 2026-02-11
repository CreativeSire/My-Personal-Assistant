"""
Victor-OS Task Queue
SQLite-backed async job queue with worker thread, retry, and progress tracking.
"""

import os
import json
import time
import uuid
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
                metadata TEXT DEFAULT '{}'
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_status ON tasks(status, priority)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_type ON tasks(task_type)")
        conn.commit()
        conn.close()

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
    ) -> str:
        """Add a task to the queue. Returns task ID."""
        task_id = uuid.uuid4().hex[:16]
        now = time.time()
        conn = self._connect()
        conn.execute(
            """INSERT INTO tasks (id, task_type, payload, status, channel, user_id,
               created_at, updated_at, priority, max_retries)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, task_type, json.dumps(payload), TaskStatus.PENDING,
             channel, user_id, now, now, priority, max_retries),
        )
        conn.commit()
        conn.close()
        logger.info(f"Enqueued task {task_id} type={task_type}")
        return task_id

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
            "SELECT * FROM tasks WHERE status=? ORDER BY priority ASC, created_at ASC LIMIT ?",
            (TaskStatus.PENDING, limit),
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
                self._notify_callback(task.user_id, task.channel, f"[Task {task_id[:8]}] {message} ({progress}%)")

    def _complete_task(self, task_id: str, result: str) -> None:
        now = time.time()
        conn = self._connect()
        conn.execute(
            "UPDATE tasks SET status=?, result=?, completed_at=?, updated_at=?, progress=100 WHERE id=?",
            (TaskStatus.COMPLETED, result, now, now, task_id),
        )
        conn.commit()
        conn.close()

    def _fail_task(self, task_id: str, error: str, retries: int) -> None:
        now = time.time()
        conn = self._connect()
        conn.execute(
            "UPDATE tasks SET status=?, error=?, retries=?, updated_at=? WHERE id=?",
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
            "UPDATE tasks SET status=?, started_at=?, updated_at=? WHERE id=?",
            (TaskStatus.RUNNING, time.time(), time.time(), task.id),
        )
        conn.commit()
        conn.close()

        try:
            result = handler(task.payload)
            self._complete_task(task.id, result)
            logger.info(f"Task {task.id} completed")
            if self._notify_callback:
                self._notify_callback(task.user_id, task.channel, f"Task completed: {result[:200]}")
            return True
        except Exception as e:
            retries = task.retries + 1
            if retries < task.max_retries:
                conn = self._connect()
                conn.execute(
                    "UPDATE tasks SET status=?, retries=?, updated_at=? WHERE id=?",
                    (TaskStatus.PENDING, retries, time.time(), task.id),
                )
                conn.commit()
                conn.close()
                logger.warning(f"Task {task.id} failed (attempt {retries}), re-queued: {e}")
            else:
                self._fail_task(task.id, str(e), retries)
                logger.error(f"Task {task.id} permanently failed after {retries} attempts: {e}")
                if self._notify_callback:
                    self._notify_callback(task.user_id, task.channel, f"Task failed: {str(e)[:200]}")
            return True

    def start_worker(self, poll_interval: float = 5.0) -> None:
        """Start background worker thread."""
        if self._running:
            return
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
