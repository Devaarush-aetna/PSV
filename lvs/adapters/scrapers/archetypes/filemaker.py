"""FileMaker WebDirect (Vaadin 8) archetype."""
from __future__ import annotations

import asyncio
import logging
import time

from engine.evidence import capture_evidence
from engine.models import SearchQuery, SiteConfig
from engine.output import map_to_license_record, upsert_to_db
from engine.proxy import get_proxy_config
from ._shared import _emit_event

log = logging.getLogger(__name__)


async def scrape_filemaker_webdirect(
    config: SiteConfig, query: SearchQuery, db, t0: float, run_id: str,
) -> list:
    """Drive a FileMaker WebDirect (Vaadin 8) verification form."""
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

                deadline = time.time() + (fm_cfg.boot_wait_ms / 1000.0)
                while time.time() < deadline:
                    try:
                        cnt = await page.locator(fm_cfg.container_selector).count()
                    except Exception:
                        cnt = 0
                    if cnt > 0:
                        break
                    await asyncio.sleep(0.5)
                await asyncio.sleep(2)

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

                row_data = await page.evaluate(
                    """({rowSel, cellSel}) => {
                        return [...document.querySelectorAll(rowSel)].map(row =>
                            [...row.querySelectorAll(cellSel)].map(d => (d.textContent || '').trim())
                        );
                    }""",
                    {"rowSel": fm_cfg.row_selector, "cellSel": fm_cfg.cell_value_selector},
                )
                log.info("[%s] FileMaker returned %d row(s)", source_id, len(row_data))
                await capture_evidence(page, config.evidence, stage="search_results", run_id=run_id, source_id=source_id, state=config.identity.state, query=query)
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
        try:
            await capture_evidence(page, config.evidence, stage="error", run_id=run_id, source_id=source_id, state=config.identity.state, query=query)
        except Exception:
            pass
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, str(exc))
        return []

    await _emit_event(db, run_id, source_id, "complete", "success", t0, len(all_records))
    if db and all_records:
        await upsert_to_db(db, all_records)
    return all_records
