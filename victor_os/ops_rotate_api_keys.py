from __future__ import annotations

import base64
import os
import re
import secrets
from pathlib import Path


ENV_PATH = Path(__file__).resolve().parent / ".env"


def _new_key() -> str:
    raw = secrets.token_bytes(36)
    token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"vtr_live_{token}"


def _replace_or_append(lines: list[str], key: str, value: str) -> list[str]:
    pat = re.compile(rf"^{re.escape(key)}\s*=")
    replaced = False
    out = []
    for line in lines:
        if pat.match(line):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line.rstrip("\n"))
    if not replaced:
        out.append(f"{key}={value}")
    return out


def main() -> int:
    if not ENV_PATH.exists():
        raise SystemExit(f".env not found: {ENV_PATH}")
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()

    primary = _new_key()
    secondary = _new_key()
    combo = f"primary:{primary},secondary:{secondary}"

    lines = _replace_or_append(lines, "VICTOR_API_KEYS", combo)
    lines = _replace_or_append(lines, "VICTOR_API_KEY", primary)
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("rotated=VICTOR_API_KEYS")
    print("primary_key_set=VICTOR_API_KEY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
