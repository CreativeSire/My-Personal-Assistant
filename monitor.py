import csv
import os
import time
from datetime import datetime
import psutil # For CPU/RAM usage

# --- CONFIGURATION ---
# Note: These paths are relative to victor_os/ directory if run from there, 
# or handled via absolute paths for robustness.
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
LOG_FILE = os.path.join(DATA_DIR, "system_logs.csv")
STATUS_FILE = os.path.join(DATA_DIR, "system_status.json")

# Ensure data directory exists
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# Initialize Log File if missing
if not os.path.exists(LOG_FILE):
    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Timestamp", "Agent", "Action", "Status", "Details"])

def log_activity(agent_name, action, status, details=""):
    """
    Logs an event to the CSV database.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([timestamp, agent_name, action, status, details])
        print(f"📝 [LOGGED] {agent_name}: {action} - {status}")
    except Exception as e:
        print(f"❌ Logging Error: {e}")

import socket

def check_active_ports(ports=[8501, 7777, 3000, 8000]):
    """
    Checks if specific ports are open/active.
    """
    results = {}
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            is_open = s.connect_ex(('localhost', port)) == 0
            results[port] = "OPEN" if is_open else "CLOSED"
    return results

def get_system_metrics():
    """
    Returns live CPU, RAM, and port status for the dashboard.
    """
    try:
        return {
            "cpu": psutil.cpu_percent(interval=None),
            "ram": psutil.virtual_memory().percent,
            "disk": psutil.disk_usage('/').percent,
            "ports": check_active_ports()
        }
    except:
        return {"cpu": 0, "ram": 0, "disk": 0, "ports": {}}

# Test run
if __name__ == "__main__":
    log_activity("System", "Boot", "Success", "Monitor Initialized")
