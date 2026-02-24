# Phase G Report: Workflow/Cron Reliability Hardening

Date: 2026-02-20

## Implemented in `victor_os/workflow_engine.py`

1. Run lock per workflow
- Added per-workflow non-blocking run lock to prevent concurrent duplicate runs.

2. Dedupe token per run
- Added `dedupe_token` on `WorkflowRun`.
- Added active token tracking + persisted recent-token checks.
- Duplicate token now hard-fails with deterministic error.

3. Restart catch-up logic
- Added scheduler catch-up at startup:
  - computes missed daily/weekly slot
  - executes at most one catch-up run per workflow slot
  - uses catch-up dedupe token.

4. Replay-safe side-effect guard
- Added side-effect guard for notify/send-like actions.
- Uses `data_engine` delivery ledger (`has_delivery` / `record_delivery_attempt`) to skip replayed side effects.

5. Retry/backoff matrix by idempotency class
- Action registration now supports `idempotency_class`:
  - `idempotent`
  - `safe_side_effect`
  - `non_idempotent`
- Retry delay matrix applied per class.

## Validation evidence
- Unit tests:
  - `py -m pytest victor_os/test_workflow_examples.py victor_os/test_delivery_dedupe_replay.py victor_os/test_security_scopes.py test_api_auth_middleware.py -q`
  - Result: `7 passed`
- Token dedupe drill:
  - first run token `demo-token-1`: completed
  - second run same token: blocked with duplicate token error.

## Acceptance status
- Run lock added: PASS
- Dedupe token added: PASS
- Restart catch-up logic added: PASS
- Replay-safe side-effect protection added: PASS
- Backoff matrix by idempotency class added: PASS
