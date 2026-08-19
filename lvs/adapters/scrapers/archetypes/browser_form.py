"""Browser form archetypes: classic_html_form, state_portal, thentia_cloud, ag_grid_spa, pega_constellation."""
from __future__ import annotations

import asyncio
import json as _json
import logging
import re
from urllib.parse import urljoin

from engine.browser import get_page
from engine.evidence import capture_evidence
from engine.extractor import extract_ag_grid, extract_results_table, extract_th_td_multi
from engine.models import LicenseStatus, SearchQuery, SiteConfig
from engine.navigator import fill_search_form, navigate_to_search
from engine.output import map_to_license_record, upsert_to_db
from engine.pagination import paginate
from engine.post_processors import apply_field_map
from ._shared import (
    _emit_event,
    _navigate_back,
    _scrape_one_detail,
    _scrape_pdf_detail,
    _set_iteration_value,
    _wait_for_detail_content,
)

log = logging.getLogger(__name__)


async def scrape_browser(
    config: SiteConfig,
    query: SearchQuery,
    db,
    t0: float,
    run_id: str,
    headless_override=None,
) -> list:
    """Universal browser-form scrape loop (all non-specialized browser archetypes)."""
    source_id = config.identity.source_id
    records = []
    partial_failures: list[str] = []

    async with get_page(config.transport, headless_override=headless_override) as page:
        try:
            await navigate_to_search(page, config)

            mi_cfg = config.multi_iteration
            iterations = (mi_cfg.values if (mi_cfg and mi_cfg.values) else [None])

            for iter_value in iterations:
                if mi_cfg and iter_value is not None:
                    log.info("[%s] multi_iteration: %s='%s'", source_id, mi_cfg.field_selector, iter_value)
                    if mi_cfg.field_kind == "url_replace":
                        target = (mi_cfg.url_template or config.identity.base_url).replace("{value}", iter_value)
                        await page.goto(target)
                        await page.wait_for_load_state("domcontentloaded")
                        await asyncio.sleep(1.0)
                    else:
                        try:
                            await _set_iteration_value(page, mi_cfg, iter_value)
                        except Exception as e:
                            log.warning("[%s] iteration set value failed: %s", source_id, e)

                has_results = await fill_search_form(page, config, query, partial_failures=partial_failures)

                if iter_value in (None, iterations[0]):
                    await capture_evidence(page, config.evidence, stage="search_results", run_id=run_id, source_id=source_id, state=config.identity.state, query=query)

                if not has_results:
                    log.info("[%s] No results for iter='%s' query='%s'", source_id, iter_value, query.query)
                    if not mi_cfg:
                        await _emit_event(db, run_id, source_id, "search", "no_results", t0, 0)
                        return []
                    if mi_cfg.field_kind != "url_replace":
                        await page.goto(config.identity.base_url)
                        await page.wait_for_load_state("domcontentloaded")
                    continue

                use_select_list = (
                    config.results.select_list
                    and await page.locator(config.results.select_list.selector).count() > 0
                )

                if config.results.type == "ag_grid":
                    if config.results.has_detail_page and config.results.detail_trigger:
                        records.extend(await _scrape_with_detail_clicks(page, config, run_id, db, query))
                    else:
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
                        await _wait_for_detail_content(page, config)
                        raw = await _scrape_one_detail(page, config, run_id, db)
                        rec = map_to_license_record(raw, config, {
                            "html_path": raw.get("html_path"),
                            "screenshot_path": raw.get("screenshot_path"),
                        })
                        records.append(rec)
                    else:
                        records.extend(await _scrape_with_detail_clicks(page, config, run_id, db, query))
                elif config.results.type == "th_td_multi":
                    raw_rows = await extract_th_td_multi(page, config.results)
                    for raw in raw_rows:
                        mapped = apply_field_map(raw, config.detail.field_map)
                        rec = map_to_license_record(mapped, config, {})
                        records.append(rec)
                else:
                    raw_rows, _warn = await extract_results_table(page, config.results)
                    if _warn:
                        partial_failures.append(_warn)
                    for raw in raw_rows:
                        rec = map_to_license_record(raw, config, {})
                        records.append(rec)

                if mi_cfg and mi_cfg.stop_after_first_hit and records:
                    break

                if mi_cfg and iter_value != iterations[-1]:
                    if mi_cfg.field_kind != "url_replace":
                        try:
                            await page.goto(config.identity.base_url)
                            await page.wait_for_load_state("domcontentloaded")
                        except Exception:
                            pass

        except Exception as exc:
            log.error("[%s] Scrape failed: %s", source_id, exc, exc_info=True)
            await capture_evidence(page, config.evidence, stage="error", run_id=run_id, source_id=source_id, state=config.identity.state, query=query)
            await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, str(exc))
            raise

    if partial_failures:
        log.warning("[%s] Partial failures: %s", source_id, partial_failures)
        for rec in records:
            rec.partial_result = True

    _final_status = "partial" if partial_failures else "success"
    await _emit_event(
        db, run_id, source_id, "complete", _final_status, t0, len(records),
        partial_result=bool(partial_failures), warnings=partial_failures,
    )

    if db and records:
        await upsert_to_db(db, records)

    return records


async def _fetch_detail_via_api(page, config: SiteConfig, idx: int) -> dict:
    """Fetch detail data by calling the board's JSON API directly (no page navigation).

    WHY THIS FUNCTION EXISTS
    ------------------------
    Some AngularJS boards (e.g. PA_PALS) open the detail page in a new _blank
    tab instead of navigating the current tab.  Playwright's normal click +
    wait_for_url flow watches the CURRENT tab, so the URL never changes and the
    detail page is never loaded.

    Instead of following the _blank tab, we:
      1. Extract POST body parameters from the Angular scope of the current page
         (using the scope_selector + scope_params paths defined in config.detail.api).
      2. Call the backing JSON API via fetch() in the page context, which sends
         session cookies automatically (no manual auth needed).
      3. Map the JSON response fields to a raw {field: value} dict via
         config.detail.api.field_map.

    The caller (``_scrape_with_detail_clicks``) merges this raw dict with the
    summary-row data using the same logic as the standard click path.

    Parameters
    ----------
    page : playwright.async_api.Page
        The Playwright page object, still on the search-results view.
    config : SiteConfig
        Board config; config.detail.api must be non-None before calling this.
    idx : int
        Zero-based index of the result row being processed.  Used to resolve
        {idx} placeholders in scope_params path expressions so each row's
        PersonId/LicenseId is picked independently.

    Returns
    -------
    dict
        Canonical field names → raw string values (e.g. {"expiration_date": "11/30/2025"}).
        Empty dict on any failure (logged as WARNING); caller falls back to summary row.
    """
    api_cfg = config.detail.api
    source_id = config.identity.source_id

    # ------------------------------------------------------------------
    # Step 1: Build the POST body by resolving scope_params paths.
    #
    # We inject a small JS snippet that:
    #   a) Finds the DOM element matching scope_selector.
    #   b) Walks up the ancestor chain until it finds an Angular scope that
    #      contains the first required data path (verifies the right scope).
    #   c) Resolves every scope_params path and returns {bodyKey: stringValue}.
    #
    # The {idx} placeholder in paths (e.g. "search.PersonDetails[{idx}].PersonId")
    # is substituted with the current row index before the JS runs.
    # ------------------------------------------------------------------
    scope_params_resolved = {
        k: v.replace("{idx}", str(idx))
        for k, v in api_cfg.scope_params.items()
    }

    # JS that runs in the browser page context to extract scope values.
    # Defined as a string so it can be passed to page.evaluate().
    _js_extract_scope = """
    ([scopeSelector, scopeParams]) => {
        // Resolve a dot-path + array-index expression against an object.
        // e.g. "search.PersonDetails[0].PersonId" on the Angular scope.
        function resolvePath(obj, path) {
            // Convert "[N]" bracket notation to ".N" so we can split on "."
            const parts = path.replace(/\\[(\\d+)\\]/g, '.$1').split('.');
            let cur = obj;
            for (const part of parts) {
                if (cur === null || cur === undefined) return undefined;
                cur = cur[part];
            }
            return cur;
        }

        // Locate the DOM anchor element for scope traversal.
        const startEl = document.querySelector(scopeSelector);
        if (!startEl) {
            return {error: 'scope_selector_not_found: ' + scopeSelector};
        }

        // The first scope_params path is used to verify we found the right scope.
        const firstPath = Object.values(scopeParams)[0];

        // Walk up the DOM tree from the anchor element.
        let el = startEl;
        while (el) {
            let s;
            try {
                // angular.element().scope() returns the Angular scope for that element.
                s = (typeof angular !== 'undefined') ? angular.element(el).scope() : null;
            } catch(_) { s = null; }

            if (s && firstPath !== undefined && resolvePath(s, firstPath) !== undefined) {
                // Found the scope with the required data — resolve all params.
                const body = {};
                for (const [key, path] of Object.entries(scopeParams)) {
                    const val = resolvePath(s, path);
                    // Stringify everything; null/undefined becomes '0' (safe default
                    // for IsFacility-style boolean flags the API expects as "0"/"1").
                    body[key] = (val === null || val === undefined) ? '0' : String(val);
                }
                return {ok: true, body: body};
            }
            el = el.parentElement;
        }
        return {error: 'scope_not_found_for_path: ' + firstPath};
    }
    """

    extract_result = await page.evaluate(_js_extract_scope, [api_cfg.scope_selector, scope_params_resolved])

    if not extract_result.get("ok"):
        log.warning(
            "[%s] detail_api: scope extraction failed (idx=%d): %s",
            source_id, idx, extract_result.get("error", "unknown"),
        )
        return {}

    post_body = extract_result["body"]
    log.debug("[%s] detail_api: POST body (idx=%d): %s", source_id, idx, post_body)

    # ------------------------------------------------------------------
    # Step 2: Call the API via fetch() inside the page context.
    #
    # Running fetch() in the page context means the browser sends all
    # existing session/auth cookies automatically — no manual cookie
    # management required.  For GET requests, body params become query
    # string params.
    # ------------------------------------------------------------------
    _js_fetch = """
    async ([endpoint, method, body]) => {
        try {
            if (method === 'GET') {
                // Append params as query string for GET requests.
                const qs = new URLSearchParams(body).toString();
                const sep = endpoint.includes('?') ? '&' : '?';
                const resp = await fetch(endpoint + sep + qs, {
                    method: 'GET',
                    credentials: 'include',
                });
                return {status: resp.status, body: await resp.text()};
            }
            // POST: send body as JSON.
            const resp = await fetch(endpoint, {
                method: 'POST',
                credentials: 'include',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body),
            });
            return {status: resp.status, body: await resp.text()};
        } catch(e) {
            return {error: String(e)};
        }
    }
    """

    fetch_result = await page.evaluate(_js_fetch, [api_cfg.endpoint, api_cfg.method.upper(), post_body])

    if fetch_result.get("error"):
        log.warning("[%s] detail_api: fetch error (idx=%d): %s", source_id, idx, fetch_result["error"])
        return {}

    http_status = fetch_result.get("status")
    body_text = fetch_result.get("body", "")

    if http_status not in (200, 201):
        log.warning(
            "[%s] detail_api: HTTP %s from %s (idx=%d) — body: %s",
            source_id, http_status, api_cfg.endpoint, idx, body_text[:200],
        )
        return {}

    # ------------------------------------------------------------------
    # Step 3: Parse the JSON response and map fields to canonical names.
    #
    # api_cfg.field_map: {"ExpiryDate": "expiration_date", ...}
    # The output raw dict uses canonical field names so map_to_license_record
    # can process it identically to a normal HTML-scraped detail page.
    # ------------------------------------------------------------------
    try:
        data = _json.loads(body_text)
    except Exception as exc:
        log.warning("[%s] detail_api: JSON parse error (idx=%d): %s", source_id, idx, exc)
        return {}

    raw: dict = {}
    for api_key, canonical_field in api_cfg.field_map.items():
        if isinstance(data, dict):
            value = data.get(api_key)
        else:
            value = None
        if value is not None:
            raw[canonical_field] = value

    log.info(
        "[%s] detail_api: OK (idx=%d) — %s",
        source_id, idx,
        {k: v for k, v in raw.items() if v},
    )
    return raw


async def _scrape_with_detail_clicks(page, config: SiteConfig, run_id: str, db, query=None) -> list:
    """Click View → extract → back, across all paginated result pages.

    When results.pagination.harvest_all is set, delegate to the two-phase
    harvester instead: it pages through the whole result set collecting summary
    rows first (no per-row navigation), then fetches detail only for rows that
    match the search target — avoiding the postback-grid page-1 reset.
    """
    if (
        config.results.pagination.harvest_all
        and config.results.type == "table"
        and config.results.detail_trigger
    ):
        return await _harvest_paginated_then_detail(page, config, run_id, db, query)

    records = []
    trigger_sel = config.results.detail_trigger.selector

    async for _ in paginate(page, config.results.pagination):
        # Pre-extract table summary rows before clicking any detail button.
        # Boards whose back_navigation resets the page (e.g. Thentia Cloud returning
        # to homepage) lose the results table after the first detail click.  Having
        # the summaries cached lets us fall back to them for rows whose View button
        # is no longer reachable after back-navigation.
        _summary_rows: list = []
        try:
            if config.results.type == "table":
                _raw_summary, _ = await extract_results_table(page, config.results)
                _summary_rows = [map_to_license_record(r, config, {}) for r in _raw_summary]
        except Exception:
            pass

        buttons = page.locator(trigger_sel)
        num_buttons = await buttons.count()
        if num_buttons == 0:
            records.extend(_summary_rows)
            break

        for idx in range(num_buttons):
            try:
                btns = page.locator(trigger_sel)
                btn = btns.nth(idx)
                url_before = page.url

                try:
                    if not await btn.is_visible(timeout=3000):
                        log.info("Button idx=%d not visible — falling back to table summary for %d remaining row(s)",
                                 idx, max(0, len(_summary_rows) - idx))
                        records.extend(_summary_rows[idx:])
                        return records
                except Exception:
                    log.info("Button idx=%d check failed — falling back to table summary for %d remaining row(s)",
                             idx, max(0, len(_summary_rows) - idx))
                    records.extend(_summary_rows[idx:])
                    return records

                await btn.scroll_into_view_if_needed()
                await asyncio.sleep(0.3)

                # ----------------------------------------------------------
                # BRANCH A: Direct JSON API detail (e.g. PA_PALS)
                #
                # When config.detail.api is set, the board's "detail link"
                # opens a new _blank tab rather than navigating the current
                # tab. Playwright's standard URL-change wait never fires here.
                #
                # Instead of clicking the link at all, we:
                #   1. Extract PersonId/LicenseId from the Angular scope.
                #   2. POST to the configured API endpoint.
                #   3. Map the JSON response directly to the raw field dict.
                #   4. Skip back-navigation (we never left the search-results page).
                #
                # See _fetch_detail_via_api() above and DetailApiConfig in
                # models.py for full documentation.
                # ----------------------------------------------------------
                if config.detail.api:
                    raw = await _fetch_detail_via_api(page, config, idx)
                    rec = map_to_license_record(raw, config, {})
                    # Merge name/license fields from the summary row — the API
                    # response does include them, but merging guarantees we have
                    # them even if the API call partially fails.
                    if idx < len(_summary_rows):
                        _sr = _summary_rows[idx]
                        if not rec.license_number and _sr.license_number:
                            rec.license_number = _sr.license_number
                        if not rec.licensee_full_name and not rec.licensee_first_name and _sr.licensee_full_name:
                            rec.licensee_full_name   = _sr.licensee_full_name
                            rec.licensee_first_name  = _sr.licensee_first_name
                            rec.licensee_last_name   = _sr.licensee_last_name
                        if not rec.license_type and _sr.license_type:
                            rec.license_type = _sr.license_type
                        if rec.status == LicenseStatus.UNKNOWN and _sr.status != LicenseStatus.UNKNOWN:
                            rec.status = _sr.status
                        if rec.expiration_date is None and _sr.expiration_date is not None:
                            rec.expiration_date = _sr.expiration_date
                    records.append(rec)
                    # No back-navigation needed — we never left the search-results page.
                    continue

                # ----------------------------------------------------------
                # BRANCH B: Standard click → navigate → scrape → back
                #
                # Original flow for all boards that navigate in the same tab.
                # ----------------------------------------------------------

                # PDF detail: if the link href points to a PDF, download and parse
                # it directly instead of navigating the browser (avoids PDF viewer issues).
                _href = (await btn.get_attribute("href") or "").strip()
                _force_pdf = bool(config.results.detail_trigger and config.results.detail_trigger.force_pdf)
                _is_pdf = _force_pdf or _href.lower().endswith(".pdf") or "pdf" in _href.lower().split("?")[0]
                if _is_pdf and not _href:
                    log.warning("force_pdf=True but href is empty at idx=%d — using summary row only", idx)
                    if idx < len(_summary_rows):
                        records.append(_summary_rows[idx])
                    continue
                if _is_pdf:
                    from engine.post_processors import apply_field_map as _afm
                    _pdf_raw = await _scrape_pdf_detail(page, _href, config)
                    _pdf_mapped = _afm(_pdf_raw, config.detail.field_map)
                    rec = map_to_license_record(_pdf_mapped, config, {})
                    if idx < len(_summary_rows):
                        _sr = _summary_rows[idx]
                        if not rec.license_number and _sr.license_number:
                            rec.license_number = _sr.license_number
                        if not rec.licensee_full_name and not rec.licensee_first_name and _sr.licensee_full_name:
                            rec.licensee_full_name = _sr.licensee_full_name
                            rec.licensee_first_name = _sr.licensee_first_name
                            rec.licensee_last_name = _sr.licensee_last_name
                        if not rec.license_type and _sr.license_type:
                            rec.license_type = _sr.license_type
                        from engine.models import LicenseStatus as _LS
                        if rec.status == _LS.UNKNOWN and _sr.status != _LS.UNKNOWN:
                            rec.status = _sr.status
                        if rec.expiration_date is None and _sr.expiration_date is not None:
                            rec.expiration_date = _sr.expiration_date
                    records.append(rec)
                    continue  # no back navigation needed — browser never navigated

                await btn.evaluate("el => el.removeAttribute('target')")
                # CHANGED: For AngularJS ng-click links that use href="" or href="#"
                # as a placeholder, clicking triggers TWO things: the ng-click handler
                # (which calls $state.go() to navigate the Angular route) AND the
                # browser's default anchor navigation to the base URL (href="" strips
                # the hash). The default navigation races against $state.go() and can
                # reload the page before Angular finishes routing. Setting href to
                # javascript:void(0) prevents the default navigation while still
                # allowing ng-click to fire — used by PALS (PA) and similar AngularJS
                # SPAs that use href="" + ng-click for in-app navigation links.
                if not _href or _href.strip() == "#":
                    await btn.evaluate("el => el.setAttribute('href', 'javascript:void(0)')")
                await btn.click()

                try:
                    await page.wait_for_function(
                        "url => window.location.href !== url",
                        url_before,
                        timeout=config.detail.wait.timeout_ms,
                    )
                except Exception:
                    pass

                await _wait_for_detail_content(page, config)

                # Detect board-side error/session-expired pages before extracting.
                # When any configured error_page_selector matches, skip extraction
                # and fall through to summary-row merge so the record is not lost.
                _err_page = False
                for _ep_sel in (config.detail.wait.error_page_selectors or []):
                    try:
                        if await page.locator(_ep_sel).count() > 0:
                            _err_page = True
                            log.warning(
                                "[%s] Error page detected (idx=%d, sel=%r) — "
                                "using summary-row fallback",
                                config.identity.source_id, idx, _ep_sel,
                            )
                            break
                    except Exception:
                        pass

                if _err_page:
                    raw = {}
                else:
                    raw = await _scrape_one_detail(page, config, run_id, db)
                rec = map_to_license_record(raw, config, {
                    "html_path": raw.get("html_path"),
                    "screenshot_path": raw.get("screenshot_path"),
                })
                # Merge summary-row fields when detail page extraction left key
                # fields blank (e.g. nested table race, garbled HTML, secondary
                # "Related Licenses" table overwriting primary with non-name data,
                # or board returning an error/session-expired page instead of the
                # real detail — e.g. Indiana mylicense.in.gov Details.aspx).
                if idx < len(_summary_rows):
                    _sr = _summary_rows[idx]
                    if not rec.license_number and _sr.license_number:
                        rec.license_number = _sr.license_number
                    if not rec.licensee_full_name and not rec.licensee_first_name and _sr.licensee_full_name:
                        rec.licensee_full_name = _sr.licensee_full_name
                        rec.licensee_first_name = _sr.licensee_first_name
                        rec.licensee_last_name  = _sr.licensee_last_name
                    if not rec.license_type and _sr.license_type:
                        rec.license_type = _sr.license_type
                    if rec.status == LicenseStatus.UNKNOWN and _sr.status != LicenseStatus.UNKNOWN:
                        rec.status = _sr.status
                    if rec.expiration_date is None and _sr.expiration_date is not None:
                        rec.expiration_date = _sr.expiration_date
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
                return records

            try:
                rw = config.search.results_wait
                if rw.selector:
                    await page.wait_for_selector(rw.selector, timeout=10000)
            except Exception:
                pass

    return records


def _numerics(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _row_matches_target(rec, query) -> bool:
    """Loose match of a summary row against the search target.

    Matches on license numerics (strongest) or last-name equality with a
    compatible first-name prefix. Kept deliberately permissive so the true
    record is never filtered out before its detail page is fetched.
    """
    if query is None:
        return False
    q_lic = _numerics(getattr(query, "license_number", "") or "")
    r_lic = _numerics(getattr(rec, "license_number", "") or "")
    if q_lic and r_lic and q_lic == r_lic:
        return True
    ql = (getattr(query, "last_name", "") or "").strip().lower()
    rl = (getattr(rec, "licensee_last_name", "") or "").strip().lower()
    if ql and rl and ql == rl:
        qf = (getattr(query, "first_name", "") or "").strip().lower()
        rf = (getattr(rec, "licensee_first_name", "") or "").strip().lower()
        if not qf or not rf or rf.startswith(qf) or qf.startswith(rf):
            return True
    return False


async def _harvest_paginated_then_detail(page, config: SiteConfig, run_id: str, db, query) -> list:
    """Two-phase scrape for postback-paginated result grids.

    Phase 1 — page through the ENTIRE result set collecting summary rows plus
    each row's detail link, never navigating away from the grid (so the grid's
    page state is never reset). Stops early once a page contains the target.
    Phase 2 — navigate directly to the detail link for matching rows and merge
    the richer detail fields; non-matching rows are returned as summary-only so
    the caller still sees the full candidate set.
    """
    source_id = config.identity.source_id
    tbl = config.results.table
    trigger_sel = config.results.detail_trigger.selector
    # Anchor selector relative to a row = the final token of the trigger selector
    # (e.g. "...tr.gridrows a[href*='results.aspx']" -> "a[href*='results.aspx']").
    anchor_sel = trigger_sel.split()[-1]

    # ---- Phase 1: harvest summaries + hrefs across all pages ----------------
    harvested: list[tuple] = []          # (record, href)
    seen: set[tuple] = set()
    found_target = False
    page_count = 0

    async for _ in paginate(page, config.results.pagination):
        page_count += 1
        rows = page.locator(tbl.row_selector)
        n = await rows.count()
        page_had_target = False
        for i in range(n):
            row = rows.nth(i)
            cells = row.locator(tbl.cell_selector)
            ncells = await cells.count()
            raw: dict = {}
            for idx, fname in tbl.columns.items():
                if idx < ncells:
                    raw[fname] = (await cells.nth(idx).inner_text()).strip()
            if not any(v for v in raw.values() if isinstance(v, str) and v.strip()):
                continue
            href = None
            links = row.locator(anchor_sel)
            if await links.count() > 0:
                href = await links.first.get_attribute("href")
            rec = map_to_license_record(raw, config, {})
            key = (
                (rec.license_number or "").upper(),
                (rec.licensee_full_name or "").upper(),
                href or "",
            )
            if key in seen:
                continue
            seen.add(key)
            harvested.append((rec, href))
            if _row_matches_target(rec, query):
                page_had_target = True
        log.info("[%s] harvest page %d: %d rows (running total %d)%s",
                 source_id, page_count, n, len(harvested),
                 "  <-- target found" if page_had_target else "")
        if page_had_target:
            found_target = True
            break  # early-exit: the person we want is on this page

    log.info("[%s] harvest complete: %d unique rows across %d page(s); target_found=%s",
             source_id, len(harvested), page_count, found_target)

    # ---- Phase 2: fetch detail for matching rows only -----------------------
    targets = [(rec, href) for rec, href in harvested if _row_matches_target(rec, query)]
    if not targets:
        # Nothing matched the pre-filter — return every summary row so the
        # caller's disambiguator still gets the full candidate set to score.
        log.info("[%s] no summary row matched target — returning %d summary-only rows",
                 source_id, len(harvested))
        return [rec for rec, _ in harvested]

    records = []
    target_hrefs = {id(rec) for rec, _ in targets}
    for rec, href in harvested:
        if id(rec) not in target_hrefs or not href:
            records.append(rec)  # summary-only candidate
            continue
        try:
            detail_url = urljoin(page.url, href)
            await page.goto(detail_url)
            await _wait_for_detail_content(page, config)
            raw = await _scrape_one_detail(page, config, run_id, db)
            drec = map_to_license_record(raw, config, {
                "html_path": raw.get("html_path"),
                "screenshot_path": raw.get("screenshot_path"),
            })
            # Backfill from the summary row when the detail page omits a field.
            if not drec.license_number and rec.license_number:
                drec.license_number = rec.license_number
            if not drec.licensee_full_name and not drec.licensee_first_name and rec.licensee_full_name:
                drec.licensee_full_name = rec.licensee_full_name
                drec.licensee_first_name = rec.licensee_first_name
                drec.licensee_last_name = rec.licensee_last_name
            if not drec.license_type and rec.license_type:
                drec.license_type = rec.license_type
            if drec.status == LicenseStatus.UNKNOWN and rec.status != LicenseStatus.UNKNOWN:
                drec.status = rec.status
            if drec.expiration_date is None and rec.expiration_date is not None:
                drec.expiration_date = rec.expiration_date
            records.append(drec)
        except Exception as exc:
            log.warning("[%s] detail fetch failed for %r: %s — using summary row",
                        source_id, href, exc)
            records.append(rec)

    return records


async def _scrape_select_list_results(page, config: SiteConfig, run_id: str, db) -> list:
    """Iterate a <select> listbox; extract detail for each option."""
    records = []
    sl = config.results.select_list
    if not sl:
        return records

    try:
        await page.wait_for_selector(sl.selector, timeout=10000)
    except Exception:
        log.warning("select_list: listbox '%s' not found", sl.selector)
        return records

    listbox = page.locator(sl.selector)
    options = listbox.locator("option")
    option_count = await options.count()
    log.info("select_list: found %d options in '%s'", option_count, sl.selector)

    option_data: list[tuple[str, str, str]] = []
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

                inp_sel = lic_mode_cfg.input_selector or config.search.form.search_input.selector
                await page.wait_for_selector(inp_sel, state="visible", timeout=8000)
                await page.locator(inp_sel).first.clear()
                await page.locator(inp_sel).first.fill(lic_num)

                btn_sel = lic_mode_cfg.button_selector or config.search.form.search_button.selector
                await page.locator(btn_sel).first.click()

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

        await _navigate_back(page, config)
        await asyncio.sleep(0.5)

    return records
