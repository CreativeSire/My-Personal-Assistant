"""
Victor-OS Unified Session Management
All channels use the same SqliteSessionService for cross-channel context.
"""

import os
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from config import get_config

_session_service: SqliteSessionService | None = None


def get_session_service() -> SqliteSessionService:
    """Returns a shared SqliteSessionService singleton used by ALL channels."""
    global _session_service
    if _session_service is None:
        cfg = get_config()
        os.makedirs(os.path.dirname(cfg.memory_db_path), exist_ok=True)
        _session_service = SqliteSessionService(db_path=cfg.memory_db_path)
    return _session_service


def resolve_user_id(channel: str, raw_id: str) -> str:
    """Returns the canonical user_id for a given channel + raw_id.

    Multi-user: returns the raw telegram_id directly so each user gets
    their own session in the ADK session store. Authorization is handled
    separately by UserRegistry, not by collapsing all IDs to the owner.
    """
    return str(raw_id) if raw_id else "unknown"


def resolve_session_id(channel: str, raw_id: str) -> str:
    """Creates a channel-specific session_id for conversation isolation."""
    return f"{channel}_{raw_id}"
