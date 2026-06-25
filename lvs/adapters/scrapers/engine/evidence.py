"""HTML and screenshot capture — stores evidence per run under PSV_DEV/Evidence/.

All evidence lands at:
    PSV_DEV/Evidence/{YYYY-MM}/{state}/{source_id}/{YYYYMMDD_HHMM}_{query_label}/{stage}.html
    PSV_DEV/Evidence/{YYYY-MM}/{state}/{source_id}/{YYYYMMDD_HHMM}_{query_label}/{stage}.png

Folder layout:
  Evidence/
    2026-06/
      TX/
        TX_CHIRO/
          20260622_0918_Smith/
            search_results.html
            search_results.png
            error.html
            error.png
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from playwright.async_api import Page

from .models import EvidenceConfig

log = logging.getLogger(__name__)

# PSV_DEV/ — two levels up from engine/, three from the scrapers package root
# engine/evidence.py → engine/ → scrapers/ → adapters/ → lvs/ → PSV_DEV/
_PROJECT_ROOT = Path(__file__).resolve().parents[4]

_RUN_ID_TS = re.compile(r'^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})')
_FS_UNSAFE = re.compile(r'[\\/:*?"<>|\s]+')


def _ts_from_run_id(run_id: str) -> tuple[str, str]:
    """Return (month='YYYY-MM', ts='YYYYMMDD_HHMM') from a run_id like '20260622_091856'.
    Falls back to datetime.now() if run_id doesn't match."""
    m = _RUN_ID_TS.match(run_id or "")
    if m:
        yyyy, mo, dd, hh, mm = m.groups()
        return f"{yyyy}-{mo}", f"{yyyy}{mo}{dd}_{hh}{mm}"
    from datetime import datetime
    now = datetime.now()
    return now.strftime("%Y-%m"), now.strftime("%Y%m%d_%H%M")


def _query_label(query) -> str:
    """Return a short, filesystem-safe identifier from a SearchQuery."""
    val = ""
    if hasattr(query, "license_number") and query.license_number:
        val = query.license_number
    elif hasattr(query, "first_name") and query.first_name:
        val = query.first_name
    elif hasattr(query, "last_name") and query.last_name:
        val = query.last_name
    elif hasattr(query, "query") and query.query:
        val = query.query
    if not val:
        return ""
    return _FS_UNSAFE.sub("_", val.strip())[:40].strip("_")


def resolve_evidence_path(
    source_id: str,
    run_id: str,
    state: str = "",
    query_label: str = "",
) -> Path:
    """Return PSV_DEV/Evidence/{YYYY-MM}/{state}/{source_id}/{YYYYMMDD_HHMM}_{query_label}/"""
    month, ts = _ts_from_run_id(run_id)
    folder = f"{ts}_{query_label}" if query_label else ts
    return _PROJECT_ROOT / "Evidence" / month / (state or "UNK") / source_id / folder


async def capture_evidence(
    page: Page,
    config: EvidenceConfig,
    stage: str,
    run_id: str,
    source_id: str = "unknown",
    state: str = "",
    query=None,
) -> dict[str, str]:
    """Capture HTML + screenshot if stage is in config.capture_on. Returns paths dict."""
    if stage not in config.capture_on:
        return {}

    ql = _query_label(query) if query is not None else ""
    base_path = resolve_evidence_path(source_id, run_id, state=state, query_label=ql)
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


def write_evidence_summary(source_id: str, run_id: str, summary: dict, state: str = "") -> str:
    """Write a JSON evidence summary for non-browser archetypes (csv_bulk, pdf_bulk, etc.).

    Returns the path written.
    """
    import json
    base_path = resolve_evidence_path(source_id, run_id, state=state)
    base_path.mkdir(parents=True, exist_ok=True)
    out = base_path / "summary.json"
    out.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    log.debug("Wrote evidence summary: %s", out)
    return str(out)
