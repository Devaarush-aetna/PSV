"""
Massachusetts HHS Counselor License Lookup Scraper
Target: https://hhsvgapps03.hhs.state.ma.us/elicensing-pubweb/couns/lookup.htm

Uses Playwright (Chromium) to handle legacy TLS and JavaScript-rendered pages.
"""

import json
import sys
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


LOOKUP_URL = "https://hhsvgapps03.hhs.state.ma.us/elicensing-pubweb/couns/lookup.htm"


def _fill_field(page, candidates: list[str], value: str) -> str:
    """Try each CSS selector candidate and fill the first one found. Returns matched selector."""
    for sel in candidates:
        try:
            el = page.locator(sel).first
            if el.count() and el.is_visible():
                el.fill(value)
                return sel
        except Exception:
            continue
    return ""


def parse_results(page) -> list[dict]:
    """Extract results from the loaded results page."""
    results = []

    # Strategy 1: <table> rows (search within #results div first, then whole page)
    search_roots = [page.query_selector("#results"), page]
    for root in search_roots:
        if root is None:
            continue
        tables = root.query_selector_all("table")
        for table in tables:
            rows = table.query_selector_all("tr")
            if len(rows) < 2:
                continue
            header_cells = rows[0].query_selector_all("th, td")
            headers = [h.inner_text().strip() for h in header_cells]
            if not headers:
                continue
            for row in rows[1:]:
                cells = [td.inner_text().strip() for td in row.query_selector_all("td")]
                if cells:
                    results.append(dict(zip(headers, cells)))
        if results:
            break

    # Strategy 2: definition lists
    if not results:
        for dl in page.query_selector_all("dl"):
            keys = [dt.inner_text().strip() for dt in dl.query_selector_all("dt")]
            vals = [dd.inner_text().strip() for dd in dl.query_selector_all("dd")]
            rec = dict(zip(keys, vals))
            if rec:
                results.append(rec)

    # Strategy 3: .data_group_container divs (BSAS-specific card layout)
    if not results:
        results_div = page.query_selector("#results")
        if results_div:
            cards = results_div.query_selector_all(".data_group_container, .result_item, [class*='result']")
            for card in cards:
                labels = card.query_selector_all("label, .label, dt")
                values = card.query_selector_all("span, .value, dd, input[readonly]")
                rec = {}
                for lbl, val in zip(labels, values):
                    k = lbl.inner_text().strip().rstrip(":")
                    v = val.inner_text().strip() or val.get_attribute("value") or ""
                    if k:
                        rec[k] = v
                if rec:
                    results.append(rec)

    return results


def _filter_by_license(results: list[dict], license_id: str) -> list[dict]:
    """Return only records whose any value contains the license_id (case-insensitive)."""
    needle = license_id.strip().lower()
    return [
        rec for rec in results
        if any(needle in str(v).lower() for v in rec.values())
    ]


def scrape(first_name: str = "John", last_name: str = "Smith", license_id: str = "") -> list[dict]:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--ignore-certificate-errors",
                "--allow-insecure-localhost",
                "--disable-web-security",
            ],
        )
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()

        print(f"[1] Navigating to: {LOOKUP_URL}")
        try:
            page.goto(LOOKUP_URL, timeout=30_000, wait_until="domcontentloaded")
        except PWTimeout:
            print("    [ERROR] Page load timed out — server may be unreachable from this network.")
            browser.close()
            return []
        except Exception as e:
            print(f"    [ERROR] Navigation failed: {e}")
            browser.close()
            return []

        print(f"    Page title: {page.title()!r}")

        # Dump all input names for diagnostics
        inputs = page.query_selector_all("input, select, textarea")
        field_names = [el.get_attribute("name") or el.get_attribute("id") or "(unnamed)" for el in inputs]
        print(f"    Form fields: {field_names}")

        # --- Fill First Name ---
        first_candidates = [
            "input[name*='first' i]",
            "input[id*='first' i]",
            "input[name*='fname' i]",
            "input[id*='fname' i]",
            "input[name*='given' i]",
        ]
        matched = _fill_field(page, first_candidates, first_name)
        if matched:
            print(f"    Filled first name via: {matched}")
        else:
            print("    [WARN] Could not locate first-name field — check field_names above.")

        # --- Fill Last Name ---
        last_candidates = [
            "input[name*='last' i]",
            "input[id*='last' i]",
            "input[name*='lname' i]",
            "input[id*='lname' i]",
            "input[name*='surname' i]",
        ]
        matched = _fill_field(page, last_candidates, last_name)
        if matched:
            print(f"    Filled last name via: {matched}")
        else:
            print("    [WARN] Could not locate last-name field — check field_names above.")

        # --- Submit ---
        license_note = f", license_id='{license_id}'" if license_id else ""
        print(f"\n[2] Submitting search: first='{first_name}', last='{last_name}'{license_note}")
        try:
            submit_btn = page.locator(
                "input[type='submit'], button[type='submit'], button:has-text('Search'), "
                "input[value*='Search' i], input[value*='Lookup' i], input[value*='Find' i]"
            ).first
            submit_btn.click()
            page.wait_for_load_state("domcontentloaded", timeout=20_000)
        except Exception as e:
            print(f"    [ERROR] Submit failed: {e}")
            page.screenshot(path="error_screenshot.png")
            print("    Screenshot saved to 'error_screenshot.png'")
            browser.close()
            return []

        print(f"    Result page URL: {page.url}")
        # Check for the specific no-results indicator used by this site
        no_items_el = page.query_selector(".no_items_message")
        if no_items_el:
            msg = no_items_el.inner_text().strip()
            print(f"\n[RESULT] No matching records found.\n         Server message: {msg}")
            browser.close()
            return []

        # Generic fallback check
        page_text = page.inner_text("body").lower()
        no_result_phrases = ["no results", "no records", "no match", "not found", "0 results", "no data"]
        if any(p in page_text for p in no_result_phrases):
            print("\n[RESULT] No matching records found for this name.")
            browser.close()
            return []

        results = parse_results(page)

        if results and license_id:
            pre_filter = len(results)
            results = _filter_by_license(results, license_id)
            print(f"    License ID filter '{license_id}': {pre_filter} → {len(results)} record(s)")

        if not results:
            print("\n[WARN] Page loaded but no table/list results were parsed.")
            print("       Saving raw HTML to 'response_raw.html' for inspection.")
            with open("response_raw.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            page.screenshot(path="result_screenshot.png")
            print("       Screenshot saved to 'result_screenshot.png'")

        browser.close()
        return results


def main():
    first_name = input("First Name : ").strip()
    last_name  = input("Last Name  : ").strip()
    license_id = input("License ID (leave blank to skip): ").strip()

    if not first_name and not last_name:
        print("[ERROR] At least one of First Name or Last Name is required.")
        sys.exit(1)

    results = scrape(first_name, last_name, license_id)

    if results:
        print(f"\n[RESULT] Found {len(results)} record(s):\n")
        for i, rec in enumerate(results, 1):
            print(f"  --- Record {i} ---")
            for key, val in rec.items():
                print(f"  {key}: {val}")
            print()

        out_file = "counselor_results.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"[SAVED] Results written to '{out_file}'")
    else:
        print("\n[DONE] No results to save.")
        sys.exit(0)


if __name__ == "__main__":
    main()
