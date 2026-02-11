import csv
import os
import sqlite3
import time
from collections import deque
from typing import Any
import json
import re
import uuid

from flask import Flask, jsonify, render_template, request
from google.adk import Runner
from google.genai import types
from werkzeug.utils import secure_filename

from agents import chief_of_staff
from config import get_config
from logging_config import get_logger, setup_logging
from monitor import get_system_metrics
from session_manager import get_session_service, resolve_session_id, resolve_user_id
from task_queue import TaskQueue


cfg = get_config()
setup_logging(cfg.log_dir)
logger = get_logger("desktop_server")

app = Flask(
    __name__,
    template_folder=os.path.join("desktop", "templates"),
    static_folder=os.path.join("desktop", "static"),
    static_url_path="/static",
)

session_service = get_session_service()
runner = Runner(
    app_name="VictorOS_Desktop",
    agent=chief_of_staff,
    session_service=session_service,
    auto_create_session=True,
)

queue = TaskQueue()
try:
    from invoice_pipeline import enqueue_invoice_job
except Exception:
    enqueue_invoice_job = None

SEND_FILE_RE = re.compile(r"<<SEND_FILE:\s*(.*?)>>", re.IGNORECASE)

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
    if not message:
        return jsonify({"ok": False, "error": "message is required"}), 400

    try:
        user_id = resolve_user_id("desktop", raw_user)
        session_id = resolve_session_id("desktop", raw_user)
        new_msg = types.Content(role="user", parts=[types.Part(text=message)])
        events = runner.run(user_id=user_id, session_id=session_id, new_message=new_msg)
        response = ""
        for event in events:
            text = _extract_text(event)
            if text:
                response += (("\n" if response else "") + text)
        if not response.strip():
            response = "I received your input, but no textual output was returned."
        return jsonify({"ok": True, "response": response.strip()})
    except Exception as e:
        logger.error(f"Desktop chat error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


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


if __name__ == "__main__":
    host = "127.0.0.1"
    port = int(os.getenv("VICTOR_DESKTOP_PORT", "7860"))
    logger.info(f"Victor Desktop preview server on http://{host}:{port}")
    # Keep debug off to avoid spawning duplicate worker/runtime side effects.
    app.run(host=host, port=port, debug=False)
