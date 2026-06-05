"""
Kansas DADS — Health Occupations Credentialing (GLSuite) — License Search Scraper
URL: https://ksdadsv7prod.glsuite.us/glsuiteweb/Clients/ksdads/public/verification/LicVerification.aspx

Results at LicVerificationResults.aspx.
Detail at LicenseeDetails.aspx?LicenseID=XXXXX

Install:
  pip install playwright
  playwright install msedge

Usage:
  python kansas_glsuite_scraper_v1.py --search-by "License Number" --query "2720"
  python kansas_glsuite_scraper_v1.py --search-by "Last Name" --query "Smith"

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

BASE_URL     = "https://ksdadsv7prod.glsuite.us/glsuiteweb/Clients/ksdads/public/verification/LicVerification.aspx"
RESULTS_URL  = "LicVerificationResults.aspx"
DETAILS_BASE = "https://ksdadsv7prod.glsuite.us"
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

    if search_by.lower() in ("license number", "license_number", "credential"):
        page.locator("input#waCredentialNumber").fill(query)
        log.info("Filled license/credential number: %s", query)

    elif search_by.lower() in ("last name", "last_name"):
        page.locator("input#waLastName").fill(query)
        log.info("Filled last name: %s", query)

    elif search_by.lower() in ("first name", "first_name"):
        page.locator("input#waFirstName").fill(query)
        log.info("Filled first name: %s", query)

    else:
        log.error("Unknown search_by: '%s'", search_by)
        return False

    return True


def click_search(page) -> bool:
    # Confirmed: input#btnSubmit type=submit value=Search
    btn = page.locator("input#btnSubmit").first
    if btn.count() == 0:
        log.error("Search button #btnSubmit not found.")
        return False
    btn.click()
    log.info("Clicked Search.")
    return True


def wait_for_results(page) -> bool:
    log.info("Waiting for results...")
    try:
        page.wait_for_url(f"**{RESULTS_URL}**", timeout=15000)
    except PlaywrightTimeout:
        pass
    page.wait_for_load_state("networkidle")

    body = page.locator("body").inner_text().lower()
    for phrase in ["no results", "no records", "not found"]:
        if phrase in body:
            log.info("No results — site says: '%s'", phrase)
            return False

    log.info("Results page: %s", page.url)
    return True


def get_result_rows(page) -> list:
    # Confirmed cols: Details | First | Middle | Last | License # | Profession
    rows = []
    for row in page.locator("table tr").all():
        cells = row.locator("td").all()
        if len(cells) not in (6, 7):
            continue
        texts = [c.inner_text().strip() for c in cells]
        if texts[0] != "Details":
            continue

        link = row.locator("a[href*='LicenseeDetails']").first
        lid  = ""
        href = ""
        if link.count() > 0:
            href = link.get_attribute("href") or ""
            if "LicenseID=" in href:
                lid = href.split("LicenseID=")[-1].split("&")[0]

        rows.append({
            "First":      texts[1] if len(texts) > 1 else "",
            "Middle":     texts[2] if len(texts) > 2 else "",
            "Last":       texts[3] if len(texts) > 3 else "",
            "License #":  texts[4] if len(texts) > 4 else "",
            "Profession": texts[5] if len(texts) > 5 else "",
            "_license_id": lid,
            "_detail_href": href,
        })

    log.info("Found %d result row(s).", len(rows))
    return rows


def scrape_detail_page(page, license_id: str) -> dict:
    url = f"{DETAILS_BASE}/GLSuiteWeb/Clients/KSDADS/Public/Verification/LicenseeDetails.aspx?LicenseID={license_id}"
    log.info("Opening detail: %s", url)
    page.goto(url, timeout=WAIT_TIMEOUT)
    page.wait_for_load_state("networkidle")

    detail = {"_source_url": url}

    body  = page.locator("body").inner_text()
    lines = [l.strip() for l in body.splitlines() if l.strip()]

    # Education Requirements is inline: "Education Requirements:\t<value>"
    for line in lines:
        if line.startswith("Education Requirements:"):
            detail["Education Requirements"] = line.split(":", 1)[1].strip()
            break

    # Values appear after Education Requirements line in fixed order
    LABELS = ["Name", "City", "State", "License/Registration Number",
              "Issue Date", "Expiration Date", "Status", "Disciplinary Action"]

    edu_idx = next((i for i, l in enumerate(lines) if l.startswith("Education Requirements:")), -1)
    if edu_idx != -1:
        NOISE  = {"See Page 2 for Formal Education Requirements"}
        values = []
        for line in lines[edu_idx + 1:]:
            if not line:
                continue
            if any(line.startswith(n) for n in NOISE):
                break
            if line.startswith("For further") or line.startswith("Health Occupations"):
                break
            values.append(line)
        for label, value in zip(LABELS, values):
            detail[label] = value

    try:
        detail["_full_page_text"] = body.strip()
    except Exception:
        pass

    log.info("Detail extracted: %d fields.", len(detail))
    return detail


def run_search(search_by: str, query: str) -> dict:
    output = {
        "state":           "Kansas",
        "board":           "Kansas DADS — Health Occupations Credentialing (GLSuite)",
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
                lid = row.get("_license_id", "")
                if lid:
                    detail = scrape_detail_page(page, lid)
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
    p = argparse.ArgumentParser(description="Kansas DADS GLSuite — License Search Scraper",
                                epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--search-by", default="License Number",
                   help="'License Number', 'Last Name', or 'First Name'")
    p.add_argument("--query",  required=True)
    p.add_argument("--output", default="kansas_glsuite_results.json")
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
