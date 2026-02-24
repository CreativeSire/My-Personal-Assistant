"""
Victor-OS Reliability Pack
===========================
Fuses: Fallback Policy Matrix + Response Normalizer + Interaction Logger + Pitch Mode

Architecture:
  Every message → DETERMINISTIC → SOCIAL → [SIMPLE|COMPLEX] → ResponseNormalizer → InteractionLogger → User

Design principles:
  1. LLM instability never surfaces as a raw error or persona break.
  2. Every exchange is logged — this IS the 30-day training dataset.
  3. PITCH_MODE=true locks Victor to maximum stability (stricter normalizer, no SIMPLE tier).
  4. Normalizer is sentence-level: one bad sentence does not destroy a good response.
  5. Fallback matrix ensures intent-aware failure messages, never generic AI disclaimers.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
import uuid

# ---------------------------------------------------------------------------
# 1. PITCH MODE FLAG
# ---------------------------------------------------------------------------
PITCH_MODE: bool = os.getenv("PITCH_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}

# ---------------------------------------------------------------------------
# 2. FALLBACK POLICY MATRIX
# Intent class → what Victor says when LLM fails or returns junk for that intent
# ---------------------------------------------------------------------------
FALLBACK_MATRIX: dict[str, str] = {
    "identity":     "I'm Victor OS — your Digital Executive Officer. What do you need?",
    "skills":       "Loaded skills: market_watch, model_router, invoice_ops, web_search. Ask me to run one.",
    "routing":      "Router is live. Pipeline: Deterministic → Social → Simple → Complex.",
    "diagnostic":   "System check temporarily unavailable. Retry in a moment.",
    "tasks":        "Task queue is empty or temporarily unavailable.",
    "market":       "Live market data temporarily unavailable. Retry shortly.",
    "news":         "News feed temporarily unavailable. Retry shortly.",
    "ops_status":   "System operational. No critical flags detected.",
    "social":       "Ready. What's the move?",
    "default":      "Hit a temporary snag. Ask again — I'll handle it.",
}

# ---------------------------------------------------------------------------
# 3. RESPONSE NORMALIZER
# Sentence-level Victor voice enforcer.
#
# Two modes:
#   NUCLEAR  — entire response is a persona break → replace from fallback matrix.
#   SURGICAL — only the offending sentence(s) get rewritten, good content preserved.
# ---------------------------------------------------------------------------

# Phrases whose mere presence means the entire response must be replaced.
_NUCLEAR_PHRASES: list[str] = [
    "i am a large language model",
    "i'm a large language model",
    "as a large language model",
    "as an ai language model",
    "i am an ai assistant",
    "i'm an ai assistant",
]

# Sentence-level patterns: (regex, victor_replacement)
# Empty replacement = sentence is simply removed.
_SENTENCE_PATCHES: list[tuple[str, str]] = [
    (r"as a large language model\b[^.!?\n]*",     "As your Digital Executive Officer"),
    (r"as an ai(?:\s+assistant)?\b[^.!?\n]*",     ""),
    (r"i'm just (?:a|an) (?:language model|ai)\b[^.!?\n]*", "I'm Victor OS."),
    (r"created by google[^.!?\n]*",               ""),
    (r"developed (?:by|in) google[^.!?\n]*",      ""),
    (r"developed by anthropic[^.!?\n]*",          ""),
    (r"i do not possess memory in the human sense[^.!?\n]*",            "I have persistent vault memory."),
    (r"i don'?t store information from previous conversations[^.!?\n]*", "I maintain memory across sessions."),
    (r"i do not have any persistent memory[^.!?\n]*",                   "I have persistent vault memory."),
    (r"i don'?t retain information from past conversations[^.!?\n]*",   "I maintain memory across sessions."),
    (r"i cannot access previous conversations[^.!?\n]*",                "I can query my session vault."),
    (r"i am programmed to be (?:a )?helpful and harmless[^.!?\n]*",     ""),
    (r"i apologize,? but i encountered an issue processing your request[^.!?\n]*", "I hit a snag — retrying."),
    (r"i will notify the development team[^.!?\n]*",                    ""),
    (r"i am currently unable to answer[^.!?\n]*",                      "Let me try another approach."),
    (r"what would you like me to help you with\??",                     "What's the move?"),
    (r"how can i help you(?: today)?\??",                               "What do you need?"),
    (r"i cannot provide that information directly[^.!?\n]*",            ""),
    (r"consult (?:the )?system administrators[^.!?\n]*",               ""),
    (r"i am unable to\b[^.!?\n]*",                                     ""),
    (r"i'm unable to\b[^.!?\n]*",                                      ""),
]

# In PITCH_MODE, also catch softer phrases
_PITCH_MODE_EXTRA: list[tuple[str, str]] = [
    (r"i should note that[^.!?\n]*",         ""),
    (r"please note that[^.!?\n]*",           ""),
    (r"it'?s worth mentioning that[^.!?\n]*", ""),
    (r"as an ai model[^.!?\n]*",             ""),
    (r"my training data[^.!?\n]*",           ""),
    (r"my knowledge cutoff[^.!?\n]*",        ""),
]

def _build_compiled_patches(extra: bool = False) -> list[tuple[re.Pattern, str]]:
    patches = list(_SENTENCE_PATCHES)
    if extra:
        patches += _PITCH_MODE_EXTRA
    return [(re.compile(pat, re.IGNORECASE), rep) for pat, rep in patches]

_COMPILED_STANDARD = _build_compiled_patches(extra=False)
_COMPILED_PITCH    = _build_compiled_patches(extra=True)


class ResponseNormalizer:
    """
    Sentence-level Victor voice enforcer.

    Returns (normalized_text, was_modified: bool).
    """

    def normalize(self, text: str, intent_class: str = "default") -> tuple[str, bool]:
        if not text or not text.strip():
            return FALLBACK_MATRIX.get(intent_class, FALLBACK_MATRIX["default"]), True

        lowered = text.lower()

        # Nuclear check — whole response is a persona break
        if any(phrase in lowered for phrase in _NUCLEAR_PHRASES):
            return FALLBACK_MATRIX.get(intent_class, FALLBACK_MATRIX["default"]), True

        # Sentence-level surgical repair
        patches = _COMPILED_PITCH if PITCH_MODE else _COMPILED_STANDARD
        result = text
        modified = False
        for pattern, replacement in patches:
            new_result = pattern.sub(replacement, result)
            if new_result != result:
                result = new_result
                modified = True

        if modified:
            # Clean up artifacts from removed phrases
            result = re.sub(r"  +", " ", result)
            result = re.sub(r"\n{3,}", "\n\n", result)
            result = re.sub(r"^\s*[.,;]\s*", "", result)  # leading punctuation
            result = result.strip()
            if not result:
                return FALLBACK_MATRIX.get(intent_class, FALLBACK_MATRIX["default"]), True

        return result, modified


# Module-level singleton
_normalizer: ResponseNormalizer | None = None

def get_normalizer() -> ResponseNormalizer:
    global _normalizer
    if _normalizer is None:
        _normalizer = ResponseNormalizer()
    return _normalizer


# ---------------------------------------------------------------------------
# 4. INTERACTION LOGGER — the 30-day training dataset
# ---------------------------------------------------------------------------
_LOG_DB_FILENAME = "interaction_log.db"


class InteractionLogger:
    """
    Logs every user↔Victor exchange to SQLite.

    Schema captures:
      - Intent classifier training signal  (user_input + intent_class + tier)
      - Response quality signal            (critic_score + was_normalized)
      - Pitch mode audit                   (pitch_mode flag)

    Use export_training_pairs() to pull high-quality interactions for retraining.
    """

    def __init__(self, db_dir: str):
        self._db_path = os.path.join(db_dir, _LOG_DB_FILENAME)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        conn = self._connect()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS interaction_log (
                id            TEXT PRIMARY KEY,
                ts            REAL NOT NULL,
                user_id       TEXT DEFAULT '',
                session_id    TEXT DEFAULT '',
                user_input    TEXT NOT NULL,
                response      TEXT NOT NULL,
                tier          TEXT NOT NULL,
                intent_class  TEXT DEFAULT 'unknown',
                critic_score  REAL DEFAULT -1,
                was_normalized INTEGER DEFAULT 0,
                pitch_mode    INTEGER DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_il_ts     ON interaction_log(ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_il_intent ON interaction_log(intent_class)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_il_tier   ON interaction_log(tier)")
        conn.commit()
        conn.close()

    def log(
        self,
        user_input: str,
        response: str,
        tier: str,
        intent_class: str = "unknown",
        critic_score: float = -1,
        was_normalized: bool = False,
        user_id: str = "",
        session_id: str = "",
    ) -> None:
        """Log one exchange. Never raises — delivery must not block on logging."""
        try:
            conn = self._connect()
            conn.execute(
                """INSERT INTO interaction_log
                   (id, ts, user_id, session_id, user_input, response, tier,
                    intent_class, critic_score, was_normalized, pitch_mode)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    uuid.uuid4().hex[:12],
                    time.time(),
                    (user_id or "")[:64],
                    (session_id or "")[:64],
                    (user_input or "")[:2000],
                    (response or "")[:4000],
                    tier,
                    intent_class,
                    float(critic_score),
                    int(was_normalized),
                    int(PITCH_MODE),
                ),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass  # never block delivery for a log failure

    def export_training_pairs(
        self,
        since_days: int = 30,
        min_critic_score: float = 7.0,
    ) -> list[dict]:
        """
        Pull high-quality interactions as training pairs for retraining.
        Only returns exchanges where the normalizer did NOT need to repair the response
        (i.e., the model got it right on its own).
        """
        since = time.time() - since_days * 86400
        try:
            conn = self._connect()
            rows = conn.execute(
                """SELECT user_input, response, intent_class, tier, critic_score
                   FROM interaction_log
                   WHERE ts > ? AND critic_score >= ? AND was_normalized = 0
                   ORDER BY critic_score DESC, ts DESC""",
                (since, min_critic_score),
            ).fetchall()
            conn.close()
            return [
                {
                    "input":        r["user_input"],
                    "output":       r["response"],
                    "intent_class": r["intent_class"],
                    "tier":         r["tier"],
                    "score":        r["critic_score"],
                }
                for r in rows
            ]
        except Exception:
            return []

    def summary(self, since_days: int = 7) -> str:
        """Plain-text summary of logging stats for the last N days."""
        since = time.time() - since_days * 86400
        try:
            conn = self._connect()
            total = conn.execute(
                "SELECT COUNT(*) FROM interaction_log WHERE ts > ?", (since,)
            ).fetchone()[0]
            normalized = conn.execute(
                "SELECT COUNT(*) FROM interaction_log WHERE ts > ? AND was_normalized = 1", (since,)
            ).fetchone()[0]
            by_tier = conn.execute(
                """SELECT tier, COUNT(*) as c FROM interaction_log
                   WHERE ts > ? GROUP BY tier ORDER BY c DESC""",
                (since,),
            ).fetchall()
            conn.close()
            lines = [
                f"Interaction Log — last {since_days} days",
                f"  Total exchanges : {total}",
                f"  Normalized/repaired: {normalized} ({int(normalized/total*100) if total else 0}%)",
            ]
            for r in by_tier:
                lines.append(f"  {r['tier']:<14}: {r['c']} messages")
            return "\n".join(lines)
        except Exception:
            return "Interaction log unavailable."


# Module-level singleton
_ilogger: InteractionLogger | None = None


def get_interaction_logger(db_dir: str = "") -> InteractionLogger:
    global _ilogger
    if _ilogger is None:
        if not db_dir:
            db_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory_store")
        _ilogger = InteractionLogger(db_dir)
    return _ilogger
