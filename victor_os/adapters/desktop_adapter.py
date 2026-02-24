from __future__ import annotations

from typing import Any
import time
import uuid

from channel_adapter import (
    AuthDecision,
    ChannelAdapter,
    ChannelHealth,
    ChannelMessage,
    DeliveryResult,
    OutboundEnvelope,
    RoutingDecision,
    emit_channel_event,
)
from channel_normalize import delivery_idempotency_key, ensure_message_id, normalize_sender_id, normalize_text
from local_inference_router import LocalInferenceRouter


class DesktopAdapter(ChannelAdapter):
    channel_name = "desktop"

    def __init__(self, *, tier_router: LocalInferenceRouter | None = None):
        self._router = tier_router or LocalInferenceRouter()

    def normalize_inbound(self, raw_event: Any) -> ChannelMessage:
        payload = raw_event if isinstance(raw_event, dict) else {}
        sender_id = normalize_sender_id(payload.get("user_id") or payload.get("sender") or "desktop_user")
        text = normalize_text(payload.get("message") or payload.get("text") or payload.get("intent") or "")
        msg = ChannelMessage(
            channel=self.channel_name,
            sender_id=sender_id,
            message_id=ensure_message_id(payload.get("message_id"), prefix="desktop"),
            text=text,
            session_id=str(payload.get("session_id") or "").strip(),
            raw=payload,
        )
        emit_channel_event(
            "channel.inbound.normalized",
            channel=self.channel_name,
            actor=sender_id,
            session_id=msg.session_id,
            payload={"message_id": msg.message_id, "text_preview": text[:180]},
            risk_score=0.1,
        )
        return msg

    def authorize(self, sender: str, raw_event: Any | None = None) -> AuthDecision:
        return AuthDecision(allowed=True, principal=sender, reason="desktop_local")

    def route(self, message: ChannelMessage) -> RoutingDecision:
        tier = self._router.classify_tier(message.text or "")
        intent_class, confidence = self._router.classify_intent(message.text or "")
        return RoutingDecision(
            tier=tier,
            intent_class=intent_class,
            route="deterministic" if tier in {"SOCIAL", "SIMPLE"} else "agent",
            confidence=float(confidence or 0.0),
        )

    def deliver(self, outbound: OutboundEnvelope) -> DeliveryResult:
        did = uuid.uuid4().hex[:12]
        dedupe = delivery_idempotency_key(
            channel=self.channel_name,
            destination=outbound.destination,
            text=outbound.text,
            task_id=outbound.task_id,
            explicit_key=outbound.idempotency_key,
        )
        started = time.time()
        latency = (time.time() - started) * 1000.0
        emit_channel_event(
            "channel.outbound.delivered",
            channel=self.channel_name,
            actor="adapter",
            session_id=outbound.session_id,
            task_id=outbound.task_id,
            payload={"delivery_id": did, "destination": outbound.destination, "idempotency_key": dedupe, "latency_ms": latency},
            risk_score=0.1,
        )
        return DeliveryResult(
            ok=True,
            channel=self.channel_name,
            destination=outbound.destination,
            idempotency_key=dedupe,
            delivery_id=did,
            metadata={"latency_ms": latency},
        )

    def heartbeat(self) -> ChannelHealth:
        return ChannelHealth(ok=True, channel=self.channel_name, latency_ms=0.0, metadata={"transport": "http"})
