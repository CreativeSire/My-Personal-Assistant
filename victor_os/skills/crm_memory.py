"""
Victor-OS Skill: CRM Memory
Track people, relationships, decisions, and follow-ups as structured memory.
Victor's personal relationship intelligence layer.
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

logger = get_logger("crm_memory_skill")


class CRMMemorySkill(Skill):
    """
    CRM-style contact and relationship memory.
    Tracks people, companies, decisions, notes, and follow-up reminders.
    """

    def __init__(self):
        self._cfg = get_config()
        self._de = get_data_engine()
        self._db_path = os.path.join(
            self._cfg.base_dir, "memory_store", "crm.db"
        )
        self._init_db()

    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="crm_memory",
            display_name="CRM Memory",
            version="1.0.0",
            description="Track people, relationships, decisions, and follow-ups as structured memory.",
            triggers=[
                r"\bwho is\b",
                r"\bcontact.*info\b",
                r"\bremember.*about\b",
                r"\badd.*contact\b",
                r"\bsave.*contact\b",
                r"\bnote.*about\b",
                r"\bfollow.*up.*with\b",
                r"\blast.*spoke.*to\b",
                r"\bcrm\b",
                r"\brelationship\b",
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
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS contacts (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                company TEXT DEFAULT '',
                email TEXT DEFAULT '',
                phone TEXT DEFAULT '',
                role TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                notes TEXT DEFAULT '',
                created_ts REAL NOT NULL,
                updated_ts REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS interactions (
                id TEXT PRIMARY KEY,
                contact_id TEXT NOT NULL,
                interaction_type TEXT NOT NULL,
                summary TEXT NOT NULL,
                decision TEXT DEFAULT '',
                follow_up TEXT DEFAULT '',
                follow_up_ts REAL,
                ts REAL NOT NULL,
                FOREIGN KEY (contact_id) REFERENCES contacts(id)
            );
        """)
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------ #
    # Tools                                                                #
    # ------------------------------------------------------------------ #

    def tool_add_contact(
        self,
        name: str,
        company: str = "",
        email: str = "",
        phone: str = "",
        role: str = "",
        notes: str = "",
        tags: str = "",
    ) -> str:
        """
        Add or update a contact.
        Args:
            name: Full name of the person.
            company: Company or organisation.
            email: Email address.
            phone: Phone number.
            role: Job title / role.
            notes: Free-form notes about this person.
            tags: Comma-separated tags (e.g. 'investor,advisor').
        """
        if not name:
            return "name is required."
        now = time.time()
        tags_list = [t.strip() for t in (tags or "").split(",") if t.strip()]

        # Check for existing contact with same name
        conn = self._connect()
        existing = conn.execute(
            "SELECT id FROM contacts WHERE lower(name)=? LIMIT 1",
            (name.lower(),),
        ).fetchone()

        if existing:
            contact_id = existing["id"]
            conn.execute(
                """UPDATE contacts SET company=?, email=?, phone=?, role=?,
                   tags=?, notes=?, updated_ts=? WHERE id=?""",
                (company, email, phone, role, json.dumps(tags_list), notes, now, contact_id),
            )
            conn.commit()
            conn.close()
            return f"Updated contact: {name} (id={contact_id})"

        contact_id = uuid.uuid4().hex[:10]
        conn.execute(
            """INSERT INTO contacts
               (id, name, company, email, phone, role, tags, notes, created_ts, updated_ts)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (contact_id, name, company, email, phone, role, json.dumps(tags_list), notes, now, now),
        )
        conn.commit()
        conn.close()
        return f"Contact saved: {name} (id={contact_id})"

    def tool_get_contact(self, name: str) -> str:
        """Look up a contact by name (fuzzy match)."""
        name = str(name or "").strip()
        if not name:
            return "name is required."
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM contacts WHERE lower(name) LIKE ? ORDER BY updated_ts DESC LIMIT 5",
            (f"%{name.lower()}%",),
        ).fetchall()
        if not rows:
            conn.close()
            return f"No contact found matching '{name}'."

        lines = []
        for r in rows:
            tags = json.loads(r["tags"] or "[]")
            lines.append(f"Name: {r['name']} (id={r['id']})")
            if r["company"]:
                lines.append(f"  Company: {r['company']}")
            if r["role"]:
                lines.append(f"  Role: {r['role']}")
            if r["email"]:
                lines.append(f"  Email: {r['email']}")
            if r["phone"]:
                lines.append(f"  Phone: {r['phone']}")
            if tags:
                lines.append(f"  Tags: {', '.join(tags)}")
            if r["notes"]:
                lines.append(f"  Notes: {r['notes'][:300]}")

            # Recent interactions
            interactions = conn.execute(
                "SELECT * FROM interactions WHERE contact_id=? ORDER BY ts DESC LIMIT 3",
                (r["id"],),
            ).fetchall()
            if interactions:
                lines.append("  Recent interactions:")
                for i in interactions:
                    ts = time.strftime("%Y-%m-%d", time.localtime(i["ts"]))
                    lines.append(f"    [{ts}] {i['interaction_type']}: {i['summary'][:100]}")
            lines.append("")
        conn.close()
        return "\n".join(lines)

    def tool_log_interaction(
        self,
        contact_name: str,
        interaction_type: str,
        summary: str,
        decision: str = "",
        follow_up: str = "",
        follow_up_hours: float = 0,
    ) -> str:
        """
        Log an interaction with a contact.
        Args:
            contact_name: Name of the person.
            interaction_type: Type of interaction (call, email, meeting, message).
            summary: What was discussed.
            decision: Any decision made.
            follow_up: Follow-up action needed.
            follow_up_hours: Hours until follow-up is due (0 = no follow-up).
        """
        contact_name = str(contact_name or "").strip()
        if not contact_name:
            return "contact_name is required."

        conn = self._connect()
        contact = conn.execute(
            "SELECT id FROM contacts WHERE lower(name) LIKE ? LIMIT 1",
            (f"%{contact_name.lower()}%",),
        ).fetchone()

        if not contact:
            # Auto-create contact if not found
            conn.close()
            self.tool_add_contact(name=contact_name)
            conn = self._connect()
            contact = conn.execute(
                "SELECT id FROM contacts WHERE lower(name) LIKE ? LIMIT 1",
                (f"%{contact_name.lower()}%",),
            ).fetchone()

        if not contact:
            conn.close()
            return f"Could not find or create contact: {contact_name}"

        now = time.time()
        follow_up_ts = now + float(follow_up_hours) * 3600 if follow_up_hours > 0 else None
        interaction_id = uuid.uuid4().hex[:10]

        conn.execute(
            """INSERT INTO interactions
               (id, contact_id, interaction_type, summary, decision, follow_up, follow_up_ts, ts)
               VALUES (?,?,?,?,?,?,?,?)""",
            (interaction_id, contact["id"], interaction_type, summary, decision, follow_up, follow_up_ts, now),
        )
        conn.execute(
            "UPDATE contacts SET updated_ts=? WHERE id=?",
            (now, contact["id"]),
        )
        conn.commit()
        conn.close()

        result = f"Logged {interaction_type} with {contact_name}."
        if decision:
            result += f" Decision: {decision}"
        if follow_up and follow_up_ts:
            due = time.strftime("%Y-%m-%d %H:%M", time.localtime(follow_up_ts))
            result += f" Follow-up: '{follow_up}' due {due}"
        return result

    def tool_list_follow_ups(self) -> str:
        """List all pending follow-ups due in the next 7 days."""
        now = time.time()
        cutoff = now + 7 * 86400
        conn = self._connect()
        rows = conn.execute(
            """SELECT i.*, c.name as contact_name FROM interactions i
               JOIN contacts c ON c.id = i.contact_id
               WHERE i.follow_up != '' AND i.follow_up_ts IS NOT NULL
               AND i.follow_up_ts > ? AND i.follow_up_ts < ?
               ORDER BY i.follow_up_ts ASC""",
            (now, cutoff),
        ).fetchall()
        conn.close()
        if not rows:
            return "No follow-ups due in the next 7 days."
        lines = [f"{len(rows)} follow-up(s) due:\n"]
        for r in rows:
            due = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["follow_up_ts"]))
            lines.append(f"• [{due}] {r['contact_name']}: {r['follow_up']}")
        return "\n".join(lines)

    def tool_search_contacts(self, query: str) -> str:
        """Search contacts by name, company, role, or tags."""
        query = str(query or "").strip()
        if not query:
            return "query is required."
        q = f"%{query.lower()}%"
        conn = self._connect()
        rows = conn.execute(
            """SELECT * FROM contacts
               WHERE lower(name) LIKE ? OR lower(company) LIKE ?
               OR lower(role) LIKE ? OR lower(tags) LIKE ?
               ORDER BY updated_ts DESC LIMIT 10""",
            (q, q, q, q),
        ).fetchall()
        conn.close()
        if not rows:
            return f"No contacts matching '{query}'."
        lines = [f"{len(rows)} contact(s) found:\n"]
        for r in rows:
            company = f" @ {r['company']}" if r["company"] else ""
            lines.append(f"• {r['name']}{company} | {r['email'] or r['phone'] or 'no contact info'}")
        return "\n".join(lines)

    def tool_crm_summary(self) -> str:
        """Return a high-level CRM summary: contact count, recent interactions, pending follow-ups."""
        conn = self._connect()
        total_contacts = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        total_interactions = conn.execute("SELECT COUNT(*) FROM interactions").fetchone()[0]
        pending_followups = conn.execute(
            "SELECT COUNT(*) FROM interactions WHERE follow_up != '' AND follow_up_ts > ?",
            (time.time(),),
        ).fetchone()[0]
        recent = conn.execute(
            """SELECT c.name, i.interaction_type, i.ts FROM interactions i
               JOIN contacts c ON c.id=i.contact_id
               ORDER BY i.ts DESC LIMIT 5""",
        ).fetchall()
        conn.close()
        lines = [
            f"CRM Summary:",
            f"  Contacts: {total_contacts}",
            f"  Interactions logged: {total_interactions}",
            f"  Pending follow-ups: {pending_followups}",
        ]
        if recent:
            lines.append("\nRecent interactions:")
            for r in recent:
                ts = time.strftime("%Y-%m-%d", time.localtime(r["ts"]))
                lines.append(f"  [{ts}] {r['name']} — {r['interaction_type']}")
        return "\n".join(lines)

    def get_proactive_checks(self) -> list[dict]:
        def _check():
            try:
                now = time.time()
                conn = self._connect()
                rows = conn.execute(
                    """SELECT i.follow_up, c.name FROM interactions i
                       JOIN contacts c ON c.id=i.contact_id
                       WHERE i.follow_up != '' AND i.follow_up_ts IS NOT NULL
                       AND i.follow_up_ts <= ? AND i.follow_up_ts > ?""",
                    (now, now - 86400),
                ).fetchall()
                conn.close()
                if rows:
                    items = [f"{r['name']}: {r['follow_up']}" for r in rows[:5]]
                    return "CRM follow-ups due:\n" + "\n".join(f"• {i}" for i in items)
            except Exception as exc:
                logger.debug(f"CRM proactive check failed: {exc}")
            return None

        return [
            {
                "name": "crm_follow_up_reminder",
                "interval_seconds": 3600,
                "callback": _check,
                "channel_hint": "telegram",
                "user_id": self._cfg.default_owner_user_id,
            }
        ]

    def get_tools(self) -> list[Callable]:
        return [
            self.tool_add_contact,
            self.tool_get_contact,
            self.tool_log_interaction,
            self.tool_list_follow_ups,
            self.tool_search_contacts,
            self.tool_crm_summary,
        ]


def create_skill():
    return CRMMemorySkill()
