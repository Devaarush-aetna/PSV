"""Socrata archetype scrapers: socrata_api and socrata_bulk_csv."""
from __future__ import annotations

import logging
import urllib.parse
from typing import Optional

from engine.evidence import capture_evidence
from engine.models import SearchQuery, SiteConfig
from engine.output import map_to_license_record, upsert_to_db
from engine.post_processors import apply_field_map
from engine.proxy import get_proxy_config
from ._shared import _emit_event


log = logging.getLogger(__name__)


def _socrata_field_for(config: SiteConfig, base_mode: str) -> Optional[str]:
    """Look up the SoQL column name from a base single-field mode's input_selector."""
    m = next((m for m in config.search.modes if m.mode == base_mode), None)
    return m.input_selector if m and m.input_selector else None


def _build_socrata_combo_url(config: SiteConfig, query: SearchQuery) -> str:
    """Build a SoQL URL with ANDed clauses for combo modes and orthogonal type filters."""
    clauses: list[str] = []
    base = config.identity.base_url.rstrip("?&")

    def _add_clause(base_mode: str, value: Optional[str], op: str = "like") -> None:
        if not value:
            return
        col = _socrata_field_for(config, base_mode)
        if not col:
            log.warning("Socrata: no SoQL column for base mode '%s'; skipping clause", base_mode)
            return
        safe = value.replace("'", "''")
        if op == "eq":
            clauses.append(f"upper({col}) = upper('{safe}')")
        else:
            clauses.append(f"upper({col}) like upper('%{safe}%')")

    # _add_clause("license_number", query.license_number or (query.query if query.mode.startswith("license") else None), op="eq")
    _lic_val = query.license_number or (query.query if query.mode.startswith("license") else None)
    _add_clause("license_number", _lic_val, op="eq" if (_lic_val and "." in _lic_val) else "like")
    _add_clause("first_name", query.first_name, op="like")
    _add_clause("last_name", query.last_name, op="like")
    if query.license_type and config.identity.license_type_selector:
        col = config.identity.license_type_selector
        safe = query.license_type.replace("'", "''")
        clauses.append(f"upper({col}) = upper('{safe}')")
    if query.provider_type and config.identity.provider_type_selector:
        col = config.identity.provider_type_selector
        safe = query.provider_type.replace("'", "''")
        clauses.append(f"upper({col}) = upper('{safe}')")

    if not clauses:
        return _build_socrata_url(config, query)

    where = " AND ".join(clauses)
    params = urllib.parse.urlencode({"$where": where, "$limit": "500"})
    return f"{base}?{params}"


def _build_socrata_url(config: SiteConfig, query: SearchQuery) -> str:
    """Build a Socrata SoQL query URL. input_selector stores the field name."""
    from engine.models import COMBO_MODES
    if query.mode in COMBO_MODES or query.license_type or query.provider_type:
        return _build_socrata_combo_url(config, query)

    mode_cfg = next((m for m in config.search.modes if m.mode == query.mode), None)
    field = mode_cfg.input_selector if mode_cfg and mode_cfg.input_selector else query.mode.replace("_", "")
    safe = query.query.replace("'", "''")
    base = config.identity.base_url.rstrip("?&")
    if query.mode == "license_number":
        params = urllib.parse.urlencode({field: query.query, "$limit": "500"})
    else:
        where = f"upper({field}) like upper('%{safe}%')"
        params = urllib.parse.urlencode({"$where": where, "$limit": "500"})
    return f"{base}?{params}"


async def scrape_socrata_api(
    config: SiteConfig, query: SearchQuery, db, t0: float, run_id: str,
) -> list:
    """Fetch records from a Socrata JSON API using Playwright's HTTP stack (proxy-aware)."""
    from playwright.async_api import async_playwright

    source_id = config.identity.source_id
    log.info("[%s] Socrata API run_id=%s  query=%s/%s", source_id, run_id, query.mode, query.query)

    proxy_cfg = get_proxy_config()

    try:
        url = _build_socrata_url(config, query)
        log.info("[%s] Fetching: %s", source_id, url)
        async with async_playwright() as pw:
            req_ctx = await pw.request.new_context(
                extra_http_headers={"Accept": "application/json"},
                proxy=proxy_cfg,
            )
            try:
                response = await req_ctx.get(url, timeout=30000)
                if not response.ok:
                    raise RuntimeError(f"HTTP {response.status}")
                raw_records = await response.json()
            finally:
                await req_ctx.dispose()
    except Exception as exc:
        log.error("[%s] Socrata fetch failed: %s", source_id, exc)
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, str(exc))
        return []

    if not isinstance(raw_records, list):
        log.error("[%s] Unexpected Socrata response: %s", source_id, str(raw_records)[:200])
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, "unexpected_format")
        return []

    log.info("[%s] Socrata returned %d record(s)", source_id, len(raw_records))
    records = []
    for raw in raw_records:
        mapped = apply_field_map(raw, config.detail.field_map)
        rec = map_to_license_record(mapped, config, {})
        records.append(rec)

    await _emit_event(db, run_id, source_id, "complete", "success", t0, len(records))
    if db and records:
        await upsert_to_db(db, records)
    return records


async def scrape_socrata_bulk_csv(
    config: SiteConfig, query: SearchQuery, db, t0: float, run_id: str,
) -> list:
    """Per-query Socrata JSON API via Playwright browser page.goto() (proxy-aware)."""
    import json as _json
    import re as _re
    from playwright.async_api import async_playwright

    source_id = config.identity.source_id
    log.info("[%s] Socrata-browser run_id=%s  query=%s/%s", source_id, run_id, query.mode, query.query)

    from engine.models import COMBO_MODES
    if query.mode in COMBO_MODES or query.license_type or query.provider_type:
        url = _build_socrata_combo_url(config, query)
    else:
        mode_cfg = next((m for m in config.search.modes if m.mode == query.mode), None)
        field = mode_cfg.input_selector if mode_cfg and mode_cfg.input_selector else query.mode
        q = query.query.strip()
        safe = q.replace("'", "''")
        base = config.identity.base_url.rstrip("?&")

        if query.mode in ("license_number", "credential_number"):
            if "." in q:
                params = urllib.parse.urlencode({field: q, "$limit": "500"})
            elif _re.match(r"^\d+$", q):
                where = f"upper({field}) like upper('%{safe}%')"
                params = urllib.parse.urlencode({"$where": where, "$limit": "500"})
            else:
                where = f"upper({field}) like upper('%{safe}%')"
                params = urllib.parse.urlencode({"$where": where, "$limit": "500"})
        else:
            where = f"upper({field}) like upper('%{safe}%')"
            params = urllib.parse.urlencode({"$where": where, "$limit": "500"})

        url = f"{base}?{params}"
    log.info("[%s] Fetching: %s", source_id, url)

    page = None
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                r = await page.goto(url, timeout=30000)
                if not r or not r.ok:
                    await capture_evidence(page, config.evidence, stage="error", run_id=run_id, source_id=source_id, state=config.identity.state, query=query)
                    raise RuntimeError(f"HTTP {r.status if r else 'no response'}")
                body = await page.inner_text("body")
                await capture_evidence(page, config.evidence, stage="search_results", run_id=run_id, source_id=source_id, state=config.identity.state, query=query)
                raw_records = _json.loads(body)
            finally:
                await browser.close()
    except Exception as exc:
        log.error("[%s] Socrata browser fetch failed: %s", source_id, exc)
        if page:
            try:
                await capture_evidence(page, config.evidence, stage="error", run_id=run_id, source_id=source_id, state=config.identity.state, query=query)
            except Exception:
                pass
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, str(exc))
        return []

    if not isinstance(raw_records, list):
        log.error("[%s] Unexpected Socrata response: %s", source_id, str(raw_records)[:200])
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, "unexpected_format")
        return []

    log.info("[%s] Socrata returned %d record(s)", source_id, len(raw_records))
    records = []
    for raw in raw_records:
        raw["_source_url"] = url
        mapped = apply_field_map(raw, config.detail.field_map)
        rec = map_to_license_record(mapped, config, {})
        records.append(rec)

    await _emit_event(db, run_id, source_id, "complete", "success", t0, len(records))
    if db and records:
        await upsert_to_db(db, records)
    return records
