import hashlib
import json
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
        self._cfg = cfg
        self._last_run: dict[str, float] = {}
        self._last_sent_hash_at: dict[str, float] = {}
        self._critical_streak: dict[str, int] = {}
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
        message = self._format_message(message)
        if not self._can_send(message):
            logger.debug(f"Proactive message dedup/rate-limited: {message[:120]}")
            return

        channels = self._build_channels(channel_hint)
        if not channels:
            logger.info("Proactive alert suppressed: no enabled outbound channels")
            return

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

    def _is_channel_enabled(self, channel: str) -> bool:
        if channel == "email":
            return bool(self._cfg.proactive_email_enabled)
        if channel == "telegram":
            return bool(self._cfg.proactive_telegram_enabled)
        if channel == "whatsapp":
            return bool(self._cfg.whatsapp_enabled)
        return False

    def _build_channels(self, channel_hint: str | None) -> list[str]:
        channels: list[str] = []
        hint = (channel_hint or "").strip().lower()
        if hint and self._is_channel_enabled(hint):
            channels.append(hint)

        if self._cfg.notify_fanout_mode == "channel_aware_fanout":
            for fallback in ("email", "telegram", "whatsapp"):
                if fallback not in channels and self._is_channel_enabled(fallback):
                    channels.append(fallback)
        elif self._cfg.notify_fanout_mode == "email_only":
            if self._is_channel_enabled("email"):
                channels = ["email"]

        return channels

    def _normalize_severity(self, value: str | None) -> str:
        raw = (value or "").strip().lower()
        if raw in {"critical", "warning", "info"}:
            return raw
        return "warning"

    def _format_message(self, message: str) -> str:
        text = str(message or "").strip()
        if not text:
            return text
        try:
            parsed = json.loads(text)
        except Exception:
            return text
        if not isinstance(parsed, dict):
            return text

        alert = str(parsed.get("alert", "")).strip()
        if alert == "ops_health_threshold":
            cpu = parsed.get("cpu", 0)
            ram = parsed.get("ram", 0)
            disk = parsed.get("disk", 0)
            queue_depth = parsed.get("queue_depth", 0)
            return (
                "Ops Health Alert\n"
                f"- CPU: {cpu}%\n"
                f"- RAM: {ram}%\n"
                f"- DISK: {disk}%\n"
                f"- Queue depth: {queue_depth}\n"
            )
        return text

    def _parse_check_output(self, output: str | dict[str, Any]) -> tuple[str, str]:
        if isinstance(output, dict):
            payload = output
            text = json.dumps(payload, ensure_ascii=False)
        else:
            text = str(output or "")
            payload = None
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    payload = parsed
            except Exception:
                payload = None
        severity = self._normalize_severity((payload or {}).get("severity"))
        return text, severity

    def _is_sendable_severity(self, severity: str) -> bool:
        mode = (self._cfg.proactive_severity_mode or "critical_only").lower()
        if mode == "critical_only":
            return severity == "critical"
        if mode == "warning_and_above":
            return severity in {"critical", "warning"}
        # "all" or any other value → send everything
        return severity in {"critical", "warning", "info"}

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
                    text, severity = self._parse_check_output(output)
                    if not self._is_sendable_severity(severity):
                        logger.info(
                            f"Proactive check '{check.name}' skipped by severity policy: {severity}"
                        )
                        continue
                    if severity == "critical":
                        streak = self._critical_streak.get(check.name, 0) + 1
                        self._critical_streak[check.name] = streak
                        if streak < int(self._cfg.critical_consecutive_failures or 3):
                            logger.info(
                                f"Proactive check '{check.name}' buffered critical streak={streak}"
                            )
                            continue
                    else:
                        self._critical_streak[check.name] = 0
                    user_id = check.user_id or "ceejay"
                    channel = check.channel_hint or "email"
                    self._fanout(user_id, channel, text)
                else:
                    self._critical_streak[check.name] = 0
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
