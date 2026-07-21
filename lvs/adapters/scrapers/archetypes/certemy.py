"""Certemy Angular SPA archetype."""
from __future__ import annotations

import asyncio
import logging

from engine.evidence import capture_evidence
from engine.models import SearchQuery, SiteConfig
from engine.output import map_to_license_record, upsert_to_db
from engine.post_processors import apply_field_map
from engine.proxy import get_proxy_config
from ._shared import _emit_event

log = logging.getLogger(__name__)


async def scrape_certemy(
    config: SiteConfig, query: SearchQuery, db, t0: float, run_id: str,
) -> list:
    """Certemy Angular SPA: live-filter input, HTML table, Material paginator."""
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

                # Wait for Angular to finish loading initial table data before filtering.
                # Without this, typing into the filter can run against an empty dataset.
                try:
                    await page.wait_for_selector("table tbody tr", state="visible", timeout=15_000)
                    await asyncio.sleep(0.5)
                except Exception:
                    await asyncio.sleep(1.5)  # board may show empty table initially; proceed

                # For last_name mode, try combined "First Last" first to reduce false positives
                # (e.g. "Uren" would match first names like "Lauren" on a global-text filter).
                # If the combined term returns 0 rows — which happens on boards that filter
                # per-column (NV_MFTPC Certemy) or when the name contains an apostrophe —
                # fall back to last_name alone so the search still works.
                if query.mode == "last_name" and query.first_name and query.query:
                    _search_terms = [f"{query.first_name} {query.query}", query.query]
                else:
                    _search_terms = [query.query]

                async def _type_and_wait(term: str) -> int:
                    """Clear the search box, type term char-by-char, return stable row count."""
                    inp = page.locator("input.search-input").first
                    await inp.click()
                    await inp.click(click_count=3)
                    await inp.fill("")
                    for ch in term:
                        await page.keyboard.type(ch)
                        await asyncio.sleep(0.08)
                    log.info("[%s] Typed query %r into input.search-input", source_id, term)
                    _prev, _stable = -1, 0
                    for _ in range(50):
                        await asyncio.sleep(0.4)
                        try:
                            _n = await page.locator("table tbody tr").count()
                        except Exception:
                            _n = -1
                        if _n == _prev and _n >= 0:
                            _stable += 1
                            if _stable >= 2:
                                break
                        else:
                            _stable = 0
                        _prev = _n
                    return _prev if _prev >= 0 else 0

                if _search_terms[0]:
                    _row_count = 0
                    for _term in _search_terms:
                        _row_count = await _type_and_wait(_term)
                        if _row_count > 0:
                            break
                        if len(_search_terms) > 1:
                            log.info(
                                "[%s] Combined search %r returned 0 rows, trying fallback %r",
                                source_id, _search_terms[0], _search_terms[-1],
                            )
                else:
                    await page.wait_for_selector("table tbody tr", state="visible", timeout=20_000)
                    await asyncio.sleep(1.0)

                headers = await page.evaluate(
                    "() => [...document.querySelectorAll('table thead tr th')].map(th => th.textContent.trim())"
                )
                log.info("[%s] Certemy columns: %s", source_id, headers)
                await capture_evidence(page, config.evidence, stage="search_results", run_id=run_id, source_id=source_id, state=config.identity.state, query=query)

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
        try:
            await capture_evidence(page, config.evidence, stage="error", run_id=run_id, source_id=source_id, state=config.identity.state, query=query)
        except Exception:
            pass
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
