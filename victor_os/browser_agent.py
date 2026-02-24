from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from logging_config import get_logger

logger = get_logger("browser_agent")


def _get_fernet():
    try:
        from cryptography.fernet import Fernet  # type: ignore
    except Exception:
        return None
    key = os.getenv("VICTOR_MEMORY_KEY", "").strip()
    if not key:
        return None
    try:
        return Fernet(key.encode("utf-8"))
    except Exception:
        return None


@dataclass
class CookieBundle:
    site: str
    cookies: list[dict[str, Any]]


class BrowserAgent:
    """
    Persistent browser sessions via Playwright.

    Cookie persistence is encrypted if VICTOR_MEMORY_KEY is available.
    This module is feature-flagged by BROWSER_AGENT_ENABLED.
    """

    def __init__(self, cookie_store_path: str):
        self.cookie_store_path = cookie_store_path
        self._sessions: dict[str, Any] = {}
        self._playwright = None
        self._browser = None
        os.makedirs(self.cookie_store_path, exist_ok=True)

    def start(self) -> None:
        if self._playwright is not None:
            return
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"playwright not installed: {exc}")
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)

    def stop(self) -> None:
        try:
            for ctx in self._sessions.values():
                try:
                    ctx.close()
                except Exception:
                    pass
        finally:
            self._sessions = {}
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._browser = None
        self._playwright = None

    def _cookie_file(self, site: str) -> str:
        safe = "".join([c for c in site.lower() if c.isalnum() or c in {"-", "_", "."}])[:80] or "site"
        return os.path.join(self.cookie_store_path, f"{safe}.cookies")

    def _load_cookies(self, site: str) -> list[dict[str, Any]]:
        path = self._cookie_file(site)
        if not os.path.exists(path):
            return []
        with open(path, "rb") as _fh:
            raw = _fh.read()
        f = _get_fernet()
        if f:
            try:
                raw = f.decrypt(raw)
            except Exception:
                pass
        try:
            payload = json.loads(raw.decode("utf-8"))
            return list(payload.get("cookies") or [])
        except Exception:
            return []

    def _save_cookies(self, site: str, cookies: list[dict[str, Any]]) -> None:
        path = self._cookie_file(site)
        payload = json.dumps({"site": site, "cookies": cookies}, ensure_ascii=False).encode("utf-8")
        f = _get_fernet()
        if f:
            payload = f.encrypt(payload)
        with open(path, "wb") as fp:
            fp.write(payload)

    def get_or_create_session(self, site: str):
        if site in self._sessions:
            return self._sessions[site]
        self.start()
        ctx = self._browser.new_context()  # type: ignore[union-attr]
        cookies = self._load_cookies(site)
        if cookies:
            try:
                ctx.add_cookies(cookies)
            except Exception:
                pass
        self._sessions[site] = ctx
        return ctx

    def save_session(self, site: str) -> None:
        ctx = self._sessions.get(site)
        if not ctx:
            return
        try:
            cookies = ctx.cookies()
            self._save_cookies(site, cookies)
            logger.info(f"Saved cookies for site={site} count={len(cookies)}")
        except Exception as exc:
            logger.warning(f"Cookie save failed site={site}: {exc}")

