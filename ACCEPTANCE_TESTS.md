# ACCEPTANCE_TESTS.md

## Non-Negotiable Given/When/Then Tests

### A. Crash Recovery and Queue Semantics
1. Given a task row in `running` with stale lease metadata, when worker boots, then it is requeued to `pending` (or terminally failed by policy) and does not stay zombie-running.
2. Given process kill mid-run, when service restarts, then stale running tasks are recovered under retry budget and eventually reach terminal state.

### B. Submission Idempotency
3. Given the same normalized job payload + same idempotency key, when submit is called twice, then the second call returns the **existing `job_id`** (not a new one).
4. Given same payload with explicit rerun intent (`rerun_of_job_id`), when submit is called, then a new `job_id` is created and linked.

### C. Delivery Idempotency
5. Given the same completion event retried (same job/artifact/destination), when notifier runs multiple times, then only one outbound send occurs and subsequent sends are suppressed/logged.
6. Given first delivery attempt fails transiently, when retry succeeds, then the delivery record is finalized once without duplicate user-visible sends.

### D. Artifact Contract
7. Given a successful job with zero review items, when finalized, then artifact stubs still exist and `review_items.json` exists with an empty list.
8. Given a job with zero failures, when finalized, then `artifacts/Failed/` still exists as an empty stub.
9. Given any finalized job, when audited, then required job-level files exist (`job.json`, `state.json`, `run_summary.json`, `items.csv`, `run.log`, output zip if channel mode requires it).

### E. Invoice Routing and Normalization
10. Given invoice number text like `INV-000123`, when normalized, then stored/validated value is `000123`.
11. Given invoice number normalizes to non-6 digits, when validated, then item routes to Review with reason `invoice_number_not_6_digits`.
12. Given missing delivery date after all extraction passes, when routed, then item goes to Review with `missing_delivery_date`.
13. Given all required fields valid with warning flags, when routed, then item goes to `OK/WARNINGS` (not Review).
14. Given all required fields valid and no warnings, when routed, then item goes to `OK/CONFIRMED`.

### F. Negative Inputs
15. Given malformed zip, when processed, then job fails gracefully with explicit error reason and no worker crash.
16. Given unreadable/corrupt scan image, when processed, then item routes Review/Failed per policy with evidence files present.
17. Given conflicting date candidates, when best-date scoring resolves, then chosen date and scoring rationale are logged in extraction debug.
