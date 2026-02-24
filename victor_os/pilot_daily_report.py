from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "docs" / "reports"
TASKS_DB = ROOT / "victor_os" / "memory_store" / "victor_tasks.db"
PLATFORM_DB = ROOT / "victor_os" / "memory_store" / "victor_platform.db"


def _q(db: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def main() -> int:
    days = int(os.getenv("PILOT_WINDOW_DAYS", "1"))
    since = time.time() - (days * 86400)

    task_rows = _q(
        TASKS_DB,
        """
        SELECT status, COUNT(*) c
        FROM tasks
        WHERE created_at >= ?
        GROUP BY status
        """,
        (since,),
    )
    by_status = {str(r["status"]): int(r["c"]) for r in task_rows}
    failed = int(by_status.get("failed", 0))
    completed = int(by_status.get("completed", 0))

    retry_rows = _q(
        TASKS_DB,
        """
        SELECT id, task_type, retries, status, error
        FROM tasks
        WHERE created_at >= ? AND retries > 0
        ORDER BY retries DESC, created_at DESC
        LIMIT 25
        """,
        (since,),
    )
    blocked_rows = _q(
        PLATFORM_DB,
        """
        SELECT ts_utc, action, policy_tier, reason, metadata_json
        FROM policy_decisions
        WHERE ts_utc >= ? AND allowed = 0
        ORDER BY ts_utc DESC
        LIMIT 50
        """,
        (since,),
    )
    wrong_output_rows = _q(
        PLATFORM_DB,
        """
        SELECT ts_utc, reason_code, task_id, rejected_output, preferred_output
        FROM corrections
        WHERE ts_utc >= ?
        ORDER BY ts_utc DESC
        LIMIT 50
        """,
        (since,),
    )

    report = {
        "generated_at": time.time(),
        "window_days": days,
        "task_status_counts": by_status,
        "completed": completed,
        "failed": failed,
        "retry_items": [dict(r) for r in retry_rows],
        "policy_blocked": [dict(r) for r in blocked_rows],
        "wrong_outputs": [
            {
                "ts_utc": r["ts_utc"],
                "reason_code": r["reason_code"],
                "task_id": r["task_id"],
                "rejected_len": len(str(r["rejected_output"] or "")),
                "preferred_len": len(str(r["preferred_output"] or "")),
            }
            for r in wrong_output_rows
        ],
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d")
    json_path = REPORT_DIR / f"pilot_daily_{stamp}.json"
    md_path = REPORT_DIR / f"pilot_daily_{stamp}.md"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Pilot Daily Report\n\n")
        f.write(f"- Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"- Window: {days} day(s)\n")
        f.write(f"- Completed: {completed}\n")
        f.write(f"- Failed: {failed}\n")
        f.write(f"- Blocked actions: {len(blocked_rows)}\n")
        f.write(f"- Wrong output corrections: {len(wrong_output_rows)}\n")
        f.write(f"- Retry tasks: {len(retry_rows)}\n")

    print(f"wrote={json_path}")
    print(f"wrote={md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
