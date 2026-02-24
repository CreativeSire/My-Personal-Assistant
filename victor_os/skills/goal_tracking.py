"""
Victor-OS Goal Tracking Skill
Exposes goal CRUD operations as FunctionTools and registers proactive checks
for overdue/stale goals with the ProactiveEngine.
"""

import json
import time

from config import get_config
from goal_tracker import get_goal_tracker
from logging_config import get_logger
from skill_base import Skill, SkillManifest

logger = get_logger("skill.goal_tracking")


class GoalTrackingSkill(Skill):
    """Skill that provides goal tracking tools and proactive goal health checks."""

    def __init__(self):
        self._cfg = get_config()

    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="goal_tracking",
            display_name="Goal Tracker",
            version="1.0.0",
            description="Track goals, deadlines, and progress. Auto-detects goals from conversation.",
            triggers=[
                r"\b(create|set|add)\s+goal\b",
                r"\bmy\s+goals?\b",
                r"\bgoal\s+status\b",
                r"\btrack\b.*\b(goal|progress)\b",
                r"\bcomplete\s+goal\b",
                r"\blist\s+goals?\b",
            ],
            enabled=True,
            author="system",
        )

    def get_tools(self):
        return [
            self.tool_create_goal,
            self.tool_update_goal,
            self.tool_list_goals,
            self.tool_complete_goal,
        ]

    def get_proactive_checks(self):
        return [
            {
                "name": "goal_overdue_check",
                "interval_seconds": 3600,
                "callback": self._check_overdue_goals,
                "channel_hint": "telegram",
                "user_id": self._cfg.default_owner_user_id,
            },
            {
                "name": "goal_stale_check",
                "interval_seconds": 7200,
                "callback": self._check_stale_goals,
                "channel_hint": "telegram",
                "user_id": self._cfg.default_owner_user_id,
            },
        ]

    # --- Tool functions ---

    def tool_create_goal(
        self,
        title: str,
        description: str = "",
        priority: int = 3,
        target_date: str = "",
    ) -> str:
        """Create a new tracked goal with optional priority and deadline.

        Args:
            title: Short descriptive title for the goal.
            description: Detailed description of what needs to be accomplished.
            priority: Priority level 1-5 (1=highest, 5=lowest). Default: 3.
            target_date: Optional deadline in ISO format (YYYY-MM-DD) or empty string.

        Returns:
            Confirmation with the new goal ID.
        """
        tracker = get_goal_tracker()

        parsed_date = None
        if target_date and target_date.strip():
            try:
                import datetime
                dt = datetime.datetime.strptime(target_date.strip(), "%Y-%m-%d")
                parsed_date = dt.timestamp()
            except ValueError:
                pass

        goal_id = tracker.create_goal(
            title=title,
            description=description,
            priority=priority,
            target_date=parsed_date,
            source="explicit",
        )

        deadline_note = f" (deadline: {target_date})" if target_date else ""
        return f"Goal created successfully.\n- ID: {goal_id}\n- Title: {title}\n- Priority: {priority}{deadline_note}"

    def tool_update_goal(
        self,
        goal_id: str,
        status: str = "",
        progress: int = -1,
        note: str = "",
    ) -> str:
        """Update an existing goal's status, progress, or add a note.

        Args:
            goal_id: The goal ID to update.
            status: New status — 'active', 'paused', or 'abandoned'. Use tool_complete_goal for completion.
            progress: Progress percentage (0-100). Pass -1 to skip.
            note: Optional note to add to the goal's history.

        Returns:
            Confirmation of the update.
        """
        tracker = get_goal_tracker()
        success = tracker.update_goal(
            goal_id=goal_id,
            status=status if status else None,
            progress=progress if progress >= 0 else None,
            note=note if note else None,
        )
        if not success:
            return f"Goal '{goal_id}' not found."
        parts = [f"Goal {goal_id} updated."]
        if status:
            parts.append(f"Status → {status}")
        if progress >= 0:
            parts.append(f"Progress → {progress}%")
        if note:
            parts.append(f"Note added.")
        return " ".join(parts)

    def tool_list_goals(self, status_filter: str = "") -> str:
        """List all tracked goals, optionally filtered by status.

        Args:
            status_filter: Filter by status — 'active', 'completed', 'paused', 'abandoned', or empty for all.

        Returns:
            Formatted list of goals with status, priority, and progress.
        """
        tracker = get_goal_tracker()
        goals = tracker.list_goals(
            status_filter=status_filter if status_filter else None,
            limit=20,
        )

        if not goals:
            return "No goals found." + (f" (filter: {status_filter})" if status_filter else "")

        lines = [f"Goals ({len(goals)} found):"]
        for g in goals:
            deadline = ""
            if g.target_date:
                import datetime
                try:
                    dt = datetime.datetime.fromtimestamp(g.target_date)
                    deadline = f" | deadline: {dt.strftime('%Y-%m-%d')}"
                except Exception:
                    pass
            lines.append(
                f"  [{g.status.upper()}] {g.title}\n"
                f"    ID: {g.id} | Priority: {g.priority} | Progress: {g.progress}%{deadline}"
            )
        return "\n".join(lines)

    def tool_complete_goal(self, goal_id: str, note: str = "") -> str:
        """Mark a goal as completed.

        Args:
            goal_id: The goal ID to complete.
            note: Optional completion note.

        Returns:
            Confirmation of completion.
        """
        tracker = get_goal_tracker()
        success = tracker.complete_goal(goal_id, note=note)
        if not success:
            return f"Goal '{goal_id}' not found."
        return f"Goal {goal_id} marked as COMPLETED. Well done."

    # --- Proactive checks ---

    def _check_overdue_goals(self) -> str | None:
        """Proactive check: alert if any active goals are past their deadline."""
        tracker = get_goal_tracker()
        overdue = tracker.get_overdue_goals()
        if not overdue:
            return None

        lines = [f"Goal Deadline Alert: {len(overdue)} goal(s) overdue"]
        for g in overdue[:5]:
            lines.append(f"- [{g.id}] {g.title} (priority: {g.priority}, progress: {g.progress}%)")

        payload = {
            "alert": "goals_overdue",
            "severity": "warning",
            "count": len(overdue),
            "message": "\n".join(lines),
        }
        return json.dumps(payload)

    def _check_stale_goals(self) -> str | None:
        """Proactive check: alert if goals have had no updates in 7 days."""
        tracker = get_goal_tracker()
        stale = tracker.get_stale_goals(days=7)
        if not stale:
            return None

        lines = [f"Stale Goals Alert: {len(stale)} goal(s) with no activity in 7+ days"]
        for g in stale[:5]:
            lines.append(f"- [{g.id}] {g.title} (priority: {g.priority}, progress: {g.progress}%)")

        payload = {
            "alert": "goals_stale",
            "severity": "info",
            "count": len(stale),
            "message": "\n".join(lines),
        }
        return json.dumps(payload)


def create_skill() -> GoalTrackingSkill:
    """Factory function for skill registry auto-discovery."""
    return GoalTrackingSkill()
