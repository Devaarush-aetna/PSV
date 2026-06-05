"""Config-driven navigation: page load, form fill, search, results wait."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from .models import SearchConfig, SearchQuery, SiteConfig

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

async def navigate_to_search(page: Page, config: SiteConfig) -> None:
    log.info("[%s] Navigating to %s", config.identity.source_id, config.identity.base_url)
    await page.goto(config.identity.base_url)
    await page.wait_for_load_state("domcontentloaded")
    # SPA archetypes need extra time for JS framework to render the search form
    if config.identity.archetype in ("thentia_cloud", "ag_grid_spa"):
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        await asyncio.sleep(3)
    else:
        await asyncio.sleep(2)


# ---------------------------------------------------------------------------
# Search-By dropdown
# ---------------------------------------------------------------------------

async def set_search_by(page: Page, config: SearchConfig, query: SearchQuery) -> bool:
    mode_cfg = next((m for m in config.modes if m.mode == query.mode), None)
    if not mode_cfg or not mode_cfg.dropdown_value:
        return True  # no dropdown needed

    dropdown_value = mode_cfg.dropdown_value
    form = config.form
    strategy = form.search_by_dropdown.strategy

    if strategy == "none":
        return True

    if strategy == "select":
        selector = form.search_by_dropdown.selector or "select"
        try:
            await page.locator(selector).first.select_option(label=dropdown_value, timeout=5000)
            log.info("Set <select> dropdown to '%s'", dropdown_value)
            await asyncio.sleep(0.5)
            return True
        except Exception as e:
            log.warning("select strategy failed (trying custom dropdown): %s", e)

    if strategy in ("select", "custom_dropdown"):
        # Custom dropdown: click each candidate trigger, check for desired item,
        # press Escape to close if wrong dropdown opened, try the next trigger.
        try:
            triggers = page.locator("[class*='dropdown'],[class*='select'],[class*='chosen']")
            count = await triggers.count()
            for i in range(count):
                trigger = triggers.nth(i)
                try:
                    tag = await trigger.evaluate("el => el.tagName.toLowerCase()")
                    if tag == "select":
                        continue
                    if not await trigger.is_visible():
                        continue
                    await trigger.click()
                    await asyncio.sleep(0.5)
                    # Check if the desired option appeared
                    items = page.locator(
                        f"li[role='option']:has-text('{dropdown_value}'),"
                        f"li.k-item:has-text('{dropdown_value}'),"
                        f"[role='option']:has-text('{dropdown_value}')"
                    )
                    if await items.count() > 0:
                        await items.first.click()
                        log.info("Clicked custom dropdown item '%s'", dropdown_value)
                        await asyncio.sleep(0.4)
                        return True
                    # Wrong dropdown opened — close it before trying the next
                    await page.keyboard.press("Escape")
                    await asyncio.sleep(0.2)
                except Exception:
                    try:
                        await page.keyboard.press("Escape")
                    except Exception:
                        pass
                    continue
        except Exception as e:
            log.warning("custom_dropdown strategy failed: %s", e)

    if strategy == "radio":
        try:
            radio = page.locator(f"input[type='radio'][value*='{dropdown_value}']")
            if await radio.count() > 0:
                await radio.first.click()
                log.info("Clicked radio '%s'", dropdown_value)
                return True
        except Exception as e:
            log.warning("radio strategy failed: %s", e)

    log.warning("Could not set Search By to '%s'", dropdown_value)
    return False


# ---------------------------------------------------------------------------
# Search input fill
# ---------------------------------------------------------------------------

async def fill_search_input(page: Page, config: SearchConfig, query: SearchQuery) -> bool:
    # Per-mode override takes precedence over global form config
    mode_cfg = next((m for m in config.modes if m.mode == query.mode), None)
    if mode_cfg and mode_cfg.input_selector:
        selectors = [mode_cfg.input_selector]
    else:
        form = config.form
        selectors = [form.search_input.selector] + form.search_input.fallback_selectors

    for sel in selectors:
        try:
            await page.wait_for_selector(sel, state="visible", timeout=5000)
            loc = page.locator(sel).first
            await loc.clear()
            await loc.fill(query.query)
            log.info("Filled search input '%s' with '%s'", sel, query.query)
            return True
        except Exception:
            continue

    # Last resort: first visible enabled text input
    try:
        inputs = page.locator("input[type='text'], input[type='search'], input:not([type])")
        count = await inputs.count()
        for i in range(count):
            inp = inputs.nth(i)
            try:
                visible = await inp.is_visible()
                enabled = await inp.is_enabled()
            except Exception:
                continue
            if visible and enabled:
                await inp.clear()
                await inp.fill(query.query)
                log.info("Filled first visible text input with '%s'", query.query)
                return True
    except Exception as e:
        log.warning("Fallback text input fill failed: %s", e)

    log.error("Could not find search text input")
    return False


# ---------------------------------------------------------------------------
# Search button click
# ---------------------------------------------------------------------------

async def click_search_button(page: Page, config: SearchConfig, query: SearchQuery | None = None) -> bool:
    # Per-mode override takes precedence over global form config
    if query:
        mode_cfg = next((m for m in config.modes if m.mode == query.mode), None)
        if mode_cfg and mode_cfg.button_selector:
            selectors = [mode_cfg.button_selector]
        else:
            form = config.form
            selectors = [form.search_button.selector] + form.search_button.fallback_selectors
    else:
        form = config.form
        selectors = [form.search_button.selector] + form.search_button.fallback_selectors

    for sel in selectors:
        try:
            await page.wait_for_selector(sel, state="visible", timeout=3000)
            loc = page.locator(sel).first
            await loc.click()
            log.info("Clicked search button '%s'", sel)
            return True
        except Exception:
            continue

    # Image/icon buttons
    try:
        imgs = page.locator("img")
        count = await imgs.count()
        for i in range(count):
            img = imgs.nth(i)
            src = (await img.get_attribute("src") or "").lower()
            alt = (await img.get_attribute("alt") or "").lower()
            if any(k in src or k in alt for k in ("search", "magnif", "glass")):
                parent = img.locator("..")
                await parent.click()
                log.info("Clicked search image button")
                return True
    except Exception as e:
        log.warning("Image button click failed: %s", e)

    # Button by text
    try:
        buttons = page.locator("button")
        count = await buttons.count()
        for i in range(count):
            btn = buttons.nth(i)
            text = (await btn.inner_text()).lower()
            if "search" in text and await btn.is_visible():
                await btn.click()
                log.info("Clicked button by text: '%s'", text.strip())
                return True
    except Exception as e:
        log.warning("Button-by-text click failed: %s", e)

    log.error("Search button not found")
    return False


# ---------------------------------------------------------------------------
# Results wait + no-results check
# ---------------------------------------------------------------------------

async def wait_for_results(page: Page, config: SearchConfig) -> bool:
    """Wait for results to appear. Returns True if results found, False if no-results."""
    rw = config.results_wait
    timeout = rw.timeout_ms

    try:
        if rw.strategy == "element_visible" and rw.selector:
            await page.wait_for_selector(rw.selector, timeout=timeout)
        elif rw.strategy == "network_idle":
            await page.wait_for_load_state("networkidle", timeout=timeout)
        elif rw.strategy == "url_change":
            await page.wait_for_function("window.location.href !== arguments[0]",
                                         await page.evaluate("window.location.href"),
                                         timeout=timeout)
        else:
            await asyncio.sleep(2)
    except PlaywrightTimeout:
        log.warning("Timed out waiting for results (%s)", rw.strategy)

    return not await is_no_results(page, config)


async def is_no_results(page: Page, config: SearchConfig) -> bool:
    try:
        content = (await page.content()).lower()
        for indicator in config.results_wait.no_results_indicators:
            if indicator.lower() in content:
                log.info("No-results indicator found: '%s'", indicator)
                return True
    except Exception as e:
        log.warning("is_no_results check failed: %s", e)
    return False


# ---------------------------------------------------------------------------
# Full search flow
# ---------------------------------------------------------------------------

async def fill_search_form(page: Page, config: SiteConfig, query: SearchQuery) -> bool:
    """Execute the complete form-fill + search click sequence."""
    await set_search_by(page, config.search, query)
    await asyncio.sleep(1.5)  # SPA re-renders after dropdown change
    filled = await fill_search_input(page, config.search, query)
    if not filled:
        raise RuntimeError(f"[{config.identity.source_id}] Could not fill search input for query '{query.query}'")
    clicked = await click_search_button(page, config.search, query)
    if not clicked:
        raise RuntimeError(f"[{config.identity.source_id}] Could not click search button")
    return await wait_for_results(page, config.search)
