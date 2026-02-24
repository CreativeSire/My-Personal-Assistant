# Victor Operator Runbook (Post-Phase-3)

## Start Services

Run:

```bat
victor_os\bootstrap_phase23.bat
```

This starts:

- `agent_framework.py` (API)
- `telegram_server.py`
- `desktop_server.py`

Optional watchdog auto-restart:

```powershell
python victor_os\ops_supervisor.py --watch --interval 30
```

## Daily Operations

1. Run health check:

```powershell
$env:VICTOR_API_KEY="<your_key>"
python victor_os\ops_health_monitor.py
```

2. Review:

- `docs/reports/ops_health_report.json`
- `docs/reports/phase23_pilot_metrics.md`

## Backup / Restore

Backup:

```powershell
python victor_os\ops_backup_restore.py backup
```

List backups:

```powershell
python victor_os\ops_backup_restore.py list
```

Restore:

```powershell
python victor_os\ops_backup_restore.py restore --src "C:\path\to\backup_folder"
```

## Security Operations

1. Keep `APP_ENV=prod`.
2. Use `VICTOR_API_KEYS` (rotating key set) or `VICTOR_API_KEY`.
3. Ensure clients call `/v1/*` with `X-API-Key`.
4. Review blocked auth/rate events:

```powershell
curl -H "X-API-Key: <key>" http://127.0.0.1:8787/v1/audit/auth_blocks
```

Rotate keys:

```powershell
python victor_os\ops_rotate_api_keys.py
```

Then restart API/clients so new keys are picked up.

## Incident Response

1. Enable kill switch (if unsafe behavior):

- Telegram: `/kill on`
- API: `POST /v1/control/kill_switch` with `{"enabled": true}`

2. Put session in safe mode:

- API: `POST /v1/control/safe_mode`

3. Capture evidence:

- `docs/reports/agent_framework.err.log`
- `docs/reports/agent_framework.out.log`
- `docs/reports/ops_health_report.json`

4. Restore from latest backup if state corruption is suspected.

## Daily Automation (Windows Task Scheduler)

Register daily maintenance at 8:00 AM:

```powershell
schtasks /Create /TN "VictorDailyMaintenance" /SC DAILY /ST 08:00 /TR "cmd /c cd /d \"C:\Users\HomePC\Desktop\My Personal Assistant\" && python victor_os\ops_daily_maintenance.py" /F
```

Register daily health-only check at 7:30 AM:

```powershell
schtasks /Create /TN "VictorDailyHealth" /SC DAILY /ST 07:30 /TR "cmd /c cd /d \"C:\Users\HomePC\Desktop\My Personal Assistant\" && set VICTOR_API_KEY=<your_key> && python victor_os\ops_health_monitor.py" /F
```
