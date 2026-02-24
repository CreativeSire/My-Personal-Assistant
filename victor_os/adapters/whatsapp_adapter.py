from __future__ import annotations

from typing import Any, Callable
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


class WhatsAppAdapter(ChannelAdapter):
    channel_name = "whatsapp"

    def __init__(self, *, sender: Callable[[str, str], Any] | None = None, tier_router: LocalInferenceRouter | None = None):
        self._send = sender
        self._router = tier_router or LocalInferenceRouter()

    def normalize_inbound(self, raw_event: Any) -> ChannelMessage:
        body = ""
        sender = ""
        sid = ""
        if hasattr(raw_event, "values"):
            body = normalize_text(raw_event.values.get("Body", ""))
            sender = normalize_sender_id(raw_event.values.get("From", ""))
            sid = str(raw_event.values.get("MessageSid", "") or "")
        msg = ChannelMessage(
            channel=self.channel_name,
            sender_id=sender or "unknown",
            message_id=ensure_message_id(sid, prefix="wa"),
            text=body,
            raw={"sid": sid},
        )
        emit_channel_event(
            "channel.inbound.normalized",
            channel=self.channel_name,
            actor=msg.sender_id,
            payload={"message_id": msg.message_id, "text_preview": msg.text[:180]},
            risk_score=0.1,
        )
        return msg

    def authorize(self, sender: str, raw_event: Any | None = None) -> AuthDecision:
        return AuthDecision(allowed=True, principal=sender, reason="wa_default_allow")

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
        try:
            if self._send:
                self._send(outbound.destination, outbound.text)
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
        except Exception as exc:
            emit_channel_event(
                "channel.outbound.failed",
                channel=self.channel_name,
                actor="adapter",
                session_id=outbound.session_id,
                task_id=outbound.task_id,
                payload={"destination": outbound.destination, "idempotency_key": dedupe, "error": str(exc)},
                risk_score=0.3,
            )
            return DeliveryResult(
                ok=False,
                channel=self.channel_name,
                destination=outbound.destination,
                idempotency_key=dedupe,
                delivery_id=did,
                error=str(exc),
            )

    def heartbeat(self) -> ChannelHealth:
        return ChannelHealth(ok=True, channel=self.channel_name, latency_ms=0.0, metadata={"transport": "twilio"})
