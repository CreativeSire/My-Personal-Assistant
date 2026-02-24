# Mirror Execution Checklist

Source plan: `victor_os/docs/Mriror.MD`

## Usage
- Mark `[x]` when completed.
- Add evidence links under each completed section.
- Keep this file updated at the end of each implementation session.

---

## Phase 1: Day 0-7 (Foundation)

## Workstream A: Skill Manifest and Schema Enforcement
- [x] Create `victor_os/skills/manifest.schema.json`
- [x] Add manifest support to `victor_os/skill_registry.py`
- [x] Enforce schema validation during skill load
- [x] Reject unknown config keys with clear errors
- [x] Add validator script `victor_os/scripts/validate_skill_manifests.py`
- [x] Add acceptance test coverage for invalid manifest rejection
- [x] Add acceptance test coverage for unknown config key rejection

Evidence:
- [x] `docs/reports/phaseA_skill_manifest_validation.md`

## Workstream B: Security Audit Engine
- [x] Create `victor_os/security_audit.py`
- [x] Implement normal audit mode
- [x] Implement deep audit mode
- [x] Add JSON output mode
- [x] Add API route `GET /v1/security/audit`
- [x] Persist latest audit report to `docs/reports/security_audit_latest.md`
- [x] Add at least 10 deterministic checks

Evidence:
- [x] `docs/reports/phaseB_security_audit.md`

## Workstream F: Scope + Rate Limit Hardening
- [x] Create `victor_os/security_scopes.py`
- [x] Define per-route scope map for `/v1/*`
- [x] Centralize scope enforcement middleware
- [x] Enforce uniform `401/403` behavior
- [x] Enforce uniform `429` behavior with retry metadata
- [x] Persist scope decision audit trail

Evidence:
- [x] `docs/reports/phaseF_scope_rate_limit.md`

## Workstream J (Skeleton): Doctor Command
- [x] Create `victor_os/doctor.py`
- [x] Add `doctor status`
- [x] Add `doctor health`
- [x] Add `doctor security`
- [x] Add output summary format (human-readable)

Evidence:
- [x] `docs/reports/phaseJ_doctor_foundation.md`

## Day 7 Exit Gate
- [x] 100% skills manifest-validated
- [x] 0 unauthenticated access to privileged `/v1/*`
- [x] Security audit available via API and CLI
- [x] Publish `docs/reports/mirror_day7_foundation.md`

---

## Phase 2: Day 8-30 (Reliability + Interop)

## Workstream C: Unified Channel Adapter Contract
- [x] Create `victor_os/channel_adapter.py`
- [x] Create `victor_os/adapters/telegram_adapter.py`
- [x] Create `victor_os/adapters/whatsapp_adapter.py`
- [x] Create `victor_os/adapters/desktop_adapter.py`
- [x] Create shared normalization in `victor_os/channel_normalize.py`
- [x] Migrate Telegram server to adapter contract
- [x] Migrate WhatsApp server to adapter contract
- [x] Migrate Desktop server to adapter contract
- [x] Align lifecycle events and delivery envelope across channels

Evidence:
- [x] `docs/reports/phaseC_channel_adapter.md`

## Workstream D: OpenResponses Compatibility Layer
- [x] Add route `POST /v1/responses`
- [x] Implement payload mapper to Victor task intent
- [x] Add compatibility error schema
- [x] Add tool call round-trip support
- [x] Add basic streaming plan/placeholder

Evidence:
- [x] `docs/reports/phaseD_openresponses_compat.md`

## Workstream G: Workflow/Cron Reliability Hardening
- [x] Add run lock per workflow execution
- [x] Add dedupe token per run
- [x] Add restart catch-up logic
- [x] Add replay-safe side-effect protection
- [x] Add backoff matrix by idempotency class
- [x] Validate duplicate outbound prevention in drills

Evidence:
- [x] `docs/reports/phaseG_workflow_hardening.md`
- [x] `docs/reports/workflow_recovery_drill.md`

## Workstream I: Hardened Default Security Profile
- [x] Create `victor_os/security_profiles/hardened_default.json`
- [x] Add apply command in audit flow
- [x] Add rollback support
- [x] Verify profile through post-apply audit run

Evidence:
- [x] `docs/reports/phaseI_hardened_profile.md`

## Day 30 Exit Gate
- [x] 0 duplicate outbound side effects in replay drills
- [x] Channel parity achieved for lifecycle actions
- [x] Hardened profile available and validated
- [x] Publish `docs/reports/mirror_day30_reliability.md`

---

## Phase 3: Day 31-90 (Scale + Productization)

## Workstream E: Device Pairing and Trust Lifecycle
- [x] Create `victor_os/device_registry.py`
- [x] Add `POST /v1/devices/pair/request`
- [x] Add `POST /v1/devices/pair/approve`
- [x] Add `POST /v1/devices/revoke`
- [x] Add `GET /v1/devices`
- [x] Enforce trust checks for privileged operations
- [x] Audit device pairing and revocation events

Evidence:
- [x] `docs/reports/phaseE_device_pairing.md`

## Workstream H: Runtime vs SDK Boundary
- [x] Create `victor_os/sdk/` package
- [x] Create `victor_os/runtime_api.py`
- [x] Expose stable extension APIs (memory, tools, events, session context)
- [x] Add deprecation warnings for private direct imports
- [x] Migrate at least 3 existing skills to SDK-only path

Evidence:
- [x] `docs/reports/phaseH_sdk_runtime_boundary.md`

## Workstream J (Full): Doctor + Ops Surface
- [x] Expand `doctor` to include `channels`, `workflows`, `logs`, `diagnostics`
- [x] Add machine-readable output mode
- [x] Integrate security findings summary
- [x] Integrate queue/workflow health checks

Evidence:
- [x] `docs/reports/phaseJ_doctor_full.md`

## Day 90 Exit Gate
- [x] Device trust lifecycle enforced
- [x] SDK-only extension path documented and usable
- [x] Mean-time-to-diagnose operational failures < 5 min (internal drill)
- [x] Publish `docs/reports/mirror_day90_scale.md`

---

## Cross-Cutting Validation

## Security
- [ ] High/critical findings trend downward week-over-week
- [ ] Hard deny zones remain enforced during all new integrations
- [ ] Auth/rate-limit audit logs reviewed daily

## Reliability
- [ ] Crash recovery drill passes
- [ ] Idempotency drill passes
- [ ] Queue stale-running recovery drill passes

## Data/Telemetry
- [ ] New features emit structured events
- [ ] Event schema drift checks in place
- [ ] Evidence reports generated automatically where possible

---

## Weekly Review Log

## Week of: __________
- Completed:
  - [ ]
- Blocked:
  - [ ]
- Risks:
  - [ ]
- Decisions:
  - [ ]
- Evidence updated:
  - [ ]
