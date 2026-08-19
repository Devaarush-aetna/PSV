"""
Extract PA_PALS JavaScript source to find:
1. What getAssetDetail() does
2. What the 'searchresult' state controller does
3. The real detail API endpoint
"""
import asyncio
import re
from playwright.async_api import async_playwright

URL = "https://www.pals.pa.gov/#!/page/search"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Collect all script bodies from network
        script_bodies: dict[str, str] = {}

        async def on_response(response):
            url = response.url
            ct = response.headers.get("content-type", "")
            if "javascript" in ct or url.endswith(".js"):
                try:
                    body = await response.body()
                    text = body.decode("utf-8", errors="ignore")
                    script_bodies[url] = text
                except Exception:
                    pass

        page.on("response", on_response)

        print(f"Loading {URL}...")
        await page.goto(URL)
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)

        print(f"\nCaptured {len(script_bodies)} JS files:")
        for url in script_bodies:
            print(f"  {len(script_bodies[url]):>8,} bytes  {url.split('/')[-1][:60]}")

        print("\n" + "=" * 70)
        print("Searching for 'getAssetDetail'...")
        print("=" * 70)

        for url, src in script_bodies.items():
            if "getAssetDetail" not in src:
                continue
            fname = url.split("/")[-1]
            print(f"\n[FILE] {fname}")
            # Extract surrounding context for each occurrence
            for m in re.finditer(r"getAssetDetail", src):
                start = max(0, m.start() - 300)
                end = min(len(src), m.end() + 800)
                snippet = src[start:end]
                print(f"\n  ...{snippet}...")

        print("\n" + "=" * 70)
        print("Searching for 'searchresult' state definition...")
        print("=" * 70)
        for url, src in script_bodies.items():
            if "searchresult" not in src.lower():
                continue
            fname = url.split("/")[-1]
            print(f"\n[FILE] {fname}")
            for m in re.finditer(r"searchresult", src, re.IGNORECASE):
                start = max(0, m.start() - 200)
                end = min(len(src), m.end() + 600)
                snippet = src[start:end]
                print(f"\n  ...{snippet}...")
                if end - start > 1000:
                    break

        print("\n" + "=" * 70)
        print("Searching for API patterns (GetProv / GetLicense / GetDetail / expir)...")
        print("=" * 70)
        patterns = [
            r"api/Search/Get\w+",
            r"api/\w+/Get\w+",
            r"GetProvider",
            r"GetLicense",
            r"GetDetail",
            r"expir",
            r"ExpirationDate",
            r"expiration",
            r"/api/[A-Za-z/]+",
        ]
        found_apis = set()
        for url, src in script_bodies.items():
            for pat in patterns:
                for m in re.finditer(pat, src, re.IGNORECASE):
                    found_apis.add(m.group(0))

        print("All API path fragments found:")
        for api in sorted(found_apis):
            if "/api/" in api.lower() or api.startswith("api/"):
                print(f"  {api}")

        print("\nAll 'Get*' function/path mentions:")
        for api in sorted(found_apis):
            if api.startswith("Get"):
                print(f"  {api}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
