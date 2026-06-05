"""
Kansas State Board of Pharmacy (KSBOP) — License Search Scraper
URL: https://ksbop.elicensesoftware.com/portal.aspx

Searches by license/permit/registration number or name.
Results show Name, AKA, L/P/R #, City, State, Class, Status.
Clicking a name opens detail page at portal.aspx?xid=...&key=...

Install:
  pip install playwright
  playwright install msedge

Usage:
  python kansas_pharmacy_scraper_v1.py --search-by "License Number" --query "14-05432"
  python kansas_pharmacy_scraper_v1.py --search-by "Name" --query "Smith"

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

BASE_URL     = "https://ksbop.elicensesoftware.com/portal.aspx"
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
    # Confirmed selectors: txtLPRNum (license), txtName (name)
    log.info("Filling '%s' = '%s'", search_by, query)

    if search_by.lower() in ("license number", "license_number", "lpr"):
        page.locator("input#txtLPRNum").fill(query)
        log.info("Filled license number: %s", query)

    elif search_by.lower() in ("name", "last name", "last_name"):
        page.locator("input#txtName").fill(query)
        log.info("Filled name: %s", query)

    else:
        log.error("Unknown search_by: '%s'. Use 'License Number' or 'Name'.", search_by)
        return False

    return True


def click_search(page) -> bool:
    # Confirmed: input#btnSearch type=submit
    btn = page.locator("input#btnSearch").first
    if btn.count() == 0:
        log.error("Search button #btnSearch not found.")
        return False
    btn.click()
    log.info("Clicked Search.")
    return True


def wait_for_results(page) -> bool:
    log.info("Waiting for results...")
    page.wait_for_load_state("networkidle")

    body = page.locator("body").inner_text().lower()
    for phrase in ["no results", "no records", "not found"]:
        if phrase in body:
            log.info("No results — site says: '%s'", phrase)
            return False

    log.info("Results loaded: %s", page.url)
    return True


def get_result_rows(page) -> list:
    # Real data rows have exactly 7 cells: Name|AKA|L/P/R #|City|State|Class|Status
    HEADERS = ["Name", "AKA", "L/P/R #", "City", "State", "Class", "Status"]
    rows    = []
    seen    = set()

    for row in page.locator("table tr").all():
        cells = row.locator("td").all()
        if len(cells) != 7:
            continue
        texts = [c.inner_text().strip() for c in cells]
        if not any(texts) or texts[0] in ("Name", ""):
            continue

        link = row.locator("a[href*='xid=']").first
        href = link.get_attribute("href") if link.count() > 0 else ""

        if href in seen:
            continue
        seen.add(href)

        record = dict(zip(HEADERS, texts))
        record["_detail_href"] = href
        rows.append(record)

    log.info("Found %d result row(s).", len(rows))
    return rows


def scrape_detail_page(page, detail_href: str) -> dict:
    url = detail_href if detail_href.startswith("http") \
        else f"https://ksbop.elicensesoftware.com/{detail_href.lstrip('/')}"

    log.info("Opening detail: %s", url)
    page.goto(url, timeout=WAIT_TIMEOUT)
    page.wait_for_load_state("networkidle")

    detail = {"_source_url": url}

    SKIP = {"General", "Licenses", "Notes", "Kansas Board of Pharmacy",
            "License Portal", "Facility/Provider Information", ""}

    for row in page.locator("table tr").all():
        cells = row.locator("td").all()
        texts = [c.inner_text().strip() for c in cells]

        if len(cells) == 4:
            for i, j in [(0, 1), (2, 3)]:
                k = texts[i].rstrip(":")
                if k and k not in SKIP:
                    detail.setdefault(k, texts[j])

        elif len(cells) == 2:
            k = texts[0].rstrip(":")
            if k and k not in SKIP:
                detail.setdefault(k, texts[1])

        elif len(cells) == 8:
            k1, v1 = texts[0].rstrip(":"), texts[1]
            k2, v2 = texts[2].rstrip(":"), texts[3].split("\n")[0].strip().rstrip("\xa0")
            if k1 and k1 not in SKIP:
                detail.setdefault(k1, v1)
            if k2 and k2 not in SKIP:
                detail.setdefault(k2, v2)

        elif len(cells) == 6:
            clean = [t for t in texts if t and t not in SKIP]
            if len(clean) == 6 and clean[0] not in ("L/P/R #",):
                detail.setdefault("License Record", {
                    "L/P/R #":     clean[0],
                    "Description": clean[1],
                    "Effective":   clean[2],
                    "Issued":      clean[3],
                    "Expires":     clean[4],
                    "Status":      clean[5],
                })

    try:
        detail["_full_page_text"] = page.locator("body").inner_text().strip()
    except Exception:
        pass

    log.info("Detail extracted: %d fields.", len(detail))
    return detail


def run_search(search_by: str, query: str) -> dict:
    output = {
        "state":           "Kansas",
        "board":           "Kansas State Board of Pharmacy (KSBOP)",
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

            # Single exact match — auto-fetch details
            if len(rows) == 1 and rows[0].get("_detail_href"):
                detail = scrape_detail_page(page, rows[0]["_detail_href"])
                detail.update({k: v for k, v in rows[0].items() if not k.startswith("_")})
                output["license_details"].append(detail)

            # Multiple results — fetch detail for each
            else:
                for row in rows:
                    if row.get("_detail_href"):
                        detail = scrape_detail_page(page, row["_detail_href"])
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
    p = argparse.ArgumentParser(description="Kansas State Board of Pharmacy — License Search Scraper",
                                epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--search-by", default="License Number",
                   help="'License Number' or 'Name' (default: License Number)")
    p.add_argument("--query",  required=True)
    p.add_argument("--output", default="kansas_pharmacy_results.json")
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
