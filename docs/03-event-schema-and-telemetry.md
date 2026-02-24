# Event Schema And Telemetry

Canonical event envelope:
- `event_id`
- `event_type`
- `ts_utc`
- `session_id`
- `task_id`
- `actor`
- `payload`
- `risk_score`
- `channel`
- `source`

Required event types:
- `intent.received`
- `plan.generated`
- `action.proposed`
- `action.executed`
- `action.blocked`
- `task.completed`
- `feedback.recorded`
- `training.example.emitted`

Storage:
- SQLite table: `events` (append-only).
