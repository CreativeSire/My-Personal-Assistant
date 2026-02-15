import csv
import os
import sys
import sqlite3
import subprocess
import time
from collections import deque
from typing import Any
import json
import re
import uuid
from datetime import datetime, timezone

from flask import Flask, Response, abort, jsonify, render_template, request, send_file, stream_with_context
from werkzeug.utils import secure_filename

from config import get_config
from logging_config import get_logger, new_correlation_id, setup_logging
from monitor import get_system_metrics
from session_manager import get_session_service, resolve_session_id, resolve_user_id
from task_queue import TaskQueue
from pathlib import Path

ROOT_DIR = str(Path(__file__).resolve().parent.parent)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

try:
    from data_engine import get_data_engine
    from victor_core import PolicyEngine, PolicyInput, Telemetry
except Exception:
    get_data_engine = None  # type: ignore[assignment]
    PolicyEngine = None  # type: ignore[assignment]
    PolicyInput = None  # type: ignore[assignment]
    Telemetry = None  # type: ignore[assignment]

try:
    from google.adk import Runner
    from google.genai import types
    _adk_import_error: Exception | None = None
except Exception as exc:  # pragma: no cover - environment specific
    Runner = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]
    _adk_import_error = exc

try:
    from agents import chief_of_staff
    _agents_import_error: Exception | None = None
except Exception as exc:  # pragma: no cover - environment specific
    chief_of_staff = None  # type: ignore[assignment]
    _agents_import_error = exc


cfg = get_config()
setup_logging(cfg.log_dir)
logger = get_logger("desktop_server")

app = Flask(
    __name__,
    template_folder=os.path.join("desktop", "templates"),
    static_folder=os.path.join("desktop", "static"),
    static_url_path="/static",
)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True

session_service = None
runner = None
_runner_init_error = ""


def _init_chat_runtime() -> None:
    """Initialize chat dependencies lazily so UI can boot even if ADK stack is unavailable."""
    global session_service, runner, _runner_init_error
    if runner is not None:
        return
    if Runner is None or types is None:
        _runner_init_error = f"ADK import failed: {_adk_import_error}"
        logger.error(_runner_init_error)
        return
    if chief_of_staff is None:
        _runner_init_error = f"Agent import failed: {_agents_import_error}"
        logger.error(_runner_init_error)
        return
    try:
        session_service = get_session_service()
        runner = Runner(
            app_name="VictorOS_Desktop",
            agent=chief_of_staff,
            session_service=session_service,
            auto_create_session=True,
        )
        _runner_init_error = ""
    except Exception as exc:  # pragma: no cover - environment specific
        _runner_init_error = f"Runner initialization failed: {exc}"
        logger.error(_runner_init_error)


_init_chat_runtime()

queue = TaskQueue()
try:
    from invoice_pipeline import enqueue_invoice_job
except Exception:
    enqueue_invoice_job = None

SEND_FILE_RE = re.compile(r"<<SEND_FILE:\s*(.*?)>>", re.IGNORECASE)
KNOWN_AGENTS = {
    "Chief_of_Staff",
    "Planner_Agent",
    "Dev_Agent",
    "Research_Agent",
    "Data_Scientist",
    "Script_Agent",
    "Academic_Writer",
}

try:
    from proactive_engine import ProactiveEngine

    _gate_probe = ProactiveEngine()
except Exception:
    _gate_probe = None

_STATE_DIR = os.path.join(cfg.base_dir, "workspace", "desktop_state")
os.makedirs(_STATE_DIR, exist_ok=True)
_MISSION_BOARD_PATH = os.path.join(_STATE_DIR, "mission_board.json")
_RESOURCE_GOV_PATH = os.path.join(_STATE_DIR, "resource_governor.json")
_TIMELINES_PATH = os.path.join(_STATE_DIR, "timelines.json")
_DESKTOP_SETTINGS_PATH = os.path.join(_STATE_DIR, "desktop_settings.json")
_RUNTIME_FLAGS_PATH = os.path.join(_STATE_DIR, "runtime_flags.json")
_WORKFLOWS_DIR = os.path.join(cfg.base_dir, "workflows")
_data_engine = get_data_engine() if get_data_engine else None
_telemetry = Telemetry() if Telemetry else None
_policy = PolicyEngine() if PolicyEngine else None
_global_kill_switch = {"enabled": False}

def _extract_text(event: Any) -> str:
    chunks: list[str] = []
    if hasattr(event, "text") and isinstance(event.text, str) and event.text.strip():
        chunks.append(event.text.strip())

    content = getattr(event, "content", None)
    if content and getattr(content, "parts", None):
        for part in content.parts:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())

    candidates = getattr(event, "candidates", None)
    if candidates:
        for candidate in candidates:
            c_content = getattr(candidate, "content", None)
            if c_content and getattr(c_content, "parts", None):
                for part in c_content.parts:
                    text = getattr(part, "text", None)
                    if isinstance(text, str) and text.strip():
                        chunks.append(text.strip())

    dedup: list[str] = []
    seen = set()
    for item in chunks:
        if item not in seen:
            seen.add(item)
            dedup.append(item)
    return "\n".join(dedup).strip()


def _extract_agent_author(event: Any) -> str:
    author = getattr(event, "author", None)
    if isinstance(author, str) and author.strip() in KNOWN_AGENTS:
        return author.strip()
    return ""


def _strip_markers(text: str) -> tuple[str, list[str]]:
    if not text:
        return "", []
    markers: list[str] = []
    for m in SEND_FILE_RE.finditer(text):
        markers.append(m.group(1).strip())
    clean = SEND_FILE_RE.sub("", text).strip()
    return clean, markers


def _extract_text_from_event_data(node: Any, seen: set[int] | None = None, from_text_field: bool = False) -> list[str]:
    if seen is None:
        seen = set()
    if node is None:
        return []
    nid = id(node)
    if nid in seen:
        return []
    seen.add(nid)

    chunks: list[str] = []
    if isinstance(node, str):
        s = node.strip()
        if s and from_text_field:
            chunks.append(s)
        return chunks

    if isinstance(node, dict):
        for key, value in node.items():
            key_lower = str(key).lower()
            is_text_key = key_lower in {"text", "output_text", "response_text", "final_text"}
            chunks.extend(_extract_text_from_event_data(value, seen, from_text_field=is_text_key))
        return chunks

    if isinstance(node, (list, tuple, set)):
        for item in node:
            chunks.extend(_extract_text_from_event_data(item, seen, from_text_field=from_text_field))
        return chunks

    return chunks


def _first_user_message_for_session(conn: sqlite3.Connection, session_id: str) -> str:
    row = conn.execute(
        """
        SELECT event_data FROM events
        WHERE session_id=?
        ORDER BY timestamp ASC
        LIMIT 120
        """,
        (session_id,),
    ).fetchall()
    for r in row:
        raw = r["event_data"]
        try:
            data = json.loads(raw)
        except Exception:
            continue
        role = str(data.get("role", data.get("author", ""))).lower()
        if role != "user":
            continue
        chunks = _extract_text_from_event_data(data)
        if chunks:
            return " ".join(chunks).strip()
    return ""


def _session_messages(session_id: str, limit: int = 300) -> list[dict[str, Any]]:
    conn = sqlite3.connect(cfg.memory_db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT timestamp, event_data
            FROM events
            WHERE session_id=?
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
        messages: list[dict[str, Any]] = []
        for row in rows:
            try:
                data = json.loads(row["event_data"])
            except Exception:
                continue
            role = str(data.get("role", data.get("author", "assistant"))).lower()
            if role not in {"user", "assistant"}:
                role = "assistant"
            chunks = _extract_text_from_event_data(data)
            text = " ".join(chunks).strip()
            if not text:
                continue
            clean, artifacts = _strip_markers(text)
            messages.append(
                {
                    "role": role,
                    "text": clean or text,
                    "timestamp": float(row["timestamp"] or 0),
                    "artifacts": artifacts,
                }
            )
        return messages
    finally:
        conn.close()


def _list_sessions(limit: int = 120) -> list[dict[str, Any]]:
    conn = sqlite3.connect(cfg.memory_db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT s.id AS session_id, s.update_time, s.create_time,
                   (SELECT COUNT(*) FROM events e WHERE e.session_id = s.id) AS event_count
            FROM sessions s
            ORDER BY s.update_time DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        sessions: list[dict[str, Any]] = []
        for row in rows:
            session_id = str(row["session_id"])
            title_seed = _first_user_message_for_session(conn, session_id)
            title = (title_seed[:30] + "...") if len(title_seed) > 30 else (title_seed or session_id)
            sessions.append(
                {
                    "session_id": session_id,
                    "title": title,
                    "update_time": float(row["update_time"] or row["create_time"] or 0),
                    "create_time": float(row["create_time"] or 0),
                    "event_count": int(row["event_count"] or 0),
                }
            )
        return sessions
    finally:
        conn.close()


def _query_recent_tasks(limit: int = 30) -> list[dict[str, Any]]:
    db_path = os.path.join(cfg.base_dir, "memory_store", "victor_tasks.db")
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, task_type, status, channel, user_id, progress, retries,
                   payload, result,
                   created_at, updated_at, started_at, completed_at, error
            FROM tasks
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def _resolve_runtime_path(path_text: str) -> str:
    normalized = str(path_text or "").strip().replace("\\", "/")
    if not normalized:
        return ""
    if normalized.startswith("victor_os/"):
        suffix = normalized[len("victor_os/"):]
        return os.path.join(cfg.base_dir, suffix.replace("/", os.sep))
    if os.path.isabs(normalized):
        return normalized
    return os.path.join(cfg.base_dir, normalized.replace("/", os.sep))

def _extract_zip_path_from_result(result_text: str) -> str:
    text = str(result_text or "")
    m = SEND_FILE_RE.search(text)
    if not m:
        return ""
    return _resolve_runtime_path(m.group(1).strip())

def _load_summary_from_task(task: dict[str, Any]) -> dict[str, Any] | None:
    zip_path = _extract_zip_path_from_result(task.get("result") or "")
    if not zip_path:
        return None
    root_dir = os.path.dirname(zip_path)
    summary_path = os.path.join(root_dir, "summary.json")
    if not os.path.exists(summary_path):
        return None
    try:
        with open(summary_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _build_truth_rows(limit_tasks: int = 20, limit_rows: int = 300) -> list[dict[str, Any]]:
    tasks = _query_recent_tasks(limit=limit_tasks)
    rows: list[dict[str, Any]] = []
    for task in tasks:
        if task.get("task_type") != "invoice_job":
            continue
        summary = _load_summary_from_task(task)
        if not summary:
            continue
        outputs = ((summary.get("outputs") or {}).get("items") or [])
        for item in outputs:
            evidence = item.get("evidence") or {}
            fields = evidence.get("fields") or {}
            warning_codes = evidence.get("warning_codes") or []
            status = str(item.get("status") or "").lower()
            confidence = float(fields.get("confidence_score") or 0.0)
            row = {
                "task_id": task.get("id"),
                "job_id": summary.get("job_id"),
                "status": status,
                "warning": bool(warning_codes) or status in {"review", "failed"},
                "reason": item.get("reason") or "",
                "file_name": item.get("input_file") or "",
                "receiver": fields.get("receiver_name") or "",
                "location": fields.get("receiver_location") or "",
                "invoice_number": fields.get("invoice_number") or "",
                "delivery_date": fields.get("delivery_date") or "",
                "confidence_score": confidence,
                "warning_codes": warning_codes,
                "raw": {
                    "item": item,
                    "summary_counts": summary.get("counts") or {},
                },
            }
            rows.append(row)
            if len(rows) >= limit_rows:
                return rows
    return rows


def _latest_completed_invoice_task() -> dict[str, Any] | None:
    tasks = _query_recent_tasks(limit=80)
    for t in tasks:
        if str(t.get("task_type")) != "invoice_job":
            continue
        if str(t.get("status")) != "completed":
            continue
        result = str(t.get("result") or "")
        zip_path = _extract_zip_path_from_result(result)
        if not zip_path or not os.path.exists(zip_path):
            continue
        summary = _load_summary_from_task(t) or {}
        return {
            "task_id": t.get("id"),
            "zip_path": zip_path,
            "output_dir": os.path.dirname(zip_path),
            "summary": summary,
            "created_at": t.get("created_at"),
            "completed_at": t.get("completed_at"),
            "result": result,
        }
    return None


def _safe_write_json(path: str, payload: Any):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _safe_read_json(path: str, fallback: Any):
    if not os.path.exists(path):
        return fallback
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return fallback


def _default_desktop_settings() -> dict[str, Any]:
    return {
        "monitor_clipboard": False,
        "timeline_mode": True,
        "proactive_paused": False,
    }


def _default_runtime_flags() -> dict[str, Any]:
    return {
        "paused": False,
        "updated_at": time.time(),
    }


def _default_mission_board() -> list[dict[str, Any]]:
    return [
        {"id": "deploy_v2", "title": "Deploy v2.0", "done": False},
        {"id": "invoice_pipeline", "title": "Fix Invoice Pipeline", "done": False},
        {"id": "memory_fabric", "title": "Harden Memory Fabric", "done": False},
    ]


def _sse_pack(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _normalize_plan_step_status(step: dict[str, Any]) -> str:
    raw = str(step.get("status") or "pending").strip().lower()
    if raw in {"pending", "in_progress", "completed", "failed", "skipped"}:
        return raw
    if raw in {"running", "started", "active"}:
        return "in_progress"
    if raw in {"done", "success"}:
        return "completed"
    return "pending"


def _save_timeline_entry(entry: dict[str, Any], limit: int = 100) -> None:
    items = _safe_read_json(_TIMELINES_PATH, [])
    if not isinstance(items, list):
        items = []
    items.append(entry)
    if len(items) > limit:
        items = items[-limit:]
    _safe_write_json(_TIMELINES_PATH, items)


def _stream_chat_response(
    message: str,
    raw_user: str,
    requested_session_id: str,
    sleeping_agents: list[str],
):
    correlation_id = new_correlation_id()
    started = time.time()
    if runner is None or types is None:
        _init_chat_runtime()
    if runner is None or types is None:
        reason = _runner_init_error or "Desktop chat runtime is unavailable."
        yield _sse_pack("error", {"message": reason, "code": "runtime_unavailable"})
        return

    user_id = resolve_user_id("desktop", raw_user)
    session_id = requested_session_id or resolve_session_id("desktop", raw_user)
    if sleeping_agents:
        message = (
            "[SYSTEM GOVERNOR NOTE] These agents are set to SLEEP. Avoid delegating to them unless absolutely required: "
            + ", ".join(sleeping_agents)
            + "\n\nUSER:\n"
            + message
        )
    yield _sse_pack(
        "meta",
        {
            "correlation_id": correlation_id,
            "session_id": session_id,
            "started_at": started,
        },
    )

    response = ""
    event_text_seen: set[str] = set()
    active_agents: list[str] = []
    timeline: list[dict[str, Any]] = []
    open_span: dict[str, Any] | None = None
    artifacts: list[str] = []

    try:
        new_msg = types.Content(role="user", parts=[types.Part(text=message)])
        events = runner.run(user_id=user_id, session_id=session_id, new_message=new_msg)
        for event in events:
            now = time.time()
            author = _extract_agent_author(event) or "Chief_of_Staff"
            if author not in active_agents:
                active_agents.append(author)

            if open_span is None or open_span.get("agent") != author:
                if open_span is not None:
                    open_span["end_ts"] = now
                    open_span["duration_ms"] = round((now - float(open_span["start_ts"])) * 1000, 2)
                    timeline.append(open_span)
                    yield _sse_pack(
                        "agent",
                        {
                            "agent": open_span.get("agent"),
                            "phase": "end",
                            "t_ms": round((now - started) * 1000, 2),
                        },
                    )
                open_span = {"agent": author, "start_ts": now}
                yield _sse_pack(
                    "agent",
                    {
                        "agent": author,
                        "phase": "start",
                        "t_ms": round((now - started) * 1000, 2),
                    },
                )

            text = _extract_text(event)
            if text and text not in event_text_seen:
                event_text_seen.add(text)
                response += (("\n" if response else "") + text)
                yield _sse_pack(
                    "token",
                    {
                        "text_chunk": text,
                        "agent": author,
                        "t_ms": round((now - started) * 1000, 2),
                    },
                )
                _, marker_paths = _strip_markers(text)
                for p in marker_paths:
                    artifacts.append(p)
                    yield _sse_pack("artifact", {"path": p})

        ended = time.time()
        if open_span is not None:
            open_span["end_ts"] = ended
            open_span["duration_ms"] = round((ended - float(open_span["start_ts"])) * 1000, 2)
            timeline.append(open_span)
            yield _sse_pack(
                "agent",
                {
                    "agent": open_span.get("agent"),
                    "phase": "end",
                    "t_ms": round((ended - started) * 1000, 2),
                },
            )

        if not response.strip():
            response = "I received your input, but no textual output was returned."

        clean, _ = _strip_markers(response.strip())
        active_agent = active_agents[-1] if active_agents else "Chief_of_Staff"
        timeline_payload = [
            {
                "agent": t.get("agent"),
                "start_ts": t.get("start_ts"),
                "end_ts": t.get("end_ts"),
                "duration_ms": t.get("duration_ms"),
            }
            for t in timeline
        ]
        timeline_entry = {
            "correlation_id": correlation_id,
            "session_id": session_id,
            "user_id": raw_user,
            "started_at": started,
            "ended_at": ended,
            "timeline": timeline_payload,
            "active_agent": active_agent,
        }
        _save_timeline_entry(timeline_entry)
        yield _sse_pack(
            "done",
            {
                "final_text": clean or response.strip(),
                "session_id": session_id,
                "active_agent": active_agent,
                "delegated_agents": [a for a in active_agents if a != "Chief_of_Staff"],
                "timeline": timeline_payload,
                "artifacts": artifacts,
                "duration_ms": round((ended - started) * 1000, 2),
            },
        )
    except Exception as exc:
        logger.error(f"Desktop stream chat error: {exc}")
        yield _sse_pack("error", {"message": str(exc), "code": "chat_stream_failed"})


def _default_resource_governor() -> dict[str, bool]:
    return {
        "Chief_of_Staff": True,
        "Planner_Agent": True,
        "Dev_Agent": True,
        "Research_Agent": True,
        "Data_Scientist": True,
        "Script_Agent": True,
        "Academic_Writer": True,
    }


def _list_artifacts(limit: int = 160) -> list[dict[str, Any]]:
    roots = [
        os.path.join(cfg.base_dir, "workspace", "jobs"),
        cfg.workspace_dir,
    ]
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for root in roots:
        if not os.path.exists(root):
            continue
        for base, _dirs, files in os.walk(root):
            for name in files:
                full = os.path.join(base, name)
                if full in seen:
                    continue
                seen.add(full)
                rel = os.path.relpath(full, cfg.base_dir).replace("\\", "/")
                ext = os.path.splitext(name)[1].lower()
                kind = "document"
                preview = False
                snippet = ""
                if ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                    kind = "image"
                    preview = True
                elif ext in {".py", ".js", ".ts", ".json", ".md", ".txt"}:
                    kind = "code"
                    try:
                        with open(full, "r", encoding="utf-8", errors="replace") as f:
                            snippet = "".join(f.readlines()[:6]).strip()
                    except Exception:
                        snippet = ""
                elif ext == ".pdf":
                    kind = "document"
                else:
                    kind = "document"
                items.append(
                    {
                        "name": name,
                        "path": rel,
                        "kind": kind,
                        "ext": ext,
                        "size": os.path.getsize(full),
                        "mtime": os.path.getmtime(full),
                        "preview": preview,
                        "snippet": snippet,
                    }
                )
    items.sort(key=lambda x: float(x.get("mtime") or 0), reverse=True)
    return items[:limit]


def _tail_csv_logs(limit: int = 40) -> list[dict[str, Any]]:
    log_path = cfg.log_file
    if not os.path.exists(log_path):
        return []
    lines: deque[dict[str, Any]] = deque(maxlen=limit)
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            lines.append(row)
    return list(lines)


def _emit_platform_event(
    event_type: str,
    *,
    session_id: str = "",
    task_id: str = "",
    actor: str = "desktop_server",
    payload: dict[str, Any] | None = None,
    risk_score: float = 0.0,
) -> None:
    if not _telemetry:
        return
    try:
        _telemetry.emit(
            event_type,
            session_id=session_id,
            task_id=task_id,
            actor=actor,
            payload=payload or {},
            risk_score=risk_score,
            channel="desktop",
            source="desktop_server",
        )
    except Exception as exc:
        logger.debug(f"telemetry emit failed: {exc}")


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/api/health")
def api_health():
    return jsonify(
        {
            "ok": True,
            "service": "victor-desktop-preview",
            "timestamp": time.time(),
            "telegram_running_hint": True,
        }
    )


@app.get("/api/metrics")
def api_metrics():
    try:
        return jsonify({"ok": True, "metrics": get_system_metrics()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "metrics": {}}), 500


@app.get("/api/tasks")
def api_tasks():
    try:
        tasks = _query_recent_tasks(limit=50)
        return jsonify({"ok": True, "tasks": tasks})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "tasks": []}), 500


@app.get("/api/logs")
def api_logs():
    try:
        return jsonify({"ok": True, "rows": _tail_csv_logs(limit=60)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "rows": []}), 500


@app.post("/api/chat")
def api_chat():
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message") or "").strip()
    raw_user = str(payload.get("user_id") or "desktop_ceejay")
    requested_session_id = str(payload.get("session_id") or "").strip()
    sleeping_agents = payload.get("sleeping_agents") or []
    sleeping_agents = [str(x) for x in sleeping_agents if str(x)]
    if not message:
        return jsonify({"ok": False, "error": "message is required"}), 400
    if _global_kill_switch["enabled"]:
        return jsonify({"ok": False, "error": "global kill switch enabled"}), 423

    if runner is None or types is None:
        _init_chat_runtime()
    if runner is None or types is None:
        reason = _runner_init_error or "Desktop chat runtime is unavailable."
        return jsonify({"ok": False, "error": reason, "mode": "ui_only"}), 503

    try:
        user_id = resolve_user_id("desktop", raw_user)
        session_id = requested_session_id or resolve_session_id("desktop", raw_user)
        _emit_platform_event(
            "intent.received",
            session_id=session_id,
            actor=user_id,
            payload={"intent": message, "channel": "desktop"},
            risk_score=0.2,
        )
        if sleeping_agents:
            message = (
                "[SYSTEM GOVERNOR NOTE] These agents are set to SLEEP. Avoid delegating to them unless absolutely required: "
                + ", ".join(sleeping_agents)
                + "\n\nUSER:\n"
                + message
            )
        new_msg = types.Content(role="user", parts=[types.Part(text=message)])
        events = runner.run(user_id=user_id, session_id=session_id, new_message=new_msg)
        response = ""
        active_agents: list[str] = []
        for event in events:
            text = _extract_text(event)
            if text:
                response += (("\n" if response else "") + text)
            author = _extract_agent_author(event)
            if author and author not in active_agents:
                active_agents.append(author)
        if not response.strip():
            response = "I received your input, but no textual output was returned."
        clean, artifacts = _strip_markers(response.strip())
        active_agent = active_agents[-1] if active_agents else "Chief_of_Staff"
        _emit_platform_event(
            "task.completed",
            session_id=session_id,
            actor=active_agent,
            payload={
                "delegated_agents": [a for a in active_agents if a != "Chief_of_Staff"],
                "artifact_count": len(artifacts),
            },
            risk_score=0.1,
        )
        return jsonify(
            {
                "ok": True,
                "response": clean or response.strip(),
                "session_id": session_id,
                "active_agent": active_agent,
                "delegated_agents": [a for a in active_agents if a != "Chief_of_Staff"],
                "artifacts": artifacts,
            }
        )
    except Exception as e:
        logger.error(f"Desktop chat error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/chat/stream")
def api_chat_stream():
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message") or "").strip()
    raw_user = str(payload.get("user_id") or "desktop_ceejay")
    requested_session_id = str(payload.get("session_id") or "").strip()
    sleeping_agents = payload.get("sleeping_agents") or []
    sleeping_agents = [str(x) for x in sleeping_agents if str(x)]
    if not message:
        return jsonify({"ok": False, "error": "message is required"}), 400

    response = Response(
        stream_with_context(
            _stream_chat_response(
                message=message,
                raw_user=raw_user,
                requested_session_id=requested_session_id,
                sleeping_agents=sleeping_agents,
            )
        ),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@app.get("/api/sessions")
def api_sessions():
    try:
        return jsonify({"ok": True, "sessions": _list_sessions(limit=150)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "sessions": []}), 500


@app.get("/api/sessions/<session_id>/messages")
def api_session_messages(session_id: str):
    try:
        messages = _session_messages(session_id, limit=400)
        return jsonify({"ok": True, "session_id": session_id, "messages": messages})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "messages": []}), 500


@app.post("/api/gateway/check")
def api_gateway_check():
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message") or "").strip()
    if not message:
        return jsonify({"ok": True, "allowed": True, "mode": "empty"})
    try:
        if _gate_probe is None:
            return jsonify({"ok": True, "allowed": True, "mode": "probe_unavailable"})
        allowed = bool(_gate_probe._can_send(message))  # noqa: SLF001
        return jsonify({"ok": True, "allowed": allowed, "mode": "proactive_can_send"})
    except Exception as e:
        return jsonify({"ok": True, "allowed": True, "mode": f"fail_open:{e.__class__.__name__}"})


@app.post("/api/system/reboot")
def api_system_reboot():
    # Safe no-op placeholder for desktop preview. Returns acknowledged reboot intent.
    return jsonify({"ok": True, "status": "acknowledged", "message": "System reboot command accepted (preview mode)."})


@app.get("/api/invoice/latest_output")
def api_invoice_latest_output():
    latest = _latest_completed_invoice_task()
    if not latest:
        return jsonify({"ok": True, "available": False})
    return jsonify({"ok": True, "available": True, **latest})


@app.post("/api/invoice/open_output")
def api_invoice_open_output():
    payload = request.get_json(silent=True) or {}
    raw_path = str(payload.get("path") or "").strip()
    if not raw_path:
        return jsonify({"ok": False, "error": "path required"}), 400
    path = _resolve_runtime_path(raw_path)
    if not os.path.exists(path):
        return jsonify({"ok": False, "error": "path not found"}), 404
    try:
        subprocess.Popen(["explorer", os.path.normpath(path)], shell=False)  # noqa: S603
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/invoice/download_zip")
def api_invoice_download_zip():
    latest = _latest_completed_invoice_task()
    if not latest:
        return abort(404)
    return send_file(latest["zip_path"], as_attachment=True, download_name=os.path.basename(latest["zip_path"]))


@app.get("/api/invoice/export_audit_csv")
def api_invoice_export_audit_csv():
    rows = _build_truth_rows(limit_tasks=80, limit_rows=2000)
    os.makedirs(_STATE_DIR, exist_ok=True)
    out = os.path.join(_STATE_DIR, "invoice_audit_export.csv")
    with open(out, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["status", "file_name", "receiver", "location", "invoice_number", "delivery_date", "confidence_score", "reason"])
        for r in rows:
            writer.writerow([
                r.get("status"),
                r.get("file_name"),
                r.get("receiver"),
                r.get("location"),
                r.get("invoice_number"),
                r.get("delivery_date"),
                r.get("confidence_score"),
                r.get("reason"),
            ])
    return send_file(out, as_attachment=True, download_name="invoice_audit_export.csv")


@app.get("/api/mission_board")
def api_mission_board():
    items = _safe_read_json(_MISSION_BOARD_PATH, _default_mission_board())
    return jsonify({"ok": True, "items": items})


@app.post("/api/mission_board")
def api_mission_board_save():
    payload = request.get_json(silent=True) or {}
    items = payload.get("items") or []
    if not isinstance(items, list):
        return jsonify({"ok": False, "error": "items must be list"}), 400
    _safe_write_json(_MISSION_BOARD_PATH, items)
    return jsonify({"ok": True})


@app.get("/api/settings/desktop")
def api_desktop_settings():
    settings = _safe_read_json(_DESKTOP_SETTINGS_PATH, _default_desktop_settings())
    if not isinstance(settings, dict):
        settings = _default_desktop_settings()
    return jsonify({"ok": True, "settings": settings})


@app.post("/api/settings/desktop")
def api_desktop_settings_save():
    payload = request.get_json(silent=True) or {}
    incoming = payload.get("settings") or payload
    if not isinstance(incoming, dict):
        return jsonify({"ok": False, "error": "settings must be object"}), 400
    clean = _default_desktop_settings()
    for k in clean.keys():
        if k in incoming:
            clean[k] = bool(incoming[k]) if isinstance(clean[k], bool) else incoming[k]
    _safe_write_json(_DESKTOP_SETTINGS_PATH, clean)
    return jsonify({"ok": True, "settings": clean})


@app.get("/api/runtime_flags")
def api_runtime_flags():
    flags = _safe_read_json(_RUNTIME_FLAGS_PATH, _default_runtime_flags())
    if not isinstance(flags, dict):
        flags = _default_runtime_flags()
    return jsonify({"ok": True, "flags": flags})


@app.post("/api/runtime_flags")
def api_runtime_flags_save():
    payload = request.get_json(silent=True) or {}
    flags = payload.get("flags") or payload
    if not isinstance(flags, dict):
        return jsonify({"ok": False, "error": "flags must be object"}), 400
    clean = _default_runtime_flags()
    clean["paused"] = bool(flags.get("paused", clean["paused"]))
    clean["updated_at"] = time.time()
    _safe_write_json(_RUNTIME_FLAGS_PATH, clean)
    return jsonify({"ok": True, "flags": clean})


@app.get("/api/strategy/goals")
def api_strategy_goals():
    status = str(request.args.get("status") or "active").strip().lower()
    limit = max(1, min(200, int(request.args.get("limit", 50))))
    db_path = cfg.goals_db_path
    if not os.path.exists(db_path):
        return jsonify({"ok": True, "goals": []})
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if status and status != "all":
            rows = conn.execute(
                """
                SELECT id, title, status, priority, progress, target_date, updated_at, source, tags
                FROM goals
                WHERE status = ?
                ORDER BY priority ASC, updated_at DESC
                LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, title, status, priority, progress, target_date, updated_at, source, tags
                FROM goals
                ORDER BY priority ASC, updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        goals = []
        for row in rows:
            tags_raw = row["tags"] or "[]"
            try:
                tags = json.loads(tags_raw) if isinstance(tags_raw, str) else list(tags_raw)
            except Exception:
                tags = []
            goals.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "status": row["status"],
                    "priority": int(row["priority"] or 3),
                    "progress": int(row["progress"] or 0),
                    "target_date": row["target_date"],
                    "updated_at": row["updated_at"],
                    "source": row["source"] or "unknown",
                    "tags": tags,
                }
            )
        return jsonify({"ok": True, "goals": goals})
    finally:
        conn.close()


@app.get("/api/strategy/plans")
def api_strategy_plans():
    statuses = str(request.args.get("status") or "active,ready").strip().lower()
    status_items = [s.strip() for s in statuses.split(",") if s.strip()]
    limit = max(1, min(200, int(request.args.get("limit", 50))))
    db_path = os.path.join(cfg.base_dir, "memory_store", "victor_plans.db")
    if not os.path.exists(db_path):
        return jsonify({"ok": True, "plans": []})
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if status_items:
            placeholders = ",".join(["?"] * len(status_items))
            rows = conn.execute(
                f"""
                SELECT id, goal, status, steps, updated_at
                FROM plans
                WHERE status IN ({placeholders})
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (*status_items, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, goal, status, steps, updated_at
                FROM plans
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        plans = []
        for row in rows:
            steps = []
            try:
                raw_steps = json.loads(row["steps"] or "[]")
                if isinstance(raw_steps, list):
                    for step in raw_steps:
                        if isinstance(step, dict):
                            step["status"] = _normalize_plan_step_status(step)
                            steps.append(step)
            except Exception:
                steps = []
            plans.append(
                {
                    "plan_id": row["id"],
                    "goal": row["goal"],
                    "status": row["status"],
                    "steps": steps,
                    "updated_at": row["updated_at"],
                }
            )
        return jsonify({"ok": True, "plans": plans})
    finally:
        conn.close()


@app.get("/api/strategy/plan/<plan_id>")
def api_strategy_plan(plan_id: str):
    db_path = os.path.join(cfg.base_dir, "memory_store", "victor_plans.db")
    if not os.path.exists(db_path):
        return jsonify({"ok": False, "error": "plan store unavailable"}), 404
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT id, goal, status, steps, created_at, updated_at, completed_at
            FROM plans WHERE id = ?
            """,
            (plan_id,),
        ).fetchone()
        if row is None:
            return jsonify({"ok": False, "error": "plan not found"}), 404
        steps = json.loads(row["steps"] or "[]")
        if isinstance(steps, list):
            for step in steps:
                if isinstance(step, dict):
                    step["status"] = _normalize_plan_step_status(step)
        else:
            steps = []
        return jsonify(
            {
                "ok": True,
                "plan": {
                    "plan_id": row["id"],
                    "goal": row["goal"],
                    "status": row["status"],
                    "steps": steps,
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "completed_at": row["completed_at"],
                },
            }
        )
    finally:
        conn.close()


@app.get("/api/strategy/overview")
def api_strategy_overview():
    goals_resp = api_strategy_goals()
    plans_resp = api_strategy_plans()
    goals_json = goals_resp.get_json(silent=True) if hasattr(goals_resp, "get_json") else {"goals": []}
    plans_json = plans_resp.get_json(silent=True) if hasattr(plans_resp, "get_json") else {"plans": []}
    timeline_items = _safe_read_json(_TIMELINES_PATH, [])
    if not isinstance(timeline_items, list):
        timeline_items = []
    latest_timeline = timeline_items[-1] if timeline_items else {}
    return jsonify(
        {
            "ok": True,
            "goals": goals_json.get("goals", []),
            "plans": plans_json.get("plans", []),
            "live_overlay": latest_timeline,
        }
    )


@app.get("/api/timelines")
def api_timelines():
    limit = max(1, min(500, int(request.args.get("limit", 50))))
    items = _safe_read_json(_TIMELINES_PATH, [])
    if not isinstance(items, list):
        items = []
    return jsonify({"ok": True, "items": items[-limit:]})


@app.get("/api/resource_governor")
def api_resource_governor():
    state = _safe_read_json(_RESOURCE_GOV_PATH, _default_resource_governor())
    return jsonify({"ok": True, "state": state})


@app.post("/api/resource_governor")
def api_resource_governor_save():
    payload = request.get_json(silent=True) or {}
    state = payload.get("state") or {}
    if not isinstance(state, dict):
        return jsonify({"ok": False, "error": "state must be object"}), 400
    clean = _default_resource_governor()
    for k in clean.keys():
        if k in state:
            clean[k] = bool(state[k])
    _safe_write_json(_RESOURCE_GOV_PATH, clean)
    return jsonify({"ok": True, "state": clean})


@app.get("/api/artifacts")
def api_artifacts():
    kind = str(request.args.get("kind") or "all").lower()
    items = _list_artifacts(limit=200)
    if kind in {"images", "image"}:
        items = [x for x in items if x.get("kind") == "image"]
    elif kind in {"documents", "document"}:
        items = [x for x in items if x.get("kind") == "document"]
    elif kind in {"code"}:
        items = [x for x in items if x.get("kind") == "code"]
    return jsonify({"ok": True, "items": items})


@app.get("/api/artifacts/file")
def api_artifact_file():
    rel = str(request.args.get("path") or "").strip()
    if not rel:
        return abort(400)
    full = _resolve_runtime_path(rel)
    base = os.path.abspath(cfg.base_dir)
    full_abs = os.path.abspath(full)
    if not full_abs.startswith(base):
        return abort(403)
    if not os.path.exists(full_abs):
        return abort(404)
    return send_file(full_abs)


@app.post("/api/invoice/enqueue")
def api_invoice_enqueue():
    payload = request.get_json(silent=True) or {}
    input_path = str(payload.get("input_path") or "").strip()
    user_id = str(payload.get("user_id") or "desktop_ceejay")
    if not input_path:
        return jsonify({"ok": False, "error": "input_path is required"}), 400
    if not os.path.exists(input_path):
        return jsonify({"ok": False, "error": f"input_path not found: {input_path}"}), 400
    if not cfg.invoice_job_enabled:
        return jsonify({"ok": False, "error": "invoice jobs are disabled"}), 400
    if enqueue_invoice_job is None:
        return jsonify({"ok": False, "error": "invoice pipeline unavailable"}), 500

    try:
        task_id = enqueue_invoice_job(input_path=input_path, channel="desktop", user_id=user_id)
        _emit_platform_event(
            "plan.generated",
            task_id=task_id,
            actor="desktop_server",
            payload={"task_type": "invoice_job", "input_path": input_path},
            risk_score=0.5,
        )
        if _data_engine:
            _data_engine.upsert_task_run(
                task_id=task_id,
                state="pending",
                channel="desktop",
                user_id=user_id,
                payload={"input_path": input_path, "task_type": "invoice_job"},
                metadata={"origin": "desktop_enqueue"},
            )
        return jsonify({"ok": True, "task_id": task_id})
    except Exception as e:
        logger.error(f"Desktop invoice enqueue error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.post("/api/invoice/upload")
def api_invoice_upload():
    user_id = str(request.form.get("user_id") or "desktop_ceejay")
    if not cfg.invoice_job_enabled:
        return jsonify({"ok": False, "error": "invoice jobs are disabled"}), 400
    if enqueue_invoice_job is None:
        return jsonify({"ok": False, "error": "invoice pipeline unavailable"}), 500

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "file is required"}), 400

    original_name = secure_filename(file.filename)
    ext = os.path.splitext(original_name)[1].lower().lstrip(".")
    allowed = set(cfg.invoice_allowed_exts) | {"zip"}
    if ext not in allowed:
        return jsonify({"ok": False, "error": f"unsupported file type: .{ext}"}), 400

    upload_dir = os.path.join(cfg.workspace_dir, "desktop_uploads")
    os.makedirs(upload_dir, exist_ok=True)
    save_name = f"{int(time.time())}_{uuid.uuid4().hex[:8]}_{original_name}"
    save_path = os.path.join(upload_dir, save_name)
    try:
        file.save(save_path)
        task_id = enqueue_invoice_job(input_path=save_path, channel="desktop", user_id=user_id)
        return jsonify({"ok": True, "task_id": task_id, "stored_path": save_path, "filename": original_name})
    except Exception as e:
        logger.error(f"Desktop upload enqueue error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.get("/api/invoice/grid")
def api_invoice_grid():
    try:
        rows = _build_truth_rows(limit_tasks=25, limit_rows=400)
        return jsonify({"ok": True, "rows": rows})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "rows": []}), 500


@app.get("/api/workflow/actions")
def api_workflow_actions():
    actions = [
        {"id": "search_web", "label": "Google Search", "type": "action", "params": ["query"]},
        {"id": "python_exec", "label": "Execute Python", "type": "action", "params": ["code"]},
        {"id": "save_memory", "label": "Save Memory", "type": "action", "params": ["text", "tags"]},
        {"id": "send_email", "label": "Send Email Alert", "type": "notify", "params": ["subject", "body"]},
        {"id": "invoice_job", "label": "Invoice Batch", "type": "action", "params": ["input_path"]},
        {"id": "condition", "label": "Condition Branch", "type": "condition", "params": ["expression"]},
    ]
    skills_dir = os.path.join(cfg.base_dir, "skills")
    if os.path.isdir(skills_dir):
        for fname in os.listdir(skills_dir):
            if fname.endswith(".py") and fname not in {"__init__.py"}:
                name = fname[:-3]
                actions.append(
                    {
                        "id": f"skill::{name}",
                        "label": f"Skill: {name}",
                        "type": "action",
                        "params": [],
                    }
                )
    return jsonify({"ok": True, "actions": actions})


@app.get("/api/workflow/definitions")
def api_workflow_definitions():
    os.makedirs(_WORKFLOWS_DIR, exist_ok=True)
    items = []
    for name in os.listdir(_WORKFLOWS_DIR):
        if not name.endswith(".json"):
            continue
        full = os.path.join(_WORKFLOWS_DIR, name)
        items.append(
            {
                "name": name,
                "mtime": os.path.getmtime(full),
                "size": os.path.getsize(full),
            }
        )
    items.sort(key=lambda x: float(x.get("mtime") or 0), reverse=True)
    return jsonify({"ok": True, "definitions": items})


@app.get("/api/workflow/definition/<name>")
def api_workflow_definition(name: str):
    safe_name = secure_filename(name)
    if not safe_name.endswith(".json"):
        safe_name += ".json"
    full = os.path.join(_WORKFLOWS_DIR, safe_name)
    if not os.path.exists(full):
        return jsonify({"ok": False, "error": "workflow not found"}), 404
    with open(full, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return jsonify({"ok": True, "name": safe_name, "workflow": payload})


def _validate_workflow_payload(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["payload must be object"]
    if "name" in payload and not str(payload.get("name") or "").strip():
        errors.append("name must be non-empty")
    if "nodes" in payload and not isinstance(payload.get("nodes"), list):
        errors.append("nodes must be list")
    if "edges" in payload and not isinstance(payload.get("edges"), list):
        errors.append("edges must be list")
    workflow = payload.get("workflow")
    if workflow is not None and not isinstance(workflow, dict):
        errors.append("workflow must be object")
    return errors


@app.post("/api/workflow/validate")
def api_workflow_validate():
    payload = request.get_json(silent=True) or {}
    errors = _validate_workflow_payload(payload)
    return jsonify({"ok": len(errors) == 0, "errors": errors})


@app.post("/api/workflow/save")
def api_workflow_save():
    payload = request.get_json(silent=True) or {}
    errors = _validate_workflow_payload(payload)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    name = secure_filename(str(payload.get("name") or "workflow_new"))
    if not name.endswith(".json"):
        name += ".json"
    os.makedirs(_WORKFLOWS_DIR, exist_ok=True)
    full = os.path.join(_WORKFLOWS_DIR, name)
    data = payload.get("workflow")
    if not isinstance(data, dict):
        data = {
            "name": name.replace(".json", ""),
            "nodes": payload.get("nodes", []),
            "edges": payload.get("edges", []),
            "metadata": {
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "source": "desktop_workflow_builder",
            },
        }
    _safe_write_json(full, data)
    return jsonify({"ok": True, "name": name})


@app.post("/api/workflow/delete")
def api_workflow_delete():
    payload = request.get_json(silent=True) or {}
    name = secure_filename(str(payload.get("name") or ""))
    if not name:
        return jsonify({"ok": False, "error": "name required"}), 400
    if not name.endswith(".json"):
        name += ".json"
    full = os.path.join(_WORKFLOWS_DIR, name)
    if not os.path.exists(full):
        return jsonify({"ok": False, "error": "workflow not found"}), 404
    archive_dir = os.path.join(_WORKFLOWS_DIR, "_archive")
    os.makedirs(archive_dir, exist_ok=True)
    archived = os.path.join(archive_dir, f"{int(time.time())}_{name}")
    os.replace(full, archived)
    return jsonify({"ok": True, "archived_as": os.path.relpath(archived, cfg.base_dir).replace('\\', '/')})


@app.post("/api/feedback")
def api_feedback():
    payload = request.get_json(silent=True) or {}
    reason_code = str(payload.get("reason_code") or "").strip()
    if not reason_code:
        return jsonify({"ok": False, "error": "reason_code is required"}), 400
    if _data_engine is None:
        return jsonify({"ok": False, "error": "data engine unavailable"}), 503
    correction_id = _data_engine.record_correction(
        session_id=str(payload.get("session_id") or "").strip(),
        task_id=str(payload.get("task_id") or "").strip(),
        channel="desktop",
        actor=str(payload.get("actor") or "desktop_user"),
        reason_code=reason_code,
        rejected_output=str(payload.get("rejected_output") or ""),
        preferred_output=str(payload.get("preferred_output") or ""),
        metadata={"source": "desktop_api"},
    )
    features = _data_engine.recalc_core_features()
    return jsonify({"ok": True, "correction_id": correction_id, "features": features})


@app.post("/api/control/kill_switch")
def api_control_kill_switch():
    payload = request.get_json(silent=True) or {}
    enabled = bool(payload.get("enabled"))
    _global_kill_switch["enabled"] = enabled
    _emit_platform_event(
        "action.executed",
        actor="desktop_operator",
        payload={"kill_switch_enabled": enabled},
        risk_score=1.0,
    )
    return jsonify({"ok": True, "enabled": enabled})


@app.post("/api/control/safe_mode")
def api_control_safe_mode():
    payload = request.get_json(silent=True) or {}
    session_id = str(payload.get("session_id") or "").strip()
    enabled = bool(payload.get("enabled"))
    if not session_id:
        return jsonify({"ok": False, "error": "session_id is required"}), 400
    if _policy is None:
        return jsonify({"ok": False, "error": "policy engine unavailable"}), 503
    _policy.set_safe_mode(session_id, enabled)
    _emit_platform_event(
        "action.executed",
        session_id=session_id,
        actor="desktop_operator",
        payload={"safe_mode": enabled},
        risk_score=0.9,
    )
    return jsonify({"ok": True, "session_id": session_id, "safe_mode": enabled})


@app.post("/api/policy/evaluate")
def api_policy_evaluate():
    payload = request.get_json(silent=True) or {}
    if _policy is None or PolicyInput is None:
        return jsonify({"ok": False, "error": "policy engine unavailable"}), 503
    req = PolicyInput(
        action=str(payload.get("action") or "").strip().lower(),
        target=str(payload.get("target") or "").strip(),
        channel="desktop",
        actor=str(payload.get("actor") or "desktop_user"),
        metadata=dict(payload.get("metadata") or {}),
    )
    session_id = str(payload.get("session_id") or "").strip()
    verdict = _policy.evaluate(req, session_id=session_id)
    if _data_engine:
        _data_engine.record_policy_decision(
            action=req.action,
            policy_tier=str(verdict.tier.value),
            allowed=verdict.allowed,
            reason=verdict.reason,
            session_id=session_id,
            actor=req.actor,
            metadata={"target": req.target, "channel": "desktop"},
        )
    event_type = "action.executed" if verdict.allowed else "action.blocked"
    _emit_platform_event(
        event_type,
        session_id=session_id,
        actor=req.actor,
        payload={
            "action": req.action,
            "target": req.target,
            "tier": verdict.tier.value,
            "reason": verdict.reason,
        },
        risk_score=1.0 if not verdict.allowed else 0.4,
    )
    return jsonify(
        {
            "ok": True,
            "allowed": verdict.allowed,
            "tier": verdict.tier.value,
            "requires_approval": verdict.requires_approval,
            "reason": verdict.reason,
        }
    )


if __name__ == "__main__":
    host = "127.0.0.1"
    port = int(os.getenv("VICTOR_DESKTOP_PORT", "7860"))
    logger.info(f"Victor Desktop preview server on http://{host}:{port}")
    # Keep debug off to avoid spawning duplicate worker/runtime side effects.
    app.run(host=host, port=port, debug=False, use_reloader=False)
