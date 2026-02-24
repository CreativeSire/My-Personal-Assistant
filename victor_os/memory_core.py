import os
import re
import json
import time
import hashlib
import sqlite3
import datetime
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
from cryptography.fernet import Fernet
from google.genai import Client

from config import get_config
from logging_config import get_logger, setup_logging
from resilience import retry_with_backoff, gemini_breaker

cfg = get_config()
logger = get_logger("memory_core")

# Initialize logging
setup_logging(cfg.log_dir)

BASE_DIR = cfg.base_dir
DB_PATH = cfg.vault_db_path
VECTORS_DIR = cfg.vectors_dir
EMBEDDING_MODEL = cfg.embedding_model
FERNET_KEY_ENV = cfg.fernet_key_env

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
os.makedirs(VECTORS_DIR, exist_ok=True)

api_key = cfg.gemini_api_key or cfg.google_api_key
client = Client(api_key=api_key)


@dataclass
class MemoryRecordInput:
    text: str
    memory_type: str = "other"
    scope: str = "global"
    agent_name: Optional[str] = None
    priority: int = 3
    source: str = "telegram"
    tags: list[str] = field(default_factory=list)
    sensitive_refs: list[dict[str, Any]] = field(default_factory=list)
    ttl_at: Optional[str] = None
    metadata_json: Optional[dict[str, Any]] = None


@dataclass
class MemoryQueryInput:
    query: str
    scope_filter: Optional[str] = None
    agent_filter: Optional[str] = None
    type_filter: Optional[list[str]] = None
    top_k: int = 5
    min_similarity: float = 0.35


def _get_fernet():
    key = os.getenv(FERNET_KEY_ENV, "").strip()
    if not key:
        return None
    try:
        return Fernet(key.encode("utf-8"))
    except Exception:
        return None


def _now_iso():
    return datetime.datetime.now(datetime.UTC).isoformat()


def _normalized_text(text: str) -> str:
    clean = re.sub(r"\s+", " ", (text or "").strip().lower())
    return clean


def _canonical_hash(text: str, memory_type: str, scope: str, agent_name: Optional[str]) -> str:
    canonical = f"{_normalized_text(text)}|{memory_type.lower()}|{scope.lower()}|{(agent_name or '').lower()}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _ensure_json_dict(raw_value: Any) -> dict[str, Any]:
    if isinstance(raw_value, dict):
        return raw_value
    if isinstance(raw_value, str) and raw_value.strip():
        try:
            loaded = json.loads(raw_value)
            if isinstance(loaded, dict):
                return loaded
        except Exception:
            return {}
    return {}


def _merge_metadata(existing_meta: dict[str, Any], record: MemoryRecordInput) -> dict[str, Any]:
    incoming_meta = _ensure_json_dict(record.metadata_json)
    existing_tags = set(existing_meta.get("tags", []))
    incoming_tags = set(record.tags or [])
    merged_tags = sorted(existing_tags | incoming_tags)
    hit_count = int(existing_meta.get("hit_count", 1)) + 1
    merged = dict(existing_meta)
    merged.update(incoming_meta)
    merged["tags"] = merged_tags
    merged["hit_count"] = hit_count
    merged["source"] = record.source
    merged["last_seen_at"] = _now_iso()
    return merged


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def _create_base_table(conn: sqlite3.Connection):
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            content TEXT,
            embedding BLOB,
            metadata TEXT,
            timestamp TEXT
        )
        """
    )
    conn.commit()


def _migrate_schema(conn: sqlite3.Connection):
    cur = conn.cursor()
    additions = [
        ("scope", "TEXT"),
        ("agent_name", "TEXT"),
        ("memory_type", "TEXT"),
        ("content_plain", "TEXT"),
        ("content_cipher", "BLOB"),
        ("content_hash", "TEXT"),
        ("priority", "INTEGER"),
        ("source", "TEXT"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
        ("ttl_at", "TEXT"),
        ("metadata_json", "TEXT"),
    ]
    for column, dtype in additions:
        if not _column_exists(conn, "memories", column):
            cur.execute(f"ALTER TABLE memories ADD COLUMN {column} {dtype}")

    cur.execute("UPDATE memories SET scope='global' WHERE scope IS NULL OR scope=''")
    cur.execute("UPDATE memories SET memory_type='other' WHERE memory_type IS NULL OR memory_type=''")
    cur.execute("UPDATE memories SET priority=3 WHERE priority IS NULL")
    cur.execute("UPDATE memories SET source='legacy' WHERE source IS NULL OR source=''")
    cur.execute("UPDATE memories SET created_at=COALESCE(created_at, timestamp)")
    cur.execute("UPDATE memories SET updated_at=COALESCE(updated_at, timestamp, created_at)")
    cur.execute("UPDATE memories SET content_plain=COALESCE(content_plain, content, '')")
    cur.execute("UPDATE memories SET metadata_json=COALESCE(metadata_json, metadata, '{}')")

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_mem_scope_agent
        ON memories(scope, agent_name)
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_mem_type_priority
        ON memories(memory_type, priority)
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_mem_updated_at
        ON memories(updated_at)
        """
    )
    cur.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_mem_content_hash_unique
        ON memories(content_hash)
        WHERE content_hash IS NOT NULL
        """
    )
    conn.commit()

    cur.execute("SELECT id, content_plain, memory_type, scope, agent_name FROM memories WHERE content_hash IS NULL")
    rows = cur.fetchall()
    for row in rows:
        content_hash = _canonical_hash(row["content_plain"] or "", row["memory_type"] or "other", row["scope"] or "global", row["agent_name"])
        cur.execute("UPDATE memories SET content_hash=? WHERE id=?", (content_hash, row["id"]))
    conn.commit()


def init_db():
    conn = _connect()
    try:
        _create_base_table(conn)
        _migrate_schema(conn)
        logger.info("Memory vault initialized")
    finally:
        conn.close()


init_db()


@retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=15.0)
def _embed_with_retry(text: str):
    """Call Gemini embedding API with retry."""
    return client.models.embed_content(model=EMBEDDING_MODEL, contents=text)


def get_embedding(text: str):
    """Uses Gemini to get vector embeddings with retry and circuit breaker."""
    try:
        response = gemini_breaker.call(_embed_with_retry, text)
        return response.embeddings[0].values
    except Exception as e:
        logger.error(f"Embedding failed: {e}", extra={"tool_name": "get_embedding"})
        return None


def save_memory(record: MemoryRecordInput) -> dict[str, Any]:
    text = (record.text or "").strip()
    if not text:
        return {"ok": False, "status": "rejected", "reason": "empty_text"}

    scope = "agent" if (record.scope or "").lower() == "agent" else "global"
    agent_name = (record.agent_name or "").strip() or None
    if scope == "agent" and not agent_name:
        return {"ok": False, "status": "rejected", "reason": "agent_scope_requires_agent_name"}

    priority = max(1, min(5, int(record.priority or 3)))
    memory_type = (record.memory_type or "other").strip().lower()
    tags = sorted(set(record.tags or []))
    metadata_json = _ensure_json_dict(record.metadata_json)
    metadata_json.setdefault("tags", tags)
    metadata_json.setdefault("hit_count", 1)
    metadata_json.setdefault("source", record.source)

    content_plain = text
    content_cipher = None
    if record.sensitive_refs:
        fernet = _get_fernet()
        if not fernet:
            return {
                "ok": False,
                "status": "rejected",
                "reason": "missing_or_invalid_memory_key",
                "required_env": FERNET_KEY_ENV,
            }
        content_cipher = fernet.encrypt(json.dumps(record.sensitive_refs).encode("utf-8"))

    embedding = get_embedding(content_plain)
    if embedding is None:
        return {"ok": False, "status": "error", "reason": "embedding_failed"}

    content_hash = _canonical_hash(content_plain, memory_type, scope, agent_name)
    now = _now_iso()
    embedding_blob = np.array(embedding, dtype=np.float32).tobytes()

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, metadata_json FROM memories WHERE content_hash=?", (content_hash,))
        existing = cur.fetchone()
        if existing:
            merged_meta = _merge_metadata(_ensure_json_dict(existing["metadata_json"]), record)
            cur.execute(
                """
                UPDATE memories
                SET updated_at=?, priority=?, source=?, metadata_json=?, ttl_at=?, content_cipher=COALESCE(?, content_cipher)
                WHERE id=?
                """,
                (now, priority, record.source, json.dumps(merged_meta), record.ttl_at, content_cipher, existing["id"]),
            )
            conn.commit()
            logger.info(f"Memory dedup-merged: {existing['id']}", extra={"agent_name": "memory_core"})
            return {"ok": True, "status": "dedup_merged", "id": existing["id"], "memory_type": memory_type, "scope": scope}

        clean_type = memory_type.replace(" ", "_")
        doc_id = f"{scope}_{clean_type}_{int(time.time() * 1000)}"
        cur.execute(
            """
            INSERT INTO memories (
                id, content, embedding, metadata, timestamp,
                scope, agent_name, memory_type, content_plain, content_cipher,
                content_hash, priority, source, created_at, updated_at, ttl_at, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc_id,
                content_plain,
                embedding_blob,
                json.dumps({"type": memory_type, "tags": tags}),
                now,
                scope,
                agent_name,
                memory_type,
                content_plain,
                content_cipher,
                content_hash,
                priority,
                record.source,
                now,
                now,
                record.ttl_at,
                json.dumps(metadata_json),
            ),
        )
        conn.commit()
        with open(os.path.join(VECTORS_DIR, "vault_status.txt"), "w", encoding="utf-8") as f:
            f.write(f"Vault write at {now}")
        logger.info(f"Memory inserted: {doc_id}", extra={"agent_name": "memory_core"})
        return {"ok": True, "status": "inserted", "id": doc_id, "memory_type": memory_type, "scope": scope}
    finally:
        conn.close()


def recall_memory(query_input: MemoryQueryInput) -> dict[str, Any]:
    query = (query_input.query or "").strip()
    if not query:
        return {"ok": False, "reason": "empty_query", "results": []}

    q_embedding = get_embedding(query)
    if q_embedding is None:
        return {"ok": False, "reason": "embedding_failed", "results": []}
    q_vec = np.array(q_embedding, dtype=np.float32)

    conn = _connect()
    try:
        cur = conn.cursor()
        sql = """
            SELECT id, scope, agent_name, memory_type, content_plain, content_cipher, content_hash,
                   embedding, priority, source, created_at, updated_at, ttl_at, metadata_json
            FROM memories
            WHERE content_plain IS NOT NULL AND content_plain != ''
        """
        params: list[Any] = []
        if query_input.scope_filter:
            sql += " AND scope = ?"
            params.append(query_input.scope_filter)
        if query_input.agent_filter:
            sql += " AND agent_name = ?"
            params.append(query_input.agent_filter)
        if query_input.type_filter:
            placeholders = ",".join("?" * len(query_input.type_filter))
            sql += f" AND memory_type IN ({placeholders})"
            params.extend(query_input.type_filter)
        cur.execute(sql, params)
        rows = cur.fetchall()
    finally:
        conn.close()

    scored: list[dict[str, Any]] = []
    for row in rows:
        emb_blob = row["embedding"]
        if not emb_blob:
            continue
        mem_vec = np.frombuffer(emb_blob, dtype=np.float32)
        if len(mem_vec) != len(q_vec):
            continue
        denom = float(np.linalg.norm(mem_vec) * np.linalg.norm(q_vec))
        if denom == 0:
            continue
        similarity = float(np.dot(q_vec, mem_vec) / denom)
        if similarity < float(query_input.min_similarity):
            continue
        scored.append(
            {
                "id": row["id"],
                "scope": row["scope"],
                "agent_name": row["agent_name"],
                "memory_type": row["memory_type"],
                "content_plain": row["content_plain"],
                "priority": int(row["priority"] or 3),
                "source": row["source"],
                "updated_at": row["updated_at"],
                "metadata_json": _ensure_json_dict(row["metadata_json"]),
                "similarity": similarity,
            }
        )

    scored.sort(key=lambda x: (x["similarity"], x["priority"]), reverse=True)
    top_k = max(1, int(query_input.top_k or 5))
    return {"ok": True, "count": len(scored), "results": scored[:top_k]}


def recall_memory_context(query: str, agent_name: str = "Chief_of_Staff", top_k_global: int = 8, top_k_agent: int = 6) -> dict[str, Any]:
    global_hits = recall_memory(
        MemoryQueryInput(
            query=query,
            scope_filter="global",
            top_k=top_k_global,
            min_similarity=0.25,
        )
    )
    agent_hits = recall_memory(
        MemoryQueryInput(
            query=query,
            scope_filter="agent",
            agent_filter=agent_name,
            top_k=top_k_agent,
            min_similarity=0.25,
        )
    )
    return {"global": global_hits.get("results", []), "agent": agent_hits.get("results", [])}


def save_long_term_memory(text: str, tags: str = "general"):
    """Backward-compatible wrapper for legacy tool calls."""
    record = MemoryRecordInput(
        text=text,
        memory_type="other",
        scope="global",
        agent_name=None,
        priority=3,
        source="legacy_tool",
        tags=[tags] if tags else [],
    )
    result = save_memory(record)
    if result.get("ok"):
        if result.get("status") == "dedup_merged":
            return f"Memory merged in Vault [{result.get('id')}]"
        return f"Memory committed to Vault [{result.get('id')}]"
    return f"Memory save failed: {result.get('reason', 'unknown')}"


def recall_long_term_memory(query: str, n_results: int = 5):
    """Backward-compatible wrapper for legacy prompt injection."""
    result = recall_memory(
        MemoryQueryInput(
            query=query,
            scope_filter=None,
            agent_filter=None,
            top_k=n_results,
            min_similarity=0.25,
        )
    )
    if not result.get("ok"):
        return "Memory Recall Error: Could not generate query embedding."
    rows = result.get("results", [])
    if not rows:
        return "No relevant long-term memories found."
    lines = [f"- [{r['memory_type']}/{r['scope']}] {r['content_plain']}" for r in rows]
    return "VAULT SEARCH RESULTS:\n" + "\n".join(lines)


def save_agent_memory(
    text: str,
    memory_type: str = "other",
    scope: str = "agent",
    agent_name: str = "Chief_of_Staff",
    priority: int = 3,
    source: str = "agent_tool",
    tags: str = "",
):
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    result = save_memory(
        MemoryRecordInput(
            text=text,
            memory_type=memory_type,
            scope=scope,
            agent_name=agent_name if scope == "agent" else None,
            priority=priority,
            source=source,
            tags=tag_list,
        )
    )
    return json.dumps(result, ensure_ascii=False)


def recall_agent_memory(
    query: str,
    scope_filter: str = "",
    agent_filter: str = "",
    type_filter: str = "",
    top_k: int = 5,
    min_similarity: float = 0.25,
):
    types = [t.strip() for t in type_filter.split(",") if t.strip()]
    result = recall_memory(
        MemoryQueryInput(
            query=query,
            scope_filter=scope_filter or None,
            agent_filter=agent_filter or None,
            type_filter=types or None,
            top_k=top_k,
            min_similarity=min_similarity,
        )
    )
    return json.dumps(result, ensure_ascii=False)


if __name__ == "__main__":
    init_db()
    print(save_long_term_memory("Victor OS Initialized with SQLite Vector Vault.", "system"))
    print(recall_long_term_memory("How is memory stored?"))
