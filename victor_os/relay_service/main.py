"""
Victor ERP Relay Service (FastAPI + Postgres, SQLite fallback).

Passive encrypted packet relay with:
- per-device API key + HMAC authentication
- idempotent packet writes
- pull and ack mechanics
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

try:
    import psycopg  # type: ignore
except Exception:
    psycopg = None

try:
    import psycopg2  # type: ignore
except Exception:
    psycopg2 = None


def _now() -> float:
    return time.time()


@dataclass
class RelayConfig:
    database_url: str = os.getenv("RELAY_DATABASE_URL", "").strip()
    hmac_secret: str = os.getenv("RELAY_HMAC_SECRET", "dev-relay-secret")
    nonce_window_seconds: int = int(os.getenv("RELAY_NONCE_WINDOW_SECONDS", "300"))
    use_sqlite_dev: bool = os.getenv("RELAY_USE_SQLITE_DEV", "true").lower() in {"1", "true", "yes", "on"}
    sqlite_path: str = os.getenv("RELAY_SQLITE_PATH", os.path.join(os.path.dirname(__file__), "relay_dev.db"))


cfg = RelayConfig()
app = FastAPI(title="Victor ERP Relay", version="0.2.0")


class PacketIn(BaseModel):
    recipient: str = Field(pattern="^(hub|client)$")
    recipient_device_id: str
    sender_device_id: str
    idempotency_key: str
    envelope: dict[str, Any]
    content_hash: str
    packet_type: str = "generic"


class AckIn(BaseModel):
    packet_id: str
    device_id: str


class RelayStore:
    backend: str = "unknown"

    def save_nonce(self, nonce: str, created_at: float) -> None: ...
    def nonce_seen(self, nonce: str) -> bool: ...
    def prune_nonces(self, older_than: float) -> None: ...
    def find_packet_by_idempotency(self, sender_device_id: str, idempotency_key: str) -> str | None: ...
    def insert_packet(self, packet_id: str, packet: PacketIn, created_at: float) -> None: ...
    def pull_packets(self, recipient: str, recipient_device_id: str, limit: int) -> list[dict[str, Any]]: ...
    def ack_packet(self, packet_id: str, at: float) -> None: ...


class SQLiteRelayStore(RelayStore):
    backend = "sqlite"

    def __init__(self, path: str):
        self.path = path
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._conn()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS packets (
                id TEXT PRIMARY KEY,
                recipient TEXT NOT NULL,
                recipient_device_id TEXT NOT NULL,
                sender_device_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                packet_type TEXT NOT NULL,
                envelope_json TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                created_at REAL NOT NULL,
                delivered_at REAL,
                acked_at REAL
            )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_packets_idempotent
            ON packets(sender_device_id, idempotency_key)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS relay_nonces (
                nonce TEXT PRIMARY KEY,
                created_at REAL NOT NULL
            )
            """
        )
        conn.commit()
        conn.close()

    def save_nonce(self, nonce: str, created_at: float) -> None:
        conn = self._conn()
        conn.execute("INSERT INTO relay_nonces (nonce, created_at) VALUES (?, ?)", (nonce, created_at))
        conn.commit()
        conn.close()

    def nonce_seen(self, nonce: str) -> bool:
        conn = self._conn()
        row = conn.execute("SELECT nonce FROM relay_nonces WHERE nonce = ?", (nonce,)).fetchone()
        conn.close()
        return row is not None

    def prune_nonces(self, older_than: float) -> None:
        conn = self._conn()
        conn.execute("DELETE FROM relay_nonces WHERE created_at < ?", (older_than,))
        conn.commit()
        conn.close()

    def find_packet_by_idempotency(self, sender_device_id: str, idempotency_key: str) -> str | None:
        conn = self._conn()
        row = conn.execute(
            "SELECT id FROM packets WHERE sender_device_id=? AND idempotency_key=?",
            (sender_device_id, idempotency_key),
        ).fetchone()
        conn.close()
        return row["id"] if row else None

    def insert_packet(self, packet_id: str, packet: PacketIn, created_at: float) -> None:
        conn = self._conn()
        conn.execute(
            """
            INSERT INTO packets (
                id, recipient, recipient_device_id, sender_device_id, idempotency_key,
                packet_type, envelope_json, content_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                packet_id,
                packet.recipient,
                packet.recipient_device_id,
                packet.sender_device_id,
                packet.idempotency_key,
                packet.packet_type,
                json.dumps(packet.envelope, ensure_ascii=False),
                packet.content_hash,
                created_at,
            ),
        )
        conn.commit()
        conn.close()

    def pull_packets(self, recipient: str, recipient_device_id: str, limit: int) -> list[dict[str, Any]]:
        conn = self._conn()
        rows = conn.execute(
            """
            SELECT id, sender_device_id, packet_type, envelope_json, content_hash, created_at
            FROM packets
            WHERE recipient=? AND recipient_device_id=? AND acked_at IS NULL
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (recipient, recipient_device_id, limit),
        ).fetchall()
        conn.close()
        items = []
        for row in rows:
            items.append(
                {
                    "packet_id": row["id"],
                    "sender_device_id": row["sender_device_id"],
                    "packet_type": row["packet_type"],
                    "envelope": json.loads(row["envelope_json"]),
                    "content_hash": row["content_hash"],
                    "created_at": row["created_at"],
                }
            )
        return items

    def ack_packet(self, packet_id: str, at: float) -> None:
        conn = self._conn()
        conn.execute(
            "UPDATE packets SET acked_at=?, delivered_at=COALESCE(delivered_at, ?) WHERE id=?",
            (at, at, packet_id),
        )
        conn.commit()
        conn.close()


class PostgresRelayStore(RelayStore):
    backend = "postgres"

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._init_db()

    def _conn(self):
        if psycopg is not None:
            return psycopg.connect(self.dsn)  # type: ignore[attr-defined]
        if psycopg2 is not None:
            return psycopg2.connect(self.dsn)  # type: ignore[attr-defined]
        raise RuntimeError("Neither psycopg nor psycopg2 is installed.")

    def _exec(self, sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall() if cur.description else []
            conn.commit()
            return rows
        finally:
            conn.close()

    def _init_db(self) -> None:
        self._exec(
            """
            CREATE TABLE IF NOT EXISTS packets (
                id TEXT PRIMARY KEY,
                recipient TEXT NOT NULL,
                recipient_device_id TEXT NOT NULL,
                sender_device_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                packet_type TEXT NOT NULL,
                envelope_json TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                created_at DOUBLE PRECISION NOT NULL,
                delivered_at DOUBLE PRECISION,
                acked_at DOUBLE PRECISION
            );
            """
        )
        self._exec(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_packets_idempotent
            ON packets(sender_device_id, idempotency_key);
            """
        )
        self._exec(
            """
            CREATE TABLE IF NOT EXISTS relay_nonces (
                nonce TEXT PRIMARY KEY,
                created_at DOUBLE PRECISION NOT NULL
            );
            """
        )

    def save_nonce(self, nonce: str, created_at: float) -> None:
        self._exec("INSERT INTO relay_nonces (nonce, created_at) VALUES (%s, %s)", (nonce, created_at))

    def nonce_seen(self, nonce: str) -> bool:
        rows = self._exec("SELECT nonce FROM relay_nonces WHERE nonce = %s", (nonce,))
        return len(rows) > 0

    def prune_nonces(self, older_than: float) -> None:
        self._exec("DELETE FROM relay_nonces WHERE created_at < %s", (older_than,))

    def find_packet_by_idempotency(self, sender_device_id: str, idempotency_key: str) -> str | None:
        rows = self._exec(
            "SELECT id FROM packets WHERE sender_device_id=%s AND idempotency_key=%s",
            (sender_device_id, idempotency_key),
        )
        return rows[0][0] if rows else None

    def insert_packet(self, packet_id: str, packet: PacketIn, created_at: float) -> None:
        self._exec(
            """
            INSERT INTO packets (
                id, recipient, recipient_device_id, sender_device_id, idempotency_key,
                packet_type, envelope_json, content_hash, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                packet_id,
                packet.recipient,
                packet.recipient_device_id,
                packet.sender_device_id,
                packet.idempotency_key,
                packet.packet_type,
                json.dumps(packet.envelope, ensure_ascii=False),
                packet.content_hash,
                created_at,
            ),
        )

    def pull_packets(self, recipient: str, recipient_device_id: str, limit: int) -> list[dict[str, Any]]:
        rows = self._exec(
            """
            SELECT id, sender_device_id, packet_type, envelope_json, content_hash, created_at
            FROM packets
            WHERE recipient=%s AND recipient_device_id=%s AND acked_at IS NULL
            ORDER BY created_at ASC
            LIMIT %s
            """,
            (recipient, recipient_device_id, limit),
        )
        items = []
        for row in rows:
            items.append(
                {
                    "packet_id": row[0],
                    "sender_device_id": row[1],
                    "packet_type": row[2],
                    "envelope": json.loads(row[3]),
                    "content_hash": row[4],
                    "created_at": row[5],
                }
            )
        return items

    def ack_packet(self, packet_id: str, at: float) -> None:
        self._exec(
            "UPDATE packets SET acked_at=%s, delivered_at=COALESCE(delivered_at, %s) WHERE id=%s",
            (at, at, packet_id),
        )


def _build_store() -> RelayStore:
    if cfg.database_url:
        try:
            return PostgresRelayStore(cfg.database_url)
        except Exception:
            if not cfg.use_sqlite_dev:
                raise
    return SQLiteRelayStore(cfg.sqlite_path)


store = _build_store()


def _sign_base(device_id: str, timestamp: str, nonce: str, body: bytes) -> bytes:
    digest = hashlib.sha256(body).hexdigest()
    return f"{device_id}:{timestamp}:{nonce}:{digest}".encode("utf-8")


def _validate_hmac(
    body: bytes,
    device_id: str,
    timestamp: str,
    nonce: str,
    signature: str,
) -> None:
    if not device_id or not timestamp or not nonce or not signature:
        raise HTTPException(status_code=401, detail="missing auth headers")
    try:
        ts = int(timestamp)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="invalid timestamp") from exc
    if abs(int(_now()) - ts) > cfg.nonce_window_seconds:
        raise HTTPException(status_code=401, detail="timestamp outside replay window")
    if store.nonce_seen(nonce):
        raise HTTPException(status_code=401, detail="nonce replay detected")

    expected = hmac.new(
        cfg.hmac_secret.encode("utf-8"),
        _sign_base(device_id, timestamp, nonce, body),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="bad signature")

    store.save_nonce(nonce, _now())
    store.prune_nonces(_now() - cfg.nonce_window_seconds)


@app.get("/v1/health")
def health():
    return {"ok": True, "service": "victor-relay", "timestamp": _now(), "backend": store.backend}


@app.post("/v1/packets")
async def push_packet(
    packet: PacketIn,
    x_device_id: str = Header(default=""),
    x_timestamp: str = Header(default=""),
    x_nonce: str = Header(default=""),
    x_signature: str = Header(default=""),
):
    body = packet.model_dump_json().encode("utf-8")
    _validate_hmac(body, x_device_id, x_timestamp, x_nonce, x_signature)

    existing_id = store.find_packet_by_idempotency(packet.sender_device_id, packet.idempotency_key)
    if existing_id:
        return {"ok": True, "packet_id": existing_id, "idempotent": True}

    packet_id = uuid.uuid4().hex
    store.insert_packet(packet_id, packet, _now())
    return {"ok": True, "packet_id": packet_id, "idempotent": False}


@app.get("/v1/packets/pull")
async def pull_packets(
    recipient: str,
    device_id: str,
    limit: int = 100,
    x_device_id: str = Header(default=""),
    x_timestamp: str = Header(default=""),
    x_nonce: str = Header(default=""),
    x_signature: str = Header(default=""),
):
    req_payload = json.dumps({"recipient": recipient, "device_id": device_id, "limit": limit}).encode("utf-8")
    _validate_hmac(req_payload, x_device_id, x_timestamp, x_nonce, x_signature)
    items = store.pull_packets(recipient, device_id, max(1, min(500, int(limit))))
    return {"ok": True, "items": items}


@app.post("/v1/packets/ack")
async def ack_packet(
    ack: AckIn,
    x_device_id: str = Header(default=""),
    x_timestamp: str = Header(default=""),
    x_nonce: str = Header(default=""),
    x_signature: str = Header(default=""),
):
    body = ack.model_dump_json().encode("utf-8")
    _validate_hmac(body, x_device_id, x_timestamp, x_nonce, x_signature)
    store.ack_packet(ack.packet_id, _now())
    return {"ok": True}

