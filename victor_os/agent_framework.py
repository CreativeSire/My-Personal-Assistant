"""
Victor Agent Framework

Merged module:
1) VictorTools (local machine actions)
2) Victor Core API (/v1/* control plane)
"""

from __future__ import annotations

import logging
import os
import hmac
import subprocess
import sys
import time
import uuid
import threading
import glob
import shutil
import urllib.request
import json
from collections import deque
from typing import Any

from flask import Flask, jsonify, request
from dotenv import load_dotenv

from data_engine import get_data_engine
from victor_core import PolicyEngine, PolicyInput, Telemetry
from victor_core.contracts import TaskIntent


# Ensure victor_os modules are importable.
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_VICTOR_OS_DIR = os.path.join(_BASE_DIR, "victor_os")
if _VICTOR_OS_DIR not in sys.path:
    sys.path.insert(0, _VICTOR_OS_DIR)
load_dotenv(os.path.join(_VICTOR_OS_DIR, ".env"))

from config import get_config
from security_audit import apply_baseline, rollback_baseline, run_security_audit
from security_scopes import (
    check_scope_allowed,
    load_scope_map_from_env,
    required_scope_for_route,
    scope_decision_payload,
)

try:
    from task_queue import TaskQueue
except Exception:
    TaskQueue = None  # type: ignore[assignment]

from agent_orchestrator import Executor, Planner, PlannerExecutorReviewer, Reviewer
from local_inference_router import LocalInferenceRouter
from tool_registry_v2 import ToolRegistryV2
from training_pipeline import TrainingPipeline
from macro_store import MacroStore
from style_store import StyleProfile, StyleStore
from device_registry import get_device_registry


logger = logging.getLogger("agent_framework")
app = Flask(__name__)
engine = get_data_engine()
telemetry = Telemetry()
policy_engine = PolicyEngine()
queue = TaskQueue() if TaskQueue else None
_KILL_SWITCH = {"enabled": False}
_API_KEY = os.getenv("VICTOR_API_KEY", "").strip()
_RATE_LIMIT_PER_MIN = max(1, int(os.getenv("VICTOR_API_RATE_LIMIT_PER_MIN", "120")))
_RATE_LIMIT_TASKS_WRITE_PER_MIN = max(1, int(os.getenv("VICTOR_API_RATE_LIMIT_TASKS_WRITE_PER_MIN", "60")))
_RATE_LIMIT_CONTROL_WRITE_PER_MIN = max(1, int(os.getenv("VICTOR_API_RATE_LIMIT_CONTROL_WRITE_PER_MIN", "20")))
_RATE_LIMIT_TRAINING_WRITE_PER_MIN = max(1, int(os.getenv("VICTOR_API_RATE_LIMIT_TRAINING_WRITE_PER_MIN", "30")))
_RATE_LIMIT_AUDIT_READ_PER_MIN = max(1, int(os.getenv("VICTOR_API_RATE_LIMIT_AUDIT_READ_PER_MIN", "60")))
_RATE_LIMIT_WINDOW_SEC = max(1, int(os.getenv("VICTOR_API_RATE_LIMIT_WINDOW_SEC", "60")))
_API_IP_ALLOWLIST = {
    x.strip()
    for x in str(os.getenv("VICTOR_API_IP_ALLOWLIST", "")).replace(";", ",").split(",")
    if x.strip()
}
_rate_limit_lock = threading.RLock()
_rate_limit_by_key: dict[str, deque[float]] = {}
_ROUTER_METRICS: dict[str, Any] = {
    "total_routed": 0,
    "local_routed": 0,
    "remote_routed": 0,
    "fallback_count": 0,
    "low_confidence_count": 0,
    "confidence_hist": {"0.0-0.49": 0, "0.50-0.69": 0, "0.70-0.84": 0, "0.85-1.00": 0},
    "intent_class_counts": {},
    "low_risk_total": 0,
    "low_risk_local": 0,
    "updated_at": time.time(),
}
tool_registry = ToolRegistryV2()
tool_registry.seed_default_tools()
router = LocalInferenceRouter(threshold=float(os.getenv("LOCAL_ROUTER_THRESHOLD", "0.7")))
training = TrainingPipeline()
cfg = get_config()
_AUDIT_REGISTRY_PATH = os.path.join(_VICTOR_OS_DIR, "memory_store", "model_registry.json")
_VISION = {"enabled": False, "started_at": 0.0}
_VISION_INDICATOR = os.path.join(_VICTOR_OS_DIR, "workspace", "vision_mode_active.txt")
_macros = MacroStore(os.path.join(_VICTOR_OS_DIR, "memory_store", "macros.json"))
_styles = StyleStore(os.path.join(_VICTOR_OS_DIR, "memory_store", "style_profiles.json"))
_device_registry = get_device_registry()

def _load_api_keys() -> dict[str, str]:
    parsed: dict[str, str] = {}
    many = str(os.getenv("VICTOR_API_KEYS", "")).strip()
    if many:
        items = [x.strip() for x in many.replace("\n", ",").replace(";", ",").split(",") if x.strip()]
        for idx, item in enumerate(items):
            if ":" in item:
                key_id, key_value = item.split(":", 1)
                key_id = key_id.strip() or f"key{idx + 1}"
                key_value = key_value.strip()
            else:
                key_id = f"key{idx + 1}"
                key_value = item
            if key_value:
                parsed[key_id] = key_value
    if _API_KEY:
        parsed.setdefault("primary", _API_KEY)
    return parsed


_API_KEYS = _load_api_keys()
_API_KEY_SCOPE_MAP = load_scope_map_from_env()

if cfg.app_env != "dev" and not _API_KEYS:
    raise RuntimeError(
        "VICTOR_API_KEY or VICTOR_API_KEYS must be set when APP_ENV is not 'dev'. "
        "Set APP_ENV=dev for local open-mode testing only."
    )


def _tool_exec_filesystem_list(inputs: dict[str, Any]) -> Any:
    return VictorTools.list_files(str(inputs.get("path") or "."))


def _tool_exec_filesystem_read(inputs: dict[str, Any]) -> Any:
    path = str(inputs.get("path") or "").strip()
    if not path:
        return "path is required"
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read(4000)
    except Exception as exc:
        return str(exc)


def _tool_exec_run_terminal(inputs: dict[str, Any]) -> Any:
    return VictorTools.run_terminal_command(str(inputs.get("command") or "echo no_command"))


def _tool_exec_telegram_send(inputs: dict[str, Any]) -> Any:
    # API worker does not send outbound telegram directly; we log placeholder action.
    return f"queued_message:{str(inputs.get('text') or '')[:120]}"


def _tool_exec_telegram_send_file(inputs: dict[str, Any]) -> Any:
    path = str(inputs.get("path") or "").strip()
    caption = str(inputs.get("caption") or "").strip()
    if not path:
        return {"ok": False, "error": "path is required"}
    return {"ok": True, "queued_file": path, "caption": caption}


def _tool_exec_filesystem_write_text(inputs: dict[str, Any]) -> Any:
    path = str(inputs.get("path") or "").strip()
    text = str(inputs.get("text") or "")
    if not path:
        return {"ok": False, "error": "path is required"}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    previous = None
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                previous = f.read()
        except Exception:
            previous = None
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return {"ok": True, "path": path, "bytes": len(text.encode("utf-8")), "previous_exists": previous is not None}


def _tool_exec_filesystem_move(inputs: dict[str, Any]) -> Any:
    src = str(inputs.get("src") or "").strip()
    dst = str(inputs.get("dst") or "").strip()
    if not src or not dst:
        return {"ok": False, "error": "src and dst are required"}
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    shutil.move(src, dst)
    return {"ok": True, "src": src, "dst": dst}


def _tool_exec_filesystem_copy(inputs: dict[str, Any]) -> Any:
    src = str(inputs.get("src") or "").strip()
    dst = str(inputs.get("dst") or "").strip()
    if not src or not dst:
        return {"ok": False, "error": "src and dst are required"}
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    shutil.copy2(src, dst)
    return {"ok": True, "src": src, "dst": dst}


def _tool_exec_filesystem_delete(inputs: dict[str, Any]) -> Any:
    path = str(inputs.get("path") or "").strip()
    if not path:
        return {"ok": False, "error": "path is required"}
    if os.path.isdir(path):
        shutil.rmtree(path)
        return {"ok": True, "deleted_dir": path}
    if os.path.exists(path):
        os.remove(path)
        return {"ok": True, "deleted_file": path}
    return {"ok": False, "error": "path_not_found"}


def _tool_exec_filesystem_mkdir(inputs: dict[str, Any]) -> Any:
    path = str(inputs.get("path") or "").strip()
    if not path:
        return {"ok": False, "error": "path is required"}
    os.makedirs(path, exist_ok=True)
    return {"ok": True, "path": path}


def _tool_exec_filesystem_glob(inputs: dict[str, Any]) -> Any:
    pattern = str(inputs.get("pattern") or "*").strip()
    root = str(inputs.get("root") or ".").strip()
    matches = glob.glob(os.path.join(root, pattern))
    return {"ok": True, "count": len(matches), "matches": matches[:200]}


def _tool_exec_browser_open_url(inputs: dict[str, Any]) -> Any:
    url = str(inputs.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "url is required"}
    return {"ok": True, "opened_url": url, "mode": "simulated"}


def _tool_exec_browser_read_dom_text(inputs: dict[str, Any]) -> Any:
    url = str(inputs.get("url") or "").strip()
    selector = str(inputs.get("selector") or "body").strip()
    if not url:
        return {"ok": False, "error": "url is required"}
    try:
        with urllib.request.urlopen(url, timeout=4.0) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "selector": selector, "text_preview": text[:1200], "chars": len(text)}


def _tool_exec_browser_capture_screenshot(inputs: dict[str, Any]) -> Any:
    url = str(inputs.get("url") or "").strip()
    out_path = str(inputs.get("path") or "").strip()
    if not out_path:
        out_path = os.path.join(_VICTOR_OS_DIR, "workspace", "browser_capture.txt")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"Simulated screenshot capture for {url or 'about:blank'} at {time.time()}\n")
    return {"ok": True, "path": out_path, "mode": "simulated"}


def _tool_exec_browser_click_selector(inputs: dict[str, Any]) -> Any:
    url = str(inputs.get("url") or "").strip()
    selector = str(inputs.get("selector") or "").strip()
    return {"ok": True, "url": url, "selector": selector, "mode": "simulated_click"}


def _tool_exec_process_list(inputs: dict[str, Any]) -> Any:
    try:
        output = subprocess.check_output("tasklist", shell=True, text=True, stderr=subprocess.STDOUT)
        lines = output.splitlines()
        return {"ok": True, "count_estimate": max(0, len(lines) - 3), "preview": lines[:30]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _tool_exec_process_launch(inputs: dict[str, Any]) -> Any:
    app_name = str(inputs.get("app") or "").strip()
    if not app_name:
        return {"ok": False, "error": "app is required"}
    return {"ok": True, "result": VictorTools.open_application(app_name)}


def _tool_exec_process_terminate(inputs: dict[str, Any]) -> Any:
    pid = str(inputs.get("pid") or "").strip()
    if not pid:
        return {"ok": False, "error": "pid is required"}
    try:
        subprocess.check_call(f"taskkill /PID {pid} /F", shell=True)
        return {"ok": True, "pid": pid}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _tool_exec_process_focus_window(inputs: dict[str, Any]) -> Any:
    title = str(inputs.get("title") or "").strip()
    return {"ok": True, "focused_title": title, "mode": "simulated_focus"}


def _tool_exec_invoice_get_artifacts(inputs: dict[str, Any]) -> Any:
    task_id = str(inputs.get("task_id") or "").strip()
    return {"ok": True, "task_id": task_id, "artifacts": _collect_task_artifacts(task_id)}


def _tool_exec_vision_capture_screen(inputs: dict[str, Any]) -> Any:
    if not _VISION["enabled"]:
        return {"ok": False, "error": "vision_mode_disabled"}
    out_path = str(inputs.get("path") or "").strip()
    if not out_path:
        out_path = os.path.join(_VICTOR_OS_DIR, "workspace", f"screen_{int(time.time())}.png")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    try:
        from PIL import ImageGrab  # type: ignore

        img = ImageGrab.grab()
        img.save(out_path)
        return {"ok": True, "path": out_path, "mode": "screenshot"}
    except Exception as exc:
        return {"ok": False, "error": f"capture_failed:{exc}"}


def _tool_exec_vision_ocr_image(inputs: dict[str, Any]) -> Any:
    path = str(inputs.get("path") or "").strip()
    if not path:
        return {"ok": False, "error": "path_required"}
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore

        text = pytesseract.image_to_string(Image.open(path))
        return {"ok": True, "chars": len(text), "text_preview": text[:1200]}
    except Exception as exc:
        return {"ok": False, "error": f"ocr_failed:{exc}"}


def _tool_exec_macro_run(inputs: dict[str, Any]) -> Any:
    name = str(inputs.get("name") or "").strip()
    macro = _macros.get(name)
    if not macro:
        return {"ok": False, "error": "macro_not_found"}
    results = []
    for step in macro.steps:
        tool_name = str(step.get("tool_name") or "")
        tool_inputs = dict(step.get("inputs") or {})
        results.append(_execute_tool_action(tool_name, tool_inputs, actor="macro_runner"))
    return {"ok": True, "name": name, "results": results}


def _tool_exec_style_apply(inputs: dict[str, Any]) -> Any:
    contact = str(inputs.get("contact") or "").strip()
    text = str(inputs.get("text") or "")
    if not contact:
        return {"ok": False, "error": "contact_required"}
    msg = _styles.get(contact).apply(text)
    return {"ok": True, "contact": contact, "text": msg}


def _tool_exec_whatsapp_draft(inputs: dict[str, Any]) -> Any:
    to = str(inputs.get("to") or "").strip()
    text = str(inputs.get("text") or "").strip()
    if to:
        text = _styles.get(to).apply(text)
    verdict = policy_engine.evaluate(
        PolicyInput(action="write", target=f"whatsapp:{to}", channel="whatsapp", actor="orchestrator"),
        session_id="orchestrator",
    )
    engine.record_policy_decision(
        action="write",
        policy_tier=verdict.tier.value,
        allowed=verdict.allowed,
        reason=verdict.reason,
        session_id="orchestrator",
        actor="orchestrator",
        metadata={"target": f"whatsapp:{to}", "tool": "messaging.whatsapp.draft"},
    )
    if not verdict.allowed:
        return {"ok": False, "error": f"policy_blocked:{verdict.reason}", "requires_approval": verdict.requires_approval}
    return {"draft_id": f"draft_{uuid.uuid4().hex[:10]}", "to": to, "text": text}


def _tool_exec_whatsapp_send(inputs: dict[str, Any]) -> Any:
    to = str(inputs.get("to") or "").strip()
    text = str(inputs.get("text") or "").strip()
    if not to:
        return {"ok": False, "error": "recipient_required"}
    text = _styles.get(to).apply(text)
    verdict = policy_engine.evaluate(
        PolicyInput(action="send", target=f"whatsapp:{to}", channel="whatsapp", actor="orchestrator"),
        session_id="orchestrator",
    )
    engine.record_policy_decision(
        action="send",
        policy_tier=verdict.tier.value,
        allowed=verdict.allowed,
        reason=verdict.reason,
        session_id="orchestrator",
        actor="orchestrator",
        metadata={"target": f"whatsapp:{to}", "tool": "messaging.whatsapp.send"},
    )
    if not verdict.allowed:
        return {"ok": False, "error": f"policy_blocked:{verdict.reason}", "requires_approval": verdict.requires_approval}
    if cfg.whatsapp_enabled and cfg.twilio_sid and cfg.twilio_auth_token and cfg.twilio_whatsapp_number:
        try:
            from twilio.rest import Client

            client = Client(cfg.twilio_sid, cfg.twilio_auth_token)
            target = to if to.startswith("whatsapp:") else f"whatsapp:{to}"
            msg = client.messages.create(
                body=text,
                from_=cfg.twilio_whatsapp_number,
                to=target,
            )
            return {"ok": True, "sid": getattr(msg, "sid", "")}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    return {"ok": True, "mode": "simulated", "to": to, "text": text}


_TOOL_EXECUTORS: dict[str, Any] = {
    "filesystem.list": _tool_exec_filesystem_list,
    "filesystem.read": _tool_exec_filesystem_read,
    "filesystem.write_text": _tool_exec_filesystem_write_text,
    "filesystem.move": _tool_exec_filesystem_move,
    "filesystem.copy": _tool_exec_filesystem_copy,
    "filesystem.delete": _tool_exec_filesystem_delete,
    "filesystem.mkdir": _tool_exec_filesystem_mkdir,
    "filesystem.glob": _tool_exec_filesystem_glob,
    "browser.open_url": _tool_exec_browser_open_url,
    "browser.read_dom_text": _tool_exec_browser_read_dom_text,
    "browser.capture_screenshot": _tool_exec_browser_capture_screenshot,
    "browser.click_selector": _tool_exec_browser_click_selector,
    "process.run_terminal": _tool_exec_run_terminal,
    "process.list": _tool_exec_process_list,
    "process.launch": _tool_exec_process_launch,
    "process.terminate": _tool_exec_process_terminate,
    "process.focus_window": _tool_exec_process_focus_window,
    "messaging.telegram.send": _tool_exec_telegram_send,
    "messaging.telegram.send_file": _tool_exec_telegram_send_file,
    "messaging.whatsapp.draft": _tool_exec_whatsapp_draft,
    "messaging.whatsapp.send": _tool_exec_whatsapp_send,
    "invoice.get_artifacts": _tool_exec_invoice_get_artifacts,
    "vision.capture_screen": _tool_exec_vision_capture_screen,
    "vision.ocr_image": _tool_exec_vision_ocr_image,
    "macro.run": _tool_exec_macro_run,
    "style.apply": _tool_exec_style_apply,
}


def _is_idempotent_strategy(strategy: str) -> bool:
    return strategy in {
        "pure_read",
        "content_hash",
        "path_pair_hash",
        "path_hash",
        "url_hash",
        "time_window",
        "title_hash",
        "app_hash",
        "selector_hash",
        "idempotency_key",
    }


def _policy_action_for_tool(tool_name: str) -> str:
    spec = tool_registry.get(tool_name)
    return str(spec.action if spec else "execute").strip().lower()


def _risk_score_for_tool(tool_name: str) -> float:
    spec = tool_registry.get(tool_name)
    risk = str(spec.risk_class if spec else "medium").strip().lower()
    return {"low": 0.2, "medium": 0.5, "high": 0.8}.get(risk, 0.5)


def _collect_task_artifacts(task_id: str) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    if not task_id:
        return artifacts
    jobs_root = os.path.join(_VICTOR_OS_DIR, "workspace", "jobs")
    candidate = os.path.join(jobs_root, task_id)
    if os.path.isdir(candidate):
        for root, _, files in os.walk(candidate):
            for name in files:
                path = os.path.join(root, name)
                rel = os.path.relpath(path, _VICTOR_OS_DIR)
                artifacts.append({"path": rel.replace("\\", "/"), "size": os.path.getsize(path)})
    return artifacts[:400]


def _execute_tool_action(
    tool_name: str,
    inputs: dict[str, Any],
    *,
    task_id: str = "",
    session_id: str = "",
    actor: str = "orchestrator",
    trace_id: str = "",
) -> dict[str, Any]:
    started = time.time()
    trace = trace_id or f"tr_{uuid.uuid4().hex[:10]}"
    spec = tool_registry.get(tool_name)
    if not spec:
        err = {"ok": False, "error": "unknown_tool", "trace_id": trace}
        engine.record_tool_call(
            tool_name=tool_name,
            status="failed",
            task_id=task_id,
            session_id=session_id,
            actor=actor,
            action="execute",
            retries=0,
            input_data=inputs,
            output_data=err,
        )
        return err

    action = _policy_action_for_tool(tool_name)
    target = str(inputs.get("path") or inputs.get("url") or inputs.get("to") or tool_name)
    verdict = policy_engine.evaluate(
        PolicyInput(action=action, target=target, channel="system", actor=actor),
        session_id=session_id or actor,
    )
    engine.record_policy_decision(
        action=action,
        policy_tier=verdict.tier.value,
        allowed=verdict.allowed,
        reason=verdict.reason,
        task_id=task_id,
        session_id=session_id,
        actor=actor,
        metadata={"tool_name": tool_name, "target": target, "trace_id": trace},
    )
    if not verdict.allowed:
        blocked = {
            "ok": False,
            "error": f"policy_blocked:{verdict.reason}",
            "requires_approval": verdict.requires_approval,
            "trace_id": trace,
        }
        telemetry.emit(
            "action.blocked",
            task_id=task_id,
            session_id=session_id,
            actor=actor,
            payload={"tool_name": tool_name, "trace_id": trace, "reason": verdict.reason},
            risk_score=_risk_score_for_tool(tool_name),
            channel="system",
        )
        engine.record_tool_call(
            tool_name=tool_name,
            status="blocked",
            task_id=task_id,
            session_id=session_id,
            actor=actor,
            action=action,
            retries=0,
            input_data=inputs,
            output_data=blocked,
        )
        return blocked

    max_attempts = 2 if _is_idempotent_strategy(spec.idempotency_strategy) else 1
    last_error = ""
    output: Any = None
    success = False
    used_attempts = 0
    for attempt in range(1, max_attempts + 1):
        used_attempts = attempt
        try:
            handler = _TOOL_EXECUTORS.get(tool_name)
            if handler is None:
                last_error = "handler_not_found"
                break
            output = handler(dict(inputs or {}))
            if isinstance(output, dict) and output.get("ok") is False:
                last_error = str(output.get("error") or "tool_failed")
                if attempt >= max_attempts:
                    break
                continue
            success = True
            break
        except Exception as exc:
            last_error = str(exc)
            if attempt >= max_attempts:
                break

    elapsed_ms = (time.time() - started) * 1000.0
    if success:
        result = {"ok": True, "trace_id": trace, "output": output, "attempts": used_attempts}
        telemetry.emit(
            "action.executed",
            task_id=task_id,
            session_id=session_id,
            actor=actor,
            payload={"tool_name": tool_name, "trace_id": trace, "attempts": result["attempts"]},
            risk_score=_risk_score_for_tool(tool_name),
            channel="system",
        )
        engine.record_tool_call(
            tool_name=tool_name,
            status="completed",
            task_id=task_id,
            session_id=session_id,
            actor=actor,
            action=action,
            latency_ms=elapsed_ms,
            retries=max(0, used_attempts - 1),
            input_data=inputs,
            output_data=result,
        )
        return result

    failed = {"ok": False, "trace_id": trace, "error": last_error or "tool_failed", "attempts": used_attempts}
    telemetry.emit(
        "action.blocked",
        task_id=task_id,
        session_id=session_id,
        actor=actor,
        payload={"tool_name": tool_name, "trace_id": trace, "error": failed["error"]},
        risk_score=_risk_score_for_tool(tool_name),
        channel="system",
    )
    engine.record_tool_call(
        tool_name=tool_name,
        status="failed",
        task_id=task_id,
        session_id=session_id,
        actor=actor,
        action=action,
        latency_ms=elapsed_ms,
        retries=max(0, used_attempts - 1),
        input_data=inputs,
        output_data=failed,
    )
    return failed


orchestrator = PlannerExecutorReviewer(
    planner=Planner(tool_registry, router),
    executor=Executor(
        {
            "filesystem.list": lambda inputs: _execute_tool_action("filesystem.list", inputs),
            "filesystem.read": lambda inputs: _execute_tool_action("filesystem.read", inputs),
            "filesystem.write_text": lambda inputs: _execute_tool_action("filesystem.write_text", inputs),
            "filesystem.move": lambda inputs: _execute_tool_action("filesystem.move", inputs),
            "filesystem.copy": lambda inputs: _execute_tool_action("filesystem.copy", inputs),
            "filesystem.delete": lambda inputs: _execute_tool_action("filesystem.delete", inputs),
            "filesystem.mkdir": lambda inputs: _execute_tool_action("filesystem.mkdir", inputs),
            "filesystem.glob": lambda inputs: _execute_tool_action("filesystem.glob", inputs),
            "browser.open_url": lambda inputs: _execute_tool_action("browser.open_url", inputs),
            "browser.read_dom_text": lambda inputs: _execute_tool_action("browser.read_dom_text", inputs),
            "browser.capture_screenshot": lambda inputs: _execute_tool_action("browser.capture_screenshot", inputs),
            "browser.click_selector": lambda inputs: _execute_tool_action("browser.click_selector", inputs),
            "process.run_terminal": lambda inputs: _execute_tool_action("process.run_terminal", inputs),
            "process.list": lambda inputs: _execute_tool_action("process.list", inputs),
            "process.launch": lambda inputs: _execute_tool_action("process.launch", inputs),
            "process.terminate": lambda inputs: _execute_tool_action("process.terminate", inputs),
            "process.focus_window": lambda inputs: _execute_tool_action("process.focus_window", inputs),
            "messaging.telegram.send": lambda inputs: _execute_tool_action("messaging.telegram.send", inputs),
            "messaging.telegram.send_file": lambda inputs: _execute_tool_action("messaging.telegram.send_file", inputs),
            "messaging.whatsapp.draft": lambda inputs: _execute_tool_action("messaging.whatsapp.draft", inputs),
            "messaging.whatsapp.send": lambda inputs: _execute_tool_action("messaging.whatsapp.send", inputs),
            "invoice.get_artifacts": lambda inputs: _execute_tool_action("invoice.get_artifacts", inputs),
        }
    ),
    reviewer=Reviewer(),
)


class VictorTools:
    """
    The 'Hands' of Victor.
    Hardcoded capabilities that can be triggered by the agent layer.
    """

    @staticmethod
    def list_files(path: str = ".") -> list[str] | str:
        try:
            return os.listdir(path)
        except Exception as exc:
            return str(exc)

    @staticmethod
    def open_application(app_name: str) -> str:
        allowed_apps = {"notepad", "calc", "excel", "whatsapp"}
        normalized = str(app_name or "").strip().lower()
        if normalized not in allowed_apps:
            return "Access Denied: Application not in whitelist."
        try:
            os.system(f"start {normalized}")
            return f"Opened {normalized}"
        except Exception as exc:
            return f"Open failed: {exc}"

    @staticmethod
    def run_terminal_command(command: str) -> str:
        """
        Execute shell commands in a restricted workspace context.
        """
        workspace = os.path.join(_VICTOR_OS_DIR, "workspace")
        try:
            result = subprocess.check_output(
                command,
                shell=True,
                cwd=workspace,
                stderr=subprocess.STDOUT,
                text=True,
            )
            return result
        except Exception as exc:
            return str(exc)


if queue is not None:
    def _generic_handler(payload: dict[str, Any]) -> str:
        intent = str(payload.get("intent") or "task")
        run = orchestrator.run(intent=intent, context=dict(payload.get("context") or {}))
        review = run.get("review", {})
        execution = run.get("execution", {})
        return (
            f"Intent executed via {run.get('plan', {}).get('provider', 'unknown')} path. "
            f"review={review.get('status')} score={review.get('score')} "
            f"actions={len(execution.get('actions', []))}"
        )

    queue.register_handler("generic_intent", _generic_handler)

    # Control API should not compete with runtime workers by default.
    if os.getenv("VICTOR_API_RUN_WORKER", "false").strip().lower() in {"1", "true", "yes", "on"}:
        try:
            from invoice_pipeline import run_invoice_job  # type: ignore

            queue.register_handler("invoice_job", run_invoice_job)
        except Exception as exc:
            logger.warning(f"invoice_job handler not registered in API worker: {exc}")
        queue.start_worker(poll_interval=1.0)


def _json() -> dict[str, Any]:
    return request.get_json(silent=True) or {}


def _estimated_actions(intent: str) -> list[str]:
    text = (intent or "").lower()
    actions: list[str] = []
    if any(token in text for token in ["read", "open", "list"]):
        actions.append("read")
    if any(token in text for token in ["write", "save", "edit", "rename"]):
        actions.append("write")
    if any(token in text for token in ["send", "message", "whatsapp", "telegram"]):
        actions.append("send")
    if any(token in text for token in ["run", "launch", "execute"]):
        actions.append("execute")
    return actions or ["inspect"]


def _map_to_task_type(intent: str) -> str:
    text = (intent or "").lower()
    if any(k in text for k in ["invoice", "receipt", "rename pdf", "zip"]):
        return "invoice_job"
    return "generic_intent"


def _compat_error(*, code: str, message: str, status: int = 400, details: dict[str, Any] | None = None) -> tuple[Any, int]:
    return (
        jsonify(
            {
                "ok": False,
                "error": {
                    "type": "invalid_request_error",
                    "code": code,
                    "message": message,
                    "details": details or {},
                },
            }
        ),
        int(status),
    )


def _extract_responses_input(payload: dict[str, Any]) -> str:
    raw = payload.get("input")
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, list):
        chunks: list[str] = []
        for item in raw:
            if isinstance(item, str):
                chunks.append(item)
                continue
            if isinstance(item, dict):
                if isinstance(item.get("content"), str):
                    chunks.append(str(item.get("content")))
                text = item.get("text")
                if isinstance(text, str):
                    chunks.append(text)
                content = item.get("content")
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and isinstance(c.get("text"), str):
                            chunks.append(str(c.get("text")))
        return "\n".join([c for c in chunks if c]).strip()
    return ""


def _extract_api_key_from_request() -> str:
    key = (request.headers.get("X-API-Key") or "").strip()
    if key:
        return key
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _match_api_key(provided: str) -> tuple[bool, str]:
    if not provided:
        return False, ""
    for key_id, value in _API_KEYS.items():
        if value and hmac.compare_digest(provided, value):
            return True, key_id
    return False, ""


def _audit_auth_failure(*, reason: str, status_code: int) -> None:
    client_ip = str(
        request.headers.get("X-Forwarded-For")
        or request.headers.get("X-Real-IP")
        or request.remote_addr
        or ""
    )
    payload = {
        "reason": reason,
        "status_code": status_code,
        "path": request.path,
        "method": request.method,
        "client_ip": client_ip,
    }
    logger.warning(f"API auth blocked: {payload}")
    telemetry.emit(
        "action.blocked",
        actor="api_gateway",
        payload=payload,
        risk_score=0.8,
        channel="api",
        source="agent_framework",
    )


def _audit_scope_decision(*, payload: dict[str, Any]) -> None:
    telemetry.emit(
        "policy.decision",
        actor="api_gateway",
        payload=payload,
        risk_score=0.2 if payload.get("allowed") else 0.8,
        channel="api",
        source="agent_framework",
    )
    try:
        engine.emit_event(
            {
                "event_type": "policy.decision",
                "session_id": str(request.headers.get("X-Session-Id") or ""),
                "task_id": str(request.headers.get("X-Task-Id") or ""),
                "actor": "api_gateway",
                "payload": payload,
                "risk_score": 0.2 if payload.get("allowed") else 0.8,
                "channel": "api",
                "source": "agent_framework",
            }
        )
    except Exception:
        pass


def _rate_limit_bucket_for_request(path: str, method: str) -> tuple[str, int]:
    m = method.upper()
    if path.startswith("/v1/control/") and m in {"POST", "PUT", "PATCH", "DELETE"}:
        return "control_write", _RATE_LIMIT_CONTROL_WRITE_PER_MIN
    if path.startswith("/v1/tasks") and m in {"POST", "PUT", "PATCH", "DELETE"}:
        return "tasks_write", _RATE_LIMIT_TASKS_WRITE_PER_MIN
    if path.startswith("/v1/training/") and m in {"POST", "PUT", "PATCH", "DELETE"}:
        return "training_write", _RATE_LIMIT_TRAINING_WRITE_PER_MIN
    if path.startswith("/v1/audit/") and m == "GET":
        return "audit_read", _RATE_LIMIT_AUDIT_READ_PER_MIN
    return "default", _RATE_LIMIT_PER_MIN


def _requires_trusted_device(path: str, method: str) -> bool:
    p = str(path or "")
    m = str(method or "GET").upper()
    if p.startswith("/v1/control/"):
        return True
    if p.startswith("/v1/tools/execute"):
        return True
    if p.startswith("/v1/training/") and m in {"POST", "PUT", "PATCH", "DELETE"}:
        return True
    return False


def _check_rate_limit(api_identity: str, limit_per_window: int) -> tuple[bool, int]:
    now = time.time()
    with _rate_limit_lock:
        bucket = _rate_limit_by_key.setdefault(api_identity, deque())
        cutoff = now - float(_RATE_LIMIT_WINDOW_SEC)
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= int(limit_per_window):
            return False, int(max(0, float(_RATE_LIMIT_WINDOW_SEC) - (now - bucket[0])))
        bucket.append(now)
        return True, 0


def _record_router_metrics(provider: str, confidence: float, intent_class: str, low_risk: bool) -> None:
    _ROUTER_METRICS["total_routed"] += 1
    if provider == "local":
        _ROUTER_METRICS["local_routed"] += 1
    else:
        _ROUTER_METRICS["remote_routed"] += 1
    if provider == "remote" and confidence >= float(os.getenv("LOCAL_ROUTER_THRESHOLD", "0.7")):
        _ROUTER_METRICS["fallback_count"] += 1
    if confidence < 0.7:
        _ROUTER_METRICS["low_confidence_count"] += 1
    if confidence < 0.5:
        bucket = "0.0-0.49"
    elif confidence < 0.7:
        bucket = "0.50-0.69"
    elif confidence < 0.85:
        bucket = "0.70-0.84"
    else:
        bucket = "0.85-1.00"
    _ROUTER_METRICS["confidence_hist"][bucket] += 1
    counts = _ROUTER_METRICS["intent_class_counts"]
    counts[intent_class] = int(counts.get(intent_class, 0)) + 1
    if low_risk:
        _ROUTER_METRICS["low_risk_total"] += 1
        if provider == "local":
            _ROUTER_METRICS["low_risk_local"] += 1
    _ROUTER_METRICS["updated_at"] = time.time()


@app.before_request
def _enforce_api_key() -> Any:
    if not request.path.startswith("/v1/"):
        return None
    if not _API_KEYS:
        return None
    client_ip = str(
        request.headers.get("X-Forwarded-For")
        or request.headers.get("X-Real-IP")
        or request.remote_addr
        or "unknown"
    )
    if _API_IP_ALLOWLIST and client_ip not in _API_IP_ALLOWLIST:
        _audit_auth_failure(reason="ip_not_allowlisted", status_code=403)
        return jsonify({"ok": False, "error": "forbidden"}), 403
    provided = _extract_api_key_from_request()
    matched, key_id = _match_api_key(provided)
    if not matched:
        _audit_auth_failure(reason="invalid_or_missing_api_key", status_code=401)
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    required_scope = required_scope_for_route(request.path, request.method)
    scope_allowed, scope_reason = check_scope_allowed(key_id, required_scope, _API_KEY_SCOPE_MAP)
    scope_payload = scope_decision_payload(
        key_id=key_id,
        required_scope=required_scope,
        allowed=scope_allowed,
        reason=scope_reason,
        path=request.path,
        method=request.method,
    )
    _audit_scope_decision(payload=scope_payload)
    if not scope_allowed:
        _audit_auth_failure(reason="scope_forbidden", status_code=403)
        return jsonify(
            {
                "ok": False,
                "error": "forbidden",
                "required_scope": required_scope,
                "reason": scope_reason,
            }
        ), 403

    bucket, route_limit = _rate_limit_bucket_for_request(request.path, request.method)
    identity = f"{key_id}:{client_ip}:{bucket}"
    allowed, retry_after = _check_rate_limit(identity, route_limit)
    if not allowed:
        _audit_auth_failure(reason="rate_limit_exceeded", status_code=429)
        response = jsonify({"ok": False, "error": "rate_limited", "retry_after_sec": retry_after})
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        response.headers["X-RateLimit-Bucket"] = bucket
        return response

    if _requires_trusted_device(request.path, request.method):
        device_id = str(
            request.headers.get("X-Device-Id")
            or request.headers.get("X-Client-Device")
            or ""
        ).strip()
        if not device_id:
            _audit_auth_failure(reason="device_id_required", status_code=403)
            return jsonify({"ok": False, "error": "trusted_device_required"}), 403
        if not _device_registry.is_trusted(device_id):
            _audit_auth_failure(reason="untrusted_device", status_code=403)
            return jsonify({"ok": False, "error": "untrusted_device", "device_id": device_id}), 403
    return None


@app.get("/v1/security/audit")
def security_audit():
    deep = str(request.args.get("deep", "false")).strip().lower() in {"1", "true", "yes", "on"}
    result = run_security_audit(deep=deep, write_report=True)
    return jsonify({"ok": True, "audit": result})


@app.post("/v1/security/profile/apply")
def security_profile_apply():
    payload = _json()
    profile = str(payload.get("profile") or "hardened_default").strip() or "hardened_default"
    try:
        result = apply_baseline(profile)
        return jsonify({"ok": True, "result": result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/v1/security/profile/rollback")
def security_profile_rollback():
    try:
        result = rollback_baseline()
        return jsonify({"ok": True, "result": result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/v1/devices/pair/request")
def device_pair_request():
    payload = _json()
    device_name = str(payload.get("device_name") or "unknown_device").strip()
    requester_user_id = str(payload.get("requester_user_id") or "unknown_user").strip()
    metadata = dict(payload.get("metadata") or {})
    result = _device_registry.pair_request(
        device_name=device_name,
        requester_user_id=requester_user_id,
        metadata=metadata,
    )
    telemetry.emit(
        "device.pair.requested",
        actor=requester_user_id,
        payload={"device_name": device_name, "device_id": result.get("device_id"), "request_id": result.get("request_id")},
        risk_score=0.3,
        channel="api",
    )
    engine.emit_event(
        {
            "event_type": "device.pair.requested",
            "actor": requester_user_id,
            "payload": {"device_name": device_name, "device_id": result.get("device_id"), "request_id": result.get("request_id")},
            "channel": "api",
            "source": "agent_framework",
            "risk_score": 0.3,
        }
    )
    return jsonify({"ok": True, "result": result})


@app.post("/v1/devices/pair/approve")
def device_pair_approve():
    payload = _json()
    request_id = str(payload.get("request_id") or "").strip()
    approver_user_id = str(payload.get("approver_user_id") or "owner").strip()
    note = str(payload.get("approval_note") or "").strip()
    if not request_id:
        return jsonify({"ok": False, "error": "request_id is required"}), 400
    result = _device_registry.approve(
        request_id=request_id,
        approver_user_id=approver_user_id,
        approval_note=note,
    )
    if not result.get("ok"):
        return jsonify({"ok": False, "error": result.get("error")}), 409
    telemetry.emit(
        "device.pair.approved",
        actor=approver_user_id,
        payload=result,
        risk_score=0.2,
        channel="api",
    )
    engine.emit_event(
        {
            "event_type": "device.pair.approved",
            "actor": approver_user_id,
            "payload": result,
            "channel": "api",
            "source": "agent_framework",
            "risk_score": 0.2,
        }
    )
    return jsonify({"ok": True, "result": result})


@app.post("/v1/devices/revoke")
def device_revoke():
    payload = _json()
    device_id = str(payload.get("device_id") or "").strip()
    revoked_by = str(payload.get("revoked_by") or "owner").strip()
    reason = str(payload.get("reason") or "").strip()
    if not device_id:
        return jsonify({"ok": False, "error": "device_id is required"}), 400
    result = _device_registry.revoke(device_id=device_id, revoked_by=revoked_by, reason=reason)
    if not result.get("ok"):
        return jsonify({"ok": False, "error": result.get("error")}), 404
    telemetry.emit(
        "device.revoked",
        actor=revoked_by,
        payload=result,
        risk_score=0.4,
        channel="api",
    )
    engine.emit_event(
        {
            "event_type": "device.revoked",
            "actor": revoked_by,
            "payload": result,
            "channel": "api",
            "source": "agent_framework",
            "risk_score": 0.4,
        }
    )
    return jsonify({"ok": True, "result": result})


@app.get("/v1/devices")
def devices_list():
    status = str(request.args.get("status") or "").strip()
    rows = _device_registry.list_devices(status=status or None)
    return jsonify({"ok": True, "count": len(rows), "devices": rows})


@app.post("/v1/responses")
def responses_compat():
    payload = _json()
    input_text = _extract_responses_input(payload)
    if not input_text and not payload.get("tool_call"):
        return _compat_error(code="missing_input", message="input is required")

    stream = bool(payload.get("stream"))
    response_id = f"resp_{uuid.uuid4().hex[:18]}"
    session_id = str(payload.get("session_id") or "").strip()
    actor = str(payload.get("actor") or "compat_user").strip()

    tool_call = payload.get("tool_call")
    if isinstance(tool_call, dict) and tool_call.get("name"):
        tool_name = str(tool_call.get("name") or "").strip()
        arguments = dict(tool_call.get("arguments") or {})
        if not tool_name:
            return _compat_error(code="missing_tool_name", message="tool_call.name is required")
        result = _execute_tool_action(
            tool_name,
            arguments,
            task_id=str(payload.get("task_id") or ""),
            session_id=session_id,
            actor=actor,
            trace_id=str(payload.get("trace_id") or ""),
        )
        if not result.get("ok"):
            return _compat_error(
                code="tool_execution_failed",
                message=str(result.get("error") or "tool failed"),
                status=409,
                details={"tool_name": tool_name, "result": result},
            )
        return jsonify(
            {
                "id": response_id,
                "object": "response",
                "status": "completed",
                "model": str(payload.get("model") or cfg.model_name),
                "output": [
                    {
                        "type": "tool_result",
                        "tool_name": tool_name,
                        "arguments": arguments,
                        "content": result,
                    }
                ],
                "stream": {"requested": stream, "supported": False, "plan": "SSE placeholder only in this build"},
            }
        )

    task_payload = {
        "intent": input_text,
        "channel": str(payload.get("channel") or "api").strip(),
        "idempotency_key": str(payload.get("idempotency_key") or "").strip(),
        "risk_level_hint": str(payload.get("risk_level_hint") or "medium").strip(),
        "context_refs": list(payload.get("context_refs") or []),
        "payload": dict(payload.get("payload") or {}),
        "session_id": session_id,
        "actor": actor,
        "user_id": str(payload.get("user_id") or "ceejay"),
    }
    intent_obj = TaskIntent(
        intent=task_payload["intent"],
        channel=task_payload["channel"],
        idempotency_key=task_payload["idempotency_key"],
        risk_level_hint=task_payload["risk_level_hint"],
        context_refs=task_payload["context_refs"],
        payload=task_payload["payload"],
    )
    if not intent_obj.intent:
        return _compat_error(code="missing_input", message="input is required")
    if _KILL_SWITCH["enabled"]:
        return _compat_error(code="kill_switch_enabled", message="global kill switch enabled", status=423)
    if queue is None:
        return _compat_error(code="queue_unavailable", message="task queue unavailable", status=503)

    queue_payload = dict(intent_obj.payload)
    explicit_task_type = str(queue_payload.get("task_type") or "").strip()
    task_type = explicit_task_type or _map_to_task_type(intent_obj.intent)
    queue_payload.setdefault("intent", intent_obj.intent)
    queue_payload.setdefault("context_refs", intent_obj.context_refs)

    task_id = queue.enqueue(
        task_type=task_type,
        payload=queue_payload,
        channel=intent_obj.channel,
        user_id=str(task_payload.get("user_id") or "ceejay"),
        idempotency_key=intent_obj.idempotency_key or None,
    )
    engine.upsert_task_run(
        task_id=task_id,
        state="pending",
        channel=intent_obj.channel,
        user_id=str(task_payload.get("user_id") or "ceejay"),
        idempotency_key=intent_obj.idempotency_key,
        payload=queue_payload,
        metadata={"created_by": actor, "compat": "openresponses"},
    )
    estimated = _estimated_actions(intent_obj.intent)
    return jsonify(
        {
            "id": response_id,
            "object": "response",
            "status": "queued",
            "model": str(payload.get("model") or cfg.model_name),
            "task_id": task_id,
            "output_text": "",
            "estimated_actions": estimated,
            "stream": {"requested": stream, "supported": False, "plan": "SSE placeholder only in this build"},
        }
    )


@app.post("/v1/tasks")
def create_task():
    payload = _json()
    intent_obj = TaskIntent(
        intent=str(payload.get("intent") or "").strip(),
        channel=str(payload.get("channel") or "system").strip(),
        idempotency_key=str(payload.get("idempotency_key") or "").strip(),
        risk_level_hint=str(payload.get("risk_level_hint") or "medium").strip(),
        context_refs=list(payload.get("context_refs") or []),
        payload=dict(payload.get("payload") or {}),
    )
    if not intent_obj.intent:
        return jsonify({"ok": False, "error": "intent is required"}), 400

    if _KILL_SWITCH["enabled"]:
        return jsonify({"ok": False, "error": "global kill switch enabled"}), 423

    if queue is None:
        return jsonify({"ok": False, "error": "task queue unavailable"}), 503

    session_id = str(payload.get("session_id") or "").strip()
    actor = str(payload.get("actor") or "api_user").strip()
    estimated = _estimated_actions(intent_obj.intent)
    risk_score = 0.7 if "execute" in estimated or "send" in estimated else 0.2

    telemetry.emit(
        "intent.received",
        session_id=session_id,
        actor=actor,
        payload={
            "intent": intent_obj.intent,
            "channel": intent_obj.channel,
            "idempotency_key": intent_obj.idempotency_key,
            "context_refs": intent_obj.context_refs,
            "risk_level_hint": intent_obj.risk_level_hint,
        },
        risk_score=risk_score,
        channel=intent_obj.channel,
    )

    queue_payload = dict(intent_obj.payload)
    explicit_task_type = str(queue_payload.get("task_type") or "").strip()
    task_type = explicit_task_type or _map_to_task_type(intent_obj.intent)
    queue_payload.setdefault("intent", intent_obj.intent)
    queue_payload.setdefault("context_refs", intent_obj.context_refs)

    task_id = queue.enqueue(
        task_type=task_type,
        payload=queue_payload,
        channel=intent_obj.channel,
        user_id=str(payload.get("user_id") or "ceejay"),
        idempotency_key=intent_obj.idempotency_key or None,
    )
    engine.upsert_task_run(
        task_id=task_id,
        state="pending",
        channel=intent_obj.channel,
        user_id=str(payload.get("user_id") or "ceejay"),
        idempotency_key=intent_obj.idempotency_key,
        payload=queue_payload,
        metadata={"created_by": actor, "risk_level_hint": intent_obj.risk_level_hint},
    )
    telemetry.emit(
        "plan.generated",
        task_id=task_id,
        session_id=session_id,
        actor="planner",
        payload={"estimated_actions": estimated, "task_type": task_type},
        risk_score=risk_score,
        channel=intent_obj.channel,
    )
    return jsonify({"ok": True, "task_id": task_id, "state": "pending", "estimated_actions": estimated})


@app.get("/v1/tasks/<task_id>")
def get_task(task_id: str):
    if queue is None:
        return jsonify({"ok": False, "error": "task queue unavailable"}), 503
    task = queue.get_task(task_id)
    if not task:
        return jsonify({"ok": False, "error": "task not found"}), 404
    events = [e for e in engine.list_recent_events(limit=500) if e.get("task_id") == task_id]
    return jsonify(
        {
            "ok": True,
            "task": {
                "id": task.id,
                "task_type": task.task_type,
                "status": str(task.status.value),
                "channel": task.channel,
                "user_id": task.user_id,
                "retries": task.retries,
                "progress": task.progress,
                "result": task.result,
                "error": task.error,
                "created_at": task.created_at,
                "updated_at": task.updated_at,
                "started_at": task.started_at,
                "completed_at": task.completed_at,
                "metadata": task.metadata,
            },
            "execution_trace": events,
            "artifacts": [],
        }
    )


@app.get("/v1/tasks/<task_id>/artifacts")
def get_task_artifacts(task_id: str):
    artifacts = _collect_task_artifacts(task_id)
    return jsonify({"ok": True, "task_id": task_id, "count": len(artifacts), "artifacts": artifacts})


@app.get("/v1/tasks/latest")
def get_latest_tasks():
    if queue is None:
        return jsonify({"ok": False, "error": "task queue unavailable"}), 503
    try:
        limit = int(request.args.get("limit", "5"))
    except Exception:
        limit = 5
    tasks = queue.list_recent_tasks(limit=max(1, min(50, limit)))
    return jsonify(
        {
            "ok": True,
            "tasks": [
                {
                    "id": t.id,
                    "task_type": t.task_type,
                    "status": t.status.value,
                    "progress": t.progress,
                    "retries": t.retries,
                    "error": t.error,
                    "result": t.result,
                    "created_at": t.created_at,
                    "updated_at": t.updated_at,
                }
                for t in tasks
            ],
        }
    )


@app.get("/v1/metrics/router")
def metrics_router():
    total = int(_ROUTER_METRICS.get("total_routed") or 0)
    local = int(_ROUTER_METRICS.get("local_routed") or 0)
    remote = int(_ROUTER_METRICS.get("remote_routed") or 0)
    low_risk_total = int(_ROUTER_METRICS.get("low_risk_total") or 0)
    low_risk_local = int(_ROUTER_METRICS.get("low_risk_local") or 0)
    return jsonify(
        {
            "ok": True,
            "metrics": {
                **_ROUTER_METRICS,
                "local_ratio": (float(local) / float(total)) if total else 0.0,
                "remote_ratio": (float(remote) / float(total)) if total else 0.0,
                "low_risk_local_ratio": (float(low_risk_local) / float(low_risk_total)) if low_risk_total else 0.0,
            },
        }
    )


@app.get("/v1/metrics/tools")
def metrics_tools():
    conn = engine._connect()  # noqa: SLF001
    try:
        tool_rows = conn.execute(
            """
            SELECT tool_name,
                   COUNT(*) AS total_calls,
                   SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed_calls,
                   SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed_calls,
                   SUM(CASE WHEN status='blocked' THEN 1 ELSE 0 END) AS blocked_calls,
                   AVG(latency_ms) AS avg_latency_ms
            FROM tool_calls
            GROUP BY tool_name
            ORDER BY total_calls DESC
            """
        ).fetchall()
        policy_rows_raw = conn.execute(
            """
            SELECT metadata_json
            FROM policy_decisions
            WHERE allowed=0
            """
        ).fetchall()
    finally:
        conn.close()

    blocked_by_tool: dict[str, int] = {}
    for r in policy_rows_raw:
        raw = str(r["metadata_json"] or "{}")
        try:
            meta = json.loads(raw)
        except Exception:
            meta = {}
        tool = str(meta.get("tool_name") or "").strip()
        if not tool:
            continue
        blocked_by_tool[tool] = int(blocked_by_tool.get(tool, 0)) + 1
    items = []
    for row in tool_rows:
        name = str(row["tool_name"] or "")
        items.append(
            {
                "tool_name": name,
                "total_calls": int(row["total_calls"] or 0),
                "completed_calls": int(row["completed_calls"] or 0),
                "failed_calls": int(row["failed_calls"] or 0),
                "blocked_calls": int(row["blocked_calls"] or 0),
                "policy_blocked_count": blocked_by_tool.get(name, 0),
                "avg_latency_ms": float(row["avg_latency_ms"] or 0.0),
            }
        )
    return jsonify({"ok": True, "tools": items})


@app.post("/v1/queue/drill")
def queue_drill():
    if queue is None:
        return jsonify({"ok": False, "error": "task queue unavailable"}), 503
    report = queue.run_crash_recovery_drill()
    return jsonify({"ok": True, "drill": report})


@app.post("/v1/tasks/<task_id>/approve")
def approve_task(task_id: str):
    payload = _json()
    approval_token = str(payload.get("approval_token") or "").strip()
    scope = str(payload.get("scope") or "single_action").strip()
    if not approval_token:
        return jsonify({"ok": False, "error": "approval_token is required"}), 400
    telemetry.emit(
        "action.executed",
        task_id=task_id,
        actor="approver",
        payload={"approval_token": "provided", "scope": scope},
        channel=str(payload.get("channel") or "system"),
    )
    return jsonify({"ok": True, "task_id": task_id, "approved": True, "scope": scope})


@app.post("/v1/tasks/<task_id>/cancel")
def cancel_task(task_id: str):
    if queue is None:
        return jsonify({"ok": False, "error": "task queue unavailable"}), 503
    ok = queue.cancel_task(task_id)
    if not ok:
        return jsonify({"ok": False, "error": "task cannot be cancelled"}), 409
    engine.upsert_task_run(task_id=task_id, state="cancelled")
    telemetry.emit("action.executed", task_id=task_id, actor="system", payload={"action": "cancel_task"})
    return jsonify({"ok": True, "task_id": task_id, "state": "cancelled"})


@app.get("/v1/capabilities")
def capabilities():
    return jsonify(
        {
            "ok": True,
            "capabilities": tool_registry.list_tools(),
        }
    )


@app.post("/v1/orchestrate/run")
def orchestrate_run():
    payload = _json()
    intent = str(payload.get("intent") or "").strip()
    if not intent:
        return jsonify({"ok": False, "error": "intent is required"}), 400
    context = dict(payload.get("context") or {})
    result = orchestrator.run(intent=intent, context=context)
    plan = dict(result.get("plan") or {})
    provider = str(plan.get("provider") or "remote")
    confidence = float(plan.get("confidence") or 0.0)
    intent_class = str(plan.get("intent_class") or "unknown")
    actions = list(plan.get("actions") or [])
    low_risk = all(str(a.get("risk_class") or "medium").lower() == "low" for a in actions) if actions else False
    _record_router_metrics(provider, confidence, intent_class, low_risk)
    session_id = str(payload.get("session_id") or "")
    task_id = str(payload.get("task_id") or "")
    actor = str(payload.get("actor") or "orchestrator")
    telemetry.emit(
        "plan.generated",
        session_id=session_id,
        task_id=task_id,
        actor=actor,
        payload={"plan": result.get("plan", {})},
        risk_score=0.3,
        channel=str(payload.get("channel") or "system"),
    )
    telemetry.emit(
        "router.decision",
        session_id=session_id,
        task_id=task_id,
        actor="router",
        payload={
            "provider": provider,
            "confidence": confidence,
            "intent_class": intent_class,
            "low_risk": low_risk,
        },
        risk_score=0.2,
        channel=str(payload.get("channel") or "system"),
    )
    return jsonify({"ok": True, "result": result})


@app.post("/v1/tools/execute")
def tools_execute():
    payload = _json()
    tool_name = str(payload.get("tool_name") or "").strip()
    if not tool_name:
        return jsonify({"ok": False, "error": "tool_name is required"}), 400
    inputs = dict(payload.get("inputs") or {})
    task_id = str(payload.get("task_id") or "").strip()
    session_id = str(payload.get("session_id") or "").strip()
    actor = str(payload.get("actor") or "api_user").strip()
    trace_id = str(payload.get("trace_id") or "").strip()
    result = _execute_tool_action(
        tool_name,
        inputs,
        task_id=task_id,
        session_id=session_id,
        actor=actor,
        trace_id=trace_id,
    )
    status = 200 if result.get("ok") else 409
    return jsonify({"ok": bool(result.get("ok")), "result": result}), status


@app.post("/v1/orchestrate/rollback")
def orchestrate_rollback():
    payload = _json()
    rollback_plan = list(payload.get("rollback_plan") or [])
    if not rollback_plan:
        return jsonify({"ok": False, "error": "rollback_plan is required"}), 400
    executed = []

    def _compensate(step: dict[str, Any]) -> dict[str, Any]:
        tool_name = str(step.get("tool_name") or "")
        inputs = dict(step.get("inputs") or {})
        if tool_name == "messaging.whatsapp.send":
            # Best available compensation for sent messages: corrective follow-up note.
            to = str(inputs.get("to") or "").strip()
            correction = (
                "Correction: please disregard previous automated message. "
                "A revised update will follow."
            )
            result = _tool_exec_whatsapp_send({"to": to, "text": correction}) if to else {"ok": False, "error": "missing_to"}
            return {"tool_name": tool_name, "status": "compensated_followup", "result": result}
        if tool_name == "messaging.whatsapp.draft":
            return {"tool_name": tool_name, "status": "compensated_drop_draft", "result": {"ok": True}}
        if tool_name == "messaging.telegram.send_file":
            return {"tool_name": tool_name, "status": "compensated_followup_notice", "result": {"ok": True}}
        if tool_name == "filesystem.copy":
            dst = str(inputs.get("dst") or "").strip()
            if dst and os.path.exists(dst):
                try:
                    os.remove(dst)
                    return {"tool_name": tool_name, "status": "compensated_delete_copy", "result": {"ok": True, "dst": dst}}
                except Exception as exc:
                    return {"tool_name": tool_name, "status": "compensation_failed", "result": {"ok": False, "error": str(exc)}}
            return {"tool_name": tool_name, "status": "noop", "result": {"ok": True}}
        if tool_name == "filesystem.move":
            src = str(inputs.get("src") or "").strip()
            dst = str(inputs.get("dst") or "").strip()
            if dst and src and os.path.exists(dst):
                try:
                    shutil.move(dst, src)
                    return {"tool_name": tool_name, "status": "compensated_move_back", "result": {"ok": True, "src": src, "dst": dst}}
                except Exception as exc:
                    return {"tool_name": tool_name, "status": "compensation_failed", "result": {"ok": False, "error": str(exc)}}
            return {"tool_name": tool_name, "status": "noop", "result": {"ok": True}}
        if tool_name == "filesystem.mkdir":
            path = str(inputs.get("path") or "").strip()
            try:
                if path and os.path.isdir(path) and not os.listdir(path):
                    os.rmdir(path)
                    return {"tool_name": tool_name, "status": "compensated_remove_dir", "result": {"ok": True, "path": path}}
            except Exception as exc:
                return {"tool_name": tool_name, "status": "compensation_failed", "result": {"ok": False, "error": str(exc)}}
            return {"tool_name": tool_name, "status": "noop", "result": {"ok": True}}
        if tool_name == "process.run_terminal":
            return {"tool_name": tool_name, "status": "manual_required", "result": {"ok": False, "error": "no_safe_auto_rollback"}}
        return {"tool_name": tool_name, "status": "no_compensation_handler", "result": {"ok": False}}

    for step in rollback_plan:
        executed.append(_compensate(step))
    return jsonify({"ok": True, "rollback": executed})


@app.post("/v1/training/export")
def training_export():
    payload = _json()
    out_dir = str(payload.get("out_dir") or os.path.join(_VICTOR_OS_DIR, "memory_store", "training_exports"))
    eval_ratio = float(payload.get("eval_ratio") or 0.2)
    result = training.export_dataset(out_dir=out_dir, eval_ratio=eval_ratio)
    telemetry.emit(
        "training.example.emitted",
        actor="training_pipeline",
        payload={
            "source": "dataset_export",
            "train_examples": result.train_examples,
            "eval_examples": result.eval_examples,
            "golden_examples": result.golden_examples,
            "out_dir": out_dir,
        },
        risk_score=0.1,
        channel="system",
    )
    return jsonify({"ok": True, "export": result.__dict__})


@app.post("/v1/training/evaluate")
def training_evaluate():
    payload = _json()
    checkpoint_id = str(payload.get("checkpoint_id") or f"ckpt_{uuid.uuid4().hex[:8]}")
    golden_path = str(
        payload.get("golden_path")
        or os.path.join(_VICTOR_OS_DIR, "memory_store", "training_exports", "golden.jsonl")
    )
    threshold = float(payload.get("accuracy_threshold") or 0.75)
    metrics = training.evaluate_checkpoint(
        checkpoint_id=checkpoint_id,
        golden_path=golden_path,
        accuracy_threshold=threshold,
    )
    return jsonify({"ok": True, "metrics": metrics})


@app.post("/v1/training/promote")
def training_promote():
    payload = _json()
    checkpoint_id = str(payload.get("checkpoint_id") or "").strip()
    metrics = dict(payload.get("metrics") or {})
    if not checkpoint_id:
        return jsonify({"ok": False, "error": "checkpoint_id is required"}), 400
    registry_path = str(
        payload.get("registry_path")
        or os.path.join(_VICTOR_OS_DIR, "memory_store", "model_registry.json")
    )
    min_accuracy = float(payload.get("min_accuracy") or 0.75)
    model_track = str(payload.get("model_track") or "routine_policy_model").strip()
    training_manifest_hash = str(payload.get("training_manifest_hash") or "").strip()
    safety_gate_passed = bool(payload.get("safety_gate_passed", True))
    regression_delta = float(payload.get("regression_delta") or 0.0)
    result = training.promote_checkpoint(
        checkpoint_id=checkpoint_id,
        metrics=metrics,
        registry_path=registry_path,
        min_accuracy=min_accuracy,
        model_track=model_track,
        training_manifest_hash=training_manifest_hash,
        safety_gate_passed=safety_gate_passed,
        regression_delta=regression_delta,
    )
    if bool(result.get("allowed")):
        telemetry.emit(
            "model.promoted",
            actor="training_pipeline",
            payload={"checkpoint_id": checkpoint_id, "result": result},
            risk_score=0.1,
            channel="system",
        )
    else:
        telemetry.emit(
            "model.promotion_blocked",
            actor="training_pipeline",
            payload={"checkpoint_id": checkpoint_id, "result": result},
            risk_score=0.2,
            channel="system",
        )
    return jsonify({"ok": True, "promotion": result})


@app.post("/v1/training/shadow_deploy")
def training_shadow_deploy():
    payload = _json()
    checkpoint_id = str(payload.get("checkpoint_id") or "").strip()
    if not checkpoint_id:
        return jsonify({"ok": False, "error": "checkpoint_id is required"}), 400
    registry_path = str(
        payload.get("registry_path")
        or os.path.join(_VICTOR_OS_DIR, "memory_store", "model_registry.json")
    )
    result = training.shadow_deploy_checkpoint(checkpoint_id=checkpoint_id, registry_path=registry_path)
    telemetry.emit(
        "model.shadow_evaluated",
        actor="training_pipeline",
        payload={"checkpoint_id": checkpoint_id, "registry_path": registry_path, "event": "shadow_deploy"},
        risk_score=0.1,
        channel="system",
    )
    return jsonify({"ok": True, "shadow": result})


@app.post("/v1/training/shadow_promote")
def training_shadow_promote():
    payload = _json()
    registry_path = str(
        payload.get("registry_path")
        or os.path.join(_VICTOR_OS_DIR, "memory_store", "model_registry.json")
    )
    metrics = dict(payload.get("metrics") or {})
    min_accuracy = float(payload.get("min_accuracy") or 0.75)
    active_metrics = dict(payload.get("active_metrics") or {})
    shadow_metrics = dict(payload.get("shadow_metrics") or {})
    compare = training.compare_shadow_vs_active_metrics(
        active_metrics=active_metrics,
        shadow_metrics=shadow_metrics,
        min_shadow_accuracy=min_accuracy,
    )
    if not compare.get("promotion_allowed", False):
        telemetry.emit(
            "model.promotion_blocked",
            actor="training_pipeline",
            payload={"reason": "shadow_compare_failed", "comparison": compare},
            risk_score=0.2,
            channel="system",
        )
        return jsonify({"ok": False, "error": "shadow_compare_failed", "comparison": compare}), 409

    result = training.promote_shadow_if_passed(
        registry_path=registry_path,
        metrics=metrics,
        min_accuracy=min_accuracy,
    )
    result["comparison"] = compare
    if bool(result.get("ok")) and bool(result.get("promotion", {}).get("allowed")):
        telemetry.emit(
            "model.promoted",
            actor="training_pipeline",
            payload={"promotion": result.get("promotion"), "comparison": compare},
            risk_score=0.1,
            channel="system",
        )
    else:
        telemetry.emit(
            "model.promotion_blocked",
            actor="training_pipeline",
            payload={"reason": "promotion_policy_failed", "result": result, "comparison": compare},
            risk_score=0.2,
            channel="system",
        )
    return jsonify(result)


@app.post("/v1/training/compare")
def training_compare():
    payload = _json()
    min_accuracy = float(payload.get("min_shadow_accuracy") or 0.75)
    active_metrics = dict(payload.get("active_metrics") or {})
    shadow_metrics = dict(payload.get("shadow_metrics") or {})
    comparison = training.compare_shadow_vs_active_metrics(
        active_metrics=active_metrics,
        shadow_metrics=shadow_metrics,
        min_shadow_accuracy=min_accuracy,
    )
    return jsonify({"ok": True, "comparison": comparison})


@app.get("/v1/policies/effective")
def effective_policies():
    sid = str(request.args.get("session_id") or "").strip()
    return jsonify(
        {
            "ok": True,
            "global_kill_switch": _KILL_SWITCH["enabled"],
            "session_safe_mode": policy_engine.get_safe_mode(sid),
            "tiers": ["ALLOW_AUTO", "ALLOW_WITH_LOG", "REQUIRE_APPROVAL", "DENY"],
            "deny_zones": ["password vault paths", "banking/payment targets", "OS credential stores"],
        }
    )


@app.post("/v1/feedback")
def feedback():
    payload = _json()
    session_id = str(payload.get("session_id") or "").strip()
    task_id = str(payload.get("task_id") or "").strip()
    reason_code = str(payload.get("reason_code") or "").strip()
    rejected = str(payload.get("rejected_output") or "")
    preferred = str(payload.get("preferred_output") or "")
    channel = str(payload.get("channel") or "system")
    actor = str(payload.get("actor") or "user")
    if not reason_code:
        return jsonify({"ok": False, "error": "reason_code is required"}), 400
    correction_id = engine.record_correction(
        session_id=session_id,
        task_id=task_id,
        channel=channel,
        actor=actor,
        reason_code=reason_code,
        rejected_output=rejected,
        preferred_output=preferred,
        metadata={"source": "api_feedback"},
    )
    telemetry.emit(
        "training.example.emitted",
        session_id=session_id,
        task_id=task_id,
        actor=actor,
        payload={"source": "feedback", "correction_id": correction_id, "reason_code": reason_code},
        risk_score=0.1,
        channel=channel,
    )
    engine.recalc_core_features()
    return jsonify({"ok": True, "correction_id": correction_id})


@app.post("/v1/control/kill_switch")
def kill_switch():
    payload = _json()
    enabled = bool(payload.get("enabled"))
    _KILL_SWITCH["enabled"] = enabled
    telemetry.emit(
        "action.executed",
        actor="operator",
        payload={"kill_switch_enabled": enabled},
        channel="system",
    )
    return jsonify({"ok": True, "enabled": enabled, "updated_at": time.time()})


@app.post("/v1/control/safe_mode")
def safe_mode():
    payload = _json()
    session_id = str(payload.get("session_id") or "").strip()
    enabled = bool(payload.get("enabled"))
    if not session_id:
        return jsonify({"ok": False, "error": "session_id is required"}), 400
    policy_engine.set_safe_mode(session_id, enabled)
    telemetry.emit(
        "action.executed",
        session_id=session_id,
        actor="operator",
        payload={"safe_mode": enabled},
        channel="system",
    )
    return jsonify({"ok": True, "session_id": session_id, "safe_mode": enabled})


@app.post("/v1/vision/start")
def vision_start():
    _VISION["enabled"] = True
    _VISION["started_at"] = time.time()
    os.makedirs(os.path.dirname(_VISION_INDICATOR), exist_ok=True)
    try:
        with open(_VISION_INDICATOR, "w", encoding="utf-8") as f:
            f.write(f"VISION_MODE_ACTIVE {int(_VISION['started_at'])}\n")
    except Exception:
        pass
    return jsonify({"ok": True, "enabled": True, "started_at": _VISION["started_at"]})


@app.post("/v1/vision/stop")
def vision_stop():
    _VISION["enabled"] = False
    try:
        if os.path.exists(_VISION_INDICATOR):
            os.remove(_VISION_INDICATOR)
    except Exception:
        pass
    return jsonify({"ok": True, "enabled": False, "stopped_at": time.time()})


@app.post("/v1/macros")
def macros_create():
    payload = _json()
    name = str(payload.get("name") or "").strip()
    steps = list(payload.get("steps") or [])
    notes = str(payload.get("notes") or "")
    if not name:
        return jsonify({"ok": False, "error": "name is required"}), 400
    macro = _macros.create(name=name, steps=steps, notes=notes)
    return jsonify({"ok": True, "macro": {"name": macro.name, "steps": macro.steps, "notes": macro.notes}})


@app.get("/v1/macros")
def macros_list():
    return jsonify({"ok": True, "macros": _macros.list()})


@app.post("/v1/macros/run")
def macros_run():
    payload = _json()
    name = str(payload.get("name") or "").strip()
    result = _tool_exec_macro_run({"name": name})
    return jsonify({"ok": bool(result.get("ok")), "result": result}), (200 if result.get("ok") else 404)


@app.post("/v1/style/set")
def style_set():
    payload = _json()
    contact = str(payload.get("contact") or "").strip()
    if not contact:
        return jsonify({"ok": False, "error": "contact is required"}), 400
    prof = StyleProfile(
        contact=contact,
        tone=str(payload.get("tone") or "neutral"),
        greeting=str(payload.get("greeting") or ""),
        signoff=str(payload.get("signoff") or ""),
        brevity=str(payload.get("brevity") or "normal"),
        updated_at=time.time(),
    )
    _styles.set(prof)
    return jsonify({"ok": True, "profile": {"contact": contact}})


@app.get("/v1/style/list")
def style_list():
    return jsonify({"ok": True, "profiles": _styles.list()})


@app.get("/v1/audit/auth_blocks")
def audit_auth_blocks():
    try:
        limit = int(request.args.get("limit", "100"))
    except Exception:
        limit = 100
    try:
        since_sec = int(request.args.get("since_sec", "86400"))
    except Exception:
        since_sec = 86400
    reason_filter = str(request.args.get("reason") or "").strip().lower()
    now = time.time()
    cutoff = now - max(1, since_sec)
    rows = engine.list_recent_events(limit=max(1, min(2000, limit * 10)))
    out: list[dict[str, Any]] = []
    for e in rows:
        if str(e.get("event_type") or "") != "action.blocked":
            continue
        if str(e.get("actor") or "") != "api_gateway":
            continue
        if float(e.get("ts_utc") or 0.0) < cutoff:
            continue
        payload = e.get("payload_json") or {}
        reason = str(payload.get("reason") or "").lower()
        if reason_filter and reason != reason_filter:
            continue
        out.append(
            {
                "event_id": e.get("event_id"),
                "ts_utc": e.get("ts_utc"),
                "reason": payload.get("reason"),
                "status_code": payload.get("status_code"),
                "path": payload.get("path"),
                "method": payload.get("method"),
                "client_ip": payload.get("client_ip"),
            }
        )
        if len(out) >= limit:
            break
    return jsonify({"ok": True, "count": len(out), "items": out})


@app.get("/v1/audit/promotions")
def audit_promotions():
    registry_path = str(request.args.get("registry_path") or _AUDIT_REGISTRY_PATH).strip()
    registry: dict[str, Any] = {"active_checkpoint": None, "shadow_checkpoint": None, "history": []}
    if os.path.exists(registry_path):
        try:
            with open(registry_path, "r", encoding="utf-8") as f:
                registry = json.load(f)
        except Exception:
            registry = {"active_checkpoint": None, "shadow_checkpoint": None, "history": []}
    return jsonify(
        {
            "ok": True,
            "registry_path": registry_path,
            "active_checkpoint": registry.get("active_checkpoint"),
            "shadow_checkpoint": registry.get("shadow_checkpoint"),
            "history_count": len(list(registry.get("history") or [])),
            "history": list(registry.get("history") or []),
        }
    )


if __name__ == "__main__":
    port = int(os.getenv("VICTOR_API_PORT", "8787"))
    app.run(host="127.0.0.1", port=port, debug=False)
