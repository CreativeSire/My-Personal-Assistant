"""
Victor Doctor CLI (Phase 3 full).

Commands:
  python doctor.py status [--json]
  python doctor.py health [--json]
  python doctor.py security [--deep] [--json]
  python doctor.py channels [--json]
  python doctor.py workflows [--json]
  python doctor.py logs [--lines N] [--json]
  python doctor.py diagnostics [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import urllib.error
import urllib.request
from typing import Any

from config import get_config
from security_audit import run_security_audit


cfg = get_config()
API_BASE = os.getenv("VICTOR_TASK_API_URL", "http://127.0.0.1:8787").rstrip("/")


def _api_get(path: str) -> tuple[int, dict[str, Any]]:
    url = f"{API_BASE}{path}"
    req = urllib.request.Request(url=url, method="GET")
    api_key = os.getenv("VICTOR_API_KEY", "").strip()
    if api_key:
        req.add_header("X-API-Key", api_key)
    try:
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return int(resp.status), json.loads(body or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body or "{}")
        except Exception:
            payload = {"error": body}
        return int(exc.code), payload
    except Exception as exc:
        return 0, {"error": str(exc)}


def _print_or_json(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
        return
    for k, v in payload.items():
        print(f"- {k}: {v}")


def _channels_snapshot() -> dict[str, Any]:
    desktop_port = int(cfg.dashboard_port)
    status_api, _ = _api_get("/v1/capabilities")
    status_router, _ = _api_get("/v1/metrics/router")
    telegram_pid_file = os.path.join(cfg.base_dir, "data", "telegram_server.pid")
    telegram_running_hint = False
    if os.path.exists(telegram_pid_file):
        try:
            pid = int(open(telegram_pid_file, "r", encoding="utf-8").read().strip())
            telegram_running_hint = pid > 0
        except Exception:
            telegram_running_hint = False
    return {
        "api_status_code": status_api,
        "router_status_code": status_router,
        "telegram_running_hint": telegram_running_hint,
        "whatsapp_enabled": bool(cfg.whatsapp_enabled),
        "desktop_port": desktop_port,
    }


def _workflow_snapshot() -> dict[str, Any]:
    state_path = os.path.join(cfg.base_dir, "memory_store", "workflow_runtime_state.json")
    workflows_dir = os.path.join(cfg.base_dir, "workflows")
    loaded = [f for f in os.listdir(workflows_dir) if f.endswith(".json")] if os.path.isdir(workflows_dir) else []
    state = {}
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {}
    return {
        "workflow_definitions": len(loaded),
        "runtime_state_file": state_path,
        "last_runs": len((state.get("last_runs") or {})) if isinstance(state, dict) else 0,
        "recent_tokens": len((state.get("recent_tokens") or [])) if isinstance(state, dict) else 0,
    }


def _logs_snapshot(lines: int = 60) -> dict[str, Any]:
    csv_path = os.path.join(cfg.data_dir, "system_logs.csv")
    tg_err = os.path.join(cfg.base_dir, "telegram_server.err.log")
    out: dict[str, Any] = {"system_logs_csv_exists": os.path.exists(csv_path), "telegram_err_log_exists": os.path.exists(tg_err)}
    if os.path.exists(csv_path):
        try:
            with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
                rows = f.readlines()
            out["system_logs_tail_count"] = min(lines, len(rows))
            out["system_logs_tail_preview"] = "".join(rows[-min(lines, len(rows)):])[-2000:]
        except Exception as exc:
            out["system_logs_error"] = str(exc)
    if os.path.exists(tg_err):
        try:
            with open(tg_err, "r", encoding="utf-8", errors="replace") as f:
                rows = f.readlines()
            out["telegram_err_tail_count"] = min(lines, len(rows))
            out["telegram_err_tail_preview"] = "".join(rows[-min(lines, len(rows)):])[-2000:]
        except Exception as exc:
            out["telegram_err_error"] = str(exc)
    return out


def _queue_snapshot() -> dict[str, Any]:
    db_path = os.path.join(cfg.base_dir, "memory_store", "victor_tasks.db")
    if not os.path.exists(db_path):
        return {"task_db_exists": False}
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        pending = int(cur.execute("SELECT COUNT(*) FROM tasks WHERE status='pending'").fetchone()[0])
        running = int(cur.execute("SELECT COUNT(*) FROM tasks WHERE status='running'").fetchone()[0])
        failed = int(cur.execute("SELECT COUNT(*) FROM tasks WHERE status='failed'").fetchone()[0])
        completed = int(cur.execute("SELECT COUNT(*) FROM tasks WHERE status='completed'").fetchone()[0])
        return {
            "task_db_exists": True,
            "pending": pending,
            "running": running,
            "failed": failed,
            "completed": completed,
        }
    finally:
        conn.close()


def doctor_status(as_json: bool = False) -> int:
    status_cap, cap = _api_get("/v1/capabilities")
    status_tasks, tasks = _api_get("/v1/tasks/latest?limit=5")
    payload = {
        "api_base": API_BASE,
        "capabilities_status": status_cap,
        "tasks_latest_status": status_tasks,
        "capabilities_ok": bool((cap or {}).get("ok")),
        "latest_tasks_count": len((tasks or {}).get("tasks") or []),
    }
    _print_or_json(payload, as_json)
    return 0 if status_cap == 200 else 1


def doctor_health(as_json: bool = False) -> int:
    status_router, router = _api_get("/v1/metrics/router")
    status_tools, tools = _api_get("/v1/metrics/tools")
    payload = {
        "router_status": status_router,
        "tools_status": status_tools,
        "routed_total": ((router or {}).get("metrics") or {}).get("total_routed", 0),
        "tools_total": len((tools or {}).get("tools") or []),
    }
    _print_or_json(payload, as_json)
    return 0 if status_router == 200 and status_tools == 200 else 1


def doctor_security(*, deep: bool, as_json: bool) -> int:
    audit = run_security_audit(deep=deep, write_report=True)
    if as_json:
        print(json.dumps(audit, indent=2))
    else:
        counts = audit.get("counts", {})
        print("- mode:", audit.get("mode"))
        print("- checks:", counts.get("total", 0))
        print("- failed:", counts.get("failed", 0))
        print("- critical_failed:", counts.get("critical_failed", 0))
    return 0 if int(audit.get("counts", {}).get("critical_failed", 0)) == 0 else 1


def doctor_channels(as_json: bool = False) -> int:
    payload = _channels_snapshot()
    _print_or_json(payload, as_json)
    return 0 if int(payload.get("api_status_code", 0)) == 200 else 1


def doctor_workflows(as_json: bool = False) -> int:
    payload = _workflow_snapshot()
    _print_or_json(payload, as_json)
    return 0


def doctor_logs(lines: int = 60, as_json: bool = False) -> int:
    payload = _logs_snapshot(lines=lines)
    _print_or_json(payload, as_json)
    return 0


def doctor_diagnostics(as_json: bool = False) -> int:
    # Unified diagnostics: channels + workflows + queue + security summary.
    channels = _channels_snapshot()
    workflows = _workflow_snapshot()
    queue = _queue_snapshot()
    security = run_security_audit(deep=False, write_report=True)
    payload = {
        "channels": channels,
        "workflows": workflows,
        "queue": queue,
        "security_summary": security.get("counts", {}),
    }
    _print_or_json(payload, as_json)
    critical = int((security.get("counts") or {}).get("critical_failed", 0))
    return 0 if critical == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Victor Doctor")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status")
    p_status.add_argument("--json", action="store_true")

    p_health = sub.add_parser("health")
    p_health.add_argument("--json", action="store_true")

    p_sec = sub.add_parser("security")
    p_sec.add_argument("--deep", action="store_true")
    p_sec.add_argument("--json", action="store_true")

    p_channels = sub.add_parser("channels")
    p_channels.add_argument("--json", action="store_true")

    p_workflows = sub.add_parser("workflows")
    p_workflows.add_argument("--json", action="store_true")

    p_logs = sub.add_parser("logs")
    p_logs.add_argument("--lines", type=int, default=60)
    p_logs.add_argument("--json", action="store_true")

    p_diag = sub.add_parser("diagnostics")
    p_diag.add_argument("--json", action="store_true")

    args = parser.parse_args()
    if args.cmd == "status":
        return doctor_status(as_json=bool(args.json))
    if args.cmd == "health":
        return doctor_health(as_json=bool(args.json))
    if args.cmd == "security":
        return doctor_security(deep=bool(args.deep), as_json=bool(args.json))
    if args.cmd == "channels":
        return doctor_channels(as_json=bool(args.json))
    if args.cmd == "workflows":
        return doctor_workflows(as_json=bool(args.json))
    if args.cmd == "logs":
        return doctor_logs(lines=max(1, int(args.lines)), as_json=bool(args.json))
    if args.cmd == "diagnostics":
        return doctor_diagnostics(as_json=bool(args.json))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
