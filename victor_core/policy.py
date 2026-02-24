from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

from .contracts import PolicyTier


@dataclass
class PolicyInput:
    action: str
    target: str = ""
    channel: str = "system"
    actor: str = "system"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyVerdict:
    tier: PolicyTier
    allowed: bool
    reason: str
    requires_approval: bool = False


class PolicyEngine:
    """
    Safety kernel v1.
    - Hard deny zones.
    - Session safe mode.
    - Violation-triggered downgrade.
    """

    def __init__(self):
        self._safe_mode_by_session: dict[str, bool] = {}
        self._violation_count_by_session: dict[str, int] = {}
        self._auto_downgrade_threshold = 3
        self._blocked_patterns = [
            r"(?i)\\passwords?\\",
            r"(?i)\\credentials?\\",
            r"(?i)\\wallet\\",
            r"(?i)bank",
            r"(?i)paystack",
            r"(?i)flutterwave",
            r"(?i)paypal",
            r"(?i)chrome\\user data",
            r"(?i)credential manager",
        ]

    def set_safe_mode(self, session_id: str, enabled: bool) -> None:
        self._safe_mode_by_session[str(session_id or "").strip()] = bool(enabled)

    def get_safe_mode(self, session_id: str) -> bool:
        return self._safe_mode_by_session.get(str(session_id or "").strip(), False)

    def register_violation(self, session_id: str) -> int:
        sid = str(session_id or "").strip()
        current = self._violation_count_by_session.get(sid, 0) + 1
        self._violation_count_by_session[sid] = current
        if current >= self._auto_downgrade_threshold:
            self.set_safe_mode(sid, True)
        return current

    def clear_violations(self, session_id: str) -> None:
        sid = str(session_id or "").strip()
        self._violation_count_by_session[sid] = 0

    def evaluate(self, req: PolicyInput, session_id: str = "") -> PolicyVerdict:
        action = str(req.action or "").strip().lower()
        target = str(req.target or "").strip()

        if self._is_hard_deny_target(target) or self._is_hard_deny_action(action):
            self.register_violation(session_id)
            return PolicyVerdict(
                tier=PolicyTier.DENY,
                allowed=False,
                reason="Target/action falls into hard deny zone",
                requires_approval=False,
            )

        if self.get_safe_mode(session_id):
            if action in {"read", "inspect", "list", "status"}:
                return PolicyVerdict(
                    tier=PolicyTier.ALLOW_WITH_LOG,
                    allowed=True,
                    reason="Session safe mode: read-only actions auto allowed",
                    requires_approval=False,
                )
            return PolicyVerdict(
                tier=PolicyTier.REQUIRE_APPROVAL,
                allowed=False,
                reason="Session safe mode requires approval for non-read action",
                requires_approval=True,
            )

        if action in {"write", "delete", "execute", "launch", "send"}:
            return PolicyVerdict(
                tier=PolicyTier.ALLOW_AUTO,
                allowed=True,
                reason="High-autonomy posture: action auto-allowed with logging",
                requires_approval=False,
            )

        return PolicyVerdict(
            tier=PolicyTier.ALLOW_WITH_LOG,
            allowed=True,
            reason="Default low-risk action path",
            requires_approval=False,
        )

    def check_role_permission(self, user_role: str, action_tier: str) -> bool:
        """
        Role-based permission check helper used by multi-user adapters.
        action_tier is a PolicyTier string (e.g. "ALLOW_AUTO", "REQUIRE_APPROVAL").
        """
        role = str(user_role or "").strip().lower()
        tier = str(action_tier or "").strip().upper()
        role_permissions = {
            "owner": {"ALLOW_AUTO", "ALLOW_WITH_LOG", "REQUIRE_APPROVAL", "DENY"},
            "trusted": {"ALLOW_AUTO", "ALLOW_WITH_LOG"},
            "viewer": {"ALLOW_WITH_LOG"},
        }
        return tier in role_permissions.get(role, set())

    def _is_hard_deny_target(self, target: str) -> bool:
        if not target:
            return False
        lower = target.lower()
        for pattern in self._blocked_patterns:
            if re.search(pattern, lower):
                return True
        # Additional common OS credential stores.
        if os.name == "nt":
            windows_cred_paths = [
                "appdata\\local\\google\\chrome\\user data\\default\\login data",
                "appdata\\local\\microsoft\\credentials",
                "windows\\system32\\config\\sam",
            ]
            return any(p in lower for p in windows_cred_paths)
        return False

    def _is_hard_deny_action(self, action: str) -> bool:
        blocked_actions = {
            "extract_passwords",
            "dump_credentials",
            "bypass_mfa",
            "exfiltrate_secret",
        }
        return action in blocked_actions
