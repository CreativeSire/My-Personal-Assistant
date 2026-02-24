from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    WAITING_APPROVAL = "waiting_approval"


class PolicyTier(str, Enum):
    ALLOW_AUTO = "ALLOW_AUTO"
    ALLOW_WITH_LOG = "ALLOW_WITH_LOG"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DENY = "DENY"


@dataclass
class TaskIntent:
    intent: str
    channel: str = "system"
    idempotency_key: str = ""
    risk_level_hint: str = "medium"
    context_refs: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)

