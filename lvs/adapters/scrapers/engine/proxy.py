"""
Corporate proxy configuration — reads env vars, returns a Playwright proxy dict.

Supported environment variables (in priority order):

  Shell-script convention (run_address_standardization.sh):
    PROXY       host:port or full URL, e.g. "proxy:9119"
    PROXY_PASS  proxy password

  Legacy LVS convention:
    LVS_PROXY_SERVER  full proxy URL, e.g. "http://user:pass@proxy:9119"
    LVS_PROXY_USER    proxy username
    LVS_PROXY_PASS    proxy password

  Legacy standalone-scraper convention:
    PROXY_NID       proxy username (NID)
    PROXY_PASSWORD  proxy password

To ENABLE  — set PROXY (and optionally PROXY_NID / PROXY_PASS) before running.
To DISABLE — unset PROXY (or leave all variables empty).
To REMOVE  — delete this file and replace every `get_proxy_config()` call with `None`.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger(__name__)


def get_proxy_config() -> Optional[dict]:
    """Return a Playwright proxy dict, or None when no proxy is configured.

    The returned dict has the shape accepted by both:
      - playwright.async_api.Browser.new_context(proxy=...)
      - playwright.async_api.Playwright.request.new_context(proxy=...)

    Keys: "server" (required), "username" (optional), "password" (optional).
    """
    server = _resolve_server()
    if not server:
        return None

    username = (
        os.environ.get("PROXY_NID", "").strip()
        or os.environ.get("LVS_PROXY_USER", "").strip()
    )
    password = (
        os.environ.get("PROXY_PASS", "").strip()
        or os.environ.get("PROXY_PASSWORD", "").strip()
        or os.environ.get("LVS_PROXY_PASS", "").strip()
    )

    cfg: dict = {"server": server}
    if username:
        cfg["username"] = username
    if password:
        cfg["password"] = password

    log.debug("Proxy configured: server=%s user=%s", server, username or "(none)")
    return cfg


def is_proxy_configured() -> bool:
    """Return True when at least a proxy server is available in the environment."""
    return _resolve_server() is not None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_server() -> Optional[str]:
    """Return the proxy server URL, or None if not configured."""
    # Priority 1 — legacy LVS full URL (already includes credentials if needed)
    lvs = os.environ.get("LVS_PROXY_SERVER", "").strip()
    if lvs:
        return lvs

    # Priority 2 — shell-script PROXY variable ("proxy:9119" or full URL)
    raw = os.environ.get("PROXY", "").strip()
    if not raw:
        return None

    # Normalise: add http:// when the value is bare host:port
    if raw.startswith(("http://", "https://", "socks5://")):
        return raw
    return f"http://{raw}"
