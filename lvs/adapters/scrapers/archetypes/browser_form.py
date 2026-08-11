"""Browser form archetypes: classic_html_form, state_portal, thentia_cloud, ag_grid_spa, pega_constellation."""
from __future__ import annotations

import asyncio
import logging

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
                        records.extend(await _scrape_with_detail_clicks(page, config, run_id, db))
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
                        records.extend(await _scrape_with_detail_clicks(page, config, run_id, db))
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


async def _scrape_with_detail_clicks(page, config: SiteConfig, run_id: str, db) -> list:
    """Click View → extract → back, across all paginated result pages."""
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
