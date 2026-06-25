"""
Corporate proxy configuration — reads env vars and psv_config.yaml, returns a Playwright proxy dict.

Resolution order (first non-empty value wins):

  1. LVS_PROXY_SERVER env var  — full URL e.g. "http://user:pass@proxy:9119"
  2. PROXY env var             — host:port or full URL, e.g. "proxy:9119"
  3. psv_config.yaml           — proxy.server key in the engine directory

Credentials (username / password) are always read from env vars:
  PROXY_NID / PROXY_PASSWORD   — preferred
  LVS_PROXY_USER / LVS_PROXY_PASS  — legacy
  PROXY_PASS                   — legacy shell-script convention

Board-level overrides (via config.yaml transport.proxy.enabled):
  enabled: true   — board requires proxy; auto-resolved from above sources
  enabled: false  — proxy is force-disabled for this board (e.g. NH_OPLC Akamai WAF)
  (absent / null) — proxy used if configured, skipped if not

To DISABLE proxy for a full run — set PROXY="" in your shell before running.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# psv_config.yaml lives in the engine/ directory alongside proxy.py
_PSV_CONFIG = Path(__file__).parent / "psv_config.yaml"


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
    if raw:
        if raw.startswith(("http://", "https://", "socks5://")):
            return raw
        return f"http://{raw}"

    # Priority 3 — psv_config.yaml project-level default
    file_server = _load_psv_config_server()
    if file_server:
        return file_server

    return None


def _load_psv_config_server() -> Optional[str]:
    """Read proxy.server from psv_config.yaml in the engine directory."""
    if not _PSV_CONFIG.exists():
        return None
    try:
        import yaml  # pyyaml — already a dependency via board config loading
        with open(_PSV_CONFIG, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        raw = (data.get("proxy") or {}).get("server", "").strip()
        if not raw:
            return None
        if raw.startswith(("http://", "https://", "socks5://")):
            return raw
        return f"http://{raw}"
    except Exception as exc:
        log.debug("Could not load psv_config.yaml proxy.server: %s", exc)
        return None
