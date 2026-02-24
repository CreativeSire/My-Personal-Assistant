"""
Victor OS Desktop Launcher

Starts the Flask desktop server and optionally wraps it in pywebview with:
- system tray controls (pystray)
- Alt+Space global hotkey focus
- optional clipboard error detection prompt
"""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from config import get_config
from logging_config import get_logger, setup_logging

import desktop_server

try:
    import webview
except Exception:  # pragma: no cover
    webview = None

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover
    pystray = None
    Image = None
    ImageDraw = None

try:
    import keyboard
except Exception:  # pragma: no cover
    keyboard = None

try:
    import pyperclip
except Exception:  # pragma: no cover
    pyperclip = None

try:
    import requests
except Exception:  # pragma: no cover
    requests = None


cfg = get_config()
setup_logging(cfg.log_dir)
logger = get_logger("desktop_launcher")


@dataclass
class LauncherState:
    paused: bool = False
    stop_event: threading.Event = threading.Event()


def _run_flask_server() -> None:
    host = "127.0.0.1"
    port = int(os.getenv("VICTOR_DESKTOP_PORT", "7860"))
    logger.info(f"Desktop launcher starting server at http://{host}:{port}")
    desktop_server.app.run(host=host, port=port, debug=False, use_reloader=False)


def _default_icon():
    if Image is None or ImageDraw is None:
        return None
    img = Image.new("RGBA", (64, 64), (13, 17, 23, 255))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((8, 8, 56, 56), radius=10, outline=(46, 160, 67, 255), width=3)
    draw.text((24, 19), "V", fill=(230, 237, 243, 255))
    return img


def _set_pause_flag(paused: bool) -> None:
    if requests is None:
        return
    try:
        requests.post("http://127.0.0.1:7860/api/runtime_flags", json={"flags": {"paused": paused}}, timeout=2)
    except Exception:
        logger.warning("Failed to push runtime pause flag to desktop server.")


def _focus_chat_input() -> None:
    if webview is None:
        return
    try:
        for win in webview.windows:
            win.restore()
            win.show()
            win.bring_to_front()
            win.evaluate_js("window.focusChatInput && window.focusChatInput();")
    except Exception as exc:
        logger.warning(f"Unable to focus chat input via hotkey: {exc}")


def _register_hotkey() -> None:
    if keyboard is None:
        logger.info("Global hotkey disabled (keyboard package unavailable).")
        return
    try:
        keyboard.add_hotkey("alt+space", _focus_chat_input)
        logger.info("Registered global hotkey Alt+Space.")
    except Exception as exc:
        logger.warning(f"Failed to register Alt+Space hotkey: {exc}")


def _clipboard_monitor(stop_event: threading.Event) -> None:
    if pyperclip is None or requests is None:
        return
    last_value = ""
    error_pattern = re.compile(
        r"(traceback|exception|error code|stack trace|connectionreseterror|fatal)",
        re.IGNORECASE,
    )
    while not stop_event.is_set():
        try:
            settings_resp = requests.get("http://127.0.0.1:7860/api/settings/desktop", timeout=2)
            monitor_enabled = bool((settings_resp.json().get("settings") or {}).get("monitor_clipboard"))
            if not monitor_enabled:
                time.sleep(1.5)
                continue
            value = pyperclip.paste()
            if isinstance(value, str) and value and value != last_value and error_pattern.search(value):
                logger.info("Clipboard intelligence detected possible error content.")
                # Lightweight local notification path: append a log marker by toggling runtime flags timestamp.
                requests.post(
                    "http://127.0.0.1:7860/api/runtime_flags",
                    json={"flags": {"paused": False}},
                    timeout=2,
                )
            last_value = value
        except Exception:
            pass
        time.sleep(1.5)


def _run_tray(state: LauncherState):
    if pystray is None:
        logger.info("System tray unavailable (pystray not installed).")
        return

    def open_dashboard(icon=None, item=None):
        _focus_chat_input()

    def pause_agents(icon=None, item=None):
        state.paused = not state.paused
        _set_pause_flag(state.paused)
        logger.info(f"Tray toggled paused={state.paused}")

    def exit_app(icon=None, item=None):
        state.stop_event.set()
        if icon:
            icon.stop()
        try:
            if webview is not None:
                for win in webview.windows:
                    win.destroy()
        except Exception:
            pass

    menu = pystray.Menu(
        pystray.MenuItem("Open Dashboard", open_dashboard),
        pystray.MenuItem("Pause Agents", pause_agents),
        pystray.MenuItem("Exit", exit_app),
    )
    icon = pystray.Icon("victor_os", _default_icon(), "Victor OS v3.0", menu)
    icon.run()


def main() -> None:
    state = LauncherState()
    server_thread = threading.Thread(target=_run_flask_server, daemon=True)
    server_thread.start()

    # Wait for server to boot
    time.sleep(1.2)
    _register_hotkey()

    clip_thread = threading.Thread(target=_clipboard_monitor, args=(state.stop_event,), daemon=True)
    clip_thread.start()

    tray_thread = threading.Thread(target=_run_tray, args=(state,), daemon=True)
    tray_thread.start()

    if webview is None:
        logger.warning("pywebview unavailable. Use browser: http://127.0.0.1:7860")
        while not state.stop_event.is_set():
            time.sleep(0.5)
        return

    window = webview.create_window(
        "Victor OS v3.0 - Midnight Protocol",
        "http://127.0.0.1:7860",
        width=1440,
        height=900,
        min_size=(1100, 700),
    )
    webview.start(debug=False)


if __name__ == "__main__":
    main()

