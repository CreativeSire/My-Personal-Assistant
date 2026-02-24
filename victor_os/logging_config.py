"""
Victor-OS Structured Logging
JSON rotating file handler + CSV backward-compat handler for dashboard.
"""

import logging
import logging.handlers
import json
import uuid
import os
import csv
import contextvars
from datetime import datetime, timezone

# Correlation ID for tracing requests across agents
correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)


def new_correlation_id() -> str:
    cid = uuid.uuid4().hex[:12]
    correlation_id_var.set(cid)
    return cid


class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": correlation_id_var.get(""),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        for key in ("agent_name", "channel", "user_id", "tool_name", "duration_ms", "details"):
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = str(record.exc_info[1])
        return json.dumps(log_entry, ensure_ascii=False)


class CSVCompatHandler(logging.Handler):
    """Writes to the legacy CSV log for dashboard backward compatibility."""

    def __init__(self, csv_path: str):
        super().__init__()
        self.csv_path = csv_path
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        if not os.path.exists(csv_path):
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Timestamp", "Agent", "Action", "Status", "Details"])

    def emit(self, record: logging.LogRecord):
        try:
            agent = getattr(record, "agent_name", record.name.split(".")[-1])
            action = record.getMessage()[:100]
            status = record.levelname
            details = getattr(record, "details", "")
            with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    agent, action, status, details
                ])
        except Exception:
            pass


_initialized = False


def setup_logging(log_dir: str, level: int = logging.INFO) -> logging.Logger:
    """Initialize the Victor-OS logging system. Safe to call multiple times."""
    global _initialized
    if _initialized:
        return logging.getLogger("victor_os")

    os.makedirs(log_dir, exist_ok=True)

    root = logging.getLogger("victor_os")
    root.setLevel(level)
    root.handlers.clear()

    # 1. JSON file handler (rotating, 5MB per file, keep 5)
    json_handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "victor_os.log.json"),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    json_handler.setFormatter(JSONFormatter())
    root.addHandler(json_handler)

    # 2. Console handler (human-readable)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(console)

    # 3. Legacy CSV handler (backward compat for dashboard)
    csv_path = os.path.join(log_dir, "system_logs.csv")
    csv_handler = CSVCompatHandler(csv_path)
    csv_handler.setLevel(logging.INFO)
    root.addHandler(csv_handler)

    _initialized = True
    return root


def get_logger(name: str) -> logging.Logger:
    """Get a child logger under the victor_os namespace."""
    return logging.getLogger(f"victor_os.{name}")
