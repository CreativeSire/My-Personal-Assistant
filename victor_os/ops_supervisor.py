from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VOS = Path(__file__).resolve().parent
LOGDIR = ROOT / "docs" / "reports"


def _is_running(pattern: str) -> bool:
    try:
        out = subprocess.check_output(
            f"wmic process where \"name='python.exe'\" get CommandLine",
            shell=True,
            stderr=subprocess.STDOUT,
            text=True,
        )
        return pattern.lower() in out.lower()
    except Exception:
        return False


def _start(cmd: str, title: str) -> None:
    LOGDIR.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        f'start "{title}" cmd /c "{cmd}"',
        shell=True,
        cwd=str(ROOT),
    )


def ensure_running() -> list[str]:
    started: list[str] = []
    if not _is_running("agent_framework.py"):
        _start(
            f'python "{ROOT}\\agent_framework.py" 1>>"{LOGDIR}\\agent_framework.out.log" 2>>"{LOGDIR}\\agent_framework.err.log"',
            "Victor API",
        )
        started.append("agent_api")
    if not _is_running("telegram_server.py"):
        _start(
            f'cd /d "{VOS}" && python telegram_server.py 1>>"{LOGDIR}\\telegram_server.out.log" 2>>"{LOGDIR}\\telegram_server.err.log"',
            "Victor Telegram",
        )
        started.append("telegram")
    if not _is_running("desktop_server.py"):
        _start(
            f'cd /d "{VOS}" && python desktop_server.py 1>>"{LOGDIR}\\desktop_server.out.log" 2>>"{LOGDIR}\\desktop_server.err.log"',
            "Victor Desktop",
        )
        started.append("desktop")
    return started


def main() -> int:
    parser = argparse.ArgumentParser(description="Ensure Victor services are running; optional monitoring loop.")
    parser.add_argument("--watch", action="store_true", help="Keep monitoring and auto-restart")
    parser.add_argument("--interval", type=int, default=30, help="Watch interval seconds")
    args = parser.parse_args()

    started = ensure_running()
    print(f"started={started}")
    if not args.watch:
        return 0
    while True:
        time.sleep(max(5, args.interval))
        started = ensure_running()
        if started:
            print(f"restarted={started} at={time.time()}")


if __name__ == "__main__":
    raise SystemExit(main())
