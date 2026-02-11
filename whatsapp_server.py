import sys

from flask import Flask, request
from google.adk import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from twilio.twiml.messaging_response import MessagingResponse

from agents import chief_of_staff
from config import get_config, validate_or_raise
from logging_config import get_logger, new_correlation_id, setup_logging
from session_manager import get_session_service, resolve_session_id, resolve_user_id


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

cfg = validate_or_raise(get_config())
setup_logging(cfg.log_dir)
logger = get_logger("whatsapp_server")

app = Flask(__name__)


def _build_session_service():
    # Primary mode: unified SQLite session memory.
    if not (cfg.app_env == "dev" and cfg.whatsapp_fallback_inmemory):
        return get_session_service()
    logger.warning("WhatsApp running in dev in-memory fallback mode")
    return InMemorySessionService()


session_service = _build_session_service()

runner = Runner(
    app_name="VictorOS_WhatsApp",
    agent=chief_of_staff,
    session_service=session_service,
    auto_create_session=True,
)


def _extract_event_text(event) -> str:
    chunks = []
    if hasattr(event, "text") and isinstance(event.text, str) and event.text.strip():
        chunks.append(event.text)
    content = getattr(event, "content", None)
    if content and getattr(content, "parts", None):
        for part in content.parts:
            part_text = getattr(part, "text", None)
            if isinstance(part_text, str) and part_text.strip():
                chunks.append(part_text)
    candidates = getattr(event, "candidates", None)
    if candidates:
        for candidate in candidates:
            c_content = getattr(candidate, "content", None)
            if c_content and getattr(c_content, "parts", None):
                for part in c_content.parts:
                    part_text = getattr(part, "text", None)
                    if isinstance(part_text, str) and part_text.strip():
                        chunks.append(part_text)
    dedup = []
    seen = set()
    for chunk in chunks:
        key = chunk.strip()
        if key and key not in seen:
            seen.add(key)
            dedup.append(key)
    return "".join(dedup)


@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    cid = new_correlation_id()
    incoming_msg = request.values.get("Body", "").strip()
    sender = request.values.get("From", "").strip()
    logger.info(
        f"WhatsApp inbound: {incoming_msg[:120]}",
        extra={"channel": "whatsapp", "user_id": sender},
    )

    new_message = types.Content(role="user", parts=[types.Part(text=incoming_msg)])
    resolved_uid = resolve_user_id("whatsapp", sender or "unknown")
    resolved_sid = resolve_session_id("whatsapp", sender or "unknown")

    try:
        events = runner.run(
            user_id=resolved_uid,
            session_id=resolved_sid,
            new_message=new_message,
        )
        result_text = ""
        for event in events:
            result_text += _extract_event_text(event)
        if not result_text.strip():
            result_text = "Task completed. No additional text returned."
    except Exception as e:
        logger.error(
            f"WhatsApp execution error: {e}",
            extra={"channel": "whatsapp", "user_id": sender},
        )
        result_text = f"Victor-OS error: {e}"

    try:
        resp = MessagingResponse()
        msg = resp.message()
        msg.body(result_text)
        return str(resp)
    except Exception as e:
        logger.error(f"Twilio response build failed: {e}", extra={"channel": "whatsapp", "user_id": sender})
        return str(MessagingResponse())


if __name__ == "__main__":
    logger.info(f"Victor-OS WhatsApp server on port {cfg.whatsapp_port}")
    app.run(port=cfg.whatsapp_port)
