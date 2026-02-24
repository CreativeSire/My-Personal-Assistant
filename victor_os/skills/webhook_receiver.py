"""
Victor-OS Skill: Webhook Receiver
Listens for incoming webhooks from Stripe, GitHub, Shopify, and custom sources.
Runs a lightweight Flask route on a configurable port.
Feature-flagged via WEBHOOK_ENABLED env var.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from typing import Callable

from config import get_config
from data_engine import get_data_engine
from logging_config import get_logger
from skill_base import Skill, SkillManifest

logger = get_logger("webhook_receiver_skill")


class WebhookReceiverSkill(Skill):
    """
    Receives inbound webhooks and routes events into Victor pipelines.
    Supports Stripe, GitHub, Shopify, and generic JSON payloads.
    """

    def __init__(self):
        self._cfg = get_config()
        self._de = get_data_engine()
        self._port = int(os.getenv("WEBHOOK_PORT", "7878"))
        self._enabled = os.getenv("WEBHOOK_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
        self._secret_stripe = os.getenv("STRIPE_WEBHOOK_SECRET", "")
        self._secret_github = os.getenv("GITHUB_WEBHOOK_SECRET", "")
        self._server_thread: threading.Thread | None = None
        self._received: list[dict] = []  # in-memory ring buffer (last 200)
        self._notify_callback: Callable[[str, str, str], None] | None = None

    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="webhook_receiver",
            display_name="Webhook Receiver",
            version="1.0.0",
            description="Receives Stripe/GitHub/Shopify/custom webhooks and reacts automatically.",
            triggers=[
                r"\bwebhook\b",
                r"\bstripe.*payment\b",
                r"\bgithub.*push\b",
                r"\bshopify.*order\b",
                r"\bincoming.*event\b",
            ],
            enabled=self._enabled,
        )

    # ------------------------------------------------------------------ #
    # Signature verification                                               #
    # ------------------------------------------------------------------ #

    def _verify_stripe(self, payload: bytes, sig_header: str) -> bool:
        if not self._secret_stripe or not sig_header:
            return True  # skip verification if not configured
        try:
            parts = {k: v for item in sig_header.split(",") for k, v in [item.split("=", 1)]}
            ts = parts.get("t", "")
            v1 = parts.get("v1", "")
            signed = f"{ts}.{payload.decode('utf-8')}".encode()
            expected = hmac.new(self._secret_stripe.encode(), signed, hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected, v1)
        except Exception:
            return False

    def _verify_github(self, payload: bytes, sig_header: str) -> bool:
        if not self._secret_github or not sig_header:
            return True
        try:
            expected = "sha256=" + hmac.new(
                self._secret_github.encode(), payload, hashlib.sha256
            ).hexdigest()
            return hmac.compare_digest(expected, sig_header)
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # Event routing                                                        #
    # ------------------------------------------------------------------ #

    def _route_event(self, source: str, event_type: str, payload: dict) -> str:
        """Route an inbound webhook event to the appropriate Victor action."""
        record = {
            "source": source,
            "event_type": event_type,
            "payload": payload,
            "ts": time.time(),
        }
        # Keep ring buffer of last 200 events
        self._received.append(record)
        if len(self._received) > 200:
            self._received.pop(0)

        self._de.emit_event(
            self._de.build_event_envelope(
                "intent.received",
                session_id="webhook_receiver",
                task_id="",
                actor="webhook_receiver",
                payload={"source": source, "event_type": event_type},
                risk_score=0.2,
                channel="system",
                source="webhook_receiver",
            )
        )

        message = self._build_notification(source, event_type, payload)
        if message and self._notify_callback:
            try:
                self._notify_callback(self._cfg.default_owner_user_id, "telegram", message)
            except Exception as exc:
                logger.warning(f"Webhook notification failed: {exc}")

        return "ok"

    @staticmethod
    def _build_notification(source: str, event_type: str, payload: dict) -> str:
        """Build a human-readable notification for a webhook event."""
        if source == "stripe":
            amount = payload.get("data", {}).get("object", {}).get("amount", 0)
            currency = payload.get("data", {}).get("object", {}).get("currency", "usd").upper()
            if event_type == "payment_intent.succeeded":
                return f"Stripe payment received: {currency} {amount / 100:.2f}"
            if event_type == "payment_intent.payment_failed":
                return f"Stripe payment FAILED: {currency} {amount / 100:.2f}"
            return f"Stripe event: {event_type}"

        if source == "github":
            repo = (payload.get("repository") or {}).get("full_name", "unknown")
            if event_type == "push":
                pusher = payload.get("pusher", {}).get("name", "someone")
                commits = len(payload.get("commits", []))
                return f"GitHub push: {pusher} pushed {commits} commit(s) to {repo}"
            if event_type == "pull_request":
                action = payload.get("action", "")
                pr_title = (payload.get("pull_request") or {}).get("title", "")
                return f"GitHub PR {action}: {pr_title} in {repo}"
            return f"GitHub event: {event_type} on {repo}"

        if source == "shopify":
            if event_type == "orders/create":
                order_id = payload.get("id", "")
                total = payload.get("total_price", "?")
                return f"Shopify order #{order_id}: ${total}"
            return f"Shopify event: {event_type}"

        return f"Webhook from {source}: {event_type}"

    # ------------------------------------------------------------------ #
    # Flask server                                                         #
    # ------------------------------------------------------------------ #

    def on_load(self) -> None:
        if not self._enabled:
            return
        try:
            from flask import Flask, request, jsonify  # type: ignore
        except ImportError:
            logger.warning("Flask not installed — webhook receiver disabled.")
            return

        app = Flask("webhook_receiver")

        @app.route("/webhook/stripe", methods=["POST"])
        def stripe_hook():
            payload = request.get_data()
            sig = request.headers.get("Stripe-Signature", "")
            if not self._verify_stripe(payload, sig):
                return jsonify({"error": "invalid signature"}), 401
            try:
                data = json.loads(payload)
            except Exception:
                return jsonify({"error": "invalid json"}), 400
            self._route_event("stripe", data.get("type", "unknown"), data)
            return jsonify({"status": "ok"}), 200

        @app.route("/webhook/github", methods=["POST"])
        def github_hook():
            payload = request.get_data()
            sig = request.headers.get("X-Hub-Signature-256", "")
            if not self._verify_github(payload, sig):
                return jsonify({"error": "invalid signature"}), 401
            try:
                data = json.loads(payload)
            except Exception:
                return jsonify({"error": "invalid json"}), 400
            event_type = request.headers.get("X-GitHub-Event", "unknown")
            self._route_event("github", event_type, data)
            return jsonify({"status": "ok"}), 200

        @app.route("/webhook/shopify", methods=["POST"])
        def shopify_hook():
            try:
                data = request.get_json(force=True) or {}
            except Exception:
                data = {}
            topic = request.headers.get("X-Shopify-Topic", "unknown")
            self._route_event("shopify", topic, data)
            return jsonify({"status": "ok"}), 200

        @app.route("/webhook/custom", methods=["POST"])
        def custom_hook():
            try:
                data = request.get_json(force=True) or {}
            except Exception:
                data = {}
            source = str(data.get("source", "custom"))
            event_type = str(data.get("event_type", "generic"))
            self._route_event(source, event_type, data)
            return jsonify({"status": "ok"}), 200

        def _run():
            app.run(host="0.0.0.0", port=self._port, debug=False, use_reloader=False)

        self._server_thread = threading.Thread(target=_run, daemon=True)
        self._server_thread.start()
        logger.info(f"Webhook receiver started on port {self._port}")

    def set_notify_callback(self, callback: Callable[[str, str, str], None]) -> None:
        self._notify_callback = callback

    # ------------------------------------------------------------------ #
    # Tools                                                                #
    # ------------------------------------------------------------------ #

    def tool_list_recent_webhooks(self, limit: int = 20) -> str:
        """List the most recently received webhook events."""
        if not self._received:
            return "No webhooks received yet."
        recent = self._received[-int(limit):][::-1]
        lines = [f"{len(recent)} recent webhook(s):\n"]
        for e in recent:
            ts = time.strftime("%H:%M:%S", time.localtime(e["ts"]))
            lines.append(f"[{ts}] {e['source']} — {e['event_type']}")
        return "\n".join(lines)

    def tool_webhook_status(self) -> str:
        """Show webhook receiver status and configuration."""
        status = "running" if (self._enabled and self._server_thread) else "disabled"
        stripe_ok = "configured" if self._secret_stripe else "no secret"
        github_ok = "configured" if self._secret_github else "no secret"
        return (
            f"Webhook receiver: {status}\n"
            f"Port: {self._port}\n"
            f"Stripe: {stripe_ok}\n"
            f"GitHub: {github_ok}\n"
            f"Events received (session): {len(self._received)}"
        )

    def get_tools(self) -> list[Callable]:
        return [
            self.tool_list_recent_webhooks,
            self.tool_webhook_status,
        ]


def create_skill():
    return WebhookReceiverSkill()
