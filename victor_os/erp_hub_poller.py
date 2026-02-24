"""
Victor ERP Hub poller.

Pulls encrypted packets from relay, decrypts locally (stub), grades evidence, and writes results.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

import requests

from config import get_config
from logging_config import get_logger

cfg = get_config()
logger = get_logger("erp_hub_poller")


@dataclass
class HubPollerConfig:
    relay_base_url: str = os.getenv("ERP_RELAY_URL", "http://127.0.0.1:8090")
    device_id: str = os.getenv("ERP_HUB_DEVICE_ID", "hub_admin")
    api_key: str = os.getenv("ERP_HUB_API_KEY", "dev-hub-key")
    hmac_secret: str = os.getenv("ERP_RELAY_HMAC_SECRET", "dev-relay-secret")


class HubPoller:
    def __init__(self, cfg_obj: HubPollerConfig | None = None):
        self.cfg = cfg_obj or HubPollerConfig()

    def _signed_headers(self, body: bytes) -> dict[str, str]:
        timestamp = str(int(time.time()))
        nonce = uuid.uuid4().hex
        digest = hashlib.sha256(body).hexdigest()
        base = f"{self.cfg.device_id}:{timestamp}:{nonce}:{digest}".encode("utf-8")
        signature = hmac.new(self.cfg.hmac_secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
        return {
            "X-Device-Id": self.cfg.device_id,
            "X-Timestamp": timestamp,
            "X-Nonce": nonce,
            "X-Signature": signature,
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
        }

    def _decrypt_envelope(self, envelope: dict[str, Any]) -> dict[str, Any]:
        # Production path will unwrap AES key with Admin private key and decrypt payload.
        # Current scaffold assumes relay packet already contains encrypted envelope metadata.
        return envelope

    def _grade_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("evidence_content") or "")
        created_at = float(payload.get("created_at") or 0)
        words = len([w for w in text.split() if w.strip()])
        too_recent = (time.time() - created_at) < 300 if created_at else False
        suspicious = words < 50 or too_recent
        return {
            "words": words,
            "too_recent": too_recent,
            "suspicious": suspicious,
            "score": max(0, min(100, 100 - (40 if suspicious else 0))),
        }

    def poll_once(self) -> dict[str, int]:
        pull_payload = {"recipient": "hub", "device_id": self.cfg.device_id, "limit": 100}
        body = json.dumps(pull_payload).encode("utf-8")
        res = requests.get(
            f"{self.cfg.relay_base_url}/v1/packets/pull",
            params=pull_payload,
            headers=self._signed_headers(body),
            timeout=20,
        )
        if not res.ok:
            logger.error(f"Relay pull failed: {res.status_code} {res.text[:160]}")
            return {"pulled": 0, "graded": 0}
        items = (res.json() or {}).get("items") or []
        graded = 0
        for item in items:
            envelope = item.get("envelope") or {}
            clear = self._decrypt_envelope(envelope)
            grade = self._grade_payload(clear)
            logger.info(
                f"ERP grade packet={item.get('packet_id')} suspicious={grade['suspicious']} score={grade['score']}"
            )
            ack_body_dict = {"packet_id": item.get("packet_id"), "device_id": self.cfg.device_id}
            ack_body = json.dumps(ack_body_dict).encode("utf-8")
            requests.post(
                f"{self.cfg.relay_base_url}/v1/packets/ack",
                data=ack_body,
                headers=self._signed_headers(ack_body),
                timeout=10,
            )
            graded += 1
        return {"pulled": len(items), "graded": graded}

