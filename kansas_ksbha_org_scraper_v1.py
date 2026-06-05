"""
Kansas Behavioral Health Authority (KSBHA.org) — License Search Scraper
URL: https://www.ksbha.org/

NOTE: This is a membership/advocacy organisation — not a regulatory board.
It may not have a license verification form.
Selectors have not been confirmed. Run with --headed to inspect.

Install:
  pip install playwright
  playwright install msedge

Usage:
  python kansas_ksbha_org_scraper_v1.py --search-by "License Number" --query "12345"
  python kansas_ksbha_org_scraper_v1.py --search-by "Last Name" --query "Smith"

Proxy (optional):
  set PROXY_NID=your_nid
  set PROXY_PASSWORD=your_password
"""

import argparse
import json
import logging
import os
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

BASE_URL     = "https://www.ksbha.org/"
WAIT_TIMEOUT = 30000

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def build_browser(p):
    proxy = _build_proxy()
    browser = p.chromium.launch(channel="msedge", headless=False,
                                proxy=proxy if proxy else None)
    context = browser.new_context(ignore_https_errors=True)
    page    = context.new_page()
    log.info("Browser launched.")
    return browser, context, page


def _build_proxy():
    nid = os.environ.get("PROXY_NID", "")
    pwd = os.environ.get("PROXY_PASSWORD", "")
    if nid and pwd:
        return {"server": f"http://{nid}:{pwd}@proxy.aetna.com:9119"}
    return None


def open_search_page(page):
    log.info("Loading %s", BASE_URL)
    page.goto(BASE_URL, timeout=WAIT_TIMEOUT)
    page.wait_for_load_state("networkidle")

    # Try to find a provider directory or verification link
    verify_link = page.locator(
        "a:has-text('License Verification'), a:has-text('Find a Provider'), "
        "a:has-text('Provider Directory'), a:has-text('Search'), "
        "a:has-text('Verify'), a:has-text('Directory')"
    ).first
    if verify_link.count() > 0 and verify_link.is_visible():
        log.info("Clicking link: %s", verify_link.inner_text().strip())
        verify_link.click()
        page.wait_for_load_state("networkidle")
    else:
        log.warning("No search link found on homepage.")

    log.info("Current URL: %s", page.url)

    log.info("=== FIELDS ON PAGE (update selectors if needed) ===")
    for inp in page.locator("input, select").all():
        log.info("  id=%s name=%s type=%s placeholder=%s",
                 inp.get_attribute("id"), inp.get_attribute("name"),
                 inp.get_attribute("type"), inp.get_attribute("placeholder"))


def fill_search_form(page, search_by: str, query: str) -> bool:
    log.info("Filling '%s' = '%s'", search_by, query)

    if search_by.lower() in ("license number", "license_number"):
        field = page.locator(
            "input[id*='icense'], input[name*='icense'], input[type='search'], input[type='text']"
        ).first
        if field.count() == 0:
            log.error("License number field not found.")
            return False
        field.fill(query)
        log.info("Filled license number: %s", query)

    elif search_by.lower() in ("last name", "last_name", "name"):
        field = page.locator(
            "input[id*='ast'], input[name*='ast'], input[name='s'], input[type='search'], input[type='text']"
        ).first
        if field.count() == 0:
            log.error("Name field not found.")
            return False
        field.fill(query)
        log.info("Filled name: %s", query)

    else:
        log.error("Unknown search_by: '%s'", search_by)
        return False

    return True


def click_search(page) -> bool:
    btn = page.locator(
        "button[type='submit'], input[type='submit'], "
        "button:has-text('Search'), input[value='Search'], "
        "button:has-text('Find')"
    ).first
    if btn.count() == 0:
        log.error("Search button not found.")
        return False
    btn.click()
    log.info("Clicked Search.")
    return True


def wait_for_results(page) -> bool:
    log.info("Waiting for results...")
    page.wait_for_load_state("networkidle")

    body = page.locator("body").inner_text().lower()
    for phrase in ["no results", "nothing found", "no records", "not found"]:
        if phrase in body:
            log.info("No results — site says: '%s'", phrase)
            return False

    log.info("Results page: %s", page.url)
    return True


def scrape_detail_page(page, url: str) -> dict:
    log.info("Extracting from: %s", url)
    detail = {"_source_url": url}

    try:
        dts = page.locator("dt").all()
        dds = page.locator("dd").all()
        for dt, dd in zip(dts, dds):
            k = dt.inner_text().strip().rstrip(":")
            if k:
                detail.setdefault(k, dd.inner_text().strip())
    except Exception as e:
        log.debug("dt/dd: %s", e)

    if len(detail) <= 1:
        try:
            for row in page.locator("table tr").all():
                cells = row.locator("td, th").all()
                if len(cells) == 2:
                    k = cells[0].inner_text().strip().rstrip(":")
                    if k:
                        detail.setdefault(k, cells[1].inner_text().strip())
        except Exception as e:
            log.debug("Two-col table: %s", e)

    if len(detail) <= 1:
        try:
            for lbl in page.locator("label, strong, b").all():
                k = lbl.inner_text().strip().rstrip(":")
                if not k or len(k) > 60:
                    continue
                try:
                    v = lbl.evaluate(
                        "el => (el.nextElementSibling || el.parentElement?.nextElementSibling)"
                        "?.innerText?.trim() || ''"
                    )
                    if v:
                        detail.setdefault(k, v)
                except Exception:
                    pass
        except Exception as e:
            log.debug("Label sibling: %s", e)

    if len(detail) <= 1:
        try:
            lines = [l.strip() for l in page.locator("body").inner_text().splitlines() if l.strip()]
            for i, line in enumerate(lines):
                if line.endswith(":") and i + 1 < len(lines):
                    detail.setdefault(line.rstrip(":"), lines[i + 1])
        except Exception as e:
            log.debug("Raw lines: %s", e)

    try:
        detail["_full_page_text"] = page.locator("body").inner_text().strip()
    except Exception:
        pass

    log.info("Detail extracted: %d fields.", len(detail))
    return detail


def run_search(search_by: str, query: str) -> dict:
    output = {
        "state":           "Kansas",
        "board":           "Kansas Behavioral Health Authority (KSBHA.org)",
        "search_by":       search_by,
        "query":           query,
        "result_count":    0,
        "license_details": [],
        "scraped_at":      datetime.utcnow().isoformat() + "Z",
        "source_url":      BASE_URL,
    }

    with sync_playwright() as p:
        browser, context, page = build_browser(p)
        try:
            open_search_page(page)

            if not fill_search_form(page, search_by, query):
                return output
            if not click_search(page):
                return output

            if not wait_for_results(page):
                return output

            detail = scrape_detail_page(page, page.url)
            output["license_details"].append(detail)
            output["result_count"] = 1

        except Exception as e:
            log.error("Scraper error: %s", e)
        finally:
            browser.close()

    return output


def parse_args():
    p = argparse.ArgumentParser(description="Kansas KSBHA.org — Search Scraper",
                                epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--search-by", default="Name",
                   help="'License Number' or 'Name'")
    p.add_argument("--query",  required=True)
    p.add_argument("--output", default="kansas_ksbha_org_results.json")
    return p.parse_args()


def main():
    args = parse_args()
    data = run_search(args.search_by, args.query)

    base, ext   = os.path.splitext(args.output)
    ts          = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = f"{base}_{ts}{ext}"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"\nDone. Saved to: {output_path}")
    print(f"  Records found : {data['result_count']}")
    if data["license_details"]:
        print("\nFirst record preview:")
        for k, v in list(data["license_details"][0].items())[:10]:
            if not k.startswith("_full_page"):
                print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
