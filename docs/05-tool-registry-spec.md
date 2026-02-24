# Tool Registry Spec

Tool definition schema:
- `tool_name`
- `action`
- `input_schema`
- `risk_class`
- `idempotency_strategy`
- `compensation`

Minimum packs:
- Filesystem.
- Browser.
- App/process.
- Messaging.

Execution requirements:
- Policy check before action.
- Telemetry `action.proposed` and `action.executed|action.blocked`.
- Retry and side-effect logging.
