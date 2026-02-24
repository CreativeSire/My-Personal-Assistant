from __future__ import annotations

import os
import time
import warnings
from dataclasses import dataclass
from typing import Any

from config import get_config
from data_engine import EventEnvelope, get_data_engine
from memory_core import MemoryQueryInput, MemoryRecordInput, recall_memory, save_memory
from session_manager import resolve_session_id, resolve_user_id
from monitor import get_system_metrics


@dataclass
class SessionContext:
    channel: str
    external_user_id: str
    user_id: str
    session_id: str
    created_at: float


class RuntimeAPI:
    """
    Stable extension surface for skills.
    """

    def __init__(self):
        self._cfg = get_config()
        self._engine = get_data_engine()

    def config(self):
        return self._cfg

    def session_context(self, *, channel: str, external_user_id: str) -> SessionContext:
        ext = str(external_user_id or "unknown")
        return SessionContext(
            channel=str(channel or "system"),
            external_user_id=ext,
            user_id=resolve_user_id(channel or "system", ext),
            session_id=resolve_session_id(channel or "system", ext),
            created_at=time.time(),
        )

    def emit_event(
        self,
        *,
        event_type: str,
        actor: str,
        payload: dict[str, Any] | None = None,
        channel: str = "system",
        session_id: str = "",
        task_id: str = "",
        risk_score: float = 0.0,
    ) -> dict[str, Any]:
        return self._engine.emit_event(
            EventEnvelope(
                event_type=event_type,
                actor=actor,
                payload=payload or {},
                channel=channel,
                session_id=session_id,
                task_id=task_id,
                risk_score=float(risk_score),
                source="runtime_api",
            )
        )

    def write_memory(
        self,
        *,
        text: str,
        memory_type: str = "project_fact",
        scope: str = "global",
        agent_name: str | None = None,
        source: str = "runtime_api",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return save_memory(
            MemoryRecordInput(
                text=str(text or ""),
                memory_type=memory_type,
                scope=scope,
                agent_name=agent_name,
                source=source,
                tags=list(tags or []),
                metadata_json=metadata or {},
            )
        )

    def read_memory(
        self,
        *,
        query: str,
        top_k: int = 10,
        scope_filter: str | None = None,
        agent_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        return recall_memory(
            MemoryQueryInput(
                query=str(query or ""),
                top_k=max(1, int(top_k)),
                scope_filter=scope_filter,
                agent_filter=agent_filter,
            )
        )

    def invoke_tool(self, *, tool_name: str, inputs: dict[str, Any] | None = None, actor: str = "skill") -> dict[str, Any]:
        # This is intentionally local-safe for now. In process, migrate to shared in-process dispatch.
        return {
            "ok": True,
            "tool_name": str(tool_name or ""),
            "inputs": dict(inputs or {}),
            "actor": actor,
            "mode": "runtime_api_stub",
        }

    def system_metrics(self) -> dict[str, Any]:
        return dict(get_system_metrics() or {})


_RUNTIME_API: RuntimeAPI | None = None


def get_runtime_api() -> RuntimeAPI:
    global _RUNTIME_API
    if _RUNTIME_API is None:
        _RUNTIME_API = RuntimeAPI()
    return _RUNTIME_API


def warn_private_import(module_name: str, replacement: str = "victor_os.sdk") -> None:
    warnings.warn(
        f"[DEPRECATION] Direct private import detected in '{module_name}'. "
        f"Use stable extension API via '{replacement}' instead.",
        DeprecationWarning,
        stacklevel=2,
    )
