# Dala <-> Victor Integration Contract

Dala is an external client of Victor platform.

## Allowed integration points
- `POST /v1/tasks` for invoice/document jobs.
- Task status polling via `GET /v1/tasks/{task_id}`.
- Completion hooks/events (phase extension).
- `POST /v1/feedback` for correction loops.

## Disallowed integration points
- Importing Victor private modules.
- Direct DB writes to Victor stores.
- Filesystem coupling into Victor internals.

## Migration note
- Existing direct coupling must be replaced with API/event calls only.
