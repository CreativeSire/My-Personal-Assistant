from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class StyleProfile:
    contact: str
    tone: str = "neutral"
    greeting: str = ""
    signoff: str = ""
    brevity: str = "normal"  # short|normal|long
    updated_at: float = 0.0

    def apply(self, text: str) -> str:
        msg = str(text or "").strip()
        if self.brevity == "short" and len(msg) > 220:
            msg = msg[:220].rstrip() + "…"
        if self.greeting:
            msg = f"{self.greeting}\n{msg}"
        if self.signoff:
            msg = f"{msg}\n{self.signoff}"
        return msg.strip()


class StyleStore:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def _load(self) -> dict[str, Any]:
        if not os.path.exists(self.path):
            return {"profiles": {}}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"profiles": {}}

    def _save(self, data: dict[str, Any]) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def get(self, contact: str) -> StyleProfile:
        key = str(contact or "").strip()
        data = self._load()
        raw = (data.get("profiles") or {}).get(key) or {}
        return StyleProfile(
            contact=key,
            tone=str(raw.get("tone") or "neutral"),
            greeting=str(raw.get("greeting") or ""),
            signoff=str(raw.get("signoff") or ""),
            brevity=str(raw.get("brevity") or "normal"),
            updated_at=float(raw.get("updated_at") or 0.0),
        )

    def set(self, profile: StyleProfile) -> None:
        data = self._load()
        profiles = data.setdefault("profiles", {})
        profiles[profile.contact] = {
            "tone": profile.tone,
            "greeting": profile.greeting,
            "signoff": profile.signoff,
            "brevity": profile.brevity,
            "updated_at": time.time(),
        }
        self._save(data)

    def list(self) -> list[dict[str, Any]]:
        data = self._load()
        out = []
        for k, v in sorted((data.get("profiles") or {}).items()):
            item = dict(v)
            item["contact"] = k
            out.append(item)
        return out

