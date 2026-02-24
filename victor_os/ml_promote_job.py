from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from training_pipeline import TrainingPipeline


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote checkpoint with governance metadata.")
    parser.add_argument("--checkpoint-id", default="")
    parser.add_argument("--metrics", default=str(Path(__file__).resolve().parent.parent / "docs" / "reports" / "training_job_latest.json"))
    parser.add_argument("--registry", default=str(Path(__file__).resolve().parent / "memory_store" / "model_registry.json"))
    parser.add_argument("--min-accuracy", type=float, default=0.75)
    parser.add_argument("--model-track", default="routine_policy_model")
    parser.add_argument("--safety-gate-passed", action="store_true")
    parser.add_argument("--regression-delta", type=float, default=0.0)
    args = parser.parse_args()

    metrics = _load_json(Path(args.metrics))
    checkpoint_id = args.checkpoint_id.strip() or str(metrics.get("checkpoint_id") or f"ckpt_promote_{int(time.time())}")
    manifest_hash = str(metrics.get("training_manifest_hash") or "")
    tp = TrainingPipeline()
    result = tp.promote_checkpoint(
        checkpoint_id=checkpoint_id,
        metrics=metrics,
        registry_path=args.registry,
        min_accuracy=float(args.min_accuracy),
        model_track=args.model_track,
        training_manifest_hash=manifest_hash,
        safety_gate_passed=bool(args.safety_gate_passed),
        regression_delta=float(args.regression_delta),
    )
    out = Path(__file__).resolve().parent.parent / "docs" / "reports" / "promotion_latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"wrote={out}")
    print(json.dumps(result, indent=2))
    return 0 if bool(result.get("allowed")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
