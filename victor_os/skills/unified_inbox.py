"""
Victor-OS Skill: Unified Inbox
Aggregates messages from Telegram, WhatsApp, and Email into a single
prioritised triage queue. Victor surfaces what needs attention first.
"""
from __future__ import annotations

import time
from typing import Callable

from config import get_config
from data_engine import get_data_engine
from logging_config import get_logger
from skill_base import Skill, SkillManifest

logger = get_logger("unified_inbox_skill")

_PRIORITY_HIGH = "high"
_PRIORITY_MED = "medium"
_PRIORITY_LOW = "low"

_URGENT_KEYWORDS = frozenset([
    "urgent", "asap", "immediately", "critical", "help", "emergency",
    "deadline", "overdue", "action required", "invoice due", "payment failed",
])


def _score_priority(sender: str, content: str) -> str:
    text = (sender + " " + content).lower()
    if any(k in text for k in _URGENT_KEYWORDS):
        return _PRIORITY_HIGH
    if len(content) > 200:
        return _PRIORITY_MED
    return _PRIORITY_LOW


class UnifiedInboxSkill(Skill):
    """
    Unified inbox that pulls from Telegram, WhatsApp (Twilio), and Email,
    de-duplicates, and surfaces a prioritised triage view.
    """

    def __init__(self):
        self._cfg = get_config()
        self._de = get_data_engine()
        # In-memory unified queue: list of dicts
        self._queue: list[dict] = []
        self._acknowledged: set[str] = set()

    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="unified_inbox",
            display_name="Unified Inbox",
            version="1.0.0",
            description="Unified triage view across Telegram, WhatsApp, and Email.",
            triggers=[
                r"\bunified.*inbox\b",
                r"\ball.*messages\b",
                r"\btriage\b",
                r"\bwhat.*messages\b",
                r"\binbox.*summary\b",
                r"\bunread.*messages\b",
                r"\bcheck.*all.*channels\b",
            ],
            enabled=True,
        )

    # ------------------------------------------------------------------ #
    # Source fetchers                                                      #
    # ------------------------------------------------------------------ #

    def _fetch_email_messages(self, limit: int = 10) -> list[dict]:
        if not self._cfg.email_user:
            return []
        try:
            import imaplib
            import email as email_lib
            host = __import__("os").getenv("EMAIL_IMAP_HOST", "imap.gmail.com")
            port = int(__import__("os").getenv("EMAIL_IMAP_PORT", "993"))
            conn = imaplib.IMAP4_SSL(host, port)
            conn.login(self._cfg.email_user, self._cfg.email_pass)
            conn.select("INBOX", readonly=True)
            _t, data = conn.search(None, "UNSEEN")
            ids = (data[0] or b"").split()
            messages = []
            for uid in reversed(ids[-limit:]):
                _t2, msg_data = conn.fetch(uid, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else None
                if not raw:
                    continue
                msg = email_lib.message_from_bytes(raw)
                messages.append({
                    "id": f"email:{uid.decode()}",
                    "channel": "email",
                    "sender": msg.get("From", ""),
                    "subject": msg.get("Subject", ""),
                    "content": msg.get("Subject", ""),
                    "ts": time.time(),
                    "priority": _score_priority(msg.get("From", ""), msg.get("Subject", "")),
                })
            conn.logout()
            return messages
        except Exception as exc:
            logger.debug(f"Unified inbox email fetch failed: {exc}")
            return []

    def _fetch_data_engine_messages(self, limit: int = 20) -> list[dict]:
        """Pull recent user messages from data_engine event log."""
        try:
            events = self._de.list_recent_events(limit=limit)
            messages = []
            for e in events:
                payload = e.get("payload", {}) if isinstance(e, dict) else {}
                if not payload:
                    continue
                content = str(payload.get("message") or payload.get("text") or "")
                if not content:
                    continue
                channel = str(e.get("channel", "unknown") if isinstance(e, dict) else "unknown")
                sender = str(payload.get("user_id") or payload.get("actor") or "user")
                messages.append({
                    "id": f"de:{e.get('event_id', '') if isinstance(e, dict) else ''}",
                    "channel": channel,
                    "sender": sender,
                    "subject": "",
                    "content": content[:300],
                    "ts": float(e.get("ts_utc", time.time()) if isinstance(e, dict) else time.time()),
                    "priority": _score_priority(sender, content),
                })
            return messages
        except Exception as exc:
            logger.debug(f"Data engine message fetch failed: {exc}")
            return []

    # ------------------------------------------------------------------ #
    # Tools                                                                #
    # ------------------------------------------------------------------ #

    def tool_triage_inbox(self, limit: int = 15) -> str:
        """
        Show a unified, priority-sorted view of unread messages across all channels.
        """
        limit = min(int(limit), 50)
        all_msgs: list[dict] = []

        # Email
        email_msgs = self._fetch_email_messages(limit=limit)
        all_msgs.extend(email_msgs)

        # Recent data engine messages (Telegram, WhatsApp, etc.)
        de_msgs = self._fetch_data_engine_messages(limit=limit)
        all_msgs.extend(de_msgs)

        # De-dup by id and filter acknowledged
        seen = set()
        unique = []
        for m in all_msgs:
            if m["id"] not in seen and m["id"] not in self._acknowledged:
                seen.add(m["id"])
                unique.append(m)

        # Sort: high priority first, then by time desc
        priority_order = {_PRIORITY_HIGH: 0, _PRIORITY_MED: 1, _PRIORITY_LOW: 2}
        unique.sort(key=lambda m: (priority_order.get(m["priority"], 3), -m["ts"]))

        # Store in queue for ack tracking
        self._queue = unique

        if not unique:
            return "Unified inbox is clear — no unread messages."

        lines = [f"Unified inbox: {len(unique)} item(s)\n"]
        high = [m for m in unique if m["priority"] == _PRIORITY_HIGH]
        if high:
            lines.append(f"🔴 HIGH PRIORITY ({len(high)}):")
            for m in high[:5]:
                label = m["subject"] or m["content"][:60]
                lines.append(f"  [{m['channel']}] {m['sender']}: {label}")
            lines.append("")

        med = [m for m in unique if m["priority"] == _PRIORITY_MED]
        if med:
            lines.append(f"🟡 MEDIUM ({len(med)}):")
            for m in med[:5]:
                label = m["subject"] or m["content"][:60]
                lines.append(f"  [{m['channel']}] {m['sender']}: {label}")
            lines.append("")

        low = [m for m in unique if m["priority"] == _PRIORITY_LOW]
        if low:
            lines.append(f"⚪ LOW ({len(low)}):")
            for m in low[:3]:
                label = m["subject"] or m["content"][:60]
                lines.append(f"  [{m['channel']}] {m['sender']}: {label}")

        return "\n".join(lines)

    def tool_acknowledge_message(self, message_id: str) -> str:
        """Mark a message as acknowledged so it won't appear in future triage views."""
        message_id = str(message_id or "").strip()
        if not message_id:
            return "message_id is required."
        self._acknowledged.add(message_id)
        return f"Message {message_id} acknowledged."

    def tool_clear_inbox(self) -> str:
        """Acknowledge all current messages in the unified inbox."""
        count = len(self._queue)
        for m in self._queue:
            self._acknowledged.add(m["id"])
        self._queue.clear()
        return f"Cleared {count} message(s) from unified inbox."

    def tool_inbox_stats(self) -> str:
        """Return channel-by-channel inbox statistics."""
        email_count = len(self._fetch_email_messages(limit=50))
        de_count = len(self._fetch_data_engine_messages(limit=50))
        acknowledged = len(self._acknowledged)
        lines = [
            "Unified Inbox Stats:",
            f"  Email unread: {email_count}",
            f"  Recent activity events: {de_count}",
            f"  Acknowledged (session): {acknowledged}",
            f"  Queued for triage: {len(self._queue)}",
        ]
        email_status = "connected" if self._cfg.email_user else "not configured"
        whatsapp_status = "enabled" if self._cfg.whatsapp_enabled else "disabled"
        lines.extend([
            "\nChannel status:",
            f"  Email: {email_status}",
            f"  Telegram: active",
            f"  WhatsApp: {whatsapp_status}",
        ])
        return "\n".join(lines)

    def get_proactive_checks(self) -> list[dict]:
        def _check():
            try:
                msgs = self._fetch_email_messages(limit=10)
                high_priority = [m for m in msgs if m["priority"] == _PRIORITY_HIGH
                                 and m["id"] not in self._acknowledged]
                if high_priority:
                    return (
                        f"Unified inbox: {len(high_priority)} high-priority message(s) need attention. "
                        f"Latest: {high_priority[0]['sender']} — {high_priority[0]['content'][:80]}"
                    )
            except Exception as exc:
                logger.debug(f"Unified inbox proactive check failed: {exc}")
            return None

        return [
            {
                "name": "unified_inbox_check",
                "interval_seconds": 600,
                "callback": _check,
                "channel_hint": "telegram",
                "user_id": self._cfg.default_owner_user_id,
            }
        ]

    def get_tools(self) -> list[Callable]:
        return [
            self.tool_triage_inbox,
            self.tool_acknowledge_message,
            self.tool_clear_inbox,
            self.tool_inbox_stats,
        ]


def create_skill():
    return UnifiedInboxSkill()
