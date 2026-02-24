"""
Victor-OS Session Overrides
============================
Per-session state store for model overrides, thinking level, and mode.
Backed by SQLite. Independent of the ADK session service.

Supports inline directives parsed from user messages:
  @fast      → thinking_level=fast, skip critic
  @deep      → thinking_level=deep, extended reasoning
  @research  → mode=research (spawns research sub-task)
  @code      → mode=code (code-focused system prompt)
  @exec      → mode=exec (owner-only: elevated tool execution)
  @reset     → clear all overrides for this session
"""

from __future__ import annotations

import re
import sqlite3
import os
import time
from dataclasses import dataclass, asdict
from typing import Optional

from config import get_config

_cfg = get_config()
_DB_PATH = os.path.join(_cfg.base_dir, "memory_store", "session_overrides.db")

VALID_THINKING_LEVELS = {"fast", "standard", "deep"}
VALID_MODES = {"default", "research", "code", "exec"}

# Directive regex — must appear at start or anywhere in message, case-insensitive
_DIRECTIVE_PATTERN = re.compile(
    r"@(fast|deep|research|code|exec|reset)\b", re.IGNORECASE
)


@dataclass
class SessionOverride:
    session_id: str
    thinking_level: str = "standard"   # fast | standard | deep
    mode: str = "default"              # default | research | code | exec
    model_override: str = ""           # e.g. "gemini-2.0-flash-001" or ""
    skip_critic: bool = False
    updated_at: float = 0.0


class SessionOverrideStore:
    """SQLite-backed per-session override store."""

    def __init__(self, db_path: str = _DB_PATH):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS session_overrides (
                    session_id     TEXT PRIMARY KEY,
                    thinking_level TEXT NOT NULL DEFAULT 'standard',
                    mode           TEXT NOT NULL DEFAULT 'default',
                    model_override TEXT NOT NULL DEFAULT '',
                    skip_critic    INTEGER NOT NULL DEFAULT 0,
                    updated_at     REAL NOT NULL DEFAULT 0
                )
            """)
            conn.commit()

    def get(self, session_id: str) -> SessionOverride:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM session_overrides WHERE session_id=?", (session_id,)
            ).fetchone()
        if row:
            return SessionOverride(
                session_id=row["session_id"],
                thinking_level=row["thinking_level"] or "standard",
                mode=row["mode"] or "default",
                model_override=row["model_override"] or "",
                skip_critic=bool(row["skip_critic"]),
                updated_at=float(row["updated_at"] or 0),
            )
        return SessionOverride(session_id=session_id, updated_at=time.time())

    def set(self, override: SessionOverride) -> None:
        override.updated_at = time.time()
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO session_overrides
                    (session_id, thinking_level, mode, model_override, skip_critic, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    thinking_level = excluded.thinking_level,
                    mode           = excluded.mode,
                    model_override = excluded.model_override,
                    skip_critic    = excluded.skip_critic,
                    updated_at     = excluded.updated_at
            """, (
                override.session_id,
                override.thinking_level,
                override.mode,
                override.model_override,
                int(override.skip_critic),
                override.updated_at,
            ))
            conn.commit()

    def reset(self, session_id: str) -> None:
        """Clear all overrides for a session (back to defaults)."""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM session_overrides WHERE session_id=?", (session_id,)
            )
            conn.commit()

    def apply_directive(
        self,
        session_id: str,
        directive: str,
        user_role: str = "viewer",
    ) -> tuple[SessionOverride, str]:
        """
        Apply a single directive string (without the @) to the session.
        Returns (updated_override, feedback_message).
        exec directive requires owner role.
        """
        override = self.get(session_id)
        directive = directive.lower().strip()

        if directive == "reset":
            self.reset(session_id)
            return self.get(session_id), "Session overrides cleared. Back to standard mode."

        if directive == "fast":
            override.thinking_level = "fast"
            override.skip_critic = True
            override.mode = "default"
            msg = "Fast mode on — critic skipped, fastest response."

        elif directive == "deep":
            override.thinking_level = "deep"
            override.skip_critic = False
            msg = "Deep mode on — extended reasoning active."

        elif directive == "research":
            override.mode = "research"
            override.thinking_level = "standard"
            override.skip_critic = False
            msg = "Research mode on — I'll go broader and cite sources."

        elif directive == "code":
            override.mode = "code"
            override.thinking_level = "standard"
            override.skip_critic = False
            msg = "Code mode on — focused on technical precision."

        elif directive == "exec":
            if user_role != "owner":
                return override, "@exec is owner-only."
            override.mode = "exec"
            override.thinking_level = "deep"
            override.skip_critic = False
            msg = "Exec mode on — elevated tool execution active."

        else:
            return override, f"Unknown directive: @{directive}"

        self.set(override)
        return override, msg


def extract_directives(text: str) -> tuple[list[str], str]:
    """
    Extract @directive tokens from a message.
    Returns (list_of_directives, cleaned_text_without_directives).
    """
    directives = []
    for match in _DIRECTIVE_PATTERN.finditer(text):
        directives.append(match.group(1).lower())
    cleaned = _DIRECTIVE_PATTERN.sub("", text).strip()
    # Collapse extra whitespace left behind
    cleaned = re.sub(r"  +", " ", cleaned).strip()
    return directives, cleaned


def get_mode_system_prompt(mode: str) -> str:
    """Return a mode-specific system prompt prefix to prepend for COMPLEX tier."""
    if mode == "research":
        return (
            "[MODE: RESEARCH]\n"
            "Go deep. Use web search and knowledge tools. Cite sources where possible. "
            "Structure your response with clear sections. Prioritise accuracy over brevity.\n\n"
        )
    if mode == "code":
        return (
            "[MODE: CODE]\n"
            "Focus on technical precision. Prefer working code over explanation. "
            "Always include language tags in code blocks. If you spot bugs, fix them.\n\n"
        )
    if mode == "exec":
        return (
            "[MODE: EXEC — ELEVATED]\n"
            "Owner-authorised execution mode. You may use all available tools without asking for "
            "confirmation unless the action is irreversible. State what you are doing before doing it.\n\n"
        )
    return ""


# Module-level singleton
_store: SessionOverrideStore | None = None


def get_override_store() -> SessionOverrideStore:
    global _store
    if _store is None:
        _store = SessionOverrideStore()
    return _store
