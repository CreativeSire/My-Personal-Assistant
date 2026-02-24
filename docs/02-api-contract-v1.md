# API Contract v1

## Endpoints
- `POST /v1/tasks`
- `GET /v1/tasks/{task_id}`
- `POST /v1/tasks/{task_id}/approve`
- `POST /v1/tasks/{task_id}/cancel`
- `GET /v1/capabilities`
- `GET /v1/policies/effective`
- `POST /v1/feedback`

## Task Create Request
```json
{
  "intent": "rename these invoices",
  "channel": "telegram",
  "idempotency_key": "user123:invoice_batch:2026-02-14",
  "risk_level_hint": "medium",
  "context_refs": ["workspace/jobs/abc"],
  "payload": {}
}
```

## Task Create Response
```json
{
  "ok": true,
  "task_id": "ab12cd34ef56gh78",
  "state": "pending",
  "estimated_actions": ["write"]
}
```
