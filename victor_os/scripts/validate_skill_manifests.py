"""
Validate Victor skill manifests against the canonical schema.

Usage:
  python scripts/validate_skill_manifests.py
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skill_registry import SkillRegistry


def main() -> int:
    registry = SkillRegistry()
    count = registry.discover_and_load()
    loaded = registry.loaded_skills
    if count <= 0 or not loaded:
        print("FAIL: no skills loaded")
        return 1
    print(f"PASS: loaded and validated {len(loaded)} skills")
    for name, manifest in sorted(loaded.items()):
        print(f"- {name} v{manifest.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

