"""
Victor-OS Passive Intelligence Engine
Observes each user's activity (messages, voice, files, images, URLs) and
builds an evolving profile of who they are, what they work on, and how
Victor can best serve them — all without being prompted.
"""

import os
import time
import datetime
import threading
from typing import Optional, Callable

from config import get_config
from logging_config import get_logger

cfg = get_config()
logger = get_logger("passive_intelligence")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REFLECTION_INTERVAL_HOURS = getattr(cfg, "passive_reflection_interval_hours", 6)
_BATCH_SIZE = getattr(cfg, "passive_observation_batch_size", 10)

# Tracks how many observations each user has received since last reflection
_observation_counts: dict[str, int] = {}
_last_reflection: dict[str, float] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Profile reflection prompt
# ---------------------------------------------------------------------------

_REFLECTION_PROMPT_TEMPLATE = """\
You are Victor's internal profiling system. Based on the observations below,
write a concise 3-5 sentence profile of this user. Cover:
- Who they are (name, role if apparent)
- What they work on (projects, interests, recurring topics)
- Their communication style and habits
- Any goals, priorities, or concerns they've expressed
- What Victor should know to serve them better

Keep it factual and grounded only in the observations. No speculation.

USER: {name} (Telegram ID: {telegram_id})
RECENT OBSERVATIONS:
{observations}

PROFILE SUMMARY:"""


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class PassiveIntelligenceEngine:
    """
    Observes user activity and builds per-user profiles in the background.

    Usage:
        engine = PassiveIntelligenceEngine()
        engine.start()                    # starts background reflection thread

        # After each user interaction:
        engine.observe(
            telegram_id="123456",
            event_type="message",
            content="User asked about Q1 sales projections",
            metadata={"channel": "telegram"}
        )
    """

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def observe(
        self,
        telegram_id: str,
        event_type: str,
        content: str,
        metadata: Optional[dict] = None,
    ):
        """
        Record a single observation for a user and trigger a profile
        refresh if the batch threshold has been reached.

        event_type: "message" | "voice" | "image" | "document" | "url" | "file"
        """
        if not getattr(cfg, "passive_intelligence_enabled", True):
            return
        if not telegram_id or not content:
            return

        try:
            self._save_observation(telegram_id, event_type, content, metadata or {})
        except Exception as e:
            logger.debug(f"Passive observe save failed (non-blocking): {e}")
            return

        with _lock:
            _observation_counts[telegram_id] = _observation_counts.get(telegram_id, 0) + 1
            count = _observation_counts[telegram_id]

        if count >= _BATCH_SIZE:
            # Reset counter and kick off async reflection
            with _lock:
                _observation_counts[telegram_id] = 0
            t = threading.Thread(
                target=self._update_profile_summary,
                args=(telegram_id,),
                daemon=True,
                name=f"reflect_{telegram_id}",
            )
            t.start()

    def start(self):
        """Start the periodic background reflection thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._reflection_loop,
            daemon=True,
            name="passive_intelligence_reflector",
        )
        self._thread.start()
        logger.info("Passive Intelligence Engine started")

    def stop(self):
        self._running = False

    # ------------------------------------------------------------------
    # Internal — save observation to memory
    # ------------------------------------------------------------------

    def _save_observation(
        self,
        telegram_id: str,
        event_type: str,
        content: str,
        metadata: dict,
    ):
        try:
            from memory_core import save_memory, MemoryRecordInput
            from user_registry import get_user_registry
        except ImportError as e:
            logger.debug(f"Passive intelligence import failed: {e}")
            return

        registry = get_user_registry()
        user = registry.get_user(telegram_id)
        if not user:
            return

        # Check consent for this event type
        consent_flag = event_type if event_type in user.consent_flags else "messages"
        if not user.consent_flags.get(consent_flag, False):
            return

        # Truncate long content to keep memory store lean
        truncated = content[:1000] if len(content) > 1000 else content

        record = MemoryRecordInput(
            text=f"[{event_type.upper()}] {truncated}",
            memory_type="observation",
            scope="agent",
            agent_name=user.agent_scope,
            priority=2,
            source="passive_intelligence",
            tags=[event_type, "passive", f"user_{telegram_id}"],
        )
        save_memory(record)

    # ------------------------------------------------------------------
    # Internal — profile reflection via Gemini
    # ------------------------------------------------------------------

    def _update_profile_summary(self, telegram_id: str):
        """
        Queries recent observations for a user, asks Gemini to summarize
        them into a profile, and saves the result back to UserRegistry.
        """
        try:
            from memory_core import recall_memory, MemoryQueryInput
            from user_registry import get_user_registry
        except ImportError as e:
            logger.debug(f"Profile update import failed: {e}")
            return

        try:
            registry = get_user_registry()
            user = registry.get_user(telegram_id)
            if not user:
                return

            # Pull recent observations for this user
            result = recall_memory(MemoryQueryInput(
                query="recent activity observations",
                scope_filter="agent",
                agent_filter=user.agent_scope,
                type_filter=["observation"],
                top_k=30,
                min_similarity=0.0,
            ))
            hits = result.get("results", []) if result.get("ok") else []
            if not hits:
                return

            obs_lines = []
            for h in hits:
                text = str(h.get("content_plain", "")).strip()
                if text:
                    obs_lines.append(f"- {text}")

            if len(obs_lines) < 3:
                return  # Not enough data yet

            prompt = _REFLECTION_PROMPT_TEMPLATE.format(
                name=user.name,
                telegram_id=telegram_id,
                observations="\n".join(obs_lines[:25]),
            )

            summary = self._call_gemini(prompt)
            if summary:
                registry.update_profile(telegram_id, profile_summary=summary.strip())
                logger.info(f"Profile updated for user {telegram_id} ({user.name})")
                with _lock:
                    _last_reflection[telegram_id] = time.time()

        except Exception as e:
            logger.debug(f"Profile reflection failed for {telegram_id}: {e}")

    def _call_gemini(self, prompt: str) -> Optional[str]:
        try:
            from google.genai import Client
            api_key = cfg.gemini_api_key or cfg.google_api_key
            client = Client(api_key=api_key)
            response = client.models.generate_content(
                model=cfg.model_name,
                contents=prompt,
            )
            return response.text
        except Exception as e:
            logger.debug(f"Gemini profile call failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Background periodic reflection
    # ------------------------------------------------------------------

    def _reflection_loop(self):
        """
        Every N hours, check all active users and refresh their profiles
        if they have new observations since last reflection.
        """
        interval_seconds = _REFLECTION_INTERVAL_HOURS * 3600
        while self._running:
            try:
                self._run_scheduled_reflections()
            except Exception as e:
                logger.debug(f"Scheduled reflection error: {e}")
            # Sleep in 60-second chunks so stop() is responsive
            for _ in range(int(interval_seconds / 60)):
                if not self._running:
                    break
                time.sleep(60)

    def _run_scheduled_reflections(self):
        try:
            from user_registry import get_user_registry
        except ImportError:
            return

        registry = get_user_registry()
        users = registry.list_users()
        now = time.time()
        interval = _REFLECTION_INTERVAL_HOURS * 3600

        for user in users:
            last = _last_reflection.get(user.telegram_id, 0.0)
            if now - last >= interval:
                logger.debug(f"Scheduled reflection for {user.telegram_id} ({user.name})")
                self._update_profile_summary(user.telegram_id)


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_engine: Optional[PassiveIntelligenceEngine] = None


def get_passive_engine() -> PassiveIntelligenceEngine:
    global _engine
    if _engine is None:
        _engine = PassiveIntelligenceEngine()
    return _engine
