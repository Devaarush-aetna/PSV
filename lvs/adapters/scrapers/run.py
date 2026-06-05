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
import uuid
from datetime import datetime
from pathlib import Path

# Add parent to path so engine imports work when run from this directory
sys.path.insert(0, str(Path(__file__).parent))

from engine.ai_fallback import extract_with_ai, should_use_ai_fallback
from engine.browser import get_page
from engine.evidence import capture_evidence, resolve_evidence_path
from engine.extractor import extract_ag_grid, extract_detail, extract_results_table
from engine.models import SearchQuery, SiteConfig, TelemetryEvent
from engine.navigator import fill_search_form, navigate_to_search
from engine.output import map_to_license_record, upsert_to_db, write_output
from engine.pagination import paginate
from engine.post_processors import apply_field_map
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

def _build_socrata_url(config: SiteConfig, query: SearchQuery) -> str:
    """Build a Socrata SoQL query URL.  input_selector stores the field name."""
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

    proxy_cfg = None
    if config.transport.proxy.enabled:
        import os as _os
        server = _os.environ.get("LVS_PROXY_SERVER", "")
        if server:
            proxy_cfg = {
                "server": server,
                "username": _os.environ.get("LVS_PROXY_USER", ""),
                "password": _os.environ.get("LVS_PROXY_PASS", ""),
            }
        else:
            # Fallback: legacy env vars used by the original standalone scrapers
            nid = _os.environ.get("PROXY_NID", "")
            pwd = _os.environ.get("PROXY_PASSWORD", "")
            if nid and pwd:
                proxy_cfg = {"server": f"http://{nid}:{pwd}@proxy.aetna.com:9119"}

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


# ---------------------------------------------------------------------------
# Universal verify_license entry point  (spec §4.3)
# ---------------------------------------------------------------------------

async def verify_license(
    config: SiteConfig,
    query: SearchQuery,
    db=None,
    headless_override: bool | None = None,
) -> list:
    run_id = str(uuid.uuid4())[:8]
    source_id = config.identity.source_id
    records = []
    t0 = time.time()

    log.info("[%s] run_id=%s  query=%s/%s", source_id, run_id, query.mode, query.query)

    # Socrata API / bulk-CSV boards bypass the browser entirely
    if config.identity.archetype == "socrata_api":
        return await _scrape_socrata_api(config, query, db, t0, run_id)
    if config.identity.archetype == "socrata_bulk_csv":
        return await _scrape_socrata_bulk_csv(config, query, db, t0, run_id)

    ev_path = resolve_evidence_path(config.evidence, source_id, run_id)
    config.evidence.local_path = ev_path

    async with get_page(config.transport, headless_override=headless_override) as page:
        try:
            # L1 — Navigate
            await navigate_to_search(page, config)

            # L2 — Fill form + search
            has_results = await fill_search_form(page, config, query)

            # L3 — Evidence: search results
            await capture_evidence(page, config.evidence, stage="search_results", run_id=run_id)

            if not has_results:
                log.info("[%s] No results for query '%s'", source_id, query.query)
                await _emit_event(db, run_id, source_id, "search", "no_results", t0, 0)
                return []

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
                records = await _scrape_select_list_results(page, config, run_id, db)
            elif config.results.type == "single_record":
                # Results page IS the detail page — extract directly without clicking
                raw = await _scrape_one_detail(page, config, run_id, db)
                rec = map_to_license_record(raw, config, {
                    "html_path": raw.get("html_path"),
                    "screenshot_path": raw.get("screenshot_path"),
                })
                records.append(rec)
            elif config.results.has_detail_page and config.results.detail_trigger:
                records = await _scrape_with_detail_clicks(page, config, run_id, db)
            else:
                raw_rows = await extract_results_table(page, config.results)
                for raw in raw_rows:
                    rec = map_to_license_record(raw, config, {})
                    records.append(rec)

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
    p.add_argument("--mode", required=True, help="Search mode (e.g. license_number, last_name, name)")
    p.add_argument("--query", required=True, help="Search string")
    p.add_argument("--headed", action="store_true", help="Run browser in headed (visible) mode")
    p.add_argument("--dry-run", action="store_true", help="Validate config and print plan; no browser")
    p.add_argument("--output", default=None, help="Output JSON path (default: output/{source_id}_{ts}.json)")
    p.add_argument("--db", default="./lvs_scrape.db", help="SQLite DB path (default: ./lvs_scrape.db)")
    p.add_argument("--evidence-dir", default=None, help="Override evidence base path")
    return p.parse_args()


async def _main():
    args = _parse_args()

    config: SiteConfig = load_config(args.config)

    if args.evidence_dir:
        config.evidence.local_path = args.evidence_dir + "/{source_id}/{run_id}/"

    if args.dry_run:
        print(f"DRY RUN — config valid")
        print(f"  source_id : {config.identity.source_id}")
        print(f"  board     : {config.identity.board_name}")
        print(f"  archetype : {config.identity.archetype}")
        print(f"  url       : {config.identity.base_url}")
        print(f"  mode      : {args.mode}")
        print(f"  query     : {args.query}")
        print(f"  headless  : {not args.headed}")
        return

    query = SearchQuery(mode=args.mode, query=args.query)

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

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    output_path = args.output or f"./output/{config.identity.source_id}_{ts}.json"
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
