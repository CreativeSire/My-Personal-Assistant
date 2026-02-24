"""
Victor-OS Skill: Model Router
Routes tasks to the best AI model (Gemini, Claude, GPT-4, local Llama)
based on task type, cost, speed, and capability requirements.
Tracks per-model performance and adjusts routing dynamically.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from typing import Callable

from config import get_config
from data_engine import get_data_engine
from logging_config import get_logger
from skill_base import Skill, SkillManifest

logger = get_logger("model_router_skill")

# Model definitions: id → {name, provider, cost_per_1k, speed_rank, capability_rank, requires_key}
_MODELS: dict[str, dict] = {
    "gemini-2.0-flash": {
        "name": "Gemini 2.0 Flash",
        "provider": "google",
        "cost_per_1k": 0.00015,
        "speed_rank": 1,       # 1=fastest
        "capability_rank": 2,  # 1=best
        "requires_key": "GOOGLE_API_KEY",
        "best_for": ["fast", "routine", "summarise", "chat", "classify"],
    },
    "gemini-1.5-pro": {
        "name": "Gemini 1.5 Pro",
        "provider": "google",
        "cost_per_1k": 0.00125,
        "speed_rank": 3,
        "capability_rank": 1,
        "requires_key": "GOOGLE_API_KEY",
        "best_for": ["complex", "reasoning", "document", "long_context", "code"],
    },
    "claude-opus-4-6": {
        "name": "Claude Opus 4.6",
        "provider": "anthropic",
        "cost_per_1k": 0.015,
        "speed_rank": 4,
        "capability_rank": 1,
        "requires_key": "ANTHROPIC_API_KEY",
        "best_for": ["code", "writing", "analysis", "complex", "creative"],
    },
    "claude-sonnet-4-6": {
        "name": "Claude Sonnet 4.6",
        "provider": "anthropic",
        "cost_per_1k": 0.003,
        "speed_rank": 2,
        "capability_rank": 2,
        "requires_key": "ANTHROPIC_API_KEY",
        "best_for": ["code", "reasoning", "writing", "balanced"],
    },
}

# Task type → preferred model id
_TASK_ROUTING: dict[str, str] = {
    "fast": "gemini-2.0-flash",
    "routine": "gemini-2.0-flash",
    "chat": "gemini-2.0-flash",
    "classify": "gemini-2.0-flash",
    "summarise": "gemini-2.0-flash",
    "code": "claude-sonnet-4-6",
    "writing": "claude-sonnet-4-6",
    "analysis": "gemini-1.5-pro",
    "complex": "gemini-1.5-pro",
    "long_context": "gemini-1.5-pro",
    "document": "gemini-1.5-pro",
    "creative": "claude-opus-4-6",
    "reasoning": "gemini-1.5-pro",
}

_TASK_KEYWORDS: list[tuple[str, str]] = [
    # (regex pattern, task_type)
    (r"\bcode\b|\bprogramm|\bdebug\b|\bfix.*bug\b|\brefactor\b", "code"),
    (r"\bwrite\b|\bdraft\b|\bessay\b|\bblog\b|\bletter\b", "writing"),
    (r"\banalys[ei]|\bresearch\b|\binvestigat\b", "analysis"),
    (r"\bsummar[iy]|\bbrief\b|\btldr\b", "summarise"),
    (r"\blong\b.*\bdocument\b|\bpdf\b|\bcontract\b", "long_context"),
    (r"\bcreat[ei]|\bstory\b|\bpoem\b|\bcreative\b", "creative"),
    (r"\bwhich\b|\bshould\b|\bbest\b|\bcompare\b|\breason\b", "reasoning"),
]


class ModelRouterSkill(Skill):
    """
    Routes Victor tasks to the optimal AI model.
    Tracks usage and performance. Allows manual overrides.
    """

    def __init__(self):
        self._cfg = get_config()
        self._de = get_data_engine()
        self._db_path = os.path.join(
            self._cfg.base_dir, "memory_store", "model_router.db"
        )
        self._override_model: str | None = None  # manual override
        self._init_db()

    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="model_router",
            display_name="Model Router",
            version="1.0.0",
            description="Routes tasks to the best AI model by cost, speed, and capability.",
            triggers=[
                r"\bmodel.*router\b",
                r"\bmodel.*routing\b",
                r"\brouting.*status\b",
                r"\bwhich.*model\b",
                r"\bswitch.*model\b",
                r"\buse.*claude\b",
                r"\buse.*gemini\b",
                r"\buse.*gpt\b",
                r"\buse.*grok\b",
                r"\bmodel.*usage\b",
                r"\bmodel.*status\b",
                r"\bai.*cost\b",
            ],
            enabled=True,
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        conn = self._connect()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS routing_log (
                id TEXT PRIMARY KEY,
                task_type TEXT NOT NULL,
                model_id TEXT NOT NULL,
                tokens_in INTEGER DEFAULT 0,
                tokens_out INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT 0,
                latency_ms INTEGER DEFAULT 0,
                critic_score REAL DEFAULT 0,
                ts REAL NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------ #
    # Routing logic                                                        #
    # ------------------------------------------------------------------ #

    def classify_task(self, prompt: str) -> str:
        """Classify a prompt into a task type."""
        import re
        text = (prompt or "").lower()
        for pattern, task_type in _TASK_KEYWORDS:
            if re.search(pattern, text, re.IGNORECASE):
                return task_type
        return "routine"

    def select_model(self, prompt: str = "", task_type: str = "") -> str:
        """
        Select the best model for a given prompt/task type.
        Respects manual overrides. Checks API key availability.
        """
        if self._override_model and self._override_model in _MODELS:
            return self._override_model

        task = task_type or self.classify_task(prompt)
        preferred = _TASK_ROUTING.get(task, "gemini-2.0-flash")

        # Check if the preferred model's API key is available
        model_def = _MODELS.get(preferred, {})
        key_env = model_def.get("requires_key", "")
        if key_env and not os.getenv(key_env, ""):
            # Fall back to a model with an available key
            for model_id, defn in _MODELS.items():
                fallback_key = defn.get("requires_key", "")
                if not fallback_key or os.getenv(fallback_key, ""):
                    return model_id

        return preferred

    def log_routing(
        self,
        task_type: str,
        model_id: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        latency_ms: int = 0,
        critic_score: float = 0,
    ) -> None:
        """Log a routing decision with usage stats."""
        model_def = _MODELS.get(model_id, {})
        cost = (tokens_in + tokens_out) / 1000 * model_def.get("cost_per_1k", 0)
        try:
            conn = self._connect()
            conn.execute(
                "INSERT INTO routing_log (id,task_type,model_id,tokens_in,tokens_out,cost_usd,latency_ms,critic_score,ts) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex[:10], task_type, model_id, tokens_in, tokens_out,
                 cost, latency_ms, critic_score, time.time()),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.debug(f"Routing log failed: {exc}")

    # ------------------------------------------------------------------ #
    # Tools                                                                #
    # ------------------------------------------------------------------ #

    def tool_model_status(self) -> str:
        """Show available models and which are configured."""
        lines = ["Available models:\n"]
        for model_id, defn in _MODELS.items():
            key_env = defn.get("requires_key", "")
            available = "✅" if (not key_env or os.getenv(key_env, "")) else "❌ (no API key)"
            override = " [ACTIVE OVERRIDE]" if self._override_model == model_id else ""
            lines.append(
                f"{available} {defn['name']} ({model_id}){override}\n"
                f"   Speed: {defn['speed_rank']} | Capability: {defn['capability_rank']} "
                f"| Cost: ${defn['cost_per_1k']:.5f}/1K tokens\n"
                f"   Best for: {', '.join(defn.get('best_for', []))}"
            )
            lines.append("")
        return "\n".join(lines)

    def tool_route_for_task(self, description: str) -> str:
        """Show which model would be selected for a given task description."""
        description = str(description or "").strip()
        if not description:
            return "Please provide a task description."
        task_type = self.classify_task(description)
        model_id = self.select_model(description)
        model_def = _MODELS.get(model_id, {})
        return (
            f"Task: {description[:100]}\n"
            f"Classified as: {task_type}\n"
            f"Selected model: {model_def.get('name', model_id)}\n"
            f"Cost estimate: ${model_def.get('cost_per_1k', 0):.5f}/1K tokens"
        )

    def tool_set_model_override(self, model_id: str) -> str:
        """Manually force all requests to use a specific model."""
        model_id = str(model_id or "").strip()
        if model_id.lower() in {"none", "off", "clear", "reset", ""}:
            self._override_model = None
            return "Model override cleared — automatic routing restored."
        if model_id not in _MODELS:
            available = ", ".join(_MODELS.keys())
            return f"Unknown model '{model_id}'. Available: {available}"
        self._override_model = model_id
        return f"Model override set: all requests will use {_MODELS[model_id]['name']}."

    def tool_routing_stats(self, days: int = 7) -> str:
        """Show routing statistics and cost breakdown for the last N days."""
        since = time.time() - int(days) * 86400
        conn = self._connect()
        rows = conn.execute(
            """SELECT model_id, COUNT(*) as calls, SUM(tokens_in+tokens_out) as total_tokens,
               SUM(cost_usd) as total_cost, AVG(critic_score) as avg_score, AVG(latency_ms) as avg_latency
               FROM routing_log WHERE ts > ? GROUP BY model_id ORDER BY calls DESC""",
            (since,),
        ).fetchall()
        conn.close()

        if not rows:
            return f"No routing data for the last {days} day(s)."

        total_cost = sum(r["total_cost"] or 0 for r in rows)
        lines = [f"Routing stats (last {days} days):\n"]
        for r in rows:
            name = _MODELS.get(r["model_id"], {}).get("name", r["model_id"])
            lines.append(
                f"• {name}: {r['calls']} calls | "
                f"{(r['total_tokens'] or 0):,} tokens | "
                f"${(r['total_cost'] or 0):.4f} | "
                f"score={r['avg_score'] or 0:.1f} | "
                f"latency={r['avg_latency'] or 0:.0f}ms"
            )
        lines.append(f"\nTotal cost: ${total_cost:.4f}")
        return "\n".join(lines)

    def get_tools(self) -> list[Callable]:
        return [
            self.tool_model_status,
            self.tool_route_for_task,
            self.tool_set_model_override,
            self.tool_routing_stats,
        ]


def create_skill():
    return ModelRouterSkill()
