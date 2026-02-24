from __future__ import annotations

from typing import Any

from data_engine import EventEnvelope, get_data_engine


class Telemetry:
    """Thin wrapper for canonical event contract emission."""

    def __init__(self):
        self._engine = get_data_engine()

    def emit(
        self,
        event_type: str,
        *,
        session_id: str = "",
        task_id: str = "",
        actor: str = "system",
        payload: Any = None,
        risk_score: float = 0.0,
        channel: str = "system",
        source: str = "platform",
    ) -> dict[str, Any]:
        return self._engine.emit_event(
            EventEnvelope(
                event_type=event_type,
                session_id=session_id,
                task_id=task_id,
                actor=actor,
                payload=payload if payload is not None else {},
                risk_score=risk_score,
                channel=channel,
                source=source,
            )
        )

