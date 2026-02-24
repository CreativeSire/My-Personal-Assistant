"""
Scope model for Victor Core API routes.
"""

from __future__ import annotations

import os
from typing import Any


def required_scope_for_route(path: str, method: str) -> str:
    p = str(path or "").strip()
    m = str(method or "GET").upper()

    if p.startswith("/v1/security/"):
        return "security.read" if m == "GET" else "security.write"
    if p.startswith("/v1/audit/"):
        return "audit.read"
    if p.startswith("/v1/training/"):
        return "training.write" if m in {"POST", "PUT", "PATCH", "DELETE"} else "training.read"
    if p.startswith("/v1/control/"):
        return "control.write"
    if p.startswith("/v1/tasks"):
        return "tasks.write" if m in {"POST", "PUT", "PATCH", "DELETE"} else "tasks.read"
    if p.startswith("/v1/tools/execute"):
        return "tools.execute"
    if p.startswith("/v1/policies/"):
        return "policy.read" if m == "GET" else "policy.write"
    if p.startswith("/v1/devices"):
        return "devices.read" if m == "GET" else "devices.write"
    if p.startswith("/v1/metrics/"):
        return "metrics.read"
    if p.startswith("/v1/feedback"):
        return "feedback.write"
    if p.startswith("/v1/"):
        return "api.read" if m == "GET" else "api.write"
    return "api.read"


def parse_scope_map_env(raw: str) -> dict[str, set[str]]:
    """
    Parse:
      VICTOR_API_KEY_SCOPES=primary:all,viewer:tasks.read|metrics.read|audit.read
    """
    out: dict[str, set[str]] = {}
    raw = str(raw or "").strip()
    if not raw:
        return out
    parts = [x.strip() for x in raw.replace("\n", ",").replace(";", ",").split(",") if x.strip()]
    for item in parts:
        if ":" not in item:
            continue
        key_id, scopes = item.split(":", 1)
        key_id = key_id.strip()
        scope_set = {s.strip() for s in scopes.split("|") if s.strip()}
        if key_id and scope_set:
            out[key_id] = scope_set
    return out


def load_scope_map_from_env() -> dict[str, set[str]]:
    return parse_scope_map_env(os.getenv("VICTOR_API_KEY_SCOPES", ""))


def check_scope_allowed(key_id: str, required_scope: str, scope_map: dict[str, set[str]]) -> tuple[bool, str]:
    if not scope_map:
        return True, "scope_map_not_configured_default_allow"
    scopes = scope_map.get(key_id)
    if not scopes:
        return False, "key_has_no_scope_mapping"
    if "all" in scopes or required_scope in scopes:
        return True, "matched_scope"
    # small hierarchy shortcut
    if required_scope.endswith(".read"):
        group = required_scope.rsplit(".", 1)[0]
        if f"{group}.write" in scopes:
            return True, "write_scope_implies_read"
    return False, "required_scope_missing"


def scope_decision_payload(
    *,
    key_id: str,
    required_scope: str,
    allowed: bool,
    reason: str,
    path: str,
    method: str,
) -> dict[str, Any]:
    return {
        "key_id": key_id,
        "required_scope": required_scope,
        "allowed": bool(allowed),
        "reason": reason,
        "path": path,
        "method": method,
    }
