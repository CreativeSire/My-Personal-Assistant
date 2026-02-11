import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Callable, Any

from config import get_config
from logging_config import get_logger


logger = get_logger("proactive_engine")


@dataclass
class ProactiveCheck:
    name: str
    interval_seconds: int
    callback: Callable[[], str | None]
    channel_hint: str | None = None
    user_id: str | None = None


class ProactiveEngine:
    """Background proactive scheduler with dedup + cooldown + rate limiting."""

    def __init__(self):
        cfg = get_config()
        self._poll_seconds = max(30, int(cfg.proactive_poll_seconds or 300))
        self._checks: list[ProactiveCheck] = []
        self._notify_callback: Callable[[str, str, str], None] | None = None
        self._channel_notifiers: dict[str, Callable[[str, str], None]] = {}
        self._last_run: dict[str, float] = {}
        self._last_sent_hash_at: dict[str, float] = {}
        self._sent_events: list[float] = []
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

        # Conservative defaults
        self._cooldown_seconds = 300
        self._dup_window_seconds = 3600
        self._max_notifications_per_hour = 12

    def register_checks_from_registry(self, checks: list[dict[str, Any]]):
        for item in checks or []:
            name = str(item.get("name", "")).strip()
            callback = item.get("callback")
            if not name or not callable(callback):
                continue
            interval = max(60, int(item.get("interval_seconds", self._poll_seconds)))
            self._checks.append(
                ProactiveCheck(
                    name=name,
                    interval_seconds=interval,
                    callback=callback,
                    channel_hint=item.get("channel_hint") or "telegram",
                    user_id=item.get("user_id") or "ceejay",
                )
            )
        logger.info(f"Registered {len(self._checks)} proactive checks")

    def set_notify_callback(self, callback: Callable[[str, str, str], None]):
        self._notify_callback = callback

    def add_channel_notifier(self, channel: str, callback: Callable[[str, str], None]):
        self._channel_notifiers[channel] = callback

    def _can_send(self, message: str) -> bool:
        now = time.time()
        with self._lock:
            self._sent_events = [t for t in self._sent_events if now - t < 3600]
            if len(self._sent_events) >= self._max_notifications_per_hour:
                return False

            digest = hashlib.sha256(message.encode("utf-8")).hexdigest()
            sent_at = self._last_sent_hash_at.get(digest)
            if sent_at and now - sent_at < self._dup_window_seconds:
                return False

            self._sent_events.append(now)
            self._last_sent_hash_at[digest] = now
            return True

    def _fanout(self, user_id: str, channel_hint: str, message: str):
        if not self._can_send(message):
            logger.debug(f"Proactive message dedup/rate-limited: {message[:120]}")
            return

        channels = []
        if channel_hint:
            channels.append(channel_hint)
        # Multi-channel fanout after origin
        for fallback in ("telegram", "whatsapp"):
            if fallback not in channels:
                channels.append(fallback)

        sent_any = False
        for channel in channels:
            notifier = self._channel_notifiers.get(channel)
            if notifier:
                try:
                    notifier(user_id, message)
                    sent_any = True
                except Exception as e:
                    logger.warning(f"Notifier failed channel={channel}: {e}")
            elif self._notify_callback:
                try:
                    self._notify_callback(user_id, channel, message)
                    sent_any = True
                except Exception as e:
                    logger.warning(f"Notify callback failed channel={channel}: {e}")

        if sent_any:
            logger.info(f"Proactive alert sent for user={user_id} via channels={channels}")

    def _run_once(self):
        now = time.time()
        for check in self._checks:
            last = self._last_run.get(check.name, 0.0)
            if now - last < check.interval_seconds:
                continue
            self._last_run[check.name] = now
            try:
                output = check.callback()
                if output:
                    user_id = check.user_id or "ceejay"
                    channel = check.channel_hint or "telegram"
                    self._fanout(user_id, channel, str(output))
            except Exception as e:
                logger.warning(f"Proactive check failed ({check.name}): {e}")

    def _loop(self):
        while self._running:
            try:
                self._run_once()
            except Exception as e:
                logger.error(f"Proactive loop error: {e}")
            time.sleep(self._poll_seconds)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("Proactive engine started")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("Proactive engine stopped")
