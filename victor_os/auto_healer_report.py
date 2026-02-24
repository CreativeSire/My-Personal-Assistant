from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
TASKS_DB = BASE_DIR / "memory_store" / "victor_tasks.db"
OUT = Path(__file__).resolve().parent.parent / "docs" / "reports" / "auto_healer_report.md"


def main() -> int:
    if not TASKS_DB.exists():
        raise SystemExit(f"Missing: {TASKS_DB}")

    conn = sqlite3.connect(str(TASKS_DB))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, task_type, status, retries, error, updated_at
            FROM tasks
            WHERE status='failed'
            ORDER BY updated_at DESC
            LIMIT 50
            """
        ).fetchall()
    finally:
        conn.close()

    errors = [str(r["error"] or "") for r in rows]
    suggestions = []
    if any("timeout" in e.lower() for e in errors):
        suggestions.append("Increase Telegram upload timeout: TELEGRAM_UPLOAD_TIMEOUT_SEC=240")
    if any("file is too big" in e.lower() for e in errors):
        suggestions.append("Reduce batch size or set TELEGRAM_MAX_DOC_MB lower and use courier fallback paths")
    if any("singleton lock" in e.lower() for e in errors):
        suggestions.append("Use ops_supervisor.py and consider TELEGRAM_SINGLETON_DISABLE=1 for supervised boots")
    suggestions.append("Tune retry backoff: TASK_RETRY_BASE_DELAY_SEC=5 TASK_RETRY_MAX_DELAY_SEC=120")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("# Auto-Healer Report\n\n")
        f.write(f"- Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"- Recent failed tasks scanned: {len(rows)}\n\n")
        f.write("## Top Suggestions\n")
        for s in suggestions[:10]:
            f.write(f"- {s}\n")
        f.write("\n## Recent Failures\n")
        for r in rows[:15]:
            f.write(f"- {r['id']} | {r['task_type']} | retries={r['retries']} | {str(r['error'] or '')[:120]}\n")

    print(f"wrote={OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

