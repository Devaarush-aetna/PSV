"""PDF bulk-roster scraper — downloads PDFs, extracts tables, searches in-memory.

Supports two PDF formats detected automatically:
  - "prof"  : individual licensee rows (Name = "FIRST LAST")
  - "estab" : establishment rows (Name = "BUSINESS‐OWNER: FIRST LAST")

License prefix routing:
  - license_number starts with "E" → estab PDF
  - license_number starts with "L" or "LA" → prof PDF
  - No prefix match → search all PDFs
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_EST = ZoneInfo("America/New_York")
from typing import Any, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PDF download + caching
# ---------------------------------------------------------------------------

def _find_cached_pdf(cache_dir: str, stem: str, cache_days: int) -> Optional[str]:
    """Find a cached PDF named {stem}_{YYYYMMDD}.pdf that is still within cache_days.

    Returns the file path string, or None if no fresh cached file exists.
    stem is the URL filename without the .pdf extension (e.g. "Podiatry_LicenseVerification_20260522").
    A glob for "{stem}_????????.pdf" finds all dated variants; the most recent is checked first.
    If the URL changes (board publishes a new PDF), the stem changes too, so the old cached
    file is not found and a fresh download is triggered automatically.
    """
    cache_path = Path(cache_dir)
    if not cache_path.exists():
        return None
    for f in sorted(cache_path.glob(f"{stem}_????????_????.pdf"), reverse=True):
        m = re.search(r"_(\d{8}_\d{4})\.pdf$", f.name)
        if not m:
            continue
        try:
            file_date = datetime.strptime(m.group(1), "%Y%m%d_%H%M")
            if (datetime.now() - file_date).days <= cache_days:
                log.info("Using cached PDF: %s", f.name)
                return str(f)
        except ValueError:
            continue
    return None


def download_pdf(url: str, cache_dir: str, cache_days: int) -> str:
    """Download PDF from URL with local caching.

    Cache filename: {url_stem}_{YYYYMMDD}.pdf
      - url_stem  = the URL's filename without .pdf extension
                    (e.g. "Podiatry_LicenseVerification_20260522" from the AR_PODIATRY URL)
      - YYYYMMDD  = download date appended at save time

    Freshness check: glob for {url_stem}_????????.pdf, parse the date suffix, skip if
    the date is within cache_days of today.  When the board publishes a new PDF under a
    different URL (new date in the filename), url_stem changes, the old cached file is not
    matched, and a fresh download is triggered automatically.

    Uses Playwright Chromium browser (OS cert store — handles Zscaler SSL interception).
    Returns path to local cached file.
    """
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    # Strip query string before extracting filename (e.g. roster.pdf?tim=123 → roster.pdf)
    url_path = url.split("?")[0].split("#")[0]
    raw_filename = url_path.split("/")[-1] or "document.pdf"
    if not raw_filename.lower().endswith(".pdf"):
        raw_filename = "document.pdf"
    stem = raw_filename[:-4]  # strip .pdf extension

    cached = _find_cached_pdf(cache_dir, stem, cache_days)
    if cached:
        return cached

    log.info("Downloading PDF via browser: %s", url)

    import asyncio as _asyncio
    import concurrent.futures

    async def _browser_download() -> bytes:
        from playwright.async_api import async_playwright as _async_playwright
        async with _async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(accept_downloads=True)
                page = await context.new_page()
                # Approach 1: Content-Disposition: attachment (most government PDFs)
                try:
                    async with page.expect_download(timeout=30000) as dl_info:
                        try:
                            await page.goto(url, timeout=30000)
                        except Exception:
                            pass  # goto may raise when download starts — expected
                    download = await dl_info.value
                    path = await download.path()
                    if not path:
                        raise RuntimeError("Download path is None")
                    with open(path, "rb") as f:
                        return f.read()
                except Exception:
                    # Approach 2: inline PDF (Content-Type: application/pdf, no attachment header)
                    log.info("No download event for %s — retrying as inline PDF", url)
                    page2 = await context.new_page()
                    response = await page2.goto(url, timeout=60000)
                    if not response or not response.ok:
                        raise RuntimeError(f"HTTP {response.status if response else 'no response'}")
                    return await response.body()
            finally:
                await browser.close()

    def _run_in_thread():
        return _asyncio.run(_browser_download())

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        content = pool.submit(_run_in_thread).result(timeout=120)

    date_tag = datetime.now(_EST).strftime("%Y%m%d_%H%M")
    filepath = os.path.join(cache_dir, f"{stem}_{date_tag}.pdf")
    with open(filepath, "wb") as f:
        f.write(content)
    log.info("PDF saved → %s (%.1f KB)", os.path.basename(filepath), len(content) / 1024)
    return filepath


# ---------------------------------------------------------------------------
# Page-link PDF URL discovery
# ---------------------------------------------------------------------------

def discover_pdf_url(base_url: str, link_selector: str = "a[href*='.pdf']", proxy_cfg=None) -> str:
    """Navigate to base_url, find anchor matching link_selector, return absolute PDF URL.

    Used by pdf_bulk download_strategy: page_link — boards that don't publish a
    stable direct PDF URL but do show a PDF download link on a known landing page.
    Retries once on timeout to handle intermittently slow board sites.
    """
    import asyncio as _asyncio
    import concurrent.futures
    from urllib.parse import urljoin

    async def _find_url() -> str:
        from playwright.async_api import async_playwright as _async_playwright
        async with _async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                ctx_kwargs = {}
                if proxy_cfg:
                    ctx_kwargs["proxy"] = proxy_cfg
                ctx = await browser.new_context(ignore_https_errors=True, **ctx_kwargs)
                page = await ctx.new_page()
                last_exc = None
                for attempt in range(2):
                    try:
                        await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
                        link = await page.query_selector(link_selector)
                        if not link:
                            raise RuntimeError(f"No element matched '{link_selector}' on {base_url}")
                        href = await link.get_attribute("href")
                        if not href:
                            raise RuntimeError(f"Matched element has no href on {base_url}")
                        return urljoin(page.url, href)
                    except RuntimeError:
                        raise
                    except Exception as exc:
                        last_exc = exc
                        if attempt == 0:
                            log.warning("discover_pdf_url attempt 1 failed, retrying: %s", exc)
                        continue
                raise last_exc
            finally:
                await browser.close()

    def _run():
        return _asyncio.run(_find_url())

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_run).result(timeout=150)


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

def detect_pdf_format(rows: list[list]) -> str:
    """Return "estab" if any of the first 20 rows contain "OWNER:", else "prof"."""
    for row in rows[:20]:
        if row and "OWNER:" in " ".join(str(c) for c in row if c):
            return "estab"
    return "prof"


# ---------------------------------------------------------------------------
# Table extraction via PyMuPDF
# ---------------------------------------------------------------------------

def extract_table_data(pdf_path: str) -> tuple[list[dict[str, Any]], str]:
    """Extract all table rows from a PDF.  Returns (records, format_type)."""
    import fitz  # PyMuPDF

    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    doc = fitz.open(pdf_path)
    all_rows: list[list] = []
    pdf_format: Optional[str] = None
    headers: Optional[list] = None

    for page_num, page in enumerate(doc, 1):
        try:
            tables = page.find_tables()
            if not tables:
                continue
            for table in tables:
                rows = table.extract()
                if not rows:
                    continue

                if pdf_format is None:
                    pdf_format = detect_pdf_format(rows)
                    log.info("Detected PDF format: %s", pdf_format.upper())

                # Determine header row
                is_label_row = False
                if pdf_format == "estab" and rows:
                    first_cell = str(rows[0][0]).lower() if rows[0] and rows[0][0] else ""
                    is_label_row = "establishment" in first_cell or "registration" in first_cell

                if is_label_row:
                    header_idx, data_start = 1, 2
                elif headers is None:
                    header_idx, data_start = 0, 1
                else:
                    header_idx, data_start = -1, 0

                if header_idx >= 0 and len(rows) > header_idx:
                    headers = rows[header_idx]

                for row in rows[data_start:]:
                    all_rows.append(row)

        except Exception as exc:
            log.warning("Error on page %d: %s", page_num, exc)

    doc.close()

    if not all_rows or headers is None:
        raise ValueError("No tables found in PDF")

    records = []
    for row in all_rows:
        rec: dict[str, Any] = {}
        for i, header in enumerate(headers):
            col_name = str(header).strip() if header else f"Column_{i}"
            rec[col_name] = row[i] if i < len(row) else None
        records.append(rec)

    log.info("Extracted %d records from PDF (format: %s)", len(records), (pdf_format or "unknown").upper())
    return records, pdf_format or "prof"


# ---------------------------------------------------------------------------
# Field normalisation
# ---------------------------------------------------------------------------

def normalize_output(row: dict[str, Any], pdf_format: str) -> dict[str, Any]:
    """Map raw PDF row keys to standard field names used by the engine.

    Canonical output keys: "License Number", "Last Name", "First Name",
    "Status", "Issued", "Expiration", "Medical" (estab only).
    Any key not matched passes through unchanged so detail.field_map can handle it.
    """
    normalized: dict[str, Any] = {}
    for key, value in row.items():
        if value is None:
            continue
        kl = key.lower().strip().rstrip(".")
        vs = str(value).strip()

        if kl in ("license #", "license", "lic. no", "license no", "license number",
                  "nv license", "lic no", "lic#", "certificate #", "certificate no"):
            normalized["License Number"] = vs

        elif kl in ("last name", "licensee last name", "surname"):
            normalized["Last Name"] = vs

        elif kl in ("first name", "licensee first name", "given name"):
            normalized["First Name"] = vs

        elif kl in ("middle name", "middle", "middle initial"):
            normalized["Middle Name"] = vs

        elif kl == "name":
            if pdf_format == "estab":
                if "OWNER:" in vs or "OWNER :" in vs:
                    parts = vs.split("OWNER:")
                    normalized["Medical"] = parts[0].strip().rstrip("‐").strip()
                    owner_parts = parts[1].strip().split() if len(parts) > 1 else []
                    if owner_parts:
                        normalized["First Name"] = owner_parts[0]
                    if len(owner_parts) >= 2:
                        normalized["Last Name"] = " ".join(owner_parts[1:])
                else:
                    normalized["Medical"] = vs
            else:
                name_parts = vs.split()
                if name_parts:
                    normalized["First Name"] = name_parts[0]
                if len(name_parts) >= 2:
                    normalized["Last Name"] = " ".join(name_parts[1:])

        elif kl == "status":
            normalized["Status"] = vs

        elif kl in ("issued", "issue date"):
            normalized["Issued"] = vs

        elif kl in ("expiration", "expiration date", "expire date"):
            normalized["Expiration"] = vs

        else:
            normalized[key.strip()] = vs

    return normalized


# ---------------------------------------------------------------------------
# Search functions
# ---------------------------------------------------------------------------

def search_by_license_number(
    license_number: str, records: list[dict[str, Any]], pdf_format: str
) -> Optional[dict[str, Any]]:
    term = license_number.strip().lower()
    for row in records:
        for val in row.values():
            if val and str(val).strip().lower() == term:
                return normalize_output(row, pdf_format)
    return None


def search_by_name(
    first_name: str, last_name: str, records: list[dict[str, Any]], pdf_format: str
) -> Optional[dict[str, Any]]:
    first_t = first_name.strip().lower()
    last_t = last_name.strip().lower()
    for row in records:
        norm = normalize_output(row, pdf_format)
        fn = str(norm.get("First Name", "")).lower()
        ln = str(norm.get("Last Name", "")).lower()
        if (first_t in fn or fn == first_t) and (last_t in ln or ln == last_t):
            return norm
    return None


def search_all_by_last_name(
    last_name: str, records: list[dict[str, Any]], pdf_format: str
) -> list[dict[str, Any]]:
    term = last_name.strip().lower()
    results = []
    for row in records:
        norm = normalize_output(row, pdf_format)
        ln = str(norm.get("Last Name", "")).lower()
        if term in ln:
            results.append(norm)
    return results


def search_by_combination(
    records: list[dict[str, Any]],
    pdf_format: str,
    license_number: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    license_type: Optional[str] = None,
    provider_type: Optional[str] = None,
) -> list[dict[str, Any]]:
    """AND-filter PDF records on any combination of populated fields.

    Match semantics:
      - license_number: case-insensitive exact match against any cell value
      - first_name / last_name: case-insensitive substring on normalized First/Last Name
      - license_type / provider_type: case-insensitive substring on normalized License Type
        / Provider Type fields (silently dropped when those fields are absent)

    Returns all matching normalized rows.
    """
    lic_t = license_number.strip().lower() if license_number else None
    fn_t = first_name.strip().lower() if first_name else None
    ln_t = last_name.strip().lower() if last_name else None
    lt_t = license_type.strip().lower() if license_type else None
    pt_t = provider_type.strip().lower() if provider_type else None

    results: list[dict[str, Any]] = []
    for row in records:
        norm = normalize_output(row, pdf_format)

        if lic_t is not None:
            # Match against any cell — license number columns vary across PDFs.
            row_lic = str(norm.get("License Number", "") or "").strip().lower()
            if row_lic != lic_t:
                # Fallback: scan all original cell values
                hit = any(
                    val and str(val).strip().lower() == lic_t
                    for val in row.values()
                )
                if not hit:
                    continue

        if fn_t is not None:
            fn = str(norm.get("First Name", "")).lower()
            if fn_t not in fn:
                continue

        if ln_t is not None:
            ln = str(norm.get("Last Name", "")).lower()
            if ln_t not in ln:
                continue

        if lt_t is not None:
            lt = str(norm.get("License Type", "")).lower()
            if lt_t not in lt:
                continue

        if pt_t is not None:
            pt = str(norm.get("Provider Type", "") or norm.get("Profession", "")).lower()
            if pt_t not in pt:
                continue

        results.append(norm)

    return results
