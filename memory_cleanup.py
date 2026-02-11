"""
Victor-OS Memory TTL Cleanup
Purges expired memories based on the ttl_at column and cleans up orphaned vectors.
Runs as a proactive check registered with the ProactiveEngine.
"""

import json
import os
import sqlite3
import datetime

from config import get_config
from logging_config import get_logger

logger = get_logger("memory_cleanup")
cfg = get_config()


def purge_expired_memories() -> dict:
    """Delete memories where ttl_at has passed. Returns stats."""
    db_path = cfg.vault_db_path
    if not os.path.exists(db_path):
        return {"purged": 0, "error": None}

    now = datetime.datetime.utcnow().isoformat()
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Count before
        cursor.execute(
            "SELECT COUNT(*) FROM memories WHERE ttl_at IS NOT NULL AND ttl_at != '' AND ttl_at < ?",
            (now,),
        )
        expired_count = cursor.fetchone()[0]

        if expired_count > 0:
            # Get IDs for vector cleanup
            cursor.execute(
                "SELECT id FROM memories WHERE ttl_at IS NOT NULL AND ttl_at != '' AND ttl_at < ?",
                (now,),
            )
            expired_ids = [row[0] for row in cursor.fetchall()]

            # Delete expired rows
            cursor.execute(
                "DELETE FROM memories WHERE ttl_at IS NOT NULL AND ttl_at != '' AND ttl_at < ?",
                (now,),
            )
            conn.commit()

            # Clean up orphaned vector files
            vectors_dir = cfg.vectors_dir
            if os.path.exists(vectors_dir):
                for mid in expired_ids:
                    vec_path = os.path.join(vectors_dir, f"{mid}.npy")
                    if os.path.exists(vec_path):
                        try:
                            os.remove(vec_path)
                        except Exception:
                            pass

            logger.info(f"Memory TTL cleanup: purged {expired_count} expired memories")
        else:
            logger.debug("Memory TTL cleanup: nothing to purge")

        conn.close()
        return {"purged": expired_count, "error": None}

    except Exception as e:
        logger.error(f"Memory TTL cleanup error: {e}")
        return {"purged": 0, "error": str(e)}


def cleanup_duplicate_memories(similarity_threshold: float = 0.98) -> dict:
    """Find and remove near-duplicate memories based on content_hash."""
    db_path = cfg.vault_db_path
    if not os.path.exists(db_path):
        return {"removed": 0}

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Find duplicate content_hash groups (keep the newest)
        cursor.execute("""
            SELECT content_hash, COUNT(*) as cnt
            FROM memories
            WHERE content_hash IS NOT NULL AND content_hash != ''
            GROUP BY content_hash
            HAVING cnt > 1
        """)
        dup_groups = cursor.fetchall()

        removed = 0
        for content_hash, count in dup_groups:
            # Keep the row with the latest updated_at, delete the rest
            cursor.execute("""
                DELETE FROM memories WHERE id IN (
                    SELECT id FROM memories
                    WHERE content_hash = ?
                    ORDER BY updated_at DESC
                    LIMIT -1 OFFSET 1
                )
            """, (content_hash,))
            removed += cursor.rowcount

        conn.commit()
        conn.close()

        if removed:
            logger.info(f"Memory dedup cleanup: removed {removed} duplicate memories")
        return {"removed": removed}

    except Exception as e:
        logger.error(f"Memory dedup cleanup error: {e}")
        return {"removed": 0, "error": str(e)}


def run_full_cleanup() -> str | None:
    """Run all memory cleanup tasks. Returns alert string if issues found, None otherwise."""
    ttl_result = purge_expired_memories()
    dedup_result = cleanup_duplicate_memories()

    total_cleaned = ttl_result.get("purged", 0) + dedup_result.get("removed", 0)

    if total_cleaned > 0:
        return json.dumps({
            "alert": "memory_cleanup",
            "severity": "info",
            "expired_purged": ttl_result.get("purged", 0),
            "duplicates_removed": dedup_result.get("removed", 0),
        })
    return None


def get_proactive_check() -> dict:
    """Return a proactive check definition for the ProactiveEngine."""
    return {
        "name": "memory_ttl_cleanup",
        "interval_seconds": 3600,  # Run hourly
        "callback": run_full_cleanup,
        "channel_hint": "telegram",
        "user_id": "ceejay",
    }
