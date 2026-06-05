"""
Kansas Behavioral Sciences Regulatory Board (BSRB) — License Search Scraper
URL: https://licensing.ks.gov/Verification_BSRB/Search.aspx

Results list at SearchResults.aspx.
Detail page opens in a new window — captured via Playwright popup handler.

Install:
  pip install playwright
  playwright install msedge

Usage:
  python kansas_bsrb_scraper_v1.py --search-by "License Number" --query "LSCSW 4719"
  python kansas_bsrb_scraper_v1.py --search-by "Last Name" --query "Smith"

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

BASE_URL     = "https://licensing.ks.gov/Verification_BSRB/Search.aspx"
RESULTS_URL  = "https://licensing.ks.gov/Verification_BSRB/SearchResults.aspx"
DETAILS_BASE = "https://licensing.ks.gov/Verification_BSRB"
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
    log.info("Search page loaded.")


def fill_search_form(page, search_by: str, query: str) -> bool:
    # Confirmed selectors from live inspect
    log.info("Filling '%s' = '%s'", search_by, query)

    if search_by.lower() in ("license number", "license_number"):
        page.locator("input#t_web_lookup__license_no").fill(query)
        log.info("Filled license number: %s", query)

    elif search_by.lower() in ("last name", "last_name"):
        page.locator("input#t_web_lookup__last_name").fill(query)
        log.info("Filled last name: %s", query)

    elif search_by.lower() in ("first name", "first_name"):
        page.locator("input#t_web_lookup__first_name").fill(query)
        log.info("Filled first name: %s", query)

    else:
        log.error("Unknown search_by: '%s'", search_by)
        return False

    return True


def click_search(page) -> bool:
    # Confirmed: input#sch_button type=submit
    btn = page.locator("input#sch_button").first
    if btn.count() == 0:
        log.error("Search button #sch_button not found.")
        return False
    btn.click()
    log.info("Clicked Search.")
    return True


def wait_for_results(page) -> bool:
    log.info("Waiting for results...")
    try:
        page.wait_for_url("**/SearchResults.aspx**", timeout=15000)
    except PlaywrightTimeout:
        pass
    page.wait_for_load_state("networkidle")

    body = page.locator("body").inner_text().lower()
    for phrase in ["no results", "no records found", "0 result"]:
        if phrase in body:
            log.info("No results — site says: '%s'", phrase)
            return False

    log.info("Results page: %s", page.url)
    return True


def get_result_rows(page) -> list:
    rows  = []
    seen  = set()

    for row in page.locator("table tr").all():
        link = row.locator("a[href*='Details.aspx']").first
        if link.count() == 0:
            continue

        href = link.get_attribute("href") or ""
        if href in seen:
            continue
        seen.add(href)

        cells = row.locator("td").all()
        texts = [c.inner_text().strip() for c in cells if c.inner_text().strip()]
        if len(texts) < 5:
            continue

        rows.append({
            "Name":       texts[0],
            "License #":  texts[2] if len(texts) > 2 else "",
            "Profession": texts[3] if len(texts) > 3 else "",
            "Lic Type":   texts[4] if len(texts) > 4 else "",
            "Status":     texts[6] if len(texts) > 6 else texts[-1],
            "_detail_href": href,
        })

    log.info("Found %d result row(s).", len(rows))
    return rows


def scrape_detail_page(context, detail_href: str) -> dict:
    # Detail opens in new window — use context.new_page() to stay in same session
    full_url = f"{DETAILS_BASE}/{detail_href.lstrip('/')}"
    log.info("Opening detail: %s", full_url)

    detail_page = context.new_page()
    detail_page.goto(full_url, timeout=WAIT_TIMEOUT)
    detail_page.wait_for_load_state("networkidle")

    detail = {"_source_url": full_url}

    # 2-cell rows: label | value
    for row in detail_page.locator("table tr").all():
        cells = row.locator("td").all()
        texts = [c.inner_text().strip() for c in cells]

        if len(cells) == 2:
            k = texts[0].rstrip(":")
            if k and k not in ("Search", "No address Information"):
                detail.setdefault(k, texts[1])

        elif len(cells) == 6:
            for i in range(0, 6, 2):
                k = texts[i].rstrip(":")
                v = texts[i + 1] if i + 1 < len(texts) else ""
                if k:
                    detail.setdefault(k, v)

        elif len(cells) == 10:
            for i in range(0, 10, 2):
                k = texts[i].rstrip(":")
                v = texts[i + 1] if i + 1 < len(texts) else ""
                if k:
                    detail.setdefault(k, v)

        # Alias rows: ['', 'Alias:', 'value']
        elif len(cells) == 3 and texts[1] == "Alias:":
            n = len([k for k in detail if k.startswith("Alias")])
            detail[f"Alias {n + 1}"] = texts[2]

    try:
        detail["_full_page_text"] = detail_page.locator("body").inner_text().strip()
    except Exception:
        pass

    detail_page.close()
    log.info("Detail extracted: %d fields.", len(detail))
    return detail


def run_search(search_by: str, query: str) -> dict:
    output = {
        "state":           "Kansas",
        "board":           "Kansas Behavioral Sciences Regulatory Board (BSRB)",
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

            rows = get_result_rows(page)

            for row in rows:
                href = row.get("_detail_href", "")
                if href:
                    detail = scrape_detail_page(context, href)
                    detail.update({k: v for k, v in row.items() if not k.startswith("_")})
                    output["license_details"].append(detail)
                else:
                    output["license_details"].append(row)

            output["result_count"] = len(output["license_details"])

        except Exception as e:
            log.error("Scraper error: %s", e)
        finally:
            browser.close()

    return output


def parse_args():
    p = argparse.ArgumentParser(description="Kansas BSRB — License Search Scraper",
                                epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--search-by", default="License Number",
                   help="'License Number', 'Last Name', or 'First Name'")
    p.add_argument("--query",  required=True)
    p.add_argument("--output", default="kansas_bsrb_results.json")
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
