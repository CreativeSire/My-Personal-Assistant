# Data Engine And Feature Store

Core tables:
- `events`
- `corrections`
- `tool_calls`
- `policy_decisions`
- `task_runs`
- `delivery_ledger`
- `feature_store`

Derived features (minimum):
- `events.total`
- `corrections.total`
- `tool_calls.total`
- `policy.blocked.total`
- `quality.correction_ratio`

Feature refresh:
- On feedback write.
- Scheduled periodic recompute.
