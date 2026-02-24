# Phase H Report: Runtime vs SDK Boundary

Date: 2026-02-20

## Implemented
- Added stable runtime facade:
  - `victor_os/runtime_api.py`
- Added SDK package:
  - `victor_os/sdk/__init__.py`
  - `victor_os/sdk/api.py`
- Added root compatibility shim:
  - `sdk/__init__.py`
- Added extension docs:
  - `victor_os/docs/SDK_EXTENSION_GUIDE.md`

## Stable extension APIs exposed
- Config access
- Event emit
- Memory read/write
- Tool invoke wrapper
- Session context accessor
- System metrics helper

## Deprecation warnings
- `victor_os/skill_registry.py` now warns when skill modules import private runtime modules directly:
  - `config`
  - `memory_core`
  - `data_engine`
  - `session_manager`

## Skills migrated to SDK path
- `victor_os/skills/market_watch.py`
- `victor_os/skills/memory_hygiene.py`
- `victor_os/skills/ops_health_check.py`

## Acceptance status
- SDK boundary exists and usable: PASS
- Deprecation warning path added: PASS
- At least 3 skills migrated to SDK-only extension imports: PASS
