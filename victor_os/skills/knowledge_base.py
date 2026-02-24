"""
Victor-OS Skill: Knowledge Base
Everything Victor reads, hears, or is told becomes a queryable knowledge base.
Structured knowledge entries with categories, cross-references, and smart search.
Sits on top of memory_core but adds structure, tagging, and knowledge graph links.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from typing import Callable

from config import get_config
from data_engine import get_data_engine
from logging_config import get_logger
from memory_core import MemoryRecordInput, save_memory, recall_memory
from skill_base import Skill, SkillManifest

logger = get_logger("knowledge_base_skill")

_CATEGORIES = {
    "fact": "Verified factual information",
    "decision": "Decision made by user or team",
    "procedure": "How to do something (SOP, runbook)",
    "contact": "Person or company information",
    "project": "Project-related information",
    "reference": "External reference material",
    "learning": "Insight or lesson learned",
    "template": "Reusable template or format",
    "config": "System or tool configuration",
    "other": "General knowledge entry",
}


class KnowledgeBaseSkill(Skill):
    """
    Structured knowledge base with categories, cross-references, and semantic search.
    Victor's long-term memory layer above raw memory_core.
    """

    def __init__(self):
        self._cfg = get_config()
        self._de = get_data_engine()
        self._db_path = os.path.join(
            self._cfg.base_dir, "memory_store", "knowledge_base.db"
        )
        self._init_db()

    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="knowledge_base",
            display_name="Knowledge Base",
            version="1.0.0",
            description="Structured knowledge store — everything Victor learns becomes queryable.",
            triggers=[
                r"\bknowledge.*base\b",
                r"\bremember.*that\b",
                r"\bsave.*fact\b",
                r"\brecord.*decision\b",
                r"\bnote.*down\b",
                r"\bwhat.*know.*about\b",
                r"\bsearch.*knowledge\b",
                r"\bwhat.*did.*we.*decide\b",
                r"\bhow.*to.*do\b",
                r"\bprocedure.*for\b",
                r"\bsop.*for\b",
            ],
            enabled=True,
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        conn = self._connect()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS knowledge_entries (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'other',
                tags TEXT NOT NULL DEFAULT '[]',
                source TEXT DEFAULT '',
                confidence REAL DEFAULT 1.0,
                created_ts REAL NOT NULL,
                updated_ts REAL NOT NULL,
                access_count INTEGER DEFAULT 0,
                last_accessed_ts REAL
            );
            CREATE TABLE IF NOT EXISTS knowledge_links (
                from_id TEXT NOT NULL,
                to_id TEXT NOT NULL,
                link_type TEXT DEFAULT 'related',
                PRIMARY KEY (from_id, to_id)
            );
            CREATE INDEX IF NOT EXISTS idx_kb_category ON knowledge_entries(category);
            CREATE INDEX IF NOT EXISTS idx_kb_updated ON knowledge_entries(updated_ts DESC);
        """)
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------ #
    # Tools                                                                #
    # ------------------------------------------------------------------ #

    def tool_save_knowledge(
        self,
        title: str,
        content: str,
        category: str = "other",
        tags: str = "",
        source: str = "",
        confidence: float = 1.0,
    ) -> str:
        """
        Save a knowledge entry to the knowledge base.
        Args:
            title: Short descriptive title.
            content: The knowledge content (can be long).
            category: One of: fact, decision, procedure, contact, project,
                      reference, learning, template, config, other.
            tags: Comma-separated tags for filtering.
            source: Where this knowledge came from.
            confidence: Confidence score 0.0–1.0 (default 1.0 = certain).
        """
        if not title or not content:
            return "Both 'title' and 'content' are required."
        if category not in _CATEGORIES:
            category = "other"
        tags_list = [t.strip() for t in (tags or "").split(",") if t.strip()]
        now = time.time()

        # Check for duplicate title
        conn = self._connect()
        existing = conn.execute(
            "SELECT id FROM knowledge_entries WHERE lower(title)=? LIMIT 1",
            (title.lower(),),
        ).fetchone()

        if existing:
            entry_id = existing["id"]
            conn.execute(
                """UPDATE knowledge_entries SET content=?, category=?, tags=?,
                   source=?, confidence=?, updated_ts=? WHERE id=?""",
                (content, category, json.dumps(tags_list), source, float(confidence), now, entry_id),
            )
            conn.commit()
            conn.close()
            # Also update in memory_core for semantic search
            save_memory(
                MemoryRecordInput(
                    content=f"[KB:{category}] {title}\n{content}",
                    tags=["knowledge_base", category] + tags_list,
                    source=source or "knowledge_base",
                )
            )
            return f"Knowledge updated: '{title}' (id={entry_id})"

        entry_id = uuid.uuid4().hex[:12]
        conn.execute(
            """INSERT INTO knowledge_entries
               (id, title, content, category, tags, source, confidence, created_ts, updated_ts)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (entry_id, title, content, category, json.dumps(tags_list), source,
             float(confidence), now, now),
        )
        conn.commit()
        conn.close()

        # Mirror in memory_core for semantic search
        save_memory(
            MemoryRecordInput(
                content=f"[KB:{category}] {title}\n{content}",
                tags=["knowledge_base", category] + tags_list,
                source=source or "knowledge_base",
            )
        )

        self._de.emit_event(
            self._de.build_event_envelope(
                "action.executed",
                session_id="knowledge_base",
                task_id="",
                actor="knowledge_base",
                payload={"action": "save_knowledge", "title": title, "category": category},
                risk_score=0.1,
                channel="system",
                source="knowledge_base",
            )
        )
        return f"Knowledge saved: '{title}' [{category}] (id={entry_id})"

    def tool_search_knowledge(self, query: str, category: str = "") -> str:
        """
        Search the knowledge base by query (semantic + SQL).
        Args:
            query: What to search for.
            category: Optional filter by category.
        """
        query = str(query or "").strip()
        if not query:
            return "Please provide a search query."

        results_text: list[dict] = []

        # SQL full-text search
        conn = self._connect()
        q = f"%{query.lower()}%"
        if category and category in _CATEGORIES:
            rows = conn.execute(
                """SELECT * FROM knowledge_entries WHERE category=?
                   AND (lower(title) LIKE ? OR lower(content) LIKE ? OR lower(tags) LIKE ?)
                   ORDER BY confidence DESC, updated_ts DESC LIMIT 8""",
                (category, q, q, q),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM knowledge_entries
                   WHERE lower(title) LIKE ? OR lower(content) LIKE ? OR lower(tags) LIKE ?
                   ORDER BY confidence DESC, updated_ts DESC LIMIT 8""",
                (q, q, q),
            ).fetchall()

        # Update access stats
        for r in rows:
            conn.execute(
                "UPDATE knowledge_entries SET access_count=access_count+1, last_accessed_ts=? WHERE id=?",
                (time.time(), r["id"]),
            )
        conn.commit()
        conn.close()

        for r in rows:
            results_text.append({
                "id": r["id"],
                "title": r["title"],
                "category": r["category"],
                "content": r["content"][:500],
                "confidence": r["confidence"],
            })

        # Semantic search via memory_core
        try:
            semantic = recall_memory(query, top_k=3)
            kb_semantic = [
                r for r in semantic
                if "knowledge_base" in (getattr(r, "tags", None) or [])
            ]
            for r in kb_semantic:
                content = getattr(r, "content", "")
                if not any(res["content"][:100] in content for res in results_text):
                    results_text.append({
                        "id": "semantic",
                        "title": content[:60] + "...",
                        "category": "semantic",
                        "content": content[:400],
                        "confidence": 0.8,
                    })
        except Exception:
            pass

        if not results_text:
            return f"No knowledge found for '{query}'. Save some knowledge first with tool_save_knowledge."

        lines = [f"{len(results_text)} knowledge entry/entries for '{query}':\n"]
        for i, r in enumerate(results_text[:6], 1):
            conf = f" [{r['confidence']:.0%}]" if r.get("confidence", 1.0) < 1.0 else ""
            lines.append(f"[{i}] {r['title']} ({r['category']}){conf}")
            lines.append(f"    {r['content'][:300].strip()}")
            lines.append("")
        return "\n".join(lines)

    def tool_get_entry(self, entry_id: str) -> str:
        """Retrieve a specific knowledge entry by ID."""
        entry_id = str(entry_id or "").strip()
        if not entry_id:
            return "entry_id is required."
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM knowledge_entries WHERE id=?", (entry_id,)
        ).fetchone()
        conn.close()
        if not row:
            return f"Entry {entry_id} not found."
        tags = json.loads(row["tags"] or "[]")
        return (
            f"ID: {row['id']}\n"
            f"Title: {row['title']}\n"
            f"Category: {row['category']}\n"
            f"Tags: {', '.join(tags) or 'none'}\n"
            f"Source: {row['source'] or 'unknown'}\n"
            f"Confidence: {row['confidence']:.0%}\n"
            f"Updated: {time.strftime('%Y-%m-%d %H:%M', time.localtime(row['updated_ts']))}\n"
            f"Accessed: {row['access_count']} time(s)\n\n"
            f"{row['content']}"
        )

    def tool_link_entries(self, from_id: str, to_id: str, link_type: str = "related") -> str:
        """Create a cross-reference link between two knowledge entries."""
        if not from_id or not to_id:
            return "Both from_id and to_id are required."
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO knowledge_links (from_id, to_id, link_type) VALUES (?,?,?)",
                (from_id, to_id, link_type),
            )
            conn.commit()
        except Exception as exc:
            conn.close()
            return f"Failed to create link: {exc}"
        conn.close()
        return f"Link created: {from_id} → {to_id} ({link_type})"

    def tool_list_by_category(self, category: str = "all") -> str:
        """List knowledge entries by category."""
        conn = self._connect()
        if category == "all":
            rows = conn.execute(
                "SELECT id, title, category, updated_ts FROM knowledge_entries ORDER BY category, updated_ts DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, category, updated_ts FROM knowledge_entries WHERE category=? ORDER BY updated_ts DESC",
                (category,),
            ).fetchall()
        conn.close()
        if not rows:
            return f"No knowledge entries{' in category ' + category if category != 'all' else ''}."
        lines = [f"{len(rows)} knowledge entry/entries:\n"]
        current_cat = ""
        for r in rows:
            if r["category"] != current_cat:
                current_cat = r["category"]
                lines.append(f"\n── {current_cat.upper()} ──")
            ts = time.strftime("%Y-%m-%d", time.localtime(r["updated_ts"]))
            lines.append(f"  [{r['id']}] {r['title']} ({ts})")
        return "\n".join(lines)

    def tool_knowledge_stats(self) -> str:
        """Return knowledge base statistics."""
        conn = self._connect()
        total = conn.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
        by_category = conn.execute(
            "SELECT category, COUNT(*) as n FROM knowledge_entries GROUP BY category ORDER BY n DESC"
        ).fetchall()
        most_accessed = conn.execute(
            "SELECT title, access_count FROM knowledge_entries ORDER BY access_count DESC LIMIT 5"
        ).fetchall()
        conn.close()

        lines = [
            f"Knowledge Base Stats:",
            f"  Total entries: {total}",
            "",
            "By category:",
        ]
        for r in by_category:
            lines.append(f"  {r['category']}: {r['n']}")
        if most_accessed:
            lines.append("\nMost accessed:")
            for r in most_accessed:
                lines.append(f"  {r['title']} ({r['access_count']} accesses)")
        return "\n".join(lines)

    def tool_delete_entry(self, entry_id: str) -> str:
        """Delete a knowledge entry by ID."""
        entry_id = str(entry_id or "").strip()
        if not entry_id:
            return "entry_id is required."
        conn = self._connect()
        cur = conn.execute("DELETE FROM knowledge_entries WHERE id=?", (entry_id,))
        conn.execute("DELETE FROM knowledge_links WHERE from_id=? OR to_id=?", (entry_id, entry_id))
        conn.commit()
        conn.close()
        if cur.rowcount == 0:
            return f"Entry {entry_id} not found."
        return f"Entry {entry_id} deleted."

    def get_tools(self) -> list[Callable]:
        return [
            self.tool_save_knowledge,
            self.tool_search_knowledge,
            self.tool_get_entry,
            self.tool_link_entries,
            self.tool_list_by_category,
            self.tool_knowledge_stats,
            self.tool_delete_entry,
        ]


def create_skill():
    return KnowledgeBaseSkill()
