import os
import socket
import psutil

from config import get_config
from logging_config import get_logger, setup_logging

cfg = get_config()
setup_logging(cfg.log_dir)
logger = get_logger("monitor")

DATA_DIR = cfg.data_dir
LOG_FILE = cfg.log_file

os.makedirs(DATA_DIR, exist_ok=True)


def log_activity(agent_name, action, status, details=""):
    """Logs an event via structured logger (backward-compatible signature)."""
    logger.info(
        action,
        extra={"agent_name": agent_name, "details": details},
    )


def check_active_ports(ports=None):
    """Checks if specific ports are open/active."""
    if ports is None:
        ports = list(cfg.monitor_ports)
    results = {}
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            is_open = s.connect_ex(('localhost', port)) == 0
            results[port] = "OPEN" if is_open else "CLOSED"
    return results


def get_system_metrics():
    """Returns live CPU, RAM, and port status for the dashboard."""
    try:
        return {
            "cpu": psutil.cpu_percent(interval=None),
            "ram": psutil.virtual_memory().percent,
            "disk": psutil.disk_usage('/').percent,
            "ports": check_active_ports()
        }
    except Exception:
        return {"cpu": 0, "ram": 0, "disk": 0, "ports": {}}


if __name__ == "__main__":
    log_activity("System", "Boot", "Success", "Monitor Initialized")
