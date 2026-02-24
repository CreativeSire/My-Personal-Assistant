"""
Victor-OS Hot Config Reload
============================
Background thread that watches the .env file for changes and reloads
safe config fields without restarting the process.

Fields that require restart (not hot-reloadable):
  - telegram_bot_token
  - memory_db_path / memory_key
  - google_api_key / gemini_api_key

Fields that ARE hot-reloadable:
  - critic_enabled, critic_score_threshold
  - proactive_enabled, proactive_poll_seconds
  - passive_intelligence_enabled
  - market_watch_move_threshold
  - self_training_enabled
  - goals_auto_detect
  - model_name (for new calls only — ongoing calls use old model)

Usage:
    from config_watcher import start_config_watcher
    start_config_watcher(notify_callback=lambda msg: bot.send_message(owner_id, msg))
"""

from __future__ import annotations

import os
import hashlib
import threading
import time
from typing import Callable, Optional

from config import get_config, load_config, VictorConfig
from logging_config import get_logger

logger = get_logger("config_watcher")

# Fields that are SAFE to hot-reload (non-infrastructure)
_HOT_RELOADABLE_FIELDS = {
    "critic_enabled",
    "critic_score_threshold",
    "proactive_enabled",
    "proactive_poll_seconds",
    "proactive_telegram_enabled",
    "proactive_email_enabled",
    "proactive_severity_mode",
    "passive_intelligence_enabled",
    "passive_reflection_interval_hours",
    "passive_observation_batch_size",
    "market_watch_move_threshold",
    "market_watch_symbols",
    "self_training_enabled",
    "self_training_min_examples",
    "goals_auto_detect",
    "model_name",
    "invoice_job_enabled",
    "file_watcher_enabled",
    "browser_agent_enabled",
    "self_coder_enabled",
    "voice_enabled",
    "daily_briefing_boot_run",
    "critical_ram_threshold",
    "critical_disk_threshold",
    "critical_queue_depth_threshold",
    "critical_consecutive_failures",
}

# Fields whose change requires a full restart
_RESTART_REQUIRED_FIELDS = {
    "telegram_bot_token",
    "google_api_key",
    "gemini_api_key",
    "memory_key",
    "memory_v3_enabled",
    "telegram_target_id",
}


class ConfigWatcher:
    """
    Polls the .env file every `interval_seconds` seconds.
    On change: reloads hot-reloadable fields in-place and notifies owner.
    On restart-required change: warns owner but does NOT restart.
    """

    def __init__(
        self,
        interval_seconds: int = 30,
        notify_callback: Optional[Callable[[str], None]] = None,
    ):
        self._interval = max(10, interval_seconds)
        self._notify = notify_callback
        self._env_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), ".env"
        )
        self._last_hash: str = ""
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def _hash_env(self) -> str:
        if not os.path.exists(self._env_path):
            return ""
        try:
            with open(self._env_path, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception:
            return ""

    def _reload(self) -> None:
        """Load fresh config and apply hot-reloadable deltas to the live singleton."""
        import config as config_module

        old_cfg: VictorConfig = config_module._config or get_config()
        try:
            new_cfg: VictorConfig = load_config()
        except Exception as exc:
            logger.warning(f"Config reload failed — keeping old config: {exc}")
            if self._notify:
                self._notify(f"Config reload failed: {exc}")
            return

        changed_hot: list[str] = []
        changed_restart: list[str] = []

        for field_name in _HOT_RELOADABLE_FIELDS:
            old_val = getattr(old_cfg, field_name, None)
            new_val = getattr(new_cfg, field_name, None)
            if old_val != new_val:
                changed_hot.append(f"{field_name}: {old_val!r} → {new_val!r}")

        for field_name in _RESTART_REQUIRED_FIELDS:
            old_val = getattr(old_cfg, field_name, None)
            new_val = getattr(new_cfg, field_name, None)
            if old_val != new_val:
                changed_restart.append(field_name)

        if not changed_hot and not changed_restart:
            return  # .env changed but no tracked field changed

        # Apply hot-reloadable changes — replace the singleton
        if changed_hot:
            # VictorConfig is frozen; swap the singleton reference
            config_module._config = new_cfg
            msg = "Config reloaded ✅\n" + "\n".join(f"  {c}" for c in changed_hot)
            logger.info(f"Hot config reload applied: {changed_hot}")
            if self._notify:
                self._notify(msg)

        if changed_restart:
            warn = (
                "⚠️ Config change detected for restart-required fields:\n"
                + "\n".join(f"  {f}" for f in changed_restart)
                + "\nRestart Victor to apply these."
            )
            logger.warning(warn)
            if self._notify:
                self._notify(warn)

    def _loop(self) -> None:
        self._last_hash = self._hash_env()
        logger.info(f"Config watcher started (interval={self._interval}s, watching {self._env_path})")
        while not self._stop_event.is_set():
            time.sleep(self._interval)
            current_hash = self._hash_env()
            if current_hash and current_hash != self._last_hash:
                logger.info("Config file change detected — reloading")
                self._last_hash = current_hash
                self._reload()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="config_watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()


# Module-level singleton
_watcher: ConfigWatcher | None = None


def start_config_watcher(
    interval_seconds: int = 30,
    notify_callback: Optional[Callable[[str], None]] = None,
) -> ConfigWatcher:
    """Start the config watcher background thread. Idempotent."""
    global _watcher
    if _watcher is None:
        _watcher = ConfigWatcher(interval_seconds=interval_seconds, notify_callback=notify_callback)
    _watcher._notify = notify_callback  # allow updating callback after init
    _watcher.start()
    return _watcher
