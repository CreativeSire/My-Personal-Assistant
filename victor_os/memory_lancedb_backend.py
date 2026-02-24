"""
Victor-OS LanceDB Memory Backend
==================================
Drop-in scalable replacement for the SQLite vector memory backend.
Activated by setting MEMORY_BACKEND=lancedb in .env

Advantages over SQLite vectors:
  - Handles millions of records without degrading
  - Native ANN (approximate nearest neighbour) search
  - Columnar storage — fast scans, low memory footprint
  - Persistent on disk, no RAM limit

Schema mirrors memory_core.py MemoryRecord so it is interchangeable.

Usage (from memory_core.py):
    if cfg.memory_backend == "lancedb":
        from memory_lancedb_backend import LanceDBMemoryBackend
        backend = LanceDBMemoryBackend()
        backend.save(record)
        results = backend.search(query_embedding, agent_filter=..., top_k=10)
"""

from __future__ import annotations

import os
import time
import json
from dataclasses import dataclass, asdict
from typing import Optional, Any

from config import get_config
from logging_config import get_logger

logger = get_logger("memory_lancedb")
_cfg = get_config()

_LANCEDB_PATH = os.path.join(_cfg.base_dir, "memory_store", "victor_brain_lancedb")
_TABLE_NAME = "memories"


@dataclass
class LanceMemoryRecord:
    id: str
    text: str
    memory_type: str        # observation | task | decision | constraint | project_fact | status | other
    scope: str              # global | agent
    agent_name: str
    priority: int
    source: str
    tags_json: str          # JSON array string
    sensitive_refs_json: str
    created_at: float
    vector: list[float]     # embedding


class LanceDBMemoryBackend:
    """
    LanceDB-backed memory store. Lazy-initialises on first use.
    Falls back gracefully if lancedb package is not installed.
    """

    def __init__(self, db_path: str = _LANCEDB_PATH):
        self._db_path = db_path
        self._db = None
        self._table = None
        self._available = self._try_init()

    def _try_init(self) -> bool:
        try:
            import lancedb  # type: ignore
            os.makedirs(self._db_path, exist_ok=True)
            self._db = lancedb.connect(self._db_path)
            if _TABLE_NAME in self._db.table_names():
                self._table = self._db.open_table(_TABLE_NAME)
            # Table will be created on first write (schema inferred from first record)
            logger.info(f"LanceDB backend initialised at {self._db_path}")
            return True
        except ImportError:
            logger.warning("lancedb package not installed — LanceDB backend unavailable. Run: pip install lancedb")
            return False
        except Exception as exc:
            logger.error(f"LanceDB init failed: {exc}")
            return False

    def is_available(self) -> bool:
        return self._available

    def save(self, record: dict[str, Any], vector: list[float]) -> bool:
        """
        Save a memory record with its embedding vector.
        Returns True on success.
        """
        if not self._available:
            return False
        try:
            import lancedb  # type: ignore
            import pyarrow as pa  # type: ignore

            row = {
                "id":                 str(record.get("id", "")),
                "text":               str(record.get("text", "")),
                "memory_type":        str(record.get("memory_type", "other")),
                "scope":              str(record.get("scope", "global")),
                "agent_name":         str(record.get("agent_name", "") or ""),
                "priority":           int(record.get("priority", 3)),
                "source":             str(record.get("source", "")),
                "tags_json":          json.dumps(record.get("tags", []) or []),
                "sensitive_refs_json": json.dumps(record.get("sensitive_refs", []) or []),
                "created_at":         float(record.get("created_at", time.time())),
                "vector":             vector,
            }

            if self._table is None:
                self._table = self._db.create_table(_TABLE_NAME, data=[row])
            else:
                self._table.add([row])

            return True
        except Exception as exc:
            logger.error(f"LanceDB save failed: {exc}")
            return False

    def search(
        self,
        query_vector: list[float],
        top_k: int = 10,
        agent_filter: Optional[str] = None,
        scope_filter: Optional[str] = None,
        memory_type_filter: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """
        ANN vector search with optional metadata filters.
        Returns list of record dicts (without the vector field).
        """
        if not self._available or self._table is None:
            return []
        try:
            query = self._table.search(query_vector).limit(top_k * 3)

            # Apply post-search filters (LanceDB supports SQL-style predicates)
            filters = []
            if agent_filter:
                filters.append(f"agent_name = '{agent_filter}'")
            if scope_filter:
                filters.append(f"scope = '{scope_filter}'")
            if memory_type_filter:
                filters.append(f"memory_type = '{memory_type_filter}'")
            if filters:
                query = query.where(" AND ".join(filters))

            results = query.limit(top_k).to_list()
            out = []
            for r in results:
                out.append({
                    "id":           r.get("id", ""),
                    "text":         r.get("text", ""),
                    "content_plain": r.get("text", ""),
                    "memory_type":  r.get("memory_type", "other"),
                    "scope":        r.get("scope", "global"),
                    "agent_name":   r.get("agent_name", ""),
                    "priority":     r.get("priority", 3),
                    "source":       r.get("source", ""),
                    "tags":         json.loads(r.get("tags_json", "[]") or "[]"),
                    "created_at":   r.get("created_at", 0),
                    "_distance":    r.get("_distance", 0),
                })
            return out
        except Exception as exc:
            logger.error(f"LanceDB search failed: {exc}")
            return []

    def migrate_from_sqlite(self, sqlite_db_path: str) -> int:
        """
        One-time migration: export all records from SQLite memory store to LanceDB.
        Returns count of records migrated.
        Run this once when switching backends.
        """
        if not self._available:
            logger.error("LanceDB unavailable — migration aborted.")
            return 0

        import sqlite3
        if not os.path.exists(sqlite_db_path):
            logger.warning(f"SQLite DB not found at {sqlite_db_path}")
            return 0

        conn = sqlite3.connect(sqlite_db_path)
        conn.row_factory = sqlite3.Row
        count = 0
        try:
            rows = conn.execute("""
                SELECT m.id, m.text, m.memory_type, m.scope, m.agent_name,
                       m.priority, m.source, m.tags_json, m.sensitive_refs_json,
                       m.created_at, v.vector_json
                FROM memories m
                LEFT JOIN memory_vectors v ON v.memory_id = m.id
                WHERE m.deleted = 0
            """).fetchall()
        except Exception as exc:
            logger.error(f"SQLite read failed during migration: {exc}")
            conn.close()
            return 0
        finally:
            conn.close()

        for row in rows:
            try:
                vector_raw = row["vector_json"] or "[]"
                vector = json.loads(vector_raw) if isinstance(vector_raw, str) else []
                if not vector:
                    continue   # skip records without embeddings
                record = {
                    "id":           row["id"],
                    "text":         row["text"],
                    "memory_type":  row["memory_type"],
                    "scope":        row["scope"],
                    "agent_name":   row["agent_name"] or "",
                    "priority":     row["priority"],
                    "source":       row["source"],
                    "tags":         json.loads(row["tags_json"] or "[]"),
                    "sensitive_refs": json.loads(row["sensitive_refs_json"] or "[]"),
                    "created_at":   row["created_at"],
                }
                if self.save(record, vector):
                    count += 1
            except Exception as exc:
                logger.debug(f"Skip record during migration: {exc}")

        logger.info(f"LanceDB migration complete: {count} records migrated from SQLite.")
        return count

    def count(self) -> int:
        """Return total number of records in LanceDB."""
        if not self._available or self._table is None:
            return 0
        try:
            return self._table.count_rows()
        except Exception:
            return 0


# Module-level singleton
_backend: LanceDBMemoryBackend | None = None


def get_lancedb_backend() -> LanceDBMemoryBackend:
    global _backend
    if _backend is None:
        _backend = LanceDBMemoryBackend()
    return _backend
