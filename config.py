"""
Victor-OS Centralized Configuration
Replaces all hardcoded values and scattered os.getenv() calls.
"""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv
from errors import VictorError

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


@dataclass(frozen=True)
class VictorConfig:
    # --- API Keys ---
    google_api_key: str = ""
    gemini_api_key: str = ""

    # --- Model ---
    model_name: str = "gemini-2.0-flash-001"
    embedding_model: str = "models/gemini-embedding-001"

    # --- Telegram ---
    telegram_bot_token: str = ""
    telegram_target_id: str = ""

    # --- Email ---
    email_user: str = ""
    email_pass: str = ""
    email_target: str = ""

    # --- Memory ---
    memory_v3_enabled: bool = True
    memory_key: str = ""
    fernet_key_env: str = "VICTOR_MEMORY_KEY"
    app_env: str = "prod"

    # --- Paths ---
    base_dir: str = field(default_factory=lambda: os.path.dirname(os.path.abspath(__file__)))

    # --- Ports ---
    whatsapp_port: int = 5000
    whatsapp_enabled: bool = False
    whatsapp_fallback_inmemory: bool = False
    twilio_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_number: str = ""
    dashboard_port: int = 8501
    monitor_ports: tuple = (8501, 7777, 3000, 8000)
    proactive_enabled: bool = False
    proactive_poll_seconds: int = 300
    notify_fanout_mode: str = "channel_aware_fanout"
    daily_briefing_boot_run: bool = False
    market_watch_symbols: tuple[str, ...] = ("BTC", "ETH")

    @property
    def data_dir(self) -> str:
        return os.path.join(self.base_dir, "data")

    @property
    def workspace_dir(self) -> str:
        return os.path.join(self.base_dir, "workspace")

    @property
    def memory_db_path(self) -> str:
        return os.path.join(self.base_dir, "memory_store", "victor_memory.db")

    @property
    def vault_db_path(self) -> str:
        return os.path.join(self.base_dir, "memory_store", "victor_vault.db")

    @property
    def vectors_dir(self) -> str:
        return os.path.join(self.base_dir, "memory_store", "victor_brain_vectors")

    @property
    def log_dir(self) -> str:
        return self.data_dir

    @property
    def log_file(self) -> str:
        return os.path.join(self.data_dir, "system_logs.csv")


def load_config() -> VictorConfig:
    symbols_raw = os.getenv("MARKET_WATCH_SYMBOLS", "BTC,ETH").strip()
    symbols = tuple([s.strip().upper() for s in symbols_raw.split(",") if s.strip()])
    return VictorConfig(
        google_api_key=os.getenv("GOOGLE_API_KEY", ""),
        gemini_api_key=os.getenv("GEMINI_API_KEY", os.getenv("GOOGLE_API_KEY", "")),
        app_env=os.getenv("APP_ENV", "prod").strip().lower(),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_target_id=os.getenv("TELEGRAM_TARGET_ID", ""),
        email_user=os.getenv("EMAIL_USER", "").strip().strip('"'),
        email_pass=os.getenv("EMAIL_PASS", "").strip().strip('"'),
        email_target=os.getenv("TO_EMAIL", "").strip().strip('"'),
        memory_v3_enabled=os.getenv("MEMORY_V3_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"},
        memory_key=os.getenv("VICTOR_MEMORY_KEY", ""),
        whatsapp_port=int(os.getenv("WHATSAPP_PORT", "5000")),
        whatsapp_enabled=os.getenv("WHATSAPP_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
        whatsapp_fallback_inmemory=os.getenv("WHATSAPP_FALLBACK_INMEMORY", "false").strip().lower() in {"1", "true", "yes", "on"},
        twilio_sid=os.getenv("TWILIO_ACCOUNT_SID", "").strip(),
        twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN", "").strip(),
        twilio_whatsapp_number=os.getenv("TWILIO_WHATSAPP_NUMBER", "").strip(),
        proactive_enabled=os.getenv("PROACTIVE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
        proactive_poll_seconds=max(30, int(os.getenv("PROACTIVE_POLL_SECONDS", "300"))),
        notify_fanout_mode=os.getenv("NOTIFY_FANOUT_MODE", "channel_aware_fanout").strip(),
        daily_briefing_boot_run=os.getenv("DAILY_BRIEFING_BOOT_RUN", "false").strip().lower() in {"1", "true", "yes", "on"},
        market_watch_symbols=symbols or ("BTC", "ETH"),
    )


# Singleton instance
_config: VictorConfig | None = None


def get_config() -> VictorConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def validate_or_raise(cfg: VictorConfig | None = None) -> VictorConfig:
    cfg = cfg or get_config()
    missing: list[str] = []

    if not (cfg.google_api_key or cfg.gemini_api_key):
        missing.append("GOOGLE_API_KEY or GEMINI_API_KEY")
    if not cfg.telegram_bot_token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if cfg.memory_v3_enabled and not cfg.memory_key:
        missing.append("VICTOR_MEMORY_KEY")
    if cfg.whatsapp_enabled:
        if not cfg.twilio_sid:
            missing.append("TWILIO_ACCOUNT_SID")
        if not cfg.twilio_auth_token:
            missing.append("TWILIO_AUTH_TOKEN")
        if not cfg.twilio_whatsapp_number:
            missing.append("TWILIO_WHATSAPP_NUMBER")
        if not cfg.whatsapp_port:
            missing.append("WHATSAPP_PORT")

    if missing:
        raise VictorError(
            "Missing critical config keys: " + ", ".join(missing),
            recoverable=False,
        )
    return cfg
