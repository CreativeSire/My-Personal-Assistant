import telebot
import os
import sys
import json
import re
import sqlite3
import glob
import tempfile
import time
from typing import Any
from tendo import singleton

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from config import get_config, validate_or_raise
from logging_config import get_logger, setup_logging, new_correlation_id
from session_manager import get_session_service, resolve_user_id, resolve_session_id
from resilience import telegram_breaker, gemini_breaker

cfg = validate_or_raise(get_config())
setup_logging(cfg.log_dir)
logger = get_logger("telegram_server")

BASE_DIR = cfg.base_dir
MEMORY_DB_PATH = cfg.memory_db_path
WORKSPACE_DIR = cfg.workspace_dir
MEMORY_V3_ENABLED = cfg.memory_v3_enabled


def _resolve_runtime_path(path_text):
    """Resolve legacy relative paths like 'victor_os/workspace/x' to this repo."""
    if not path_text:
        return path_text
    path_text = path_text.strip()
    normalized = path_text.replace("\\", "/")
    if normalized.startswith("victor_os/"):
        suffix = normalized[len("victor_os/"):]
        return os.path.join(BASE_DIR, suffix.replace("/", os.sep))
    if os.path.isabs(path_text):
        return path_text
    return os.path.join(BASE_DIR, path_text)

def _has_live_telegram_process() -> bool:
    try:
        import psutil

        current = os.getpid()
        for proc in psutil.process_iter(["pid", "cmdline", "name"]):
            if proc.info.get("pid") == current:
                continue
            cmd = " ".join(proc.info.get("cmdline") or [])
            if "telegram_server.py" in cmd:
                return True
    except Exception:
        return True
    return False


def _cleanup_stale_singleton_lock():
    temp_dir = tempfile.gettempdir()
    patterns = [
        os.path.join(temp_dir, "*telegram_server-.lock"),
        os.path.join(temp_dir, "*telegram_server*lock*"),
    ]
    for pattern in patterns:
        for path in glob.glob(pattern):
            try:
                os.remove(path)
            except Exception:
                continue


# --- SINGLETON LOCK (Prevent Duplicate Instances) ---
try:
    me = singleton.SingleInstance()
except singleton.SingleInstanceException:
    if not _has_live_telegram_process():
        _cleanup_stale_singleton_lock()
        try:
            me = singleton.SingleInstance()
        except singleton.SingleInstanceException:
            logger.warning("Singleton lock detected and could not be recovered. Exiting.")
            sys.exit(0)
    else:
        logger.warning("Singleton lock — another instance running. Exiting.")
        sys.exit(0)

logger.info("Telegram server starting up")

from google.adk import Runner
from google.genai import types
from agents import chief_of_staff, get_skill_registry
from tools import transcribe_audio_file, generate_tts_file, send_email_alert
from monitor import log_activity
from memory_core import MemoryQueryInput, MemoryRecordInput, recall_memory, save_memory
from memory_policy import classify_memory_candidate, redact_sensitive
from sitrep_builder import build_executive_sitrep

# --- CONFIGURATION ---
BOT_TOKEN = cfg.telegram_bot_token

logger.info("Connecting to Telegram...")
bot = telebot.TeleBot(BOT_TOKEN)
session_service = get_session_service()

# Initialize Runner
runner = Runner(
    agent=chief_of_staff,
    session_service=session_service,
    app_name="victor_os",
    auto_create_session=True
)

logger.info(f"Victor-OS (Multimodal Mode) is Online! (Bot ID: {BOT_TOKEN.split(':')[0]})")

# --- START TASK QUEUE & PROACTIVE ENGINE ---
from task_queue import TaskQueue

_task_queue = TaskQueue()

def _extract_send_file_marker(text: str) -> tuple[str | None, str]:
    msg = str(text or "")
    file_match = re.search(r"<<SEND_FILE:\s*(.*?)>>", msg)
    if not file_match:
        return None, msg.strip()
    raw_path = file_match.group(1).strip()
    clean_message = msg.replace(file_match.group(0), "").strip()
    return raw_path, clean_message


def _notify_user_telegram(user_id, channel, message):
    """Push task/proactive notifications to Telegram."""
    try:
        user_target = str(user_id or "").strip()
        target = cfg.telegram_target_id
        if channel == "telegram" and user_target.isdigit():
            target = user_target
        if not target:
            return
        raw_file, clean_message = _extract_send_file_marker(str(message))
        if clean_message:
            telegram_breaker.call(bot.send_message, target, clean_message)
        if raw_file:
            resolved_path = _resolve_runtime_path(raw_file)
            if os.path.exists(resolved_path):
                sent = False
                send_error = None
                for attempt in range(3):
                    try:
                        with open(resolved_path, "rb") as doc:
                            telegram_breaker.call(bot.send_document, target, doc)
                        sent = True
                        break
                    except Exception as e:
                        send_error = e
                        time.sleep(1.0 + attempt)
                if not sent:
                    size_mb = os.path.getsize(resolved_path) / (1024 * 1024)
                    fallback = (
                        "Output file is ready but Telegram upload failed after retries.\n"
                        f"File: {os.path.basename(resolved_path)} ({size_mb:.2f} MB)\n"
                        f"Local path: {resolved_path}\n"
                        f"Error: {send_error}"
                    )
                    telegram_breaker.call(bot.send_message, target, fallback)
            else:
                telegram_breaker.call(bot.send_message, target, f"Courier Error: File not found at {raw_file}")
    except Exception as e:
        logger.error(f"Notification push failed: {e}")

def _notify_user_email(user_id, channel, message):
    """Email-only proactive notifier for serious alerts."""
    try:
        subject = "Critical Victor-OS Alert"
        body = (
            f"Channel: {channel}\n"
            f"User: {user_id}\n\n"
            f"{message}"
        )
        send_email_alert(subject, body)
    except Exception as e:
        logger.error(f"Email notification failed: {e}")


def _notify_user_whatsapp(user_id, message):
    """Optional WhatsApp fanout via Twilio REST if configured."""
    if not (cfg.whatsapp_enabled and cfg.twilio_sid and cfg.twilio_auth_token and cfg.twilio_whatsapp_number):
        return
    try:
        from twilio.rest import Client
        target = str(user_id or "").strip()
        if not target.startswith("whatsapp:"):
            target = f"whatsapp:{target}"
        client = Client(cfg.twilio_sid, cfg.twilio_auth_token)
        client.messages.create(
            body=message,
            from_=cfg.twilio_whatsapp_number,
            to=target,
        )
    except Exception as e:
        logger.warning(f"WhatsApp fanout failed: {e}")

try:
    from invoice_pipeline import run_invoice_job
    _task_queue.register_handler("invoice_job", run_invoice_job)
except Exception as e:
    logger.warning(f"Invoice pipeline handler unavailable: {e}")

_task_queue.set_notify_callback(_notify_user_telegram)
_task_queue.start_worker()

# Start proactive engine if skills have checks
try:
    from proactive_engine import ProactiveEngine
    _skill_registry = get_skill_registry()
    if _skill_registry and cfg.proactive_enabled:
        _proactive = ProactiveEngine()
        _proactive.register_checks_from_registry(_skill_registry.get_proactive_checks())

        # Register memory TTL cleanup as a proactive check
        try:
            from memory_cleanup import get_proactive_check as _get_mem_cleanup_check
            _proactive.register_checks_from_registry([_get_mem_cleanup_check()])
            logger.info("Memory TTL cleanup registered with proactive engine (hourly)")
        except Exception as _mc_err:
            logger.warning(f"Memory cleanup proactive check not registered: {_mc_err}")

        _proactive.set_notify_callback(_notify_user_telegram)
        _proactive.add_channel_notifier("telegram", lambda user_id, message: _notify_user_telegram(user_id, "telegram", message))
        _proactive.add_channel_notifier("email", lambda user_id, message: _notify_user_email(user_id, "email", message))
        _proactive.start()
        logger.info("Proactive engine started with telegram + email channels")
except Exception as e:
    logger.warning(f"Proactive engine not started: {e}")

# Start workflow scheduler for trigger-based workflows
try:
    from workflow_engine import WorkflowEngine, WorkflowScheduler
    _wf_engine = WorkflowEngine()
    _wf_engine.load_definitions_from_dir()

    # Register daily briefing action handlers so ops_daily_brief.json runs via scheduler
    try:
        from skills.ops_health_check import OpsHealthCheckSkill as _OpsSkill
        from skills.market_watch import MarketWatchSkill as _MarketSkill
        from skills.memory_hygiene import MemoryHygieneSkill as _MemHygieneSkill

        _ops_skill = _OpsSkill()
        _market_skill = _MarketSkill()
        _mem_hygiene_skill = _MemHygieneSkill()

        _wf_engine.register_action("ops_health_summary", lambda params, ctx: _ops_skill.tool_ops_health_summary())
        _wf_engine.register_action("market_snapshot", lambda params, ctx: _market_skill.tool_market_snapshot())
        _wf_engine.register_action("memory_hygiene", lambda params, ctx: _mem_hygiene_skill.tool_memory_hygiene_report())

        def _compose_daily_report(params, ctx):
            """Run Chief through ADK to compose the daily briefing."""
            _brief_msg = types.Content(role="user", parts=[types.Part(text=(
                "MORNING BRIEFING TASK:\n"
                "1. Research the current price of Bitcoin and Ethereum.\n"
                "2. Find one major AI headline in the last 24 hours.\n"
                "3. Find one key business headline from Lagos, Nigeria.\n"
                "Return a concise executive report."
            ))])
            _chunks = []
            for _ev in gemini_breaker.call(runner.run, user_id="briefing_system", session_id="daily_briefing_session", new_message=_brief_msg):
                _chunks.extend(_extract_text_from_node(_ev))
            return "Victor-OS Morning Briefing\n\n" + ("\n".join(_unique_text_chunks(_chunks)) or "Morning briefing returned no content.")

        _wf_engine.register_action("compose_daily_ops_report", _compose_daily_report)
        logger.info("Daily briefing action handlers registered with workflow scheduler")
    except Exception as _db_err:
        logger.warning(f"Daily briefing action handlers not registered: {_db_err}")

    # Register action handlers for goal review workflow
    try:
        from goal_tracker import get_goal_tracker as _get_gt
        _gt = _get_gt()
        _wf_engine.register_action("list_active_goals", lambda params, ctx: "\n".join(
            f"- [{g.status}] {g.title} (P{g.priority}, {g.progress}%)" for g in _gt.list_goals(status_filter="active")
        ) or "No active goals.")
        _wf_engine.register_action("check_overdue_goals", lambda params, ctx: "\n".join(
            f"- OVERDUE: {g.title} (P{g.priority})" for g in _gt.get_overdue_goals()
        ) or "No overdue goals.")
        _wf_engine.register_action("format_goal_review", lambda params, ctx: (
            "Weekly Goal Review\n\n"
            f"Active Goals:\n{ctx.get('step_list_active_result', 'None')}\n\n"
            f"Overdue:\n{ctx.get('step_check_overdue_result', 'None')}"
        ))
    except Exception as e:
        logger.debug(f"Goal workflow actions not registered: {e}")

    # Register notify action
    _wf_engine.register_action("notify", lambda params, ctx: str(params.get("message", "Notification sent.")))

    _wf_scheduler = WorkflowScheduler(
        engine=_wf_engine,
        notify_callback=_notify_user_telegram,
    )
    _wf_scheduler.start()
except Exception as e:
    logger.warning(f"Workflow scheduler not started: {e}")


@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "Victor-OS is Online. I can read your texts, hear your voice, and analyze your photos!")

# --- SAFETY VALVE (Auto-Splitter) ---
def send_smart_message(chat_id, text, reply_to_id=None):
    """Splits messages longer than 4000 chars and sends them in parts."""
    MAX_LENGTH = 4000
    if len(text) <= MAX_LENGTH:
        if reply_to_id:
            return telegram_breaker.call(bot.reply_to, reply_to_id, text)
        else:
            return telegram_breaker.call(bot.send_message, chat_id, text)

    chunks = [text[i:i + MAX_LENGTH] for i in range(0, len(text), MAX_LENGTH)]
    total = len(chunks)

    for i, chunk in enumerate(chunks):
        formatted_chunk = f"(Part {i+1}/{total})\n\n{chunk}"
        if i == 0 and reply_to_id:
            telegram_breaker.call(bot.reply_to, reply_to_id, formatted_chunk)
        else:
            telegram_breaker.call(bot.send_message, chat_id, formatted_chunk)
    return True


def _extract_text_from_node(node, seen=None, from_text_field=False):
    """Recursively extract all text-like fields from mixed event payloads."""
    if seen is None:
        seen = set()

    chunks = []
    node_id = id(node)
    if node_id in seen:
        return chunks
    seen.add(node_id)

    if node is None:
        return chunks

    if isinstance(node, str):
        text = node.strip()
        if from_text_field and text:
            chunks.append(text)
        return chunks

    if isinstance(node, dict):
        for key, value in node.items():
            key_lower = str(key).lower()
            is_text_key = key_lower in {"text", "output_text", "response_text", "final_text"}
            chunks.extend(_extract_text_from_node(value, seen, from_text_field=is_text_key))
        return chunks

    if isinstance(node, (list, tuple, set)):
        for item in node:
            chunks.extend(_extract_text_from_node(item, seen, from_text_field=from_text_field))
        return chunks

    if hasattr(node, "text") and isinstance(getattr(node, "text"), str):
        text = node.text.strip()
        if text:
            chunks.append(text)

    if hasattr(node, "model_dump"):
        try:
            dumped = node.model_dump(exclude_none=True)
            chunks.extend(_extract_text_from_node(dumped, seen, from_text_field=from_text_field))
            return chunks
        except Exception:
            pass

    if hasattr(node, "__dict__"):
        try:
            chunks.extend(_extract_text_from_node(vars(node), seen, from_text_field=from_text_field))
        except Exception:
            pass

    return chunks


def _extract_tool_names_from_node(node, seen=None):
    """Recursively detect tool/function call names from mixed event payloads."""
    if seen is None:
        seen = set()

    names = []
    node_id = id(node)
    if node_id in seen:
        return names
    seen.add(node_id)

    if node is None:
        return names

    if isinstance(node, dict):
        if "tool_calls" in node and isinstance(node["tool_calls"], list):
            for call in node["tool_calls"]:
                if isinstance(call, dict):
                    fn_name = ""
                    if isinstance(call.get("name"), str):
                        fn_name = call["name"].strip()
                    elif isinstance(call.get("function"), dict) and isinstance(call["function"].get("name"), str):
                        fn_name = call["function"]["name"].strip()
                    if fn_name:
                        names.append(fn_name)
        if "function_call" in node and isinstance(node["function_call"], dict):
            fn_name = node["function_call"].get("name")
            if isinstance(fn_name, str) and fn_name.strip():
                names.append(fn_name.strip())
        if "function_calls" in node and isinstance(node["function_calls"], list):
            for call in node["function_calls"]:
                if isinstance(call, dict):
                    fn_name = call.get("name")
                    if isinstance(fn_name, str) and fn_name.strip():
                        names.append(fn_name.strip())

        for value in node.values():
            names.extend(_extract_tool_names_from_node(value, seen))
        return names

    if isinstance(node, (list, tuple, set)):
        for item in node:
            names.extend(_extract_tool_names_from_node(item, seen))
        return names

    if hasattr(node, "tool_calls") and getattr(node, "tool_calls"):
        tool_calls = getattr(node, "tool_calls")
        if isinstance(tool_calls, list):
            for call in tool_calls:
                fn_name = getattr(call, "name", None)
                if isinstance(fn_name, str) and fn_name.strip():
                    names.append(fn_name.strip())
                fn_obj = getattr(call, "function", None)
                fn_obj_name = getattr(fn_obj, "name", None)
                if isinstance(fn_obj_name, str) and fn_obj_name.strip():
                    names.append(fn_obj_name.strip())

    if hasattr(node, "function_call") and getattr(node, "function_call"):
        fn_call = getattr(node, "function_call")
        fn_name = getattr(fn_call, "name", None)
        if isinstance(fn_name, str) and fn_name.strip():
            names.append(fn_name.strip())

    if hasattr(node, "model_dump"):
        try:
            dumped = node.model_dump(exclude_none=True)
            names.extend(_extract_tool_names_from_node(dumped, seen))
            return names
        except Exception:
            pass

    if hasattr(node, "__dict__"):
        try:
            names.extend(_extract_tool_names_from_node(vars(node), seen))
        except Exception:
            pass

    return names


def _unique_text_chunks(chunks):
    deduped = []
    seen = set()
    for chunk in chunks:
        key = chunk.strip()
        if key and key not in seen:
            seen.add(key)
            deduped.append(key)
    return deduped


def _sanitize_history_line(role, text):
    """Drop known persona-breaking assistant outputs from injected memory history."""
    if role != "model":
        return text

    lowered = text.lower()
    blocked = [
        "i am a large language model",
        "as an ai",
        "i do not retain information about our past conversations",
        "i don't retain information from past conversations",
    ]
    for phrase in blocked:
        if phrase in lowered:
            return ""
    return text


def _strip_injected_context(text: str) -> str:
    """Remove our injected memory wrapper to prevent recursive prompt explosion."""
    if not text:
        return ""
    idx = text.rfind("USER:")
    if idx >= 0:
        text = text[idx + len("USER:") :]
    text = text.replace("RULE: Use these contexts. Do not claim lack of memory without checking these blocks.", "")
    text = re.sub(r"=== MEMORY FABRIC V3 ===.*?========================", "", text, flags=re.DOTALL)
    return text.strip()


def _compact_text(text: str, max_chars: int = 360) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text[:max_chars]


def _sanitize_model_reply(text: str) -> str:
    """Guardrail in runtime output in case delegated agents break persona."""
    if not text:
        return text
    lowered = text.lower()
    blocked = [
        "as a large language model",
        "i do not possess memory in the human sense",
        "i don't store information from previous conversations",
        "i do not have any persistent memory",
    ]
    if any(b in lowered for b in blocked):
        return (
            "I maintain continuity using your active session context and long-term vault records. "
            "If you want, I can give you an immediate executive sitrep of what I currently retain."
        )
    return text


def _is_transient_network_error(exc: Exception) -> bool:
    lowered = repr(exc).lower()
    return any(
        marker in lowered
        for marker in [
            "connectionreseterror",
            "connection aborted",
            "winerror 10054",
            "timeout",
            "timed out",
            "temporarily unavailable",
        ]
    )


def _build_session_context(session_ids: list[str], limit: int = 10) -> str:
    history_context = ""
    try:
        conn = sqlite3.connect(MEMORY_DB_PATH)
        cursor = conn.cursor()
        placeholders = ",".join("?" * len(session_ids))
        params = list(session_ids) + [limit * 4]
        cursor.execute(
            f"SELECT event_data FROM events WHERE session_id IN ({placeholders}) ORDER BY timestamp DESC LIMIT ?",
            params,
        )
        rows = cursor.fetchall()
        messages = []
        for row in rows:
            try:
                event_json = row[0]
                data = json.loads(event_json)
                if "content" in data and "parts" in data["content"]:
                    role = data["content"]["role"]
                    text = ""
                    for part in data["content"]["parts"]:
                        if "text" in part:
                            text += part["text"]
                    if role == "user":
                        text = _strip_injected_context(text)
                    text = _sanitize_history_line(role, text)
                    if text:
                        label = "User" if role == "user" else "Victor"
                        messages.append(f"{label}: {_compact_text(text)}")
            except Exception:
                continue
        conn.close()
        if messages:
            history_str = "\n".join(reversed(messages[-limit:]))
            history_context = (
                "SESSION_CONTEXT:\n"
                "Use this recent chat history for continuity. If useful facts exist here, do not claim amnesia.\n"
                f"[HISTORY LOG]\n{history_str}\n"
            )
    except Exception as e:
        logger.warning(f"Session context failed: {e}")
    return history_context


def _memory_rank(hit: dict[str, Any]) -> tuple[int, float, int]:
    order = {
        "constraint": 1,
        "task": 2,
        "decision": 3,
        "preference": 4,
    }
    mtype = str(hit.get("memory_type", "other")).lower()
    return (order.get(mtype, 5), -float(hit.get("similarity", 0.0)), -int(hit.get("priority", 3)))


def _format_hits_block(title: str, hits: list[dict[str, Any]]) -> str:
    if not hits:
        return f"{title}:\n- No relevant entries.\n"
    ranked = sorted(hits, key=_memory_rank)
    lines = []
    for hit in ranked[:10]:
        scope = hit.get("scope", "global")
        mtype = hit.get("memory_type", "other")
        content = str(hit.get("content_plain", "")).strip()
        if content:
            lines.append(f"- [{mtype}/{scope}] {content}")
    if not lines:
        lines = ["- No relevant entries."]
    return f"{title}:\n" + "\n".join(lines) + "\n"


def _build_memory_context(query: str) -> dict[str, Any]:
    global_res = recall_memory(
        MemoryQueryInput(
            query=query,
            scope_filter="global",
            top_k=10,
            min_similarity=0.2,
        )
    )
    agent_res = recall_memory(
        MemoryQueryInput(
            query=query,
            scope_filter="agent",
            agent_filter="Chief_of_Staff",
            top_k=8,
            min_similarity=0.2,
        )
    )
    global_hits = global_res.get("results", []) if global_res.get("ok") else []
    agent_hits = agent_res.get("results", []) if agent_res.get("ok") else []
    return {"global_hits": global_hits, "agent_hits": agent_hits}


def _extract_deadlines(memories: list[dict[str, Any]]) -> list[str]:
    deadline_re = re.compile(r"\b(\d{4}-\d{2}-\d{2}|due\b.*|deadline\b.*|by\s+\w+\s+\d{1,2})", re.IGNORECASE)
    deadlines = []
    for item in memories:
        text = str(item.get("content_plain", ""))
        found = deadline_re.findall(text)
        for part in found:
            entry = text.strip()
            if entry and entry not in deadlines:
                deadlines.append(entry)
    return deadlines[:6]


def _build_sitrep_report(user_id: str, query: str) -> str:
    memory = _build_memory_context(query)
    g_hits = memory["global_hits"]
    a_hits = memory["agent_hits"]
    all_hits = g_hits + a_hits

    by_type: dict[str, list[str]] = {}
    for hit in all_hits:
        mtype = str(hit.get("memory_type", "other")).lower()
        by_type.setdefault(mtype, [])
        text = str(hit.get("content_plain", "")).strip()
        if text and text not in by_type[mtype]:
            by_type[mtype].append(text)

    mission = by_type.get("constraint", [])[:3] + by_type.get("project_fact", [])[:2]
    projects = by_type.get("project_fact", [])[:5] + by_type.get("task", [])[:2]
    decisions = by_type.get("decision", [])[:5]
    blockers = [x for x in by_type.get("task", []) if re.search(r"\b(block|risk|issue|stuck|pending)\b", x, re.IGNORECASE)][:5]
    deadlines = _extract_deadlines(all_hits)
    next_actions = by_type.get("task", [])[:5]

    return build_executive_sitrep(mission, projects, decisions, blockers, deadlines, next_actions)


def _save_deterministic_memories(user_text: str, final_answer: str, tool_names: list[str], delegated_agents: list[str]):
    if not MEMORY_V3_ENABLED:
        return
    tool_ctx = {"tool_names": tool_names, "delegated_agents": delegated_agents}
    candidates = classify_memory_candidate(user_text, final_answer, tool_ctx)
    for candidate in candidates:
        redacted_text, sensitive_refs = redact_sensitive(candidate.get("text", ""))
        record = MemoryRecordInput(
            text=redacted_text,
            memory_type=candidate.get("memory_type", "other"),
            scope=candidate.get("scope", "global"),
            agent_name=candidate.get("agent_name"),
            priority=int(candidate.get("priority", 3)),
            source=candidate.get("source", "telegram"),
            tags=candidate.get("tags", []),
            sensitive_refs=sensitive_refs,
        )
        result = save_memory(record)
        logger.debug(
            f"MEMORY_SAVE: status={result.get('status')} id={result.get('id')} type={record.memory_type}",
            extra={"agent_name": "memory_v3"},
        )

    for agent_name in delegated_agents:
        handoff = MemoryRecordInput(
            text=f"Handoff observed: Chief_of_Staff delegated work to {agent_name}.",
            memory_type="status",
            scope="agent",
            agent_name=agent_name,
            priority=3,
            source="telegram_handoff",
            tags=["handoff"],
        )
        save_memory(handoff)

# --- VOICE HANDLER ---
@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    cid = new_correlation_id()
    user_id = str(message.chat.id)
    logger.info(f"Voice note from {message.from_user.first_name}", extra={"channel": "telegram", "user_id": user_id})
    bot.send_chat_action(message.chat.id, 'record_audio')

    try:
        log_activity("Telegram User", "Voice Note Received", "Info")
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)

        voice_path = f"voice_{message.chat.id}.ogg"
        with open(voice_path, 'wb') as new_file:
            new_file.write(downloaded_file)

        user_text = transcribe_audio_file(voice_path)

        if not user_text or user_text.startswith("Transcription Error"):
            bot.reply_to(message, "Sorry, I couldn't understand that audio.")
            return

        logger.info(f"Transcribed: '{user_text}'", extra={"channel": "telegram", "user_id": user_id})
        process_message(message, user_text)

        if os.path.exists(voice_path): os.remove(voice_path)
        wav_path = voice_path.replace(".ogg", ".wav")
        if os.path.exists(wav_path): os.remove(wav_path)

    except Exception as e:
        logger.error(f"Voice error: {e}", extra={"channel": "telegram", "user_id": user_id})
        bot.reply_to(message, f"Voice System Error: {e}")

# --- PHOTO HANDLER ---
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    cid = new_correlation_id()
    user_id = str(message.chat.id)
    caption = message.caption if message.caption else "Analyze this image."
    logger.info(f"Photo from {message.from_user.first_name}: {caption}", extra={"channel": "telegram", "user_id": user_id})
    bot.send_chat_action(message.chat.id, 'upload_photo')

    try:
        log_activity("Telegram User", "Photo Received", "Info", f"Caption: {caption}")
        file_info = bot.get_file(message.photo[-1].file_id)
        image_data = bot.download_file(file_info.file_path)
        process_message(message, caption, image_data=image_data)

    except Exception as e:
        logger.error(f"Photo error: {e}", extra={"channel": "telegram", "user_id": user_id})
        bot.reply_to(message, f"Vision System Error: {e}")

# --- DOCUMENT HANDLER ---
@bot.message_handler(content_types=['document'])
def handle_document(message):
    cid = new_correlation_id()
    user_id = str(message.chat.id)
    file_name = message.document.file_name
    logger.info(f"Document from {message.from_user.first_name}: {file_name}", extra={"channel": "telegram", "user_id": user_id})
    bot.send_chat_action(message.chat.id, 'upload_document')

    try:
        log_activity("Telegram User", "Document Received", "Info", f"File: {file_name}")
        os.makedirs(WORKSPACE_DIR, exist_ok=True)

        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)

        file_path = os.path.join(WORKSPACE_DIR, file_name)
        with open(file_path, 'wb') as new_file:
            new_file.write(downloaded_file)

        file_ext = os.path.splitext(file_name)[1].lower().lstrip(".")
        invoice_mode = cfg.invoice_job_enabled and (
            file_ext == "zip" or file_ext in set(cfg.invoice_allowed_exts)
        )
        if invoice_mode:
            from invoice_pipeline import enqueue_invoice_job
            task_id = enqueue_invoice_job(file_path, channel="telegram", user_id=user_id)
            bot.reply_to(
                message,
                (
                    f"Invoice job queued (ID: {task_id}). "
                    "I will return a zipped output with artifacts/OK and artifacts/Review when complete."
                ),
            )
            return

        system_note = f"User uploaded a file available at: {file_path}. Use Python to read/analyze it if requested."
        user_text = message.caption if message.caption else f"I've uploaded {file_name}."
        combined_text = f"{user_text}\n\n[SYSTEM NOTE: {system_note}]"

        process_message(message, combined_text)

    except Exception as e:
        logger.error(f"Document error: {e}", extra={"channel": "telegram", "user_id": user_id})
        bot.reply_to(message, f"File System Error: {e}")

# --- TEXT HANDLER ---
@bot.message_handler(func=lambda message: True)
def handle_text(message):
    process_message(message, message.text)

def process_message(message, user_text, image_data=None):
    cid = new_correlation_id()
    user_id = str(message.chat.id)
    logger.info(f"Processing: {user_text[:80]}{'...' if len(user_text) > 80 else ''} {'(with image)' if image_data else ''}", extra={"channel": "telegram", "user_id": user_id})
    try:
        telegram_breaker.call(bot.send_chat_action, message.chat.id, 'typing')
    except Exception as e:
        logger.warning(f"Typing indicator failed: {e}", extra={"channel": "telegram", "user_id": user_id})
    log_activity("Telegram User", "Input Received", "Info", f"Text: {user_text[:50]}...")

    try:
        lower_text = (user_text or "").strip().lower()
        folder_intent = (
            ("folder" in lower_text or "folders" in lower_text)
            and any(k in lower_text for k in ["can you", "do you", "take", "accept", "support", "process"])
        )
        if folder_intent and cfg.invoice_job_enabled:
            quick_reply = (
                "Yes. I support folder processing in zip mode.\n"
                "Send one .zip file containing your documents and I will process it as a background invoice job.\n"
                "You will receive an output zip with `artifacts/OK`, `artifacts/Review`, `summary.json`, "
                "`review_items.json`, and `run.log`."
            )
            send_smart_message(message.chat.id, quick_reply, reply_to_id=message)
            return

        # 1. Prepare Content Object
        resolved_sid = resolve_session_id("telegram", user_id)
        history_context = _build_session_context([resolved_sid, user_id])
        memory_context = _build_memory_context(user_text)
        global_block = _format_hits_block("GLOBAL_CONTEXT", memory_context["global_hits"])
        agent_block = _format_hits_block("AGENT_CONTEXT", memory_context["agent_hits"])
        logger.debug(f"Memory context loaded: global={len(memory_context['global_hits'])}, agent={len(memory_context['agent_hits'])}")

        sitrep_requested = bool(re.search(r"\b(sitrep|situation report|status report)\b", user_text.lower()))
        if MEMORY_V3_ENABLED and sitrep_requested:
            final_answer = _build_sitrep_report(user_id, user_text)
            send_smart_message(message.chat.id, final_answer, reply_to_id=message)
            _save_deterministic_memories(user_text, final_answer, [], [])
            log_activity("Chief_of_Staff", "Sitrep Sent", "Success")
            return

        full_system_block = (
            "=== MEMORY FABRIC V3 ===\n"
            f"{global_block}\n"
            f"{agent_block}\n"
            f"{history_context}\n"
            "RULE: Use these contexts. Do not claim lack of memory without checking these blocks.\n"
            "========================\n\n"
        )
        full_prompt = full_system_block + "USER: " + user_text

        parts = [types.Part(text=full_prompt)]
        if image_data:
            parts.append(types.Part.from_bytes(data=image_data, mime_type="image/jpeg"))

        new_message = types.Content(
            role="user",
            parts=parts
        )

        # 2. Start the Stream
        resolved_uid = resolve_user_id("telegram", user_id)

        final_answer = ""
        tool_names = []
        delegated_agents = []
        stream_error = None
        for attempt in range(2):
            try:
                event_stream = gemini_breaker.call(
                    runner.run,
                    user_id=resolved_uid,
                    session_id=resolved_sid,
                    new_message=new_message
                )
                for event in event_stream:
                    detected_tools = _extract_tool_names_from_node(event)
                    if detected_tools:
                        tool_names.extend(detected_tools)
                    author_name = getattr(event, "author", None)
                    if isinstance(author_name, str) and author_name and author_name != "Chief_of_Staff":
                        delegated_agents.append(author_name)

                    text_chunks = _unique_text_chunks(_extract_text_from_node(event))
                    if text_chunks:
                        final_answer += "".join(text_chunks)
                stream_error = None
                break
            except Exception as e:
                stream_error = e
                if attempt == 0 and _is_transient_network_error(e):
                    logger.warning(
                        f"Transient stream error; retrying once: {e}",
                        extra={"channel": "telegram", "user_id": user_id},
                    )
                    time.sleep(1.0)
                    continue
                logger.error(f"Event loop failed: {e}", extra={"channel": "telegram", "user_id": user_id})
                break

        # 3. Decision: Text or Voice Reply?
        should_speak = "speak" in user_text.lower() or "say" in user_text.lower()

        # 4. Final Fallback Logic
        unique_tools = _unique_text_chunks(tool_names)
        delegated_agents = _unique_text_chunks(delegated_agents)
        if not final_answer.strip():
            if stream_error is not None:
                final_answer = "I hit a temporary network issue while processing your request. Please resend once."
            elif unique_tools:
                tool_summary = ", ".join(unique_tools[:3])
                final_answer = f"Completed your request using: {tool_summary}. If you want, I can provide a concise result summary next."
            else:
                final_answer = "I received your input, but I was unable to generate a response. Please check the system logs."

        final_answer = _sanitize_model_reply(final_answer)

        # --- CRITIC LAYER ---
        if cfg.critic_enabled:
            try:
                from critic import get_critic
                verdict = get_critic().evaluate(user_text, final_answer)
                if verdict.revised_response:
                    logger.info(
                        f"Critic revised response: score={verdict.score}, issues={verdict.issues}",
                        extra={"channel": "telegram", "user_id": user_id},
                    )
                    final_answer = verdict.revised_response
                else:
                    logger.debug(
                        f"Critic approved: score={verdict.score}",
                        extra={"channel": "telegram", "user_id": user_id},
                    )
            except Exception as e:
                logger.warning(f"Critic evaluation failed (fail-open): {e}")

        _save_deterministic_memories(user_text, final_answer, unique_tools, delegated_agents)

        # --- GOAL AUTO-DETECTION ---
        if cfg.goals_auto_detect:
            try:
                from goal_tracker import get_goal_tracker
                _goal_tracker = get_goal_tracker()
                candidates = _goal_tracker.detect_goals_from_text(user_text, final_answer)
                for candidate in candidates:
                    gid = _goal_tracker.create_goal(
                        title=candidate.title,
                        description=candidate.description,
                        priority=candidate.priority,
                        source="auto_detected",
                    )
                    logger.info(f"Auto-detected goal: {gid} '{candidate.title}'", extra={"channel": "telegram"})
            except Exception as e:
                logger.warning(f"Goal auto-detection failed: {e}")

        if should_speak:
            logger.info("Generating voice response", extra={"channel": "telegram", "user_id": user_id})
            audio_path = generate_tts_file(final_answer, f"reply_{user_id}.mp3")
            if not audio_path.startswith("TTS Generation Error"):
                with open(audio_path, 'rb') as audio:
                    telegram_breaker.call(bot.send_voice, message.chat.id, audio)
                os.remove(audio_path)
                log_activity("Chief_of_Staff", "Voice Response Sent", "Success")
            else:
                send_smart_message(message.chat.id, final_answer, reply_to_id=message)
                log_activity("Chief_of_Staff", "Text Response Sent (Voice Failed)", "Success")
        else:
            # --- COURIER PROTOCOL (Auto-Upload) ---
            file_match = re.search(r"<<SEND_FILE:\s*(.*?)>>", final_answer)

            if file_match:
                raw_path = file_match.group(1).strip()
                resolved_path = _resolve_runtime_path(raw_path)
                clean_answer = final_answer.replace(file_match.group(0), "").strip()

                if clean_answer:
                    send_smart_message(message.chat.id, clean_answer, reply_to_id=message)

                logger.info(f"COURIER: Shipping {resolved_path}", extra={"channel": "telegram"})
                if os.path.exists(resolved_path):
                    with open(resolved_path, 'rb') as doc:
                        telegram_breaker.call(bot.send_document, message.chat.id, doc)
                    log_activity("Chief_of_Staff", "Courier Successful", "Success", f"File: {resolved_path}")
                else:
                    send_smart_message(message.chat.id, f"Courier Error: File not found at {raw_path}", reply_to_id=message)
                    log_activity("Chief_of_Staff", "Courier Failed", "Error", f"Missing: {resolved_path}")
            else:
                send_smart_message(message.chat.id, final_answer, reply_to_id=message)
                log_activity("Chief_of_Staff", "Text Response Sent", "Success")

    except Exception as e:
        logger.error(f"Processing error: {e}", extra={"channel": "telegram", "user_id": user_id})
        send_smart_message(
            message.chat.id,
            "I hit a temporary processing issue. Please resend your last message.",
            reply_to_id=message,
        )
        log_activity("System", "Error", "Failed", str(e))

bot.infinity_polling(timeout=60, long_polling_timeout=60)
