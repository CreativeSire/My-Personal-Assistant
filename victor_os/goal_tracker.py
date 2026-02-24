"""
Victor-OS Goal Tracker
SQLite-backed goal persistence with CRUD operations, auto-detection, and proactive checks.
Supports both explicit goal creation via tools and auto-detection from conversation.
"""

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from config import get_config
from logging_config import get_logger

logger = get_logger("goal_tracker")
cfg = get_config()

_DB_PATH = os.path.join(cfg.base_dir, "memory_store", "victor_goals.db")


@dataclass
class Goal:
    id: str
    title: str
    description: str
    status: str  # active, completed, paused, abandoned
    priority: int  # 1-5 (1=highest)
    created_at: float
    updated_at: float
    target_date: float | None = None
    completed_at: float | None = None
    source: str = "explicit"  # explicit, auto_detected, workflow
    tags: list[str] = field(default_factory=list)
    progress: int = 0
    notes: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class GoalCandidate:
    """A potential goal detected from user text."""
    title: str
    description: str
    priority: int = 3
    target_date: float | None = None


# ---------------------------------------------------------------------------
# Auto-detection patterns
# ---------------------------------------------------------------------------

_GOAL_PATTERNS = [
    # "I need to <goal>"
    re.compile(r"\bi\s+need\s+to\s+(.{10,80}?)(?:\.|$|,\s)", re.IGNORECASE),
    # "I want to <goal>"
    re.compile(r"\bi\s+want\s+to\s+(.{10,80}?)(?:\.|$|,\s)", re.IGNORECASE),
    # "Let's build/create/make <goal>"
    re.compile(r"\blet(?:'s|us)\s+(?:build|create|make|implement|develop|set up)\s+(.{10,80}?)(?:\.|$|,\s)", re.IGNORECASE),
    # "We should <goal>"
    re.compile(r"\bwe\s+should\s+(.{10,80}?)(?:\.|$|,\s)", re.IGNORECASE),
    # "Remind me to <goal>"
    re.compile(r"\bremind\s+me\s+to\s+(.{10,80}?)(?:\.|$|,\s)", re.IGNORECASE),
    # "Don't forget to <goal>"
    re.compile(r"\bdon'?t\s+forget\s+to\s+(.{10,80}?)(?:\.|$|,\s)", re.IGNORECASE),
    # "The plan is to <goal>"
    re.compile(r"\bthe\s+plan\s+is\s+to\s+(.{10,80}?)(?:\.|$|,\s)", re.IGNORECASE),
    # "Goal is to <goal>"
    re.compile(r"\b(?:my\s+)?goal\s+is\s+to\s+(.{10,80}?)(?:\.|$|,\s)", re.IGNORECASE),
    # "I have to <goal> by <date>"
    re.compile(r"\bi\s+have\s+to\s+(.{10,80}?)(?:\.|$|,\s)", re.IGNORECASE),
]

_DATE_PATTERNS = [
    # "by Friday", "by next week", "by March", "by 2026-03-15"
    re.compile(r"\bby\s+(\w+(?:\s+\w+)?(?:\s+\d{1,4})?)\b", re.IGNORECASE),
    # "before March 15"
    re.compile(r"\bbefore\s+(\w+\s+\d{1,2}(?:,?\s+\d{4})?)\b", re.IGNORECASE),
    # ISO dates
    re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),
]


# ---------------------------------------------------------------------------
# GoalTracker
# ---------------------------------------------------------------------------

class GoalTracker:
    """SQLite-backed goal persistence with full lifecycle management."""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or _DB_PATH
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self._db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS goals (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                priority INTEGER DEFAULT 3,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                target_date REAL,
                completed_at REAL,
                source TEXT DEFAULT 'explicit',
                tags TEXT DEFAULT '[]',
                progress INTEGER DEFAULT 0,
                notes TEXT DEFAULT '[]'
            )
        """)
        conn.commit()
        conn.close()

    def _row_to_goal(self, row: tuple) -> Goal:
        return Goal(
            id=row[0],
            title=row[1],
            description=row[2] or "",
            status=row[3],
            priority=row[4],
            created_at=row[5],
            updated_at=row[6],
            target_date=row[7],
            completed_at=row[8],
            source=row[9] or "explicit",
            tags=json.loads(row[10]) if row[10] else [],
            progress=row[11] or 0,
            notes=json.loads(row[12]) if row[12] else [],
        )

    def create_goal(
        self,
        title: str,
        description: str = "",
        priority: int = 3,
        target_date: float | None = None,
        tags: list[str] | None = None,
        source: str = "explicit",
    ) -> str:
        """Create a new goal. Returns the goal ID."""
        goal_id = uuid.uuid4().hex[:12]
        now = time.time()
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            """INSERT INTO goals (id, title, description, status, priority, created_at, updated_at,
               target_date, source, tags, progress, notes)
               VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, 0, '[]')""",
            (
                goal_id,
                title.strip(),
                description.strip(),
                max(1, min(5, priority)),
                now,
                now,
                target_date,
                source,
                json.dumps(tags or []),
            ),
        )
        conn.commit()
        conn.close()
        logger.info(f"Goal created: {goal_id} '{title}' (source={source})")
        return goal_id

    def update_goal(
        self,
        goal_id: str,
        status: str | None = None,
        progress: int | None = None,
        note: str | None = None,
        priority: int | None = None,
    ) -> bool:
        """Update a goal's status, progress, or add a note."""
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM goals WHERE id = ?", (goal_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return False

        goal = self._row_to_goal(row)
        now = time.time()
        updates = ["updated_at = ?"]
        params: list[Any] = [now]

        if status and status in ("active", "completed", "paused", "abandoned"):
            updates.append("status = ?")
            params.append(status)
            if status == "completed":
                updates.append("completed_at = ?")
                params.append(now)
                updates.append("progress = ?")
                params.append(100)

        if progress is not None:
            updates.append("progress = ?")
            params.append(max(0, min(100, progress)))

        if priority is not None:
            updates.append("priority = ?")
            params.append(max(1, min(5, priority)))

        if note:
            notes = goal.notes
            notes.append({"timestamp": now, "text": note.strip()})
            updates.append("notes = ?")
            params.append(json.dumps(notes))

        params.append(goal_id)
        conn.execute(f"UPDATE goals SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
        conn.close()
        logger.info(f"Goal updated: {goal_id} status={status} progress={progress}")
        return True

    def complete_goal(self, goal_id: str, note: str = "") -> bool:
        """Mark a goal as completed."""
        return self.update_goal(goal_id, status="completed", note=note or "Goal completed.")

    def get_goal(self, goal_id: str) -> Goal | None:
        """Get a single goal by ID."""
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM goals WHERE id = ?", (goal_id,))
        row = cursor.fetchone()
        conn.close()
        return self._row_to_goal(row) if row else None

    def list_goals(self, status_filter: str | None = None, limit: int = 20) -> list[Goal]:
        """List goals, optionally filtered by status."""
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()
        if status_filter and status_filter in ("active", "completed", "paused", "abandoned"):
            cursor.execute(
                "SELECT * FROM goals WHERE status = ? ORDER BY priority ASC, updated_at DESC LIMIT ?",
                (status_filter, limit),
            )
        else:
            cursor.execute(
                "SELECT * FROM goals ORDER BY priority ASC, updated_at DESC LIMIT ?",
                (limit,),
            )
        rows = cursor.fetchall()
        conn.close()
        return [self._row_to_goal(row) for row in rows]

    def get_overdue_goals(self) -> list[Goal]:
        """Get goals past their target date that are still active."""
        now = time.time()
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM goals WHERE status = 'active' AND target_date IS NOT NULL AND target_date < ?",
            (now,),
        )
        rows = cursor.fetchall()
        conn.close()
        return [self._row_to_goal(row) for row in rows]

    def get_stale_goals(self, days: int = 7) -> list[Goal]:
        """Get active goals with no updates in the specified number of days."""
        cutoff = time.time() - (days * 86400)
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM goals WHERE status = 'active' AND updated_at < ?",
            (cutoff,),
        )
        rows = cursor.fetchall()
        conn.close()
        return [self._row_to_goal(row) for row in rows]

    def detect_goals_from_text(self, user_text: str, response_text: str = "") -> list[GoalCandidate]:
        """Detect potential goals from user text using regex pattern matching.

        Uses simple patterns to identify goal-like statements. Does NOT use LLM.
        Returns a list of GoalCandidate objects that can be persisted.
        """
        candidates = []
        seen_titles = set()

        for pattern in _GOAL_PATTERNS:
            matches = pattern.findall(user_text)
            for match in matches:
                title = match.strip().rstrip(".,;:!?")
                if len(title) < 10 or title.lower() in seen_titles:
                    continue
                seen_titles.add(title.lower())

                # Try to detect a deadline
                target_date = None
                for date_pat in _DATE_PATTERNS:
                    date_match = date_pat.search(user_text)
                    if date_match:
                        # Store the raw date string as a note; don't try to parse complex dates
                        break

                candidates.append(
                    GoalCandidate(
                        title=title[:120],
                        description=user_text[:300],
                        priority=3,
                        target_date=target_date,
                    )
                )

        return candidates[:3]  # Cap at 3 per message to avoid noise


# Module-level singleton
_tracker: GoalTracker | None = None
_tracker_lock = threading.Lock()


def get_goal_tracker() -> GoalTracker:
    """Get or create the singleton GoalTracker instance (thread-safe)."""
    global _tracker
    if _tracker is None:
        with _tracker_lock:
            if _tracker is None:
                _tracker = GoalTracker()
    return _tracker
