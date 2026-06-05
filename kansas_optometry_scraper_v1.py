"""
Kansas Board of Optometry — License Search Scraper
URL: https://www.kansas.gov/ssrv-optometry/search/search.html

Searches by license number or name.
Results list at results.html — click a record to see full details at details.html?id=X.

Install:
  pip install playwright
  playwright install msedge

Usage:
  python kansas_optometry_scraper_v1.py --search-by "License Number" --query "1230-3"
  python kansas_optometry_scraper_v1.py --search-by "Last Name" --query "Smith"

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

BASE_URL    = "https://www.kansas.gov/ssrv-optometry/search/search.html"
RESULTS_URL = "https://www.kansas.gov/ssrv-optometry/search/results.html"
DETAILS_URL = "https://www.kansas.gov/ssrv-optometry/search/details.html"
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
    # Confirmed selectors: licenseNumber, lastName, firstName, city
    log.info("Filling '%s' = '%s'", search_by, query)

    if search_by.lower() in ("license number", "license_number"):
        page.locator("input[name='licenseNumber']").fill(query)
        log.info("Filled license number: %s", query)

    elif search_by.lower() in ("last name", "last_name"):
        page.locator("input[name='lastName']").fill(query)
        log.info("Filled last name: %s", query)

    elif search_by.lower() in ("first name", "first_name"):
        page.locator("input[name='firstName']").fill(query)
        log.info("Filled first name: %s", query)

    else:
        log.error("Unknown search_by: '%s'", search_by)
        return False

    return True


def click_search(page) -> bool:
    # Confirmed: search button is input[name='search'] type=image
    btn = page.locator("input[name='search']").first
    if btn.count() == 0:
        log.error("Search button not found.")
        return False
    btn.click()
    log.info("Clicked Search.")
    return True


def wait_for_results(page) -> bool:
    log.info("Waiting for results...")
    try:
        page.wait_for_url("**/results.html**", timeout=15000)
    except PlaywrightTimeout:
        pass
    page.wait_for_load_state("networkidle")

    body = page.locator("body").inner_text().lower()
    for phrase in ["no results", "no records", "not found", "0 results"]:
        if phrase in body:
            log.info("No results — site says: '%s'", phrase)
            return False

    log.info("Results page: %s", page.url)
    return True


def get_result_rows(page) -> list:
    rows = []
    for row in page.locator("table tr").all():
        cells = row.locator("td").all()
        if not cells:
            continue
        texts = [c.inner_text().strip() for c in cells]
        if not any(texts):
            continue

        link  = row.locator("a[href*='details.html']").first
        href  = link.get_attribute("href") if link.count() > 0 else ""
        pid   = href.split("id=")[-1].split("&")[0] if "id=" in href else ""

        rows.append({
            "Name":           texts[0] if len(texts) > 0 else "",
            "Address":        texts[1] if len(texts) > 1 else "",
            "City":           texts[2] if len(texts) > 2 else "",
            "License Number": texts[3] if len(texts) > 3 else "",
            "_detail_id":     pid,
            "_detail_href":   href,
        })
    log.info("Found %d result row(s).", len(rows))
    return rows


def scrape_detail_page(page, url: str) -> dict:
    log.info("Opening detail: %s", url)
    page.goto(url, timeout=WAIT_TIMEOUT)
    page.wait_for_load_state("networkidle")

    detail = {"_source_url": url}

    # Strategy 1: dt/dd
    try:
        dts = page.locator("dt").all()
        dds = page.locator("dd").all()
        for dt, dd in zip(dts, dds):
            k = dt.inner_text().strip().rstrip(":")
            if k:
                detail.setdefault(k, dd.inner_text().strip())
    except Exception as e:
        log.debug("dt/dd: %s", e)

    # Strategy 2: two-column table
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

    # Strategy 3: four-column table
    if len(detail) <= 1:
        try:
            for row in page.locator("table tr").all():
                cells = row.locator("td, th").all()
                if len(cells) == 4:
                    texts = [c.inner_text().strip() for c in cells]
                    if texts[0]:
                        detail.setdefault(texts[0].rstrip(":"), texts[1])
                    if texts[2]:
                        detail.setdefault(texts[2].rstrip(":"), texts[3])
        except Exception as e:
            log.debug("Four-col table: %s", e)

    # Strategy 4: label + sibling
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

    # Strategy 5: raw lines
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
        "board":           "Kansas Board of Optometry",
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

            has_results = wait_for_results(page)
            if not has_results:
                return output

            rows = get_result_rows(page)

            for row in rows:
                pid = row.get("_detail_id", "")
                if pid:
                    url    = f"{DETAILS_URL}?id={pid}"
                    detail = scrape_detail_page(page, url)
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
    p = argparse.ArgumentParser(description="Kansas Board of Optometry — License Search Scraper",
                                epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--search-by", default="License Number",
                   help="'License Number', 'Last Name', or 'First Name'")
    p.add_argument("--query",  required=True)
    p.add_argument("--output", default="kansas_optometry_results.json")
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
