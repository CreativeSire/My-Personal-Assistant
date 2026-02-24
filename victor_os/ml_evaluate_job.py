from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from training_pipeline import TrainingPipeline


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate candidate checkpoint against golden set proxy metrics.")
    parser.add_argument("--checkpoint-id", default="")
    parser.add_argument("--export-dir", default=str(Path(__file__).resolve().parent / "memory_store" / "training_exports"))
    parser.add_argument("--accuracy-threshold", type=float, default=0.75)
    args = parser.parse_args()

    export_dir = Path(args.export_dir)
    golden = export_dir / "golden.jsonl"
    checkpoint_id = args.checkpoint_id.strip() or f"ckpt_eval_{int(time.time())}"
    tp = TrainingPipeline()
    metrics = tp.evaluate_checkpoint(
        checkpoint_id=checkpoint_id,
        golden_path=str(golden),
        accuracy_threshold=float(args.accuracy_threshold),
    )
    out = Path(__file__).resolve().parent.parent / "docs" / "reports" / "evaluation_latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(f"wrote={out}")
    print(json.dumps(metrics, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
