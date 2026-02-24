# Victor x OpenClaw Mirror Blueprint

## Document Purpose

This document is a **detailed execution blueprint** for mirroring the strongest architectural ideas from OpenClaw into Victor, while preserving Victor's current Python-first implementation and roadmap.

This is not a generic comparison. It is an implementation-grade mirror plan mapped directly to the current Victor codebase.

---

## Snapshot Used For This Mirror Plan

### OpenClaw Snapshot (reviewed from local archive)
- Archive: `openclaw-main.zip`
- Focus areas reviewed:
  - Gateway architecture, protocol, HTTP APIs, security docs
  - Channel plugin system and extension manifests
  - Cron/service hardening
  - Device/node pairing model
  - CLI/ops surface (`doctor`, diagnostics, status)
  - Model routing/failover/auth profile patterns

### Victor Snapshot (current baseline)
- Core API:
  - `agent_framework.py`
  - `victor_os/agent_orchestrator.py`
  - `victor_os/tool_registry_v2.py`
  - `victor_os/local_inference_router.py`
  - `victor_os/training_pipeline.py`
- Runtime/channel:
  - `victor_os/telegram_server.py`
  - `victor_os/desktop_server.py`
  - `victor_os/whatsapp_server.py`
  - `victor_os/task_queue.py`
  - `victor_os/workflow_engine.py`
- Intelligence/data:
  - `data_engine.py`
  - `victor_os/memory_core.py`
  - `victor_os/passive_intelligence.py`
  - `victor_os/user_registry.py`
  - `victor_os/skills/*`
- Ops:
  - `victor_os/ops_health_monitor.py`
  - `victor_os/ops_backup_restore.py`
  - `victor_os/ops_rotate_api_keys.py`
  - `victor_os/docs/BUILD_LEDGER.md`

---

## Executive Summary: What To Mirror First

OpenClaw's biggest advantage is not one model or one feature. It is **system discipline**:
1. Strict plugin contracts
2. Strong default security posture
3. Protocolized control plane
4. Operational diagnostics
5. Deterministic safeguards around automation

Victor should mirror those in this order:
1. Plugin/skill manifests + schema validation
2. Security audit engine + enforced baseline
3. Unified channel adapter contract
4. OpenResponses-style HTTP compatibility
5. Device pairing and trust lifecycle
6. Cron/workflow hardening and idempotent delivery
7. Runtime/SDK boundary for extensions
8. `doctor`-style operational command surface

---

## Mirror Principles (Non-Negotiables)

1. Mirror patterns, not code dumps.
2. Keep Victor Python-native.
3. Favor explicit contracts over implicit coupling.
4. Default deny for high-risk actions.
5. Every reliability feature must emit measurable evidence.
6. Every new module ships with acceptance checks.

---

## Current Gap Analysis (Victor vs OpenClaw)

### 1) Plugin/Extension Governance
- OpenClaw:
  - Every extension has `openclaw.plugin.json` with strict `configSchema`.
  - Unknown or broken plugin configs fail fast.
- Victor:
  - Skills exist, but contract strictness is uneven.
  - Skill config schemas are not uniformly enforced.

**Gap severity:** High

### 2) Security Audit Surface
- OpenClaw:
  - Dedicated audit command and threat model checks.
  - Checks auth exposure, tool blast radius, risky defaults.
- Victor:
  - Has policy systems and logs, but lacks one authoritative audit command/report.

**Gap severity:** High

### 3) Channel Architecture
- OpenClaw:
  - Standardized channel plugin adapters.
  - Shared normalize/send/monitor patterns.
- Victor:
  - Telegram/Desktop/WhatsApp implementations are functional but more server-specific.

**Gap severity:** Medium-high

### 4) Protocol Interoperability
- OpenClaw:
  - OpenResponses-compatible endpoint.
- Victor:
  - Strong `/v1/*` APIs, but limited compatibility facade.

**Gap severity:** Medium

### 5) Device Trust Lifecycle
- OpenClaw:
  - Pairing approval and device identity controls.
- Victor:
  - User and API auth exist; device-centric trust lifecycle is partial.

**Gap severity:** Medium-high

### 6) Ops/Doctor Experience
- OpenClaw:
  - Broad CLI diagnostics and recoverability UX.
- Victor:
  - Good scripts exist, but fragmented.

**Gap severity:** Medium

---

## Mirror Workstreams

## Workstream A: Skill Manifest and Schema Enforcement

### Objective
Create a strict, machine-validated contract for every Victor skill.

### Deliverables
1. `victor_os/skills/manifest.schema.json` (new)
2. `victor_os/skills/*/skill.manifest.json` optional external manifest or inline manifest export
3. Registry-level validation in `victor_os/skill_registry.py`
4. Config validation hooks in `victor_os/config.py`
5. CI/local validator script:
   - `victor_os/scripts/validate_skill_manifests.py` (new)

### Contract Fields (minimum)
- `id`
- `name`
- `version`
- `description`
- `triggers`
- `required_env`
- `config_schema`
- `risk_profile`
- `permissions_required`
- `tools_exposed`

### Acceptance Criteria
1. Skill with invalid schema does not load.
2. Unknown config key is rejected with explicit error.
3. `skill_registry` status report includes validation state per skill.

---

## Workstream B: Victor Security Audit Engine

### Objective
Implement an OpenClaw-like security audit command and report generation path.

### Deliverables
1. `victor_os/security_audit.py` (new)
2. API endpoint:
   - `GET /v1/security/audit`
3. Optional CLI wrapper:
   - `python security_audit.py --deep --json`
4. Report output:
   - `victor_os/docs/reports/security_audit_latest.md`

### Checks to Implement
1. Gateway/API bind exposure
2. Missing/weak auth key policies
3. High-risk tool auto-execution paths
4. Hard deny zones verification
5. Telegram/WhatsApp policy consistency
6. Local filesystem permission hygiene for sensitive files
7. Audit log rotation/retention sanity
8. Rate-limit and brute-force protections
9. Disabled kill switch detection
10. Policy drift detection

### Severity Model
- `critical`
- `high`
- `medium`
- `low`
- `info`

### Acceptance Criteria
1. Audit completes in under 5 seconds (normal mode).
2. Findings are deterministic.
3. At least 10 core checks available with machine-readable JSON output.

---

## Workstream C: Unified Channel Adapter Contract

### Objective
Decouple channel specifics from orchestration with a shared adapter interface.

### Deliverables
1. New base contract:
   - `victor_os/channel_adapter.py`
2. Adapter implementations:
   - `victor_os/adapters/telegram_adapter.py`
   - `victor_os/adapters/whatsapp_adapter.py`
   - `victor_os/adapters/desktop_adapter.py`
3. Shared normalization utilities:
   - `victor_os/channel_normalize.py`
4. Shared outbound envelope:
   - message text
   - artifacts
   - delivery metadata
   - idempotency key

### Interface Methods (proposed)
- `normalize_inbound(raw_event) -> ChannelMessage`
- `authorize(sender) -> AuthDecision`
- `route(message) -> RoutingDecision`
- `deliver(outbound) -> DeliveryResult`
- `heartbeat() -> ChannelHealth`

### Acceptance Criteria
1. Telegram/WhatsApp/Desktop share same lifecycle events.
2. Delivery idempotency behavior is consistent across all channels.
3. Channel-level retries follow shared policy.

---

## Workstream D: OpenResponses Compatibility Layer

### Objective
Expose a compatibility endpoint to ease client/tool integration.

### Deliverables
1. Endpoint:
   - `POST /v1/responses` (new facade)
2. Mapper:
   - OpenResponses payload -> Victor task intent
3. Streaming compatibility (phase 2 of this workstream):
   - SSE/events optional

### Files to Touch
- `agent_framework.py` (routing and auth)
- `victor_os/agent_orchestrator.py` (request mapping)
- `victor_os/training_pipeline.py` (optional metadata enrichment)

### Acceptance Criteria
1. Basic text prompt works via `/v1/responses`.
2. Tool call request can round-trip through Victor tool execution path.
3. Errors use consistent response schema.

---

## Workstream E: Device Pairing and Trust Lifecycle

### Objective
Introduce explicit device identity controls for remote/control surfaces.

### Deliverables
1. `victor_os/device_registry.py` (new)
2. Pairing tokens and approval workflow
3. Device trust policies:
   - `pending`, `approved`, `revoked`
4. API:
   - `POST /v1/devices/pair/request`
   - `POST /v1/devices/pair/approve`
   - `POST /v1/devices/revoke`
   - `GET /v1/devices`

### Integration Targets
- `victor_os/desktop_server.py`
- `victor_os/telegram_server.py` (device-issued control commands if applicable)
- `agent_framework.py`

### Acceptance Criteria
1. Unknown device cannot execute privileged actions.
2. Revoked device loses access immediately.
3. Device approval events are audited.

---

## Workstream F: Policy Scope + Rate Limit Hardening

### Objective
Mirror strict method scope controls for all critical APIs.

### Deliverables
1. Per-route required scope table:
   - `victor_os/security_scopes.py` (new)
2. Role + scope + policy merge decision function
3. Central middleware usage across `/v1/*`
4. Rate-limit profile tiers:
   - unauthenticated
   - authenticated
   - admin/tool execution

### Acceptance Criteria
1. Unauthorized scope calls return deterministic `401/403`.
2. Rate-limit violations return deterministic `429` with retry metadata.
3. Audit trail stores scope decision reason.

---

## Workstream G: Workflow/Cron Reliability Hardening

### Objective
Bring Victor’s workflow engine to strict service-grade behavior.

### Deliverables
1. Workflow locks and dedupe tokens per run.
2. Restart catch-up behavior rules.
3. Backoff/retry matrix by action idempotency.
4. Run log and replay guard:
   - no duplicate side effects on replay.

### Files to Touch
- `victor_os/workflow_engine.py`
- `victor_os/task_queue.py`
- `victor_os/data_engine.py` (or `data_engine.py`, depending integration point)

### Acceptance Criteria
1. No duplicate outbound deliveries during recovery drills.
2. Restart mid-run resumes safely.
3. Run logs can certify deterministic outcomes.

---

## Workstream H: Extension Runtime vs SDK Boundary

### Objective
Create a stable extension API so skills/extensions do not rely on private internals.

### Deliverables
1. `victor_os/sdk/` package (new)
2. `victor_os/runtime_api.py` facade (new)
3. Deprecation warnings for direct private imports
4. Compatibility contract documentation

### SDK Surface (initial)
- Event emit
- Tool invoke wrapper
- Memory read/write wrapper
- Session context accessor
- Policy query helper

### Acceptance Criteria
1. New skills can be built with SDK only.
2. Internal refactors do not break skill interface.

---

## Workstream I: Security Default Baseline Profile

### Objective
Ship a hardened default profile users can opt into immediately.

### Deliverables
1. `victor_os/security_profiles/hardened_default.json` (new)
2. One-command apply:
   - `python security_audit.py --apply-baseline hardened_default`
3. Baseline includes:
   - strict deny zones
   - require approval for risky tools
   - constrained filesystem roots
   - safe proactive defaults

### Acceptance Criteria
1. Baseline application is reversible.
2. Post-apply audit score improves measurably.

---

## Workstream J: Doctor/Status/Health Unified Operations

### Objective
Provide one clear operational entry point for support, demos, and production checks.

### Deliverables
1. `victor_os/doctor.py` (new)
2. Standard command set:
   - `doctor status`
   - `doctor health`
   - `doctor channels`
   - `doctor security`
   - `doctor workflows`
3. Report outputs in:
   - `victor_os/docs/reports/`

### Acceptance Criteria
1. Single command surfaces top 20 critical checks.
2. Support workflow can identify failures in under 2 minutes.

---

## 7 / 30 / 90-Day Execution Matrix

## Day 0-7 (Foundation)

### Must Ship
1. Workstream A (manifest/schema validation)
2. Workstream B (security audit v1)
3. Workstream F (scopes/rate limits unified)
4. Workstream J (doctor skeleton)

### Exit Evidence
- `docs/reports/mirror_day7_foundation.md`

### KPI Targets
- 100% skills manifest-validated
- 0 unauthenticated access to privileged `/v1/*`
- Security audit available via API + CLI

---

## Day 8-30 (Reliability + Interop)

### Must Ship
1. Workstream C (channel adapter layer)
2. Workstream D (`/v1/responses` compatibility)
3. Workstream G (workflow hardening)
4. Workstream I (hardened profile)

### Exit Evidence
- `docs/reports/mirror_day30_reliability.md`

### KPI Targets
- 0 duplicate outbound side effects during replay drills
- Channel parity on lifecycle actions
- Baseline security profile enabled and verified

---

## Day 31-90 (Scale + Productization)

### Must Ship
1. Workstream E (device pairing lifecycle)
2. Workstream H (SDK/runtime boundary)
3. Doctor command full suite + dashboard linkage
4. Expanded audit checks + policy drift monitor

### Exit Evidence
- `docs/reports/mirror_day90_scale.md`

### KPI Targets
- Device trust model enforced
- SDK-only skill dev path documented and usable
- Mean time to diagnose operational issues < 5 minutes

---

## Detailed Mapping: OpenClaw Capability -> Victor Implementation Target

1. Plugin manifest strictness
- OpenClaw reference:
  - `docs/plugins/manifest.md`
  - `extensions/*/openclaw.plugin.json`
- Victor target:
  - `victor_os/skill_registry.py`
  - `victor_os/config.py`
  - new `victor_os/skills/manifest.schema.json`

2. Gateway security audit
- OpenClaw reference:
  - `docs/gateway/security/index.md`
  - `src/gateway/auth-rate-limit.ts`
  - `src/gateway/control-plane-audit.ts`
- Victor target:
  - new `victor_os/security_audit.py`
  - `agent_framework.py` API route integration

3. Channel plugin architecture
- OpenClaw reference:
  - `src/channels/plugins/*`
- Victor target:
  - new `victor_os/channel_adapter.py`
  - `victor_os/telegram_server.py`
  - `victor_os/whatsapp_server.py`
  - `victor_os/desktop_server.py`

4. OpenResponses endpoint
- OpenClaw reference:
  - `docs/gateway/openresponses-http-api.md`
  - `src/gateway/openresponses-http.ts`
- Victor target:
  - `agent_framework.py`
  - `victor_os/agent_orchestrator.py`

5. Device pairing
- OpenClaw reference:
  - `extensions/device-pair/openclaw.plugin.json`
  - device/auth docs in gateway architecture
- Victor target:
  - new `victor_os/device_registry.py`
  - `agent_framework.py`

6. Cron reliability
- OpenClaw reference:
  - `src/cron/service.ts`
  - restart catch-up and dedupe tests
- Victor target:
  - `victor_os/workflow_engine.py`
  - `victor_os/task_queue.py`

7. SDK/runtime split
- OpenClaw reference:
  - `docs/refactor/plugin-sdk.md`
- Victor target:
  - new `victor_os/sdk/*`
  - `victor_os/runtime_api.py`

---

## Risks and Mitigations

1. Risk: Over-engineering too early.
- Mitigation: enforce 7/30/90 gates and measurable KPIs only.

2. Risk: Security regressions during adapter refactor.
- Mitigation: route all executions through central policy middleware first.

3. Risk: Endpoint sprawl.
- Mitigation: keep compatibility layer thin; orchestrator remains canonical.

4. Risk: Skill ecosystem breakage during contract tightening.
- Mitigation: add warning mode before hard enforcement.

---

## Out of Scope (For This Mirror Cycle)

1. Full mobile app parity with OpenClaw (Android/iOS/macOS node architecture).
2. Rebuilding Victor in Node/TS.
3. Adopting all channels at once.
4. Replacing existing planner/executor/reviewer architecture.

---

## Reporting and Governance

## Required Reports
1. `docs/reports/mirror_day7_foundation.md`
2. `docs/reports/mirror_day30_reliability.md`
3. `docs/reports/mirror_day90_scale.md`
4. `docs/reports/security_audit_latest.md`
5. `docs/reports/channel_parity_matrix.md`
6. `docs/reports/workflow_recovery_drill.md`

## Weekly Steering Review Template
1. What was mirrored this week?
2. What broke?
3. What was blocked by policy/security?
4. Which KPI moved?
5. What goes to next week?

---

## Final Recommendation

Do not try to copy OpenClaw feature-for-feature. Mirror the **discipline layer** first:
- schema contracts,
- security audits,
- adapter boundaries,
- operational diagnostics,
- deterministic reliability.

That is the shortest path to making Victor pitch-ready, stable, and safer at higher autonomy.

