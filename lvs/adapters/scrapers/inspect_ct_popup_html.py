"""Dump CT eLicense popup HTML to find field element IDs.

Run:
    python inspect_ct_popup_html.py
"""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')


async def main():
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_default_timeout(30000)

        await page.goto("https://elicense.ct.gov/Lookup/LicenseLookup.aspx", wait_until="networkidle")
        await page.fill(
            "#ctl00_MainContentPlaceHolder_ucLicenseLookup_ctl03_tbLastName_Contact",
            "DeAtley"
        )
        await page.click("#ctl00_MainContentPlaceHolder_ucLicenseLookup_btnLookup")
        await asyncio.sleep(5)

        btns = page.locator("a:has-text('Details'), button:has-text('Details')")
        count = await btns.count()
        print(f"Found {count} Details button(s)")
        if count == 0:
            await browser.close()
            return

        await btns.first.click()
        await asyncio.sleep(4)

        # Get FULL HTML of the popup
        win = page.locator("[data-role='window']")
        n = await win.count()
        print(f"data-role=window elements: {n}")

        if n > 0:
            html = await win.first.inner_html()
            text = await win.first.inner_text()
            print(f"HTML length: {len(html)}")
            print(f"Text length: {len(text)}")
            print("\n--- FULL TEXT ---")
            print(text)
            print("\n--- HTML (sections with License data) ---")
            # Find the section containing 'License Type'
            idx = html.find('License Type')
            if idx > -1:
                print(f"Found 'License Type' at pos {idx}")
                print(html[max(0, idx-500):idx+3000])
            else:
                # Print all HTML
                print(html[:6000])

        # Also use JS to find elements with visible text matching field labels
        print("\n--- JS: find all leaf elements with 'License' in text ---")
        field_els = await page.evaluate("""() => {
            const results = [];
            const labels = ['License Type', 'License Number', 'Expiration Date', 'License Status', 'Granted Date', 'License Name', 'Name'];
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
            while (walker.nextNode()) {
                const el = walker.currentNode;
                const text = (el.textContent || '').trim();
                if (labels.includes(text) && el.children.length === 0) {
                    const ns = el.nextElementSibling;
                    const parent = el.parentElement;
                    results.push({
                        label: text,
                        tag: el.tagName,
                        id: el.id,
                        cls: el.className.substring(0, 50),
                        parent_tag: parent ? parent.tagName : '',
                        parent_id: parent ? parent.id : '',
                        parent_cls: parent ? parent.className.substring(0, 50) : '',
                        next_sib: ns ? (ns.tagName + '#' + ns.id + '.' + ns.className.substring(0, 20) + ':' + ns.textContent.trim().substring(0, 30)) : 'none'
                    });
                }
            }
            return results;
        }""")
        for el in field_els:
            print(f"  '{el['label']}': <{el['tag']} id={el['id']!r} cls={el['cls']!r}>")
            print(f"    parent: <{el['parent_tag']} id={el['parent_id']!r} cls={el['parent_cls']!r}>")
            print(f"    next_sib: {el['next_sib']!r}")

        await browser.close()
        print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
