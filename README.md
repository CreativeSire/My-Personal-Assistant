# Victor OS 2.0: The Sovereign AI Daemon

Victor is an autonomous AI agent designed to live in your operating system. He is not just a chatbot; he is a digital employee capable of executing tasks, managing files, and "seeing" your workflow.

## Quick Start

### 1. The Neural Interface (Terminal Chat)
Talk to Victor directly without latency or API limits.
```bash
python manage.py chat
```

### 2. Ghost Vision (Screen Memory)
Activate the background daemon to capture your screen workflow.
```bash
python manage.py watch
```

### 3. System Maintenance
Run health checks and clean temporary files.
```bash
python manage.py doctor
python manage.py clean
```

## Architecture

- **`manage.py`**: The universal entry point.
- **`victor_os/terminal_client.py`**: The Plan-Execute-Review loop (OpenClaw rival).
- **`victor_os/vision_daemon.py`**: The background vision service.
- **`archive/`**: Legacy scripts and artifacts from Victor 1.0.

## Configuration

Edit `victor_os/config.py` or `.env` to set your API keys.

## Safety

Victor includes a **Safety Kernel** that blocks destructive commands (like `rm -rf /`) by default.
