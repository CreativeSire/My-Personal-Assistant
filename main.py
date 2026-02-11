import signal
import sys

from google.adk import Runner
from google.genai import types

from agents import chief_of_staff
from config import get_config, validate_or_raise
from logging_config import get_logger, new_correlation_id, setup_logging
from session_manager import get_session_service
from task_queue import TaskQueue
from tools import listen_to_user


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

cfg = validate_or_raise(get_config())
setup_logging(cfg.log_dir)
logger = get_logger("main")

session_service = get_session_service()
runner = Runner(
    app_name="VictorOS",
    agent=chief_of_staff,
    session_service=session_service,
    auto_create_session=True,
)
queue = TaskQueue()
try:
    from invoice_pipeline import run_invoice_job

    queue.register_handler("invoice_job", run_invoice_job)
except Exception:
    pass


def _extract_text(event) -> str:
    output = ""
    if hasattr(event, "text") and event.text:
        output += event.text
    content = getattr(event, "content", None)
    if content and getattr(content, "parts", None):
        for part in content.parts:
            txt = getattr(part, "text", None)
            if txt:
                output += txt
    return output


def _run_turn(user_input: str, user_id: str, session_id: str) -> str:
    msg = types.Content(role="user", parts=[types.Part(text=user_input)])
    events = runner.run(user_id=user_id, session_id=session_id, new_message=msg)
    response = ""
    for event in events:
        response += _extract_text(event)
    if not response.strip():
        response = "I received your input, but no textual output was returned."
    response = response.strip()

    # --- CRITIC LAYER ---
    if cfg.critic_enabled:
        try:
            from critic import get_critic
            verdict = get_critic().evaluate(user_input, response)
            if verdict.revised_response:
                logger.info(f"Critic revised CLI response: score={verdict.score}")
                response = verdict.revised_response
        except Exception as e:
            logger.warning(f"Critic evaluation failed (fail-open): {e}")

    return response


def run_assistant():
    print("\n==================================================")
    print("       VICTOR-OS: AUTONOMOUS AGENT SYSTEM        ")
    print("==================================================")
    print("[1] Keyboard Mode (Type)")
    print("[2] Voice Mode")
    print("--------------------------------------------------")

    mode_choice = input("Select Interface (1 or 2): ").strip()
    is_voice_mode = mode_choice == "2"

    user_id = "ceejay"
    session_id = "cli_ceejay"
    running = True

    def _shutdown_handler(signum, frame):
        nonlocal running
        running = False
        logger.info("Shutdown signal received, stopping services")

    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)

    logger.info(f"CLI mode started (voice={is_voice_mode})")
    print(f"\nSystem Online in {'VOICE' if is_voice_mode else 'TEXT'} mode. (Type 'exit' to quit)\n")

    queue.start_worker()
    try:
        while running:
            user_input = ""
            if is_voice_mode:
                user_input = listen_to_user()
                if not user_input:
                    print("...waiting for voice...")
                    continue
            else:
                user_input = input("\nYou: ").strip()

            if user_input.lower() in {"exit", "quit", "shut down"}:
                print("Victor-OS: Shutting down.")
                break

            if not user_input:
                continue

            cid = new_correlation_id()
            try:
                response = _run_turn(user_input, user_id=user_id, session_id=session_id)
                print(f"\nAssistant: {response}")
            except Exception as e:
                logger.error(f"Runtime error: {e}")
                print(f"\nRuntime Error: {e}")
    finally:
        queue.stop_worker()
        logger.info("CLI shutdown complete")


if __name__ == "__main__":
    run_assistant()
