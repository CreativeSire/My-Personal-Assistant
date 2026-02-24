"""
Victor-OS User Registry
Central database of all users — profiles, roles, data modes, consent flags.
Replaces the flat authorized_users config tuple.
"""

import os
import json
import sqlite3
import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional
from pathlib import Path

from config import get_config

cfg = get_config()
_DB_PATH = Path(cfg.base_dir) / "memory_store" / "users.db"

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

ROLE_OWNER = "owner"
ROLE_TRUSTED = "trusted"
ROLE_VIEWER = "viewer"
VALID_ROLES = {ROLE_OWNER, ROLE_TRUSTED, ROLE_VIEWER}

DATA_MODE_ISOLATED = "isolated"
DATA_MODE_SHARED = "shared"
DATA_MODE_COLLABORATIVE = "collaborative"
VALID_DATA_MODES = {DATA_MODE_ISOLATED, DATA_MODE_SHARED, DATA_MODE_COLLABORATIVE}

# Default consent: messages always on; everything else requires explicit opt-in
DEFAULT_CONSENT = {
    "messages": True,
    "voice": False,
    "images": False,
    "files": False,
    "urls": False,
    "screen": False,
    "browser": False,
    "calendar": False,
    "mic": False,
}


@dataclass
class UserProfile:
    telegram_id: str
    name: str
    role: str = ROLE_VIEWER
    data_mode: str = DATA_MODE_ISOLATED
    onboarded: bool = False
    joined_at: str = ""
    last_seen: str = ""
    profile_summary: str = ""
    consent_flags: dict = field(default_factory=lambda: dict(DEFAULT_CONSENT))

    @property
    def is_owner(self) -> bool:
        return self.role == ROLE_OWNER

    @property
    def is_trusted(self) -> bool:
        return self.role in (ROLE_OWNER, ROLE_TRUSTED)

    @property
    def can_write(self) -> bool:
        """Trusted and owner can write data; viewers are read-only."""
        return self.role in (ROLE_OWNER, ROLE_TRUSTED)

    @property
    def agent_scope(self) -> str:
        """The agent_name key used for per-user memory scoping."""
        return f"user_{self.telegram_id}"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class UserRegistry:
    def __init__(self, db_path: Path = _DB_PATH):
        self._db = str(db_path)
        os.makedirs(os.path.dirname(self._db), exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id   TEXT PRIMARY KEY,
                    name          TEXT NOT NULL DEFAULT '',
                    role          TEXT NOT NULL DEFAULT 'viewer',
                    data_mode     TEXT NOT NULL DEFAULT 'isolated',
                    onboarded     INTEGER NOT NULL DEFAULT 0,
                    joined_at     TEXT NOT NULL DEFAULT '',
                    last_seen     TEXT NOT NULL DEFAULT '',
                    profile_summary TEXT NOT NULL DEFAULT '',
                    consent_flags TEXT NOT NULL DEFAULT '{}'
                )
            """)
            # Migration: add columns introduced after initial deploy
            for col, definition in [
                ("data_mode", "TEXT NOT NULL DEFAULT 'isolated'"),
                ("profile_summary", "TEXT NOT NULL DEFAULT ''"),
                ("consent_flags", "TEXT NOT NULL DEFAULT '{}'"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
                except Exception:
                    pass  # Column already exists

    def _conn(self):
        conn = sqlite3.connect(self._db)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def _row_to_profile(self, row) -> UserProfile:
        try:
            consent = json.loads(row["consent_flags"] or "{}")
        except Exception:
            consent = {}
        flags = {**DEFAULT_CONSENT, **consent}
        return UserProfile(
            telegram_id=row["telegram_id"],
            name=row["name"],
            role=row["role"],
            data_mode=row["data_mode"],
            onboarded=bool(row["onboarded"]),
            joined_at=row["joined_at"],
            last_seen=row["last_seen"],
            profile_summary=row["profile_summary"],
            consent_flags=flags,
        )

    def get_user(self, telegram_id: str) -> Optional[UserProfile]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE telegram_id = ?",
                (str(telegram_id),)
            ).fetchone()
        return self._row_to_profile(row) if row else None

    def add_user(
        self,
        telegram_id: str,
        name: str,
        role: str = ROLE_VIEWER,
        data_mode: str = DATA_MODE_ISOLATED,
    ) -> UserProfile:
        if role not in VALID_ROLES:
            raise ValueError(f"Invalid role '{role}'. Must be one of {VALID_ROLES}")
        if data_mode not in VALID_DATA_MODES:
            raise ValueError(f"Invalid data_mode '{data_mode}'. Must be one of {VALID_DATA_MODES}")

        now = datetime.datetime.now(datetime.UTC).isoformat()
        profile = UserProfile(
            telegram_id=str(telegram_id),
            name=name,
            role=role,
            data_mode=data_mode,
            onboarded=False,
            joined_at=now,
            last_seen=now,
            profile_summary="",
            consent_flags=dict(DEFAULT_CONSENT),
        )
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO users
                    (telegram_id, name, role, data_mode, onboarded,
                     joined_at, last_seen, profile_summary, consent_flags)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile.telegram_id, profile.name, profile.role,
                    profile.data_mode, int(profile.onboarded),
                    profile.joined_at, profile.last_seen,
                    profile.profile_summary,
                    json.dumps(profile.consent_flags),
                ),
            )
        return profile

    def update_profile(self, telegram_id: str, **fields) -> bool:
        """Update one or more profile fields. Returns True if row existed."""
        allowed = {
            "name", "role", "data_mode", "onboarded",
            "last_seen", "profile_summary", "consent_flags",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False

        # Serialize consent_flags if provided as dict
        if "consent_flags" in updates and isinstance(updates["consent_flags"], dict):
            updates["consent_flags"] = json.dumps(updates["consent_flags"])
        if "onboarded" in updates:
            updates["onboarded"] = int(bool(updates["onboarded"]))

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [str(telegram_id)]
        with self._conn() as conn:
            cur = conn.execute(
                f"UPDATE users SET {set_clause} WHERE telegram_id = ?",
                values,
            )
        return cur.rowcount > 0

    def touch(self, telegram_id: str):
        """Update last_seen to now."""
        now = datetime.datetime.now(datetime.UTC).isoformat()
        self.update_profile(telegram_id, last_seen=now)

    def remove_user(self, telegram_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM users WHERE telegram_id = ?",
                (str(telegram_id),)
            )
        return cur.rowcount > 0

    def list_users(self) -> list[UserProfile]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM users ORDER BY joined_at"
            ).fetchall()
        return [self._row_to_profile(r) for r in rows]

    def is_authorized(self, telegram_id: str) -> bool:
        return self.get_user(telegram_id) is not None

    def get_role(self, telegram_id: str) -> Optional[str]:
        user = self.get_user(telegram_id)
        return user.role if user else None

    def set_consent(self, telegram_id: str, flag: str, value: bool) -> bool:
        user = self.get_user(telegram_id)
        if not user:
            return False
        if flag == "messages":
            return True  # messages consent is always on, silently ignore attempt to turn off
        flags = dict(user.consent_flags)
        flags[flag] = bool(value)
        return self.update_profile(telegram_id, consent_flags=flags)

    def get_consent(self, telegram_id: str, flag: str) -> bool:
        user = self.get_user(telegram_id)
        if not user:
            return False
        return bool(user.consent_flags.get(flag, False))

    # ------------------------------------------------------------------
    # Owner bootstrap
    # ------------------------------------------------------------------

    def ensure_owner(self, telegram_id: str, name: str = "Owner") -> UserProfile:
        """
        Called at startup — ensures the owner's telegram_id exists in the DB
        with role=owner. Safe to call repeatedly (idempotent).
        """
        existing = self.get_user(telegram_id)
        if existing:
            if existing.role != ROLE_OWNER:
                self.update_profile(telegram_id, role=ROLE_OWNER)
            return self.get_user(telegram_id)
        return self.add_user(telegram_id, name, role=ROLE_OWNER, data_mode=DATA_MODE_ISOLATED)

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    def format_user_line(self, profile: UserProfile) -> str:
        last = profile.last_seen[:10] if profile.last_seen else "never"
        summary = (profile.profile_summary[:80] + "...") if len(profile.profile_summary) > 80 else profile.profile_summary
        return (
            f"• {profile.name} ({profile.telegram_id})\n"
            f"  Role: {profile.role} | Mode: {profile.data_mode} | Last seen: {last}\n"
            f"  {summary or 'No profile yet.'}"
        )


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_registry: Optional[UserRegistry] = None


def get_user_registry() -> UserRegistry:
    global _registry
    if _registry is None:
        _registry = UserRegistry()
    return _registry
