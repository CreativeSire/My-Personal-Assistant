"""
Victor-OS Centralized Configuration
Replaces all hardcoded values and scattered os.getenv() calls.
"""

import os
import json
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
    proactive_telegram_enabled: bool = False
    proactive_email_enabled: bool = False
    proactive_severity_mode: str = "warning_and_above"
    critical_ram_threshold: int = 95
    critical_disk_threshold: int = 95
    critical_queue_depth_threshold: int = 20
    critical_consecutive_failures: int = 3
    daily_briefing_boot_run: bool = False
    market_watch_symbols: tuple[str, ...] = ("BTC", "ETH")
    market_watch_move_threshold: float = 1.5
    invoice_job_enabled: bool = True
    invoice_mode: str = "production"
    golden_input_path: str = field(default_factory=lambda: os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden", "input.zip"))
    golden_images_only: bool = True
    invoice_schema_path: str = field(default_factory=lambda: os.path.join(os.path.dirname(os.path.abspath(__file__)), "schemas", "invoice_43.schema.json"))
    golden_expected_path: str = field(default_factory=lambda: os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden", "golden43_expected.json"))
    invoice_allowed_exts: tuple[str, ...] = (
        "png",
        "jpg",
        "jpeg",
        "pdf",
        "tif",
        "tiff",
        "bmp",
        "webp",
        "heic",
    )

    # --- Critic ---
    critic_enabled: bool = True
    critic_score_threshold: int = 6

    # --- Goals ---
    goals_auto_detect: bool = True

    # --- Victor Core API (local control plane) ---
    victor_task_api_url: str = "http://127.0.0.1:8787"
    victor_api_key: str = ""

    # --- Self-training ---
    self_training_enabled: bool = True
    self_training_min_examples: int = 50
    self_training_golden_context_k: int = 20
    gemini_tuning_enabled: bool = False
    gemini_tuning_min_examples: int = 500

    # --- Voice ---
    voice_enabled: bool = False
    porcupine_access_key: str = ""
    wake_word: str = "jarvis"

    # --- File watcher ---
    file_watcher_enabled: bool = False
    file_watcher_inbox: str = ""

    # --- Calendar ---
    google_calendar_credentials: str = ""
    google_calendar_token: str = ""
    google_calendar_id: str = "primary"

    # --- Browser agent ---
    browser_agent_enabled: bool = False
    browser_cookie_store: str = "memory_store/browser_sessions"

    # --- Self-coder ---
    self_coder_enabled: bool = False
    self_coder_workspace: str = "workspace/proposed_changes"
    anthropic_api_key: str = ""

    # --- Multi-user ---
    # authorized_users is kept for backward-compat config parsing but UserRegistry is the live store
    authorized_users: tuple[dict, ...] = field(default_factory=tuple)
    default_owner_user_id: str = "ceejay"

    # --- Passive Intelligence ---
    passive_intelligence_enabled: bool = True
    passive_reflection_interval_hours: int = 6
    passive_observation_batch_size: int = 10  # trigger profile refresh after N observations

    # --- Hive Mind (Supabase) ---
    supabase_url: str = ""
    supabase_key: str = ""

    # --- Voice of God (Twilio Voice) ---
    twilio_voice_enabled: bool = False
    my_phone_number: str = ""

    # --- Dream State ---
    dream_interests: tuple[str, ...] = ("AI Research", "Stock Market", "Tech News")
    dream_interval_hours: int = 12

    @property
    def data_dir(self) -> str:
        return os.path.join(self.base_dir, "data")

    @property
    def user_db_path(self) -> str:
        return os.path.join(self.base_dir, "memory_store", "users.db")

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
    def goals_db_path(self) -> str:
        return os.path.join(self.base_dir, "memory_store", "victor_goals.db")

    @property
    def log_dir(self) -> str:
        return self.data_dir

    @property
    def log_file(self) -> str:
        return os.path.join(self.data_dir, "system_logs.csv")


def load_config() -> VictorConfig:
    def _parse_json_list(raw: str) -> list[dict]:
        raw = str(raw or "").strip()
        if not raw:
            return []
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
        except Exception:
            return []
        return []

    def _first_api_key_value(raw: str) -> str:
        """
        Support either:
        - VICTOR_API_KEY=<value>
        - VICTOR_API_KEYS=primary:<value>,rot:<value2>
        - VICTOR_API_KEYS=<value1>,<value2>
        """
        raw = str(raw or "").strip().strip('"').strip("'").strip()
        if not raw:
            return ""
        first = raw.replace("\n", ",").replace(";", ",").split(",", 1)[0].strip()
        if ":" in first:
            _kid, val = first.split(":", 1)
            return val.strip()
        return first

    symbols_raw = os.getenv("MARKET_WATCH_SYMBOLS", "BTC,ETH").strip()
    symbols = tuple([s.strip().upper() for s in symbols_raw.split(",") if s.strip()])
    invoice_exts_raw = os.getenv(
        "INVOICE_ALLOWED_EXTS",
        "png,jpg,jpeg,pdf,tif,tiff,bmp,webp,heic",
    ).strip()
    invoice_exts = tuple(
        [x.strip().lower() for x in invoice_exts_raw.split(",") if x.strip()]
    )
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
        proactive_telegram_enabled=os.getenv("PROACTIVE_TELEGRAM_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
        proactive_email_enabled=os.getenv("PROACTIVE_EMAIL_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
        proactive_severity_mode=os.getenv("PROACTIVE_SEVERITY_MODE", "warning_and_above").strip().lower(),
        critical_ram_threshold=max(1, min(100, int(os.getenv("CRITICAL_RAM_THRESHOLD", "95")))),
        critical_disk_threshold=max(1, min(100, int(os.getenv("CRITICAL_DISK_THRESHOLD", "95")))),
        critical_queue_depth_threshold=max(1, int(os.getenv("CRITICAL_QUEUE_DEPTH_THRESHOLD", "20"))),
        critical_consecutive_failures=max(1, int(os.getenv("CRITICAL_CONSECUTIVE_FAILURES", "3"))),
        daily_briefing_boot_run=os.getenv("DAILY_BRIEFING_BOOT_RUN", "false").strip().lower() in {"1", "true", "yes", "on"},
        market_watch_symbols=symbols or ("BTC", "ETH"),
        market_watch_move_threshold=float(os.getenv("MARKET_WATCH_MOVE_THRESHOLD", "1.5")),
        invoice_job_enabled=os.getenv("INVOICE_JOB_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"},
        invoice_mode=os.getenv("INVOICE_MODE", "production").strip().lower(),
        golden_input_path=os.getenv("GOLDEN_INPUT_PATH", "").strip(),
        golden_images_only=os.getenv("GOLDEN_IMAGES_ONLY", "true").strip().lower() in {"1", "true", "yes", "on"},
        invoice_schema_path=os.getenv("INVOICE_SCHEMA_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "schemas", "invoice_43.schema.json")).strip(),
        golden_expected_path=os.getenv("GOLDEN_EXPECTED_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden", "golden43_expected.json")).strip(),
        invoice_allowed_exts=invoice_exts or ("png", "jpg", "jpeg", "pdf", "tif", "tiff", "bmp", "webp", "heic"),
        critic_enabled=os.getenv("CRITIC_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"},
        critic_score_threshold=max(1, min(10, int(os.getenv("CRITIC_SCORE_THRESHOLD", "6")))),
        goals_auto_detect=os.getenv("GOALS_AUTO_DETECT", "true").strip().lower() in {"1", "true", "yes", "on"},
        victor_task_api_url=os.getenv("VICTOR_TASK_API_URL", "http://127.0.0.1:8787").strip(),
        victor_api_key=_first_api_key_value(os.getenv("VICTOR_API_KEY", "") or os.getenv("VICTOR_API_KEYS", "")),

        self_training_enabled=os.getenv("SELF_TRAINING_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"},
        self_training_min_examples=max(1, int(os.getenv("SELF_TRAINING_MIN_EXAMPLES", "50"))),
        self_training_golden_context_k=max(1, min(50, int(os.getenv("SELF_TRAINING_GOLDEN_CONTEXT_K", "20")))),
        gemini_tuning_enabled=os.getenv("GEMINI_TUNING_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
        gemini_tuning_min_examples=max(1, int(os.getenv("GEMINI_TUNING_MIN_EXAMPLES", "500"))),

        voice_enabled=os.getenv("VOICE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
        porcupine_access_key=os.getenv("PORCUPINE_ACCESS_KEY", "").strip(),
        wake_word=os.getenv("WAKE_WORD", "jarvis").strip() or "jarvis",

        file_watcher_enabled=os.getenv("FILE_WATCHER_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
        file_watcher_inbox=os.getenv("FILE_WATCHER_INBOX", "").strip(),

        google_calendar_credentials=os.getenv("GOOGLE_CALENDAR_CREDENTIALS", "").strip(),
        google_calendar_token=os.getenv("GOOGLE_CALENDAR_TOKEN", "").strip(),
        google_calendar_id=os.getenv("GOOGLE_CALENDAR_ID", "primary").strip() or "primary",

        browser_agent_enabled=os.getenv("BROWSER_AGENT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
        browser_cookie_store=os.getenv("BROWSER_COOKIE_STORE", "memory_store/browser_sessions").strip(),

        self_coder_enabled=os.getenv("SELF_CODER_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
        self_coder_workspace=os.getenv("SELF_CODER_WORKSPACE", "workspace/proposed_changes").strip(),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip(),

        authorized_users=tuple(_parse_json_list(os.getenv("VICTOR_AUTHORIZED_USERS", ""))),
        default_owner_user_id=os.getenv("VICTOR_OWNER_USER_ID", "ceejay").strip() or "ceejay",

        # --- New Trinity Configs ---
        supabase_url=os.getenv("SUPABASE_URL", "").strip(),
        supabase_key=os.getenv("SUPABASE_KEY", "").strip(),
        twilio_voice_enabled=os.getenv("TWILIO_VOICE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
        my_phone_number=os.getenv("MY_PHONE_NUMBER", "").strip(),
        dream_interests=tuple([i.strip() for i in os.getenv("DREAM_INTERESTS", "AI Research,Stock Market,Tech News").split(",") if i.strip()]),
        dream_interval_hours=int(os.getenv("DREAM_INTERVAL_HOURS", "12")),
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
    if cfg.proactive_email_enabled:
        if not cfg.email_user:
            missing.append("EMAIL_USER")
        if not cfg.email_pass:
            missing.append("EMAIL_PASS")
        if not cfg.email_target:
            missing.append("TO_EMAIL")

    if missing:
        raise VictorError(
            "Missing critical config keys: " + ", ".join(missing),
            recoverable=False,
        )
    return cfg
