"""
Victor-OS Skill: Prompt Experiments
A/B test different system prompts, log critic scores for each variant,
adopt the winner automatically when statistically confident.
"""
from __future__ import annotations

import json
import os
import random
import sqlite3
import time
import uuid
from typing import Callable

from config import get_config
from data_engine import get_data_engine
from logging_config import get_logger
from skill_base import Skill, SkillManifest

logger = get_logger("prompt_experiments_skill")

_STATUS_RUNNING = "running"
_STATUS_CONCLUDED = "concluded"
_STATUS_PAUSED = "paused"


class PromptExperimentsSkill(Skill):
    """
    A/B test prompt variants. Automatically logs critic scores per variant
    and promotes the winner when a minimum sample size is reached.
    """

    def __init__(self):
        self._cfg = get_config()
        self._de = get_data_engine()
        self._db_path = os.path.join(
            self._cfg.base_dir, "memory_store", "prompt_experiments.db"
        )
        self._active_experiment: dict | None = None
        self._init_db()

    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="prompt_experiments",
            display_name="Prompt Experiments",
            version="1.0.0",
            description="A/B test system prompt variants, track critic scores, adopt winners.",
            triggers=[
                r"\bprompt.*experiment\b",
                r"\bab.*test.*prompt\b",
                r"\btest.*prompt\b",
                r"\bprompt.*variant\b",
                r"\bimprove.*prompt\b",
                r"\bexperiment.*result\b",
                r"\bwhich.*prompt.*better\b",
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
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS experiments (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'running',
                control_prompt TEXT NOT NULL,
                challenger_prompt TEXT NOT NULL,
                min_samples INTEGER DEFAULT 30,
                created_ts REAL NOT NULL,
                concluded_ts REAL,
                winner TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS experiment_results (
                id TEXT PRIMARY KEY,
                experiment_id TEXT NOT NULL,
                variant TEXT NOT NULL,
                score REAL NOT NULL,
                session_id TEXT DEFAULT '',
                ts REAL NOT NULL,
                FOREIGN KEY (experiment_id) REFERENCES experiments(id)
            );
        """)
        conn.commit()
        conn.close()
        # Load active experiment if any
        self._load_active_experiment()

    def _load_active_experiment(self) -> None:
        try:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM experiments WHERE status=? ORDER BY created_ts DESC LIMIT 1",
                (_STATUS_RUNNING,),
            ).fetchone()
            conn.close()
            self._active_experiment = dict(row) if row else None
        except Exception:
            self._active_experiment = None

    # ------------------------------------------------------------------ #
    # Public API for integration with telegram_server.py                  #
    # ------------------------------------------------------------------ #

    def get_active_prompt(self) -> str | None:
        """
        Returns the prompt variant to use for the current request (A/B selection).
        Called by telegram_server.py before building the system prompt.
        Returns None if no active experiment.
        """
        if not self._active_experiment:
            return None
        # 50/50 random assignment per request
        variant = random.choice(["control", "challenger"])
        self._active_experiment["_current_variant"] = variant
        if variant == "control":
            return self._active_experiment["control_prompt"]
        return self._active_experiment["challenger_prompt"]

    def log_score(self, score: float, session_id: str = "") -> None:
        """
        Log a critic score for the current active experiment variant.
        Called after critic evaluation in telegram_server.py.
        """
        if not self._active_experiment:
            return
        variant = self._active_experiment.get("_current_variant", "control")
        try:
            conn = self._connect()
            conn.execute(
                "INSERT INTO experiment_results (id, experiment_id, variant, score, session_id, ts) VALUES (?,?,?,?,?,?)",
                (uuid.uuid4().hex[:10], self._active_experiment["id"], variant, float(score), session_id, time.time()),
            )
            conn.commit()
            conn.close()
            # Check if we can conclude
            self._maybe_conclude_experiment()
        except Exception as exc:
            logger.debug(f"Experiment score log failed: {exc}")

    def _maybe_conclude_experiment(self) -> None:
        """Auto-conclude experiment if min_samples reached and winner is clear."""
        if not self._active_experiment:
            return
        exp_id = self._active_experiment["id"]
        min_samples = int(self._active_experiment.get("min_samples", 30))
        try:
            conn = self._connect()
            control_rows = conn.execute(
                "SELECT score FROM experiment_results WHERE experiment_id=? AND variant='control'",
                (exp_id,),
            ).fetchall()
            challenger_rows = conn.execute(
                "SELECT score FROM experiment_results WHERE experiment_id=? AND variant='challenger'",
                (exp_id,),
            ).fetchall()
            conn.close()

            if len(control_rows) < min_samples or len(challenger_rows) < min_samples:
                return  # Not enough data yet

            control_avg = sum(r["score"] for r in control_rows) / len(control_rows)
            challenger_avg = sum(r["score"] for r in challenger_rows) / len(challenger_rows)

            # Winner needs at least 0.5 point advantage to conclude
            if abs(control_avg - challenger_avg) < 0.5:
                return  # Too close to call

            winner = "challenger" if challenger_avg > control_avg else "control"
            logger.info(
                f"Experiment {exp_id} concluded: winner={winner} "
                f"control={control_avg:.2f} challenger={challenger_avg:.2f}"
            )

            conn2 = self._connect()
            conn2.execute(
                "UPDATE experiments SET status=?, concluded_ts=?, winner=? WHERE id=?",
                (_STATUS_CONCLUDED, time.time(), winner, exp_id),
            )
            conn2.commit()
            conn2.close()
            self._active_experiment = None

            # Notify
            self._de.emit_event(
                self._de.build_event_envelope(
                    "action.executed",
                    session_id="prompt_experiments",
                    task_id="",
                    actor="prompt_experiments",
                    payload={
                        "action": "experiment_concluded",
                        "winner": winner,
                        "control_avg": round(control_avg, 2),
                        "challenger_avg": round(challenger_avg, 2),
                    },
                    risk_score=0.1,
                    channel="system",
                    source="prompt_experiments",
                )
            )
        except Exception as exc:
            logger.warning(f"Experiment conclusion check failed: {exc}")

    # ------------------------------------------------------------------ #
    # Tools                                                                #
    # ------------------------------------------------------------------ #

    def tool_create_experiment(
        self,
        name: str,
        control_prompt: str,
        challenger_prompt: str,
        min_samples: int = 30,
        description: str = "",
    ) -> str:
        """
        Create a new A/B prompt experiment.
        Args:
            name: Short name for this experiment.
            control_prompt: The current/baseline system prompt addition.
            challenger_prompt: The new variant to test.
            min_samples: Minimum samples per variant before concluding.
            description: Optional description.
        """
        if not name or not control_prompt or not challenger_prompt:
            return "name, control_prompt, and challenger_prompt are all required."

        # Pause any existing running experiment
        if self._active_experiment:
            conn = self._connect()
            conn.execute(
                "UPDATE experiments SET status=? WHERE id=?",
                (_STATUS_PAUSED, self._active_experiment["id"]),
            )
            conn.commit()
            conn.close()

        exp_id = uuid.uuid4().hex[:10]
        conn = self._connect()
        conn.execute(
            """INSERT INTO experiments
               (id, name, description, status, control_prompt, challenger_prompt, min_samples, created_ts)
               VALUES (?,?,?,?,?,?,?,?)""",
            (exp_id, name, description, _STATUS_RUNNING, control_prompt, challenger_prompt,
             int(min_samples), time.time()),
        )
        conn.commit()
        conn.close()
        self._load_active_experiment()
        return (
            f"Experiment '{name}' started (id={exp_id}).\n"
            f"Min samples per variant: {min_samples}\n"
            f"Victor will now A/B test both prompt variants and automatically pick the winner."
        )

    def tool_experiment_status(self) -> str:
        """Show the status of all experiments (running, concluded, paused)."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT e.*, "
            "(SELECT COUNT(*) FROM experiment_results r WHERE r.experiment_id=e.id AND r.variant='control') as ctrl_n, "
            "(SELECT AVG(score) FROM experiment_results r WHERE r.experiment_id=e.id AND r.variant='control') as ctrl_avg, "
            "(SELECT COUNT(*) FROM experiment_results r WHERE r.experiment_id=e.id AND r.variant='challenger') as chal_n, "
            "(SELECT AVG(score) FROM experiment_results r WHERE r.experiment_id=e.id AND r.variant='challenger') as chal_avg "
            "FROM experiments ORDER BY created_ts DESC LIMIT 10"
        ).fetchall()
        conn.close()

        if not rows:
            return "No experiments found. Use tool_create_experiment to start one."

        lines = [f"{len(rows)} experiment(s):\n"]
        for r in rows:
            status_icon = {"running": "🔵", "concluded": "✅", "paused": "⏸️"}.get(r["status"], "❓")
            lines.append(f"{status_icon} [{r['id']}] {r['name']} — {r['status'].upper()}")
            lines.append(f"  Control: n={r['ctrl_n'] or 0}, avg_score={r['ctrl_avg'] or 0:.2f}")
            lines.append(f"  Challenger: n={r['chal_n'] or 0}, avg_score={r['chal_avg'] or 0:.2f}")
            if r["winner"]:
                lines.append(f"  Winner: {r['winner'].upper()}")
            lines.append(f"  Min samples: {r['min_samples']}")
            lines.append("")
        return "\n".join(lines)

    def tool_stop_experiment(self, experiment_id: str) -> str:
        """Manually stop/pause a running experiment."""
        experiment_id = str(experiment_id or "").strip()
        if not experiment_id:
            return "experiment_id is required."
        conn = self._connect()
        cur = conn.execute(
            "UPDATE experiments SET status=? WHERE id=?",
            (_STATUS_PAUSED, experiment_id),
        )
        conn.commit()
        conn.close()
        if cur.rowcount == 0:
            return f"Experiment {experiment_id} not found."
        self._active_experiment = None
        self._load_active_experiment()
        return f"Experiment {experiment_id} paused."

    def tool_get_winner_prompt(self, experiment_id: str) -> str:
        """Get the winning prompt from a concluded experiment."""
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM experiments WHERE id=?", (experiment_id,)
        ).fetchone()
        conn.close()
        if not row:
            return f"Experiment {experiment_id} not found."
        if row["status"] != _STATUS_CONCLUDED:
            return f"Experiment {experiment_id} has not concluded yet (status={row['status']})."
        winner = row["winner"]
        prompt = row[f"{winner}_prompt"]
        return f"Winner: {winner.upper()}\n\nWinning prompt:\n{prompt}"

    def get_tools(self) -> list[Callable]:
        return [
            self.tool_create_experiment,
            self.tool_experiment_status,
            self.tool_stop_experiment,
            self.tool_get_winner_prompt,
        ]


def create_skill():
    return PromptExperimentsSkill()
