"""
Victor-OS Skill: Email Intelligence
Reads inbox, summarizes threads, flags urgent emails, and drafts replies.
Feature-flagged via EMAIL_USER env var.
"""
from __future__ import annotations

import email as email_lib
import imaplib
import json
import os
import re
import smtplib
import time
from email.mime.text import MIMEText
from typing import Callable

from config import get_config
from data_engine import get_data_engine
from logging_config import get_logger
from skill_base import Skill, SkillManifest

logger = get_logger("email_intelligence_skill")

_URGENT_PATTERNS = re.compile(
    r"\b(urgent|asap|immediately|critical|action required|deadline|overdue|invoice due)\b",
    re.IGNORECASE,
)


class EmailIntelligenceSkill(Skill):
    """
    Email inbox intelligence: reads, summarizes, flags urgent, drafts replies.
    Uses IMAP for reading and SMTP for sending (same credentials as existing email notifier).
    """

    def __init__(self):
        self._cfg = get_config()
        self._de = get_data_engine()

    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="email_intelligence",
            display_name="Email Intelligence",
            version="1.0.0",
            description="Reads inbox, summarizes threads, flags urgent emails, and drafts replies.",
            triggers=[
                r"\bcheck.*email\b",
                r"\bmy.*inbox\b",
                r"\bunread.*email\b",
                r"\bemail.*summary\b",
                r"\bany.*email\b",
                r"\bdraft.*reply\b",
                r"\breply.*to\b",
                r"\bmark.*read\b",
            ],
            enabled=bool(self._cfg.email_user),
        )

    # ------------------------------------------------------------------ #
    # Internal IMAP helpers                                                #
    # ------------------------------------------------------------------ #

    def _imap_connect(self) -> imaplib.IMAP4_SSL:
        host = os.getenv("EMAIL_IMAP_HOST", "imap.gmail.com")
        port = int(os.getenv("EMAIL_IMAP_PORT", "993"))
        conn = imaplib.IMAP4_SSL(host, port)
        conn.login(self._cfg.email_user, self._cfg.email_pass)
        return conn

    def _fetch_messages(self, limit: int = 10, folder: str = "INBOX") -> list[dict]:
        messages: list[dict] = []
        try:
            conn = self._imap_connect()
            conn.select(folder, readonly=True)
            _typ, data = conn.search(None, "UNSEEN")
            ids = (data[0] or b"").split()
            # Most recent first
            for uid in reversed(ids[-limit:]):
                _typ2, msg_data = conn.fetch(uid, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else None
                if not raw:
                    continue
                msg = email_lib.message_from_bytes(raw)
                body = self._extract_body(msg)
                messages.append({
                    "uid": uid.decode(),
                    "from": msg.get("From", ""),
                    "subject": msg.get("Subject", "(no subject)"),
                    "date": msg.get("Date", ""),
                    "body": body[:2000],
                    "urgent": bool(_URGENT_PATTERNS.search(msg.get("Subject", "") + " " + body[:500])),
                })
            conn.logout()
        except Exception as exc:
            logger.warning(f"IMAP fetch failed: {exc}")
        return messages

    @staticmethod
    def _extract_body(msg: email_lib.message.Message) -> str:
        body_parts: list[str] = []
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    try:
                        body_parts.append(part.get_payload(decode=True).decode("utf-8", errors="replace"))
                    except Exception:
                        pass
        else:
            try:
                body_parts.append(msg.get_payload(decode=True).decode("utf-8", errors="replace"))
            except Exception:
                pass
        return "\n".join(body_parts)

    def _send_email(self, to: str, subject: str, body: str) -> None:
        host = os.getenv("EMAIL_SMTP_HOST", "smtp.gmail.com")
        port = int(os.getenv("EMAIL_SMTP_PORT", "587"))
        msg = MIMEText(body, "plain", "utf-8")
        msg["From"] = self._cfg.email_user
        msg["To"] = to
        msg["Subject"] = subject
        with smtplib.SMTP(host, port) as s:
            s.starttls()
            s.login(self._cfg.email_user, self._cfg.email_pass)
            s.sendmail(self._cfg.email_user, [to], msg.as_string())

    # ------------------------------------------------------------------ #
    # Tools                                                                #
    # ------------------------------------------------------------------ #

    def tool_check_inbox(self, limit: int = 10) -> str:
        """Fetch up to `limit` unread emails and return a human-readable summary."""
        if not self._cfg.email_user:
            return "Email not configured (EMAIL_USER missing)."
        messages = self._fetch_messages(limit=int(limit))
        if not messages:
            return "No unread emails found."
        lines = [f"Found {len(messages)} unread email(s):\n"]
        for i, m in enumerate(messages, 1):
            urgent_tag = " [URGENT]" if m["urgent"] else ""
            lines.append(f"{i}. {urgent_tag}From: {m['from']}")
            lines.append(f"   Subject: {m['subject']}")
            lines.append(f"   Date: {m['date']}")
            lines.append(f"   Preview: {m['body'][:150].strip()}")
            lines.append("")
        return "\n".join(lines)

    def tool_flag_urgent(self) -> str:
        """Return only urgent unread emails."""
        if not self._cfg.email_user:
            return "Email not configured."
        messages = self._fetch_messages(limit=20)
        urgent = [m for m in messages if m["urgent"]]
        if not urgent:
            return "No urgent emails found."
        lines = [f"{len(urgent)} urgent email(s):\n"]
        for m in urgent:
            lines.append(f"From: {m['from']}")
            lines.append(f"Subject: {m['subject']}")
            lines.append(f"Preview: {m['body'][:200].strip()}\n")
        return "\n".join(lines)

    def tool_draft_reply(self, to: str, subject: str, context: str) -> str:
        """
        Draft a reply given recipient, subject, and context.
        Returns the drafted text for review — does NOT send automatically.
        """
        if not to or not subject:
            return "Both 'to' and 'subject' are required."
        draft = (
            f"Draft reply to: {to}\n"
            f"Subject: Re: {subject}\n"
            f"---\n"
            f"Hi,\n\n"
            f"Thank you for your email regarding \"{subject}\".\n\n"
            f"{context.strip()}\n\n"
            f"Best regards,\nVictor"
        )
        return draft

    def tool_send_reply(self, to: str, subject: str, body: str) -> str:
        """Send an email reply. Requires explicit user confirmation before calling."""
        if not self._cfg.email_user:
            return "Email not configured."
        try:
            self._send_email(to, f"Re: {subject}", body)
            self._de.emit_event(
                self._de.build_event_envelope(
                    "action.executed",
                    session_id="email_intelligence",
                    task_id="",
                    actor="email_intelligence",
                    payload={"action": "send_reply", "to": to, "subject": subject},
                    risk_score=0.3,
                    channel="system",
                    source="email_intelligence",
                )
            )
            return f"Reply sent to {to}."
        except Exception as exc:
            return f"Failed to send: {exc}"

    def tool_email_summary(self) -> str:
        """Return a brief summary of inbox state."""
        messages = self._fetch_messages(limit=20)
        if not messages:
            return "Inbox clean — no unread emails."
        total = len(messages)
        urgent = sum(1 for m in messages if m["urgent"])
        senders = list({m["from"] for m in messages})[:5]
        return (
            f"Inbox: {total} unread, {urgent} urgent.\n"
            f"Recent senders: {', '.join(senders)}"
        )

    def get_proactive_checks(self) -> list[dict]:
        if not self._cfg.email_user:
            return []

        def _check():
            try:
                messages = self._fetch_messages(limit=20)
                urgent = [m for m in messages if m["urgent"]]
                if urgent:
                    return f"You have {len(urgent)} urgent unread email(s). Latest: {urgent[0]['subject']}"
            except Exception as exc:
                logger.debug(f"Email proactive check failed: {exc}")
            return None

        return [
            {
                "name": "email_urgent_check",
                "interval_seconds": 900,
                "callback": _check,
                "channel_hint": "telegram",
                "user_id": self._cfg.default_owner_user_id,
            }
        ]

    def get_tools(self) -> list[Callable]:
        return [
            self.tool_check_inbox,
            self.tool_flag_urgent,
            self.tool_draft_reply,
            self.tool_send_reply,
            self.tool_email_summary,
        ]


def create_skill():
    return EmailIntelligenceSkill()
