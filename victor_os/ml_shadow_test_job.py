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
    parser = argparse.ArgumentParser(description="Shadow compare candidate metrics against active.")
    parser.add_argument("--candidate-metrics", default=str(Path(__file__).resolve().parent.parent / "docs" / "reports" / "training_job_latest.json"))
    parser.add_argument("--active-metrics", default=str(Path(__file__).resolve().parent.parent / "docs" / "reports" / "active_metrics.json"))
    parser.add_argument("--min-shadow-accuracy", type=float, default=0.75)
    args = parser.parse_args()

    candidate = _load_json(Path(args.candidate_metrics))
    active = _load_json(Path(args.active_metrics))
    tp = TrainingPipeline()
    result = tp.compare_shadow_vs_active_metrics(
        active_metrics=active,
        shadow_metrics=candidate,
        min_shadow_accuracy=float(args.min_shadow_accuracy),
    )
    result["generated_at"] = time.time()
    out = Path(__file__).resolve().parent.parent / "docs" / "reports" / "shadow_compare_latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"wrote={out}")
    print(json.dumps(result, indent=2))
    return 0 if bool(result.get("promotion_allowed")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
