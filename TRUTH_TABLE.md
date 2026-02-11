# TRUTH_TABLE.md

## Runtime Truth Table (Observed Now)

| Feature | Status | Evidence (file:line) | Hard Drill | Expected Outcome |
|---|---|---|---|---|
| Queue selects only pending | Wired | `task_queue.py:141-148` | Insert one `pending` + one `running`, start worker once | Only `pending` task executes |
| Task marked running before handler | Wired | `task_queue.py:217-224` | Add slow handler and inspect DB immediately after pickup | Row status becomes `running` before completion |
| Completion callback sends final message | Wired | `task_queue.py:234-235` | Register notify callback, run invoice task | Callback receives `"Task completed: ..."` |
| Invoice result includes file marker | Wired | `invoice_pipeline.py:1569-1573` | Call `run_invoice_job` with fixture input | Return string contains `<<SEND_FILE: ...>>` |
| Telegram notifier parses/send marker | Wired | `telegram_server.py:125-132`, `telegram_server.py:144-156` | Feed callback with marker and existing file | `send_document` path executes once |
| Within-job hash dedupe | Wired | `invoice_pipeline.py:1168-1176` | Process same file twice in one job context | 2nd item = `skipped_duplicate` |
| Same-name collision suffix | Wired | `invoice_pipeline.py:1011-1019` | Force filename collision with distinct content | Outputs include `_DUP2` |
| Job ID regenerated per run | Wired | `invoice_pipeline.py:1422` | Run twice with same input | Different `job_id`s |
| Summary finalization | Wired | `invoice_pipeline.py:1519-1520` | Complete run and inspect job dir | `summary.json` exists and populated |
| Zip finalization with top folders | Wired | `invoice_pipeline.py:1375-1394` | Open generated output zip | Contains `artifacts/OK`, `Review`, `Failed`, `evidence` stubs |
| Stale running auto-recovery | Broken | No stale lease recovery path in `task_queue.py` | Kill process mid-run, restart worker | Old row remains `running` and is not resumed |
| Queue submission idempotency key | Missing | `task_queue.py:108-131` (uuid only) | Submit same payload twice | Two different task IDs created |
| Delivery idempotency ledger | Missing | No delivery table/guard path | Trigger duplicate completion callback | Duplicate sends are possible |
| Proactive channel safe-off default | Broken by default | `config.py:54-56` defaults true | Boot with no env overrides | Telegram/email proactive can be active |
| Proactive severity gating | Wired | `proactive_engine.py:190-197`, `209-227` | Set `critical_only`, emit warning payload | Warning suppressed, critical allowed |

## Drill Notes
- Drills are mandatory operational checks, not “unit test might pass” checks.
- A drill fails if observed output differs from expected outcome, even if code compiles.
