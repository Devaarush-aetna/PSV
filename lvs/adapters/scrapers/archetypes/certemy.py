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
