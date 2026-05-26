"""
Massachusetts Health License Scraper
URL: https://url.usb.m.mimecastprotect.com/s/Kq4DCB1G7jH8Z8mMzx4fNi5U2tgPK?domain=checkahealthlicense.mass.gov

Uses undetected-chromedriver to bypass bot detection.
The results table uses AG Grid (div.ag-row / div.ag-cell).

Install:
  pip install selenium undetected-chromedriver webdriver-manager

Search by name:
  python mass_license_scraper.py --mode name --first John --last Smith

Search by license number:
  python mass_license_scraper.py --mode number --license RN2266916

With filters:
  python mass_license_scraper.py --mode name --first John --last Smith \
      --board "Board of Registration in Nursing" \
      --output results.json
"""

import argparse
import os
import json
import time
import logging
import re
from datetime import datetime
from typing import Optional

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException,
    StaleElementReferenceException, ElementNotInteractableException,
)

BASE_URL      = "https://url.usb.m.mimecastprotect.com/s/Kq4DCB1G7jH8Z8mMzx4fNi5U2tgPK?domain=checkahealthlicense.mass.gov"
CHROME_VER    = 148   # change if your Chrome version differs
WAIT_TIMEOUT  = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ── Expected column order from AG Grid header (confirmed from debug) ────────
EXPECTED_COLUMNS = [
    "License Number",
    "License Type",
    "License Status",
    "First Name",
    "Middle Name",
    "Last Name",
    "Suffix",
    "Organization Name",
    "Address",
    "Issue Date",
    "Last Issue/Renewal Date",
    "Expiration Date",
]


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def build_driver() -> uc.Chrome:
    options = uc.ChromeOptions()
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-popup-blocking")
    log.info("Starting undetected Chrome (version=%d)...", CHROME_VER)
    driver = uc.Chrome(options=options, version_main=CHROME_VER)
    return driver


def wait_until(driver, condition, timeout=WAIT_TIMEOUT):
    return WebDriverWait(driver, timeout).until(condition)


# ---------------------------------------------------------------------------
# Page load
# ---------------------------------------------------------------------------

def open_search_page(driver):
    log.info("Loading %s", BASE_URL)
    driver.get(BASE_URL)
    # Wait until Angular finishes — "Loading..." disappears
    wait_until(driver,
        lambda d: "Loading..." not in d.find_element(By.TAG_NAME, "body").text)
    time.sleep(1.5)
    log.info("Page ready.")


# ---------------------------------------------------------------------------
# Form helpers
# ---------------------------------------------------------------------------

def select_mode(driver, mode: str):
    """
    Click the correct radio button for name or number search.

    From debug_radio.py output we know:
      - Radio inputs have class 'mdc-radio__native-control' and are NOT displayed
      - Their labels ARE displayed with text 'Licensee Name' / 'License Number'
      - Label[for='mat-radio-1-input'] text='License Number' triggers the switch
      - After clicking, Angular replaces form inputs within ~1 second

    The key insight: we must click the label, but also dispatch Angular's
    (change) event on the hidden native input to trigger form re-render.
    """
    target_text = "Licensee Name" if mode == "name" else "License Number"
    target_val  = "BY_LICENSEE_NAME" if mode == "name" else "BY_LICENSE_NUMBER"
    log.info("Selecting mode: %s", mode)

    # Find the radio input by value attribute (most reliable)
    radios = driver.find_elements(By.CSS_SELECTOR, "input[type='radio']")
    target_radio = None
    for r in radios:
        if r.get_attribute("value") == target_val:
            target_radio = r
            break

    # Fallback: by position (name=0, number=1)
    if not target_radio:
        idx = 0 if mode == "name" else 1
        if len(radios) > idx:
            target_radio = radios[idx]

    if not target_radio:
        log.error("Could not find radio button for mode=%s", mode)
        return

    radio_id = target_radio.get_attribute("id") or ""
    log.info("Target radio: id='%s' value='%s'", radio_id, target_radio.get_attribute("value"))

    # Step 1: click the visible label (user-facing interaction)
    if radio_id:
        try:
            lbl = driver.find_element(By.CSS_SELECTOR, f"label[for='{radio_id}']")
            driver.execute_script("arguments[0].click();", lbl)
            log.info("Clicked label[for='%s']", radio_id)
        except NoSuchElementException:
            pass

    # Step 2: also fire events directly on the hidden native input
    # Angular listens for 'change' and 'click' on the native control
    driver.execute_script("""
        var el = arguments[0];
        el.checked = true;
        el.dispatchEvent(new MouseEvent('click',  {bubbles: true, cancelable: true}));
        el.dispatchEvent(new Event('change',       {bubbles: true}));
        el.dispatchEvent(new Event('input',        {bubbles: true}));
    """, target_radio)
    log.info("Dispatched click/change/input events on radio input")

    # Step 3: also click the mat-radio-button wrapper if present
    try:
        mat_radios = driver.find_elements(By.TAG_NAME, "mat-radio-button")
        idx = 0 if mode == "name" else 1
        if len(mat_radios) > idx:
            driver.execute_script("arguments[0].click();", mat_radios[idx])
            log.info("Also clicked mat-radio-button[%d]", idx)
    except Exception:
        pass

    # Wait for Angular to re-render the form
    time.sleep(2)

    # Verify the switch worked by checking which inputs are now visible
    text_inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='text']")
    visible_ids = [i.get_attribute("id") for i in text_inputs if i.is_displayed()]
    log.info("Visible text inputs after mode switch: %s", visible_ids)


def set_select(driver, select_id: str, value_text: str):
    """Set a <select> by its id, with a wait."""
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, select_id)))
        sel = driver.find_element(By.ID, select_id)
        Select(sel).select_by_visible_text(value_text)
        log.info("Set select #%s = '%s'", select_id, value_text)
    except Exception as e:
        log.warning("Could not set select #%s: %s", select_id, e)


def fill(driver, input_id: str, value: str):
    """Type into an input — wait for it to appear first."""
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, input_id)))
        el = driver.find_element(By.ID, input_id)
        el.clear()
        el.send_keys(value)
        log.info("Filled #%s = '%s'", input_id, value)
    except Exception:
        # Fallback: find visible text inputs by position
        inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='text']")
        visible = [i for i in inputs if i.is_displayed() and i.is_enabled()]
        # Match by known label text
        label_map = {
            "licensee-first-name-or-organization-input": ("first", 0),
            "licensee-last-name-input": ("last", 1),
        }
        pos_info = label_map.get(input_id, ("", 0))
        idx = pos_info[1]
        if len(visible) > idx:
            visible[idx].clear()
            visible[idx].send_keys(value)
            log.info("Filled input[%d] (fallback) = '%s'", idx, value)
        else:
            log.warning("Could not fill input #%s", input_id)


def click_search(driver):
    """Click the Search button (confirmed class: button.search-button)."""
    try:
        btn = driver.find_element(By.CSS_SELECTOR, "button.search-button")
    except NoSuchElementException:
        # Fallback: any button whose text is exactly "Search"
        buttons = driver.find_elements(By.TAG_NAME, "button")
        btn = next((b for b in buttons
                    if b.text.strip().lower() == "search"
                    and "commonwealth" not in b.text.lower()), None)
        if not btn:
            raise Exception("Search button not found on page")
    driver.execute_script("arguments[0].click();", btn)
    log.info("Search clicked.")


# ---------------------------------------------------------------------------
# Wait for AG Grid results
# ---------------------------------------------------------------------------

def wait_for_ag_grid(driver):
    """
    Wait for AG Grid data rows to appear.
    Rows: div.ag-row  (confirmed from debug output)
    Header: div.ag-header-row
    """
    log.info("Waiting for AG Grid results...")
    try:
        wait_until(driver,
            lambda d: len(d.find_elements(
                By.CSS_SELECTOR, "div.ag-row:not(.ag-header-row)")) > 0)
        log.info("AG Grid results appeared.")
    except TimeoutException:
        log.warning("Timed out waiting for AG Grid rows.")
    time.sleep(1)


# ---------------------------------------------------------------------------
# Parse AG Grid headers
# ---------------------------------------------------------------------------

def get_ag_headers(driver) -> list:
    """
    Read column headers from the AG Grid header row.
    Each header cell: div.ag-header-cell  with  div.ag-header-cell-text inside.
    Falls back to EXPECTED_COLUMNS if headers can't be read.
    """
    headers = []
    try:
        header_cells = driver.find_elements(
            By.CSS_SELECTOR, "div.ag-header-cell")
        for cell in header_cells:
            try:
                text_el = cell.find_element(By.CSS_SELECTOR, "div.ag-header-cell-text")
                text = text_el.text.strip()
            except NoSuchElementException:
                text = cell.text.strip()
            if text:
                headers.append(text)
        log.info("AG Grid headers (%d): %s", len(headers), headers)
    except Exception as e:
        log.warning("Could not read AG Grid headers: %s", e)

    if not headers:
        log.info("Using expected column list as fallback.")
        headers = EXPECTED_COLUMNS.copy()

    return headers


# ---------------------------------------------------------------------------
# Parse AG Grid rows
# ---------------------------------------------------------------------------

def parse_ag_grid(driver) -> list:
    """
    Parse all visible AG Grid data rows.

    Structure confirmed from debug:
      Row:  div[role='row']  with class ag-row / ag-row-even / ag-row-odd
      Cell: div[role='gridcell']  with class ag-cell

    AG Grid virtualises rows — only visible rows exist in the DOM.
    We scroll down to load all rows.
    """
    wait_for_ag_grid(driver)
    headers = get_ag_headers(driver)

    results = []
    seen_license_numbers = set()

    # AG Grid virtualises — scroll to load all rows
    log.info("Scrolling through AG Grid to load all rows...")
    scroll_container = None
    for sel in ["div.ag-body-viewport", "div.ag-center-cols-viewport",
                "div.ag-body-clipper"]:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            scroll_container = els[0]
            break

    last_row_count = 0
    for scroll_attempt in range(30):
        # Collect current rows
        rows = driver.find_elements(
            By.CSS_SELECTOR, "div[role='row'].ag-row")
        if not rows:
            # Try without role filter
            rows = driver.find_elements(
                By.CSS_SELECTOR,
                "div.ag-row:not(.ag-header-row):not(.ag-row-group)")

        for row in rows:
            cells = row.find_elements(
                By.CSS_SELECTOR, "div[role='gridcell'], div.ag-cell")
            if not cells:
                continue

            row_data = {}
            for i, cell in enumerate(cells):
                key = headers[i] if i < len(headers) else f"col_{i}"
                val = cell.text.strip()
                row_data[key] = val

                # Capture hyperlink URL on License Number cell
                if i == 0:
                    try:
                        link = cell.find_element(By.TAG_NAME, "a")
                        href = link.get_attribute("href") or ""
                        if href:
                            row_data["License Number_url"] = href
                    except NoSuchElementException:
                        pass

            license_num = row_data.get("License Number", "").strip()
            if license_num and license_num not in seen_license_numbers:
                seen_license_numbers.add(license_num)
                results.append(row_data)

        current_count = len(results)
        log.info("Scroll %d: %d unique rows collected", scroll_attempt + 1, current_count)

        # If no new rows after scrolling, we're done
        if current_count == last_row_count and scroll_attempt > 1:
            break
        last_row_count = current_count

        # Scroll down in the grid
        if scroll_container:
            driver.execute_script(
                "arguments[0].scrollTop += 500;", scroll_container)
        else:
            driver.execute_script("window.scrollBy(0, 500);")
        time.sleep(0.5)

    log.info("Total rows captured: %d", len(results))
    return results


# ---------------------------------------------------------------------------
# License detail page
# ---------------------------------------------------------------------------

def scrape_license_detail(driver, url: str) -> dict:
    """
    Open the license detail page and extract all fields.
    The detail page likely also uses AG Grid or a simple key-value layout.
    """
    log.info("Opening detail: %s", url)
    driver.get(url)
    # Wait for page to render
    try:
        wait_until(driver,
            lambda d: "Loading..." not in d.find_element(By.TAG_NAME, "body").text)
    except TimeoutException:
        pass
    time.sleep(2)

    detail = {"_source_url": url}

    # Strategy 1: AG Grid on detail page
    try:
        rows = driver.find_elements(By.CSS_SELECTOR,
            "div[role='row'].ag-row, div.ag-row:not(.ag-header-row)")
        if rows:
            headers = get_ag_headers(driver)
            for row in rows:
                cells = row.find_elements(By.CSS_SELECTOR,
                    "div[role='gridcell'], div.ag-cell")
                row_data = {}
                for i, cell in enumerate(cells):
                    key = headers[i] if i < len(headers) else f"col_{i}"
                    row_data[key] = cell.text.strip()
                if any(row_data.values()):
                    # 2-col layout = label/value pair
                    vals = list(row_data.values())
                    if len(vals) == 2:
                        detail.setdefault(vals[0], vals[1])
                    else:
                        detail.update({k: v for k, v in row_data.items() if v})
    except Exception as e:
        log.debug("Detail AG Grid: %s", e)

    # Strategy 2: definition list
    try:
        for dt, dd in zip(
            driver.find_elements(By.TAG_NAME, "dt"),
            driver.find_elements(By.TAG_NAME, "dd")
        ):
            k = dt.text.strip().rstrip(":")
            v = dd.text.strip()
            if k:
                detail.setdefault(k, v)
    except Exception:
        pass

    # Strategy 3: label pairs
    try:
        for lbl in driver.find_elements(By.TAG_NAME, "label"):
            k = lbl.text.strip().rstrip(":")
            if not k:
                continue
            for_id = lbl.get_attribute("for")
            if for_id:
                try:
                    el = driver.find_element(By.ID, for_id)
                    detail.setdefault(k, el.text.strip() or el.get_attribute("value") or "")
                    continue
                except NoSuchElementException:
                    pass
            try:
                detail.setdefault(k, lbl.find_element(
                    By.XPATH, "following-sibling::*[1]").text.strip())
            except NoSuchElementException:
                pass
    except Exception:
        pass

    # Strategy 4: full visible body text (always capture)
    try:
        detail["_full_page_text"] = driver.find_element(
            By.TAG_NAME, "body").text.strip()
    except Exception:
        pass

    log.info("Detail: %d fields captured", len(detail))
    return detail


# ---------------------------------------------------------------------------
# Search orchestration
# ---------------------------------------------------------------------------

def search_and_scrape(
    driver,
    mode: str,
    first_name: str = "",
    last_name: str = "",
    license_number: str = "",
    board: str = "",
    license_type: str = "",
    fetch_details: bool = True,
) -> dict:

    open_search_page(driver)
    select_mode(driver, mode)

    if mode == "name":
        if board:
            set_select(driver, "search-license-board", board)
            time.sleep(0.5)
        if license_type:
            set_select(driver, "search-license-name-and-type", license_type)
            time.sleep(0.5)
        if first_name:
            fill(driver, "licensee-first-name-or-organization-input", first_name)
        if last_name:
            fill(driver, "licensee-last-name-input", last_name)

    else:  # number mode
        # After clicking License Number radio, Angular re-renders the form.
        # Confirmed input id: 'licensee-license-number-input'
        # We try that first, then fall back to any single visible text input.
        log.info("Waiting for license number input (id=licensee-license-number-input)...")

        inp = None

        # Strategy 1: wait for the exact known ID
        try:
            WebDriverWait(driver, 8).until(
                EC.presence_of_element_located(
                    (By.ID, "licensee-license-number-input"))
            )
            inp = driver.find_element(By.ID, "licensee-license-number-input")
            log.info("Found input by id: licensee-license-number-input")
        except (TimeoutException, NoSuchElementException):
            log.warning("Exact ID not found — trying fallbacks")

        # Strategy 2: any visible text input whose id contains 'number'
        if not inp:
            all_text = driver.find_elements(By.CSS_SELECTOR, "input[type='text']")
            for i in all_text:
                iid = (i.get_attribute("id") or "").lower()
                if "number" in iid and i.is_displayed():
                    inp = i
                    log.info("Found input by id containing 'number': %s", i.get_attribute("id"))
                    break

        # Strategy 3: only one visible text input left on page
        if not inp:
            visible = [i for i in driver.find_elements(By.CSS_SELECTOR, "input[type='text']")
                      if i.is_displayed() and i.is_enabled()]
            log.info("Visible text inputs: %d → %s",
                     len(visible), [i.get_attribute("id") for i in visible])
            if len(visible) == 1:
                inp = visible[0]
                log.info("Using sole visible input: id='%s'", inp.get_attribute("id"))
            elif len(visible) > 1:
                # Pick the one that is NOT a name field
                for i in visible:
                    iid = (i.get_attribute("id") or "").lower()
                    if "first" not in iid and "last" not in iid:
                        inp = i
                        log.info("Picked non-name input: id='%s'", i.get_attribute("id"))
                        break

        if inp:
            inp.clear()
            inp.send_keys(license_number)
            log.info("Entered license number: %s", license_number)
        else:
            log.error("FAILED to find license number input — cannot proceed")
            raise Exception("License number input not found after radio switch")

    click_search(driver)
    time.sleep(1)

    results = parse_ag_grid(driver)

    output = {
        "search_mode": mode,
        "search_params": {
            "first_name": first_name,
            "last_name": last_name,
            "license_number": license_number,
            "board": board,
            "license_type": license_type,
        },
        "result_count": len(results),
        "results": results,
        "license_details": [],
        "scraped_at": datetime.utcnow().isoformat() + "Z",
    }

    if fetch_details and results:
        log.info("Fetching detail pages for %d results...", len(results))
        for row in results:
            url = row.get("License Number_url", "")
            if url:
                try:
                    detail = scrape_license_detail(driver, url)
                    detail["_license_number"] = row.get("License Number", "")
                    output["license_details"].append(detail)
                    # Go back to results
                    driver.back()
                    try:
                        wait_until(driver,
                            lambda d: len(d.find_elements(
                                By.CSS_SELECTOR,
                                "div.ag-row:not(.ag-header-row)")) > 0)
                    except TimeoutException:
                        pass
                    time.sleep(1)
                except Exception as e:
                    log.error("Detail scrape failed for %s: %s", url, e)

    return output


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="MA Health License Scraper")
    p.add_argument("--mode", choices=["name", "number"], required=True)
    p.add_argument("--first",   help="First name")
    p.add_argument("--last",    help="Last name")
    p.add_argument("--license", help="License number")
    p.add_argument("--board",   help="License Board filter")
    p.add_argument("--license-type", help="License Type filter")
    p.add_argument("--output",  default="license_results.json")
    p.add_argument("--no-details", action="store_true",
                   help="Skip detail pages")
    p.add_argument("--chrome-version", type=int, default=CHROME_VER,
                   help=f"Your Chrome major version (default: {CHROME_VER})")
    return p.parse_args()


def main():
    args = parse_args()

    if args.mode == "name" and not (args.first or args.last):
        print("ERROR: provide --first and/or --last for name mode")
        return
    if args.mode == "number" and not args.license:
        print("ERROR: --license required for number mode")
        return

    # Allow overriding Chrome version from CLI
    global CHROME_VER
    CHROME_VER = args.chrome_version

    driver = build_driver()
    try:
        data = search_and_scrape(
            driver,
            mode=args.mode,
            first_name=args.first or "",
            last_name=args.last or "",
            license_number=args.license or "",
            board=args.board or "",
            license_type=args.license_type or "",
            fetch_details=not args.no_details,
        )

        # Avoid overwriting existing file — add timestamp if file exists
        output_path = args.output
        if os.path.exists(output_path):
            base, ext = os.path.splitext(output_path)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = f"{base}_{timestamp}{ext}"
            log.info("File already exists — saving as '%s'", output_path)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        print(f"\n✅ Done! Saved to: {output_path}")
        print(f"   Results found  : {data['result_count']}")
        print(f"   Details fetched: {len(data['license_details'])}")

        # Print first result as preview
        if data["results"]:
            print("\nFirst result preview:")
            for k, v in data["results"][0].items():
                print(f"  {k}: {v}")

    except Exception as e:
        log.error("Scraper failed: %s", e)
        raise
    finally:
        driver.quit()


if __name__ == "__main__":
    main()

