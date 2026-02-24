from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_PATH = os.path.join(ROOT, "docs", "reports", "ops_health_report.json")


def _request(url: str, headers: dict[str, str] | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            body = resp.read().decode("utf-8")
            try:
                data = json.loads(body or "{}")
            except Exception:
                data = {"raw": body}
            return resp.status, data
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc.fp else "{}"
        try:
            return exc.code, json.loads(raw or "{}")
        except Exception:
            return exc.code, {"raw": raw}
    except Exception as exc:
        return 0, {"error": str(exc)}


def main() -> int:
    api_key = os.getenv("VICTOR_API_KEY", "").strip()
    strict_desktop = os.getenv("HEALTH_REQUIRE_DESKTOP", "false").strip().lower() in {"1", "true", "yes", "on"}
    headers = {"X-API-Key": api_key} if api_key else {}
    checks = []

    status, body = _request("http://127.0.0.1:8787/v1/capabilities", headers=headers)
    checks.append({"name": "agent_api", "status": status, "ok": status == 200, "body": body})

    status, body = _request("http://127.0.0.1:8787/v1/metrics/router", headers=headers)
    checks.append({"name": "router_metrics", "status": status, "ok": status == 200, "body": body})

    status, body = _request("http://127.0.0.1:8787/v1/metrics/tools", headers=headers)
    checks.append({"name": "tools_metrics", "status": status, "ok": status == 200, "body": body})

    status, body = _request("http://127.0.0.1:5000/api/health")
    checks.append(
        {
            "name": "desktop_ui",
            "status": status,
            "ok": (status == 200) or (not strict_desktop),
            "required": strict_desktop,
            "body": body,
        }
    )

    report = {
        "generated_at": time.time(),
        "all_ok": all(c["ok"] for c in checks),
        "checks": checks,
    }
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"wrote={REPORT_PATH}")
    print(f"all_ok={report['all_ok']}")
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
