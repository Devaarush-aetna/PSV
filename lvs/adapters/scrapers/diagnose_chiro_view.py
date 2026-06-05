"""Check NV_CHIRO View button structure after search."""
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
        await page.wait_for_selector("table tbody tr", timeout=25000)
        await asyncio.sleep(1)

        # Check the last cell of first row
        first_row = page.locator("table tbody tr").first
        cells = first_row.locator("td")
        last_td = cells.last
        print("Last td innerHTML:", await last_td.inner_html())

        # Check View button selectors
        for sel in ["a:has-text('View')", "button:has-text('View')", "input[value='View']", "td:has-text('View') a", "td:has-text('View') button", "td:last-child a"]:
            c = await page.locator(sel).count()
            print(f"  {sel!r}: {c}")

        await browser.close()

asyncio.run(main())
