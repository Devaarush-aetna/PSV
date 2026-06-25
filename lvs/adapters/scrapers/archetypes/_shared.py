"""Shared helpers used by multiple archetype modules."""
from __future__ import annotations

import asyncio
import logging
import time

from engine.evidence import capture_evidence
from engine.extractor import extract_detail
from engine.ai_fallback import extract_with_ai, should_use_ai_fallback
from engine.models import SiteConfig, TelemetryEvent
from engine.telemetry import log_scrape_event

log = logging.getLogger(__name__)


async def _emit_event(
    db, run_id, source_id, stage, status, t0, count,
    error=None, partial_result=False, warnings=None,
):
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
        partial_result=partial_result,
        warnings=warnings or [],
    )
    await log_scrape_event(db, event)


async def _scrape_one_detail(page, config: SiteConfig, run_id: str, db) -> dict:
    evidence = await capture_evidence(
        page, config.evidence, stage="detail_page", run_id=run_id,
        source_id=config.identity.source_id, state=config.identity.state,
    )
    raw = await extract_detail(page, config.detail)

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


async def _wait_for_detail_content(page, config: SiteConfig) -> None:
    """Wait for detail page content to render."""
    dw = config.detail.wait
    if dw.strategy == "element_visible":
        wait_sels = ([dw.selector] if dw.selector else []) + dw.fallback_selectors
        for sel in wait_sels:
            try:
                await page.wait_for_selector(sel, state="visible", timeout=dw.timeout_ms)
                log.debug("Detail content visible via selector '%s'", sel)
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
