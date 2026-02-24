from __future__ import annotations

import argparse
import os
import shutil
import time
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
MEMORY_DIR = BASE_DIR / "memory_store"
BACKUP_DIR = MEMORY_DIR / "backups"
TARGETS = [
    MEMORY_DIR / "victor_platform.db",
    MEMORY_DIR / "victor_tasks.db",
    MEMORY_DIR / "model_registry.json",
]


def backup() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = BACKUP_DIR / f"phase23_state_{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    for path in TARGETS:
        if path.exists():
            shutil.copy2(path, out / path.name)
    return out


def restore(src: Path) -> None:
    if not src.exists() or not src.is_dir():
        raise FileNotFoundError(f"Backup folder not found: {src}")
    for path in TARGETS:
        candidate = src / path.name
        if candidate.exists():
            shutil.copy2(candidate, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup/restore critical Victor phase23 state files.")
    parser.add_argument("action", choices=["backup", "restore", "list"])
    parser.add_argument("--src", default="", help="Backup folder path (for restore)")
    args = parser.parse_args()

    if args.action == "backup":
        out = backup()
        print(f"backup_created={out}")
        return 0
    if args.action == "list":
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        items = sorted([p for p in BACKUP_DIR.iterdir() if p.is_dir()], reverse=True)
        for item in items:
            print(item)
        return 0

    src = Path(args.src).expanduser().resolve() if args.src else None
    if not src:
        raise SystemExit("--src is required for restore")
    restore(src)
    print(f"restored_from={src}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
