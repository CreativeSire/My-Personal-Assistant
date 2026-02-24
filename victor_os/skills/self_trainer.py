import json
import os
import time
from typing import Callable

from config import get_config
from data_engine import get_data_engine
from skill_base import Skill, SkillManifest
from training_pipeline import TrainingPipeline


class SelfTrainerSkill(Skill):
    """
    Self-training glue:
    - reads corrections
    - exports datasets
    - emits readiness alerts
    - provides few-shot golden context (Option A)
    """

    _FEATURE_LAST_TRAIN_TS = "self_training.last_training_ts"

    def __init__(self):
        self._cfg = get_config()
        self._de = get_data_engine()

    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="self_trainer",
            display_name="Self-Training Engine",
            version="1.0.0",
            description="Analyzes correction pairs and triggers training pipeline",
            triggers=[
                r"\bself.?train\b",
                r"\btraining report\b",
                r"\btraining status\b",
                r"\bhow.*learning\b",
                r"\bimproving\b",
            ],
            schedule="daily",
            enabled=True,
        )

    def _get_last_training_ts(self) -> float:
        try:
            return float(self._de.get_feature(self._FEATURE_LAST_TRAIN_TS, 0.0))
        except Exception:
            return 0.0

    def _set_last_training_ts(self, ts: float) -> None:
        try:
            self._de.upsert_feature(feature_key=self._FEATURE_LAST_TRAIN_TS, feature_value=float(ts), metadata={"source": "self_trainer"})
        except Exception:
            pass

    def _count_new_corrections(self, since_ts: float) -> int:
        conn = self._de._connect()  # noqa: SLF001
        try:
            row = conn.execute(
                "SELECT COUNT(*) c FROM corrections WHERE ts_utc > ?",
                (float(since_ts),),
            ).fetchone()
            return int(row["c"] or 0)
        finally:
            conn.close()

    def tool_training_status(self) -> str:
        last_ts = self._get_last_training_ts()
        new_count = self._count_new_corrections(last_ts)
        registry_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "memory_store", "model_registry.json")
        registry_path = os.path.normpath(registry_path)
        active = ""
        try:
            if os.path.exists(registry_path):
                with open(registry_path, "r", encoding="utf-8") as f:
                    reg = json.load(f)
                active = str(reg.get("active_checkpoint") or "")
        except Exception:
            active = ""

        last_run = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_ts)) if last_ts else "never"
        return (
            "Self-Training Status\n"
            f"- New corrections since last run: {new_count}\n"
            f"- Last training timestamp: {last_run}\n"
            f"- Active model checkpoint: {active or 'none'}\n"
        )

    def tool_trigger_training_export(self) -> str:
        out_dir = os.path.join(self._cfg.base_dir, "memory_store", "training_exports")
        os.makedirs(out_dir, exist_ok=True)
        result = TrainingPipeline().export_dataset(out_dir=out_dir, eval_ratio=0.2)
        # Best-effort retrain of local router classifier from newly accumulated corrections.
        try:
            from local_inference_router import LocalInferenceRouter

            LocalInferenceRouter()._train_classifier()  # noqa: SLF001
        except Exception:
            pass
        # Record "training run" timestamp even if we're only exporting.
        self._set_last_training_ts(time.time())
        try:
            with open(result.manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            manifest = {"manifest_path": result.manifest_path}
        return "Training export complete\n" + json.dumps(manifest, indent=2)

    def tool_inject_golden_context(self, top_k: int = 20) -> str:
        k = max(1, min(50, int(top_k)))
        conn = self._de._connect()  # noqa: SLF001
        try:
            rows = conn.execute(
                """
                SELECT reason_code, rejected_output, preferred_output, ts_utc
                FROM corrections
                ORDER BY ts_utc DESC
                LIMIT ?
                """,
                (k,),
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            return ""

        blocks = []
        for i, row in enumerate(rows, start=1):
            rejected = str(row["rejected_output"] or "").strip()
            preferred = str(row["preferred_output"] or "").strip()
            if not preferred:
                continue
            blocks.append(
                "\n".join(
                    [
                        f"[Training Example {i}] ({row['reason_code']})",
                        f"Input: {rejected or 'User asked: ...'}",
                        f"Better Response: {preferred}",
                    ]
                )
            )
        if not blocks:
            return ""
        return "=== SELF-TRAINING GOLDEN EXAMPLES ===\n" + "\n\n".join(blocks) + "\n=== END GOLDEN EXAMPLES ==="

    def tool_training_report(self) -> str:
        features = self._de.recalc_core_features()
        total_events = int(features.get("events.total", 0))
        total_corrections = int(features.get("corrections.total", 0))
        ratio = float(features.get("quality.correction_ratio", 0.0))
        return (
            "Training Report\n"
            f"- Total events: {total_events}\n"
            f"- Total corrections: {total_corrections}\n"
            f"- Correction ratio: {ratio:.4f}\n"
        )

    def _check_training_readiness(self) -> str | None:
        last_ts = self._get_last_training_ts()
        new_count = self._count_new_corrections(last_ts)
        minimum = int(getattr(self._cfg, "self_training_min_examples", 50) or 50)
        if new_count >= minimum:
            return f"Training ready: {new_count} new examples since last run"
        return None

    def get_tools(self) -> list[Callable]:
        return [
            self.tool_training_status,
            self.tool_trigger_training_export,
            self.tool_inject_golden_context,
            self.tool_training_report,
        ]

    def get_proactive_checks(self) -> list[dict]:
        if not bool(getattr(self._cfg, "self_training_enabled", True)):
            return []
        return [
            {
                "name": "self_training_readiness",
                "interval_seconds": 86400,
                "callback": self._check_training_readiness,
                "channel_hint": "telegram",
                "user_id": self._cfg.default_owner_user_id,
            }
        ]


def create_skill():
    return SelfTrainerSkill()
