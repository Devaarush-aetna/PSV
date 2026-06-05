"""Diagnostic: load a board URL, capture screenshot + print DOM structure."""
import asyncio, sys
from playwright.async_api import async_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "https://nsbme.us.thentiacloud.net/webs/nsbme/register/#"
OUT = sys.argv[2] if len(sys.argv) > 2 else "diagnose_output"

_REAL_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-first-run"],
        )
        ctx = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=_REAL_UA,
            locale="en-US",
        )
        page = await ctx.new_page()
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page.set_default_timeout(60000)

        print(f"Navigating to {URL} ...")
        await page.goto(URL)
        await page.wait_for_load_state("domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        await asyncio.sleep(4)

        await page.screenshot(path=f"{OUT}.png", full_page=True)
        print(f"Screenshot saved: {OUT}.png")

        html = await page.content()
        with open(f"{OUT}.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"HTML saved: {OUT}.html  ({len(html)} chars)")

        # Print key elements
        print("\n--- selects ---")
        for i, sel in enumerate(await page.locator("select").all()):
            print(f"  select[{i}]: id={await sel.get_attribute('id')} name={await sel.get_attribute('name')} options={await sel.locator('option').all_inner_texts()}")

        print("\n--- text inputs ---")
        for i, inp in enumerate(await page.locator("input").all()):
            t = await inp.get_attribute("type") or "text"
            ph = await inp.get_attribute("placeholder") or ""
            vid = await inp.get_attribute("id") or ""
            print(f"  input[{i}]: type={t} id={vid} placeholder={ph!r} visible={await inp.is_visible()}")

        print("\n--- buttons ---")
        for i, btn in enumerate(await page.locator("button").all()):
            txt = (await btn.inner_text()).strip()[:60]
            cls = (await btn.get_attribute("class") or "")[:60]
            print(f"  button[{i}]: text={txt!r} class={cls!r} visible={await btn.is_visible()}")

        await browser.close()

asyncio.run(main())
