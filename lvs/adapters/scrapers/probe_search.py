"""Probe search behavior — fills the form, waits, captures results state."""
import asyncio
import sys

from engine.browser import get_page
from engine.models import TransportConfig


SCENARIOS = {
    "OR_NATUROPATH": {
        "url": "https://obnm.us.thentiacloud.net/webs/obnm/register/",
        "input_selector": "input#keywords",
        "submit_via_enter": True,
        "search_button": None,
    },
    "OR_PT": {
        "url": "https://obpt.us.thentiacloud.net/webs/obpt/register/",
        "input_selector": "input#keywords",
        "submit_via_enter": True,
    },
    "OK_OPTOMETRY_v1": {
        "url": "https://obeo.thentiacloud.net/webs/obeo/register/",
        "input_selector": "input#keywords",
        "submit_via_enter": True,
    },
    "OK_OPTOMETRY_v2": {
        "url": "https://obeo.us.thentiacloud.net/webs/obeo/register/",
        "input_selector": "input#keywords",
        "submit_via_enter": True,
    },
}


async def probe(name: str, scenario: dict) -> None:
    transport = TransportConfig(
        browser="chromium",
        headless=True,
        viewport={"width": 1280, "height": 900},
        timeout_ms=45000,
        navigation_timeout_ms=30000,
        rate_limit={"delay_between_requests_ms": 2000, "max_concurrent": 1},
        retry={"max_attempts": 1, "backoff_ms": [1000], "retry_on": ["timeout"]},
        proxy={"enabled": True},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    )
    print("=" * 80)
    print(f"BOARD: {name}")
    print(f"URL: {scenario['url']}")
    try:
        async with get_page(transport) as page:
            try:
                await page.goto(scenario["url"], wait_until="domcontentloaded", timeout=30_000)
            except Exception as e:
                print(f"GOTO ERROR: {e}")
                return
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass
            await asyncio.sleep(2)
            try:
                title = await page.title()
                print(f"TITLE: {title}")
            except Exception:
                pass

            # Find input
            try:
                cnt = await page.locator(scenario["input_selector"]).count()
                print(f"input count for '{scenario['input_selector']}': {cnt}")
            except Exception as e:
                print(f"input check error: {e}")
                return
            if cnt == 0:
                return

            # Get pre-search content
            try:
                pre = await page.evaluate(
                    "() => ({rows: document.querySelectorAll('table tbody tr').length, body_snippet: (document.body.innerText||'').slice(0,500)})"
                )
                print(f"PRE: rows={pre['rows']}, body_snippet={pre['body_snippet']!r}")
            except Exception as e:
                print(f"pre check error: {e}")

            try:
                await page.locator(scenario["input_selector"]).first.fill("Smith")
                if scenario.get("submit_via_enter"):
                    await page.locator(scenario["input_selector"]).first.press("Enter")
                elif scenario.get("search_button"):
                    await page.locator(scenario["search_button"]).first.click()
            except Exception as e:
                print(f"fill/submit error: {e}")
                return

            # Wait for search to complete
            await asyncio.sleep(4)
            try:
                post = await page.evaluate(
                    "() => ({rows: document.querySelectorAll('table tbody tr').length, body_snippet: (document.body.innerText||'').slice(0,1500), url: window.location.href})"
                )
                print(f"POST: rows={post['rows']}, url={post['url']}")
                print(f"POST body_snippet={post['body_snippet']!r}")
            except Exception as e:
                print(f"post error: {e}")
    except Exception as e:
        print(f"OUTER ERROR: {e}")


async def main():
    names = sys.argv[1:] if len(sys.argv) > 1 else list(SCENARIOS.keys())
    for n in names:
        if n in SCENARIOS:
            await probe(n, SCENARIOS[n])


if __name__ == "__main__":
    asyncio.run(main())
