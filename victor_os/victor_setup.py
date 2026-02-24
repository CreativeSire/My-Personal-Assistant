"""
Victor-OS Interactive Setup Wizard
Run this once to configure your .env file interactively.
Usage: python victor_setup.py
"""
from __future__ import annotations

import os
import sys
import secrets
import base64


def _ask(prompt: str, default: str = "", secret: bool = False) -> str:
    display = f"{prompt} [{default}]: " if default else f"{prompt}: "
    if secret:
        import getpass
        value = getpass.getpass(display)
    else:
        value = input(display).strip()
    return value if value else default


def _generate_fernet_key() -> str:
    """Generate a valid Fernet encryption key."""
    try:
        from cryptography.fernet import Fernet
        return Fernet.generate_key().decode()
    except ImportError:
        # Fallback: generate a valid 32-byte base64url key manually
        raw = secrets.token_bytes(32)
        return base64.urlsafe_b64encode(raw).decode()


def _section(title: str) -> None:
    print(f"\n{'=' * 55}")
    print(f"  {title}")
    print(f"{'=' * 55}")


def run_setup() -> None:
    print("\n" + "=" * 55)
    print("  Welcome to Victor-OS Setup")
    print("  This wizard configures your .env file.")
    print("=" * 55)

    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        overwrite = _ask("\n.env already exists. Overwrite? (yes/no)", default="no")
        if overwrite.lower() not in {"yes", "y"}:
            print("Setup cancelled. Your existing .env was not changed.")
            return

    values: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # Owner Identity                                                       #
    # ------------------------------------------------------------------ #
    _section("1. Your Identity")
    print("  Victor needs to know who you are.")
    print("  Your Telegram user ID is a number like 123456789.")
    print("  Get it from: https://t.me/userinfobot")
    values["VICTOR_OWNER_USER_ID"] = _ask("Your Telegram User ID")
    values["APP_ENV"] = "prod"

    # ------------------------------------------------------------------ #
    # AI Keys                                                             #
    # ------------------------------------------------------------------ #
    _section("2. AI API Keys")
    print("  Victor uses Google Gemini as its primary brain.")
    print("  Get your key at: https://aistudio.google.com/app/apikey")
    values["GOOGLE_API_KEY"] = _ask("Google / Gemini API Key", secret=True)
    values["GEMINI_API_KEY"] = values["GOOGLE_API_KEY"]

    print("\n  Claude API (optional, only needed for Self-Coder skill)")
    print("  Get it at: https://console.anthropic.com/")
    anthropic = _ask("Anthropic API Key (press Enter to skip)", secret=True)
    values["ANTHROPIC_API_KEY"] = anthropic
    if anthropic:
        values["SELF_CODER_ENABLED"] = "true"
    else:
        values["SELF_CODER_ENABLED"] = "false"

    # ------------------------------------------------------------------ #
    # Telegram                                                            #
    # ------------------------------------------------------------------ #
    _section("3. Telegram Bot")
    print("  Create a bot at: https://t.me/BotFather")
    print("  Send /newbot, follow the steps, copy the token.")
    values["TELEGRAM_BOT_TOKEN"] = _ask("Telegram Bot Token", secret=True)
    values["TELEGRAM_TARGET_ID"] = values["VICTOR_OWNER_USER_ID"]

    # ------------------------------------------------------------------ #
    # Memory Encryption                                                   #
    # ------------------------------------------------------------------ #
    _section("4. Memory Encryption")
    print("  Victor encrypts sensitive memories. Generating a key for you...")
    fernet_key = _generate_fernet_key()
    print(f"  Generated: {fernet_key[:20]}...  (full key written to .env)")
    print("  IMPORTANT: Back this key up somewhere safe.")
    print("  If you lose it, encrypted memories cannot be recovered.")
    values["VICTOR_MEMORY_KEY"] = fernet_key

    # ------------------------------------------------------------------ #
    # Email (optional)                                                    #
    # ------------------------------------------------------------------ #
    _section("5. Email (Optional)")
    print("  Enables Victor to read your inbox and send emails.")
    print("  For Gmail: use an App Password (not your main password).")
    print("  Generate at: myaccount.google.com → Security → App Passwords")
    setup_email = _ask("Set up email? (yes/no)", default="no")
    if setup_email.lower() in {"yes", "y"}:
        values["EMAIL_USER"] = _ask("Email address (Gmail recommended)")
        values["EMAIL_PASS"] = _ask("App Password", secret=True)
        values["TO_EMAIL"] = values["EMAIL_USER"]
        values["PROACTIVE_EMAIL_ENABLED"] = "true"
    else:
        values["EMAIL_USER"] = ""
        values["EMAIL_PASS"] = ""
        values["TO_EMAIL"] = ""
        values["PROACTIVE_EMAIL_ENABLED"] = "false"

    # ------------------------------------------------------------------ #
    # File Watcher                                                        #
    # ------------------------------------------------------------------ #
    _section("6. File Watcher")
    print("  Victor can watch a folder on your PC and auto-ingest files.")
    default_inbox = os.path.join(os.path.expanduser("~"), "Desktop", "Victor_Inbox")
    setup_watcher = _ask("Enable file watcher? (yes/no)", default="yes")
    if setup_watcher.lower() in {"yes", "y"}:
        inbox = _ask("Inbox folder path", default=default_inbox)
        os.makedirs(inbox, exist_ok=True)
        values["FILE_WATCHER_ENABLED"] = "true"
        values["FILE_WATCHER_INBOX"] = inbox
        print(f"  Inbox folder created/confirmed: {inbox}")
    else:
        values["FILE_WATCHER_ENABLED"] = "false"
        values["FILE_WATCHER_INBOX"] = default_inbox

    # ------------------------------------------------------------------ #
    # Market Watch                                                        #
    # ------------------------------------------------------------------ #
    _section("7. Market Watch")
    print("  Victor monitors asset prices and alerts you to big moves.")
    default_symbols = "BTC,ETH,SOL,BNB,USD/NGN"
    symbols = _ask("Symbols to track (comma-separated)", default=default_symbols)
    values["MARKET_WATCH_SYMBOLS"] = symbols
    values["MARKET_WATCH_MOVE_THRESHOLD"] = _ask("Alert threshold % move", default="1.5")

    # ------------------------------------------------------------------ #
    # EC2 / Server                                                        #
    # ------------------------------------------------------------------ #
    _section("8. Server Configuration")
    print("  If running on EC2 or a remote server, set the public URL.")
    print("  Leave blank if running locally only.")
    victor_url = _ask("Victor public URL (e.g. https://victor.yourdomain.com)", default="")
    values["VICTOR_PUBLIC_URL"] = victor_url
    values["VICTOR_TASK_API_URL"] = victor_url + "/api" if victor_url else "http://127.0.0.1:8787"

    # API key for the REST control plane
    api_key = secrets.token_urlsafe(32)
    values["VICTOR_API_KEY"] = api_key
    print(f"  Generated REST API key: {api_key[:16]}...  (full key in .env)")

    # ------------------------------------------------------------------ #
    # Defaults for everything else                                        #
    # ------------------------------------------------------------------ #
    values.update({
        "PROACTIVE_ENABLED": "true",
        "PROACTIVE_TELEGRAM_ENABLED": "true",
        "PROACTIVE_POLL_SECONDS": "300",
        "PROACTIVE_SEVERITY_MODE": "warning_and_above",
        "DAILY_BRIEFING_BOOT_RUN": "true",
        "INVOICE_JOB_ENABLED": "true",
        "INVOICE_MODE": "production",
        "INVOICE_ALLOWED_EXTS": "png,jpg,jpeg,pdf,tif,tiff,bmp,webp,heic",
        "WHATSAPP_ENABLED": "false",
        "TWILIO_ACCOUNT_SID": "",
        "TWILIO_AUTH_TOKEN": "",
        "TWILIO_WHATSAPP_NUMBER": "",
        "SELF_TRAINING_ENABLED": "true",
        "SELF_TRAINING_MIN_EXAMPLES": "50",
        "SELF_TRAINING_GOLDEN_CONTEXT_K": "20",
        "GEMINI_TUNING_ENABLED": "false",
        "GEMINI_TUNING_MIN_EXAMPLES": "500",
        "LOCAL_ROUTER_THRESHOLD": "0.7",
        "LOCAL_ROUTER_ALLOWED_CLASSES": "ops_status,filesystem_routine,invoice_routine",
        "LOCAL_ROUTER_MODEL_PATH": "memory_store/intent_classifier.pkl",
        "VOICE_ENABLED": "false",
        "PORCUPINE_ACCESS_KEY": "",
        "WAKE_WORD": "victor",
        "GOOGLE_CALENDAR_CREDENTIALS": "memory_store/google_credentials.json",
        "GOOGLE_CALENDAR_TOKEN": "memory_store/google_calendar_token.json",
        "GOOGLE_CALENDAR_ID": "primary",
        "BROWSER_AGENT_ENABLED": "false",
        "BROWSER_COOKIE_STORE": "memory_store/browser_sessions",
        "SELF_CODER_WORKSPACE": "workspace/proposed_changes",
        "CRITIC_ENABLED": "true",
        "CRITIC_SCORE_THRESHOLD": "6",
        "GOALS_AUTO_DETECT": "true",
        "VICTOR_AUTHORIZED_USERS": "[]",
        "WEBHOOK_ENABLED": "false",
        "WEBHOOK_PORT": "7878",
        "STRIPE_WEBHOOK_SECRET": "",
        "GITHUB_WEBHOOK_SECRET": "",
    })

    # ------------------------------------------------------------------ #
    # Write .env                                                          #
    # ------------------------------------------------------------------ #
    _section("Writing .env")

    lines = [
        "# Victor-OS Environment Configuration",
        "# Generated by victor_setup.py",
        "# DO NOT commit this file to git.",
        "",
        "# --- Owner ---",
        f"VICTOR_OWNER_USER_ID={values['VICTOR_OWNER_USER_ID']}",
        f"APP_ENV={values['APP_ENV']}",
        "",
        "# --- API Keys ---",
        f"GOOGLE_API_KEY={values['GOOGLE_API_KEY']}",
        f"GEMINI_API_KEY={values['GEMINI_API_KEY']}",
        f"ANTHROPIC_API_KEY={values['ANTHROPIC_API_KEY']}",
        "",
        "# --- Memory Encryption (BACK THIS UP) ---",
        f"VICTOR_MEMORY_KEY={values['VICTOR_MEMORY_KEY']}",
        "",
        "# --- Telegram ---",
        f"TELEGRAM_BOT_TOKEN={values['TELEGRAM_BOT_TOKEN']}",
        f"TELEGRAM_TARGET_ID={values['TELEGRAM_TARGET_ID']}",
        "",
        "# --- Email ---",
        f"EMAIL_USER={values['EMAIL_USER']}",
        f"EMAIL_PASS={values['EMAIL_PASS']}",
        f"TO_EMAIL={values['TO_EMAIL']}",
        "",
        "# --- Server / EC2 ---",
        f"VICTOR_PUBLIC_URL={values['VICTOR_PUBLIC_URL']}",
        f"VICTOR_TASK_API_URL={values['VICTOR_TASK_API_URL']}",
        f"VICTOR_API_KEY={values['VICTOR_API_KEY']}",
        "",
        "# --- Proactive Engine ---",
        f"PROACTIVE_ENABLED={values['PROACTIVE_ENABLED']}",
        f"PROACTIVE_TELEGRAM_ENABLED={values['PROACTIVE_TELEGRAM_ENABLED']}",
        f"PROACTIVE_EMAIL_ENABLED={values['PROACTIVE_EMAIL_ENABLED']}",
        f"PROACTIVE_POLL_SECONDS={values['PROACTIVE_POLL_SECONDS']}",
        f"PROACTIVE_SEVERITY_MODE={values['PROACTIVE_SEVERITY_MODE']}",
        f"DAILY_BRIEFING_BOOT_RUN={values['DAILY_BRIEFING_BOOT_RUN']}",
        "",
        "# --- Market Watch ---",
        f"MARKET_WATCH_SYMBOLS={values['MARKET_WATCH_SYMBOLS']}",
        f"MARKET_WATCH_MOVE_THRESHOLD={values['MARKET_WATCH_MOVE_THRESHOLD']}",
        "",
        "# --- File Watcher ---",
        f"FILE_WATCHER_ENABLED={values['FILE_WATCHER_ENABLED']}",
        f"FILE_WATCHER_INBOX={values['FILE_WATCHER_INBOX']}",
        "",
        "# --- Self-Training ---",
        f"SELF_TRAINING_ENABLED={values['SELF_TRAINING_ENABLED']}",
        f"SELF_TRAINING_MIN_EXAMPLES={values['SELF_TRAINING_MIN_EXAMPLES']}",
        f"SELF_TRAINING_GOLDEN_CONTEXT_K={values['SELF_TRAINING_GOLDEN_CONTEXT_K']}",
        f"GEMINI_TUNING_ENABLED={values['GEMINI_TUNING_ENABLED']}",
        f"GEMINI_TUNING_MIN_EXAMPLES={values['GEMINI_TUNING_MIN_EXAMPLES']}",
        "",
        "# --- Local Router ---",
        f"LOCAL_ROUTER_THRESHOLD={values['LOCAL_ROUTER_THRESHOLD']}",
        f"LOCAL_ROUTER_ALLOWED_CLASSES={values['LOCAL_ROUTER_ALLOWED_CLASSES']}",
        f"LOCAL_ROUTER_MODEL_PATH={values['LOCAL_ROUTER_MODEL_PATH']}",
        "",
        "# --- Voice ---",
        f"VOICE_ENABLED={values['VOICE_ENABLED']}",
        f"PORCUPINE_ACCESS_KEY={values['PORCUPINE_ACCESS_KEY']}",
        f"WAKE_WORD={values['WAKE_WORD']}",
        "",
        "# --- Google Calendar ---",
        f"GOOGLE_CALENDAR_CREDENTIALS={values['GOOGLE_CALENDAR_CREDENTIALS']}",
        f"GOOGLE_CALENDAR_TOKEN={values['GOOGLE_CALENDAR_TOKEN']}",
        f"GOOGLE_CALENDAR_ID={values['GOOGLE_CALENDAR_ID']}",
        "",
        "# --- Browser Agent ---",
        f"BROWSER_AGENT_ENABLED={values['BROWSER_AGENT_ENABLED']}",
        f"BROWSER_COOKIE_STORE={values['BROWSER_COOKIE_STORE']}",
        "",
        "# --- Self-Coder ---",
        f"SELF_CODER_ENABLED={values['SELF_CODER_ENABLED']}",
        f"SELF_CODER_WORKSPACE={values['SELF_CODER_WORKSPACE']}",
        "",
        "# --- Invoice Pipeline ---",
        f"INVOICE_JOB_ENABLED={values['INVOICE_JOB_ENABLED']}",
        f"INVOICE_MODE={values['INVOICE_MODE']}",
        f"INVOICE_ALLOWED_EXTS={values['INVOICE_ALLOWED_EXTS']}",
        "",
        "# --- WhatsApp (Twilio) ---",
        f"WHATSAPP_ENABLED={values['WHATSAPP_ENABLED']}",
        f"TWILIO_ACCOUNT_SID={values['TWILIO_ACCOUNT_SID']}",
        f"TWILIO_AUTH_TOKEN={values['TWILIO_AUTH_TOKEN']}",
        f"TWILIO_WHATSAPP_NUMBER={values['TWILIO_WHATSAPP_NUMBER']}",
        "",
        "# --- Webhooks ---",
        f"WEBHOOK_ENABLED={values['WEBHOOK_ENABLED']}",
        f"WEBHOOK_PORT={values['WEBHOOK_PORT']}",
        f"STRIPE_WEBHOOK_SECRET={values['STRIPE_WEBHOOK_SECRET']}",
        f"GITHUB_WEBHOOK_SECRET={values['GITHUB_WEBHOOK_SECRET']}",
        "",
        "# --- Critic ---",
        f"CRITIC_ENABLED={values['CRITIC_ENABLED']}",
        f"CRITIC_SCORE_THRESHOLD={values['CRITIC_SCORE_THRESHOLD']}",
        "",
        "# --- Goals ---",
        f"GOALS_AUTO_DETECT={values['GOALS_AUTO_DETECT']}",
        "",
        "# --- Multi-User ---",
        f"VICTOR_AUTHORIZED_USERS={values['VICTOR_AUTHORIZED_USERS']}",
    ]

    with open(env_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\n  .env written to: {env_path}")

    # ------------------------------------------------------------------ #
    # Summary                                                             #
    # ------------------------------------------------------------------ #
    print("\n" + "=" * 55)
    print("  Setup Complete!")
    print("=" * 55)
    print("""
Next steps:
  1. Start Victor:
       python telegram_server.py

  2. Open Telegram, find your bot, send /start

  3. Drop a file in your Victor_Inbox folder
     (if file watcher is enabled)

  4. Use Victor for EVERYTHING — questions, tasks,
     research, notes, corrections. The more you use it,
     the smarter it gets.

  5. When ready to move to EC2:
       python deploy/deploy_ec2.py

Remember:
  - Back up your VICTOR_MEMORY_KEY — it encrypts your memories
  - Correct Victor when it's wrong — those corrections train it
  - Your interaction data starts accumulating from day one
""")


if __name__ == "__main__":
    try:
        run_setup()
    except KeyboardInterrupt:
        print("\n\nSetup cancelled.")
        sys.exit(0)
