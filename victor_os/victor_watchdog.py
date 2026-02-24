"""
Victor-OS Watchdog
Monitors telegram_server.py and restarts it automatically if it crashes.
Uses a PID file written by telegram_server.py for reliable detection.
Launched by start_victor.bat (which runs via Task Scheduler at login).

Usage: python victor_watchdog.py
"""

import os
import sys
import time
import subprocess
import logging
import signal
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
VENV_PYTHON = BASE_DIR.parent / "victor_os_env" / "Scripts" / "python.exe"
PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable

CHECK_INTERVAL  = int(os.getenv("WATCHDOG_CHECK_SECONDS",     "30"))
MAX_RESTARTS_HR = int(os.getenv("WATCHDOG_MAX_RESTARTS_HOUR", "10"))
RESTART_DELAY   = int(os.getenv("WATCHDOG_RESTART_DELAY",      "5"))
# How long to wait at boot before assuming Victor failed to start on its own
BOOT_GRACE      = int(os.getenv("WATCHDOG_BOOT_GRACE",        "90"))

DATA_DIR          = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

LOG_FILE          = DATA_DIR / "watchdog.log"
PID_FILE          = DATA_DIR / "telegram_server.pid"
WATCHDOG_PID_FILE = DATA_DIR / "watchdog.pid"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [watchdog] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
    ],
)
log = logging.getLogger("victor_watchdog")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pid_alive(pid: int) -> bool:
    """Return True if a process with this PID is currently running."""
    if sys.platform == "win32":
        import ctypes
        # PROCESS_QUERY_LIMITED_INFORMATION works across privilege levels
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle == 0:
            # OpenProcess failed — check if it's access denied (process exists) or not found
            ERROR_ACCESS_DENIED = 5
            last_err = ctypes.windll.kernel32.GetLastError()
            return last_err == ERROR_ACCESS_DENIED  # True = process exists but we can't open it
        STILL_ACTIVE = 259
        exit_code = ctypes.c_ulong(0)
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return exit_code.value == STILL_ACTIVE
    # Unix fallback
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Process exists, we just can't signal it
    except Exception:
        return False


def _read_pid_file(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Singleton guard — only one watchdog at a time (uses exclusive file lock)
# ---------------------------------------------------------------------------

_lock_handle = None
LOCK_FILE = DATA_DIR / "watchdog.lock"


def _acquire_singleton():
    global _lock_handle
    # Use an exclusive OS file lock so concurrent launches can't both win
    if sys.platform == "win32":
        import msvcrt
        try:
            _lock_handle = open(LOCK_FILE, "w")
            msvcrt.locking(_lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
        except (OSError, IOError):
            log.info("Watchdog already running (lock held). Exiting.")
            sys.exit(0)
    else:
        import fcntl
        try:
            _lock_handle = open(LOCK_FILE, "w")
            fcntl.flock(_lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError):
            log.info("Watchdog already running (lock held). Exiting.")
            sys.exit(0)
    # Write PID for informational purposes only (not used for liveness check)
    _lock_handle.write(str(os.getpid()))
    _lock_handle.flush()
    WATCHDOG_PID_FILE.write_text(str(os.getpid()))


def _release_singleton():
    global _lock_handle
    try:
        if _lock_handle:
            _lock_handle.close()
            _lock_handle = None
        LOCK_FILE.unlink(missing_ok=True)
        WATCHDOG_PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Victor liveness — three states
# ---------------------------------------------------------------------------

def _victor_state() -> str:
    """
    Returns:
      'alive'   — PID file exists and process is running
      'dead'    — PID file exists but process is gone (genuine crash)
      'absent'  — no PID file at all (never started, or clean shutdown)
    """
    pid = _read_pid_file(PID_FILE)
    if pid is None:
        return "absent"
    return "alive" if _pid_alive(pid) else "dead"


# ---------------------------------------------------------------------------
# Restart rate limiter
# ---------------------------------------------------------------------------
_restart_times: list[float] = []


def _can_restart() -> bool:
    now = time.time()
    recent = [t for t in _restart_times if now - t < 3600]
    _restart_times[:] = recent
    if len(recent) >= MAX_RESTARTS_HR:
        log.error(
            f"telegram_server.py crashed {len(recent)}x in the last hour. "
            "Auto-restart suspended. Check data/victor_error.log for root cause."
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------
_victor_proc: subprocess.Popen | None = None


def _start_victor():
    global _victor_proc
    script = BASE_DIR / "telegram_server.py"
    if not script.exists():
        log.error(f"telegram_server.py not found at {script}")
        return
    if not _can_restart():
        return

    _restart_times.append(time.time())
    log.info(f"Starting telegram_server.py (restart #{len(_restart_times)})...")
    try:
        flags = subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
        _victor_proc = subprocess.Popen(
            [PYTHON, str(script)],
            cwd=str(BASE_DIR),
            creationflags=flags,
        )
        log.info(f"telegram_server.py launched — PID {_victor_proc.pid}")
        # Give it time to start and write its own PID file
        for _ in range(20):
            if _shutdown:
                break
            time.sleep(1)
            if PID_FILE.exists():
                log.info("PID file confirmed — Victor is up.")
                break
    except Exception as exc:
        log.error(f"Failed to start telegram_server.py: {exc}")
        _restart_times.pop()  # Don't count a launch failure as a crash


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------
_shutdown = False


def _on_signal(sig, frame):
    global _shutdown
    log.info(f"Signal {sig} — watchdog shutting down.")
    _shutdown = True


signal.signal(signal.SIGINT,  _on_signal)
signal.signal(signal.SIGTERM, _on_signal)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    _acquire_singleton()

    log.info("=" * 55)
    log.info("  Victor-OS Watchdog")
    log.info(f"  Python      : {PYTHON}")
    log.info(f"  PID file    : {PID_FILE}")
    log.info(f"  Boot grace  : {BOOT_GRACE}s")
    log.info(f"  Check every : {CHECK_INTERVAL}s | max restarts/hr: {MAX_RESTARTS_HR}")
    log.info("=" * 55)

    # ── Boot grace period ──────────────────────────────────────────────────
    # Wait for start_victor.bat to launch telegram_server.py and for it to
    # write its PID file. Only start monitoring after the grace window.
    log.info(f"Boot grace period: waiting up to {BOOT_GRACE}s for Victor to start...")
    for elapsed in range(BOOT_GRACE):
        if _shutdown:
            break
        if PID_FILE.exists():
            pid = _read_pid_file(PID_FILE)
            if pid and _pid_alive(pid):
                log.info(f"Victor detected after {elapsed}s (PID {pid}). Monitoring started.")
                break
        time.sleep(1)
    else:
        # Grace period expired without Victor appearing — start it ourselves
        log.warning(f"Victor did not appear within {BOOT_GRACE}s grace period. Starting it now...")
        _start_victor()

    # ── Main watch loop ────────────────────────────────────────────────────
    while not _shutdown:
        state = _victor_state()

        if state == "dead":
            log.warning("Victor crashed (PID file stale). Restarting...")
            PID_FILE.unlink(missing_ok=True)  # Clear stale PID before restart
            time.sleep(RESTART_DELAY)
            if not _shutdown:
                _start_victor()

        elif state == "absent" and _victor_proc is not None:
            # We spawned Victor but the PID file disappeared — it exited cleanly or crashed
            if _victor_proc.poll() is not None:
                log.warning(f"Victor exited (code {_victor_proc.returncode}). Restarting...")
                time.sleep(RESTART_DELAY)
                if not _shutdown:
                    _start_victor()

        # state == "alive": do nothing, all good

        for _ in range(CHECK_INTERVAL):
            if _shutdown:
                break
            time.sleep(1)

    _release_singleton()
    log.info("Watchdog stopped.")


if __name__ == "__main__":
    main()
