"""
Victor-OS Skill: Daily Digest
Morning briefing that pulls everything Victor knows about your day:
calendar events, pending tasks, overdue goals, market snapshot,
urgent emails, and a memory recap — all in one message.
"""
from __future__ import annotations

import time
from typing import Callable

from config import get_config
from logging_config import get_logger
from skill_base import Skill, SkillManifest

logger = get_logger("daily_digest_skill")


class DailyDigestSkill(Skill):
    """
    Aggregates data from every available skill and delivers a
    single, structured morning briefing via Telegram.
    """

    def __init__(self):
        self._cfg = get_config()

    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="daily_digest",
            display_name="Daily Digest",
            version="1.0.0",
            description="Morning briefing: tasks, goals, calendar, markets, emails, memory recap.",
            triggers=[
                r"\bdaily\s+digest\b",
                r"\bmorning\s+brief",
                r"\bmorning\s+summary\b",
                r"\bwhat.*today\b",
                r"\bmy\s+day\b",
                r"\bdigest\b",
                r"\bbriefing\b",
            ],
            enabled=True,
        )

    # ------------------------------------------------------------------ #
    # Section builders — each is gracefully independent                   #
    # ------------------------------------------------------------------ #

    def _section_header(self, title: str) -> str:
        return f"\n{'─' * 40}\n{title}\n{'─' * 40}"

    def _section_tasks(self) -> str:
        try:
            from task_queue import get_task_queue
            q = get_task_queue()
            tasks = q.list_tasks(status="pending")
            if not tasks:
                return ""
            lines = [self._section_header("📋 Pending Tasks")]
            for t in tasks[:8]:
                title = t.get("title") or t.get("task_type") or "untitled"
                priority = t.get("priority", "")
                lines.append(f"  • [{priority}] {title}")
            if len(tasks) > 8:
                lines.append(f"  … and {len(tasks) - 8} more")
            return "\n".join(lines)
        except Exception as exc:
            logger.debug(f"Tasks section failed: {exc}")
            return ""

    def _section_goals(self) -> str:
        try:
            from goal_tracker import get_goal_tracker
            tracker = get_goal_tracker()
            goals = tracker.list_goals()
            if not goals:
                return ""
            active = [g for g in goals if getattr(g, "status", "") not in {"completed", "archived"}]
            if not active:
                return ""
            lines = [self._section_header("🎯 Active Goals")]
            for g in active[:6]:
                name = getattr(g, "name", str(g))
                progress = getattr(g, "progress_pct", None)
                pstr = f" ({progress:.0f}%)" if progress is not None else ""
                lines.append(f"  • {name}{pstr}")
            return "\n".join(lines)
        except Exception as exc:
            logger.debug(f"Goals section failed: {exc}")
            return ""

    def _section_calendar(self) -> str:
        try:
            from skill_registry import get_skill_registry
            registry = get_skill_registry()
            cal = registry.get_skill("calendar_sync")
            if cal is None:
                return ""
            agenda = cal.tool_get_todays_agenda()
            if not agenda or "No events" in agenda or "not configured" in agenda.lower():
                return ""
            return self._section_header("📅 Today's Calendar") + "\n" + agenda
        except Exception as exc:
            logger.debug(f"Calendar section failed: {exc}")
            return ""

    def _section_markets(self) -> str:
        try:
            from skill_registry import get_skill_registry
            registry = get_skill_registry()
            mw = registry.get_skill("market_watch")
            if mw is None:
                return ""
            snapshot = mw.tool_price_snapshot()
            if not snapshot or "unavailable" in snapshot.lower():
                return ""
            return self._section_header("📈 Market Snapshot") + "\n" + snapshot
        except Exception as exc:
            logger.debug(f"Markets section failed: {exc}")
            return ""

    def _section_emails(self) -> str:
        try:
            from skill_registry import get_skill_registry
            registry = get_skill_registry()
            email_skill = registry.get_skill("email_intelligence")
            if email_skill is None:
                return ""
            summary = email_skill.tool_check_inbox(max_messages=10)
            if not summary or "no new" in summary.lower() or "not configured" in summary.lower():
                return ""
            # Only include urgent ones in digest
            lines = []
            for line in summary.splitlines():
                if any(kw in line.upper() for kw in ["URGENT", "HIGH", "ACTION", "CRITICAL"]):
                    lines.append(line)
            if not lines:
                return ""
            return self._section_header("📧 Urgent Emails") + "\n" + "\n".join(lines[:5])
        except Exception as exc:
            logger.debug(f"Emails section failed: {exc}")
            return ""

    def _section_memory_recap(self) -> str:
        """Pull recent memories from the last 24h as a lightweight recap."""
        try:
            from memory_core import recall_memory
            recent = recall_memory("today plans tasks goals", top_k=5)
            if not recent:
                return ""
            lines = [self._section_header("🧠 Memory Recap")]
            for r in recent[:4]:
                content = getattr(r, "content", str(r))
                snippet = content[:120].replace("\n", " ")
                lines.append(f"  • {snippet}{'…' if len(content) > 120 else ''}")
            return "\n".join(lines)
        except Exception as exc:
            logger.debug(f"Memory recap failed: {exc}")
            return ""

    def _section_overdue_delegations(self) -> str:
        try:
            from skill_registry import get_skill_registry
            registry = get_skill_registry()
            delg = registry.get_skill("task_delegation")
            if delg is None:
                return ""
            report = delg.tool_overdue_delegations()
            if not report or "no overdue" in report.lower():
                return ""
            return self._section_header("⚠️ Overdue Delegations") + "\n" + report[:400]
        except Exception as exc:
            logger.debug(f"Delegations section failed: {exc}")
            return ""

    def _section_self_training(self) -> str:
        """Show if training data is ready to export."""
        try:
            from skill_registry import get_skill_registry
            registry = get_skill_registry()
            trainer = registry.get_skill("self_trainer")
            if trainer is None:
                return ""
            status = trainer.tool_training_status()
            if "ready" in status.lower() or "sufficient" in status.lower():
                return self._section_header("🤖 Self-Training") + "\n" + status[:200]
            return ""
        except Exception as exc:
            logger.debug(f"Self-training section failed: {exc}")
            return ""

    # ------------------------------------------------------------------ #
    # Tool                                                                 #
    # ------------------------------------------------------------------ #

    def tool_daily_digest(self) -> str:
        """
        Generate and return the full daily digest.
        Pulls from: tasks, goals, calendar, markets, emails, memory, delegations.
        """
        now = time.strftime("%A, %B %d %Y — %H:%M")
        header = f"Good morning! Here's your Victor Digest\n{now}"

        sections = [
            self._section_tasks(),
            self._section_goals(),
            self._section_calendar(),
            self._section_markets(),
            self._section_emails(),
            self._section_overdue_delegations(),
            self._section_memory_recap(),
            self._section_self_training(),
        ]

        body = "\n".join(s for s in sections if s)

        if not body:
            return (
                f"{header}\n\n"
                "Everything looks clear — no pending tasks, goals, or alerts.\n"
                "Have a great day!"
            )

        footer = "\n\n─────────────────────────────────────\nSend /capture to add a note, /tasks to manage tasks."
        return header + body + footer

    def _proactive_morning_digest(self) -> str | None:
        """Proactive check: send digest at 08:00 ± 30 min once per day."""
        import datetime
        now = datetime.datetime.now()
        hour, minute = now.hour, now.minute
        if not (7 <= hour < 9):
            return None

        # Guard: only fire once per day using a simple state flag on self
        today = now.strftime("%Y-%m-%d")
        if getattr(self, "_last_digest_date", None) == today:
            return None

        self._last_digest_date = today
        return self.tool_daily_digest()

    def get_proactive_checks(self) -> list[dict]:
        return [
            {
                "name": "morning_digest",
                "interval_seconds": 1800,  # Check every 30 min, fire logic guards actual send
                "callback": self._proactive_morning_digest,
                "channel_hint": "telegram",
                "user_id": self._cfg.default_owner_user_id,
            }
        ]

    def get_tools(self) -> list[Callable]:
        return [self.tool_daily_digest]


def create_skill():
    return DailyDigestSkill()
