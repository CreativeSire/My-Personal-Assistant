"""
Optional GitHub Copilot SDK bridge for Victor-OS.

Safe by default:
- If the SDK is missing, this module returns clear status/error messages.
- It does not alter core runtime behavior unless explicitly called.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from typing import Any

from config import get_config


class CopilotBridge:
    def __init__(self):
        self.cfg = get_config()
        self.cli_path = os.getenv("COPILOT_CLI_PATH", "copilot").strip() or "copilot"
        self.default_model = os.getenv("COPILOT_SDK_MODEL", "gpt-5").strip() or "gpt-5"
        self.timeout_sec = int(os.getenv("COPILOT_SDK_TIMEOUT_SEC", "35"))

    def status(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "sdk_installed": False,
            "cli_found": False,
            "cli_path": self.cli_path,
            "default_model": self.default_model,
            "error": "",
        }

        info["cli_found"] = bool(shutil.which(self.cli_path))

        try:
            from copilot import CopilotClient  # type: ignore  # noqa: F401

            info["sdk_installed"] = True
        except Exception as exc:
            info["error"] = f"python_sdk_missing: {exc}"
            return info

        return info

    async def _ask_async(self, prompt: str, model: str | None = None) -> str:
        from copilot import CopilotClient  # type: ignore

        final_chunks: list[str] = []
        done = asyncio.Event()

        client = CopilotClient(
            {
                "cli_path": self.cli_path,
                "log_level": "error",
                "auto_start": True,
                "auto_restart": True,
            }
        )
        await client.start()
        session = await client.create_session({"model": model or self.default_model})

        def on_event(event: Any) -> None:
            try:
                event_type = getattr(getattr(event, "type", None), "value", None) or getattr(event, "type", "")
                if event_type == "assistant.message":
                    content = getattr(getattr(event, "data", None), "content", None)
                    if isinstance(content, str) and content.strip():
                        final_chunks.append(content.strip())
                elif event_type == "session.idle":
                    done.set()
            except Exception:
                pass

        session.on(on_event)
        await session.send({"prompt": prompt})
        await asyncio.wait_for(done.wait(), timeout=float(self.timeout_sec))

        try:
            await session.destroy()
        finally:
            await client.stop()

        text = "\n".join(x for x in final_chunks if x).strip()
        return text or "Copilot session completed but returned no assistant message."

    def ask(self, prompt: str, model: str | None = None) -> str:
        if not prompt.strip():
            return "Prompt is required."
        stat = self.status()
        if not stat.get("sdk_installed"):
            return (
                "Copilot SDK is not installed. Install with:\n"
                "  pip install github-copilot-sdk\n"
                f"Details: {stat.get('error')}"
            )
        if not stat.get("cli_found"):
            return (
                "Copilot CLI was not found in PATH.\n"
                "Install and auth it first, then retry."
            )
        try:
            return asyncio.run(self._ask_async(prompt=prompt, model=model))
        except Exception as exc:
            return f"Copilot request failed: {exc}"


_bridge: CopilotBridge | None = None


def get_copilot_bridge() -> CopilotBridge:
    global _bridge
    if _bridge is None:
        _bridge = CopilotBridge()
    return _bridge

