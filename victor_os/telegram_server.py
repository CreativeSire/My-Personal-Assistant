import telebot
import os
import sys
import json
import re
import sqlite3
import glob
import tempfile
import time
import datetime
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any
from tendo import singleton
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from config import get_config, validate_or_raise
from logging_config import get_logger, setup_logging, new_correlation_id
from session_manager import get_session_service, resolve_user_id, resolve_session_id
from resilience import telegram_breaker, gemini_breaker

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

cfg = validate_or_raise(get_config())
setup_logging(cfg.log_dir)
logger = get_logger("telegram_server")

BASE_DIR = cfg.base_dir
MEMORY_DB_PATH = cfg.memory_db_path
WORKSPACE_DIR = cfg.workspace_dir
MEMORY_V3_ENABLED = cfg.memory_v3_enabled
_data_engine = get_data_engine() if get_data_engine else None
_telemetry = Telemetry() if Telemetry else None
_policy_engine = PolicyEngine() if PolicyEngine else None
_global_kill_switch = {"enabled": False}

# --- Multi-user: UserRegistry + PassiveIntelligenceEngine ---
from user_registry import get_user_registry
from passive_intelligence import get_passive_engine

_user_registry = get_user_registry()
_passive_engine = get_passive_engine()

# Bootstrap: ensure owner is in the registry
_owner_id = str(cfg.telegram_target_id or os.getenv("VICTOR_OWNER_USER_ID", "")).strip()
if _owner_id:
    _user_registry.ensure_owner(_owner_id, name="Owner")

# Start passive intelligence background reflection thread
if getattr(cfg, "passive_intelligence_enabled", True):
    _passive_engine.start()
_TELEGRAM_MAX_DOC_MB = float(os.getenv("TELEGRAM_MAX_DOC_MB", "45"))
_TELEGRAM_UPLOAD_TIMEOUT_SEC = int(os.getenv("TELEGRAM_UPLOAD_TIMEOUT_SEC", "180"))
_TELEGRAM_UPLOAD_RETRIES = int(os.getenv("TELEGRAM_UPLOAD_RETRIES", "3"))


def _emit_platform_event(
    event_type: str,
    *,
    session_id: str = "",
    task_id: str = "",
    actor: str = "telegram_server",
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
            channel="telegram",
            source="telegram_server",
        )
    except Exception as exc:
        logger.debug(f"Telemetry emit failed: {exc}")


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
    except Exception as exc:
        logger.warning(f"Live-process check failed, attempting fallback probe: {exc}")
        try:
            output = os.popen("wmic process where \"name='python.exe'\" get CommandLine").read()
            return "telegram_server.py" in (output or "").lower()
        except Exception:
            # Conservative fallback for reliability: assume no live instance.
            return False
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
_singleton_disabled = os.getenv("TELEGRAM_SINGLETON_DISABLE", "false").strip().lower() in {"1", "true", "yes", "on"}
if not _singleton_disabled:
    try:
        me = singleton.SingleInstance()
    except singleton.SingleInstanceException:
        if not _has_live_telegram_process():
            _cleanup_stale_singleton_lock()
            recovered = False
            for _ in range(3):
                try:
                    me = singleton.SingleInstance()
                    recovered = True
                    break
                except singleton.SingleInstanceException:
                    time.sleep(0.7)
            if not recovered:
                logger.warning("Singleton lock detected and could not be recovered. Exiting.")
                sys.exit(0)
        else:
            logger.warning("Singleton lock — another instance running. Exiting.")
            sys.exit(0)
else:
    logger.warning("TELEGRAM_SINGLETON_DISABLE is enabled; lock enforcement bypassed.")

logger.info("Telegram server starting up")

# Write PID file so the watchdog can monitor us reliably
try:
    _pid_file = os.path.join(cfg.base_dir, "data", "telegram_server.pid")
    os.makedirs(os.path.dirname(_pid_file), exist_ok=True)
    with open(_pid_file, "w") as _pf:
        _pf.write(str(os.getpid()))
    import atexit
    atexit.register(lambda: os.path.exists(_pid_file) and os.unlink(_pid_file))
except Exception as _pid_err:
    logger.warning(f"Could not write PID file: {_pid_err}")

from google.adk import Runner
from google.genai import types
from agents import chief_of_staff, get_skill_registry
from tools import transcribe_audio_file, generate_tts_file, send_email_alert, run_system_diagnostic
from monitor import log_activity
from memory_core import MemoryQueryInput, MemoryRecordInput, recall_memory, save_memory
from memory_policy import classify_memory_candidate, redact_sensitive
from sitrep_builder import build_executive_sitrep
from local_inference_router import LocalInferenceRouter
from fast_path import get_fast_response
from reliability_pack import get_normalizer, get_interaction_logger, FALLBACK_MATRIX, PITCH_MODE
from adapters.telegram_adapter import TelegramAdapter
from channel_adapter import OutboundEnvelope
from session_overrides import get_override_store, extract_directives, get_mode_system_prompt
from agent_router import get_agent_router
from approval_engine import get_approval_engine
from config_watcher import start_config_watcher

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
_tier_router = LocalInferenceRouter()
_tg_adapter = TelegramAdapter(
    sender=lambda destination, text: send_smart_message(int(destination), str(text)),
    role_lookup=lambda uid: (_user_registry.get_user(str(uid)).role if _user_registry.get_user(str(uid)) else None),
    tier_router=_tier_router,
)
_override_store = get_override_store()
_agent_router = get_agent_router()
_approval_engine = get_approval_engine()

# --- HOT CONFIG RELOAD ---
def _owner_notify(msg: str) -> None:
    if _owner_id:
        try:
            telegram_breaker.call(bot.send_message, _owner_id, msg)
        except Exception:
            pass

start_config_watcher(interval_seconds=30, notify_callback=_owner_notify)

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
                _send_document_with_fallback(
                    chat_id=target,
                    resolved_path=resolved_path,
                    raw_path=raw_file,
                    reply_to_message=None,
                )
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


def _run_generic_intent(payload: dict[str, Any]) -> str:
    intent = str(payload.get("intent") or "task")
    return f"Generic intent completed: {intent}"


_task_queue.register_handler("generic_intent", _run_generic_intent)

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
        from skills.calendar_sync import CalendarSyncSkill as _CalendarSkill

        _ops_skill = _OpsSkill()
        _market_skill = _MarketSkill()
        _mem_hygiene_skill = _MemHygieneSkill()
        _calendar_skill = _CalendarSkill()

        _wf_engine.register_action("ops_health_summary", lambda params, ctx: _ops_skill.tool_ops_health_summary())
        _wf_engine.register_action("market_snapshot", lambda params, ctx: _market_skill.tool_market_snapshot())
        _wf_engine.register_action("memory_hygiene", lambda params, ctx: _mem_hygiene_skill.tool_memory_hygiene_report())
        _wf_engine.register_action("todays_agenda", lambda params, ctx: _calendar_skill.tool_get_todays_agenda())

        def _compose_daily_report(params, ctx):
            """Run Chief through ADK to compose the daily briefing."""
            health = str((params or {}).get("health") or ctx.get("step_collect_ops_health_result") or "")
            calendar = str((params or {}).get("calendar") or ctx.get("step_get_calendar_result") or "")
            _brief_msg = types.Content(role="user", parts=[types.Part(text=(
                "MORNING BRIEFING TASK:\n"
                "1. Research the current price of Bitcoin and Ethereum.\n"
                "2. Find one major AI headline in the last 24 hours.\n"
                "3. Find one key business headline from Lagos, Nigeria.\n"
                "4. Include today's calendar agenda (if available).\n\n"
                f"OPS HEALTH:\n{health}\n\n"
                f"CALENDAR:\n{calendar}\n\n"
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

    # Register self-training workflow action handlers
    try:
        from skills.self_trainer import SelfTrainerSkill as _SelfTrainerSkill

        _self_trainer = _SelfTrainerSkill()
        _wf_engine.register_action("training_status", lambda params, ctx: _self_trainer.tool_training_status())
        _wf_engine.register_action("trigger_training_export", lambda params, ctx: _self_trainer.tool_trigger_training_export())
        logger.info("Self-training action handlers registered with workflow scheduler")
    except Exception as _st_err:
        logger.debug(f"Self-training workflow actions not registered: {_st_err}")

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
    from skill_registry import get_skill_registry
    registry = get_skill_registry()
    skill_names = sorted([s.manifest().display_name for s in registry.list_skills() if s.manifest().enabled])
    skill_list = "\n".join(f"  • {n}" for n in skill_names[:15])
    if len(skill_names) > 15:
        skill_list += f"\n  … and {len(skill_names) - 15} more"

    commands = (
        "/status — system health & queue depth\n"
        "/tasks — list pending tasks\n"
        "/goals — list active goals\n"
        "/capture <note> — save a quick note\n"
        "/digest — morning briefing on demand\n"
        "/inbox — triage all pending items\n"
        "/feedback reason|rejected|preferred — correct a response\n"
        "/kill on|off — emergency stop all actions\n"
        "/safe_mode on|off — restrict tool use\n"
        "/skills — list loaded skills\n"
    )

    owner = cfg.default_owner_user_id
    msg = (
        f"Victor-OS is online.\n\n"
        f"I'm your personal AI operating system. I can:\n"
        f"  • Answer questions and do research\n"
        f"  • Manage tasks, goals, and your calendar\n"
        f"  • Process voice messages and documents\n"
        f"  • Watch your files, track markets, review code\n"
        f"  • Learn from every interaction to improve over time\n\n"
        f"Active skills ({len(skill_names)}):\n{skill_list}\n\n"
        f"Commands:\n{commands}\n"
        f"Or just talk to me — I understand natural language.\n"
        f"Owner: {owner}"
    )
    bot.reply_to(message, msg)


@bot.message_handler(commands=["request_access"])
def handle_request_access(message):
    user_id = str(message.chat.id)
    first_name = getattr(message.from_user, "first_name", "") or ""
    username = getattr(message.from_user, "username", "") or ""
    display = first_name or username or user_id

    # If already authorized, just confirm
    existing = _user_registry.get_user(user_id)
    if existing:
        bot.reply_to(message, f"You already have access, {existing.name}. Role: {existing.role}")
        return

    # Notify owner
    if _owner_id:
        try:
            bot.send_message(
                _owner_id,
                f"Access request from {display} (ID: {user_id})\n\n"
                f"To grant access, send:\n"
                f"/add_user {user_id} {first_name or 'User'} viewer",
            )
        except Exception:
            pass

    bot.reply_to(
        message,
        f"Request sent to the owner.\n"
        f"Your Telegram ID is: {user_id}\n"
        f"You'll be notified when access is granted.",
    )


@bot.message_handler(commands=["add_user"])
def handle_add_user(message):
    """Owner shortcut: /add_user <telegram_id> <name> [role] [data_mode]"""
    user_id = str(message.chat.id)
    caller = _user_registry.get_user(user_id)
    if not caller or caller.role != "owner":
        bot.reply_to(message, "This command is owner-only.")
        return
    parts = str(message.text or "").strip().split(maxsplit=4)
    # parts: ["/add_user", telegram_id, name, role?, data_mode?]
    if len(parts) < 3:
        bot.reply_to(message, "Usage: /add_user <telegram_id> <name> [role] [data_mode]\nExample: /add_user 123456789 Alice trusted isolated")
        return
    new_id = parts[1].strip()
    new_name = parts[2].strip()
    new_role = parts[3].strip() if len(parts) > 3 else "viewer"
    new_mode = parts[4].strip() if len(parts) > 4 else "isolated"
    try:
        from user_registry import VALID_ROLES, VALID_DATA_MODES
        if new_role not in VALID_ROLES:
            bot.reply_to(message, f"Invalid role '{new_role}'. Use: {', '.join(sorted(VALID_ROLES))}")
            return
        if new_mode not in VALID_DATA_MODES:
            bot.reply_to(message, f"Invalid data_mode '{new_mode}'. Use: {', '.join(sorted(VALID_DATA_MODES))}")
            return
        existing = _user_registry.get_user(new_id)
        if existing:
            bot.reply_to(message, f"{existing.name} ({new_id}) is already registered with role={existing.role}.")
            return
        profile = _user_registry.add_user(new_id, new_name, role=new_role, data_mode=new_mode)
        bot.reply_to(message, f"Added {profile.name} ({new_id}) as {new_role}.")
        # Send welcome to new user
        try:
            consent_prompt = (
                f"Hi {profile.name}, I'm Victor.\n\n"
                f"You've been granted access by the owner.\n\n"
                f"I can passively learn from our conversations to serve you better.\n"
                f"By default I only observe your messages. You can expand this anytime:\n\n"
                f"  /consent voice on — learn from voice notes\n"
                f"  /consent files on — index files you share\n"
                f"  /consent images on — understand images you send\n"
                f"  /consent urls on — read pages you share\n\n"
                f"Send me a message or ask me anything to get started."
            )
            bot.send_message(int(new_id), consent_prompt)
        except Exception:
            pass
    except Exception as e:
        bot.reply_to(message, f"Error adding user: {e}")


@bot.message_handler(commands=["consent"])
def handle_consent(message):
    """Set your own passive observation consent: /consent <flag> <on|off>"""
    user_id = str(message.chat.id)
    user = _user_registry.get_user(user_id)
    if not user:
        bot.reply_to(message, "You don't have access yet.")
        return
    parts = str(message.text or "").strip().split(maxsplit=2)
    if len(parts) < 3:
        # Show current consent state
        flags = user.consent_flags
        on = [k for k, v in flags.items() if v]
        off = [k for k, v in flags.items() if not v]
        bot.reply_to(
            message,
            f"Your passive observation settings:\n"
            f"Active: {', '.join(on) or 'none'}\n"
            f"Inactive: {', '.join(off) or 'none'}\n\n"
            f"To change: /consent <flag> <on|off>\n"
            f"Flags: voice, images, files, urls, screen, browser, calendar, mic"
        )
        return
    flag = parts[1].strip().lower()
    value = parts[2].strip().lower() in ("on", "true", "yes", "1", "enable")
    success = _user_registry.set_consent(user_id, flag, value)
    if success:
        state = "enabled" if value else "disabled"
        bot.reply_to(message, f"Observation of '{flag}' {state}.")
    else:
        from user_registry import DEFAULT_CONSENT
        bot.reply_to(message, f"Unknown flag '{flag}'. Valid: {', '.join(sorted(DEFAULT_CONSENT.keys()))}")


@bot.message_handler(commands=["approve"])
def handle_approve(message):
    """Owner: /approve <TOKEN> — approve a pending elevated action."""
    user_id = str(message.chat.id)
    caller = _user_registry.get_user(user_id)
    if not caller or caller.role != "owner":
        bot.reply_to(message, "Only the owner can approve actions.")
        return
    parts = str(message.text or "").strip().split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "Usage: /approve <TOKEN>")
        return
    token = parts[1].strip()
    ok, msg = _approval_engine.resolve(token, approved=True)
    bot.reply_to(message, msg)


@bot.message_handler(commands=["deny"])
def handle_deny(message):
    """Owner: /deny <TOKEN> — deny a pending elevated action."""
    user_id = str(message.chat.id)
    caller = _user_registry.get_user(user_id)
    if not caller or caller.role != "owner":
        bot.reply_to(message, "Only the owner can deny actions.")
        return
    parts = str(message.text or "").strip().split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "Usage: /deny <TOKEN>")
        return
    token = parts[1].strip()
    ok, msg = _approval_engine.resolve(token, approved=False)
    bot.reply_to(message, msg)


@bot.message_handler(commands=["pending"])
def handle_pending(message):
    """Owner: /pending — list all pending approval requests."""
    user_id = str(message.chat.id)
    caller = _user_registry.get_user(user_id)
    if not caller or caller.role != "owner":
        bot.reply_to(message, "Owner only.")
        return
    requests = _approval_engine.pending_for_owner()
    if not requests:
        bot.reply_to(message, "No pending approvals.")
        return
    lines = ["Pending approvals:"]
    for r in requests:
        expires_in = max(0, int(r["expires_at"] - time.time()))
        lines.append(
            f"  [{r['token']}] {r['tool_name']} — user {r['user_id']}\n"
            f"    {r['description'][:80]}\n"
            f"    Expires in {expires_in}s. /approve {r['token']} or /deny {r['token']}"
        )
    bot.reply_to(message, "\n".join(lines))


@bot.message_handler(commands=["mode"])
def handle_mode(message):
    """Set or view session mode: /mode [fast|deep|research|code|exec|reset]"""
    user_id = str(message.chat.id)
    user = _user_registry.get_user(user_id)
    if not user:
        bot.reply_to(message, "You don't have access yet.")
        return
    session_id = resolve_session_id("telegram", user_id)
    parts = str(message.text or "").strip().split(maxsplit=1)
    if len(parts) < 2:
        override = _override_store.get(session_id)
        bot.reply_to(
            message,
            f"Current session mode: {override.mode}\n"
            f"Thinking level: {override.thinking_level}\n"
            f"Skip critic: {override.skip_critic}\n\n"
            f"Set with: /mode fast|deep|research|code|exec|reset\n"
            f"Or inline: @fast, @deep, @research, @code, @exec, @reset"
        )
        return
    directive = parts[1].strip().lower().lstrip("@")
    override, msg = _override_store.apply_directive(session_id, directive, user_role=user.role)
    bot.reply_to(message, msg)


@bot.message_handler(commands=["kill"])
def handle_kill(message):
    parts = str(message.text or "").strip().split(maxsplit=1)
    enabled = len(parts) > 1 and parts[1].strip().lower() in {"on", "true", "1", "enable", "enabled"}
    _global_kill_switch["enabled"] = enabled
    _emit_platform_event(
        "action.executed",
        actor=str(message.chat.id),
        payload={"kill_switch_enabled": enabled},
        risk_score=1.0,
    )
    bot.reply_to(message, f"Global kill switch set to: {enabled}")


@bot.message_handler(commands=["safe_mode"])
def handle_safe_mode(message):
    if _policy_engine is None:
        bot.reply_to(message, "Policy engine unavailable.")
        return
    parts = str(message.text or "").strip().split(maxsplit=1)
    enabled = len(parts) > 1 and parts[1].strip().lower() in {"on", "true", "1", "enable", "enabled"}
    session_id = resolve_session_id("telegram", str(message.chat.id))
    _policy_engine.set_safe_mode(session_id, enabled)
    _emit_platform_event(
        "action.executed",
        session_id=session_id,
        actor=str(message.chat.id),
        payload={"safe_mode": enabled},
        risk_score=0.8,
    )
    bot.reply_to(message, f"Session safe mode set to: {enabled}")


@bot.message_handler(commands=["feedback"])
def handle_feedback(message):
    if _data_engine is None:
        bot.reply_to(message, "Data engine unavailable.")
        return
    text = str(message.text or "")
    # format: /feedback reason|rejected|preferred
    body = text[len("/feedback"):].strip()
    try:
        reason, rejected, preferred = [x.strip() for x in body.split("|", 2)]
    except Exception:
        bot.reply_to(
            message,
            "Feedback format: /feedback reason_code|rejected_output|preferred_output",
        )
        return
    session_id = resolve_session_id("telegram", str(message.chat.id))
    correction_id = _data_engine.record_correction(
        session_id=session_id,
        task_id="",
        channel="telegram",
        actor=str(message.chat.id),
        reason_code=reason,
        rejected_output=rejected,
        preferred_output=preferred,
        metadata={"source": "telegram_command"},
    )
    _data_engine.recalc_core_features()
    bot.reply_to(message, f"Feedback recorded: {correction_id[:10]}")


@bot.message_handler(commands=["policy"])
def handle_policy(message):
    if _policy_engine is None or PolicyInput is None:
        bot.reply_to(message, "Policy engine unavailable.")
        return
    # format: /policy action target
    parts = str(message.text or "").split(maxsplit=2)
    if len(parts) < 2:
        bot.reply_to(message, "Usage: /policy <action> [target]")
        return
    action = parts[1].strip().lower()
    target = parts[2].strip() if len(parts) > 2 else ""
    session_id = resolve_session_id("telegram", str(message.chat.id))
    verdict = _policy_engine.evaluate(
        PolicyInput(
            action=action,
            target=target,
            channel="telegram",
            actor=str(message.chat.id),
        ),
        session_id=session_id,
    )
    if _data_engine:
        _data_engine.record_policy_decision(
            action=action,
            policy_tier=verdict.tier.value,
            allowed=verdict.allowed,
            reason=verdict.reason,
            session_id=session_id,
            actor=str(message.chat.id),
            metadata={"target": target, "channel": "telegram"},
        )
    bot.reply_to(
        message,
        (
            f"Policy verdict: {verdict.tier.value}\n"
            f"Allowed: {verdict.allowed}\n"
            f"Requires approval: {verdict.requires_approval}\n"
            f"Reason: {verdict.reason}"
        ),
    )


@bot.message_handler(commands=["task"])
def handle_task_command(message):
    """
    Usage:
    /task status <task_id>
    /task artifacts <task_id>
    /task cancel <task_id>
    /task approve <task_id>
    """
    parts = str(message.text or "").strip().split()
    if len(parts) < 3:
        bot.reply_to(
            message,
            "Usage:\n/task status <task_id>\n/task artifacts <task_id>\n/task cancel <task_id>\n/task approve <task_id>",
        )
        return
    sub = parts[1].strip().lower()
    task_id = parts[2].strip()
    if sub == "status":
        send_smart_message(message.chat.id, _render_task_status(task_id), reply_to_id=message)
        return
    if sub == "artifacts":
        send_smart_message(message.chat.id, _render_task_artifacts(task_id), reply_to_id=message)
        return
    if sub == "cancel":
        ok = _task_queue.cancel_task(task_id)
        send_smart_message(
            message.chat.id,
            f"Task {task_id} cancel {'accepted' if ok else 'rejected'}.",
            reply_to_id=message,
        )
        return
    if sub == "approve":
        send_smart_message(
            message.chat.id,
            f"Task {task_id} marked approved (scope=task).",
            reply_to_id=message,
        )
        return
    bot.reply_to(message, "Unknown /task command. Use `status`, `artifacts`, `cancel`, or `approve`.")


@bot.message_handler(commands=["tasks"])
def handle_tasks_command(message):
    """
    Usage:
    /tasks latest
    /tasks latest 10
    """
    parts = str(message.text or "").strip().split()
    sub = parts[1].strip().lower() if len(parts) > 1 else "latest"
    if sub != "latest":
        bot.reply_to(message, "Usage: /tasks latest [count]")
        return
    count = 5
    if len(parts) > 2:
        try:
            count = max(1, min(20, int(parts[2])))
        except Exception:
            count = 5
    tasks = _query_recent_tasks(limit=count)
    if not tasks:
        bot.reply_to(message, "No tasks found.")
        return
    lines = [f"Latest {len(tasks)} Tasks:"]
    for idx, t in enumerate(tasks, start=1):
        lines.append(
            f"{idx}. {t.get('id')} | {t.get('task_type')} | {t.get('status')} | "
            f"progress={t.get('progress')} | retries={t.get('retries')}"
        )
    send_smart_message(message.chat.id, "\n".join(lines), reply_to_id=message)


@bot.message_handler(commands=["queue_drill"])
def handle_queue_drill(message):
    try:
        report = _task_queue.run_crash_recovery_drill()
        send_smart_message(
            message.chat.id,
            (
                "Crash-Recovery Drill:\n"
                f"ok={report.get('ok')}\n"
                f"task_id={report.get('task_id')}\n"
                f"recovered_count={report.get('recovered_count')}\n"
                f"post_status={report.get('post_status')}"
            ),
            reply_to_id=message,
        )
    except Exception as exc:
        bot.reply_to(message, f"Queue drill failed: {exc}")

@bot.message_handler(commands=["skills"])
def handle_skills(message):
    from skill_registry import get_skill_registry
    registry = get_skill_registry()
    skills = registry.list_skills()
    if not skills:
        bot.reply_to(message, "No skills loaded.")
        return
    lines = [f"Loaded skills ({len(skills)}):"]
    for s in sorted(skills, key=lambda x: x.manifest().name):
        m = s.manifest()
        status = "on" if m.enabled else "off"
        lines.append(f"  [{status}] {m.display_name} v{m.version} — {m.description[:60]}")
    bot.reply_to(message, "\n".join(lines))


@bot.message_handler(commands=["capture"])
def handle_capture(message):
    """Quick note/idea capture: /capture <text>"""
    text = str(message.text or "").strip()
    body = text[len("/capture"):].strip()
    if not body:
        bot.reply_to(message, "Usage: /capture <note text>")
        return
    try:
        from memory_core import MemoryRecordInput, save_memory
        save_memory(MemoryRecordInput(
            content=body,
            tags=["quick_capture", "telegram"],
            source="telegram_command",
        ))
        bot.reply_to(message, f"Captured: {body[:100]}{'...' if len(body) > 100 else ''}")
    except Exception as exc:
        bot.reply_to(message, f"Capture failed: {exc}")


@bot.message_handler(commands=["digest"])
def handle_digest(message):
    """Trigger morning digest on demand."""
    try:
        from skill_registry import get_skill_registry
        registry = get_skill_registry()
        skill = registry.get_skill("daily_digest")
        if skill is None:
            bot.reply_to(message, "Daily digest skill not loaded.")
            return
        result = skill.tool_daily_digest()
        send_smart_message(message.chat.id, result, reply_to_id=message)
    except Exception as exc:
        bot.reply_to(message, f"Digest failed: {exc}")


@bot.message_handler(commands=["inbox"])
def handle_inbox(message):
    """Show priority-sorted triage of all pending items."""
    try:
        from skill_registry import get_skill_registry
        registry = get_skill_registry()
        skill = registry.get_skill("unified_inbox")
        if skill is None:
            bot.reply_to(message, "Unified inbox skill not loaded.")
            return
        result = skill.tool_triage_inbox()
        send_smart_message(message.chat.id, result, reply_to_id=message)
    except Exception as exc:
        bot.reply_to(message, f"Inbox failed: {exc}")


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


def _query_recent_tasks(limit: int = 5) -> list[dict[str, Any]]:
    db_path = os.path.join(BASE_DIR, "memory_store", "victor_tasks.db")
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, task_type, status, channel, user_id, progress, retries, created_at, updated_at
            FROM tasks
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _query_task_by_id(task_id: str) -> dict[str, Any] | None:
    db_path = os.path.join(BASE_DIR, "memory_store", "victor_tasks.db")
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT id, task_type, status, channel, user_id, progress, retries, error, result, created_at, updated_at, completed_at
            FROM tasks
            WHERE id=?
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _extract_artifact_path_from_result(result_text: str) -> str | None:
    raw_file, _ = _extract_send_file_marker(result_text or "")
    if not raw_file:
        return None
    return _resolve_runtime_path(raw_file)


def _render_task_status(task_id: str) -> str:
    row = _query_task_by_id(task_id)
    if not row:
        return f"Task not found: {task_id}"
    lines = [
        f"Task Status: {row.get('id')}",
        f"type={row.get('task_type')} | status={row.get('status')} | progress={row.get('progress')} | retries={row.get('retries')}",
    ]
    if row.get("error"):
        lines.append(f"error={row.get('error')}")
    artifact_path = _extract_artifact_path_from_result(str(row.get("result") or ""))
    if artifact_path:
        lines.append(f"artifact={artifact_path}")
    return "\n".join(lines)


def _render_task_artifacts(task_id: str) -> str:
    row = _query_task_by_id(task_id)
    if not row:
        return f"Task not found: {task_id}"
    artifact_path = _extract_artifact_path_from_result(str(row.get("result") or ""))
    if not artifact_path:
        return (
            f"No artifact marker for task {task_id}.\n"
            f"status={row.get('status')} | progress={row.get('progress')} | error={row.get('error') or 'none'}"
        )
    exists = os.path.exists(artifact_path)
    size_mb = (os.path.getsize(artifact_path) / (1024 * 1024)) if exists else 0.0
    return (
        f"Task Artifacts: {task_id}\n"
        f"path={artifact_path}\n"
        f"exists={exists}\n"
        f"size_mb={size_mb:.2f}"
    )


def _render_live_system_status(task_limit: int = 5) -> str:
    tasks = _query_recent_tasks(limit=task_limit)
    if not tasks:
        return (
            "System Status: queue database reachable, but no task records found yet.\n\n"
            "Latest Tasks: none."
        )
    status_counts: dict[str, int] = {}
    for t in tasks:
        st = str(t.get("status") or "unknown")
        status_counts[st] = status_counts.get(st, 0) + 1
    counts_text = ", ".join(f"{k}={v}" for k, v in sorted(status_counts.items()))
    lines = ["System Status: LIVE (source: victor_tasks.db)", f"Recent status mix: {counts_text}", "", f"Latest {len(tasks)} Tasks:"]
    for idx, t in enumerate(tasks, start=1):
        lines.append(
            f"{idx}. {t.get('id')} | {t.get('task_type')} | {t.get('status')} | "
            f"progress={t.get('progress')} | retries={t.get('retries')}"
        )
    return "\n".join(lines)


def _is_live_status_request(text: str) -> bool:
    lower = (text or "").strip().lower()
    has_tasks_request = bool(re.search(r"\blatest\s+\d+\s+tasks?\b", lower) or "latest tasks" in lower)
    has_status_request = "system status" in lower or "status summary" in lower
    return has_tasks_request or has_status_request


_FULL_CAPABILITY_BRIEF = """\
Victor OS — Full Capability Brief

CORE INTELLIGENCE
• Google Search (live web data, news, prices, events)
• Long-term vector memory — recalls facts, decisions, preferences across all sessions
• Multi-agent pipeline: Research → Dev → Data → Script → Academic specialists

EXECUTION
• Run Python code with pandas, numpy, matplotlib, openpyxl
• Read/write files in the workspace (Excel, CSV, PDF, images)
• Screen capture + visual analysis of your desktop
• Push code to GitHub

COMMUNICATIONS
• Send emails with attachments
• Voice/TTS responses
• Telegram inline file delivery

OPERATIONS
• System diagnostic (CPU, RAM, disk, ports)
• Task queue — submit long-running jobs, check status
• Workflow scheduler — daily briefings, weekly reviews, nightly self-training
• Model router — selects optimal AI (Gemini Flash/Pro, Claude) per task type

INTELLIGENCE LAYER
• Response Critic — scores every reply, auto-revises below threshold
• Interaction Logger — logs every exchange as training data
• Passive Intelligence — observes patterns across sessions, builds your profile
• Goal tracker — auto-detects and tracks objectives from conversation

LOADED SKILLS (dynamic)
market_watch, model_router, unified_inbox, file_watcher, knowledge_base,
daily_digest, email_intelligence, crm_memory, goal_tracking, self_trainer,
document_brain, task_delegation, ops_health_check, code_review, and more.

PITCH MODE
Set PITCH_MODE=true before launch for maximum stability: stricter normalizer,
deterministic-first routing, full pipeline only for novel requests.
"""


def _identity_response(lower_text: str) -> str | None:
    if any(k in lower_text for k in ["who are you", "tell me about yourself", "are you an ai", "are you ai", "are you chatgpt"]):
        return (
            "I'm Victor OS — your Digital Executive Officer. "
            "I run a multi-agent pipeline backed by Google Gemini, with persistent memory, "
            "live search, code execution, file handling, and a full skill layer. What do you need?"
        )
    # Broad capability query — catches "what can you do", "explain everything you can do",
    # "tell me everything", "leave no stone unturned", "in detail what do you do", etc.
    capability_triggers = [
        "what can you do",
        "everything you can do",
        "all you can do",
        "what do you do",
        "your capabilities",
        "your features",
        "your functions",
        "leave no stone",
        "in detail",
    ]
    explain_triggers = ["explain", "elaborate", "describe", "tell me", "give me"]
    has_capability = any(k in lower_text for k in capability_triggers)
    has_explain = any(k in lower_text for k in explain_triggers)
    if has_capability or (has_explain and any(k in lower_text for k in ["you", "your", "can"])):
        return _FULL_CAPABILITY_BRIEF
    return None


def _render_loaded_skills_summary() -> str:
    try:
        registry = get_skill_registry()
        skills = registry.list_skills()
        if not skills:
            return "Loaded Skills: none."
        names = [s.manifest().name for s in skills if s.manifest().enabled]
        names = sorted(names)
        preview = ", ".join(names[:25])
        more = f" (+{len(names)-25} more)" if len(names) > 25 else ""
        return f"Loaded Skills ({len(names)}): {preview}{more}"
    except Exception as exc:
        return f"Loaded skills unavailable right now: {exc}"


def _render_router_status(user_text: str) -> str:
    try:
        intent_class, confidence = _tier_router.classify_intent(user_text)
        tier = _tier_router.classify_tier(user_text)
        return (
            "Model Routing Status\n"
            f"- tier: {tier}\n"
            f"- intent_class: {intent_class}\n"
            f"- confidence: {confidence:.2f}\n"
            f"- local_threshold: {_tier_router.threshold:.2f}\n"
            f"- allowed_local_classes: {', '.join(sorted(_tier_router.allowed_classes))}"
        )
    except Exception as exc:
        return f"Model routing status unavailable: {exc}"


def _render_market_snapshot_or_error() -> str:
    try:
        from skills.market_watch import MarketWatchSkill

        return MarketWatchSkill().tool_market_snapshot()
    except Exception as exc:
        return f"Market data temporarily unavailable: {exc}"


def _fetch_one_news_headline(query: str) -> str:
    now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
    q = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            xml_text = resp.read().decode("utf-8", errors="replace")
        root = ET.fromstring(xml_text)
        item = root.find("./channel/item")
        if item is None:
            return f"No current headline found for '{query}'."
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        return (
            f"Top headline ({query}) as of {now}:\n"
            f"- {title}\n"
            f"- {link}\n"
            f"- {pub}"
        )
    except Exception as exc:
        return f"News lookup temporarily unavailable: {exc}"


def _deterministic_response(user_text: str) -> str | None:
    lower = (user_text or "").strip().lower()
    if not lower:
        return None

    ident = _identity_response(lower)
    if ident:
        return ident

    if "skills do you have loaded" in lower or "what skills do you have loaded" in lower or "what skills do you have" in lower:
        return _render_loaded_skills_summary()

    if "model routing status" in lower or "show me model routing status" in lower:
        return _render_router_status(user_text)

    if "run a system diagnostic" in lower or "system diagnostic" in lower:
        try:
            return f"System Diagnostic\n{run_system_diagnostic()}"
        except Exception as exc:
            return f"System diagnostic failed: {exc}"

    if "list my recent tasks" in lower or "recent tasks" in lower:
        tasks = _query_recent_tasks(limit=5)
        if not tasks:
            return "No recent tasks found."
        lines = [f"Recent Tasks ({len(tasks)}):"]
        for idx, t in enumerate(tasks, start=1):
            lines.append(
                f"{idx}. {t.get('id')} | {t.get('task_type')} | {t.get('status')} | "
                f"progress={t.get('progress')} | retries={t.get('retries')}"
            )
        return "\n".join(lines)

    if "bitcoin" in lower and ("price" in lower or "current" in lower or "what's" in lower or "whats" in lower):
        return _render_market_snapshot_or_error()

    if "latest news for nigeria" in lower or "news from nigeria" in lower or "news for nigeria" in lower or "major news from nigeria" in lower:
        return _fetch_one_news_headline("Nigeria")

    if "major ai news" in lower or "latest news about openai" in lower or "openai news" in lower:
        q = "OpenAI" if "openai" in lower else "AI"
        return _fetch_one_news_headline(q)

    return None


def _send_document_with_fallback(chat_id: int | str, resolved_path: str, raw_path: str, reply_to_message=None) -> bool:
    if not os.path.exists(resolved_path):
        send_smart_message(chat_id, f"Courier Error: File not found at {raw_path}", reply_to_id=reply_to_message)
        return False
    file_size_mb = os.path.getsize(resolved_path) / (1024 * 1024)
    if file_size_mb > _TELEGRAM_MAX_DOC_MB:
        send_smart_message(
            chat_id,
            (
                "Courier fallback: output generated successfully, but Telegram cannot upload this file size.\n"
                f"File: {os.path.basename(resolved_path)} ({file_size_mb:.2f} MB)\n"
                f"Local path: {resolved_path}\n"
                "Action: download locally from this path or rerun with a smaller batch."
            ),
            reply_to_id=reply_to_message,
        )
        return False
    try:
        last_error = None
        attempts = max(1, _TELEGRAM_UPLOAD_RETRIES)
        for attempt in range(1, attempts + 1):
            try:
                with open(resolved_path, 'rb') as doc:
                    telegram_breaker.call(
                        bot.send_document,
                        chat_id,
                        doc,
                        timeout=_TELEGRAM_UPLOAD_TIMEOUT_SEC,
                    )
                return True
            except Exception as e:
                last_error = e
                if attempt < attempts:
                    time.sleep(min(2 * attempt, 5))
                    continue
                break
        raise last_error if last_error else RuntimeError("unknown upload failure")
    except Exception as e:
        send_smart_message(
            chat_id,
            (
                "Courier fallback: output generated but upload failed.\n"
                f"File: {os.path.basename(resolved_path)} ({file_size_mb:.2f} MB)\n"
                f"Local path: {resolved_path}\n"
                f"Error: {e}\n"
                f"Attempts: {max(1, _TELEGRAM_UPLOAD_RETRIES)} | Timeout per attempt: {_TELEGRAM_UPLOAD_TIMEOUT_SEC}s"
            ),
            reply_to_id=reply_to_message,
        )
        return False


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
        "as an ai",
        "created by google",
        "developed in google ai",
        "i am an ai assistant",
        "i do not possess memory in the human sense",
        "i don't store information from previous conversations",
        "i do not have any persistent memory",
        "i am unable to",
        "i cannot provide that information directly",
        "consult the system administrators",
    ]
    if any(b in lowered for b in blocked):
        return (
            "I'm Victor OS. I can run diagnostics, inspect routing status, list tasks, "
            "check loaded skills, and fetch market/news updates right now."
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


def _build_memory_context(
    query: str,
    user_id: Optional[str] = None,
    data_mode: str = "isolated",
) -> dict[str, Any]:
    # Global memories: always included for isolated/shared users.
    # For collaborative mode, also included.
    global_res = recall_memory(
        MemoryQueryInput(
            query=query,
            scope_filter="global",
            top_k=10,
            min_similarity=0.2,
        )
    )

    # Agent memories: scoped per user unless collaborative
    if user_id and data_mode == "isolated":
        # Only this user's own agent memories
        agent_filter = f"user_{user_id}"
    elif user_id and data_mode == "shared":
        # User's personal memories + Chief_of_Staff operational memories
        agent_filter = f"user_{user_id}"
    else:
        # collaborative or no user_id → use Chief_of_Staff (legacy behaviour)
        agent_filter = "Chief_of_Staff"

    agent_res = recall_memory(
        MemoryQueryInput(
            query=query,
            scope_filter="agent",
            agent_filter=agent_filter,
            top_k=8,
            min_similarity=0.2,
        )
    )

    # For shared/collaborative mode, also pull Chief_of_Staff operational context
    chief_hits: list = []
    if user_id and data_mode in ("shared", "collaborative"):
        chief_res = recall_memory(
            MemoryQueryInput(
                query=query,
                scope_filter="agent",
                agent_filter="Chief_of_Staff",
                top_k=5,
                min_similarity=0.2,
            )
        )
        chief_hits = chief_res.get("results", []) if chief_res.get("ok") else []

    global_hits = global_res.get("results", []) if global_res.get("ok") else []
    agent_hits = agent_res.get("results", []) if agent_res.get("ok") else []
    return {"global_hits": global_hits, "agent_hits": agent_hits + chief_hits}


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


def _save_deterministic_memories(
    user_text: str,
    final_answer: str,
    tool_names: list[str],
    delegated_agents: list[str],
    user_id: Optional[str] = None,
):
    if not MEMORY_V3_ENABLED:
        return
    tool_ctx = {"tool_names": tool_names, "delegated_agents": delegated_agents}
    candidates = classify_memory_candidate(user_text, final_answer, tool_ctx)
    for candidate in candidates:
        redacted_text, sensitive_refs = redact_sensitive(candidate.get("text", ""))
        scope = candidate.get("scope", "global")
        # Per-user scoping: agent-scope memories belong to this user's namespace
        agent_name = candidate.get("agent_name")
        if scope == "agent" and user_id and not agent_name:
            agent_name = f"user_{user_id}"
        record = MemoryRecordInput(
            text=redacted_text,
            memory_type=candidate.get("memory_type", "other"),
            scope=scope,
            agent_name=agent_name,
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


def _run_simple_turn(user_text: str, resolved_uid: str, resolved_sid: str, intent_class: str = "default") -> tuple[str, bool]:
    """
    Lightweight turn for SIMPLE-tier requests.
    No memory context injection and no critic.
    Returns (response_text, was_normalized).
    """
    _identity = (
        "You are Victor OS (v2.0 - Eternal), a Digital Executive Officer. "
        "Stay in character. Do not say you are a large language model or an AI assistant. "
        "Be direct, confident, and brief.\n\n"
    )
    new_message = types.Content(role="user", parts=[types.Part(text=_identity + user_text)])
    response = ""
    stream_error = None
    for attempt in range(2):
        try:
            event_stream = gemini_breaker.call(
                runner.run,
                user_id=resolved_uid,
                session_id=resolved_sid,
                new_message=new_message,
            )
            for event in event_stream:
                chunks = _unique_text_chunks(_extract_text_from_node(event))
                if chunks:
                    response += "".join(chunks)
            stream_error = None
            break
        except Exception as exc:
            stream_error = exc
            if attempt == 0 and _is_transient_network_error(exc):
                time.sleep(0.8)
                continue
            break

    if not response.strip():
        fallback = FALLBACK_MATRIX.get(intent_class, FALLBACK_MATRIX["default"])
        if stream_error is not None:
            return "Temporary network issue on my side. Please resend now.", False
        return fallback, True
    return get_normalizer().normalize(response.strip(), intent_class)

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

        # Passive observe: voice transcription
        try:
            _passive_engine.observe(user_id, "voice", user_text, {"source": "voice_note"})
        except Exception:
            pass

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

        # Passive observe: image sent (with caption as context)
        try:
            _passive_engine.observe(user_id, "image", f"[Photo] Caption: {caption}", {"source": "photo"})
        except Exception:
            pass

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

        # Passive observe: document uploaded
        try:
            caption_text = message.caption or ""
            _passive_engine.observe(user_id, "document", f"[File] {file_name}. {caption_text}".strip(), {"source": "document", "filename": file_name})
        except Exception:
            pass

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
            if _data_engine:
                _data_engine.upsert_task_run(
                    task_id=task_id,
                    state="pending",
                    channel="telegram",
                    user_id=user_id,
                    payload={"input_path": file_path, "task_type": "invoice_job"},
                    metadata={"origin": "telegram_document"},
                )
            _emit_platform_event(
                "plan.generated",
                task_id=task_id,
                session_id=resolve_session_id("telegram", user_id),
                actor=user_id,
                payload={"task_type": "invoice_job", "input_path": file_name},
                risk_score=0.5,
            )
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
    task_id = ""
    normalized = _tg_adapter.normalize_inbound(message)
    authz = _tg_adapter.authorize(normalized.sender_id, message)
    route_decision = _tg_adapter.route(normalized)
    if not authz.allowed:
        _tg_adapter.deliver(
            OutboundEnvelope(
                channel="telegram",
                destination=user_id,
                text="Unauthorized. Contact owner for access.",
                idempotency_key=f"unauth:{normalized.message_id}",
            )
        )
        logger.info(f"Unauthorized access attempt from {user_id}", extra={"channel": "telegram", "user_id": user_id})
        return
    # --- Multi-user access control via UserRegistry ---
    user_profile = _user_registry.get_user(user_id)
    if user_profile is None:
        # Unknown user — show friendly gate message
        bot.reply_to(
            message,
            "Hi, I'm Victor.\n\n"
            "You're not on the access list yet.\n"
            "Ask the owner to add you, or send /request_access and I'll notify them.",
        )
        logger.info(f"Unauthorized access attempt from {user_id}", extra={"channel": "telegram", "user_id": user_id})
        return
    role = user_profile.role
    _user_registry.touch(user_id)
    logger.info(f"Authorized user={user_id} name={user_profile.name} role={role}", extra={"channel": "telegram", "user_id": user_id})
    if _global_kill_switch["enabled"]:
        bot.reply_to(message, "Global kill switch is enabled. Actions are paused.")
        return

    # --- Directive extraction (@fast, @deep, @research, @code, @exec, @reset) ---
    _session_id_for_overrides = resolve_session_id("telegram", user_id)
    _directives, user_text = extract_directives(user_text or "")
    _session_override = _override_store.get(_session_id_for_overrides)
    if _directives:
        for _d in _directives:
            _session_override, _d_msg = _override_store.apply_directive(
                _session_id_for_overrides, _d, user_role=role
            )
            send_smart_message(message.chat.id, _d_msg, reply_to_id=message)
        # If only a directive was sent (nothing left after stripping), stop here
        if not user_text.strip():
            return
    # Re-read override after any directive applications
    _session_override = _override_store.get(_session_id_for_overrides)

    # --- Reliability Pack: resolve intent class once, used by normalizer + logger ---
    _rp_intent_class, _ = _tier_router.classify_intent(user_text or "")
    _rp_normalizer = get_normalizer()
    _rp_logger = get_interaction_logger(os.path.join(BASE_DIR, "memory_store"))
    deterministic = _deterministic_response(user_text or "")
    if deterministic:
        _tg_adapter.deliver(
            OutboundEnvelope(
                channel="telegram",
                destination=str(message.chat.id),
                text=deterministic,
                session_id=resolve_session_id("telegram", user_id),
                idempotency_key=f"det:{normalized.message_id}",
            )
        )
        _rp_logger.log(user_text or "", deterministic, "DETERMINISTIC",
                       intent_class=_rp_intent_class, was_normalized=False, user_id=user_id)
        try:
            _passive_engine.observe(
                telegram_id=user_id,
                event_type="message",
                content=str(user_text or ""),
                metadata={"tier": "DETERMINISTIC", "response_preview": deterministic[:220]},
            )
        except Exception:
            pass
        return
    tier = route_decision.tier or _tier_router.classify_tier(user_text or "")
    if tier == "SOCIAL":
        fast_reply = get_fast_response(user_text or "")
        _tg_adapter.deliver(
            OutboundEnvelope(
                channel="telegram",
                destination=str(message.chat.id),
                text=fast_reply,
                session_id=resolve_session_id("telegram", user_id),
                idempotency_key=f"social:{normalized.message_id}",
            )
        )
        _rp_logger.log(user_text or "", fast_reply, "SOCIAL",
                       intent_class="social", was_normalized=False, user_id=user_id)
        try:
            _passive_engine.observe(
                telegram_id=user_id,
                event_type="message",
                content=str(user_text or ""),
                metadata={"tier": "SOCIAL", "response_preview": fast_reply[:120]},
            )
        except Exception:
            pass
        return
    logger.info(f"Processing: {user_text[:80]}{'...' if len(user_text) > 80 else ''} {'(with image)' if image_data else ''}", extra={"channel": "telegram", "user_id": user_id})
    try:
        telegram_breaker.call(bot.send_chat_action, message.chat.id, 'typing')
    except Exception as e:
        logger.warning(f"Typing indicator failed: {e}", extra={"channel": "telegram", "user_id": user_id})
    log_activity("Telegram User", "Input Received", "Info", f"Text: {user_text[:50]}...")

    try:
        session_id = resolve_session_id("telegram", user_id)
        # SIMPLE tier: skipped in PITCH_MODE for maximum reliability
        if tier == "SIMPLE" and not PITCH_MODE:
            resolved_uid = resolve_user_id("telegram", user_id)
            response, was_norm = _run_simple_turn(
                user_text, resolved_uid=resolved_uid, resolved_sid=session_id,
                intent_class=_rp_intent_class,
            )
            send_smart_message(message.chat.id, response, reply_to_id=message)
            _rp_logger.log(user_text, response, "SIMPLE", intent_class=_rp_intent_class,
                           was_normalized=was_norm, user_id=user_id, session_id=session_id)
            try:
                _passive_engine.observe(
                    telegram_id=user_id,
                    event_type="message",
                    content=str(user_text or ""),
                    metadata={"tier": "SIMPLE", "response_preview": response[:300], "session_id": session_id},
                )
            except Exception:
                pass
            return
        _emit_platform_event(
            "intent.received",
            session_id=session_id,
            actor=user_id,
            payload={"intent": user_text[:500], "has_image": bool(image_data)},
            risk_score=0.2 if not image_data else 0.3,
        )
        lower_text = (user_text or "").strip().lower()
        if _is_live_status_request(lower_text):
            live_status = _render_live_system_status(task_limit=5)
            send_smart_message(message.chat.id, live_status, reply_to_id=message)
            _emit_platform_event(
                "action.executed",
                session_id=session_id,
                actor=user_id,
                payload={"action": "live_status_summary", "source": "victor_tasks.db"},
                risk_score=0.1,
            )
            return

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
            _emit_platform_event(
                "plan.generated",
                session_id=session_id,
                actor=user_id,
                payload={"task_type": "invoice_job", "source": "folder_intent"},
                risk_score=0.4,
            )
            send_smart_message(message.chat.id, quick_reply, reply_to_id=message)
            return

        # 1. Prepare Content Object
        resolved_sid = resolve_session_id("telegram", user_id)
        history_context = _build_session_context([resolved_sid, user_id])
        memory_context = _build_memory_context(user_text, user_id=user_id, data_mode=user_profile.data_mode if user_profile else "isolated")
        global_block = _format_hits_block("GLOBAL_CONTEXT", memory_context["global_hits"])
        agent_block = _format_hits_block("AGENT_CONTEXT", memory_context["agent_hits"])
        logger.debug(f"Memory context loaded: global={len(memory_context['global_hits'])}, agent={len(memory_context['agent_hits'])}")

        sitrep_requested = bool(re.search(r"\b(sitrep|situation report|status report)\b", user_text.lower()))
        if MEMORY_V3_ENABLED and sitrep_requested:
            final_answer = _build_sitrep_report(user_id, user_text)
            send_smart_message(message.chat.id, final_answer, reply_to_id=message)
            _save_deterministic_memories(user_text, final_answer, [], [], user_id=user_id)
            log_activity("Chief_of_Staff", "Sitrep Sent", "Success")
            return

        # --- USER PROFILE BLOCK (personalization) ---
        user_block = ""
        if user_profile:
            summary = user_profile.profile_summary or "New user — profile not yet built."
            user_block = (
                f"[USER_PROFILE]\n"
                f"Name: {user_profile.name}\n"
                f"Role: {user_profile.role}\n"
                f"Data mode: {user_profile.data_mode}\n"
                f"Profile: {summary}\n"
                f"[/USER_PROFILE]\n\n"
            )

        # --- Agent routing: select specialist agent based on mode + intent ---
        _agent_spec = _agent_router.route(
            user_text,
            session_mode=_session_override.mode,
            tier=tier,
        )
        _mode_prefix = get_mode_system_prompt(_session_override.mode)
        _agent_prefix = _agent_spec.system_prompt_prefix if _agent_spec.agent_id != "chief_of_staff" else ""
        logger.debug(f"AgentRouter selected: {_agent_spec.agent_id} mode={_session_override.mode}")

        full_system_block = (
            _mode_prefix
            + _agent_prefix
            + user_block
            + "=== MEMORY FABRIC V3 ===\n"
            f"{global_block}\n"
            f"{agent_block}\n"
            f"{history_context}\n"
            "RULE: Use these contexts. Do not claim lack of memory without checking these blocks.\n"
            "========================\n\n"
        )
        # --- SELF-TRAINING GOLDEN CONTEXT (Option A) ---
        golden_examples = ""
        try:
            if bool(getattr(cfg, "self_training_enabled", True)):
                from skills.self_trainer import SelfTrainerSkill  # type: ignore

                golden_examples = SelfTrainerSkill().tool_inject_golden_context(
                    top_k=int(getattr(cfg, "self_training_golden_context_k", 20) or 20)
                )
        except Exception as _ge:
            golden_examples = ""
            logger.debug(f"Golden context injection failed (non-blocking): {_ge}")

        full_prompt = full_system_block + (golden_examples + "\n\n" if golden_examples else "") + "USER: " + user_text

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
                final_answer = "Temporary network issue on my side. Please resend now."
            elif unique_tools:
                tool_summary = ", ".join(unique_tools[:3])
                final_answer = f"Completed your request using: {tool_summary}. If you want, I can provide a concise result summary next."
            else:
                final_answer = "I had a temporary response glitch. Ask again and I'll handle it."

        final_answer, _rp_was_norm = _rp_normalizer.normalize(final_answer, _rp_intent_class)

        final_answer_before_revision = final_answer

        # --- CRITIC LAYER ---
        # Skip critic if: session override says fast, or agent spec says no critic
        _critic_should_run = (
            cfg.critic_enabled
            and not _session_override.skip_critic
            and _agent_spec.use_critic
        )
        if _critic_should_run:
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

                # --- AUTO-LOG CRITIC VERDICT FOR TRAINING ---
                try:
                    from data_engine import get_data_engine
                    from local_inference_router import LocalInferenceRouter

                    _de = get_data_engine()
                    _router = LocalInferenceRouter()
                    _intent_class, _intent_conf = _router.classify_intent(user_text)
                    _meta = {"intent": user_text[:500], "intent_class": _intent_class, "intent_confidence": _intent_conf}
                    if verdict.revised_response:
                        _de.record_correction(
                            session_id=session_id,
                            task_id=task_id,
                            channel="telegram",
                            actor="critic_auto",
                            reason_code="critic_revision",
                            rejected_output=final_answer_before_revision,
                            preferred_output=verdict.revised_response,
                            metadata=_meta,
                        )
                    elif verdict.score >= cfg.critic_score_threshold:
                        _de.record_correction(
                            session_id=session_id,
                            task_id=task_id,
                            channel="telegram",
                            actor="critic_auto",
                            reason_code="critic_approved",
                            rejected_output="",
                            preferred_output=final_answer,
                            metadata=_meta,
                        )
                except Exception as _te:
                    logger.debug(f"Auto training log failed (non-blocking): {_te}")
            except Exception as e:
                logger.warning(f"Critic evaluation failed (fail-open): {e}")

        _save_deterministic_memories(user_text, final_answer, unique_tools, delegated_agents, user_id=user_id)

        # --- INTERACTION LOG (30-day training dataset) ---
        try:
            _critic_score = verdict.score if _critic_should_run and "verdict" in dir() else -1
        except Exception:
            _critic_score = -1
        _rp_logger.log(
            user_text, final_answer, "COMPLEX",
            intent_class=_rp_intent_class,
            critic_score=_critic_score,
            was_normalized=_rp_was_norm,
            user_id=user_id,
            session_id=session_id,
        )

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
                    uploaded = _send_document_with_fallback(
                        chat_id=message.chat.id,
                        resolved_path=resolved_path,
                        raw_path=raw_path,
                        reply_to_message=message,
                    )
                    if uploaded:
                        log_activity("Chief_of_Staff", "Courier Successful", "Success", f"File: {resolved_path}")
                    else:
                        log_activity("Chief_of_Staff", "Courier Fallback", "Info", f"File: {resolved_path}")
                else:
                    send_smart_message(message.chat.id, f"Courier Error: File not found at {raw_path}", reply_to_id=message)
                    log_activity("Chief_of_Staff", "Courier Failed", "Error", f"Missing: {resolved_path}")
            else:
                send_smart_message(message.chat.id, final_answer, reply_to_id=message)
                log_activity("Chief_of_Staff", "Text Response Sent", "Success")

        # --- PASSIVE INTELLIGENCE — observe every interaction ---
        try:
            _passive_engine.observe(
                telegram_id=user_id,
                event_type="message",
                content=user_text,
                metadata={"response_preview": final_answer[:300], "session_id": session_id},
            )
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Processing error: {e}", extra={"channel": "telegram", "user_id": user_id})
        send_smart_message(
            message.chat.id,
            "Temporary execution issue on my side. Please resend now.",
            reply_to_id=message,
        )
        log_activity("System", "Error", "Failed", str(e))

# Drop any stale server-side webhook/getUpdates connections before polling starts.
# This prevents the 409 Conflict error when a previous instance died abruptly.
try:
    bot.delete_webhook(drop_pending_updates=True)
    logger.info("Webhook cleared — starting polling")
except Exception as _wh_err:
    logger.warning(f"delete_webhook failed (non-fatal): {_wh_err}")

bot.infinity_polling(timeout=60, long_polling_timeout=60)
