"""Inspect CT eLicense Detail popup — find content location and confirm close works.

Run:
    python inspect_ct_modal.py
"""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))


async def main():
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_default_timeout(30000)

        print("Navigating to CT eLicense...")
        await page.goto("https://elicense.ct.gov/Lookup/LicenseLookup.aspx", wait_until="networkidle")

        await page.fill(
            "#ctl00_MainContentPlaceHolder_ucLicenseLookup_ctl03_tbLicenseNumber",
            "14883"
        )
        await page.click("#ctl00_MainContentPlaceHolder_ucLicenseLookup_btnLookup")
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)

        btns = page.locator("a:has-text('Details'), button:has-text('Details')")
        count = await btns.count()
        print(f"Found {count} Details button(s)")

        # Click first Details button
        await btns.first.click()
        await asyncio.sleep(3)  # wait for popup + AJAX content to load

        # Check k-overlay
        overlay_count = await page.locator(".k-overlay").count()
        print(f"k-overlay present: {overlay_count}")

        # Check all k-window elements
        print("\n--- k-window elements ---")
        kw = page.locator(".k-window")
        n = await kw.count()
        print(f"Found {n} .k-window elements")
        for i in range(n):
            el = kw.nth(i)
            visible = await el.is_visible()
            print(f"  [{i}] visible={visible}")

        # Check k-window-content elements
        print("\n--- k-window-content elements ---")
        kwc = page.locator(".k-window-content")
        n = await kwc.count()
        print(f"Found {n} .k-window-content elements")
        for i in range(n):
            el = kwc.nth(i)
            visible = await el.is_visible()
            text = await el.inner_text()
            print(f"  [{i}] visible={visible} text_len={len(text)} text_preview={text[:100]!r}")

        # Check #window element
        print("\n--- #window element ---")
        win = page.locator("#window")
        n = await win.count()
        print(f"Found {n} #window elements")
        for i in range(n):
            el = win.nth(i)
            visible = await el.is_visible()
            html = await el.inner_html()
            text = await el.inner_text()
            print(f"  [{i}] visible={visible} html_len={len(html)} text_preview={text[:200]!r}")

        # Dump ALL tables
        print("\n--- ALL tables (with all cells) ---")
        tables = page.locator("table")
        t_count = await tables.count()
        print(f"Total tables: {t_count}")
        for t in range(t_count):
            table = tables.nth(t)
            visible = await table.is_visible()
            rows = table.locator("tr")
            r_count = await rows.count()
            print(f"\n  Table {t} (visible={visible}, {r_count} rows):")
            for r in range(r_count):
                row = rows.nth(r)
                # Check both td and th
                td_cells = row.locator("td")
                th_cells = row.locator("th")
                td_count = await td_cells.count()
                th_count = await th_cells.count()
                all_text = []
                for c in range(td_count):
                    all_text.append(f"td:{(await td_cells.nth(c).inner_text()).strip()[:30]!r}")
                for c in range(th_count):
                    all_text.append(f"th:{(await th_cells.nth(c).inner_text()).strip()[:30]!r}")
                if all_text:
                    print(f"    row {r}: {' | '.join(all_text)}")

        # Try JavaScript to extract the popup content
        print("\n--- JS: Kendo Window content ---")
        js_content = await page.evaluate("""() => {
            const win = document.querySelector('[data-role=\"window\"]');
            if (!win) return 'NO data-role=window element';
            return JSON.stringify({
                id: win.id,
                classes: win.className,
                visible: window.getComputedStyle(win).display !== 'none',
                text: win.innerText.substring(0, 500),
                html: win.innerHTML.substring(0, 1000)
            });
        }""")
        print(js_content)

        # Try to find the detail content in the UpdatePanel
        print("\n--- UpdatePanel content ---")
        up_html = await page.evaluate("""() => {
            const el = document.getElementById('ctl00_MainContentPlaceHolder_ucLicenseLookup_UpdtPanelGridLookup');
            if (!el) return 'NOT FOUND';
            return el.innerText.substring(0, 500);
        }""")
        print(up_html[:500])

        # Try clicking the close button with force
        print("\n--- Testing close with .k-window-action:has-text('Close') ---")
        close_sel = page.locator(".k-window-action:has-text('Close')")
        n = await close_sel.count()
        print(f"  Matches: {n}")
        if n > 0:
            # Click with force to bypass overlay
            await close_sel.last.click(force=True)
            await asyncio.sleep(1.5)
            overlay_after = await page.locator(".k-overlay").count()
            print(f"  k-overlay after force-click: {overlay_after} (want 0)")
            kw_visible = await page.locator(".k-window").is_visible()
            print(f"  .k-window still visible: {kw_visible}")

        # Try JS close as fallback
        print("\n--- JS close fallback ---")
        overlay_now = await page.locator(".k-overlay").count()
        if overlay_now > 0:
            print("  Trying JS: $('#window').data('kendoWindow').close()")
            await page.evaluate("$('#window').data('kendoWindow').close()")
            await asyncio.sleep(1)
            overlay_js = await page.locator(".k-overlay").count()
            print(f"  k-overlay after JS close: {overlay_js}")

        await browser.close()
        print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
