import re
from typing import Any


SENSITIVE_PATTERNS = [
    ("api_key", re.compile(r"\b(?:api[_-]?key|token|secret|password|passwd)\b\s*[:=]\s*([A-Za-z0-9_\-]{8,})", re.IGNORECASE)),
    ("bearer", re.compile(r"\bBearer\s+([A-Za-z0-9\-._~+/]+=*)", re.IGNORECASE)),
    ("email", re.compile(r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")),
    ("phone", re.compile(r"\b(\+?\d[\d\-\s]{7,}\d)\b")),
]

PREFERENCE_RE = re.compile(r"\b(i prefer|my preference|i like|always use|prefer to)\b", re.IGNORECASE)
TASK_RE = re.compile(r"\b(todo|to do|task|deadline|due|by\s+\d{4}-\d{2}-\d{2}|remind me|follow up)\b", re.IGNORECASE)
DECISION_RE = re.compile(r"\b(we decided|decision|we will|let us|let's|go with|chosen default)\b", re.IGNORECASE)
CONSTRAINT_RE = re.compile(r"\b(always|never|must|do not|don't|forbidden|strictly|mandatory|cannot)\b", re.IGNORECASE)
PROJECT_RE = re.compile(r"\b(project|codename|milestone|release|roadmap|sprint)\b", re.IGNORECASE)


def contains_sensitive(text: str) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    body = text or ""
    for kind, pattern in SENSITIVE_PATTERNS:
        for match in pattern.finditer(body):
            whole = match.group(0)
            secret_part = match.group(1) if match.lastindex else whole
            matches.append(
                {
                    "type": kind,
                    "start": match.start(),
                    "end": match.end(),
                    "value": secret_part,
                    "raw": whole,
                }
            )
    return {"has_sensitive": bool(matches), "matches": matches}


def redact_sensitive(text: str) -> tuple[str, list[dict[str, Any]]]:
    payload = text or ""
    findings = contains_sensitive(payload)["matches"]
    if not findings:
        return payload, []

    redacted = payload
    refs: list[dict[str, Any]] = []
    for idx, item in enumerate(findings, start=1):
        placeholder = f"[REDACTED_{item['type'].upper()}_{idx}]"
        if item["raw"] in redacted:
            redacted = redacted.replace(item["raw"], placeholder)
        refs.append(
            {
                "placeholder": placeholder,
                "type": item["type"],
                "value": item["value"],
            }
        )
    return redacted, refs


def _make_record(text: str, memory_type: str, priority: int, scope: str = "global", agent_name: str | None = None, tags: list[str] | None = None):
    return {
        "text": text.strip(),
        "memory_type": memory_type,
        "priority": priority,
        "scope": scope,
        "agent_name": agent_name,
        "tags": tags or [],
        "source": "telegram",
    }


def classify_memory_candidate(user_text: str, model_output: str = "", tool_context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    tool_context = tool_context or {}
    candidates: list[dict[str, Any]] = []

    user_body = (user_text or "").strip()
    model_body = (model_output or "").strip()

    if not user_body:
        return candidates

    if PREFERENCE_RE.search(user_body):
        candidates.append(_make_record(user_body, "preference", 4, "global", None, ["preference"]))
    if TASK_RE.search(user_body):
        candidates.append(_make_record(user_body, "task", 5, "global", None, ["task"]))
    if DECISION_RE.search(user_body):
        candidates.append(_make_record(user_body, "decision", 4, "global", None, ["decision"]))
    if CONSTRAINT_RE.search(user_body):
        candidates.append(_make_record(user_body, "constraint", 5, "global", None, ["constraint"]))
    if PROJECT_RE.search(user_body):
        candidates.append(_make_record(user_body, "project_fact", 4, "global", None, ["project"]))

    if model_body and ("i will" in model_body.lower() or "completed" in model_body.lower()):
        candidates.append(_make_record(model_body[:700], "status", 3, "agent", "Chief_of_Staff", ["status"]))

    tool_names = tool_context.get("tool_names") or []
    if tool_names:
        tool_text = "Tools used: " + ", ".join(tool_names[:8])
        candidates.append(_make_record(tool_text, "artifact", 3, "agent", "Chief_of_Staff", ["tooling"]))

    # Dedup by tuple key
    seen = set()
    unique: list[dict[str, Any]] = []
    for item in candidates:
        key = (item["text"], item["memory_type"], item["scope"], item.get("agent_name"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique
