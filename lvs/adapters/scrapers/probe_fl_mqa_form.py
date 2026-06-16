"""Probe FL_MQA search form — dumps all inputs, selects, and their options."""
import asyncio, os, sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from engine.browser import get_page
from engine.models import TransportConfig

URL = "https://mqa-internet.doh.state.fl.us/MQASearchServices/HealthCareProviders"

transport = TransportConfig(
    browser="chromium",
    headless=True,
    viewport={"width": 1280, "height": 900},
    timeout_ms=60000,
    navigation_timeout_ms=45000,
    rate_limit={"delay_between_requests_ms": 1000, "max_concurrent": 1},
    retry={"max_attempts": 1, "backoff_ms": [1000], "retry_on": ["timeout"]},
    proxy={"enabled": bool(os.environ.get("PROXY"))},
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
)


async def main():
    async with get_page(transport) as page:
        print(f"Navigating to {URL} …")
        await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        try:
            await page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        await asyncio.sleep(2)

        # Dump all <input> fields
        inputs = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('input')).map(el => ({
                tag: 'input',
                type: el.type,
                name: el.name,
                id: el.id,
                placeholder: el.placeholder,
                value: el.value,
            }));
        }""")
        print("\n=== INPUTS ===")
        for i in inputs:
            print(f"  type={i['type']!r:12} name={i['name']!r:45} id={i['id']!r:30} placeholder={i['placeholder']!r}")

        # Dump all <select> fields with their options
        selects = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('select')).map(el => ({
                tag: 'select',
                name: el.name,
                id: el.id,
                options: Array.from(el.options).map(o => ({value: o.value, text: o.text.trim()})).slice(0, 15),
                total_options: el.options.length,
            }));
        }""")
        print("\n=== SELECTS ===")
        for s in selects:
            print(f"\n  name={s['name']!r} id={s['id']!r} ({s['total_options']} options)")
            for o in s['options']:
                print(f"    value={o['value']!r:20} text={o['text']!r}")
            if s['total_options'] > 15:
                print(f"    … ({s['total_options'] - 15} more)")

        # Dump labels
        labels = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('label')).map(el => ({
                for_: el.htmlFor,
                text: el.innerText.trim(),
            }));
        }""")
        print("\n=== LABELS ===")
        for l in labels:
            print(f"  for={l['for_']!r:30} text={l['text']!r}")

        # Save page HTML for reference
        html = await page.content()
        out = "evidence/FL_MQA_search_form.html"
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"\nSaved form HTML to {out}")


asyncio.run(main())
