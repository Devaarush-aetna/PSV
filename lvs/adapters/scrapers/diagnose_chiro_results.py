"""Diagnose NV_CHIRO search results structure."""
import asyncio
from playwright.async_api import async_playwright

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=_UA)
        page = await ctx.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        await page.goto("https://nvcpbn.portalus.thentiacloud.net/webs/portal/register/#/")
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        await asyncio.sleep(3)

        await page.fill("input#keywords", "Smith")
        await page.click("button.btn-brand")
        print("Submitted search, waiting for results...")

        try:
            await page.wait_for_selector("table tbody tr", timeout=20000)
            rows = await page.locator("table tbody tr").count()
            print(f"Table rows: {rows}")
            # Print first row content
            if rows > 0:
                cells = page.locator("table tbody tr").first.locator("td")
                for i in range(await cells.count()):
                    print(f"  td[{i}]: {(await cells.nth(i).inner_text()).strip()[:80]!r}")
        except Exception as e:
            print(f"No table rows within 20s: {e}")
            html = await page.content()
            print(f"HTML length: {len(html)}")
            for sel in ["div.result", "ul.results", ".licensee", 'a:has-text("View")', ".card", "tbody", ".panel", ".list-group-item"]:
                c = await page.locator(sel).count()
                if c > 0:
                    print(f"  Found: {sel!r} x{c}")

        await page.screenshot(path="diagnose_chiro_results.png", full_page=True)
        print("Screenshot saved: diagnose_chiro_results.png")
        await browser.close()

asyncio.run(main())
