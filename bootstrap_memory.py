import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass

from memory_core import MemoryRecordInput, save_memory
from memory_policy import classify_memory_candidate, redact_sensitive


DB_PATH = "memory_store/victor_memory.db"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


@dataclass
class EventRow:
    session_id: str
    timestamp: float
    role: str
    author: str
    text: str


def _extract_text(parts):
    chunks = []
    for part in parts or []:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            chunks.append(part["text"])
    return "".join(chunks).strip()


def _clean_user_text(text: str) -> str:
    if not text:
        return ""
    # Remove injected context wrapper from Telegram runtime.
    idx = text.rfind("USER:")
    if idx >= 0:
        text = text[idx + len("USER:") :]
    text = text.strip()
    return text


def _is_noise(text: str) -> bool:
    if not text:
        return True
    lowered = text.lower()
    if lowered.startswith("=== memory fabric v3 ==="):
        return True
    if "no relevant entries." in lowered and "global_context" in lowered:
        return True
    if lowered.startswith("system note:"):
        return True
    return False


def load_events(session_id: str | None, limit: int) -> list[EventRow]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if session_id:
        cur.execute(
            """
            SELECT session_id, timestamp, event_data
            FROM events
            WHERE session_id=?
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            (session_id, limit),
        )
    else:
        cur.execute(
            """
            SELECT session_id, timestamp, event_data
            FROM events
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            (limit,),
        )

    rows = []
    for sid, ts, raw in cur.fetchall():
        try:
            event = json.loads(raw)
        except Exception:
            continue
        content = event.get("content") or {}
        role = (content.get("role") or "").strip()
        author = (event.get("author") or "").strip()
        text = _extract_text(content.get("parts") or [])
        if role == "user":
            text = _clean_user_text(text)
        text = text.strip()
        if not role or _is_noise(text):
            continue
        rows.append(EventRow(session_id=sid, timestamp=float(ts), role=role, author=author, text=text))

    conn.close()
    return rows


def _infer_agent(author: str) -> str | None:
    if not author:
        return None
    if author.lower() in {"chief_of_staff", "victor", "model"}:
        return "Chief_of_Staff"
    return author


def _to_record(candidate: dict, source: str, force_agent: str | None) -> MemoryRecordInput:
    redacted_text, sensitive_refs = redact_sensitive(candidate.get("text", ""))
    scope = candidate.get("scope", "global")
    agent_name = candidate.get("agent_name")
    if scope == "agent" and not agent_name:
        agent_name = force_agent or "Chief_of_Staff"
    return MemoryRecordInput(
        text=redacted_text,
        memory_type=candidate.get("memory_type", "other"),
        scope=scope,
        agent_name=agent_name,
        priority=int(candidate.get("priority", 3)),
        source=source,
        tags=candidate.get("tags", []),
        sensitive_refs=sensitive_refs,
        metadata_json={"bootstrap": True},
    )


def bootstrap(session_id: str | None, limit: int, dry_run: bool):
    events = load_events(session_id=session_id, limit=limit)
    saved = 0
    merged = 0
    rejected = 0
    seen_phrases = set()

    for idx, current in enumerate(events):
        if len(current.text) < 8:
            continue

        # Pair user event with next model output for better decision/task extraction.
        next_model = ""
        if current.role == "user":
            for nxt in events[idx + 1 : idx + 6]:
                if nxt.role == "model":
                    next_model = nxt.text
                    break

        candidates = classify_memory_candidate(
            current.text,
            next_model,
            {"tool_names": [], "delegated_agents": []},
        )
        if not candidates:
            continue

        force_agent = _infer_agent(current.author)
        for candidate in candidates:
            phrase = (candidate.get("text", "").strip(), candidate.get("memory_type", "other"), candidate.get("scope", "global"))
            if phrase in seen_phrases:
                continue
            seen_phrases.add(phrase)

            record = _to_record(candidate, source="bootstrap", force_agent=force_agent)
            if dry_run:
                print(
                    json.dumps(
                        {
                            "scope": record.scope,
                            "memory_type": record.memory_type,
                            "priority": record.priority,
                            "text": record.text[:140],
                        },
                        ensure_ascii=False,
                    )
                )
                continue

            result = save_memory(record)
            if result.get("ok") and result.get("status") == "inserted":
                saved += 1
            elif result.get("ok") and result.get("status") == "dedup_merged":
                merged += 1
            else:
                rejected += 1

    summary = {"saved": saved, "merged": merged, "rejected": rejected, "events_scanned": len(events)}
    print(json.dumps(summary, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="One-time Memory Fabric bootstrap from session event history.")
    parser.add_argument("--session-id", default=None, help="Specific session_id to ingest. Default: all sessions.")
    parser.add_argument("--limit", type=int, default=500, help="Max events to scan in ascending time order.")
    parser.add_argument("--dry-run", action="store_true", help="Preview candidate records without writing.")
    args = parser.parse_args()
    bootstrap(session_id=args.session_id, limit=max(1, args.limit), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
