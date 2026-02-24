"""
Victor-OS Skill: Task Delegation Tracker
Track tasks assigned to other people, auto follow-up reminders,
and closure confirmation.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from typing import Callable

from config import get_config
from data_engine import get_data_engine
from logging_config import get_logger
from skill_base import Skill, SkillManifest

logger = get_logger("task_delegation_skill")

_STATUS_OPEN = "open"
_STATUS_DONE = "done"
_STATUS_OVERDUE = "overdue"


class TaskDelegationSkill(Skill):
    """
    Track tasks delegated to other people (not Victor's internal tasks).
    Stores in its own SQLite table and fires proactive follow-up reminders.
    """

    def __init__(self):
        self._cfg = get_config()
        self._de = get_data_engine()
        self._db_path = os.path.join(
            self._cfg.base_dir, "memory_store", "delegation.db"
        )
        self._init_db()

    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="task_delegation",
            display_name="Task Delegation Tracker",
            version="1.0.0",
            description="Track tasks delegated to others, auto follow-up reminders, closure tracking.",
            triggers=[
                r"\bdelegate\b",
                r"\bassign.*task\b",
                r"\bfollow.*up\b",
                r"\bwaiting.*on\b",
                r"\bchase.*up\b",
                r"\bask.*[A-Z][a-z]+.*to\b",
                r"\bremind.*[A-Z][a-z]+\b",
                r"\bdelegation.*status\b",
                r"\bpending.*from\b",
            ],
            enabled=True,
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        conn = self._connect()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS delegations (
                id TEXT PRIMARY KEY,
                assignee TEXT NOT NULL,
                task TEXT NOT NULL,
                due_ts REAL,
                follow_up_interval_hours REAL DEFAULT 24,
                last_follow_up_ts REAL,
                status TEXT NOT NULL DEFAULT 'open',
                notes TEXT DEFAULT '',
                created_ts REAL NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------ #
    # Tools                                                                #
    # ------------------------------------------------------------------ #

    def tool_delegate_task(
        self,
        assignee: str,
        task: str,
        due_in_hours: float = 24,
        follow_up_hours: float = 24,
    ) -> str:
        """
        Record a new delegated task.
        Args:
            assignee: Name of person responsible.
            task: Description of the task.
            due_in_hours: Hours from now until due (default 24).
            follow_up_hours: How often to send follow-up reminders (default 24).
        """
        if not assignee or not task:
            return "Both 'assignee' and 'task' are required."
        now = time.time()
        task_id = uuid.uuid4().hex[:10]
        due_ts = now + float(due_in_hours) * 3600
        conn = self._connect()
        conn.execute(
            """INSERT INTO delegations
               (id, assignee, task, due_ts, follow_up_interval_hours, last_follow_up_ts, status, created_ts)
               VALUES (?,?,?,?,?,?,?,?)""",
            (task_id, assignee, task, due_ts, float(follow_up_hours), now, _STATUS_OPEN, now),
        )
        conn.commit()
        conn.close()
        due_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(due_ts))
        return f"Delegation recorded (id={task_id}): {assignee} — {task} | Due: {due_str}"

    def tool_list_delegations(self, status: str = "open") -> str:
        """
        List delegated tasks by status: 'open', 'done', 'overdue', or 'all'.
        """
        conn = self._connect()
        if status == "all":
            rows = conn.execute(
                "SELECT * FROM delegations ORDER BY due_ts ASC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM delegations WHERE status=? ORDER BY due_ts ASC",
                (status,),
            ).fetchall()
        conn.close()
        if not rows:
            return f"No {status} delegations."
        now = time.time()
        lines = [f"{len(rows)} delegation(s) [{status}]:\n"]
        for r in rows:
            due = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["due_ts"])) if r["due_ts"] else "no due date"
            overdue = " [OVERDUE]" if r["due_ts"] and r["due_ts"] < now and r["status"] == _STATUS_OPEN else ""
            lines.append(f"[{r['id']}] {r['assignee']}: {r['task']}")
            lines.append(f"  Status: {r['status']}{overdue} | Due: {due}")
            if r["notes"]:
                lines.append(f"  Notes: {r['notes']}")
            lines.append("")
        return "\n".join(lines)

    def tool_close_delegation(self, task_id: str, notes: str = "") -> str:
        """Mark a delegated task as done."""
        task_id = str(task_id or "").strip()
        if not task_id:
            return "task_id is required."
        conn = self._connect()
        cur = conn.execute(
            "UPDATE delegations SET status=?, notes=? WHERE id=?",
            (_STATUS_DONE, notes or "", task_id),
        )
        conn.commit()
        conn.close()
        if cur.rowcount == 0:
            return f"Delegation {task_id} not found."
        return f"Delegation {task_id} marked as done."

    def tool_snooze_delegation(self, task_id: str, hours: float = 24) -> str:
        """Snooze follow-up reminders for a delegation by N hours."""
        task_id = str(task_id or "").strip()
        if not task_id:
            return "task_id is required."
        new_follow_up = time.time() + float(hours) * 3600
        conn = self._connect()
        cur = conn.execute(
            "UPDATE delegations SET last_follow_up_ts=? WHERE id=?",
            (new_follow_up, task_id),
        )
        conn.commit()
        conn.close()
        if cur.rowcount == 0:
            return f"Delegation {task_id} not found."
        return f"Delegation {task_id} snoozed for {hours}h."

    def tool_overdue_report(self) -> str:
        """Return all overdue open delegations."""
        now = time.time()
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM delegations WHERE status=? AND due_ts < ? ORDER BY due_ts ASC",
            (_STATUS_OPEN, now),
        ).fetchall()
        conn.close()
        if not rows:
            return "No overdue delegations."
        lines = [f"{len(rows)} overdue delegation(s):\n"]
        for r in rows:
            overdue_hours = (now - r["due_ts"]) / 3600
            lines.append(f"[{r['id']}] {r['assignee']}: {r['task']}")
            lines.append(f"  Overdue by {overdue_hours:.1f}h\n")
        return "\n".join(lines)

    def get_proactive_checks(self) -> list[dict]:
        def _check():
            try:
                now = time.time()
                conn = self._connect()
                rows = conn.execute(
                    "SELECT * FROM delegations WHERE status=?", (_STATUS_OPEN,)
                ).fetchall()
                conn.close()
                alerts: list[str] = []
                for r in rows:
                    due_ts = r["due_ts"] or 0
                    last_fu = r["last_follow_up_ts"] or r["created_ts"]
                    interval_secs = float(r["follow_up_interval_hours"]) * 3600
                    # Mark overdue
                    if due_ts and due_ts < now:
                        alerts.append(f"OVERDUE: {r['assignee']} — {r['task']}")
                    elif now - last_fu >= interval_secs:
                        alerts.append(f"Follow-up needed: {r['assignee']} — {r['task']}")
                        # Update last follow-up time
                        conn2 = self._connect()
                        conn2.execute(
                            "UPDATE delegations SET last_follow_up_ts=? WHERE id=?",
                            (now, r["id"]),
                        )
                        conn2.commit()
                        conn2.close()
                if alerts:
                    return "Delegation reminders:\n" + "\n".join(f"• {a}" for a in alerts[:5])
            except Exception as exc:
                logger.debug(f"Delegation proactive check failed: {exc}")
            return None

        return [
            {
                "name": "delegation_follow_up",
                "interval_seconds": 1800,
                "callback": _check,
                "channel_hint": "telegram",
                "user_id": self._cfg.default_owner_user_id,
            }
        ]

    def get_tools(self) -> list[Callable]:
        return [
            self.tool_delegate_task,
            self.tool_list_delegations,
            self.tool_close_delegation,
            self.tool_snooze_delegation,
            self.tool_overdue_report,
        ]


def create_skill():
    return TaskDelegationSkill()
