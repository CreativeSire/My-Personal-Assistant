from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "docs" / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def _run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    try:
        out = subprocess.check_output(cmd, cwd=str(cwd) if cwd else None, stderr=subprocess.STDOUT, text=True)
        return 0, out
    except subprocess.CalledProcessError as exc:
        return exc.returncode, exc.output
    except Exception as exc:
        return 1, str(exc)


def _audit_auth_blocks() -> dict:
    key = os.getenv("VICTOR_API_KEY", "").strip()
    headers = {"X-API-Key": key} if key else {}
    req = urllib.request.Request("http://127.0.0.1:8787/v1/audit/auth_blocks?limit=200&since_sec=86400", headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def main() -> int:
    results = {"generated_at": time.time()}

    rc, out = _run(["python", str(ROOT / "victor_os" / "ops_backup_restore.py"), "backup"])
    results["backup"] = {"rc": rc, "out": out.strip()}

    rc, out = _run(["python", str(ROOT / "victor_os" / "ops_health_monitor.py")])
    results["health"] = {"rc": rc, "out": out.strip()}

    rc, out = _run(["python", str(ROOT / "victor_os" / "pilot_daily_report.py")])
    results["pilot_daily"] = {"rc": rc, "out": out.strip()}

    rc, out = _run(["python", str(ROOT / "victor_os" / "ml_monitoring_report.py")])
    results["ml_monitoring"] = {"rc": rc, "out": out.strip()}

    rc, out = _run(["python", str(ROOT / "victor_os" / "auto_healer_report.py")])
    results["auto_healer"] = {"rc": rc, "out": out.strip()}

    rc, out = _run(["python", str(ROOT / "victor_os" / "competitor_watch_run.py")])
    results["competitor_watch"] = {"rc": rc, "out": out.strip()}

    results["auth_blocks_daily"] = _audit_auth_blocks()

    path = REPORT_DIR / f"ops_daily_{time.strftime('%Y%m%d')}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"wrote={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
