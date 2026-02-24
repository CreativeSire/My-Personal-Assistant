# Phase 1 Acceptance

This file is the closure checklist for Phase 1 (`Data Engine + Boundary Stabilization`).

## Gates

1. Event envelope completeness >= 95%
- Status: `PASS`
- Evidence artifact: `docs/reports/event_envelope_report.md`

2. Duplicate outbound notifications suppressed on replay
- Status: `PASS`
- Evidence: `victor_os/test_delivery_dedupe_replay.py`

3. Queue stale-running recovery works
- Status: `PASS`
- Evidence: `victor_os/test_task_queue_phase1.py::test_recover_stale_running_tasks`

4. Submission idempotency key returns existing task
- Status: `PASS`
- Evidence: `victor_os/test_task_queue_phase1.py::test_enqueue_idempotency_reuses_existing_task`

5. Control plane API + centralized task creation path active
- Status: `PASS`
- Evidence: `agent_framework.py` `/v1/tasks`; `victor_os/invoice_pipeline.py` API-first enqueue

6. Dala external contract smoke test (from separate repo/folder)
- Status: `PASS`
- Evidence: `docs/reports/dala_smoke_test.json` (executed from `%TEMP%`)

## Latest Verification Snapshot

- Queue + replay tests: `3 passed` (`victor_os/test_task_queue_phase1.py`, `victor_os/test_delivery_dedupe_replay.py`)
- Crash-recovery drill: `PASS` (`docs/reports/crash_recovery_drill.json`)
- External Dala smoke test: `PASS` (`docs/reports/dala_smoke_test.json`)
- Event envelope report: `meets_target=True` with `sample_size=310` in `docs/reports/event_envelope_report.md`

## Run Commands

```powershell
# 1) Queue + delivery replay tests
python -m pytest -q victor_os/test_task_queue_phase1.py victor_os/test_delivery_dedupe_replay.py

# 2) Generate event-envelope completeness report
python phase1_event_envelope_report.py --limit 500 --out-dir docs/reports

# 3) (External to this repo) Dala smoke test
# Call:
#   POST http://127.0.0.1:8787/v1/tasks
#   GET  http://127.0.0.1:8787/v1/tasks/{task_id}
```

## Exit Criteria

Phase 1 is complete when all gate statuses are `PASS`, including:
- Gate 1 ratio >= `0.95`
- Gate 6 external smoke test pass result recorded.
