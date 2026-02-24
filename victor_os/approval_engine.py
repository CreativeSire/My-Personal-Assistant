"""
Victor-OS Approval Engine
==========================
Manages approval workflows for elevated/irreversible tool calls.

Flow:
  1. process_message() checks if tool is flagged as elevated
  2. approval_engine.request_approval(session_id, tool_name, description, callback)
  3. Victor sends owner a confirmation message with an approval token
  4. Owner replies: /approve <token> or /deny <token>
  5. approval_engine resolves the pending request → runs or cancels the callback

Storage: SQLite with 5-minute TTL on pending approvals.

Rate limiting: configurable per-tool per-user daily limits.
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
import threading
from dataclasses import dataclass
from typing import Callable, Optional

from config import get_config
from logging_config import get_logger

logger = get_logger("approval_engine")
_cfg = get_config()

_DB_PATH = os.path.join(_cfg.base_dir, "memory_store", "approvals.db")
_APPROVAL_TTL_SECONDS = 300     # 5 minutes — after this, request expires
_RATE_LIMIT_WINDOW = 86400      # 24 hours


@dataclass
class ApprovalRequest:
    token: str
    session_id: str
    user_id: str
    tool_name: str
    description: str
    created_at: float
    expires_at: float
    status: str = "pending"     # pending | approved | denied | expired


class ApprovalEngine:
    """SQLite-backed approval workflow manager."""

    def __init__(self, db_path: str = _DB_PATH):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._pending_callbacks: dict[str, Callable] = {}
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS approval_requests (
                    token       TEXT PRIMARY KEY,
                    session_id  TEXT NOT NULL,
                    user_id     TEXT NOT NULL,
                    tool_name   TEXT NOT NULL,
                    description TEXT NOT NULL,
                    created_at  REAL NOT NULL,
                    expires_at  REAL NOT NULL,
                    status      TEXT NOT NULL DEFAULT 'pending'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rate_limits (
                    user_id    TEXT NOT NULL,
                    tool_name  TEXT NOT NULL,
                    day_bucket TEXT NOT NULL,
                    count      INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, tool_name, day_bucket)
                )
            """)
            conn.commit()

    def _day_bucket(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

    def check_rate_limit(self, user_id: str, tool_name: str, max_per_day: int = 20) -> bool:
        """Returns True if under limit, False if exceeded."""
        bucket = self._day_bucket()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT count FROM rate_limits WHERE user_id=? AND tool_name=? AND day_bucket=?",
                (user_id, tool_name, bucket),
            ).fetchone()
        count = int(row["count"]) if row else 0
        return count < max_per_day

    def increment_rate_limit(self, user_id: str, tool_name: str) -> None:
        bucket = self._day_bucket()
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO rate_limits (user_id, tool_name, day_bucket, count)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(user_id, tool_name, day_bucket) DO UPDATE SET count = count + 1
            """, (user_id, tool_name, bucket))
            conn.commit()

    def request_approval(
        self,
        session_id: str,
        user_id: str,
        tool_name: str,
        description: str,
        on_approved: Callable,
        on_denied: Optional[Callable] = None,
    ) -> str:
        """
        Create a pending approval request.
        Returns the approval token (show this to owner so they can /approve <token>).
        """
        token = uuid.uuid4().hex[:8].upper()
        now = time.time()
        req = ApprovalRequest(
            token=token,
            session_id=session_id,
            user_id=user_id,
            tool_name=tool_name,
            description=description,
            created_at=now,
            expires_at=now + _APPROVAL_TTL_SECONDS,
        )
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO approval_requests
                    (token, session_id, user_id, tool_name, description, created_at, expires_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
            """, (
                req.token, req.session_id, req.user_id, req.tool_name,
                req.description, req.created_at, req.expires_at,
            ))
            conn.commit()

        with self._lock:
            self._pending_callbacks[token] = {"approved": on_approved, "denied": on_denied}

        logger.info(f"Approval requested: token={token} tool={tool_name} user={user_id}")
        return token

    def resolve(self, token: str, approved: bool) -> tuple[bool, str]:
        """
        Resolve a pending approval (called by /approve or /deny handler).
        Returns (success, message).
        """
        token = token.upper().strip()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM approval_requests WHERE token=?", (token,)
            ).fetchone()

        if not row:
            return False, f"No approval request found for token: {token}"

        if row["status"] != "pending":
            return False, f"Token {token} already {row['status']}."

        if time.time() > row["expires_at"]:
            with self._conn() as conn:
                conn.execute(
                    "UPDATE approval_requests SET status='expired' WHERE token=?", (token,)
                )
                conn.commit()
            return False, f"Token {token} has expired."

        # Update status
        status = "approved" if approved else "denied"
        with self._conn() as conn:
            conn.execute(
                "UPDATE approval_requests SET status=? WHERE token=?", (status, token)
            )
            conn.commit()

        # Run callback
        with self._lock:
            callbacks = self._pending_callbacks.pop(token, {})

        tool_name = row["tool_name"]
        user_id = row["user_id"]

        if approved:
            self.increment_rate_limit(user_id, tool_name)
            cb = callbacks.get("approved")
            if cb:
                try:
                    cb()
                except Exception as exc:
                    logger.error(f"Approved callback failed for token={token}: {exc}")
                    return True, f"Approved, but execution failed: {exc}"
            return True, f"Approved ✅ — {tool_name} executing."
        else:
            cb = callbacks.get("denied")
            if cb:
                try:
                    cb()
                except Exception:
                    pass
            return True, f"Denied ❌ — {tool_name} cancelled."

    def pending_for_owner(self) -> list[dict]:
        """Return all pending, non-expired requests (for /pending command)."""
        now = time.time()
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT token, user_id, tool_name, description, expires_at
                FROM approval_requests
                WHERE status='pending' AND expires_at > ?
                ORDER BY created_at DESC
                LIMIT 20
            """, (now,)).fetchall()
        return [dict(r) for r in rows]

    def expire_stale(self) -> int:
        """Expire any pending requests past their TTL. Returns count expired."""
        now = time.time()
        with self._conn() as conn:
            cursor = conn.execute("""
                UPDATE approval_requests SET status='expired'
                WHERE status='pending' AND expires_at <= ?
            """, (now,))
            conn.commit()
            count = cursor.rowcount

        if count:
            # Clean up in-memory callbacks for expired tokens
            with self._lock:
                with self._conn() as conn:
                    expired = conn.execute(
                        "SELECT token FROM approval_requests WHERE status='expired'"
                    ).fetchall()
                for row in expired:
                    self._pending_callbacks.pop(row["token"], None)

        return count


# Decorator: mark a skill tool as requiring approval
def requires_approval(tool_name: str, description: str, max_per_day: int = 10):
    """
    Decorator for skill tool functions to flag them as requiring owner approval.
    Attach metadata — actual enforcement happens in process_message().
    """
    def decorator(fn):
        fn._requires_approval = True
        fn._approval_tool_name = tool_name
        fn._approval_description = description
        fn._approval_max_per_day = max_per_day
        return fn
    return decorator


# Module-level singleton
_engine: ApprovalEngine | None = None


def get_approval_engine() -> ApprovalEngine:
    global _engine
    if _engine is None:
        _engine = ApprovalEngine()
    return _engine
