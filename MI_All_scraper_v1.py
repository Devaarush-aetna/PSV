"""
Accela Citizen Access – License / Permit Search Scraper
URL pattern: https://<host>/CitizenAccess/Cap/CapSearch.aspx
             https://aca3.accela.com/<agency>/Cap/CapSearch.aspx
             https://aca-prod.accela.com/<agency>/GeneralProperty/PropertyLookUp.aspx?isLicensee=Y&TabName=APO

Uses Playwright + Chromium to drive the Accela portal (ASP.NET WebForms).
Fills the search form, paginates through results, opens every detail page
(CapDetail.aspx), scrapes all visible tabs/sections, and saves:

  Artifacts:
    artifacts/<timestamp>/search_page.html
    artifacts/<timestamp>/results_page.html
    artifacts/<timestamp>/detail_<n>.html
    artifacts/<timestamp>/detail_<n>_assets/images/

  JSON output: accela_results_<timestamp>.json

Install:
  pip install playwright requests
  playwright install chromium

Usage examples (default URL: MILARA PropertyLookUp licensee page):

  # Interactive mode — prompts for all fields:
  python MI_All_scraper_v1.py

  # License number only:
  python MI_All_scraper_v1.py --license-number 6301062818

  # Former license number:
  python MI_All_scraper_v1.py --former-license-number 1234567

  # First name + Last name:
  python MI_All_scraper_v1.py --first-name John --last-name Smith

  # First name + Middle initial + Last name:
  python MI_All_scraper_v1.py --first-name John --middle-initial A --last-name Smith

  # First name + Middle initial only:
  python MI_All_scraper_v1.py --first-name John --middle-initial A

  # Middle initial + Last name only:
  python MI_All_scraper_v1.py --middle-initial A --last-name Smith

  # Organization / company name:
  python MI_All_scraper_v1.py --organization-name "ACME Corp"

  # DBA / Trade name:
  python MI_All_scraper_v1.py --dba-name "Main Street Plumbing"

  # County filter:
  python MI_All_scraper_v1.py --last-name Smith --county Wayne

  # License number + name combination:
  python MI_All_scraper_v1.py --license-number 6301062818 --last-name Smith

  # Override to a different Accela instance:
  python MI_All_scraper_v1.py \\
      --search-url "https://aca-prod.accela.com/MILARA/GeneralProperty/PropertyLookUp.aspx?isLicensee=Y&TabName=APO" \\
      --last-name Smith

  # Classic CapSearch via base-url (constructs path automatically):
  python MI_All_scraper_v1.py \\
      --base-url https://aca3.accela.com/MYAGENCY \\
      --search-by "Record Number" --query "BLDG-2024-00123"

  # List available record/license types and exit:
  python MI_All_scraper_v1.py --list-types
"""

import argparse
import base64
import json
import logging
import os
import re
import time
import warnings
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlencode, parse_qs

import requests

warnings.filterwarnings("ignore")

from playwright.sync_api import (
    Browser,
    Page,
    Playwright,
    TimeoutError as PWTimeoutError,
    sync_playwright,
)

# ── Configuration ─────────────────────────────────────────────────────────────
WAIT_TIMEOUT = 30_000   # milliseconds

def _clean_text(text: str) -> str:
    """Strip Accela's zero-width space injections and normalise whitespace.

    Accela injects zero-width Unicode chars between every character in displayed
    values as an anti-scraping measure.  We strip them all before storing data.
    """
    # U+200B zero-width space, U+200C ZWNJ, U+200D ZWJ, U+2060 word joiner, U+FEFF BOM
    for zw in ("​", "‌", "‍", "⁠", "﻿"):
        text = text.replace(zw, "")
    return " ".join(text.split())

MILARA_DEFAULT_URL = (
    "https://aca-prod.accela.com/MILARA/GeneralProperty/PropertyLookUp.aspx?isLicensee=Y&TabName=Home"    
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# Accela detail-page tab labels (varies slightly by agency, but mostly standard)
ACCELA_TAB_LABELS = [
    "Record Info",
    "Processing Status",
    "Conditions",
    "Comments",
    "Inspections",
    "Fees / Payments",
    "Attachments",
    "Related Records",
    "Application Status",
    "Contact",
    "Owner",
    "Licensed Professional",
    "Additional Information",
]

# CSS selectors for Accela's standard page elements.
# Covers both CapSearch.aspx (permit search) and
# PropertyLookUp.aspx?isLicensee=Y (licensee/contractor lookup).
ACCELA_SELECTORS = {
    # Search form — CapSearch.aspx fields
    "record_type_select":   "select[id*='ddlCapType'], select[id*='CapType'], select[id*='RecordType']",
    "search_by_select":     "select[id*='ddlSearchBy'], select[id*='SearchBy']",
    "record_number_input":  "input[id*='txtPermitNumber'], input[id*='txtNumber'], input[id*='PermitNumber']",
    "project_name_input":   "input[id*='txtProjectName'], input[id*='ProjectName']",
    "address_input":        "input[id*='txtStreetName'], input[id*='StreetName'], input[id*='Address']",
    "parcel_input":         "input[id*='txtParcelNumber'], input[id*='ParcelNumber'], input[id*='Parcel']",
    # Search form — PropertyLookUp.aspx licensee fields
    # (these IDs are present on the MILARA portal and similar Accela licensee pages)
    "license_number_input":  (
        "input[id*='txtLicNumber'], input[id*='txtLicenseNumber'], input[id*='LicNumber'], "
        "input[id*='txtLicNo'], input[id*='LicenseNumber']"
    ),
    "former_license_number_input": (
        # MILARA uses txtBusiLicense for "Former License Number"
        "input[id*='txtBusiLicense'], "
        "input[id*='txtFormerLicNumber'], input[id*='FormerLicNumber'], "
        "input[id*='txtFormerLicense'], input[id*='FormerLicense'], input[id*='txtFormerLic']"
    ),
    "license_type_select":   (
        "select[id*='ddlLicType'], select[id*='LicenseType'], select[id*='LicType'], "
        "select[id*='ddlLicenseType']"
    ),
    "business_name_input":   (
        "input[id*='txtBusinessName'], input[id*='BusinessName'], input[id*='txtBusiness'], "
        "input[id*='txtOrganizationName'], input[id*='OrganizationName'], input[id*='txtOrgName']"
    ),
    "organization_name_input": (
        "input[id*='txtOrganizationName'], input[id*='OrganizationName'], input[id*='txtOrgName'], "
        "input[id*='txtBusinessName'], input[id*='BusinessName']"
    ),
    "dba_name_input": (
        "input[id*='txtDBAName'], input[id*='DBAName'], input[id*='txtDBA'], "
        "input[id*='txtTradeName'], input[id*='TradeName'], input[id*='txtDba']"
    ),
    "county_input": (
        "input[id*='txtCounty'], input[id*='County'], select[id*='ddlCounty'], "
        "input[id*='txtCnty'], select[id*='ddlCnty']"
    ),
    "first_name_input":      "input[id*='txtFirstName'], input[id*='FirstName'], input[id*='txtGivenName']",
    "middle_initial_input":  (
        "input[id*='txtMiddleName'], input[id*='MiddleName'], "
        "input[id*='txtMiddleInitial'], input[id*='MiddleInitial'], "
        "input[id*='txtMiddle']"
    ),
    "last_name_input":       "input[id*='txtLastName'], input[id*='LastName'], input[id*='txtSurName']",
    # Search / Go button (both page types)
    "search_button":        (
        "input[id*='btnSearch'], a[id*='btnSearch'], "
        "input[type='submit'][value*='Search'], "
        "input[type='button'][value*='Search'], "
        "a[id*='lnkSearch'], span[id*='btnSearch']"
    ),
    # Results table
    "results_table":        (
        "table[id*='GridViewBuildingPermit'], table[id*='CapListGrid'], "
        "table[id*='gridResult'], table[id*='GridResult'], "
        "table.ACA_Grid, table[class*='Grid']"
    ),
    # Pagination
    "pagination":           (
        "table[id*='GridViewBuildingPermit'] td[colspan] a, "
        ".ACA_Grid tfoot a, [id*='Pager'] a, "
        "table[id*='gridResult'] tfoot a"
    ),
    # Detail page
    "detail_tabs":          ".TabStrip a, ul.TabStrip li a, .tab-link, [id*='TabHeader'] a",
    "detail_tab_panels":    ".TabBody, .tab-content, [id*='TabBody']",
}


# ── Browser / page setup ──────────────────────────────────────────────────────

def build_browser(pw: Playwright) -> Browser:
    log.info("Launching Chromium (headless=False)...")
    return pw.chromium.launch(
        headless=False,
        args=["--start-maximized", "--disable-popup-blocking"],
    )


def new_page(browser: Browser) -> Page:
    ctx  = browser.new_context(viewport={"width": 1920, "height": 1080})
    page = ctx.new_page()
    page.set_default_timeout(WAIT_TIMEOUT)
    return page


def _resolve_search_url(base_url: str = "", search_url: str = "") -> str:
    """
    Return the URL to navigate to for the search form.
    Priority:
      1. search_url (full URL supplied directly — e.g. PropertyLookUp.aspx)
      2. base_url + /Cap/CapSearch.aspx  (classic CapSearch)
    The old _search_url() had a bug: 'return' inside the for-loop always
    chose the first suffix. Now fixed by using explicit logic.
    """
    if search_url:
        return search_url.strip()
    if base_url:
        base = base_url.rstrip("/")
        return f"{base}/Cap/CapSearch.aspx"
    raise ValueError("Provide either --search-url or --base-url.")


def open_search_page(page: Page, base_url: str = "", search_url: str = ""):
    url = _resolve_search_url(base_url, search_url)
    log.info("Loading search page: %s", url)
    page.goto(url, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=WAIT_TIMEOUT)
    except PWTimeoutError:
        log.warning("Network idle timeout — continuing anyway.")
    log.info("Search page ready. Title: %s", page.title())


# ── Record type dropdown ───────────────────────────────────────────────────────

def get_record_types(page: Page) -> list:
    """Return list of (value, text) from the Record Type / Cap Type / License Type dropdown."""
    opts = []
    # Try both CapSearch record-type selectors and PropertyLookUp license-type selectors
    combined_sels = (
        ACCELA_SELECTORS["record_type_select"] + ", " +
        ACCELA_SELECTORS["license_type_select"]
    )
    for sel in combined_sels.split(", "):
        loc = page.locator(sel.strip())
        if loc.count() > 0:
            for opt in loc.first.locator("option").all():
                try:
                    val  = opt.get_attribute("value") or ""
                    text = opt.inner_text().strip()
                    if text and text.lower() not in ("-- select --", "select", "--select--", ""):
                        opts.append((val, text))
                except Exception:
                    pass
            if opts:
                log.info("Record types (%d): %s", len(opts), [t for _, t in opts[:8]])
                return opts
    # Fallback: any visible <select> on the page
    for sel_loc in page.locator("select:visible").all():
        try:
            opt_texts = [o.inner_text().strip() for o in sel_loc.locator("option").all()]
            # Accela record type dropdowns have 5+ entries
            if len(opt_texts) >= 3:
                for i, text in enumerate(opt_texts):
                    if text and text.lower() not in ("-- select --", "select"):
                        opts.append((str(i), text))
                if opts:
                    return opts
        except Exception:
            pass
    return opts


def set_record_type(page: Page, record_type: str) -> bool:
    """Set the Record Type dropdown. Returns True on success."""
    if not record_type or record_type.lower() in ("all", "any", ""):
        return True
    log.info("Setting Record Type: '%s'", record_type)
    combined_sels = (
        ACCELA_SELECTORS["record_type_select"] + ", " +
        ACCELA_SELECTORS["license_type_select"]
    )
    for sel in combined_sels.split(", "):
        loc = page.locator(sel)
        if loc.count() > 0:
            try:
                loc.first.select_option(label=record_type)
                log.info("Record type set via label match.")
                page.wait_for_timeout(1000)
                return True
            except Exception:
                pass
            # Partial match
            for val, text in get_record_types(page):
                if record_type.lower() in text.lower():
                    try:
                        loc.first.select_option(value=val)
                        log.info("Record type set via partial match: '%s'", text)
                        page.wait_for_timeout(1000)
                        return True
                    except Exception:
                        pass
    log.warning("Could not set Record Type '%s'.", record_type)
    return False


# ── Search fields ──────────────────────────────────────────────────────────────

def _fill_input(page: Page, selectors: str, value: str, label: str = "") -> bool:
    """Try each CSS selector in a comma-separated string and fill the first match."""
    if not value:
        return True
    for sel in selectors.split(", "):
        loc = page.locator(sel.strip())
        if loc.count() > 0 and loc.first.is_visible():
            try:
                loc.first.fill(value)
                log.info("Filled '%s' = '%s'", label or sel, value)
                return True
            except Exception as e:
                log.debug("Fill failed for '%s': %s", sel, e)
    return False


def fill_search_fields(page: Page, search_params: dict) -> dict:
    """
    Fill whichever search fields are provided in search_params.
    Covers both CapSearch.aspx and PropertyLookUp.aspx licensee fields.
    Keys: record_number, license_number, business_name, project_name,
          first_name, middle_initial, last_name, address, parcel
    Returns dict of {field: value} pairs actually filled.
    """
    filled = {}
    mapping = {
        "record_number":         ACCELA_SELECTORS["record_number_input"],
        "license_number":        ACCELA_SELECTORS["license_number_input"],
        "former_license_number": ACCELA_SELECTORS["former_license_number_input"],
        "business_name":         ACCELA_SELECTORS["business_name_input"],
        "organization_name":     ACCELA_SELECTORS["organization_name_input"],
        "dba_name":              ACCELA_SELECTORS["dba_name_input"],
        "county":                ACCELA_SELECTORS["county_input"],
        "project_name":          ACCELA_SELECTORS["project_name_input"],
        "first_name":            ACCELA_SELECTORS["first_name_input"],
        "middle_initial":        ACCELA_SELECTORS["middle_initial_input"],
        "last_name":             ACCELA_SELECTORS["last_name_input"],
        "address":               ACCELA_SELECTORS["address_input"],
        "parcel":                ACCELA_SELECTORS["parcel_input"],
    }
    for key, sels in mapping.items():
        val = search_params.get(key, "")
        if val:
            if _fill_input(page, sels, val, key):
                filled[key] = val
    return filled


# ── Go / Search button ────────────────────────────────────────────────────────

def click_search_button(page: Page):
    """Click the Search / Go button."""
    for sel in ACCELA_SELECTORS["search_button"].split(", "):
        loc = page.locator(sel.strip())
        if loc.count() > 0 and loc.first.is_visible():
            try:
                loc.first.click()
                log.info("Clicked search button: %s", sel)
                return
            except Exception:
                pass

    # Fallback: any visible submit / button with 'Search' text.
    # For <a> tags we require an EXACT "search" or "go" match so we never
    # accidentally click navigation links like "Advanced Search".
    for tag_sel in ("input[type='submit']", "input[type='button']", "button", "a"):
        for el in page.locator(f"{tag_sel}:visible").all():
            try:
                txt = (
                    el.get_attribute("value")
                    or el.inner_text()
                    or el.get_attribute("title")
                    or ""
                ).strip().lower()
                if tag_sel == "a":
                    # Exact match only for anchor tags — avoids "Advanced Search"
                    matches = txt in ("search", "find", "go")
                else:
                    # input/button: contains "search" is safe (no nav links)
                    matches = "search" in txt or txt == "go"
                if matches:
                    el.click()
                    log.info("Clicked fallback button tag='%s' text='%s'", tag_sel, txt)
                    return
            except Exception:
                pass

    raise RuntimeError("Search button not found on page.")


# ── Results detection + parsing ───────────────────────────────────────────────

def wait_for_results(page: Page) -> bool:
    """Wait for result rows. Returns False when 'no records found'."""
    log.info("Waiting for results...")
    try:
        page.wait_for_function(
            """() => {
                const rows = document.querySelectorAll(
                    'table tr td a[href*="CapDetail"], '
                    + 'table tr td a[href*="Cap/"], '
                    + 'table[class*="Grid"] tbody tr'
                );
                const bodyText = document.body.innerText.toLowerCase();
                const noRec = bodyText.includes('no records') ||
                              bodyText.includes('no results') ||
                              bodyText.includes('0 record');
                return rows.length > 0 || noRec;
            }""",
            timeout=WAIT_TIMEOUT,
        )
    except PWTimeoutError:
        log.warning("Timed out waiting for results — continuing.")
    page.wait_for_timeout(1500)

    body_text = page.locator("body").inner_text().lower()
    for phrase in ("no records found", "no results found", "0 records", "no permits found",
                   "no applications found", "no matching records"):
        if phrase in body_text:
            log.info("Portal reported: '%s'", phrase)
            return False
    return True


def _extract_result_count(page: Page) -> int:
    """Parse displayed count like '25 records found'."""
    try:
        body = page.locator("body").inner_text()
        for pat in [
            r"(\d[\d,]*)\s+record",
            r"(\d[\d,]*)\s+result",
            r"showing\s+(?:\d+\s*[-–]\s*\d+\s+of\s+)?(\d[\d,]*)",
            r"total[:\s]+(\d[\d,]*)",
            r"found[:\s]+(\d[\d,]*)",
        ]:
            m = re.search(pat, body, re.IGNORECASE)
            if m:
                count = int(m.group(1).replace(",", ""))
                log.info("Result count from page: %d", count)
                return count
    except Exception:
        pass
    return -1


def _parse_results_table(page: Page) -> list:
    """
    Parse the Accela results grid.
    Tries four strategies:
      0. MILARA licensee grid (gdvRefLicenseeList) — __doPostBack rows, URL constructed
      1. ACA_Grid / standard <table> with <th> headers
      2. Tables whose first row contains 'Record' or 'Permit'
      3. Any visible link to CapDetail / LicenseeDetail as a fallback
    Returns list of dicts with a '_detail_url' key where available.
    """
    from urllib.parse import quote, urlparse

    results = []

    # Strategy 0: MILARA licensee grid
    # Detail links are __doPostBack JS — no real href. Build LicenseeDetail URL
    # from the license number and type already present in each row.
    milara_grid = page.locator("table[id*='gdvRefLicenseeList']")
    if milara_grid.count() > 0:
        grid = milara_grid.first
        # Extract column headers from the dedicated header row.
        # NOTE: the first <tr> in the grid is a "Showing N of M" caption row
        # that contains a nested <table><tr> — so grid.locator("tr").first
        # picks up that inner <tr> instead of the real header.  Use the
        # class name to target it unambiguously.
        headers = []
        header_rows = grid.locator("tr[class*='ACA_TabRow_Header']").all()
        if header_rows:
            for th in header_rows[0].locator("th").all():
                # Each <th> has an <a title="Column Name"> sort link; use that
                # title attribute for a clean header name.
                a_loc = th.locator("a[title]")
                if a_loc.count() > 0:
                    txt = a_loc.first.get_attribute("title") or ""
                else:
                    txt = _clean_text(th.inner_text())
                headers.append(txt if txt.strip() else f"col_{len(headers)}")

        # Data rows carry ACA_TabRow_Odd / ACA_TabRow_Even class
        data_rows = grid.locator("tr[class*='ACA_TabRow_Odd'], tr[class*='ACA_TabRow_Even']").all()
        parsed_url = urlparse(page.url)
        base_path = parsed_url.path.rsplit("/", 1)[0]
        detail_base = f"{parsed_url.scheme}://{parsed_url.netloc}{base_path}"

        for row in data_rows:
            cells = row.locator("td").all()
            rec = {}
            for i, cell in enumerate(cells):
                key = headers[i] if i < len(headers) else f"col_{i}"
                rec[key] = _clean_text(cell.inner_text().strip())

            # Use named keys if headers resolved; fall back to index 0/1
            lic_num  = (rec.get("License Number") or
                        rec.get(headers[1] if len(headers) > 1 else "col_1", "")).strip()
            lic_type = (rec.get("License Type") or
                        rec.get(headers[0] if headers else "col_0", "")).strip()
            if lic_num:
                rec["_detail_url"] = (
                    f"{detail_base}/LicenseeDetail.aspx"
                    f"?LicenseeNumber={quote(lic_num)}"
                    f"&LicenseeType={quote(lic_type)}"
                )
            if any(v for k, v in rec.items() if not k.startswith("_")):
                results.append(rec)

        if results:
            log.info("MILARA licensee grid: %d row(s)", len(results))
            return results

    # Strategy 1 + 2: HTML table
    for tbl in page.locator("table").all():
        rows = tbl.locator("tr").all()
        if len(rows) < 2:
            continue

        # Try header row
        first_row = rows[0]
        ths = first_row.locator("th").all()
        if ths:
            headers   = [th.inner_text().strip() for th in ths]
            data_rows = rows[1:]
        else:
            tds_h = first_row.locator("td").all()
            headers = [td.inner_text().strip() for td in tds_h]
            data_rows = rows[1:]

        if not headers or not any(headers):
            continue

        # Only process tables that look like permit/record result tables
        hdr_text = " ".join(headers).lower()
        if not any(kw in hdr_text for kw in
                   ("record", "permit", "status", "type", "description",
                    "number", "application", "address")):
            continue

        for row in data_rows:
            tds = row.locator("td").all()
            if not tds:
                continue
            rec = {}
            for i, td in enumerate(tds):
                key = headers[i] if i < len(headers) else f"col_{i}"
                rec[key] = td.inner_text().strip()
                # Capture record detail link
                a_loc = td.locator("a")
                if a_loc.count() > 0:
                    href = a_loc.first.get_attribute("href") or ""
                    if href and ("capdetail" in href.lower() or "cap/" in href.lower()):
                        rec["_detail_url"] = urljoin(page.url, href)
                    elif href and href not in ("", "#"):
                        rec["_link_url"] = urljoin(page.url, href)

            if any(v for k, v in rec.items() if not k.startswith("_")):
                results.append(rec)

        if results:
            log.info("Table strategy: %d row(s)", len(results))
            return results

    # Strategy 3: collect all CapDetail / LicenseeDetail links as minimal records
    log.info("Falling back to detail link collection...")
    seen_urls = set()
    for a_loc in page.locator(
        "a[href*='CapDetail'], a[href*='Cap/'], a[href*='LicenseeDetail']"
    ).all():
        try:
            href = a_loc.get_attribute("href") or ""
            if not href or href in seen_urls:
                continue
            abs_href = urljoin(page.url, href)
            if abs_href in seen_urls:
                continue
            seen_urls.add(abs_href)
            text = a_loc.inner_text().strip()
            results.append({"Record Number": text, "_detail_url": abs_href})
        except Exception:
            pass

    if results:
        log.info("Link fallback: %d record(s)", len(results))
    return results


# ── Pagination ─────────────────────────────────────────────────────────────────

def _get_pagination_links(page: Page) -> list:
    """
    Return enabled Next-page Playwright locators for Accela's ASP.NET pager.
    Accela paginates via numeric page links or a 'Next' link in the grid footer.
    """
    # Try the standard ACA pager row (last <tr> inside the results table)
    current_page_links = []
    try:
        pager_links = page.locator(
            "table[id*='GridViewBuildingPermit'] td[colspan] a, "
            "table[class*='Grid'] tfoot a, "
            "[id*='Pager'] a, "
            ".pagerTable a, "
            "td.pager a"
        ).all()
        for lnk in pager_links:
            try:
                txt = lnk.inner_text().strip()
                cls = lnk.get_attribute("class") or ""
                # Skip the currently-active page (usually bold/span, not a link)
                if txt and "disabled" not in cls.lower():
                    current_page_links.append(lnk)
            except Exception:
                pass
    except Exception as e:
        log.debug("Pager link scan error: %s", e)
    return current_page_links


def _find_next_page_link(page: Page, current_page: int) -> object:
    """
    Return locator for page (current_page + 1) in the ACA pager, or None.
    """
    next_num = str(current_page + 1)
    # Look for '>' or 'Next' text link first
    for txt in (">", "Next", "Next »", "»"):
        try:
            loc = page.locator(f"a:text-is('{txt}'), a:has-text('{txt}')")
            if loc.count() > 0 and loc.first.is_visible():
                cls = loc.first.get_attribute("class") or ""
                if "disabled" not in cls.lower():
                    return loc.first
        except Exception:
            pass

    # Fallback: numeric page link
    pager_links = _get_pagination_links(page)
    for lnk in pager_links:
        try:
            if lnk.inner_text().strip() == next_num:
                return lnk
        except Exception:
            pass
    return None


# ── Artifact helpers ──────────────────────────────────────────────────────────

def save_html_artifact(page: Page, filepath: Path):
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(page.content(), encoding="utf-8")
    log.info("HTML saved: %s", filepath)


def save_screenshot(page: Page, filepath: Path):
    """Save a full-page PNG screenshot of the current browser page."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(filepath), full_page=True)
        log.info("Screenshot saved: %s", filepath)
    except Exception as e:
        log.warning("Screenshot failed for %s: %s", filepath, e)


def download_images(page: Page, asset_dir: Path, session: requests.Session) -> list:
    """Download all <img> on the current page into asset_dir/images/."""
    img_dir = asset_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)

    saved, seen = [], set()
    current_url = page.url

    for img in page.locator("img").all():
        try:
            src = img.get_attribute("src") or img.get_attribute("data-src") or ""
        except Exception:
            continue
        if not src or src in seen:
            continue
        seen.add(src)
        abs_src = urljoin(current_url, src)

        if abs_src.startswith("data:"):
            try:
                header, b64data = abs_src.split(",", 1)
                ext_m = re.search(r"image/(\w+)", header)
                ext   = ext_m.group(1) if ext_m else "png"
                dest  = img_dir / f"inline_{len(saved)}.{ext}"
                dest.write_bytes(base64.b64decode(b64data))
                saved.append(str(dest))
            except Exception as e:
                log.debug("Inline image error: %s", e)
            continue

        try:
            stem = Path(urlparse(abs_src).path).name or f"img_{len(saved)}"
            dest = img_dir / re.sub(r"[^\w.\-]", "_", stem)
            resp = session.get(abs_src, timeout=15)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            saved.append(str(dest))
        except Exception as e:
            log.debug("Image download failed (%s): %s", abs_src, e)

    log.info("Saved %d image(s) → %s", len(saved), img_dir)
    return saved


# ── Detail page scraping ──────────────────────────────────────────────────────

def _expand_all_tabs(page: Page):
    """
    Click each tab in the Accela detail page TabStrip so all content loads.
    Accela tabs are ASP.NET AJAX panels — content only renders after clicking.
    """
    tab_selectors = [
        ".TabStrip a",
        "ul.tabs li a",
        "[id*='TabHeader'] a",
        "a.tab-link",
        "[role='tab']",
        "li[role='presentation'] a",
    ]
    for sel in tab_selectors:
        tab_links = page.locator(sel).all()
        if not tab_links:
            continue
        log.info("Found %d tab(s) via '%s' — clicking each.", len(tab_links), sel)
        for i, tab in enumerate(tab_links):
            try:
                tab_text = tab.inner_text().strip()
                log.info("  Tab %d: '%s'", i + 1, tab_text)
                tab.click()
                page.wait_for_timeout(800)
            except Exception as e:
                log.debug("Tab click error: %s", e)
        return  # Only process the first matching set of tabs


def _parse_section(page: Page, container_loc) -> dict:
    """
    Extract key-value data from a single section/panel on the detail page.
    Handles: dl/dt/dd, 2-col tables, label[for], span pairs, field-label/value,
    and the MILARA Accela pattern of span[id$='_value'] / bold-label pairs.
    All values are cleaned of zero-width space injections via _clean_text().
    """
    data = {}

    # ── Strategy A: MILARA span[id$='_value'] pairs ───────────────────────────
    # Pattern: <span id="...lblFoo">Label text</span>
    #          <span id="...lblFoo_value">Actual value</span>
    try:
        for val_span in container_loc.locator("span[id$='_value']").all():
            try:
                span_id = val_span.get_attribute("id") or ""
                label_id = span_id[:-6]  # strip "_value"
                lbl_el = page.locator(f"#{label_id}")
                if lbl_el.count() > 0:
                    k = _clean_text(lbl_el.first.inner_text()).rstrip(":").strip()
                    v = _clean_text(val_span.inner_text())
                    if k and v:
                        data.setdefault(k, v)
            except Exception:
                pass
    except Exception:
        pass

    # ── Strategy B: MILARA bold-label + following sibling span ────────────────
    # Pattern: <span style="font-weight:bold;">Label: </span><span>Value</span>
    try:
        for bold_lbl in container_loc.locator(
            "span[style*='font-weight:bold'], span[style*='font-weight: bold']"
        ).all():
            try:
                k = _clean_text(bold_lbl.inner_text()).rstrip(":").strip()
                if not k or len(k) > 80:
                    continue
                sib = bold_lbl.locator("xpath=following-sibling::span[1]")
                if sib.count() > 0:
                    v = _clean_text(sib.first.inner_text())
                    if v:
                        data.setdefault(k, v)
            except Exception:
                pass
    except Exception:
        pass

    # ── Strategy C: dl / dt / dd ──────────────────────────────────────────────
    try:
        dts = container_loc.locator("dt").all()
        dds = container_loc.locator("dd").all()
        for dt, dd in zip(dts, dds):
            k = _clean_text(dt.inner_text()).rstrip(":")
            if k:
                data.setdefault(k, _clean_text(dd.inner_text()))
    except Exception:
        pass

    # ── Strategy D: HTML tables — 2-col and 4-col key/value rows ─────────────
    try:
        for row in container_loc.locator("tr").all():
            cols = row.locator("td, th").all()
            if len(cols) == 2:
                k = _clean_text(cols[0].inner_text()).rstrip(":")
                if k and len(k) < 80:
                    data.setdefault(k, _clean_text(cols[1].inner_text()))
            elif len(cols) == 4:
                for ki, vi in [(0, 1), (2, 3)]:
                    k = _clean_text(cols[ki].inner_text()).rstrip(":")
                    if k and len(k) < 80:
                        data.setdefault(k, _clean_text(cols[vi].inner_text()))
    except Exception:
        pass

    # ── Strategy E: label[for] associations ──────────────────────────────────
    try:
        for lbl in container_loc.locator("label").all():
            k = _clean_text(lbl.inner_text()).rstrip(":")
            if not k:
                continue
            for_id = lbl.get_attribute("for") or ""
            if for_id:
                el = page.locator(f"#{for_id}")
                if el.count() > 0:
                    data.setdefault(
                        k,
                        _clean_text(el.first.inner_text()) or el.first.get_attribute("value") or ""
                    )
                    continue
            try:
                sib = lbl.locator("xpath=following-sibling::*[1]")
                if sib.count() > 0:
                    data.setdefault(k, _clean_text(sib.first.inner_text()))
            except Exception:
                pass
    except Exception:
        pass

    # ── Strategy F: Accela field-label / ACA_SmLabel class patterns ──────────
    try:
        for lbl_el in container_loc.locator(
            "[class*='ACA_SmLabel_Label'], [class*='td_label'], "
            "[class*='field-label'], [class*='fieldLabel'], "
            "[class*='formLabel']"
        ).all():
            k = _clean_text(lbl_el.inner_text()).rstrip(":")
            if not k or len(k) > 80:
                continue
            try:
                val_el = lbl_el.locator("xpath=following-sibling::*").first
                v = _clean_text(val_el.inner_text())
                if v:
                    data.setdefault(k, v)
            except Exception:
                pass
    except Exception:
        pass

    return data


def _scrape_inspections_table(page: Page) -> list:
    """
    Accela inspection records appear in a distinct table on the Inspections tab.
    Returns list of dicts (one per inspection row).
    """
    records = []
    for tbl in page.locator("table[id*='Inspect'], table[id*='inspection']").all():
        rows = tbl.locator("tr").all()
        if len(rows) < 2:
            continue
        first_row = rows[0]
        ths = first_row.locator("th").all()
        headers = [th.inner_text().strip() for th in ths] if ths else \
                  [td.inner_text().strip() for td in first_row.locator("td").all()]
        if not headers:
            continue
        for row in rows[1:]:
            tds = row.locator("td").all()
            if not tds:
                continue
            rec = {}
            for i, td in enumerate(tds):
                key = headers[i] if i < len(headers) else f"col_{i}"
                rec[key] = td.inner_text().strip()
            if any(rec.values()):
                records.append(rec)
    return records


def scrape_detail_page(page: Page, url: str, artifact_dir: Path,
                       session: requests.Session, index: int) -> dict:
    """
    Navigate to an Accela CapDetail.aspx page, expand all tabs,
    scrape every section, save HTML + images, return structured dict.
    """
    log.info("Detail [%04d]: %s", index, url)
    page.goto(url, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=WAIT_TIMEOUT)
    except PWTimeoutError:
        pass
    page.wait_for_timeout(1500)

    detail: dict = {"_source_url": url, "_detail_index": index}

    # Save HTML artifact and screenshot
    html_path = artifact_dir / f"detail_{index:04d}.html"
    save_html_artifact(page, html_path)
    save_screenshot(page, html_path.with_suffix(".png"))
    detail["_html_artifact"] = str(html_path)

    # Download images
    detail["_images"] = download_images(
        page, artifact_dir / f"detail_{index:04d}_assets", session
    )

    # Record header info (usually visible without clicking any tab)
    try:
        # Accela renders record number / status in a header div
        header_sels = [
            ".MoreDetailTitle", "#ctl00_PlaceHolderMain_lblPermitNumber",
            "[id*='lblPermitNumber']", "[id*='lblRecordNumber']",
            "[id*='lblCapNumber']", "[class*='record-number']",
        ]
        for sel in header_sels:
            loc = page.locator(sel)
            if loc.count() > 0:
                text = loc.first.inner_text().strip()
                if text:
                    detail.setdefault("Record Number", text)
                    break
    except Exception:
        pass

    # Page title as fallback record number
    try:
        title = page.title().strip()
        if title:
            detail.setdefault("_page_title", title)
    except Exception:
        pass

    # Expand all tabs so their content is rendered in the DOM
    _expand_all_tabs(page)
    page.wait_for_timeout(1000)

    # ── Tab / section extraction ──────────────────────────────────────────────

    # Strategy 1: Named tab panels
    for panel_sel in (
        "[id*='TabBody']", ".TabBody", ".tab-pane", "[role='tabpanel']"
    ):
        panels = page.locator(panel_sel).all()
        if not panels:
            continue
        log.info("Found %d panel(s) via '%s'", len(panels), panel_sel)
        for panel in panels:
            try:
                # Find heading of this panel (title attr, sibling tab, or h tag)
                heading = ""
                for hsel in ("h1", "h2", "h3", "h4", ".sectionTitle", ".panel-title"):
                    try:
                        h = panel.locator(hsel)
                        if h.count() > 0:
                            heading = h.first.inner_text().strip()
                            break
                    except Exception:
                        pass
                # Also check id or title attribute
                if not heading:
                    panel_id = panel.get_attribute("id") or ""
                    if panel_id:
                        heading = re.sub(r"(TabBody|Panel|_tab|_panel)", "", panel_id,
                                         flags=re.IGNORECASE).strip("_")

                sec_data = _parse_section(page, panel)
                if not sec_data:
                    continue

                safe_key = re.sub(r"[^a-zA-Z0-9_]", "_", heading).strip("_") if heading else None
                if safe_key:
                    existing = detail.get(safe_key)
                    if isinstance(existing, dict):
                        existing.update(sec_data)
                    else:
                        detail[safe_key] = sec_data
                else:
                    for k, v in sec_data.items():
                        detail.setdefault(k, v)
            except Exception as e:
                log.debug("Panel parse error: %s", e)

    # Strategy 2: Accela MoreDetailInfo sections (common in older ACA builds)
    for sec_sel in (
        ".MoreDetailInfoDiv", "[class*='MoreDetail']",
        "table[id*='tblPermit']", "table[id*='tblCap']",
        "#ctl00_PlaceHolderMain_UpdatePanel1",
    ):
        sec_locs = page.locator(sec_sel).all()
        if not sec_locs:
            continue
        for sec_loc in sec_locs:
            try:
                sec_data = _parse_section(page, sec_loc)
                for k, v in sec_data.items():
                    detail.setdefault(k, v)
            except Exception:
                pass

    # Strategy 3: full-page flat extraction as comprehensive fallback
    # Applies _clean_text() to strip Accela's zero-width space injections.

    # MILARA span[id$='_value'] pattern (whole-page sweep)
    try:
        for val_span in page.locator("span[id$='_value']").all():
            try:
                span_id = val_span.get_attribute("id") or ""
                label_id = span_id[:-6]
                lbl_el = page.locator(f"#{label_id}")
                if lbl_el.count() > 0:
                    k = _clean_text(lbl_el.first.inner_text()).rstrip(":").strip()
                    v = _clean_text(val_span.inner_text())
                    if k and v:
                        detail.setdefault(k, v)
            except Exception:
                pass
    except Exception:
        pass

    # Bold-label + following sibling span (whole-page sweep)
    try:
        for bold_lbl in page.locator(
            "span[style*='font-weight:bold'], span[style*='font-weight: bold']"
        ).all():
            try:
                k = _clean_text(bold_lbl.inner_text()).rstrip(":").strip()
                if not k or len(k) > 80:
                    continue
                sib = bold_lbl.locator("xpath=following-sibling::span[1]")
                if sib.count() > 0:
                    v = _clean_text(sib.first.inner_text())
                    if v:
                        detail.setdefault(k, v)
            except Exception:
                pass
    except Exception:
        pass

    try:
        for dt, dd in zip(
            page.locator("dt").all(),
            page.locator("dd").all(),
        ):
            k = _clean_text(dt.inner_text()).rstrip(":")
            if k:
                detail.setdefault(k, _clean_text(dd.inner_text()))
    except Exception:
        pass

    try:
        for row in page.locator("tr").all():
            cols = row.locator("td").all()
            if len(cols) == 2:
                k = _clean_text(cols[0].inner_text()).rstrip(":")
                if k and len(k) < 80 and not k.isdigit():
                    detail.setdefault(k, _clean_text(cols[1].inner_text()))
            elif len(cols) == 4:
                for ki, vi in [(0, 1), (2, 3)]:
                    k = _clean_text(cols[ki].inner_text()).rstrip(":")
                    if k and len(k) < 80 and not k.isdigit():
                        detail.setdefault(k, _clean_text(cols[vi].inner_text()))
    except Exception:
        pass

    try:
        for lbl in page.locator("label").all():
            k = _clean_text(lbl.inner_text()).rstrip(":")
            if not k:
                continue
            for_id = lbl.get_attribute("for") or ""
            if for_id:
                el = page.locator(f"#{for_id}")
                if el.count() > 0:
                    detail.setdefault(
                        k,
                        _clean_text(el.first.inner_text()) or el.first.get_attribute("value") or "",
                    )
                    continue
            try:
                sib = lbl.locator("xpath=following-sibling::*[1]")
                if sib.count() > 0:
                    detail.setdefault(k, _clean_text(sib.first.inner_text()))
            except Exception:
                pass
    except Exception:
        pass

    # Inspections table (separate structured data)
    inspections = _scrape_inspections_table(page)
    if inspections:
        detail["_inspections"] = inspections

    # Full page text — always capture as fallback
    try:
        detail["_full_page_text"] = page.locator("body").inner_text().strip()
    except Exception:
        pass

    log.info("Detail [%04d]: %d top-level field(s) captured", index, len(detail))
    return detail


# ── Interactive prompts ────────────────────────────────────────────────────────

def _detect_page_type(page: Page) -> str:
    """
    Detect which Accela search page is loaded so we can tailor prompts.
    Returns 'licensee' for PropertyLookUp.aspx, 'permit' for CapSearch.aspx,
    or 'unknown' for anything else.
    """
    url = page.url.lower()
    if "propertylookup" in url or "islicensee=y" in url:
        return "licensee"
    if "capsearch" in url or "generalsearch" in url:
        return "permit"
    # Inspect visible form fields as a fallback signal
    if page.locator(ACCELA_SELECTORS["license_number_input"]).count() > 0:
        return "licensee"
    return "permit"


def _prompt_search_fields(page: Page, base_url: str = "", search_url: str = "") -> dict:
    """
    Interactively prompt the user for search fields based on what's
    visible on the current Accela search page.
    Returns search_params dict.
    """
    display_url = search_url or base_url
    print("\n" + "=" * 64)
    print("  Accela Citizen Access — License / Permit Search")
    print(f"  URL: {display_url}")
    print("=" * 64)

    search_params: dict = {}
    page_type = _detect_page_type(page)
    log.info("Detected page type: %s", page_type)

    # Record type / license type selector
    rec_types = get_record_types(page)
    if rec_types:
        label = "License Types" if page_type == "licensee" else "Record Types"
        print(f"\nAvailable {label}:")
        print("  (0) All / Any")
        for i, (val, text) in enumerate(rec_types, 1):
            print(f"  ({i}) {text}")
        raw = input(f"\nSelect {label} number (or press Enter for All): ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(rec_types):
            search_params["record_type"] = rec_types[int(raw) - 1][1]
        else:
            search_params["record_type"] = ""

        if search_params.get("record_type"):
            set_record_type(page, search_params["record_type"])
            page.wait_for_timeout(1000)

    # Field prompts differ by page type
    if page_type == "licensee":
        field_prompts = [
            ("license_number",        "License Number (leave blank to skip)"),
            ("former_license_number", "Former License Number (leave blank to skip)"),
            ("first_name",            "First Name (leave blank to skip)"),
            ("middle_initial",        "Middle Initial (leave blank to skip)"),
            ("last_name",             "Last Name (leave blank to skip)"),
            ("organization_name",     "Organization Name (leave blank to skip)"),
            ("dba_name",              "DBA / Trade Name (leave blank to skip)"),
            ("county",                "County (leave blank to skip)"),
        ]
    else:
        field_prompts = [
            ("record_number", "Record / Permit Number (leave blank to skip)"),
            ("project_name",  "Project Name (leave blank to skip)"),
            ("first_name",    "Applicant First Name (leave blank to skip)"),
            ("last_name",     "Applicant Last Name (leave blank to skip)"),
            ("address",       "Street Address (leave blank to skip)"),
            ("parcel",        "Parcel Number (leave blank to skip)"),
        ]

    print("\nSearch criteria (fill at least one):")
    for key, prompt in field_prompts:
        val = input(f"  {prompt}: ").strip()
        if val:
            search_params[key] = val

    return search_params


# ── Main search flow ───────────────────────────────────────────────────────────

def run_search(
    page: Page,
    base_url: str,
    artifact_dir: Path,
    session: requests.Session,
    output_file: str,
    search_params: dict,
    interactive: bool = True,
    search_url: str = "",
) -> dict:
    """
    Full flow: open → fill form → submit → paginate → detail pages → save JSON.
    Pass search_url to navigate directly to a full URL (e.g. PropertyLookUp.aspx).
    Pass base_url to use the classic CapSearch.aspx path.
    """
    open_search_page(page, base_url=base_url, search_url=search_url)
    save_html_artifact(page, artifact_dir / "search_page.html")
    save_screenshot(page, artifact_dir / "search_page.png")
    download_images(page, artifact_dir / "search_page_assets", session)

    all_search_keys = (
        "record_number", "license_number", "business_name",
        "project_name", "first_name", "last_name", "address", "parcel"
    )
    # Populate search_params interactively if not fully specified
    if interactive and not any(search_params.get(k) for k in all_search_keys):
        search_params = _prompt_search_fields(page, base_url=base_url, search_url=search_url)
    elif search_params.get("record_type"):
        set_record_type(page, search_params["record_type"])
        page.wait_for_timeout(1000)

    filled = fill_search_fields(page, search_params)
    if not filled:
        raise RuntimeError(
            "No search fields were filled. "
            "Provide --query with --search-by, or run interactively."
        )

    log.info("Submitting search with params: %s", filled)
    print("\nSubmitting search...")
    click_search_button(page)

    try:
        page.wait_for_load_state("networkidle", timeout=WAIT_TIMEOUT)
    except PWTimeoutError:
        pass
    page.wait_for_timeout(1500)

    # Save results page
    save_html_artifact(page, artifact_dir / "results_page.html")
    save_screenshot(page, artifact_dir / "results_page.png")
    download_images(page, artifact_dir / "results_page_assets", session)

    # ── Direct-to-detail redirect detection ──────────────────────────────────
    # MILARA (and some other Accela instances) skip the results grid entirely
    # when a search returns exactly one match — navigating straight to the
    # LicenseeDetail.aspx or CapDetail.aspx page.  Detect this and treat the
    # current URL as the only detail page, bypassing results-grid parsing.
    current_url_lower = page.url.lower()
    _is_direct_detail = (
        "licenseedetail" in current_url_lower or
        "capdetail"      in current_url_lower
    )

    has_results = wait_for_results(page)
    total_count = _extract_result_count(page)
    print(f"Result count (from page text): {total_count}")

    all_results: list = []
    detail_urls_ordered: list = []

    if _is_direct_detail:
        log.info("Direct detail redirect detected: %s", page.url)
        print(f"Direct redirect to detail page — scraping: {page.url}")
        all_results = [{"_detail_url": page.url, "_direct_redirect": True}]
        detail_urls_ordered = [page.url]
        has_results = True
        if total_count < 1:
            total_count = 1

    elif not has_results:
        print("No records found for the given search criteria.")
    else:
        # Paginate and collect all result rows + detail URLs
        current_page = 1
        while True:
            rows = _parse_results_table(page)
            log.info("Page %d: %d row(s)", current_page, len(rows))

            for row in rows:
                all_results.append(row)
                detail_url = row.get("_detail_url") or row.get("_link_url", "")
                if detail_url and detail_url not in detail_urls_ordered:
                    detail_urls_ordered.append(detail_url)

            # Try to move to next page
            next_link = _find_next_page_link(page, current_page)
            if not next_link:
                log.info("No more result pages.")
                break

            log.info("Navigating to result page %d...", current_page + 1)
            try:
                next_link.click()
                page.wait_for_load_state("networkidle", timeout=WAIT_TIMEOUT)
            except PWTimeoutError:
                pass
            page.wait_for_timeout(1500)
            current_page += 1

            if current_page > 100:
                log.warning("Pagination safety limit (100 pages) reached.")
                break

        print(f"Total result rows collected: {len(all_results)}")
        print(f"Detail pages to fetch     : {len(detail_urls_ordered)}")

    # ── Fetch detail pages ────────────────────────────────────────────────────
    all_details: list = []
    if detail_urls_ordered:
        print(f"\nFetching {len(detail_urls_ordered)} detail page(s)...")
        for i, detail_url in enumerate(detail_urls_ordered, 1):
            try:
                detail = scrape_detail_page(page, detail_url, artifact_dir, session, i)
                detail["_result_index"] = i - 1
                all_details.append(detail)

                # Return to results page (go_back may not work after tab-clicking)
                if len(detail_urls_ordered) > 1:
                    page.go_back()
                    try:
                        page.wait_for_load_state("networkidle", timeout=15_000)
                    except PWTimeoutError:
                        pass
                    page.wait_for_timeout(1000)

            except Exception as e:
                log.error("Detail [%04d] failed (%s): %s", i, detail_url, e)

    # ── Assemble output ───────────────────────────────────────────────────────
    output = {
        "scraper":         "MI_All_scraper_v1",
        "scraped_at":      datetime.utcnow().isoformat() + "Z",
        "base_url":        base_url,
        "search_url":      search_url or _resolve_search_url(base_url, search_url),
        "search_params":   search_params,
        "result_count":    total_count if total_count > 0 else len(all_results),
        "results":         all_results,
        "detail_count":    len(all_details),
        "license_details": all_details,
        "artifacts_dir":   str(artifact_dir),
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n{'=' * 64}")
    print(f"  JSON output : {output_file}")
    print(f"  Artifacts   : {artifact_dir}/")
    print(f"  Results     : {len(all_results)}")
    print(f"  Details     : {len(all_details)}")
    print(f"{'=' * 64}")

    if all_results:
        print("\nFirst result preview:")
        for k, v in list(all_results[0].items())[:10]:
            if not k.startswith("_"):
                print(f"  {k}: {v}")

    return output


# ── CLI ────────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Accela Citizen Access – License/Permit Scraper (Playwright Chromium)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    url_group = p.add_mutually_exclusive_group(required=False)
    url_group.add_argument(
        "--search-url",
        default="",
        help=(
            "Full URL of the Accela search page. "
            f"Defaults to the MILARA licensee page:\n  {MILARA_DEFAULT_URL}"
        ),
    )
    url_group.add_argument(
        "--base-url",
        default="",
        help=(
            "Base URL of an Accela instance — scraper appends /Cap/CapSearch.aspx. "
            "Example: https://aca3.accela.com/MYAGENCY"
        ),
    )

    # ── Direct search-field arguments ────────────────────────────────────────
    p.add_argument(
        "--license-number",
        default="",
        metavar="NUM",
        help="License number to search for (e.g. 6301062818)",
    )
    p.add_argument(
        "--first-name",
        default="",
        metavar="NAME",
        help="Licensee first name",
    )
    p.add_argument(
        "--middle-initial",
        default="",
        metavar="INIT",
        help="Licensee middle initial or middle name",
    )
    p.add_argument(
        "--last-name",
        default="",
        metavar="NAME",
        help="Licensee last name",
    )
    p.add_argument(
        "--former-license-number",
        default="",
        metavar="NUM",
        help="Former license number",
    )
    p.add_argument(
        "--organization-name",
        default="",
        metavar="NAME",
        help="Organization / company name",
    )
    p.add_argument(
        "--dba-name",
        default="",
        metavar="NAME",
        help="DBA / Trade name",
    )
    p.add_argument(
        "--county",
        default="",
        metavar="COUNTY",
        help="County name",
    )

    # ── Legacy --search-by / --query style (still supported) ─────────────────
    p.add_argument(
        "--search-by",
        default="",
        help=(
            "Field to search by (legacy style): 'Record Number', 'License Number', "
            "'First Name', 'Last Name', 'Business Name', 'Address', 'Parcel'"
        ),
    )
    p.add_argument(
        "--query",
        default="",
        help="Primary search query value used with --search-by (legacy style)",
    )
    p.add_argument(
        "--record-type",
        default="",
        help="Record/Permit/License type filter, e.g. 'Building', 'Contractor'",
    )
    p.add_argument(
        "--output",
        default="accela_results.json",
        help="Base name for JSON output (timestamp appended automatically)",
    )
    p.add_argument(
        "--artifacts-dir",
        default="artifacts",
        help="Root directory for HTML/image artifacts  (default: artifacts/)",
    )
    p.add_argument(
        "--list-types",
        action="store_true",
        help="Print available Record/License Types for this Accela instance and exit",
    )
    p.add_argument(
        "--headless",
        action="store_true",
        help="Run Chromium in headless mode (default: visible)",
    )
    return p.parse_args()


def _build_search_params_from_args(args) -> dict:
    """Convert CLI args into a search_params dict.

    Supports two styles:
      New:    --license-number / --first-name / --middle-initial / --last-name
      Legacy: --search-by <field> --query <value>
    Both styles may be combined (new-style args take precedence for their fields).
    """
    params: dict = {"record_type": args.record_type}

    # ── New-style direct args ─────────────────────────────────────────────────
    if args.license_number:
        params["license_number"] = args.license_number
    if args.former_license_number:
        params["former_license_number"] = args.former_license_number
    if args.first_name:
        params["first_name"] = args.first_name
    if args.middle_initial:
        params["middle_initial"] = args.middle_initial
    if args.last_name:
        params["last_name"] = args.last_name
    if args.organization_name:
        params["organization_name"] = args.organization_name
    if args.dba_name:
        params["dba_name"] = args.dba_name
    if args.county:
        params["county"] = args.county

    # ── Legacy --search-by / --query style ───────────────────────────────────
    q  = args.query
    sb = args.search_by.lower() if args.search_by else ""

    if q:
        if "license" in sb and "number" in sb:
            params.setdefault("license_number", q)
        elif "record" in sb or "permit" in sb:
            params.setdefault("record_number", q)
        elif "business" in sb or "company" in sb:
            params.setdefault("business_name", q)
        elif "first" in sb:
            params.setdefault("first_name", q)
        elif "last" in sb:
            params.setdefault("last_name", q)
        elif "address" in sb:
            params.setdefault("address", q)
        elif "parcel" in sb:
            params.setdefault("parcel", q)
        elif "project" in sb:
            params.setdefault("project_name", q)
        else:
            # No --search-by — infer from value format
            if re.match(r"^[A-Z]{2,}-\d{4}-\d+", q):
                params.setdefault("record_number", q)
            elif re.match(r"^\d{6,}$", q):
                params.setdefault("license_number", q)
            elif re.match(r"^\d{3,}-\d{2,}", q):
                params.setdefault("parcel", q)
            else:
                params.setdefault("license_number", q)
                params.setdefault("record_number",  q)

    return params


def main():
    args = parse_args()

    base_url   = args.base_url   or ""
    # Default to MILARA licensee page when no URL is specified
    search_url = args.search_url or ("" if base_url else MILARA_DEFAULT_URL)

    # --list-types: open search page, print record/license types, exit
    if args.list_types:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=args.headless, args=["--start-maximized"])
            page = browser.new_context(viewport={"width": 1920, "height": 1080}).new_page()
            page.set_default_timeout(WAIT_TIMEOUT)
            try:
                open_search_page(page, base_url=base_url, search_url=search_url)
                types = get_record_types(page)
                display = search_url or base_url
                print(f"\nAvailable Record / License Types at:\n  {display}")
                for val, text in types:
                    print(f"  [{val}]  {text}")
                if not types:
                    print("  (none found — page may require authentication or JS)")
            finally:
                browser.close()
        return

    ts           = datetime.now().strftime("%Y%m%d_%H%M%S")
    base, ext    = os.path.splitext(args.output)
    output_file  = f"{base}_{ts}{ext}"
    artifact_dir = Path(args.artifacts_dir) / ts
    artifact_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    search_params = _build_search_params_from_args(args)
    # Run interactively only when no search criteria were specified at all
    _any_search_arg = bool(
        args.license_number or args.former_license_number or
        args.first_name     or args.middle_initial        or
        args.last_name      or args.organization_name     or
        args.dba_name       or args.county                or args.query
    )
    interactive = not _any_search_arg

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=args.headless,
            args=["--start-maximized", "--disable-popup-blocking"],
        )
        page = browser.new_context(
            viewport={"width": 1920, "height": 1080}
        ).new_page()
        page.set_default_timeout(WAIT_TIMEOUT)

        try:
            run_search(
                page=page,
                base_url=base_url,
                artifact_dir=artifact_dir,
                session=session,
                output_file=output_file,
                search_params=search_params,
                interactive=interactive,
                search_url=search_url,
            )
        except KeyboardInterrupt:
            print("\nInterrupted by user.")
        except Exception as e:
            log.error("Scraper failed: %s", e)
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    main()
