# System Context And Boundaries

## Components
- `victor_core`: contracts, policy kernel, telemetry API.
- `data_engine.py`: canonical telemetry/training store.
- `victor_os/*`: execution runtime (Telegram, Desktop, queue, tools).
- `agent_framework.py`: `/v1/*` control API.
- `dala` (external): business app consuming Victor API/events.

## Boundary Rules
- Dala must not import Victor internals directly.
- All task lifecycle operations pass through API/events.
- Invoice pipeline remains adapter functionality, not platform identity.

## Ownership
- Victor owns orchestration, policy, and telemetry.
- Client apps own domain UX/business logic.
