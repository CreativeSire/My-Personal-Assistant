from __future__ import annotations

import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any

from config import get_config


@dataclass
class DeviceRecord:
    device_id: str
    device_name: str
    owner_user_id: str
    status: str
    requested_at: float
    approved_at: float
    revoked_at: float
    metadata_json: str


class DeviceRegistry:
    def __init__(self, db_path: str | None = None):
        cfg = get_config()
        self._db_path = db_path or os.path.join(cfg.base_dir, "memory_store", "device_registry.db")
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY,
                    device_name TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    requested_at REAL NOT NULL,
                    approved_at REAL NOT NULL DEFAULT 0,
                    revoked_at REAL NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pair_requests (
                    request_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    requester_user_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    approval_note TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(device_id) REFERENCES devices(device_id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pair_status ON pair_requests(status)")
            conn.commit()
        finally:
            conn.close()

    def pair_request(
        self,
        *,
        device_name: str,
        requester_user_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        device_id = f"dev_{uuid.uuid4().hex[:16]}"
        request_id = f"pair_{uuid.uuid4().hex[:16]}"
        meta = metadata or {}
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO devices(device_id, device_name, owner_user_id, status, requested_at, metadata_json)
                VALUES(?, ?, ?, 'pending', ?, ?)
                """,
                (device_id, device_name or "unknown_device", requester_user_id, now, str(meta)),
            )
            conn.execute(
                """
                INSERT INTO pair_requests(request_id, device_id, requester_user_id, created_at, status)
                VALUES(?, ?, ?, ?, 'pending')
                """,
                (request_id, device_id, requester_user_id, now),
            )
            conn.commit()
            return {
                "ok": True,
                "request_id": request_id,
                "device_id": device_id,
                "status": "pending",
            }
        finally:
            conn.close()

    def approve(
        self,
        *,
        request_id: str,
        approver_user_id: str,
        approval_note: str = "",
    ) -> dict[str, Any]:
        now = time.time()
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM pair_requests WHERE request_id=? LIMIT 1", (request_id,)).fetchone()
            if not row:
                return {"ok": False, "error": "request_not_found"}
            if str(row["status"]) != "pending":
                return {"ok": False, "error": "request_not_pending"}
            device_id = str(row["device_id"])
            conn.execute(
                "UPDATE pair_requests SET status='approved', approval_note=? WHERE request_id=?",
                (approval_note, request_id),
            )
            conn.execute(
                "UPDATE devices SET status='approved', approved_at=?, revoked_at=0 WHERE device_id=?",
                (now, device_id),
            )
            conn.commit()
            return {"ok": True, "request_id": request_id, "device_id": device_id, "approved_by": approver_user_id}
        finally:
            conn.close()

    def revoke(self, *, device_id: str, revoked_by: str, reason: str = "") -> dict[str, Any]:
        now = time.time()
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM devices WHERE device_id=? LIMIT 1", (device_id,)).fetchone()
            if not row:
                return {"ok": False, "error": "device_not_found"}
            conn.execute(
                "UPDATE devices SET status='revoked', revoked_at=? WHERE device_id=?",
                (now, device_id),
            )
            conn.commit()
            return {"ok": True, "device_id": device_id, "revoked_by": revoked_by, "reason": reason}
        finally:
            conn.close()

    def list_devices(self, *, status: str | None = None) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            if status:
                rows = conn.execute("SELECT * FROM devices WHERE status=? ORDER BY requested_at DESC", (status,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM devices ORDER BY requested_at DESC").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def is_trusted(self, device_id: str) -> bool:
        if not device_id:
            return False
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM devices WHERE device_id=? AND status='approved' LIMIT 1",
                (device_id,),
            ).fetchone()
            return bool(row)
        finally:
            conn.close()


_REGISTRY: DeviceRegistry | None = None


def get_device_registry() -> DeviceRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = DeviceRegistry()
    return _REGISTRY
