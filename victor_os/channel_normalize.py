from __future__ import annotations

from typing import Any
import hashlib
import re
import uuid


def normalize_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_sender_id(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "unknown"
    return raw


def ensure_message_id(value: Any, *, prefix: str = "msg") -> str:
    text = str(value or "").strip()
    if text:
        return text
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def delivery_idempotency_key(
    *,
    channel: str,
    destination: str,
    text: str,
    task_id: str = "",
    explicit_key: str = "",
) -> str:
    if explicit_key:
        return explicit_key
    basis = "|".join(
        [
            str(channel or "").strip(),
            str(destination or "").strip(),
            str(task_id or "").strip(),
            normalize_text(text),
        ]
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]
