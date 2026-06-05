"""Diagnose Maryland Board of Physicians site structure."""
import asyncio
from playwright.async_api import async_playwright

BASE_URL = "https://www.mbp.state.md.us/bpqapp/"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900}, user_agent=_UA)
        page = await ctx.new_page()

        print("=== Loading search page ===")
        await page.goto(BASE_URL)
        await page.wait_for_load_state("networkidle", timeout=20000)

        # --- Snapshot form inputs ---
        print("\n=== All visible form inputs ===")
        inputs = page.locator("input:visible")
        for i in range(await inputs.count()):
            el = inputs.nth(i)
            id_ = await el.get_attribute("id") or ""
            name = await el.get_attribute("name") or ""
            type_ = await el.get_attribute("type") or "text"
            ph = await el.get_attribute("placeholder") or ""
            print(f"  <input id={id_!r} name={name!r} type={type_!r} placeholder={ph!r}>")

        print("\n=== All visible buttons ===")
        buttons = page.locator("input[type='button'], input[type='submit'], button")
        for i in range(await buttons.count()):
            el = buttons.nth(i)
            id_ = await el.get_attribute("id") or ""
            val = await el.get_attribute("value") or await el.inner_text() or ""
            print(f"  button id={id_!r} value/text={val!r}")

        # --- License number search ---
        print("\n=== Searching by license number: D0091066 ===")
        lic_input = page.locator("#txtLicense")
        if await lic_input.count():
            await lic_input.fill("D0091066")
        await page.locator("#btnLICENSE").click()
        await asyncio.sleep(2)

        print("\nAfter clicking #btnLICENSE:")
        url_after = page.url
        print(f"  URL: {url_after}")

        # Check for btnLICNO2
        btn2 = page.locator("#btnLICNO2")
        if await btn2.count():
            val = await btn2.get_attribute("value") or await btn2.inner_text() or ""
            print(f"  #btnLICNO2 found, text={val!r}")

            # Click it and go to detail page
            await btn2.click()
            await asyncio.sleep(2)
            print(f"\n  Detail page URL: {page.url}")

            # Check detail fields
            for field_id in ["Lic_no", "Name", "Lic_Type", "Lic_Status", "Org_Lic_Date", "Expiration_Date"]:
                el = page.locator(f"#{field_id}")
                if await el.count():
                    text = (await el.first.inner_text()).strip()
                    print(f"  #{field_id}: {text!r}")

            # Check labels
            print("\n  Labels on detail page:")
            labels = page.locator("label")
            for i in range(await labels.count()):
                lbl = labels.nth(i)
                for_attr = await lbl.get_attribute("for") or ""
                text = (await lbl.inner_text()).strip()
                print(f"    label for={for_attr!r}: {text!r}")

            # Check 2-column tables
            print("\n  2-col table rows on detail page:")
            tables = page.locator("table")
            for t in range(await tables.count()):
                rows = tables.nth(t).locator("tr")
                for r in range(await rows.count()):
                    cells = rows.nth(r).locator("td")
                    if await cells.count() == 2:
                        k = (await cells.nth(0).inner_text()).strip()
                        v = (await cells.nth(1).inner_text()).strip()
                        if k:
                            print(f"    {k!r}: {v!r}")

            # Save detail page HTML
            html = await page.content()
            with open("evidence/MD_PHYSICIANS_detail.html", "w", encoding="utf-8") as f:
                f.write(html)
            print(f"\n  Detail page HTML saved ({len(html)} bytes)")
        else:
            print("  #btnLICNO2 not found")
            # Check what appeared
            html = await page.content()
            with open("evidence/MD_PHYSICIANS_search.html", "w", encoding="utf-8") as f:
                f.write(html)
            print(f"  Search result HTML saved ({len(html)} bytes)")

        # --- Name search ---
        print("\n=== Going back and searching by last name: Smith ===")
        await page.goto(BASE_URL)
        await asyncio.sleep(2)

        last_name_input = page.locator("#LastName")
        if await last_name_input.count():
            await last_name_input.fill("Smith")
        await page.locator("#btnLastName").click()
        await asyncio.sleep(3)

        print(f"  URL after name search: {page.url}")
        listbox = page.locator("#listbox_Names")
        if await listbox.count():
            options = listbox.locator("option")
            cnt = await options.count()
            print(f"  #listbox_Names found with {cnt} options")
            for i in range(min(cnt, 5)):
                text = (await options.nth(i).inner_text()).strip()
                print(f"    option[{i}]: {text!r}")

            # Check what buttons/actions are near the listbox
            print("\n  Buttons near listbox:")
            btns = page.locator("input[type='button'], input[type='submit'], button")
            for i in range(await btns.count()):
                el = btns.nth(i)
                id_ = await el.get_attribute("id") or ""
                val = await el.get_attribute("value") or await el.inner_text() or ""
                if await el.is_visible():
                    print(f"    button id={id_!r} value={val!r}")

            # Try clicking first option and see what happens
            print("\n  Selecting first option and checking for submit button...")
            await options.first.click()
            await asyncio.sleep(0.5)

            # Try clicking btnLICNO2 style button if present
            btn2 = page.locator("#btnNAME2, #btnLICNO2, [id*='btn'][id*='2'], [id*='btn'][id*='SELECT']")
            if await btn2.count():
                id_ = await btn2.first.get_attribute("id") or ""
                print(f"    Submit button found: #{id_}")
            else:
                print("    No obvious submit button found — dumping all visible buttons again:")
                for i in range(await btns.count()):
                    el = btns.nth(i)
                    if await el.is_visible():
                        id_ = await el.get_attribute("id") or ""
                        val = await el.get_attribute("value") or await el.inner_text() or ""
                        print(f"      id={id_!r} value={val!r}")
        else:
            print("  #listbox_Names not found")
            # What appeared?
            for sel in ["select", "table", "#results"]:
                loc = page.locator(sel)
                c = await loc.count()
                if c:
                    print(f"  Found {c} {sel!r} element(s)")

        await browser.close()


asyncio.run(main())
