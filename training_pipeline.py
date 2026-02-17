"""
Phase 3 training/evaluation/promotion helpers.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from dataclasses import dataclass
from typing import Any

from data_engine import get_data_engine


PII_PATTERNS = [
    (re.compile(r"\b[\w\.-]+@[\w\.-]+\.\w+\b"), "<EMAIL>"),
    (re.compile(r"\b(?:\+?\d[\d\s\-]{7,}\d)\b"), "<PHONE>"),
    (re.compile(r"\b\d{10,}\b"), "<LONG_NUMBER>"),
]


def redact_text(value: str) -> str:
    out = str(value or "")
    for pattern, repl in PII_PATTERNS:
        out = pattern.sub(repl, out)
    return out


def _to_jsonl(path: str, rows: list[dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


@dataclass
class DatasetExportResult:
    train_path: str
    eval_path: str
    golden_path: str
    total_examples: int
    train_examples: int
    eval_examples: int
    golden_examples: int
    exported_at: float


class TrainingPipeline:
    def __init__(self):
        self.engine = get_data_engine()

    def export_dataset(self, out_dir: str, eval_ratio: float = 0.2, seed: int = 42) -> DatasetExportResult:
        events = self.engine.list_recent_events(limit=5000)
        examples: list[dict[str, Any]] = []
        for e in events:
            payload = e.get("payload_json") or {}
            examples.append(
                {
                    "event_id": e.get("event_id"),
                    "event_type": e.get("event_type"),
                    "task_id": e.get("task_id", ""),
                    "session_id": e.get("session_id", ""),
                    "actor": e.get("actor", ""),
                    "input": redact_text(json.dumps(payload, ensure_ascii=False)),
                    "label": e.get("event_type", "unknown"),
                    "ts_utc": e.get("ts_utc", 0.0),
                }
            )

        random.Random(seed).shuffle(examples)
        split_idx = int(len(examples) * (1.0 - float(eval_ratio)))
        train_rows = examples[:split_idx]
        eval_rows = examples[split_idx:]

        # Golden set from feedback corrections (preferred outputs).
        corrections: list[dict[str, Any]] = []
        conn = self.engine._connect()  # noqa: SLF001
        try:
            rows = conn.execute(
                """
                SELECT id, session_id, task_id, reason_code, rejected_output, preferred_output, ts_utc
                FROM corrections
                ORDER BY ts_utc DESC
                LIMIT 1000
                """
            ).fetchall()
            for row in rows:
                corrections.append(
                    {
                        "correction_id": row["id"],
                        "session_id": row["session_id"],
                        "task_id": row["task_id"],
                        "reason_code": row["reason_code"],
                        "input": redact_text(row["rejected_output"] or ""),
                        "target": redact_text(row["preferred_output"] or ""),
                        "ts_utc": row["ts_utc"],
                    }
                )
        finally:
            conn.close()

        train_path = os.path.join(out_dir, "train.jsonl")
        eval_path = os.path.join(out_dir, "eval.jsonl")
        golden_path = os.path.join(out_dir, "golden.jsonl")
        _to_jsonl(train_path, train_rows)
        _to_jsonl(eval_path, eval_rows)
        _to_jsonl(golden_path, corrections)

        return DatasetExportResult(
            train_path=train_path,
            eval_path=eval_path,
            golden_path=golden_path,
            total_examples=len(examples),
            train_examples=len(train_rows),
            eval_examples=len(eval_rows),
            golden_examples=len(corrections),
            exported_at=time.time(),
        )

    def evaluate_checkpoint(
        self,
        checkpoint_id: str,
        golden_path: str,
        accuracy_threshold: float = 0.75,
    ) -> dict[str, Any]:
        total = 0
        scored = 0
        if os.path.exists(golden_path):
            with open(golden_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    total += 1
                    row = json.loads(line)
                    # Proxy score: non-empty target and target != input.
                    inp = str(row.get("input", "")).strip()
                    tgt = str(row.get("target", "")).strip()
                    if tgt and tgt != inp:
                        scored += 1
        accuracy = (float(scored) / float(total)) if total else 0.0
        return {
            "checkpoint_id": checkpoint_id,
            "golden_total": total,
            "golden_scored": scored,
            "accuracy": accuracy,
            "passed": accuracy >= float(accuracy_threshold),
            "evaluated_at": time.time(),
        }

    def promote_checkpoint(
        self,
        checkpoint_id: str,
        metrics: dict[str, Any],
        registry_path: str,
        min_accuracy: float = 0.75,
    ) -> dict[str, Any]:
        accuracy = float(metrics.get("accuracy", 0.0))
        allowed = accuracy >= float(min_accuracy)
        registry: dict[str, Any] = {"active_checkpoint": None, "history": []}
        if os.path.exists(registry_path):
            try:
                with open(registry_path, "r", encoding="utf-8") as f:
                    registry = json.load(f)
            except Exception:
                registry = {"active_checkpoint": None, "history": []}

        event = {
            "checkpoint_id": checkpoint_id,
            "accuracy": accuracy,
            "allowed": allowed,
            "evaluated_at": metrics.get("evaluated_at", time.time()),
            "recorded_at": time.time(),
        }
        registry.setdefault("history", []).append(event)
        if allowed:
            registry["active_checkpoint"] = checkpoint_id

        os.makedirs(os.path.dirname(registry_path), exist_ok=True)
        with open(registry_path, "w", encoding="utf-8") as f:
            json.dump(registry, f, indent=2)

        return {
            "checkpoint_id": checkpoint_id,
            "allowed": allowed,
            "active_checkpoint": registry.get("active_checkpoint"),
            "registry_path": registry_path,
        }

    def shadow_deploy_checkpoint(self, checkpoint_id: str, registry_path: str) -> dict[str, Any]:
        registry: dict[str, Any] = {"active_checkpoint": None, "shadow_checkpoint": None, "history": []}
        if os.path.exists(registry_path):
            try:
                with open(registry_path, "r", encoding="utf-8") as f:
                    registry = json.load(f)
            except Exception:
                registry = {"active_checkpoint": None, "shadow_checkpoint": None, "history": []}
        registry["shadow_checkpoint"] = checkpoint_id
        registry.setdefault("history", []).append(
            {
                "event": "shadow_deploy",
                "checkpoint_id": checkpoint_id,
                "ts": time.time(),
            }
        )
        os.makedirs(os.path.dirname(registry_path), exist_ok=True)
        with open(registry_path, "w", encoding="utf-8") as f:
            json.dump(registry, f, indent=2)
        return {
            "shadow_checkpoint": checkpoint_id,
            "active_checkpoint": registry.get("active_checkpoint"),
            "registry_path": registry_path,
        }

    def promote_shadow_if_passed(
        self,
        registry_path: str,
        metrics: dict[str, Any],
        min_accuracy: float = 0.75,
    ) -> dict[str, Any]:
        if not os.path.exists(registry_path):
            return {"ok": False, "error": "registry_not_found"}
        with open(registry_path, "r", encoding="utf-8") as f:
            registry = json.load(f)
        shadow = str(registry.get("shadow_checkpoint") or "").strip()
        if not shadow:
            return {"ok": False, "error": "no_shadow_checkpoint"}
        result = self.promote_checkpoint(
            checkpoint_id=shadow,
            metrics=metrics,
            registry_path=registry_path,
            min_accuracy=min_accuracy,
        )
        if result.get("allowed"):
            registry["shadow_checkpoint"] = None
            registry.setdefault("history", []).append(
                {
                    "event": "shadow_promoted",
                    "checkpoint_id": shadow,
                    "ts": time.time(),
                }
            )
            with open(registry_path, "w", encoding="utf-8") as f:
                json.dump(registry, f, indent=2)
        return {"ok": True, "promotion": result}

    def compare_shadow_vs_active_metrics(
        self,
        *,
        active_metrics: dict[str, Any],
        shadow_metrics: dict[str, Any],
        min_shadow_accuracy: float = 0.75,
    ) -> dict[str, Any]:
        active_acc = float(active_metrics.get("accuracy", 0.0))
        shadow_acc = float(shadow_metrics.get("accuracy", 0.0))
        delta = shadow_acc - active_acc
        promotion_allowed = shadow_acc >= float(min_shadow_accuracy) and delta >= 0.0
        return {
            "active_accuracy": active_acc,
            "shadow_accuracy": shadow_acc,
            "delta_accuracy": delta,
            "min_shadow_accuracy": float(min_shadow_accuracy),
            "promotion_allowed": promotion_allowed,
            "evaluated_at": time.time(),
        }
