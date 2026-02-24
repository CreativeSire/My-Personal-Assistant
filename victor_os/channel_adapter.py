from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import time
import uuid

from data_engine import EventEnvelope, get_data_engine


@dataclass
class ChannelMessage:
    channel: str
    sender_id: str
    message_id: str
    text: str = ""
    attachments: list[dict[str, Any]] = field(default_factory=list)
    session_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuthDecision:
    allowed: bool
    reason: str = ""
    principal: str = ""


@dataclass
class RoutingDecision:
    tier: str
    intent_class: str = ""
    route: str = "default"
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutboundEnvelope:
    channel: str
    destination: str
    text: str = ""
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    delivery_metadata: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    task_id: str = ""
    session_id: str = ""


@dataclass
class DeliveryResult:
    ok: bool
    channel: str
    destination: str
    idempotency_key: str
    delivery_id: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChannelHealth:
    ok: bool
    channel: str
    latency_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class ChannelAdapter:
    channel_name: str = "unknown"

    def normalize_inbound(self, raw_event: Any) -> ChannelMessage:
        raise NotImplementedError

    def authorize(self, sender: str, raw_event: Any | None = None) -> AuthDecision:
        return AuthDecision(allowed=True, principal=str(sender or ""))

    def route(self, message: ChannelMessage) -> RoutingDecision:
        return RoutingDecision(tier="COMPLEX", intent_class="generic", route="default", confidence=0.5)

    def deliver(self, outbound: OutboundEnvelope) -> DeliveryResult:
        raise NotImplementedError

    def heartbeat(self) -> ChannelHealth:
        return ChannelHealth(ok=True, channel=self.channel_name, latency_ms=0.0, metadata={"mode": "default"})


def emit_channel_event(
    event_type: str,
    *,
    channel: str,
    actor: str,
    session_id: str = "",
    task_id: str = "",
    payload: dict[str, Any] | None = None,
    risk_score: float = 0.1,
) -> None:
    engine = get_data_engine()
    engine.emit_event(
        EventEnvelope(
            event_id=uuid.uuid4().hex,
            event_type=event_type,
            ts_utc=time.time(),
            session_id=session_id,
            task_id=task_id,
            actor=actor or "system",
            payload=payload or {},
            risk_score=float(risk_score),
            channel=channel,
            source=f"{channel}_adapter",
        )
    )
