#!/usr/bin/env python3
"""
Probe each Certemy public-registry URL, type "Smith", wait for the Angular
filter to settle, then print the <thead th> column headers.

Uses the same proxy.py logic as the main engine — set PROXY=proxy:9119 before
running.

Usage:
    cd lvs/adapters/scrapers
    set PROXY=proxy:9119
    python discover_certemy_headers.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# Add parent dirs so engine.proxy is importable
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from engine.proxy import get_proxy_config  # noqa: E402

BOARDS = [
    ("LA_ADRA",     "https://app.certemy.com/public-registry/8ca73d3d-b51c-42b9-9e44-892b2411d264"),
    ("NV_PODIATRY", "https://app.certemy.com/public-registry/7a4cd67a-0473-4f45-8dff-a88975fc3269"),
    ("NV_MFTPC",    "https://nvboe.certemy.com/public-registry/00b35480-36a9-4898-a052-c13871cce91e"),
    ("NV_ORIENTAL", "https://nvbom.certemy.com/public-registry/1d329d3a-aded-4c8a-a5b2-253e2d32b09d"),
    ("NV_ABA",      "https://nvba.certemy.com/public-registry/cf634e05-338d-4856-8fce-d0104b351632"),
    ("WV_OPTOMETRY","https://wvbo.certemy.com/public-registry/0ee899c3-0585-4788-a489-2fd4fa363bae"),
]

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_SEARCH_INPUT = "input.search-input"
_TABLE_HEADER = "table thead tr th"
_TABLE_ROW    = "table tbody tr"
_TABLE_CELL   = "td"


async def _wait_settle(page, timeout_ms: int = 15_000) -> None:
    """Poll tbody row count until stable for 2 consecutive 0.4 s ticks."""
    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
    prev, stable = -1, 0
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.4)
        try:
            n = await page.locator(_TABLE_ROW).count()
        except Exception:
            n = -1
        if n == prev and n >= 0:
            stable += 1
            if stable >= 2:
                return
        else:
            stable = 0
        prev = n


async def probe_board(pw, source_id: str, url: str, proxy_cfg) -> dict:
    print(f"\n{'='*60}\n{source_id}  {url}", flush=True)
    browser = await pw.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled"],
    )
    ctx = await browser.new_context(
        viewport={"width": 1440, "height": 900},
        user_agent=_UA,
        proxy=proxy_cfg,
        locale="en-US",
    )
    ctx.set_default_timeout(60_000)
    page = await ctx.new_page()
    await page.add_init_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
    )

    result = {"source_id": source_id, "url": url, "headers": [], "sample_row": [], "error": None}
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)

        # Wait for Angular to render the search input
        try:
            await page.wait_for_selector(_SEARCH_INPUT, state="visible", timeout=25_000)
        except PWTimeout:
            print(f"  [WARN] search input not visible after 25s — may still be loading", flush=True)

        await asyncio.sleep(2.0)

        # Type "Smith" character by character to trigger Angular reactive events
        inp = page.locator(_SEARCH_INPUT).first
        await inp.click()
        await inp.fill("")
        await inp.type("Smith", delay=80)
        print(f"  Typed 'Smith' into {_SEARCH_INPUT}", flush=True)

        await _wait_settle(page, timeout_ms=15_000)

        # Grab headers
        headers: list[str] = await page.evaluate(
            "() => [...document.querySelectorAll(%r)].map(th => th.textContent.trim())"
            % _TABLE_HEADER
        )
        result["headers"] = [h for h in headers if h]
        print(f"  Headers ({len(result['headers'])}): {result['headers']}", flush=True)

        # Grab first row cells as sample
        sample: list[str] = await page.evaluate(
            "() => { const r=document.querySelector(%r); "
            "if(!r) return []; "
            "return [...r.querySelectorAll(%r)].map(td=>td.textContent.trim()); }"
            % (_TABLE_ROW, _TABLE_CELL)
        )
        result["sample_row"] = sample
        print(f"  Sample row: {sample}", flush=True)

        # Count rows found
        n = await page.locator(_TABLE_ROW).count()
        print(f"  Row count: {n}", flush=True)
        result["row_count"] = n

    except Exception as exc:
        result["error"] = str(exc)
        print(f"  ERROR: {exc}", flush=True)
    finally:
        await ctx.close()
        await browser.close()

    return result


async def probe_sd_site(pw, source_id: str, url: str, proxy_cfg) -> dict:
    """Navigate to an SD licensing site and look for CSV/download links."""
    print(f"\n{'='*60}\n{source_id}  {url}", flush=True)
    browser = await pw.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled"],
    )
    ctx = await browser.new_context(
        viewport={"width": 1440, "height": 900},
        user_agent=_UA,
        proxy=proxy_cfg,
        locale="en-US",
    )
    ctx.set_default_timeout(60_000)
    page = await ctx.new_page()

    result = {"source_id": source_id, "url": url, "links": [], "page_url": None, "error": None}
    try:
        await page.goto(url, wait_until="networkidle", timeout=60_000)
        result["page_url"] = page.url
        print(f"  Landed on: {page.url}", flush=True)

        # Collect all links from the page
        links = await page.evaluate(
            "() => [...document.querySelectorAll('a')].map(a=>({text:a.textContent.trim(),href:a.href}))"
        )
        # Filter for CSV/download-related links
        relevant = [l for l in links if any(kw in (l.get("text","") + l.get("href","")).lower()
                                             for kw in ["csv","download","roster","export","lookup","search","verif"])]
        result["links"] = relevant[:20]
        print(f"  Relevant links ({len(relevant)}):", flush=True)
        for lnk in relevant[:20]:
            print(f"    [{lnk['text'][:50]}]  {lnk['href'][:100]}", flush=True)

        # Also check for forms
        forms = await page.evaluate(
            "() => [...document.querySelectorAll('form')].map(f=>({action:f.action,method:f.method}))"
        )
        if forms:
            print(f"  Forms: {forms}", flush=True)
            result["forms"] = forms

        # Full page title
        title = await page.title()
        print(f"  Page title: {title}", flush=True)
        result["title"] = title

    except Exception as exc:
        result["error"] = str(exc)
        print(f"  ERROR: {exc}", flush=True)
    finally:
        await ctx.close()
        await browser.close()

    return result


SD_BOARDS = [
    ("SD_CHIRO", "https://bocelicensing.appssd.sd.gov"),
    ("SD_OPT",   "https://optometry.appssd.sd.gov"),
]


async def main() -> None:
    proxy_cfg = get_proxy_config()
    if proxy_cfg:
        print(f"Using proxy: {proxy_cfg['server']}", flush=True)
    else:
        print("No proxy configured (PROXY env var not set) — direct connection", flush=True)

    all_results = {}

    async with async_playwright() as pw:
        # Probe Certemy boards one at a time to avoid hammering the site
        for source_id, url in BOARDS:
            r = await probe_board(pw, source_id, url, proxy_cfg)
            all_results[source_id] = r

        # Probe SD sites
        for source_id, url in SD_BOARDS:
            r = await probe_sd_site(pw, source_id, url, proxy_cfg)
            all_results[source_id] = r

    # Final JSON summary
    print(f"\n{'='*60}\nSUMMARY JSON:\n", flush=True)
    print(json.dumps(all_results, indent=2, default=str), flush=True)

    # Save to file for easy reference
    out = __import__("pathlib").Path(__file__).parent / "certemy_headers_discovery.json"
    out.write_text(json.dumps(all_results, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved to {out}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
