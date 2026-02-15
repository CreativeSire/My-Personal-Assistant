"""
Victor ERP Staff Client (offline-first local model).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any


@dataclass
class StaffClientConfig:
    base_dir: str
    db_path: str


class StaffDataStore:
    def __init__(self, cfg: StaffClientConfig):
        self.cfg = cfg
        os.makedirs(os.path.dirname(self.cfg.db_path), exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.cfg.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._conn()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS outbox_events (
                id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                retries INTEGER NOT NULL DEFAULT 0,
                next_retry_at REAL NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_completions (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                proof_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS compliance_local (
                id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        conn.commit()
        conn.close()

    def enqueue_event(self, event_type: str, payload: dict[str, Any], idempotency_key: str | None = None) -> str:
        event_id = uuid.uuid4().hex
        idem = idempotency_key or uuid.uuid4().hex
        now = time.time()
        conn = self._conn()
        conn.execute(
            """
            INSERT INTO outbox_events (
                id, idempotency_key, event_type, payload_json, status, retries, next_retry_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'pending', 0, ?, ?, ?)
            """,
            (event_id, idem, event_type, json.dumps(payload, ensure_ascii=False), now, now, now),
        )
        conn.commit()
        conn.close()
        return event_id

    def save_completion(self, task_id: str, proof_type: str, payload: dict[str, Any], status: str) -> str:
        completion_id = uuid.uuid4().hex
        now = time.time()
        conn = self._conn()
        conn.execute(
            """
            INSERT INTO task_completions (id, task_id, proof_type, payload_json, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (completion_id, task_id, proof_type, json.dumps(payload, ensure_ascii=False), status, now),
        )
        conn.commit()
        conn.close()
        return completion_id

