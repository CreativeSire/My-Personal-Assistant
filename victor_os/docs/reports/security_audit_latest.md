# Security Audit Report

- Timestamp (utc epoch): `1771598205.3091962`
- Mode: `normal`
- Total checks: `10`
- Failed: `0`
- Critical failed: `0`

## Findings

| Check ID | Severity | Status | Message | Fix |
|---|---|---|---|---|
| `api.keys.present` | `critical` | `pass` | 2 API key(s) configured | - |
| `app_env.not_dev` | `high` | `pass` | APP_ENV=prod | - |
| `api.ip_allowlist.configured` | `high` | `pass` | IP allowlist is configured | - |
| `rate_limit.base` | `medium` | `pass` | Base rate limit is bounded | - |
| `rate_limit.training_write` | `medium` | `pass` | Training write rate limit is bounded | - |
| `kill_switch.path.present` | `low` | `pass` | Kill switch route implemented (/v1/control/kill_switch) | - |
| `hard_deny.zones.config` | `high` | `pass` | Hard deny policy is enabled | - |
| `auth.audit.endpoint` | `low` | `pass` | Auth block audit endpoint present (/v1/audit/auth_blocks) | - |
| `api.scope.enforcement` | `high` | `pass` | Scope enforcement middleware enabled | - |
| `telegram.token.present` | `medium` | `pass` | Telegram token configured | - |
