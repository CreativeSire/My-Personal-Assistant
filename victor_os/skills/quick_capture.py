"""
Victor-OS Skill: Quick Capture
Fast capture of notes, tasks, ideas, and links with smart routing.
Anything sent to Victor that starts with a capture intent keyword
gets instantly filed in the right place.

Routing logic:
  "task: ..."       → task_queue
  "goal: ..."       → goal_tracker
  "idea: ..."       → memory with tag=idea
  "note: ..."       → memory with tag=note
  "link: <url>"     → memory with tag=link, optional fetch for title
  "remind: ..."     → memory with tag=reminder + timestamp
  "contact: ..."    → crm_memory
  "meeting: ..."    → memory with tag=meeting_note
  (anything else)   → memory with tag=quick_capture
"""
from __future__ import annotations

import re
import time
from typing import Callable

from config import get_config
from logging_config import get_logger
from skill_base import Skill, SkillManifest

logger = get_logger("quick_capture_skill")

# Ordered routing rules — first match wins
_CAPTURE_ROUTES = [
    # (trigger_pattern, route_key, clean_prefix)
    (r"^(task|todo|do):\s*", "task", True),
    (r"^(goal|objective|target):\s*", "goal", True),
    (r"^(idea|thought|brainstorm):\s*", "idea", True),
    (r"^(link|url|bookmark|save link):\s*", "link", True),
    (r"^(remind|reminder|remind me):\s*", "reminder", True),
    (r"^(contact|person|crm):\s*", "contact", True),
    (r"^(meeting|standup|sync):\s*", "meeting", True),
    (r"^(note|log|record):\s*", "note", True),
]


def _detect_route(text: str) -> tuple[str, str]:
    """Returns (route_key, cleaned_text)."""
    for pattern, route_key, strip_prefix in _CAPTURE_ROUTES:
        m = re.match(pattern, text.strip(), re.IGNORECASE)
        if m:
            body = text[m.end():].strip() if strip_prefix else text.strip()
            return route_key, body
    return "note", text.strip()


def _extract_url(text: str) -> str | None:
    m = re.search(r"https?://\S+", text)
    return m.group(0) if m else None


def _fetch_url_title(url: str) -> str:
    """Try to fetch page title for bookmark enrichment. Non-blocking."""
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            html = resp.read(8192).decode("utf-8", errors="replace")
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()[:120]
    except Exception:
        pass
    return ""


class QuickCaptureSkill(Skill):

    def __init__(self):
        self._cfg = get_config()

    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="quick_capture",
            display_name="Quick Capture",
            version="1.0.0",
            description="Instantly routes notes, tasks, ideas, links, contacts to the right store.",
            triggers=[
                r"\b(task|todo|do):\s+\S",
                r"\b(goal|objective):\s+\S",
                r"\b(idea|thought):\s+\S",
                r"\b(link|url|bookmark):\s+https?://",
                r"\b(remind|reminder):\s+\S",
                r"\b(note|log|record):\s+\S",
                r"\b(contact|person):\s+\S",
                r"\b(meeting|standup):\s+\S",
                r"\bcapture\b",
            ],
            enabled=True,
        )

    # ------------------------------------------------------------------ #
    # Routing handlers                                                     #
    # ------------------------------------------------------------------ #

    def _route_task(self, text: str) -> str:
        try:
            from task_queue import get_task_queue, TaskInput
            q = get_task_queue()
            task_id = q.enqueue(TaskInput(
                task_type="manual_task",
                title=text[:200],
                payload={"description": text, "source": "quick_capture"},
                priority="medium",
                channel="telegram",
            ))
            return f"Task queued: {text[:80]}{'…' if len(text) > 80 else ''} (id={task_id[:8]})"
        except Exception as exc:
            logger.debug(f"Task route failed: {exc}")
            return _save_to_memory(text, ["task", "quick_capture"])

    def _route_goal(self, text: str) -> str:
        try:
            from goal_tracker import get_goal_tracker
            tracker = get_goal_tracker()
            g = tracker.add_goal(name=text[:200], description=text)
            return f"Goal added: {text[:80]}{'…' if len(text) > 80 else ''}"
        except Exception as exc:
            logger.debug(f"Goal route failed: {exc}")
            return _save_to_memory(text, ["goal", "quick_capture"])

    def _route_link(self, text: str) -> str:
        url = _extract_url(text) or text
        title = _fetch_url_title(url)
        content = f"{title}\n{url}" if title else url
        return _save_to_memory(content, ["link", "bookmark", "quick_capture"])

    def _route_reminder(self, text: str) -> str:
        # Extract time expressions like "tomorrow", "friday", "at 3pm"
        ts = time.strftime("%Y-%m-%d %H:%M")
        content = f"[REMINDER set {ts}] {text}"
        return _save_to_memory(content, ["reminder", "quick_capture"])

    def _route_contact(self, text: str) -> str:
        try:
            from skill_registry import get_skill_registry
            registry = get_skill_registry()
            crm = registry.get_skill("crm_memory")
            if crm is not None:
                # Try to parse "Name: John Doe, email: john@example.com"
                name_m = re.search(r"(?:name|person):\s*([^,\n]+)", text, re.IGNORECASE)
                email_m = re.search(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", text)
                note_text = text
                name = name_m.group(1).strip() if name_m else text[:60]
                email = email_m.group(0) if email_m else ""
                result = crm.tool_add_contact(name=name, email=email, notes=note_text)
                return result
        except Exception as exc:
            logger.debug(f"CRM route failed: {exc}")
        return _save_to_memory(text, ["contact", "crm", "quick_capture"])

    def _route_meeting(self, text: str) -> str:
        return _save_to_memory(text, ["meeting_note", "quick_capture"])

    # ------------------------------------------------------------------ #
    # Main tool                                                            #
    # ------------------------------------------------------------------ #

    def tool_capture(self, text: str) -> str:
        """
        Capture a note, task, idea, link, or reminder and route it correctly.

        Prefix your input with a type to route it:
          task: Buy groceries
          goal: Run 5km three times a week
          idea: App that tracks water intake
          link: https://example.com
          remind: Call the dentist
          contact: Jane Smith, email: jane@example.com
          meeting: Q1 planning — decided to move launch to March
          note: Anything without a prefix goes here

        Args:
            text: The content to capture (with optional prefix).
        """
        text = str(text or "").strip()
        if not text:
            return "Nothing to capture. Provide some text."

        route, body = _detect_route(text)

        if not body:
            return "Capture text cannot be empty after stripping the prefix."

        router_map = {
            "task": self._route_task,
            "goal": self._route_goal,
            "link": self._route_link,
            "reminder": self._route_reminder,
            "contact": self._route_contact,
            "meeting": self._route_meeting,
            "idea": lambda t: _save_to_memory(t, ["idea", "quick_capture"]),
            "note": lambda t: _save_to_memory(t, ["note", "quick_capture"]),
        }

        handler = router_map.get(route, lambda t: _save_to_memory(t, ["quick_capture"]))
        result = handler(body)
        logger.info(f"Quick capture [{route}]: {body[:60]}")
        return result

    def tool_recent_captures(self, limit: int = 10) -> str:
        """Show the most recently captured items."""
        try:
            from memory_core import recall_memory
            results = recall_memory("quick_capture recent", top_k=int(limit))
            if not results:
                return "No recent captures found."
            lines = [f"Recent captures ({len(results)}):"]
            for i, r in enumerate(results, 1):
                content = getattr(r, "content", str(r))
                tags = getattr(r, "tags", [])
                tag_str = f" [{', '.join(tags[:3])}]" if tags else ""
                snippet = content[:100].replace("\n", " ")
                lines.append(f"  {i}.{tag_str} {snippet}{'…' if len(content) > 100 else ''}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Could not retrieve captures: {exc}"

    def get_tools(self) -> list[Callable]:
        return [
            self.tool_capture,
            self.tool_recent_captures,
        ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _save_to_memory(text: str, tags: list[str]) -> str:
    """Fallback: save to memory_core and return confirmation."""
    try:
        from memory_core import MemoryRecordInput, save_memory
        save_memory(MemoryRecordInput(
            content=text,
            tags=tags,
            source="quick_capture",
        ))
        route_label = tags[0] if tags else "note"
        snippet = text[:80]
        return f"Saved [{route_label}]: {snippet}{'…' if len(text) > 80 else ''}"
    except Exception as exc:
        logger.error(f"Memory save failed: {exc}")
        return f"Capture failed: {exc}"


def create_skill():
    return QuickCaptureSkill()
