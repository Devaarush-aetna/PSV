"""Force-download all 11 WY Google-Sheet CSV boards to PSV/CSVS/.

Tries direct connection (no proxy) first — proxy:9119 blocks docs.google.com
with URLBlockedStorage 403. Falls back to proxy if direct fails.

Usage:
    python download_wy_csvs.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).parents[4]
CSVS_DIR = ROOT / "PSV" / "CSVS"
CSVS_DIR.mkdir(parents=True, exist_ok=True)

# Make `lvs` importable when the script is run directly (not as a package).
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WY_BOARDS = [
    # Selectors match current config.yaml link_selector values (mailing-list versions).
    {
        "source_id": "WY_CHIRO",
        "base_url": "https://chiropractic.wyo.gov/consumers/lookup",
        "link_selector": "a[aria-label='Mailing List of Active and Expired Licenses']",
        "header_row": 3,
    },
    {
        "source_id": "WY_DENTAL",
        "base_url": "https://dental.wyo.gov/public/lookup",
        "link_selector": "a[aria-label='Active Dentists List']",
        "header_row": 4,
    },
    {
        "source_id": "WY_DIETETICS",
        "base_url": "https://dietetics.wyo.gov/consumers/lookup",
        "link_selector": "a[aria-label='Mailing List of Active and Expired Licenses']",
        "header_row": 3,
    },
    {
        "source_id": "WY_MENTAL_HEALTH",
        "base_url": "https://mentalhealth.wyo.gov/public/license-verification",
        "link_selector": "a[aria-label='Active License Verification']",
        "additional_link_selectors": ["a[aria-label='Expired License Verification']"],
        "header_row": 3,
    },
    {
        "source_id": "WY_OPTOMETRY",
        "base_url": "https://optometry.wyo.gov/public/lookup",
        "link_selector": "a:has-text('Mailing List of Active and Expired Licenses')",
        "header_row": 3,
    },
    {
        "source_id": "WY_OT",
        "base_url": "https://occupationaltherapy.wyo.gov/public/license-lookup",
        "link_selector": "text=Mailing List of Active and Expired Licenses",
        "header_row": 3,
    },
    {
        "source_id": "WY_PODIATRY",
        "base_url": "https://podiatry.wyo.gov/public/license-verification",
        "link_selector": "text=Mailing List of Active and Expired Licenses",
        "header_row": 6,
    },
    {
        "source_id": "WY_PSYCH",
        "base_url": "https://psychology.wyo.gov/public/lookup",
        "link_selector": "text=List of Active and Expired Licensees",
        "header_row": 3,
    },
    {
        "source_id": "WY_PT",
        "base_url": "https://physicaltherapy.wyo.gov/public/lookup",
        "link_selector": "text=Mailing List of Active and Expired Licenses",
        "header_row": 3,
    },
    {
        "source_id": "WY_RESP",
        "base_url": "https://respiratory.wyo.gov/public-information/license-lookup",
        "link_selector": "text=Mailing List of Active and Expired Licenses",
        "header_row": 3,
    },
    {
        "source_id": "WY_SPEECH",
        "base_url": "https://speech.wyo.gov/public/license-verification",
        "link_selector": "text=Mailing List of Active and Expired Licenses",
        "header_row": 3,
    },
]


async def _download_one(base_url: str, link_selector: str, source_id: str, proxy_cfg) -> str:
    """Navigate to base_url, find Google Sheet link, return CSV text."""
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            proxy=proxy_cfg,
            ignore_https_errors=True,
            accept_downloads=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()
        try:
            log.info("[%s] Navigating to %s", source_id, base_url)
            await page.goto(base_url, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_selector(link_selector, timeout=30_000)

            locator = page.locator(link_selector).first
            href = (await locator.get_attribute("href")) or ""

            if "docs.google.com/spreadsheets" in href:
                sheet_url = href
            else:
                async with ctx.expect_page() as new_page_info:
                    await locator.click()
                new_page = await new_page_info.value
                await new_page.wait_for_load_state("domcontentloaded", timeout=30_000)
                sheet_url = new_page.url
                log.info("[%s] Google Sheet URL = %s", source_id, sheet_url[:80])

            if "/d/" not in sheet_url:
                raise RuntimeError(f"Not a Google Sheets URL: {sheet_url}")

            if "/edit" in sheet_url:
                export_url = sheet_url.split("/edit")[0] + "/export?format=csv"
            else:
                sheet_id = sheet_url.split("/d/")[1].split("/")[0]
                export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"

            log.info("[%s] Downloading CSV from %s", source_id, export_url[:80])
            # Use expect_download via page.goto — Chromium negotiates NTLM proxy
            # auth automatically via Windows SSO; APIRequestContext cannot.
            try:
                async with page.expect_download(timeout=60_000) as dl_info:
                    try:
                        await page.goto(export_url, wait_until="domcontentloaded", timeout=30_000)
                    except Exception:
                        pass  # navigation exception is expected when a download is triggered
                dl = await dl_info.value
                tmp = await dl.path()
                if not tmp:
                    raise RuntimeError(f"Download path is None: {await dl.failure()}")
                raw = Path(tmp).read_bytes()
                for enc in ("utf-8-sig", "utf-8", "latin-1"):
                    try:
                        return raw.decode(enc)
                    except UnicodeDecodeError:
                        continue
                return raw.decode("latin-1")
            except Exception as dl_exc:
                # Fallback: page may have rendered the CSV as text instead of downloading
                content = await page.content()
                body_text = await page.inner_text("body") if "DOCTYPE" not in content else ""
                if body_text and len(body_text) > 50 and "," in body_text.splitlines()[0]:
                    log.info("[%s] Captured CSV from page body (%d chars)", source_id, len(body_text))
                    return body_text
                raise RuntimeError(f"expect_download failed: {dl_exc}") from dl_exc
        finally:
            await browser.close()


async def download_board(board: dict) -> bool:
    source_id = board["source_id"]
    base_url = board["base_url"]
    link_selector = board["link_selector"]

    # Remove any existing cached files for this board first
    for old in CSVS_DIR.glob(f"{source_id}_????????_????.csv"):
        old.unlink()
        log.info("[%s] Removed old cache: %s", source_id, old.name)

    # Try direct (no proxy) first, then proxy fallback
    from lvs.adapters.scrapers.engine.proxy import get_proxy_config
    proxy_cfg = get_proxy_config()

    attempts = [
        ("direct (no proxy)", None),
        ("proxy:9119", proxy_cfg),
    ]

    additional_selectors = board.get("additional_link_selectors", [])
    header_row = board.get("header_row", 3)

    for attempt_label, pcfg in attempts:
        try:
            log.info("[%s] Attempting download via %s ...", source_id, attempt_label)
            text = await _download_one(base_url, link_selector, source_id, pcfg)

            if additional_selectors:
                import io
                import pandas as pd
                primary_df = pd.read_csv(
                    io.StringIO(text), dtype=str, header=header_row, on_bad_lines="skip"
                )
                primary_df.columns = primary_df.columns.str.strip()
                primary_df = primary_df.fillna("")
                dfs = [primary_df]
                for extra_sel in additional_selectors:
                    try:
                        extra_text = await _download_one(base_url, extra_sel, source_id, pcfg)
                        extra_df = pd.read_csv(
                            io.StringIO(extra_text), dtype=str, header=header_row, on_bad_lines="skip"
                        )
                        extra_df.columns = extra_df.columns.str.strip()
                        extra_df = extra_df.fillna("")
                        dfs.append(extra_df)
                        log.info("[%s] Additional sheet (%s): %d rows", source_id, extra_sel, len(extra_df))
                    except Exception as exc:
                        log.warning("[%s] Additional sheet %r failed: %s", source_id, extra_sel, exc)
                merged_df = pd.concat(dfs, ignore_index=True).fillna("")
                text = merged_df.to_csv(index=False)
                log.info("[%s] Merged %d total rows", source_id, len(merged_df))

            date_tag = datetime.now().strftime("%Y%m%d_%H%M")
            save_path = CSVS_DIR / f"{source_id}_{date_tag}.csv"
            save_path.write_text(text, encoding="utf-8-sig")
            line_count = text.count("\n")
            log.info("[%s] SAVED -> %s  (~%d lines)", source_id, save_path.name, line_count)
            return True
        except Exception as exc:
            log.warning("[%s] %s failed: %s", source_id, attempt_label, exc)

    log.error("[%s] ALL ATTEMPTS FAILED", source_id)
    return False


async def main():
    log.info("Downloading %d WY CSV boards to %s", len(WY_BOARDS), CSVS_DIR)
    results = {}
    for board in WY_BOARDS:
        ok = await download_board(board)
        results[board["source_id"]] = "OK" if ok else "FAILED"

    print("\n=== Results ===")
    passed = [k for k, v in results.items() if v == "OK"]
    failed = [k for k, v in results.items() if v == "FAILED"]
    for k in passed:
        print(f"  PASS  {k}")
    for k in failed:
        print(f"  FAIL  {k}")
    print(f"\n{len(passed)}/{len(WY_BOARDS)} downloaded successfully")

    if failed:
        print("\nFailed boards may need:")
        print("  1. Proxy exception for docs.google.com (Colleague Zone > Unblock Proxy)")
        print("  2. Run from outside corporate network")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
