"""
Victor ERP Staff Sync Engine (append-only outbox with idempotency + retry).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass

import requests


@dataclass
class SyncConfig:
    relay_base_url: str
    device_id: str
    api_key: str
    hmac_secret: str
    db_path: str


class SyncEngine:
    def __init__(self, cfg: SyncConfig):
        self.cfg = cfg

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.cfg.db_path)
        conn.row_factory = sqlite3.Row
        return conn

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

    def sync_once(self, recipient: str, recipient_device_id: str) -> dict[str, int]:
        conn = self._conn()
        try:
            rows = conn.execute(
                """
                SELECT id, idempotency_key, event_type, payload_json, retries
                FROM outbox_events
                WHERE status='pending' AND next_retry_at <= ?
                ORDER BY created_at ASC
                LIMIT 50
                """,
                (time.time(),),
            ).fetchall()
            pushed = 0
            failed = 0
            for row in rows:
                payload_json = row["payload_json"] or "{}"
                payload = json.loads(payload_json)
                packet = {
                    "recipient": recipient,
                    "recipient_device_id": recipient_device_id,
                    "sender_device_id": self.cfg.device_id,
                    "idempotency_key": row["idempotency_key"],
                    "envelope": payload,
                    "content_hash": hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
                    "packet_type": row["event_type"],
                }
                body = json.dumps(packet, ensure_ascii=False).encode("utf-8")
                try:
                    res = requests.post(
                        f"{self.cfg.relay_base_url}/v1/packets",
                        data=body,
                        headers=self._signed_headers(body),
                        timeout=10,
                    )
                    if res.ok and res.json().get("ok"):
                        conn.execute(
                            "UPDATE outbox_events SET status='sent', updated_at=? WHERE id=?",
                            (time.time(), row["id"]),
                        )
                        pushed += 1
                    else:
                        retries = int(row["retries"] or 0) + 1
                        conn.execute(
                            "UPDATE outbox_events SET retries=?, next_retry_at=?, updated_at=? WHERE id=?",
                            (retries, time.time() + min(3600, 2**retries), time.time(), row["id"]),
                        )
                        failed += 1
                except Exception:
                    retries = int(row["retries"] or 0) + 1
                    conn.execute(
                        "UPDATE outbox_events SET retries=?, next_retry_at=?, updated_at=? WHERE id=?",
                        (retries, time.time() + min(3600, 2**retries), time.time(), row["id"]),
                    )
                    failed += 1
            conn.commit()
            return {"pushed": pushed, "failed": failed}
        finally:
            conn.close()

