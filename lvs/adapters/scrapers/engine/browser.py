"""Playwright browser pool — async context manager yielding a configured Page."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from .models import TransportConfig
from .proxy import get_proxy_config  # remove this import (and the call below) to disable proxy


_REAL_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Args that suppress automation signals detectable by bot-protection middleware
_STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
]


@asynccontextmanager
async def get_page(config: TransportConfig, headless_override: bool | None = None) -> AsyncGenerator[Page, None]:
    """Yield a Playwright Page configured per TransportConfig.

    Proxy credentials are read exclusively from environment variables:
      LVS_PROXY_SERVER, LVS_PROXY_USER, LVS_PROXY_PASS
    """
    headless = headless_override if headless_override is not None else config.headless

    # Proxy is applied whenever PROXY (or LVS_PROXY_SERVER) env vars are set,
    # unless the board's config sets transport.proxy.enabled: false (hard bypass).
    if config.proxy.enabled is False:
        proxy_cfg = None
        launch_args = [*_STEALTH_ARGS, "--no-proxy-server"]
    else:
        proxy_cfg = get_proxy_config()
        launch_args = _STEALTH_ARGS

    # Use a real browser UA unless config explicitly overrides it
    user_agent = config.user_agent
    if not user_agent or user_agent == "LVS-LicenseVerifier/1.0":
        user_agent = _REAL_UA

    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(
            headless=headless,
            args=launch_args,
        )
        ctx: BrowserContext = await browser.new_context(
            viewport=config.viewport,
            user_agent=user_agent,
            proxy=proxy_cfg,
            locale="en-US",
            timezone_id="America/New_York",
            java_script_enabled=True,
        )
        ctx.set_default_timeout(config.timeout_ms)
        ctx.set_default_navigation_timeout(config.navigation_timeout_ms)

        page: Page = await ctx.new_page()
        # Remove the webdriver property that headless detection checks
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        try:
            yield page
        finally:
            await ctx.close()
            await browser.close()
