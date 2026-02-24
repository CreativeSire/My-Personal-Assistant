from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PLATFORM_DB = ROOT / "victor_os" / "memory_store" / "victor_platform.db"
TASKS_DB = ROOT / "victor_os" / "memory_store" / "victor_tasks.db"
REPORT = ROOT / "docs" / "reports" / "ml_monitoring_targets.json"


def _single(db: Path, sql: str, params: tuple = ()) -> float:
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(sql, params).fetchone()
        return float(row[0] or 0.0) if row else 0.0
    finally:
        conn.close()


def main() -> int:
    since = time.time() - 86400
    correction_ratio = _single(
        PLATFORM_DB,
        """
        SELECT CASE WHEN e.cnt=0 THEN 0.0 ELSE CAST(c.cnt AS REAL)/CAST(e.cnt AS REAL) END
        FROM (SELECT COUNT(*) cnt FROM events WHERE ts_utc >= ?) e,
             (SELECT COUNT(*) cnt FROM corrections WHERE ts_utc >= ?) c
        """,
        (since, since),
    )
    policy_violations = _single(
        PLATFORM_DB,
        "SELECT COUNT(*) FROM policy_decisions WHERE ts_utc >= ? AND allowed=0",
        (since,),
    )
    retries = _single(
        TASKS_DB,
        "SELECT SUM(retries) FROM tasks WHERE updated_at >= ?",
        (since,),
    )
    training_metrics_path = ROOT / "docs" / "reports" / "training_job_latest.json"
    training_metrics = {}
    if training_metrics_path.exists():
        training_metrics = json.loads(training_metrics_path.read_text(encoding="utf-8"))
    data = {
        "generated_at": time.time(),
        "window_hours": 24,
        "accuracy": float(training_metrics.get("accuracy", 0.0)),
        "retries": int(retries),
        "correction_ratio": float(correction_ratio),
        "policy_violations": int(policy_violations),
        "targets": {
            "accuracy_min": float(os.getenv("TARGET_ACCURACY_MIN", "0.75")),
            "retries_max": int(os.getenv("TARGET_RETRIES_MAX", "20")),
            "correction_ratio_max": float(os.getenv("TARGET_CORRECTION_RATIO_MAX", "0.20")),
            "policy_violations_max": int(os.getenv("TARGET_POLICY_VIOLATIONS_MAX", "10")),
        },
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"wrote={REPORT}")
    print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
