"""
Victor data engine.

Phase-1 scope:
- Canonical SQLite schema for telemetry/training signals.
- Event envelope writer.
- Correction/tool/policy/task/delivery stores.
- Feature-store rollups.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any


_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "victor_os",
    "memory_store",
    "victor_platform.db",
)


def _utc_ts() -> float:
    return time.time()


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_load(text: str | None, fallback: Any) -> Any:
    if not text:
        return fallback
    try:
        return json.loads(text)
    except Exception:
        return fallback


def _payload_hash(payload: Any) -> str:
    raw = _json_dump(payload if payload is not None else {})
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class EventEnvelope:
    event_type: str
    session_id: str = ""
    task_id: str = ""
    actor: str = "system"
    payload: dict[str, Any] | list[Any] | str | None = None
    risk_score: float = 0.0
    channel: str = "system"
    source: str = "platform"
    event_id: str = ""
    ts_utc: float = 0.0

    def normalized(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id or uuid.uuid4().hex,
            "event_type": str(self.event_type or "").strip(),
            "ts_utc": float(self.ts_utc or _utc_ts()),
            "session_id": str(self.session_id or "").strip(),
            "task_id": str(self.task_id or "").strip(),
            "actor": str(self.actor or "system").strip(),
            "payload": self.payload if self.payload is not None else {},
            "risk_score": float(self.risk_score or 0.0),
            "channel": str(self.channel or "system").strip(),
            "source": str(self.source or "platform").strip(),
        }


class DataEngine:
    """Thread-safe sqlite-backed platform telemetry store."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or _DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS events (
                        event_id TEXT PRIMARY KEY,
                        event_type TEXT NOT NULL,
                        ts_utc REAL NOT NULL,
                        session_id TEXT,
                        task_id TEXT,
                        actor TEXT,
                        payload_json TEXT NOT NULL,
                        risk_score REAL DEFAULT 0.0,
                        channel TEXT DEFAULT 'system',
                        source TEXT DEFAULT 'platform'
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS corrections (
                        id TEXT PRIMARY KEY,
                        ts_utc REAL NOT NULL,
                        session_id TEXT,
                        task_id TEXT,
                        channel TEXT,
                        actor TEXT,
                        reason_code TEXT,
                        rejected_output TEXT,
                        preferred_output TEXT,
                        metadata_json TEXT DEFAULT '{}'
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tool_calls (
                        id TEXT PRIMARY KEY,
                        ts_utc REAL NOT NULL,
                        task_id TEXT,
                        session_id TEXT,
                        actor TEXT,
                        tool_name TEXT NOT NULL,
                        action TEXT DEFAULT '',
                        status TEXT NOT NULL,
                        latency_ms REAL DEFAULT 0,
                        retries INTEGER DEFAULT 0,
                        side_effects_json TEXT DEFAULT '{}',
                        input_json TEXT DEFAULT '{}',
                        output_json TEXT DEFAULT '{}'
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS policy_decisions (
                        id TEXT PRIMARY KEY,
                        ts_utc REAL NOT NULL,
                        task_id TEXT,
                        session_id TEXT,
                        actor TEXT,
                        action TEXT NOT NULL,
                        policy_tier TEXT NOT NULL,
                        allowed INTEGER NOT NULL,
                        reason TEXT DEFAULT '',
                        metadata_json TEXT DEFAULT '{}'
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS task_runs (
                        task_id TEXT PRIMARY KEY,
                        created_ts REAL NOT NULL,
                        updated_ts REAL NOT NULL,
                        channel TEXT,
                        user_id TEXT,
                        state TEXT NOT NULL,
                        idempotency_key TEXT,
                        payload_hash TEXT,
                        metadata_json TEXT DEFAULT '{}'
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS delivery_ledger (
                        id TEXT PRIMARY KEY,
                        ts_utc REAL NOT NULL,
                        destination TEXT NOT NULL,
                        dedupe_key TEXT NOT NULL,
                        payload_hash TEXT NOT NULL,
                        status TEXT NOT NULL,
                        transport TEXT DEFAULT 'unknown',
                        task_id TEXT DEFAULT '',
                        metadata_json TEXT DEFAULT '{}'
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_delivery_dedupe
                    ON delivery_ledger(destination, dedupe_key, payload_hash)
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS feature_store (
                        feature_key TEXT PRIMARY KEY,
                        feature_value REAL NOT NULL,
                        updated_ts REAL NOT NULL,
                        metadata_json TEXT DEFAULT '{}'
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, ts_utc)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, ts_utc)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_corrections_ts ON corrections(ts_utc)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_calls_task ON tool_calls(task_id, ts_utc)")
                conn.commit()
            finally:
                conn.close()

    def emit_event(self, event: EventEnvelope | dict[str, Any]) -> dict[str, Any]:
        envelope = event.normalized() if isinstance(event, EventEnvelope) else EventEnvelope(**event).normalized()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO events (
                        event_id, event_type, ts_utc, session_id, task_id,
                        actor, payload_json, risk_score, channel, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        envelope["event_id"],
                        envelope["event_type"],
                        envelope["ts_utc"],
                        envelope["session_id"],
                        envelope["task_id"],
                        envelope["actor"],
                        _json_dump(envelope["payload"]),
                        envelope["risk_score"],
                        envelope["channel"],
                        envelope["source"],
                    ),
                )
                conn.commit()
                return envelope
            finally:
                conn.close()

    def record_correction(
        self,
        *,
        session_id: str,
        task_id: str,
        channel: str,
        actor: str,
        reason_code: str,
        rejected_output: str,
        preferred_output: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        correction_id = uuid.uuid4().hex
        now = _utc_ts()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO corrections (
                        id, ts_utc, session_id, task_id, channel, actor,
                        reason_code, rejected_output, preferred_output, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        correction_id,
                        now,
                        session_id,
                        task_id,
                        channel,
                        actor,
                        reason_code,
                        rejected_output,
                        preferred_output,
                        _json_dump(metadata or {}),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        self.emit_event(
            EventEnvelope(
                event_type="feedback.recorded",
                session_id=session_id,
                task_id=task_id,
                actor=actor or "user",
                payload={
                    "correction_id": correction_id,
                    "reason_code": reason_code,
                    "channel": channel,
                },
            )
        )
        self.emit_event(
            EventEnvelope(
                event_type="training.example.emitted",
                session_id=session_id,
                task_id=task_id,
                actor=actor or "user",
                payload={
                    "source": "correction",
                    "correction_id": correction_id,
                    "reason_code": reason_code,
                },
            )
        )
        return correction_id

    def record_tool_call(
        self,
        *,
        tool_name: str,
        status: str,
        task_id: str = "",
        session_id: str = "",
        actor: str = "system",
        action: str = "",
        latency_ms: float = 0.0,
        retries: int = 0,
        side_effects: dict[str, Any] | None = None,
        input_data: dict[str, Any] | None = None,
        output_data: dict[str, Any] | None = None,
    ) -> str:
        item_id = uuid.uuid4().hex
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO tool_calls (
                        id, ts_utc, task_id, session_id, actor, tool_name, action, status,
                        latency_ms, retries, side_effects_json, input_json, output_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        _utc_ts(),
                        task_id,
                        session_id,
                        actor,
                        tool_name,
                        action,
                        status,
                        latency_ms,
                        retries,
                        _json_dump(side_effects or {}),
                        _json_dump(input_data or {}),
                        _json_dump(output_data or {}),
                    ),
                )
                conn.commit()
                return item_id
            finally:
                conn.close()

    def record_policy_decision(
        self,
        *,
        action: str,
        policy_tier: str,
        allowed: bool,
        reason: str = "",
        task_id: str = "",
        session_id: str = "",
        actor: str = "policy_engine",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        item_id = uuid.uuid4().hex
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO policy_decisions (
                        id, ts_utc, task_id, session_id, actor, action, policy_tier, allowed, reason, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        _utc_ts(),
                        task_id,
                        session_id,
                        actor,
                        action,
                        policy_tier,
                        1 if allowed else 0,
                        reason,
                        _json_dump(metadata or {}),
                    ),
                )
                conn.commit()
                return item_id
            finally:
                conn.close()

    def upsert_task_run(
        self,
        *,
        task_id: str,
        state: str,
        channel: str = "",
        user_id: str = "",
        idempotency_key: str = "",
        payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = _utc_ts()
        payload_digest = _payload_hash(payload or {})
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO task_runs (
                        task_id, created_ts, updated_ts, channel, user_id, state,
                        idempotency_key, payload_hash, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id) DO UPDATE SET
                        updated_ts=excluded.updated_ts,
                        state=excluded.state,
                        channel=excluded.channel,
                        user_id=excluded.user_id,
                        idempotency_key=excluded.idempotency_key,
                        payload_hash=excluded.payload_hash,
                        metadata_json=excluded.metadata_json
                    """,
                    (
                        task_id,
                        now,
                        now,
                        channel,
                        user_id,
                        state,
                        idempotency_key,
                        payload_digest,
                        _json_dump(metadata or {}),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def record_delivery_attempt(
        self,
        *,
        destination: str,
        dedupe_key: str,
        payload: Any,
        status: str,
        transport: str,
        task_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        payload_digest = _payload_hash(payload)
        row_id = uuid.uuid4().hex
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO delivery_ledger (
                        id, ts_utc, destination, dedupe_key, payload_hash, status, transport, task_id, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row_id,
                        _utc_ts(),
                        destination,
                        dedupe_key,
                        payload_digest,
                        status,
                        transport,
                        task_id,
                        _json_dump(metadata or {}),
                    ),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False
            finally:
                conn.close()

    def has_delivery(self, *, destination: str, dedupe_key: str, payload: Any) -> bool:
        payload_digest = _payload_hash(payload)
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT 1 FROM delivery_ledger
                WHERE destination=? AND dedupe_key=? AND payload_hash=?
                LIMIT 1
                """,
                (destination, dedupe_key, payload_digest),
            ).fetchone()
            return bool(row)
        finally:
            conn.close()

    def set_feature(self, feature_key: str, value: float, metadata: dict[str, Any] | None = None) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO feature_store(feature_key, feature_value, updated_ts, metadata_json)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(feature_key) DO UPDATE SET
                        feature_value=excluded.feature_value,
                        updated_ts=excluded.updated_ts,
                        metadata_json=excluded.metadata_json
                    """,
                    (feature_key, float(value), _utc_ts(), _json_dump(metadata or {})),
                )
                conn.commit()
            finally:
                conn.close()

    def recalc_core_features(self) -> dict[str, float]:
        conn = self._connect()
        try:
            total_events = conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
            total_corrections = conn.execute("SELECT COUNT(*) c FROM corrections").fetchone()["c"]
            total_tool_calls = conn.execute("SELECT COUNT(*) c FROM tool_calls").fetchone()["c"]
            blocked_actions = conn.execute(
                "SELECT COUNT(*) c FROM policy_decisions WHERE allowed=0"
            ).fetchone()["c"]
            correction_ratio = (float(total_corrections) / float(total_events)) if total_events else 0.0
            features = {
                "events.total": float(total_events),
                "corrections.total": float(total_corrections),
                "tool_calls.total": float(total_tool_calls),
                "policy.blocked.total": float(blocked_actions),
                "quality.correction_ratio": correction_ratio,
            }
        finally:
            conn.close()
        for k, v in features.items():
            self.set_feature(k, v, metadata={"source": "recalc_core_features"})
        return features

    def upsert_feature(self, *, feature_key: str, feature_value: float, metadata: dict[str, Any] | None = None) -> None:
        """
        Backwards/compat alias used by some skills/specs.
        """
        self.set_feature(feature_key, float(feature_value), metadata=metadata)

    def get_feature(self, feature_key: str, default: float = 0.0) -> float:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT feature_value FROM feature_store WHERE feature_key=? LIMIT 1",
                (str(feature_key),),
            ).fetchone()
            if not row:
                return float(default)
            return float(row["feature_value"] or 0.0)
        finally:
            conn.close()

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM task_runs WHERE task_id=? LIMIT 1",
                (task_id,),
            ).fetchone()
            if not row:
                return None
            result = dict(row)
            result["metadata_json"] = _json_load(result.get("metadata_json"), {})
            return result
        finally:
            conn.close()

    def list_recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY ts_utc DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
            out: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                item["payload_json"] = _json_load(item.get("payload_json"), {})
                out.append(item)
            return out
        finally:
            conn.close()


_ENGINE: DataEngine | None = None


def get_data_engine() -> DataEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = DataEngine()
    return _ENGINE
