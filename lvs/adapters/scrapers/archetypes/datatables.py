"""DataTables JS API archetype."""
from __future__ import annotations

import asyncio
import logging

from engine.evidence import capture_evidence
from engine.extractor import extract_results_table
from engine.models import SearchQuery, SiteConfig
from engine.output import map_to_license_record, upsert_to_db
from engine.proxy import get_proxy_config
from ._shared import _emit_event

log = logging.getLogger(__name__)


async def scrape_datatables_jsapi(
    config: SiteConfig, query: SearchQuery, db, t0: float, run_id: str,
) -> list:
    """Drive a DataTables grid via its JS API."""
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

    is_combo = query.mode in COMBO_MODES
    drive_pairs: list[tuple[int, str]] = []
    if isinstance(col_idx_raw, list):
        canonical = [query.license_number, query.first_name, query.last_name]
        values = [v for v in canonical if v]
        for idx, val in zip(col_idx_raw, values):
            drive_pairs.append((idx, val))
    elif is_combo:
        drive_pairs.append((-1, query.query))
    else:
        drive_pairs.append((col_idx_raw, query.query))

    all_records: list = []
    dt_partial_failures: list[str] = []
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
                        await capture_evidence(page, config.evidence, stage="search_results", run_id=run_id, source_id=source_id, state=config.identity.state, query=query)

                        rows, _warn = await extract_results_table(page, config.results)
                        if _warn:
                            dt_partial_failures.append(f"[{url}] {_warn}")
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
        try:
            await capture_evidence(page, config.evidence, stage="error", run_id=run_id, source_id=source_id, state=config.identity.state, query=query)
        except Exception:
            pass
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, str(exc))
        return []

    status = "partial" if dt_partial_failures else "success"
    await _emit_event(
        db, run_id, source_id, "complete", status, t0, len(all_records),
        partial_result=bool(dt_partial_failures), warnings=dt_partial_failures,
    )
    if db and all_records:
        await upsert_to_db(db, all_records)
    return all_records
