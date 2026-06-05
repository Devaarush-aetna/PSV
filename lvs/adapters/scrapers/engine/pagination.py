"""Generic pagination handler — yields once per page of results."""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from playwright.async_api import Page

from .models import PaginationConfig

log = logging.getLogger(__name__)

_SAFETY_CAP = 50


async def paginate(page: Page, config: PaginationConfig) -> AsyncIterator[None]:
    """
    Async generator that yields once per result page.
    Caller processes the current page, then we advance to the next.
    """
    if not config.enabled or config.strategy == "none":
        yield
        return

    for page_num in range(1, _SAFETY_CAP + 1):
        yield  # let caller process current page

        if config.strategy == "next_button":
            advanced = await _click_next_button(page, config)
        elif config.strategy == "page_numbers":
            advanced = await _click_page_number(page, page_num + 1)
        elif config.strategy == "infinite_scroll":
            advanced = await _scroll_for_more(page)
        else:
            break

        if not advanced:
            break
        await asyncio.sleep(2)

    if page_num >= _SAFETY_CAP:
        log.warning("Pagination safety cap (%d pages) reached", _SAFETY_CAP)


async def _click_next_button(page: Page, config: PaginationConfig) -> bool:
    if not config.next_selector:
        return False
    try:
        next_btn = page.locator(config.next_selector).first
        if await next_btn.count() == 0:
            return False
        classes = (await next_btn.get_attribute("class") or "").lower()
        if config.disabled_class.lower() in classes:
            log.info("Next button is disabled — end of pages")
            return False
        aria_disabled = await next_btn.get_attribute("aria-disabled")
        if aria_disabled == "true":
            return False
        await next_btn.click()
        log.info("Clicked next page button")
        return True
    except Exception as e:
        log.warning("_click_next_button failed: %s", e)
        return False


async def _click_page_number(page: Page, page_num: int) -> bool:
    try:
        btn = page.get_by_role("button", name=str(page_num))
        if await btn.count() == 0:
            btn = page.get_by_text(str(page_num), exact=True)
        if await btn.count() > 0:
            await btn.first.click()
            log.info("Clicked page %d", page_num)
            return True
    except Exception as e:
        log.warning("_click_page_number(%d) failed: %s", page_num, e)
    return False


async def _scroll_for_more(page: Page) -> bool:
    """Scroll to bottom; return True if page height increased (more content loaded)."""
    try:
        height_before = await page.evaluate("document.body.scrollHeight")
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1.5)
        height_after = await page.evaluate("document.body.scrollHeight")
        return height_after > height_before
    except Exception as e:
        log.warning("_scroll_for_more failed: %s", e)
        return False
