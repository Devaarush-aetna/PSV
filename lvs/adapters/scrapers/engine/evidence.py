"""HTML and screenshot capture — stores evidence per run for debugging and AI fallback."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from playwright.async_api import Page

from .models import EvidenceConfig

log = logging.getLogger(__name__)


async def capture_evidence(
    page: Page,
    config: EvidenceConfig,
    stage: str,
    run_id: str,
) -> dict[str, str]:
    """Capture HTML + screenshot if stage is in config.capture_on. Returns paths dict."""
    if stage not in config.capture_on:
        return {}

    base = config.local_path.format(source_id="unknown", run_id=run_id)
    # Allow caller to override source_id after instantiation
    base_path = Path(base)
    base_path.mkdir(parents=True, exist_ok=True)

    paths: dict[str, str] = {}

    if config.capture_html:
        html_path = base_path / f"{stage}.html"
        try:
            html = await page.content()
            html_path.write_text(html, encoding="utf-8")
            paths["html_path"] = str(html_path)
            log.debug("Captured HTML: %s", html_path)
        except Exception as e:
            log.warning("HTML capture failed (%s): %s", stage, e)

    if config.capture_screenshot:
        shot_path = base_path / f"{stage}.png"
        try:
            await page.screenshot(path=str(shot_path), full_page=True)
            paths["screenshot_path"] = str(shot_path)
            log.debug("Captured screenshot: %s", shot_path)
        except Exception as e:
            log.warning("Screenshot capture failed (%s): %s", stage, e)

    return paths


def resolve_evidence_path(config: EvidenceConfig, source_id: str, run_id: str) -> str:
    """Return resolved local evidence directory path."""
    return config.local_path.format(source_id=source_id, run_id=run_id)
