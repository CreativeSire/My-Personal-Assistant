"""
Victor-OS Sandbox Runner
=========================
Isolated code execution via Docker containers.
Falls back to subprocess with timeout if Docker is unavailable.

Security model:
  - No network access (--network none)
  - Read-only root filesystem
  - tmpfs at /tmp (256MB)
  - Per-session workspace volume (read-write at /workspace)
  - 256MB RAM limit, 0.5 CPU limit
  - 15-second hard timeout
  - Auto-prune containers after session or 5 minutes idle

Usage:
    from sandbox_runner import get_sandbox_runner
    result = get_sandbox_runner().run_python(code, session_id="telegram_12345")

The result is a SandboxResult with stdout, stderr, exit_code, timed_out.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config import get_config
from logging_config import get_logger

logger = get_logger("sandbox_runner")
_cfg = get_config()

_DOCKER_IMAGE = os.getenv("SANDBOX_DOCKER_IMAGE", "python:3.12-slim")
_TIMEOUT_SECONDS = int(os.getenv("SANDBOX_TIMEOUT_SEC", "15"))
_MEMORY_LIMIT = os.getenv("SANDBOX_MEMORY_LIMIT", "256m")
_CPU_LIMIT = float(os.getenv("SANDBOX_CPU_LIMIT", "0.5"))
_WORKSPACE_ROOT = os.path.join(_cfg.base_dir, "workspace", "sandbox_sessions")
_CONTAINER_PREFIX = "victor_sandbox_"
_IDLE_PRUNE_SECONDS = 300   # auto-remove containers idle > 5 minutes


@dataclass
class SandboxResult:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False
    backend: str = "subprocess"   # "docker" | "subprocess"
    execution_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def format(self) -> str:
        """Human-readable result for Telegram output."""
        parts = []
        if self.stdout.strip():
            parts.append(f"Output:\n```\n{self.stdout.strip()[:3000]}\n```")
        if self.stderr.strip():
            parts.append(f"Stderr:\n```\n{self.stderr.strip()[:1000]}\n```")
        if self.timed_out:
            parts.append(f"⚠️ Execution timed out after {_TIMEOUT_SECONDS}s.")
        if self.exit_code != 0 and not self.timed_out:
            parts.append(f"Exit code: {self.exit_code}")
        parts.append(f"_via {self.backend} sandbox_")
        return "\n\n".join(parts) if parts else "No output."


class SandboxRunner:
    """
    Runs Python code in an isolated environment.
    Tries Docker first; falls back to subprocess with timeout.
    """

    def __init__(self):
        self._docker_available = self._check_docker()
        self._active_containers: dict[str, dict] = {}   # container_name → {started_at}
        self._lock = threading.Lock()
        os.makedirs(_WORKSPACE_ROOT, exist_ok=True)
        if self._docker_available:
            logger.info(f"Sandbox: Docker available, image={_DOCKER_IMAGE}")
        else:
            logger.warning("Sandbox: Docker unavailable — using subprocess fallback (less isolated)")

    def _check_docker(self) -> bool:
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True, timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _get_workspace(self, session_id: str) -> Path:
        safe_id = "".join(c for c in session_id if c.isalnum() or c == "_")[:40]
        ws = Path(_WORKSPACE_ROOT) / safe_id
        ws.mkdir(parents=True, exist_ok=True)
        return ws

    def _container_name(self, session_id: str) -> str:
        safe_id = "".join(c for c in session_id if c.isalnum() or c == "_")[:30]
        return f"{_CONTAINER_PREFIX}{safe_id}"

    def run_python(
        self,
        code: str,
        session_id: str = "default",
        extra_packages: Optional[list[str]] = None,
    ) -> SandboxResult:
        """Execute Python code in an isolated sandbox. Returns SandboxResult."""
        start = time.time()
        if self._docker_available:
            result = self._run_docker(code, session_id, extra_packages or [])
        else:
            result = self._run_subprocess(code)
        result.execution_ms = int((time.time() - start) * 1000)
        return result

    def _run_docker(
        self,
        code: str,
        session_id: str,
        extra_packages: list[str],
    ) -> SandboxResult:
        workspace = self._get_workspace(session_id)
        # Write code to a temp file in the workspace
        code_file = workspace / f"run_{uuid.uuid4().hex[:8]}.py"
        code_file.write_text(code, encoding="utf-8")

        container_name = self._container_name(session_id)
        cmd = [
            "docker", "run",
            "--rm",
            "--name", container_name,
            "--network", "none",
            "--read-only",
            "--tmpfs", "/tmp:size=256m",
            f"--memory={_MEMORY_LIMIT}",
            f"--cpus={_CPU_LIMIT}",
            "--security-opt", "no-new-privileges",
            "-v", f"{workspace}:/workspace:rw",
            "-w", "/workspace",
            _DOCKER_IMAGE,
            "python", code_file.name,
        ]

        with self._lock:
            self._active_containers[container_name] = {"started_at": time.time()}

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS + 5,   # outer timeout > inner
            )
            timed_out = False
            if proc.returncode == 124:
                timed_out = True
            return SandboxResult(
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                exit_code=proc.returncode,
                timed_out=timed_out,
                backend="docker",
            )
        except subprocess.TimeoutExpired:
            # Force-kill the container
            try:
                subprocess.run(["docker", "kill", container_name], capture_output=True, timeout=5)
            except Exception:
                pass
            return SandboxResult(
                stdout="",
                stderr="Execution killed: timeout exceeded.",
                exit_code=1,
                timed_out=True,
                backend="docker",
            )
        except Exception as exc:
            logger.error(f"Docker sandbox error: {exc}")
            # Fall back to subprocess
            return self._run_subprocess(code)
        finally:
            with self._lock:
                self._active_containers.pop(container_name, None)
            # Clean up temp code file
            try:
                code_file.unlink(missing_ok=True)
            except Exception:
                pass

    def _run_subprocess(self, code: str) -> SandboxResult:
        """Fallback: run in a subprocess with timeout. Less isolated."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            tmp_path = f.name

        try:
            proc = subprocess.run(
                ["python", tmp_path],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
            )
            return SandboxResult(
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                exit_code=proc.returncode,
                timed_out=False,
                backend="subprocess",
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                stdout="",
                stderr="Execution timed out.",
                exit_code=1,
                timed_out=True,
                backend="subprocess",
            )
        except Exception as exc:
            return SandboxResult(
                stdout="",
                stderr=str(exc),
                exit_code=1,
                timed_out=False,
                backend="subprocess",
            )
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def prune_idle_containers(self) -> int:
        """Remove containers that have been running beyond idle threshold."""
        if not self._docker_available:
            return 0
        pruned = 0
        now = time.time()
        with self._lock:
            stale = [
                name for name, meta in self._active_containers.items()
                if now - meta.get("started_at", now) > _IDLE_PRUNE_SECONDS
            ]
        for name in stale:
            try:
                subprocess.run(["docker", "kill", name], capture_output=True, timeout=5)
                subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=5)
                pruned += 1
            except Exception:
                pass
            with self._lock:
                self._active_containers.pop(name, None)
        if pruned:
            logger.info(f"Sandbox: pruned {pruned} idle containers")
        return pruned


# Module-level singleton
_runner: SandboxRunner | None = None


def get_sandbox_runner() -> SandboxRunner:
    global _runner
    if _runner is None:
        _runner = SandboxRunner()
    return _runner
