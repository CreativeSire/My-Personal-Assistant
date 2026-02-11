# CANON.md

## CANON-A (Observed)

### Product Identity (Observed)
- Victor OS: Telegram-first multi-agent assistant with memory, tools, and queue-backed async jobs.
- Invoice Renamer: Headless document job pipeline for single file or zip batch, standardized to PDF, then routed.

### Queue Semantics (Observed)
- Current queue semantics are **at-least-once execution** for retried exceptions, with no stale-running auto-recovery on process death.
- Current queue fetches only `pending` tasks and marks selected tasks `running` before handler execution.
- Submission currently uses generated task IDs; no enforced queue-level idempotency key yet.
- Delivery currently depends on completion callback + marker parsing; no persisted delivery idempotency ledger yet.

### Invoice Routing (Observed)
- Outcomes: `ok`, `review`, `failed`, `skipped_duplicate`.
- `ok` routes to `artifacts/OK/CONFIRMED` or `artifacts/OK/WARNINGS`.
- `review` routes to `artifacts/Review`.
- `failed` routes to `artifacts/Failed`.
- Within-job dedupe exists via SHA256 (`hash_identical_duplicate`), and filename collisions use `_DUPn`.

### Proactive Notifications (Observed)
- Proactive engine is started when enabled and can fan out to Telegram/email channels.
- Current defaults in config are not safe-off unless environment overrides are applied.

## CANON-B (Target)

### Product Boundary Invariant
- Victor OS and Invoice Renamer share infrastructure only; they must not share product identity, policy ownership, or acceptance criteria.

### Queue Semantics Invariant
- **Queue semantics: at-least-once + idempotent submission + idempotent delivery.**
- At-least-once: job may re-run after failure/recovery, never silently dropped.
- Idempotent submission: same idempotency key returns existing active job.
- Idempotent delivery: same completion payload/artifact is sent once per destination.

### Recovery Invariant
- No zombie `running` tasks after crash.
- Startup recovery must requeue stale leased/running tasks to pending under retry budget.

### Proactive Invariant
- **Proactive defaults safe-off**: by default, proactive outbound channels are disabled until explicitly enabled.

### Artifact Invariant
- **Always-present artifact stubs** for every job, even when empty:
  - `artifacts/OK/CONFIRMED/`
  - `artifacts/OK/WARNINGS/`
  - `artifacts/Review/`
  - `artifacts/Failed/`
  - `review_items.json` (empty list allowed)
  - core summaries/state files required by contract

### Invoice Outcome Contract (Target)
- `OK/CONFIRMED`: all required rename fields valid, no warning flags.
- `OK/WARNINGS`: required rename fields valid, warning flags present.
- `Review`: required field invalid/missing after all passes.
- `Failed`: system/runtime error only.

### Rename-Critical Field Contract
- `receiver_name`
- `receiver_location` (short locality token)
- `invoice_number` (digits-only, exactly 6)
- `delivery_date` (YYYY-MM-DD)
- Output filename format: `RECEIVERNAME_LOCATION_INVOICENUMBER_DATE.pdf`
