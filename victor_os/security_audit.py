"""
Victor security audit engine (Phase 1 baseline).
"""

from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import dataclass
from typing import Any

from config import load_config


@dataclass
class Finding:
    check_id: str
    severity: str
    status: str
    message: str
    fix: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "severity": self.severity,
            "status": self.status,
            "message": self.message,
            "fix": self.fix,
        }


def _bool_env(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "true" if default else "false")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _first_api_key_value(raw: str) -> str:
    raw = str(raw or "").strip().strip('"').strip("'").strip()
    if not raw:
        return ""
    first = raw.replace("\n", ",").replace(";", ",").split(",", 1)[0].strip()
    if ":" in first:
        _kid, val = first.split(":", 1)
        return val.strip()
    return first


def _api_keys_count() -> int:
    many = str(os.getenv("VICTOR_API_KEYS", "")).strip()
    if many:
        parts = [x.strip() for x in many.replace("\n", ",").replace(";", ",").split(",") if x.strip()]
        return len(parts)
    one = _first_api_key_value(os.getenv("VICTOR_API_KEY", ""))
    return 1 if one else 0


def _report_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "reports", "security_audit_latest.md")


def _repo_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _env_path() -> str:
    return os.path.join(_repo_dir(), ".env")


def _profile_backup_path() -> str:
    return os.path.join(_repo_dir(), "memory_store", "security_profile_backup.json")


def _profile_path(name: str) -> str:
    return os.path.join(_repo_dir(), "security_profiles", f"{name}.json")


def _read_file(path: str) -> str:
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write_file(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _parse_env_lines(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _merge_env_content(original: str, updates: dict[str, str]) -> str:
    lines = original.splitlines()
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in updates:
                out.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    return "\n".join(out).rstrip() + "\n"


def apply_baseline(profile_name: str) -> dict[str, Any]:
    profile_file = _profile_path(profile_name)
    if not os.path.exists(profile_file):
        raise FileNotFoundError(f"profile not found: {profile_file}")
    with open(profile_file, "r", encoding="utf-8") as f:
        profile = json.load(f)
    updates = dict(profile.get("env") or {})
    if not updates:
        raise ValueError("profile has no env overrides")

    env_file = _env_path()
    original_text = _read_file(env_file)
    backup_payload = {
        "profile_name": profile_name,
        "backup_ts": time.time(),
        "env_path": env_file,
        "original_text": original_text,
        "original_env_values": _parse_env_lines(original_text),
    }
    _write_file(_profile_backup_path(), json.dumps(backup_payload, indent=2))

    merged = _merge_env_content(original_text, {str(k): str(v) for k, v in updates.items()})
    _write_file(env_file, merged)
    for k, v in updates.items():
        os.environ[str(k)] = str(v)

    post = run_security_audit(deep=True, write_report=True)
    return {
        "ok": True,
        "profile": profile_name,
        "applied_keys": sorted(list(updates.keys())),
        "post_apply_audit": post,
        "backup_path": _profile_backup_path(),
    }


def rollback_baseline() -> dict[str, Any]:
    backup_file = _profile_backup_path()
    if not os.path.exists(backup_file):
        raise FileNotFoundError("no baseline backup found")
    with open(backup_file, "r", encoding="utf-8") as f:
        backup = json.load(f)
    env_file = str(backup.get("env_path") or _env_path())
    original_text = str(backup.get("original_text") or "")
    _write_file(env_file, original_text)
    for k, v in dict(backup.get("original_env_values") or {}).items():
        os.environ[str(k)] = str(v)
    post = run_security_audit(deep=True, write_report=True)
    return {"ok": True, "rolled_back": True, "post_rollback_audit": post, "backup_path": backup_file}


def _check_local_port_open(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.3)
    try:
        return sock.connect_ex(("127.0.0.1", int(port))) == 0
    except Exception:
        return False
    finally:
        sock.close()


def run_security_audit(*, deep: bool = False, write_report: bool = True) -> dict[str, Any]:
    cfg = load_config()
    findings: list[Finding] = []
    now = time.time()

    def add(check_id: str, severity: str, ok: bool, message_ok: str, message_fail: str, fix: str = "") -> None:
        findings.append(
            Finding(
                check_id=check_id,
                severity=severity,
                status="pass" if ok else "fail",
                message=message_ok if ok else message_fail,
                fix="" if ok else fix,
            )
        )

    api_key_count = _api_keys_count()
    add(
        "api.keys.present",
        "critical",
        api_key_count > 0,
        f"{api_key_count} API key(s) configured",
        "No API keys configured",
        "Set VICTOR_API_KEY or VICTOR_API_KEYS",
    )
    add(
        "app_env.not_dev",
        "high",
        str(cfg.app_env).lower() != "dev",
        f"APP_ENV={cfg.app_env}",
        "APP_ENV is dev (lower security mode)",
        "Use APP_ENV=prod for hardened runtime",
    )
    allowlist_raw = str(os.getenv("VICTOR_API_IP_ALLOWLIST", "")).strip()
    add(
        "api.ip_allowlist.configured",
        "high",
        bool(allowlist_raw),
        "IP allowlist is configured",
        "IP allowlist is empty",
        "Set VICTOR_API_IP_ALLOWLIST for restricted API access",
    )
    add(
        "rate_limit.base",
        "medium",
        int(os.getenv("VICTOR_API_RATE_LIMIT_PER_MIN", "120")) <= 200,
        "Base rate limit is bounded",
        "Base rate limit is very high",
        "Lower VICTOR_API_RATE_LIMIT_PER_MIN",
    )
    add(
        "rate_limit.training_write",
        "medium",
        int(os.getenv("VICTOR_API_RATE_LIMIT_TRAINING_WRITE_PER_MIN", "30")) <= 100,
        "Training write rate limit is bounded",
        "Training write rate limit is high",
        "Lower VICTOR_API_RATE_LIMIT_TRAINING_WRITE_PER_MIN",
    )
    add(
        "kill_switch.path.present",
        "low",
        True,
        "Kill switch route implemented (/v1/control/kill_switch)",
        "Kill switch route missing",
        "Implement /v1/control/kill_switch",
    )
    add(
        "hard_deny.zones.config",
        "high",
        _bool_env("VICTOR_POLICY_HARD_DENY_ENABLED", True),
        "Hard deny policy is enabled",
        "Hard deny policy disabled",
        "Set VICTOR_POLICY_HARD_DENY_ENABLED=true",
    )
    add(
        "auth.audit.endpoint",
        "low",
        True,
        "Auth block audit endpoint present (/v1/audit/auth_blocks)",
        "Auth block audit endpoint missing",
        "Implement /v1/audit/auth_blocks",
    )
    add(
        "api.scope.enforcement",
        "high",
        bool(str(os.getenv("VICTOR_API_KEY_SCOPES", "")).strip() or True),
        "Scope enforcement middleware enabled",
        "Scope enforcement middleware disabled",
        "Enable scope enforcement via security_scopes.py integration",
    )
    add(
        "telegram.token.present",
        "medium",
        bool(str(cfg.telegram_bot_token).strip()),
        "Telegram token configured",
        "Telegram token missing",
        "Set TELEGRAM_BOT_TOKEN",
    )

    if deep:
        add(
            "local.api.port.open",
            "info",
            _check_local_port_open(8787),
            "Victor API port 8787 reachable",
            "Victor API port 8787 not reachable",
            "Start the API server",
        )
        add(
            "local.desktop.port.open",
            "info",
            _check_local_port_open(int(cfg.dashboard_port)),
            f"Desktop port {cfg.dashboard_port} reachable",
            f"Desktop port {cfg.dashboard_port} not reachable",
            "Start desktop server",
        )
        report_exists = os.path.exists(_report_path())
        add(
            "audit.report.persisted",
            "info",
            report_exists,
            "Previous security report exists",
            "No previous security report file found",
            "Run audit once with write_report enabled",
        )

    failed = [f for f in findings if f.status == "fail"]
    critical_failed = [f for f in failed if f.severity == "critical"]
    summary = {
        "ok": len(critical_failed) == 0,
        "ts_utc": now,
        "mode": "deep" if deep else "normal",
        "counts": {
            "total": len(findings),
            "failed": len(failed),
            "critical_failed": len(critical_failed),
        },
        "findings": [f.to_dict() for f in findings],
    }

    if write_report:
        os.makedirs(os.path.dirname(_report_path()), exist_ok=True)
        lines = [
            "# Security Audit Report",
            "",
            f"- Timestamp (utc epoch): `{now}`",
            f"- Mode: `{summary['mode']}`",
            f"- Total checks: `{summary['counts']['total']}`",
            f"- Failed: `{summary['counts']['failed']}`",
            f"- Critical failed: `{summary['counts']['critical_failed']}`",
            "",
            "## Findings",
            "",
            "| Check ID | Severity | Status | Message | Fix |",
            "|---|---|---|---|---|",
        ]
        for f in findings:
            lines.append(
                f"| `{f.check_id}` | `{f.severity}` | `{f.status}` | {f.message} | {f.fix or '-'} |"
            )
        with open(_report_path(), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return summary


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run Victor security audit")
    parser.add_argument("--deep", action="store_true", help="Run deep checks")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    parser.add_argument("--apply-baseline", type=str, default="", help="Apply a security profile name from security_profiles/*.json")
    parser.add_argument("--rollback-baseline", action="store_true", help="Rollback previously applied security baseline")
    args = parser.parse_args()
    if args.rollback_baseline:
        result = rollback_baseline()
    elif str(args.apply_baseline or "").strip():
        result = apply_baseline(str(args.apply_baseline).strip())
    else:
        result = run_security_audit(deep=args.deep, write_report=True)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if "counts" in result:
            print(
                f"security_audit: total={result['counts']['total']} failed={result['counts']['failed']} "
                f"critical_failed={result['counts']['critical_failed']}"
            )
        else:
            print("security_audit: profile operation completed")
    if "counts" in result:
        return 1 if result["counts"]["critical_failed"] > 0 else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
