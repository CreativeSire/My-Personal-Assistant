#!/usr/bin/env python3
"""
Victor OS - Management CLI
The unified entry point for all Victor operations. Replaces .bat/.vbs scripts.

Usage:
    python manage.py run        # Start the main OS (Agent Orchestrator)
    python manage.py doctor     # Run health checks
    python manage.py setup      # First-time setup
    python manage.py clean      # Clean temporary files/memory
    python manage.py test       # Run test suite
"""
import sys
import os
import subprocess
import argparse
from pathlib import Path

# Add victor_os to path so we can import modules
sys.path.append(os.path.join(os.path.dirname(__file__), "victor_os"))

def run_doctor():
    print("Running System Diagnosis...")
    subprocess.run([sys.executable, "victor_os/doctor.py"])

def run_os():
    print("Booting Victor OS...")
    # This would link to the main entry point
    subprocess.run([sys.executable, "victor_os/main.py"])

def run_tests():
    print("Running Test Suite...")
    subprocess.run([sys.executable, "-m", "pytest", "victor_os/"])

def clean_state():
    print("Cleaning runtime artifacts...")
    # Implementation for cleaning cache/logs
    pass

def run_chat():
    try:
        from victor_os.terminal_client import start_terminal_session
        start_terminal_session()
    except ImportError as e:
        print(f"Failed to load Terminal Client: {e}")
        sys.exit(1)

def run_watch():
    try:
        from victor_os.vision_daemon import start_watcher
        start_watcher()
    except ImportError as e:
        print(f"Failed to load Vision Daemon (requires pyautogui): {e}")
        sys.exit(1)

def run_dream():
    try:
        from victor_os.dream_state import start_dream_daemon
        start_dream_daemon()
    except ImportError as e:
        print(f"Failed to load Dream State: {e}")
        sys.exit(1)

def run_chronos(query=None):
    try:
        from victor_os.chronos import index_vision_log, search_chronos
        if query:
            search_chronos(query)
        else:
            index_vision_log()
    except ImportError as e:
        print(f"Failed to load Chronos: {e}")
        sys.exit(1)

def run_sentinel():
    try:
        from victor_os.sentinel import start_sentinel_daemon
        start_sentinel_daemon()
    except ImportError as e:
        print(f"Failed to load Sentinel (requires psutil): {e}")
        sys.exit(1)

def run_forge():
    try:
        from victor_os.forge import run_forge_repair
        run_forge_repair()
    except ImportError as e:
        print(f"Failed to load The Forge: {e}")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Victor OS Management Console")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    subparsers.add_parser("run", help="Start Victor OS")
    subparsers.add_parser("doctor", help="Run health checks")
    subparsers.add_parser("setup", help="Initialize environment")
    subparsers.add_parser("clean", help="Clean artifacts")
    subparsers.add_parser("test", help="Run tests")
    subparsers.add_parser("chat", help="Start Neural Interface (Terminal)")
    subparsers.add_parser("watch", help="Start Ghost Vision Daemon")
    subparsers.add_parser("dream", help="Start Dream State Research Daemon")
    
    chronos_parser = subparsers.add_parser("chronos", help="Manage Visual Memory")
    chronos_parser.add_argument("--search", "-s", help="Search query for visual memory")

    subparsers.add_parser("sentinel", help="Start Security Monitor Daemon")
    subparsers.add_parser("forge", help="Run Autonomous Repair")

    args = parser.parse_args()

    if args.command == "run":
        run_os()
    elif args.command == "doctor":
        run_doctor()
    elif args.command == "test":
        run_tests()
    elif args.command == "clean":
        clean_state()
    elif args.command == "chat":
        run_chat()
    elif args.command == "watch":
        run_watch()
    elif args.command == "dream":
        run_dream()
    elif args.command == "chronos":
        run_chronos(args.search)
    elif args.command == "sentinel":
        run_sentinel()
    elif args.command == "forge":
        run_forge()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
