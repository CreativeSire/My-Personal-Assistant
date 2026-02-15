import os
import sys
import time
import schedule
import telebot

from google.adk import Runner
from google.genai import types

from agents import chief_of_staff
from config import get_config, validate_or_raise
from logging_config import get_logger, new_correlation_id, setup_logging
from resilience import telegram_breaker
from session_manager import get_session_service
from task_queue import TaskQueue
from workflow_engine import WorkflowEngine
from skills.ops_health_check import OpsHealthCheckSkill
from skills.market_watch import MarketWatchSkill
from skills.memory_hygiene import MemoryHygieneSkill


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

cfg = validate_or_raise(get_config())
setup_logging(cfg.log_dir)
logger = get_logger("daily_briefing")

bot = telebot.TeleBot(cfg.telegram_bot_token)
session_service = get_session_service()

runner = Runner(
    app_name="VictorOS_Briefing",
    agent=chief_of_staff,
    session_service=session_service,
    auto_create_session=True,
)

queue = TaskQueue()


def _collect_stream_text(events):
    final = ""
    for event in events:
        if hasattr(event, "text") and event.text:
            final += event.text
            continue
        content = getattr(event, "content", None)
        if content and getattr(content, "parts", None):
            for part in content.parts:
                if getattr(part, "text", None):
                    final += part.text
            continue
        candidates = getattr(event, "candidates", None)
        if candidates:
            for candidate in candidates:
                c_content = getattr(candidate, "content", None)
                if c_content and getattr(c_content, "parts", None):
                    for part in c_content.parts:
                        if getattr(part, "text", None):
                            final += part.text
    return final.strip()


def run_briefing_job(payload: dict) -> str:
    cid = new_correlation_id()
    target = cfg.telegram_target_id
    engine = WorkflowEngine()
    engine.load_definitions_from_dir()
    ops = OpsHealthCheckSkill()
    market = MarketWatchSkill()
    memory_hygiene = MemoryHygieneSkill()

    def _action_ops_health_summary(params, context):
        return ops.tool_ops_health_summary()

    def _action_compose_daily_ops_report(params, context):
        prompt = (
            "MORNING BRIEFING TASK:\n"
            "1. Research the current price of Bitcoin and Ethereum.\n"
            "2. Find one major AI headline in the last 24 hours.\n"
            "3. Find one key business headline from Lagos, Nigeria.\n"
            "Return a concise executive report."
        )
        new_message = types.Content(role="user", parts=[types.Part(text=prompt)])
        events = runner.run(
            user_id="briefing_system",
            session_id="daily_briefing_session",
            new_message=new_message,
        )
        text = _collect_stream_text(events)
        if not text:
            text = "Morning briefing returned no content."
        return "Victor-OS Morning Briefing\n\n" + text

    def _action_market_snapshot(params, context):
        return market.tool_market_snapshot()

    def _action_memory_hygiene(params, context):
        return memory_hygiene.tool_memory_hygiene_report()

    def _action_notify(params, context):
        return str(params.get("message", "Notification sent."))

    engine.register_action("ops_health_summary", _action_ops_health_summary)
    engine.register_action("compose_daily_ops_report", _action_compose_daily_ops_report)
    engine.register_action("market_snapshot", _action_market_snapshot)
    engine.register_action("memory_hygiene", _action_memory_hygiene)
    engine.register_action("notify", _action_notify)

    wf_name = payload.get("workflow_name", "ops_daily_brief")
    wf_run = engine.execute(wf_name)
    message = str(wf_run.context.get("step_notify_user_result") or wf_run.context.get("step_format_report_result") or "Daily briefing completed.")
    if target:
        telegram_breaker.call(bot.send_message, target, message)
    logger.info(f"Daily briefing delivered via workflow={wf_name}", extra={"channel": "telegram", "user_id": target})
    return f"Daily briefing workflow '{wf_name}' sent."


def enqueue_briefing():
    task_id = queue.enqueue(
        task_type="daily_briefing",
        payload={"workflow_name": "ops_daily_brief"},
        channel="telegram",
        user_id=cfg.telegram_target_id or "ceejay",
        priority=2,
    )
    logger.info(f"Queued daily briefing task {task_id}")


def main():
    queue.register_handler("daily_briefing", run_briefing_job)
    # Prevent this scheduler process from competing for unrelated queue tasks.
    run_worker = os.getenv("DAILY_BRIEFING_RUN_WORKER", "false").strip().lower() in {"1", "true", "yes", "on"}
    if run_worker:
        queue.start_worker()
    else:
        logger.info("Daily briefing queue worker disabled (set DAILY_BRIEFING_RUN_WORKER=true to enable)")

    schedule.every().day.at(os.getenv("DAILY_BRIEFING_TIME", "08:00")).do(enqueue_briefing)

    if cfg.daily_briefing_boot_run:
        enqueue_briefing()

    logger.info("Daily briefing scheduler online")
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
