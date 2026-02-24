# Victor SDK Extension Guide

This guide defines the stable extension boundary for skills.

## Use this import path

```python
from sdk import get_config, emit_event, write_memory, read_memory, invoke_tool, session_context, system_metrics
```

## Avoid private imports (deprecated)

Do not import these directly inside skills:
- `config`
- `memory_core`
- `data_engine`
- `session_manager`

The registry will emit deprecation warnings for direct private imports.

## Stable APIs

1. `get_config()`
- Returns Victor configuration object.

2. `session_context(channel, external_user_id)`
- Returns normalized user/session mapping.

3. `emit_event(...)`
- Writes structured telemetry/event records.

4. `write_memory(...)`
- Writes memory records through the runtime boundary.

5. `read_memory(...)`
- Reads memory via query abstraction.

6. `invoke_tool(...)`
- Runtime tool invocation wrapper.

7. `system_metrics()`
- Returns CPU/RAM/disk metrics.

## Migration status

SDK path adopted in these skills:
- `victor_os/skills/market_watch.py`
- `victor_os/skills/memory_hygiene.py`
- `victor_os/skills/ops_health_check.py`
