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


class TelegramAdapter(ChannelAdapter):
    channel_name = "telegram"

    def __init__(
        self,
        *,
        sender: Callable[[str, str], Any],
        role_lookup: Callable[[str], str | None] | None = None,
        tier_router: LocalInferenceRouter | None = None,
    ):
        self._send = sender
        self._role_lookup = role_lookup
        self._router = tier_router or LocalInferenceRouter()

    def normalize_inbound(self, raw_event: Any) -> ChannelMessage:
        chat = getattr(raw_event, "chat", None)
        sender_id = normalize_sender_id(getattr(chat, "id", "unknown"))
        text = normalize_text(getattr(raw_event, "text", ""))
        message_id = ensure_message_id(getattr(raw_event, "message_id", ""), prefix="tg")
        msg = ChannelMessage(
            channel=self.channel_name,
            sender_id=sender_id,
            message_id=message_id,
            text=text,
            raw={"has_photo": bool(getattr(raw_event, "photo", None)), "has_doc": bool(getattr(raw_event, "document", None))},
        )
        emit_channel_event(
            "channel.inbound.normalized",
            channel=self.channel_name,
            actor=sender_id,
            payload={"message_id": message_id, "text_preview": text[:180]},
            risk_score=0.1,
        )
        return msg

    def authorize(self, sender: str, raw_event: Any | None = None) -> AuthDecision:
        if not self._role_lookup:
            return AuthDecision(allowed=True, principal=sender, reason="no_role_lookup")
        role = self._role_lookup(str(sender))
        if role is None:
            return AuthDecision(allowed=False, principal=sender, reason="unknown_user")
        return AuthDecision(allowed=True, principal=sender, reason=f"role:{role}")

    def route(self, message: ChannelMessage) -> RoutingDecision:
        tier = self._router.classify_tier(message.text or "")
        intent_class, confidence = self._router.classify_intent(message.text or "")
        route = "deterministic" if tier in {"SOCIAL", "SIMPLE"} else "agent"
        return RoutingDecision(
            tier=tier,
            intent_class=intent_class,
            route=route,
            confidence=float(confidence or 0.0),
            metadata={"message_id": message.message_id},
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
            self._send(outbound.destination, outbound.text)
            latency = (time.time() - started) * 1000.0
            emit_channel_event(
                "channel.outbound.delivered",
                channel=self.channel_name,
                actor="adapter",
                session_id=outbound.session_id,
                task_id=outbound.task_id,
                payload={
                    "delivery_id": did,
                    "destination": outbound.destination,
                    "idempotency_key": dedupe,
                    "latency_ms": latency,
                    "artifact_count": len(outbound.artifacts or []),
                },
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
        return ChannelHealth(ok=True, channel=self.channel_name, latency_ms=0.0, metadata={"transport": "telebot"})
