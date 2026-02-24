"""
Victor-OS File Bridge
Runs on your local PC (Windows/Mac/Linux). Watches configured folders
and POSTs new files to Victor's REST API for automatic ingestion.

Usage:
    python file_bridge.py

Configuration (environment variables or .env in this file's directory):
    VICTOR_API_URL      - Victor API base URL (e.g. http://localhost:8787 or https://your-ec2-domain.com)
    VICTOR_API_KEY      - API key from VICTOR_API_KEY in your .env
    BRIDGE_WATCH_DIRS   - Comma-separated list of folders to watch
                          Default: Desktop,Downloads,Documents
    BRIDGE_POLL_SECONDS - How often to scan for new files (default: 30)
    BRIDGE_EXTENSIONS   - Comma-separated extensions to process (default: pdf,docx,txt,md,jpg,jpeg,png,mp3,wav,m4a)
    BRIDGE_STATE_FILE   - Where to store seen-file hashes (default: .bridge_state.json in this dir)
"""

import hashlib
import json
import mimetypes
import os
import sys
import time
import logging
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass  # dotenv optional

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
VICTOR_API_URL = os.getenv("VICTOR_API_URL", "http://127.0.0.1:8787").rstrip("/")
VICTOR_API_KEY = os.getenv("VICTOR_API_KEY", "")
POLL_SECONDS = max(5, int(os.getenv("BRIDGE_POLL_SECONDS", "30")))
STATE_FILE = os.getenv(
    "BRIDGE_STATE_FILE",
    str(Path(__file__).resolve().parent / ".bridge_state.json"),
)

_DEFAULT_EXTENSIONS = {"pdf", "docx", "doc", "txt", "md", "jpg", "jpeg", "png",
                        "gif", "webp", "mp3", "wav", "m4a", "ogg", "zip"}
_ext_raw = os.getenv("BRIDGE_EXTENSIONS", "")
WATCH_EXTENSIONS = (
    {e.strip().lower().lstrip(".") for e in _ext_raw.split(",") if e.strip()}
    if _ext_raw else _DEFAULT_EXTENSIONS
)

# Default watch directories — expand user paths
_home = Path.home()
_DEFAULT_DIRS = [
    _home / "Desktop",
    _home / "Downloads",
    _home / "Documents",
]
_dirs_raw = os.getenv("BRIDGE_WATCH_DIRS", "")
if _dirs_raw:
    WATCH_DIRS = [Path(d.strip()).expanduser() for d in _dirs_raw.split(",") if d.strip()]
else:
    WATCH_DIRS = [d for d in _DEFAULT_DIRS if d.exists()]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [bridge] %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("file_bridge")

# ---------------------------------------------------------------------------
# State management — tracks which files have already been sent
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"seen": {}}


def _save_state(state: dict) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as exc:
        log.warning(f"Could not save state: {exc}")


def _file_hash(path: Path) -> str:
    """Fast content hash using first 64KB + file size."""
    h = hashlib.sha256()
    try:
        size = path.stat().st_size
        h.update(str(size).encode())
        with open(path, "rb") as f:
            h.update(f.read(65536))
    except Exception:
        h.update(str(path).encode())
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Upload logic
# ---------------------------------------------------------------------------

def _ingest_file(path: Path) -> bool:
    """
    POST a file to Victor's /v1/files/ingest endpoint.
    Returns True on success, False on failure.
    """
    mime_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    headers = {}
    if VICTOR_API_KEY:
        headers["X-Victor-API-Key"] = VICTOR_API_KEY

    try:
        with open(path, "rb") as f:
            resp = requests.post(
                f"{VICTOR_API_URL}/v1/files/ingest",
                files={"file": (path.name, f, mime_type)},
                data={"source": "file_bridge", "original_path": str(path)},
                headers=headers,
                timeout=120,
            )
        if resp.status_code in (200, 201, 202):
            log.info(f"Uploaded: {path.name} → {resp.status_code}")
            return True
        else:
            log.warning(f"Upload failed ({resp.status_code}): {path.name} — {resp.text[:200]}")
            return False
    except requests.exceptions.ConnectionError:
        log.warning(f"Cannot reach Victor at {VICTOR_API_URL}. Will retry next cycle.")
        return False
    except Exception as exc:
        log.warning(f"Upload error for {path.name}: {exc}")
        return False


def _ping_victor() -> bool:
    """Check if Victor API is reachable."""
    try:
        resp = requests.get(f"{VICTOR_API_URL}/health", timeout=5)
        return resp.status_code < 500
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Scan loop
# ---------------------------------------------------------------------------

def _scan_once(state: dict) -> int:
    """Scan all watch directories, upload new files. Returns count uploaded."""
    seen: dict = state.setdefault("seen", {})
    uploaded = 0

    for watch_dir in WATCH_DIRS:
        if not watch_dir.exists():
            continue
        try:
            entries = list(watch_dir.iterdir())
        except PermissionError:
            continue

        for entry in entries:
            if not entry.is_file():
                continue
            ext = entry.suffix.lstrip(".").lower()
            if ext not in WATCH_EXTENSIONS:
                continue

            # Skip very new files (still being written) — must be >5s old
            try:
                age = time.time() - entry.stat().st_mtime
                if age < 5:
                    continue
            except Exception:
                continue

            key = str(entry.resolve())
            fhash = _file_hash(entry)

            if seen.get(key) == fhash:
                continue  # Already processed this version

            log.info(f"New file detected: {entry.name} ({entry.stat().st_size // 1024}KB)")
            if _ingest_file(entry):
                seen[key] = fhash
                uploaded += 1

    if uploaded:
        _save_state(state)

    return uploaded


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    log.info("=" * 60)
    log.info("  Victor-OS File Bridge")
    log.info("=" * 60)
    log.info(f"  API: {VICTOR_API_URL}")
    log.info(f"  Watching: {[str(d) for d in WATCH_DIRS]}")
    log.info(f"  Extensions: {sorted(WATCH_EXTENSIONS)}")
    log.info(f"  Poll interval: {POLL_SECONDS}s")
    log.info(f"  State file: {STATE_FILE}")
    log.info("")

    if not VICTOR_API_KEY:
        log.warning("VICTOR_API_KEY not set — requests will be unauthenticated")

    if not WATCH_DIRS:
        log.error("No watch directories found. Set BRIDGE_WATCH_DIRS or ensure Desktop/Downloads/Documents exist.")
        sys.exit(1)

    # Initial connectivity check
    if _ping_victor():
        log.info("Victor API is reachable. Bridge is active.")
    else:
        log.warning(f"Victor API not reachable at {VICTOR_API_URL}. Will keep retrying.")

    state = _load_state()
    cycle = 0

    while True:
        cycle += 1
        try:
            count = _scan_once(state)
            if count:
                log.info(f"Cycle {cycle}: uploaded {count} file(s)")
        except KeyboardInterrupt:
            log.info("Bridge stopped by user.")
            break
        except Exception as exc:
            log.error(f"Scan error: {exc}")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
