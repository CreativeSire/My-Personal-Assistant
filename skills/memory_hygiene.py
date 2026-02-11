import json
import os
import sqlite3
from typing import Callable

from config import get_config
from skill_base import Skill, SkillManifest


class MemoryHygieneSkill(Skill):
    def __init__(self):
        cfg = get_config()
        self._vault_db = cfg.vault_db_path

    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="memory_hygiene",
            display_name="Memory Hygiene",
            version="1.0.0",
            description="Audits memory distribution, sparse categories, and dedup health.",
            triggers=[r"\bmemory hygiene\b", r"\bmemory audit\b", r"\bvault health\b"],
        )

    def _stats(self) -> dict:
        if not os.path.exists(self._vault_db):
            return {"total": 0, "by_type": {}, "dedup_merged": 0}
        conn = sqlite3.connect(self._vault_db)
        cur = conn.cursor()
        try:
            cur.execute("SELECT COUNT(*) FROM memories")
            total = int(cur.fetchone()[0])

            cur.execute("SELECT memory_type, COUNT(*) FROM memories GROUP BY memory_type")
            by_type = {k: int(v) for k, v in cur.fetchall()}

            cur.execute("SELECT metadata_json FROM memories WHERE metadata_json IS NOT NULL")
            merged = 0
            for (raw_meta,) in cur.fetchall():
                try:
                    meta = json.loads(raw_meta or "{}")
                    if int(meta.get("hit_count", 1)) > 1:
                        merged += 1
                except Exception:
                    continue
            return {"total": total, "by_type": by_type, "dedup_merged": merged}
        finally:
            conn.close()

    def tool_memory_hygiene_report(self) -> str:
        stats = self._stats()
        lines = [f"- Total memories: {stats['total']}"]
        lines.append(f"- Dedup-merged records: {stats['dedup_merged']}")
        lines.append("- Distribution by type:")
        for key, value in sorted(stats["by_type"].items()):
            lines.append(f"  - {key}: {value}")
        return "Memory Hygiene Report\n" + "\n".join(lines)

    def get_tools(self) -> list[Callable]:
        return [self.tool_memory_hygiene_report]

    def get_proactive_checks(self) -> list[dict]:
        def _check():
            stats = self._stats()
            sparse = [
                name
                for name in ("project_fact", "task", "decision", "constraint")
                if stats["by_type"].get(name, 0) == 0
            ]
            if sparse:
                return "Memory Hygiene Alert\n- Sparse categories: " + ", ".join(sparse)
            return None

        return [
            {
                "name": "memory_hygiene_sparse",
                "interval_seconds": 900,
                "callback": _check,
                "channel_hint": "telegram",
                "user_id": "ceejay",
            }
        ]


def create_skill():
    return MemoryHygieneSkill()
