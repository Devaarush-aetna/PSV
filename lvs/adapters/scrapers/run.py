"""
Universal scraper CLI — driven entirely by per-board config.yaml files.

Usage:
  python run.py --config sites/NV_MEDBOARD/config.yaml --mode license_number --query "12345"
  python run.py --config sites/NV_CHIRO/config.yaml   --mode last_name      --query "Smith" --headed
  python run.py --config sites/MA_HEALTH/config.yaml  --mode name            --query "Smith" --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add parent to path so engine imports work when run from this directory
sys.path.insert(0, str(Path(__file__).parent))

from engine.ai_fallback import extract_with_ai, should_use_ai_fallback
from engine.browser import get_page
from engine.evidence import capture_evidence, resolve_evidence_path
from engine.extractor import extract_ag_grid, extract_detail, extract_results_table
from engine.models import SearchQuery, SiteConfig, TelemetryEvent
from engine.navigator import fill_search_form, navigate_to_search, wait_for_results
from engine.output import map_to_license_record, upsert_to_db, write_output
from engine.pagination import paginate
from engine.post_processors import apply_field_map
from engine.proxy import get_proxy_config
from engine.retry import with_retry
from engine.telemetry import init_db, log_scrape_event
from engine.validate import load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("run")


# ---------------------------------------------------------------------------
# Detail-page click → extract → back
# ---------------------------------------------------------------------------

async def _scrape_one_detail(page, config: SiteConfig, run_id: str, db) -> dict:
    evidence = await capture_evidence(
        page, config.evidence, stage="detail_page", run_id=run_id,
    )
    raw = await extract_detail(page, config.detail)

    # AI fallback if rule-based extraction is sparse
    if should_use_ai_fallback(raw):
        html = await page.content()
        ai_data = await extract_with_ai(
            html=html,
            field_map=config.detail.field_map,
            source_id=config.identity.source_id,
            run_id=run_id,
            db=db,
        )
        raw.update(ai_data)

    raw.update(evidence)
    return raw


async def _navigate_back(page, config: SiteConfig) -> None:
    nav = config.detail.back_navigation
    if nav.strategy == "browser_back":
        await page.go_back()
    elif nav.strategy == "breadcrumb_click" and nav.selector:
        try:
            # Iterate matches and click the first VISIBLE one (inline detail close buttons
            # exist in DOM for every row but only the expanded row's button is visible).
            close_btn = page.locator(nav.selector)
            count = await close_btn.count()
            clicked = False
            for i in range(count):
                btn = close_btn.nth(i)
                try:
                    if await btn.is_visible():
                        await btn.click(timeout=5000)
                        clicked = True
                        break
                except Exception:
                    continue
            if clicked:
                log.info("breadcrumb_click: clicked '%s'", nav.selector)
            elif count > 0:
                log.warning("breadcrumb_click: no visible instance of '%s' found — skipping back", nav.selector)
            else:
                log.warning("breadcrumb_click: selector '%s' not found — skipping back", nav.selector)
        except Exception as e:
            log.warning("breadcrumb_click failed: %s", e)
    elif nav.strategy == "url_navigate" and nav.url_fragment:
        base = config.identity.base_url
        target = base.rstrip("/").rsplit("/", 1)[0] + "/" + nav.url_fragment.lstrip("/")
        await page.goto(target)
    else:
        await page.go_back()

    if nav.wait_after_ms > 0:
        await asyncio.sleep(nav.wait_after_ms / 1000.0)


# ---------------------------------------------------------------------------
# Socrata Open Data API boards (e.g. Delaware)
# ---------------------------------------------------------------------------

def _socrata_field_for(config: SiteConfig, base_mode: str) -> Optional[str]:
    """Look up the SoQL column name from a base single-field mode's input_selector."""
    m = next((m for m in config.search.modes if m.mode == base_mode), None)
    return m.input_selector if m and m.input_selector else None


def _build_socrata_combo_url(config: SiteConfig, query: SearchQuery) -> str:
    """Build a SoQL URL with ANDed clauses for combo modes and orthogonal type filters.

    Reads each constituent base mode's input_selector to discover its SoQL column name
    (e.g. license_number → 'lic_no', last_name → 'lname'). license_type / provider_type
    columns come from SiteIdentity.license_type_selector / provider_type_selector when
    those carry a SoQL column name (per-board YAML responsibility).
    """
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

    _add_clause("license_number", query.license_number or (query.query if query.mode.startswith("license") else None), op="eq")
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
        # Fall back to a global LIKE on the configured mode field.
        return _build_socrata_url(config, query)

    where = " AND ".join(clauses)
    params = urllib.parse.urlencode({"$where": where, "$limit": "500"})
    return f"{base}?{params}"


def _build_socrata_url(config: SiteConfig, query: SearchQuery) -> str:
    """Build a Socrata SoQL query URL.  input_selector stores the field name."""
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


async def _scrape_socrata_api(
    config: SiteConfig, query: SearchQuery, db, t0: float, run_id: str,
) -> list:
    """Fetch records from a Socrata JSON API using Playwright's HTTP stack (proxy-aware)."""
    from playwright.async_api import async_playwright

    source_id = config.identity.source_id
    log.info("[%s] Socrata API run_id=%s  query=%s/%s", source_id, run_id, query.mode, query.query)

    from engine.proxy import get_proxy_config as _get_proxy  # remove to disable proxy
    proxy_cfg = _get_proxy()

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


# ---------------------------------------------------------------------------
# Socrata bulk-CSV boards (e.g. Washington DOH credential data)
# ---------------------------------------------------------------------------

async def _scrape_socrata_bulk_csv(
    config: SiteConfig, query: SearchQuery, db, t0: float, run_id: str,
) -> list:
    """Per-query Socrata JSON API via Playwright browser page.goto().

    Used when Playwright's APIRequestContext is blocked by corporate SSL inspection
    (ECONNRESET) — the Chromium browser uses the OS certificate store and handles
    Zscaler/Netskope interception transparently.

    Credential-number matching (mirrors original washington.py logic):
      - Contains '.'  → exact equality: ?field=VALUE
      - Digits only   → LIKE '%DIGITS%' in SoQL $where
      - Otherwise     → case-insensitive LIKE substring
    Name modes: case-insensitive LIKE.
    """
    import json as _json
    import re as _re
    from playwright.async_api import async_playwright

    source_id = config.identity.source_id
    log.info("[%s] Socrata-browser run_id=%s  query=%s/%s", source_id, run_id, query.mode, query.query)

    from engine.models import COMBO_MODES
    if query.mode in COMBO_MODES or query.license_type or query.provider_type:
        # Combo path delegates to the shared SoQL builder (which AND-combines clauses).
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
            else:
                digits = _re.sub(r"\D", "", q)
                pattern = digits if digits else safe
                where = f"upper({field}) like upper('%{pattern}%')"
                params = urllib.parse.urlencode({"$where": where, "$limit": "500"})
        else:
            where = f"upper({field}) like upper('%{safe}%')"
            params = urllib.parse.urlencode({"$where": where, "$limit": "500"})

        url = f"{base}?{params}"
    log.info("[%s] Fetching: %s", source_id, url)

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                r = await page.goto(url, timeout=30000)
                if not r or not r.ok:
                    raise RuntimeError(f"HTTP {r.status if r else 'no response'}")
                body = await page.inner_text("body")
                raw_records = _json.loads(body)
            finally:
                await browser.close()
    except Exception as exc:
        log.error("[%s] Socrata browser fetch failed: %s", source_id, exc)
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


async def _scrape_csv_bulk(
    config: SiteConfig, query: SearchQuery, db, t0: float, run_id: str,
) -> list:
    """Download CSV roster (with caching), search in-memory, map to LicenseRecords.

    Supports two download strategies (link_text, post_form) — see csv_extractor.py.
    Column routing is controlled by csv_bulk.search_columns in config.yaml.
    """
    from engine.csv_extractor import (
        get_csv, load_csv, search_by_license_number, search_by_name, search_by_multi_column,
    )
    from engine.models import COMBO_MODES

    source_id = config.identity.source_id
    csv_cfg = config.csv_bulk
    if not csv_cfg:
        log.error("[%s] csv_bulk archetype requires a csv_bulk section in config", source_id)
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, "no_csv_bulk_config")
        return []

    log.info("[%s] CSV bulk run_id=%s  query=%s/%s", source_id, run_id, query.mode, query.query)

    is_combo = query.mode in COMBO_MODES
    has_type_filter = bool(query.license_type or query.provider_type)
    search_col_or_list = csv_cfg.search_columns.get(query.mode)

    if not is_combo and not search_col_or_list:
        log.error("[%s] No search_column configured for mode '%s'", source_id, query.mode)
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, f"no_search_column:{query.mode}")
        return []

    try:
        csv_path = await get_csv(config.identity.base_url, source_id, csv_cfg)
        df = load_csv(csv_path, csv_cfg.encoding, csv_cfg.header_row)
    except Exception as exc:
        log.error("[%s] CSV load failed: %s", source_id, exc)
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, str(exc))
        return []

    # Build a logical-field → CSV-column map by reading the single-field mode entries.
    # This lets combo modes find the right columns even when the YAML only declares
    # the primary single-field entries.
    col_map = {
        "license_number": csv_cfg.search_columns.get("license_number") if isinstance(csv_cfg.search_columns.get("license_number"), str) else None,
        "first_name": csv_cfg.search_columns.get("first_name") if isinstance(csv_cfg.search_columns.get("first_name"), str) else None,
        "last_name": csv_cfg.search_columns.get("last_name") if isinstance(csv_cfg.search_columns.get("last_name"), str) else None,
        "license_type": csv_cfg.license_type_column,
        "provider_type": csv_cfg.provider_type_column,
    }

    if is_combo or has_type_filter:
        # Multi-column AND-filter path. Works for both pure combos and single-mode
        # searches that also have a license_type / provider_type modifier.
        # For single-mode + type filter, fall through to multi-column with only
        # that one field + the type filter populated.
        if is_combo:
            raw_results = search_by_multi_column(
                df, col_map,
                license_number=query.license_number or (query.query if query.mode.startswith("license") else None),
                first_name=query.first_name,
                last_name=query.last_name,
                license_type=query.license_type,
                provider_type=query.provider_type,
            )
        else:
            # Single-mode + type filter: route the query value to its mode's logical field.
            field_for_mode = {
                "license_number": "license_number",
                "first_name": "first_name",
                "last_name": "last_name",
            }.get(query.mode)
            kwargs = {"license_type": query.license_type, "provider_type": query.provider_type}
            if field_for_mode:
                kwargs[field_for_mode] = query.query
            raw_results = search_by_multi_column(df, col_map, **kwargs)
    elif query.mode == "license_number":
        raw_results = search_by_license_number(df, search_col_or_list, query.query)
    else:
        raw_results = search_by_name(df, search_col_or_list, query.query)

    log.info("[%s] CSV search returned %d record(s)", source_id, len(raw_results))

    records = []
    for raw in raw_results:
        raw["_source_url"] = config.identity.base_url
        mapped = apply_field_map(raw, config.detail.field_map)
        rec = map_to_license_record(mapped, config, {})
        records.append(rec)

    await _emit_event(db, run_id, source_id, "complete", "success", t0, len(records))
    if db and records:
        await upsert_to_db(db, records)
    return records


async def _scrape_pdf_bulk(
    config: SiteConfig, query: SearchQuery, db, t0: float, run_id: str,
) -> list:
    """Download PDF roster(s), extract tables, search in-memory.

    Routing by license_number prefix:
      - license starts with "E" → estab PDF
      - license starts with "L" or "LA" → prof PDF
      - No prefix match → search all PDFs in order
    Name searches (last_name / first_name) scan all PDFs.
    """
    from engine.pdf_extractor import (
        download_pdf,
        extract_table_data,
        search_all_by_last_name,
        search_by_combination,
        search_by_license_number,
        search_by_name,
    )
    from engine.models import COMBO_MODES

    source_id = config.identity.source_id
    pdf_cfg = config.pdf_bulk
    if not pdf_cfg or not pdf_cfg.pdfs:
        log.error("[%s] pdf_bulk archetype requires pdf_bulk.pdfs list in config", source_id)
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, "no_pdfs_configured")
        return []

    cache_dir = pdf_cfg.cache_dir.replace("{source_id}", source_id)
    q = query.query.strip()

    # Load all configured PDFs, routing by license prefix if applicable
    all_pdf_data: list[tuple[list, str]] = []
    for entry in pdf_cfg.pdfs:
        if query.mode == "license_number" and entry.license_prefix:
            if not q.upper().startswith(entry.license_prefix.upper()):
                continue
        try:
            pdf_path = download_pdf(entry.url, cache_dir, pdf_cfg.cache_days)
            records, fmt = extract_table_data(pdf_path)
            all_pdf_data.append((records, entry.format if entry.format != "default" else fmt))
        except Exception as exc:
            log.warning("[%s] Failed to load PDF %s: %s", source_id, entry.url, exc)

    # If prefix routing excluded everything, fall back to all PDFs
    if not all_pdf_data:
        for entry in pdf_cfg.pdfs:
            try:
                pdf_path = download_pdf(entry.url, cache_dir, pdf_cfg.cache_days)
                records, fmt = extract_table_data(pdf_path)
                all_pdf_data.append((records, entry.format if entry.format != "default" else fmt))
            except Exception as exc:
                log.warning("[%s] PDF fallback load failed: %s", source_id, exc)

    if not all_pdf_data:
        log.error("[%s] No PDFs could be loaded", source_id)
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, "pdf_load_failed")
        return []

    raw_results: list[dict] = []

    is_combo = query.mode in COMBO_MODES
    has_type_filter = bool(query.license_type or query.provider_type)

    if is_combo or has_type_filter:
        # AND-filter across PDFs by any combination of populated fields.
        for records, fmt in all_pdf_data:
            raw_results.extend(search_by_combination(
                records, fmt,
                license_number=query.license_number or (q if query.mode.startswith("license") else None),
                first_name=query.first_name,
                last_name=query.last_name,
                license_type=query.license_type,
                provider_type=query.provider_type,
            ))
    elif query.mode == "license_number":
        for records, fmt in all_pdf_data:
            found = search_by_license_number(q, records, fmt)
            if found:
                raw_results.append(found)
                break
    elif query.mode == "last_name":
        for records, fmt in all_pdf_data:
            raw_results.extend(search_all_by_last_name(q, records, fmt))
    elif query.mode in ("first_name", "full_name"):
        parts = q.split(None, 1)
        fn, ln = (parts[0], parts[1]) if len(parts) == 2 else (q, "")
        for records, fmt in all_pdf_data:
            found = search_by_name(fn, ln, records, fmt)
            if found:
                raw_results.append(found)

    log.info("[%s] PDF search returned %d record(s)", source_id, len(raw_results))

    # Map normalized PDF fields → LicenseRecord via detail.field_map
    result_records = []
    for raw in raw_results:
        # Build a unified field dict using the detail field_map
        mapped = apply_field_map(raw, config.detail.field_map)
        rec = map_to_license_record(mapped, config, {})
        result_records.append(rec)

    await _emit_event(db, run_id, source_id, "complete", "success", t0, len(result_records))
    if db and result_records:
        await upsert_to_db(db, result_records)
    return result_records


# ---------------------------------------------------------------------------
# Certemy Angular SPA scraper  (archetype: certemy)
# ---------------------------------------------------------------------------

async def _scrape_certemy(
    config: SiteConfig, query: SearchQuery, db, t0: float, run_id: str,
) -> list:
    """Certemy Angular SPA: live-filter input, HTML table, Material paginator.

    All Certemy boards share the same Angular structure:
      - input.search-input  →  live-filter (no submit button)
      - table thead tr th   →  column headers (discovered at runtime)
      - table tbody tr / td →  data rows
      - .mat-paginator-navigation-next / [aria-label='Next page']  →  pagination

    Keys discovered from <thead> are mapped to canonical fields via detail.field_map.
    """
    from playwright.async_api import async_playwright

    source_id = config.identity.source_id
    log.info("[%s] Certemy run_id=%s  query=%s/%s", source_id, run_id, query.mode, query.query)

    all_raw: list[dict] = []
    headers: list[str] = []

    try:
        proxy_cfg = get_proxy_config()
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                ctx = await browser.new_context(
                    viewport={"width": 1440, "height": 900},
                    proxy=proxy_cfg,
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                )
                page = await ctx.new_page()
                page.set_default_timeout(30_000)

                await page.goto(config.identity.base_url, wait_until="domcontentloaded", timeout=30_000)

                try:
                    await page.wait_for_selector("input.search-input", state="visible", timeout=20_000)
                except Exception:
                    log.warning("[%s] input.search-input not visible — Angular may still loading", source_id)

                await asyncio.sleep(1.5)

                if query.query:
                    inp = page.locator("input.search-input").first
                    await inp.click()
                    await inp.click(click_count=3)
                    await inp.fill("")
                    for ch in query.query:
                        await page.keyboard.type(ch)
                        await asyncio.sleep(0.08)
                    log.info("[%s] Typed query %r into input.search-input", source_id, query.query)

                    # Wait for Angular filter to stabilize (row count unchanged for 2 ticks)
                    prev_n, stable = -1, 0
                    for _ in range(50):
                        await asyncio.sleep(0.4)
                        try:
                            n = await page.locator("table tbody tr").count()
                        except Exception:
                            n = -1
                        if n == prev_n and n >= 0:
                            stable += 1
                            if stable >= 2:
                                break
                        else:
                            stable = 0
                        prev_n = n
                else:
                    await page.wait_for_selector("table tbody tr", state="visible", timeout=20_000)
                    await asyncio.sleep(1.0)

                # Discover column headers
                headers = await page.evaluate(
                    "() => [...document.querySelectorAll('table thead tr th')].map(th => th.textContent.trim())"
                )
                log.info("[%s] Certemy columns: %s", source_id, headers)

                # Paginate and collect
                page_num = 0
                seen_hashes: set[str] = set()
                while True:
                    page_num += 1
                    rows: list[list[str]] = await page.evaluate(
                        "() => [...document.querySelectorAll('table tbody tr')].map(tr => "
                        "  [...tr.querySelectorAll('td')].map(td => td.textContent.trim()))"
                    )
                    for cells in rows:
                        raw: dict = {
                            headers[i]: cells[i]
                            for i in range(min(len(headers), len(cells)))
                        }
                        raw["_source_url"] = config.identity.base_url
                        key = "|".join(str(v) for v in raw.values())
                        if key not in seen_hashes:
                            seen_hashes.add(key)
                            all_raw.append(raw)

                    # Try next page via Material paginator
                    advanced = False
                    for sel in (
                        "[aria-label='Next page']",
                        "[aria-label='next page']",
                        ".mat-paginator-navigation-next",
                        "button:has-text('Next')",
                    ):
                        try:
                            btn = page.locator(sel).first
                            if await btn.is_visible(timeout=800) and not await btn.is_disabled():
                                await btn.click()
                                await page.wait_for_load_state("networkidle", timeout=15_000)
                                await asyncio.sleep(1.2)
                                advanced = True
                                break
                        except Exception:
                            pass
                    if not advanced:
                        break

            finally:
                await browser.close()

    except Exception as exc:
        log.error("[%s] Certemy scrape failed: %s", source_id, exc)
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, str(exc))
        return []

    log.info("[%s] Certemy raw rows: %d", source_id, len(all_raw))
    records = []
    for raw in all_raw:
        mapped = apply_field_map(raw, config.detail.field_map)
        rec = map_to_license_record(mapped, config, {})
        records.append(rec)

    await _emit_event(db, run_id, source_id, "complete", "success", t0, len(records))
    if db and records:
        await upsert_to_db(db, records)
    return records


# ---------------------------------------------------------------------------
# JSON API archetype (e.g. MA_MDDO findmydoctor.mass.gov)
# ---------------------------------------------------------------------------

async def _scrape_json_api(
    config: SiteConfig, query: SearchQuery, db, t0: float, run_id: str,
) -> list:
    """POST/GET a JSON request and parse a list of provider records.

    Body templates substitute {q} for the query string. Records are reached via
    json_api.records_path (dot path). Each record passes through detail.field_map
    so canonical field names line up with output.
    """
    from playwright.async_api import async_playwright

    source_id = config.identity.source_id
    api_cfg = config.json_api
    if not api_cfg:
        log.error("[%s] json_api archetype requires json_api section", source_id)
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, "no_json_api_config")
        return []

    log.info("[%s] JSON API run_id=%s  query=%s/%s", source_id, run_id, query.mode, query.query)

    proxy_cfg = get_proxy_config()

    def _sub_str(s: str, sq: SearchQuery) -> str:
        if sq.first_name is not None or sq.last_name is not None:
            first = sq.first_name or ""
            last = sq.last_name or ""
        else:
            parts = sq.query.rsplit(" ", 1)
            first = parts[0] if len(parts) > 1 else ""
            last = parts[-1]
        license_val = sq.license_number if sq.license_number is not None else sq.query
        return (
            s.replace("{q}", sq.query)
            .replace("{first}", first)
            .replace("{last}", last)
            .replace("{license}", license_val or "")
            .replace("{type}", sq.license_type or "")
            .replace("{provider}", sq.provider_type or "")
        )

    def _sub(obj, sq: SearchQuery):
        if isinstance(obj, dict):
            return {k: _sub(v, sq) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_sub(v, sq) for v in obj]
        if isinstance(obj, str):
            return _sub_str(obj, sq)
        return obj

    body_template = api_cfg.bodies.get(query.mode) or {}
    param_template = api_cfg.params.get(query.mode) or {}

    # Default Origin/Referer headers from base_url so corporate proxies / WAFs
    # accept the API call as if it came from the public form.
    base_origin = config.identity.base_url.rstrip("/")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": base_origin,
        "Referer": config.identity.base_url,
        "User-Agent": config.transport.user_agent,
    }
    headers.update(api_cfg.headers)

    payload = None
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                ctx = await browser.new_context(
                    proxy=proxy_cfg,
                    user_agent=config.transport.user_agent,
                    ignore_https_errors=True,
                )
                page = await ctx.new_page()

                if api_cfg.mode == "intercept":
                    # Drive the public form so the SPA fires its own XHR; capture the response.
                    # Match POST/GET response whose URL CONTAINS pattern AND ENDS with the
                    # endpoint_url path, so /search/get-metadata doesn't shadow /search.
                    pattern = api_cfg.intercept_url_pattern or api_cfg.endpoint_url
                    endpoint_path = api_cfg.endpoint_url.split("?", 1)[0].rstrip("/")
                    captured = []

                    def _on_resp(resp):
                        url = resp.url.split("?", 1)[0].rstrip("/")
                        if pattern in resp.url and (url == endpoint_path or url.endswith(endpoint_path)):
                            captured.append(resp)
                            log.info("[%s] captured response: %s", source_id, resp.url)

                    page.on("response", _on_resp)
                    await page.goto(config.identity.base_url, wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(3)

                    form = api_cfg.intercept_form
                    if form:
                        for sel in form.pre_clicks.get(query.mode, []):
                            try:
                                await page.locator(sel).first.click()
                                await asyncio.sleep(0.4)
                            except Exception as e:
                                log.warning("[%s] pre_click '%s' failed: %s", source_id, sel, e)
                        for sel, val_tpl in form.fills.get(query.mode, {}).items():
                            v = _sub_str(val_tpl, query)
                            try:
                                loc = page.locator(sel).first
                                await loc.clear()
                                await loc.fill(v)
                            except Exception as e:
                                log.warning("[%s] fill '%s' failed: %s", source_id, sel, e)
                        if form.submit_selector:
                            try:
                                await page.locator(form.submit_selector).first.click()
                            except Exception as e:
                                log.warning("[%s] submit '%s' failed: %s", source_id, form.submit_selector, e)
                        if form.submit_via_enter:
                            await page.keyboard.press("Enter")

                    # Wait for the response (poll for up to timeout_ms)
                    deadline = time.time() + (api_cfg.timeout_ms / 1000.0)
                    while time.time() < deadline and not captured:
                        await asyncio.sleep(0.4)

                    if captured:
                        for resp in captured[::-1]:
                            try:
                                payload = await resp.json()
                                break
                            except Exception:
                                continue

                    if payload is None:
                        raise RuntimeError(f"no JSON response captured from pattern '{pattern}'")
                else:
                    # direct mode: warm cookies then page.evaluate(fetch)
                    try:
                        await page.goto(config.identity.base_url, wait_until="domcontentloaded", timeout=20000)
                        await asyncio.sleep(1.5)
                    except Exception:
                        pass
                    if api_cfg.method == "POST":
                        body = _sub(body_template, query)
                        log.info("[%s] POST %s body=%s", source_id, api_cfg.endpoint_url, body)
                        payload = await page.evaluate(
                            """async ({url, body, headers}) => {
                                const r = await fetch(url, {
                                    method: 'POST', headers, body: JSON.stringify(body),
                                    credentials: 'include',
                                });
                                if (!r.ok) throw new Error('HTTP ' + r.status);
                                return await r.json();
                            }""",
                            {"url": api_cfg.endpoint_url, "body": body, "headers": headers},
                        )
                    else:
                        params = _sub(param_template, query)
                        log.info("[%s] GET %s params=%s", source_id, api_cfg.endpoint_url, params)
                        payload = await page.evaluate(
                            """async ({url, params, headers}) => {
                                const u = new URL(url);
                                for (const [k, v] of Object.entries(params || {})) u.searchParams.set(k, v);
                                const r = await fetch(u.toString(), {method: 'GET', headers, credentials: 'include'});
                                if (!r.ok) throw new Error('HTTP ' + r.status);
                                return await r.json();
                            }""",
                            {"url": api_cfg.endpoint_url, "params": params, "headers": headers},
                        )
            finally:
                await browser.close()
    except Exception as exc:
        log.error("[%s] JSON API fetch failed: %s", source_id, exc)
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, str(exc))
        return []

    # Drill into records_path (e.g. "results" or "data.items")
    cursor = payload
    if api_cfg.records_path:
        for key in api_cfg.records_path.split("."):
            if isinstance(cursor, dict):
                cursor = cursor.get(key)
            else:
                cursor = None
                break
    raw_records = cursor if isinstance(cursor, list) else []

    log.info("[%s] JSON API returned %d record(s)", source_id, len(raw_records))
    records = []
    for raw in raw_records:
        if not isinstance(raw, dict):
            continue
        raw["_source_url"] = api_cfg.endpoint_url
        mapped = apply_field_map(raw, config.detail.field_map)
        rec = map_to_license_record(mapped, config, {})
        records.append(rec)

    await _emit_event(db, run_id, source_id, "complete", "success", t0, len(records))
    if db and records:
        await upsert_to_db(db, records)
    return records


# ---------------------------------------------------------------------------
# DataTables JS API archetype (e.g. OK_DENTAL, TX_DENTAL)
# ---------------------------------------------------------------------------

async def _scrape_datatables_jsapi(
    config: SiteConfig, query: SearchQuery, db, t0: float, run_id: str,
) -> list:
    """Drive a DataTables grid via its JS API.

    For each `sub_page_urls` (or just base_url if empty):
      1. goto url and wait for table.dataTable
      2. Run column-search (or global search) via window.jQuery
      3. After settle, read tbody rows using ResultsTableConfig.columns
    Rows are concatenated across all sub-pages.
    """
    from playwright.async_api import async_playwright

    source_id = config.identity.source_id
    dt_cfg = config.datatables
    if not dt_cfg:
        log.error("[%s] datatables_jsapi archetype requires datatables section", source_id)
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, "no_datatables_config")
        return []

    log.info("[%s] DataTables run_id=%s  query=%s/%s", source_id, run_id, query.mode, query.query)

    from engine.models import COMBO_MODES
    urls = dt_cfg.sub_page_urls or [config.identity.base_url]
    col_idx_raw = dt_cfg.column_index.get(query.mode, -1)

    # Normalize column_index to a list of (col_idx, value) pairs to drive sequentially.
    # Scalar int + scalar query → one pair (legacy behavior).
    # list[int] for combo modes → one pair per index, value from the matching field
    #   in canonical order [license_number, first_name, last_name].
    is_combo = query.mode in COMBO_MODES
    drive_pairs: list[tuple[int, str]] = []
    if isinstance(col_idx_raw, list):
        # Combo: pair each index with the corresponding canonical field value.
        canonical = [query.license_number, query.first_name, query.last_name]
        # Filter out empty positions and align indices with their populated values.
        values = [v for v in canonical if v]
        for idx, val in zip(col_idx_raw, values):
            drive_pairs.append((idx, val))
    elif is_combo:
        # Combo mode but no list configured — fall back to the global filter with
        # the auto-joined query string. Best-effort.
        drive_pairs.append((-1, query.query))
    else:
        drive_pairs.append((col_idx_raw, query.query))

    all_records: list = []
    proxy_cfg = get_proxy_config()
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                ctx = await browser.new_context(
                    viewport={"width": 1440, "height": 900},
                    proxy=proxy_cfg,
                    user_agent=config.transport.user_agent,
                    ignore_https_errors=True,
                )
                page = await ctx.new_page()
                page.set_default_timeout(config.transport.timeout_ms)

                for url in urls:
                    try:
                        log.info("[%s] DataTables sub-page: %s", source_id, url)
                        await page.goto(url, wait_until="domcontentloaded")
                        try:
                            await page.wait_for_selector(dt_cfg.table_selector, timeout=20_000)
                        except Exception:
                            log.warning("[%s] table %s not found on %s", source_id, dt_cfg.table_selector, url)
                            continue

                        # Wait for the unfiltered table to populate before applying a search.
                        # DataTables creates the <table> immediately; rows arrive via AJAX.
                        try:
                            row_sel = (
                                config.results.table.row_selector
                                if config.results.table else f"{dt_cfg.table_selector} tbody tr"
                            )
                            await page.wait_for_function(
                                f"() => document.querySelectorAll({row_sel!r}).length > 0",
                                timeout=20_000,
                            )
                        except Exception:
                            log.warning("[%s] table never populated rows on %s", source_id, url)
                            continue

                        # Drive the DataTables JS API. For combo modes, apply each
                        # (column, value) pair in sequence then draw once at the end.
                        await page.evaluate(
                            """({sel, pairs}) => {
                                if (!window.jQuery) return false;
                                const api = window.jQuery(sel).DataTable();
                                for (let i = 0; i < pairs.length; i++) {
                                    const [col, q] = pairs[i];
                                    if (col >= 0) {
                                        api.column(col).search(q);
                                    } else {
                                        api.search(q);
                                    }
                                }
                                api.draw();
                                return true;
                            }""",
                            {"sel": dt_cfg.table_selector, "pairs": [list(p) for p in drive_pairs]},
                        )

                        await asyncio.sleep(dt_cfg.settle_ms / 1000.0)

                        # Reuse the table-extractor to read rows
                        rows = await extract_results_table(page, config.results)
                        log.info("[%s] DataTables sub-page yielded %d row(s)", source_id, len(rows))
                        for raw in rows:
                            raw["_source_url"] = url
                            rec = map_to_license_record(raw, config, {})
                            all_records.append(rec)
                    except Exception as e:
                        log.warning("[%s] DataTables sub-page %s failed: %s", source_id, url, e)
                        continue
            finally:
                await browser.close()
    except Exception as exc:
        log.error("[%s] DataTables scrape failed: %s", source_id, exc)
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, str(exc))
        return []

    await _emit_event(db, run_id, source_id, "complete", "success", t0, len(all_records))
    if db and all_records:
        await upsert_to_db(db, all_records)
    return all_records


# ---------------------------------------------------------------------------
# FileMaker WebDirect archetype (e.g. TX_CHIRO)
# ---------------------------------------------------------------------------

async def _scrape_filemaker_webdirect(
    config: SiteConfig, query: SearchQuery, db, t0: float, run_id: str,
) -> list:
    """Drive a FileMaker WebDirect (Vaadin 8) verification form.

    Vaadin renders text fields as div.fm-textarea (no native <input>) — so:
      1. Wait for boot_wait_ms or until ≥ filemaker.field_index size containers exist.
      2. .click() the chosen container, then keyboard.type() the value.
      3. Click submit_selector and wait for row_selector to appear.
      4. Read each row's cell_value_selector text and zip into ResultsTableConfig.columns.
    """
    from playwright.async_api import async_playwright

    source_id = config.identity.source_id
    fm_cfg = config.filemaker
    if not fm_cfg:
        log.error("[%s] filemaker_webdirect archetype requires filemaker section", source_id)
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, "no_filemaker_config")
        return []

    log.info("[%s] FileMaker run_id=%s  query=%s/%s", source_id, run_id, query.mode, query.query)

    proxy_cfg = get_proxy_config()
    all_records: list = []
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                ctx = await browser.new_context(
                    viewport={"width": config.transport.viewport.get("width", 1440),
                              "height": config.transport.viewport.get("height", 900)},
                    proxy=proxy_cfg,
                    user_agent=config.transport.user_agent,
                    ignore_https_errors=True,
                )
                page = await ctx.new_page()
                page.set_default_timeout(config.transport.timeout_ms)
                await page.goto(config.identity.base_url, wait_until="domcontentloaded")

                # Wait for the Vaadin app to boot — poll for at least one container.
                deadline = time.time() + (fm_cfg.boot_wait_ms / 1000.0)
                while time.time() < deadline:
                    try:
                        cnt = await page.locator(fm_cfg.container_selector).count()
                    except Exception:
                        cnt = 0
                    if cnt > 0:
                        break
                    await asyncio.sleep(0.5)
                # Settle a little more so the field becomes interactive
                await asyncio.sleep(2)

                # Pick the field index for this mode (default 0).
                # Scalar int = single-field. list[int] = multi-field combo drive
                # (canonical order [license_number, first_name, last_name]).
                idx_raw = fm_cfg.field_index.get(query.mode, 0)
                fields = page.locator(fm_cfg.container_selector)
                count = await fields.count()
                if count == 0:
                    raise RuntimeError(f"Vaadin booted but no '{fm_cfg.container_selector}' found")

                if isinstance(idx_raw, list):
                    canonical_vals = [query.license_number, query.first_name, query.last_name]
                    populated = [v for v in canonical_vals if v]
                    fills = list(zip(idx_raw, populated))
                else:
                    if idx_raw >= count:
                        log.warning("[%s] field_index %d >= count %d, falling back to 0", source_id, idx_raw, count)
                        idx_raw = 0
                    fills = [(idx_raw, query.query)]

                for idx, val in fills:
                    if idx >= count:
                        log.warning("[%s] field_index %d >= count %d, skipping fill", source_id, idx, count)
                        continue
                    target = fields.nth(idx)
                    await target.click()
                    await asyncio.sleep(0.3)
                    await page.keyboard.type(val, delay=40)
                    await asyncio.sleep(0.3)
                await asyncio.sleep(0.5)

                await page.locator(fm_cfg.submit_selector).first.click()

                try:
                    await page.wait_for_selector(
                        fm_cfg.row_selector, timeout=config.search.results_wait.timeout_ms,
                    )
                except Exception:
                    log.info("[%s] no rows appeared after submit", source_id)
                    return []

                # Vaadin renders the row skeleton immediately and populates cells
                # lazily; wait until the first row actually has text content.
                try:
                    await page.wait_for_function(
                        """({rowSel, cellSel}) => {
                            const r = document.querySelector(rowSel);
                            if (!r) return false;
                            const cells = r.querySelectorAll(cellSel);
                            if (cells.length === 0) return false;
                            return [...cells].some(c => (c.textContent || '').trim().length > 0);
                        }""",
                        arg={"rowSel": fm_cfg.row_selector, "cellSel": fm_cfg.cell_value_selector},
                        timeout=20_000,
                    )
                except Exception:
                    log.warning("[%s] row cells never populated", source_id)

                # Each row is a single <td> with many div.text descendants — one per column.
                # Use page.evaluate to read all rows in one shot (faster + dodges
                # Playwright locator chain quirks on Vaadin nested layout).
                row_data = await page.evaluate(
                    """({rowSel, cellSel}) => {
                        return [...document.querySelectorAll(rowSel)].map(row =>
                            [...row.querySelectorAll(cellSel)].map(d => (d.textContent || '').trim())
                        );
                    }""",
                    {"rowSel": fm_cfg.row_selector, "cellSel": fm_cfg.cell_value_selector},
                )
                log.info("[%s] FileMaker returned %d row(s)", source_id, len(row_data))
                if row_data:
                    log.info("[%s] first row cells: %s", source_id, row_data[0])

                cols_map = (config.results.table.columns if config.results.table else {}) or {}
                for cells in row_data:
                    rec: dict = {}
                    for ci, fname in cols_map.items():
                        if ci < len(cells):
                            rec[fname] = cells[ci]
                    if rec and any(v.strip() for v in rec.values() if isinstance(v, str)):
                        rec["_source_url"] = config.identity.base_url
                        all_records.append(map_to_license_record(rec, config, {}))
            finally:
                await browser.close()
    except Exception as exc:
        log.error("[%s] FileMaker scrape failed: %s", source_id, exc)
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, str(exc))
        return []

    await _emit_event(db, run_id, source_id, "complete", "success", t0, len(all_records))
    if db and all_records:
        await upsert_to_db(db, all_records)
    return all_records


# ---------------------------------------------------------------------------
# Multi-iteration helper (e.g. AZ_SPEECH_HEAR provider type loop)
# ---------------------------------------------------------------------------

async def _set_iteration_value(page, mi_cfg, value: str) -> None:
    """Set an iteration value (select option / input fill / URL navigate)."""
    if mi_cfg.field_kind == "select":
        try:
            await page.locator(mi_cfg.field_selector).first.select_option(value=value, timeout=5000)
        except Exception:
            try:
                await page.locator(mi_cfg.field_selector).first.select_option(label=value, timeout=5000)
            except Exception as e:
                log.warning("multi_iteration: select '%s' failed: %s", value, e)
    elif mi_cfg.field_kind == "input":
        loc = page.locator(mi_cfg.field_selector).first
        await loc.clear()
        await loc.fill(value)
    # url_replace handled at navigation time

# ---------------------------------------------------------------------------
# Universal verify_license entry point  (spec §4.3)
# ---------------------------------------------------------------------------

async def verify_license(
    config: SiteConfig,
    query: SearchQuery,
    db=None,
    headless_override: bool | None = None,
) -> list:
    source_id = config.identity.source_id
    _ts = datetime.utcnow()
    month = _ts.strftime("%Y-%m")
    _date_str = _ts.strftime("%Y%m%d")
    _time_str = _ts.strftime("%H%M%S")
    _ev_source_dir = Path(f"./evidence/{month}/{source_id}")
    _today_seq = (
        sum(1 for d in _ev_source_dir.iterdir() if d.is_dir() and d.name.startswith(_date_str))
        if _ev_source_dir.exists()
        else 0
    )
    run_id = f"{_date_str}_{_time_str}_{_today_seq + 1:03d}"
    records = []
    t0 = time.time()

    log.info("[%s] run_id=%s  query=%s/%s", source_id, run_id, query.mode, query.query)

    # Capability check: degrade or reject before launching the browser.
    from engine.navigator import check_board_capability
    cap_status, fallback = check_board_capability(config, query)
    if cap_status == "reject":
        log.error("[%s] Capability reject: %s", source_id, fallback)
        await _emit_event(db, run_id, source_id, "capability", "reject", t0, 0, fallback)
        return []
    if cap_status == "degrade" and fallback:
        log.warning(
            "[%s] Board cannot satisfy mode '%s' natively; degrading to '%s' with auto-joined query='%s'",
            source_id, query.mode, fallback, query.query,
        )
        # Build a degraded query: same fields, fallback mode, query.query left auto-joined.
        query = query.model_copy(update={"mode": fallback})

    # Socrata API / bulk-CSV boards bypass the browser entirely
    if config.identity.archetype == "socrata_api":
        return await _scrape_socrata_api(config, query, db, t0, run_id)
    if config.identity.archetype == "socrata_bulk_csv":
        return await _scrape_socrata_bulk_csv(config, query, db, t0, run_id)
    if config.identity.archetype == "pdf_bulk":
        return await _scrape_pdf_bulk(config, query, db, t0, run_id)
    if config.identity.archetype == "csv_bulk":
        return await _scrape_csv_bulk(config, query, db, t0, run_id)
    if config.identity.archetype == "certemy":
        return await _scrape_certemy(config, query, db, t0, run_id)
    if config.identity.archetype == "json_api":
        return await _scrape_json_api(config, query, db, t0, run_id)
    if config.identity.archetype == "datatables_jsapi":
        return await _scrape_datatables_jsapi(config, query, db, t0, run_id)
    if config.identity.archetype == "filemaker_webdirect":
        return await _scrape_filemaker_webdirect(config, query, db, t0, run_id)

    ev_path = resolve_evidence_path(config.evidence, source_id, run_id, month)
    config.evidence.local_path = ev_path

    async with get_page(config.transport, headless_override=headless_override) as page:
        try:
            # L1 — Navigate
            await navigate_to_search(page, config)

            mi_cfg = config.multi_iteration
            iterations = (mi_cfg.values if (mi_cfg and mi_cfg.values) else [None])

            for iter_value in iterations:
                if mi_cfg and iter_value is not None:
                    log.info("[%s] multi_iteration: %s='%s'", source_id, mi_cfg.field_selector, iter_value)
                    if mi_cfg.field_kind == "url_replace":
                        # Reload base_url with the iteration value substituted into url_template
                        target = (mi_cfg.url_template or config.identity.base_url).replace("{value}", iter_value)
                        await page.goto(target)
                        await page.wait_for_load_state("domcontentloaded")
                        await asyncio.sleep(1.0)
                    else:
                        try:
                            await _set_iteration_value(page, mi_cfg, iter_value)
                        except Exception as e:
                            log.warning("[%s] iteration set value failed: %s", source_id, e)

                # L2 — Fill form + search
                has_results = await fill_search_form(page, config, query)

                # L3 — Evidence: search results (only on first iteration to keep noise down)
                if iter_value in (None, iterations[0]):
                    await capture_evidence(page, config.evidence, stage="search_results", run_id=run_id)

                if not has_results:
                    log.info("[%s] No results for iter='%s' query='%s'", source_id, iter_value, query.query)
                    if not mi_cfg:
                        await _emit_event(db, run_id, source_id, "search", "no_results", t0, 0)
                        return []
                    # multi_iteration: continue to next iteration
                    if mi_cfg.field_kind != "url_replace":
                        await page.goto(config.identity.base_url)
                        await page.wait_for_load_state("domcontentloaded")
                    continue

                # L4 — Extract
                # Detect select_list results at runtime (some sites use a <select>
                # listbox for name searches but a button for license-number searches).
                use_select_list = (
                    config.results.select_list
                    and await page.locator(config.results.select_list.selector).count() > 0
                )

                if config.results.type == "ag_grid":
                    raw_rows = await extract_ag_grid(page, config.results.ag_grid_columns or None)
                    for raw in raw_rows:
                        mapped = apply_field_map(raw, config.detail.field_map)
                        rec = map_to_license_record(mapped, config, {})
                        records.append(rec)
                elif use_select_list:
                    records.extend(await _scrape_select_list_results(page, config, run_id, db))
                elif config.results.type == "single_record":
                    raw = await _scrape_one_detail(page, config, run_id, db)
                    rec = map_to_license_record(raw, config, {
                        "html_path": raw.get("html_path"),
                        "screenshot_path": raw.get("screenshot_path"),
                    })
                    records.append(rec)
                elif config.results.has_detail_page and config.results.detail_trigger:
                    single_pat = config.results.single_result_url_pattern
                    if single_pat and single_pat in page.url:
                        # Portal auto-redirected to detail page (single-result shortcut).
                        # Extract directly — do not hunt for trigger links on this page.
                        raw = await _scrape_one_detail(page, config, run_id, db)
                        rec = map_to_license_record(raw, config, {
                            "html_path": raw.get("html_path"),
                            "screenshot_path": raw.get("screenshot_path"),
                        })
                        records.append(rec)
                    else:
                        records.extend(await _scrape_with_detail_clicks(page, config, run_id, db))
                else:
                    raw_rows = await extract_results_table(page, config.results)
                    for raw in raw_rows:
                        rec = map_to_license_record(raw, config, {})
                        records.append(rec)

                if mi_cfg and mi_cfg.stop_after_first_hit and records:
                    break

                # Reset for the next iteration
                if mi_cfg and iter_value != iterations[-1]:
                    if mi_cfg.field_kind != "url_replace":
                        try:
                            await page.goto(config.identity.base_url)
                            await page.wait_for_load_state("domcontentloaded")
                        except Exception:
                            pass

        except Exception as exc:
            log.error("[%s] Scrape failed: %s", source_id, exc, exc_info=True)
            await capture_evidence(page, config.evidence, stage="error", run_id=run_id)
            await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, str(exc))
            raise

    await _emit_event(db, run_id, source_id, "complete", "success", t0, len(records))

    if db and records:
        await upsert_to_db(db, records)

    return records


async def _wait_for_detail_content(page, config: SiteConfig) -> None:
    """Wait for detail page content to render.

    If config.detail.wait.strategy == "element_visible", wait for the configured
    selector(s) first — this handles inline detail panels that don't change the URL
    (e.g. NV_DENTAL AngularJS inline expansion).

    For SPA archetypes (thentia_cloud, ag_grid_spa) with url_change strategy: use a JS
    predicate that checks for actual text in Angular dt/heading elements.

    For classic_html_form / state_portal: a simple networkidle wait is enough.
    """
    dw = config.detail.wait
    if dw.strategy == "element_visible":
        wait_sels = ([dw.selector] if dw.selector else []) + dw.fallback_selectors
        for sel in wait_sels:
            try:
                await page.wait_for_selector(sel, state="visible", timeout=dw.timeout_ms)
                log.debug("Detail content visible via selector '%s'", sel)
                # SPA archetypes render the container shell instantly but populate data
                # via XHR (e.g. getLicenseDetails()). Wait for ng-repeat rows — those
                # only appear once the async response has been bound by Angular.
                if config.identity.archetype in ("ag_grid_spa", "thentia_cloud"):
                    try:
                        await page.wait_for_function(
                            """() => {
                                const rows = document.querySelectorAll(
                                    'div.detail-container tr[ng-repeat], div.detail-container tr[ng-repeat-end]'
                                );
                                if (rows.length === 0) return false;
                                const cell = rows[0].querySelector('td.ng-binding');
                                return cell && cell.textContent.trim().length > 0;
                            }""",
                            timeout=10000,
                        )
                    except Exception:
                        await asyncio.sleep(3)
                return
            except Exception:
                continue
        # None matched — fall through to archetype logic as last resort
        return

    archetype = config.identity.archetype
    if archetype in ("thentia_cloud", "ag_grid_spa"):
        try:
            await page.wait_for_function(
                """() => {
                    const dts = document.querySelectorAll('dl dt, dl dt.ng-binding');
                    if (dts.length > 0 && Array.from(dts).some(dt => dt.textContent.trim().length > 1)) {
                        return true;
                    }
                    const h = document.querySelector('h1.ng-binding, h2.ng-binding, h3.ng-binding');
                    return h && h.textContent.trim().length > 1 && !h.textContent.includes('SHARED_LABEL');
                }""",
                timeout=35000,
            )
        except Exception:
            await asyncio.sleep(10)
            try:
                await page.wait_for_function(
                    """() => {
                        const dts = document.querySelectorAll('dl dt');
                        if (dts.length > 0 && Array.from(dts).some(dt => dt.textContent.trim().length > 1)) return true;
                        const h = document.querySelector('h1.ng-binding, h2.ng-binding, h3.ng-binding');
                        return h && h.textContent.trim().length > 1 && !h.textContent.includes('SHARED_LABEL');
                    }""",
                    timeout=15000,
                )
            except Exception:
                pass
    else:
        # Classic server-rendered pages: wait for networkidle or fallback selectors
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        for sel in config.detail.wait.fallback_selectors:
            try:
                await page.wait_for_selector(sel, timeout=5000)
                break
            except Exception:
                continue


async def _scrape_with_detail_clicks(page, config: SiteConfig, run_id: str, db) -> list:
    """Click View → extract → back, across all paginated result pages."""
    records = []
    trigger_sel = config.results.detail_trigger.selector

    async for _ in paginate(page, config.results.pagination):
        # Snapshot View buttons on this page
        buttons = page.locator(trigger_sel)
        num_buttons = await buttons.count()
        if num_buttons == 0:
            break

        for idx in range(num_buttons):
            try:
                btns = page.locator(trigger_sel)
                btn = btns.nth(idx)
                url_before = page.url

                # Quick visibility check before the slow scroll_into_view — break
                # if the SPA lost its state after browser_back navigations.
                try:
                    if not await btn.is_visible(timeout=3000):
                        log.info("Button idx=%d not visible, ending page iteration", idx)
                        break
                except Exception:
                    log.info("Button idx=%d check failed, ending page iteration", idx)
                    break

                await btn.scroll_into_view_if_needed()
                await asyncio.sleep(0.3)
                # Remove target="_blank" so new-window links open in the same tab
                await btn.evaluate("el => el.removeAttribute('target')")
                await btn.click()

                # Wait for detail page URL change
                try:
                    await page.wait_for_function(
                        "url => window.location.href !== url",
                        url_before,
                        timeout=config.detail.wait.timeout_ms,
                    )
                except Exception:
                    pass

                await _wait_for_detail_content(page, config)

                raw = await _scrape_one_detail(page, config, run_id, db)
                rec = map_to_license_record(raw, config, {
                    "html_path": raw.get("html_path"),
                    "screenshot_path": raw.get("screenshot_path"),
                })
                records.append(rec)

            except Exception as exc:
                log.error("Detail scrape failed (idx=%d): %s", idx, exc)
                try:
                    await _navigate_back(page, config)
                except Exception:
                    pass
                continue

            try:
                await _navigate_back(page, config)
            except Exception as back_err:
                log.warning(
                    "navigate_back failed after idx=%d (collected %d record(s)): %s",
                    idx, len(records), back_err,
                )
                return records  # stop here — can't reliably continue from this page

            # Re-wait for results table
            try:
                rw = config.search.results_wait
                if rw.selector:
                    await page.wait_for_selector(rw.selector, timeout=10000)
            except Exception:
                pass

    return records


async def _scrape_select_list_results(page, config: SiteConfig, run_id: str, db) -> list:
    """Iterate a <select> listbox; extract detail for each option.

    Two strategies:
    - submit_button: select the option then click sl.submit_selector to reach detail page.
    - license_number_search: parse the license number from each option text, navigate to
      base_url, and re-run the license_number search flow (mirrors original Selenium behaviour).
    """
    records = []
    sl = config.results.select_list
    if not sl:
        return records

    try:
        await page.wait_for_selector(sl.selector, timeout=10000)
    except Exception:
        log.warning("select_list: listbox '%s' not found", sl.selector)
        return records

    # Collect all (license_num, display_text) from the listbox up front
    listbox = page.locator(sl.selector)
    options = listbox.locator("option")
    option_count = await options.count()
    log.info("select_list: found %d options in '%s'", option_count, sl.selector)

    option_data: list[tuple[str, str]] = []
    sep = sl.option_separator
    for i in range(option_count):
        try:
            opt = options.nth(i)
            text = (await opt.inner_text()).strip()
            if not text:
                continue
            if sep in text:
                lic_num = text.split(sep, 1)[0].strip()
            else:
                lic_num = text
            option_value = await opt.get_attribute("value") or text
            option_data.append((lic_num, option_value, text))
        except Exception:
            continue

    for lic_num, option_value, option_text in option_data:
        log.info("select_list: processing '%s'", option_text)
        try:
            if sl.navigation_strategy == "license_number_search":
                # Re-run the license_number search from the base URL
                await page.goto(config.identity.base_url)
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(1)

                lic_mode_cfg = next(
                    (m for m in config.search.modes if m.mode == sl.license_number_mode),
                    None,
                )
                if lic_mode_cfg is None:
                    log.warning("select_list: mode '%s' not found in config", sl.license_number_mode)
                    continue

                # Fill license number input
                inp_sel = lic_mode_cfg.input_selector or config.search.form.search_input.selector
                await page.wait_for_selector(inp_sel, state="visible", timeout=8000)
                await page.locator(inp_sel).first.clear()
                await page.locator(inp_sel).first.fill(lic_num)

                # Click search button
                btn_sel = lic_mode_cfg.button_selector or config.search.form.search_button.selector
                await page.locator(btn_sel).first.click()

                # Wait for detail trigger (e.g. #btnLICNO2)
                trigger_sel = config.results.detail_trigger.selector if config.results.detail_trigger else "#btnLICNO2"
                try:
                    await page.wait_for_selector(trigger_sel, timeout=10000)
                except Exception:
                    log.warning("select_list: detail trigger '%s' not found for '%s'", trigger_sel, lic_num)
                    continue

                url_before = page.url
                await page.locator(trigger_sel).first.click()
                try:
                    await page.wait_for_function(
                        "url => window.location.href !== url",
                        url_before,
                        timeout=config.detail.wait.timeout_ms,
                    )
                except Exception:
                    pass

            else:
                # submit_button strategy: select option then click submit
                await listbox.select_option(value=option_value)
                await asyncio.sleep(0.3)

                url_before = page.url
                submit_btn = page.locator(sl.submit_selector)
                if await submit_btn.count() == 0:
                    log.warning("select_list: submit button '%s' not found", sl.submit_selector)
                    continue
                await submit_btn.click()
                try:
                    await page.wait_for_function(
                        "url => window.location.href !== url",
                        url_before,
                        timeout=config.detail.wait.timeout_ms,
                    )
                except Exception:
                    pass

            await _wait_for_detail_content(page, config)

            raw = await _scrape_one_detail(page, config, run_id, db)
            if not raw.get("license_number") and lic_num:
                raw["license_number"] = lic_num

            rec = map_to_license_record(raw, config, {
                "html_path": raw.get("html_path"),
                "screenshot_path": raw.get("screenshot_path"),
            })
            records.append(rec)

        except Exception as exc:
            log.error("select_list: detail scrape failed for '%s': %s", lic_num, exc)

        # Navigate back so the next iteration can use the listbox
        await _navigate_back(page, config)
        await asyncio.sleep(0.5)

    return records


async def _emit_event(db, run_id, source_id, stage, status, t0, count, error=None):
    if db is None:
        return
    event = TelemetryEvent(
        run_id=run_id,
        source_id=source_id,
        stage=stage,
        status=status,
        duration_ms=int((time.time() - t0) * 1000),
        record_count=count,
        error_msg=error,
    )
    await log_scrape_event(db, event)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args():
    p = argparse.ArgumentParser(description="LVS Universal License Scraper")
    p.add_argument("--config", required=True, help="Path to board config.yaml")
    # Legacy single-mode interface — still supported for backward compat.
    p.add_argument("--mode", default=None, help="Search mode (e.g. license_number, last_name, name). Optional when structured fields are provided.")
    p.add_argument("--query", default=None, help="Search string for legacy single-mode invocation.")
    # Structured field flags — any combination derives the appropriate mode.
    p.add_argument("--license-number", dest="license_number", default=None, help="License number value")
    p.add_argument("--first-name", dest="first_name", default=None, help="First name value")
    p.add_argument("--last-name", dest="last_name", default=None, help="Last name value")
    p.add_argument("--license-type", dest="license_type", default=None, help="Orthogonal filter: license type / sub-category (Active, Permanent, ...)")
    p.add_argument("--provider-type", dest="provider_type", default=None, help="Orthogonal filter: provider type (MD, DO, RN, LPN, PA, NP, ...)")
    p.add_argument("--headed", action="store_true", help="Run browser in headed (visible) mode")
    p.add_argument("--dry-run", action="store_true", help="Validate config and print plan; no browser")
    p.add_argument("--output", default=None, help="Output JSON path (default: output/{source_id}_{ts}.json)")
    p.add_argument("--db", default="./lvs_scrape.db", help="SQLite DB path (default: ./lvs_scrape.db)")
    p.add_argument("--evidence-dir", default=None, help="Override evidence base path")
    args = p.parse_args()

    # Validation: require either --mode/--query OR at least one structured field.
    has_structured = any([args.license_number, args.first_name, args.last_name])
    if not args.mode and not has_structured:
        p.error("must provide either --mode/--query or one of --license-number/--first-name/--last-name")
    return args


def _derive_mode_from_flags(license_number, first_name, last_name) -> Optional[str]:
    """Auto-derive a SearchQuery mode from which structured field flags are set."""
    has_lic = bool(license_number)
    has_first = bool(first_name)
    has_last = bool(last_name)
    if has_lic and has_first and has_last:
        return "license_first_last"
    if has_lic and has_last:
        return "license_and_last"
    if has_lic and has_first:
        return "license_and_first"
    if has_first and has_last:
        return "first_and_last"
    if has_lic:
        return "license_number"
    if has_first:
        return "first_name"
    if has_last:
        return "last_name"
    return None


async def _main():
    args = _parse_args()

    config: SiteConfig = load_config(args.config)

    if args.evidence_dir:
        config.evidence.local_path = args.evidence_dir + "/{month}/{source_id}/{run_id}/"

    # Derive mode from structured flags when --mode is not given. Explicit --mode
    # always wins (user can request a synthesized combo by name + supplying fields).
    derived = _derive_mode_from_flags(args.license_number, args.first_name, args.last_name)
    mode = args.mode or derived or "last_name"

    if args.dry_run:
        print(f"DRY RUN — config valid")
        print(f"  source_id      : {config.identity.source_id}")
        print(f"  board          : {config.identity.board_name}")
        print(f"  archetype      : {config.identity.archetype}")
        print(f"  url            : {config.identity.base_url}")
        print(f"  mode           : {mode} ({'auto-derived' if not args.mode else 'explicit'})")
        print(f"  query          : {args.query}")
        print(f"  license_number : {args.license_number}")
        print(f"  first_name     : {args.first_name}")
        print(f"  last_name      : {args.last_name}")
        print(f"  license_type   : {args.license_type}")
        print(f"  provider_type  : {args.provider_type}")
        print(f"  headless       : {not args.headed}")
        return

    query = SearchQuery(
        mode=mode,
        query=args.query or "",
        license_number=args.license_number,
        first_name=args.first_name,
        last_name=args.last_name,
        license_type=args.license_type,
        provider_type=args.provider_type,
    )

    db = await init_db(args.db)
    try:
        records = await verify_license(
            config=config,
            query=query,
            db=db,
            headless_override=not args.headed if args.headed else None,
        )
    finally:
        await db.close()

    if not records:
        print("No records found.")
        return

    _out_ts = datetime.utcnow()
    _out_month = _out_ts.strftime("%Y-%m")
    _out_ts_str = _out_ts.strftime("%Y%m%d_%H%M%S")
    output_path = args.output or f"./output/{_out_month}/{config.identity.source_id}_{_out_ts_str}.json"
    await write_output(records, output_path)

    print(f"\nDone. {len(records)} record(s) written to {output_path}")
    for r in records[:3]:
        name = r.licensee_full_name or f"{r.licensee_first_name or ''} {r.licensee_last_name or ''}".strip()
        print(f"  [{r.license_number}] {name} — {r.status.value} — exp {r.expiration_date}")
    if len(records) > 3:
        print(f"  ... and {len(records) - 3} more")


def main():
    asyncio.run(_main())


if __name__ == "__main__":
    main()
