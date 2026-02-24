from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class Macro:
    name: str
    steps: list[dict[str, Any]]
    created_at: float
    updated_at: float
    notes: str = ""


class MacroStore:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def _load(self) -> dict[str, Any]:
        if not os.path.exists(self.path):
            return {"macros": {}}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"macros": {}}

    def _save(self, data: dict[str, Any]) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def list(self) -> list[dict[str, Any]]:
        data = self._load()
        out = []
        for name, m in sorted((data.get("macros") or {}).items()):
            item = dict(m)
            item["name"] = name
            out.append(item)
        return out

    def get(self, name: str) -> Macro | None:
        data = self._load()
        raw = (data.get("macros") or {}).get(name)
        if not raw:
            return None
        return Macro(
            name=name,
            steps=list(raw.get("steps") or []),
            created_at=float(raw.get("created_at") or 0.0),
            updated_at=float(raw.get("updated_at") or 0.0),
            notes=str(raw.get("notes") or ""),
        )

    def upsert(self, macro: Macro) -> None:
        data = self._load()
        macros = data.setdefault("macros", {})
        macros[macro.name] = {
            "steps": macro.steps,
            "created_at": macro.created_at,
            "updated_at": macro.updated_at,
            "notes": macro.notes,
        }
        self._save(data)

    def create(self, name: str, steps: list[dict[str, Any]], notes: str = "") -> Macro:
        now = time.time()
        macro = Macro(name=name, steps=steps, created_at=now, updated_at=now, notes=notes)
        self.upsert(macro)
        return macro

    def delete(self, name: str) -> bool:
        data = self._load()
        macros = data.get("macros") or {}
        if name not in macros:
            return False
        del macros[name]
        self._save(data)
        return True

