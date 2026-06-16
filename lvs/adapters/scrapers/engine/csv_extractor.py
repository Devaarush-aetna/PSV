"""CSV bulk roster scraper — download, cache, search in-memory.

Two download strategies:
  link_text  — navigate to base_url, find anchor by visible text, fetch CSV via JS fetch
               (used by Alaska CBP: main page has a "Professional License Download" link)
  post_form  — navigate to ASP.NET form page, extract hidden tokens, POST to get CSV
               (used by Alabama ALBME: roster.aspx returns CSV on form submit)
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_EST = ZoneInfo("America/New_York")
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _find_cached_csv(cache_dir: str, source_id: str, cache_days: int) -> Optional[Path]:
    cache_path = Path(cache_dir)
    if not cache_path.exists():
        return None
    for f in sorted(cache_path.glob(f"{source_id}_????????_????.csv"), reverse=True):
        m = re.search(r"_(\d{8}_\d{4})\.csv$", f.name)
        if not m:
            continue
        try:
            file_date = datetime.strptime(m.group(1), "%Y%m%d_%H%M")
            if (datetime.now() - file_date).days <= cache_days:
                log.info("Using cached CSV: %s", f.name)
                return f
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Download strategies
# ---------------------------------------------------------------------------

async def _download_ohio_data_portal_csv(
    base_url: str, proxy_cfg=None, download_timeout_ms: int = 180_000,
) -> str:
    """Ohio data portal CSV download (data.ohio.gov/wps/portal/.../view/...).

    The portal page has an Export menu that opens a CSV download via a chained
    JS sequence: click visible #export-data → wait → click #export-contents > a.
    The standalone Ohio_All_Providers scripts use headless=False + Edge channel
    to make this work; we attempt the same flow in headless Chromium with the
    AutomationControlled hint disabled.

    Returns the CSV file content as UTF-8 string.
    """
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
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
            log.info("ohio_data_portal_csv: navigating to %s", base_url)
            await page.goto(base_url, wait_until="domcontentloaded", timeout=300_000)
            await page.wait_for_timeout(5000)

            async with page.expect_download(timeout=download_timeout_ms) as dl_info:
                await page.evaluate(
                    """async () => {
                        const els = document.querySelectorAll('#export-data');
                        for (const el of els) {
                            const s = window.getComputedStyle(el);
                            const visible = s.display !== 'none'
                                && s.visibility !== 'hidden'
                                && el.offsetParent !== null
                                && !el.closest('.hidden');
                            if (visible) { el.click(); break; }
                        }
                        await new Promise(r => setTimeout(r, 3000));
                        const csv = document.querySelector('#export-contents > a');
                        if (csv) csv.click();
                    }"""
                )
            download = await dl_info.value
            tmp_path = await download.path()
            if not tmp_path:
                raise RuntimeError(f"ohio_data_portal_csv: no download path for {base_url}")
            with open(tmp_path, "rb") as fh:
                return fh.read().decode("utf-8-sig", errors="replace")
        finally:
            await browser.close()


async def _download_link_text_xlsx(
    base_url: str,
    link_text: str,
    proxy_cfg=None,
    download_timeout_ms: int = 120_000,
    header_row: int = 0,
) -> str:
    """Click an anchor by visible text whose download is an XLSX file; convert to CSV.

    Used by boards that publish Excel rosters behind a portal page (e.g. Texas
    HHS Chemical Dependency Counselor program). Mirrors the click + download
    capture pattern used by texas_chemical_csv.py.

    Returns the worksheet content as CSV text (header_row is 0-indexed; pass
    1 to skip a generation-date row that some boards put above the columns).
    """
    import pandas as pd
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            proxy=proxy_cfg,
            ignore_https_errors=True,
            accept_downloads=True,
        )
        page = await ctx.new_page()
        try:
            log.info("link_text_xlsx: navigating to %s", base_url)
            await page.goto(base_url, wait_until="domcontentloaded", timeout=120_000)
            try:
                await page.wait_for_selector(f"text={link_text}", timeout=60_000)
            except Exception:
                pass

            log.info("link_text_xlsx: clicking link '%s'", link_text)
            async with page.expect_download(timeout=download_timeout_ms) as dl_info:
                await page.locator(f"text={link_text}").first.click()
            download = await dl_info.value
            tmp_path = await download.path()
            if not tmp_path:
                raise RuntimeError(f"link_text_xlsx: no download path for '{link_text}'")
            df = pd.read_excel(tmp_path, header=header_row, dtype=str)
            df.columns = df.columns.str.strip()
            df = df.fillna("")
            return df.to_csv(index=False)
        finally:
            await browser.close()


async def _download_direct_url(base_url: str, proxy_cfg=None, download_timeout_ms: int = 120_000) -> str:
    """Download a CSV/text file directly from a URL (no parent page, no anchor click).

    Strategy: navigate to the URL's origin root first (sets up cookies + origin
    context), then fetch the target URL. This works around CORS / corporate SSL
    inspection that ECONNRESETs Playwright's APIRequestContext but accepts
    Chromium's same-origin fetch.

    Returns the file content as a UTF-8 string. Suitable for plain-text/CSV URLs.
    """
    from urllib.parse import urlparse
    from playwright.async_api import async_playwright

    origin = "{0.scheme}://{0.netloc}/".format(urlparse(base_url))
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(proxy=proxy_cfg, ignore_https_errors=True)
        page = await ctx.new_page()
        try:
            log.info("direct_url: GET %s (via %s)", base_url, origin)
            try:
                await page.goto(origin, wait_until="domcontentloaded", timeout=30_000)
            except Exception as e:
                log.warning("direct_url: origin goto failed (%s) — proceeding with about:blank", e)
                await page.goto("about:blank")
            text = await page.evaluate(
                """async (url) => {
                    const resp = await fetch(url, {redirect: 'follow', credentials: 'include'});
                    if (!resp.ok) throw new Error('HTTP ' + resp.status + ' for ' + url);
                    return await resp.text();
                }""",
                base_url,
            )
            return text
        finally:
            await browser.close()


async def _download_link_text(base_url: str, link_text: str) -> str:
    """Navigate to base_url, find anchor whose text contains link_text, fetch via JS."""
    from playwright.async_api import async_playwright
    from .proxy import get_proxy_config
    proxy_cfg = get_proxy_config()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(proxy=proxy_cfg, ignore_https_errors=True)
        page = await ctx.new_page()
        try:
            await page.goto(base_url, wait_until="domcontentloaded", timeout=60_000)
            # Wait up to 25s for JS-rendered pages to paint the download link
            try:
                await page.wait_for_selector(
                    f'a:has-text("{link_text}")', timeout=25_000
                )
            except Exception:
                pass  # will surface as "Cannot find link" below
            # Pass link_text as a JS argument to avoid string injection
            result = await page.evaluate(
                """async (linkLower) => {
                    const links = Array.from(document.querySelectorAll('a'));
                    const target = links.find(a =>
                        a.textContent.trim().toLowerCase().includes(linkLower)
                    );
                    if (!target) {
                        throw new Error(
                            'Cannot find link containing "' + linkLower + '" on ' + window.location.href
                        );
                    }
                    const resp = await fetch(target.href);
                    if (!resp.ok) {
                        throw new Error(
                            'CSV download failed: HTTP ' + resp.status + ' for ' + target.href
                        );
                    }
                    return await resp.text();
                }""",
                link_text.lower(),
            )
            return result
        finally:
            await browser.close()


async def _download_multi_step_checkbox(
    base_url: str,
    section_text: str,
    practitioner_types: list[str],
) -> str:
    """
    CT eLicense multi-step roster download:
      1. Navigate to GenerateRoster.aspx
      2. Click section header (e.g. "Healthcare Practitioners")
      3. Check checkbox(es) by label-text match for each practitioner type
      4. Submit → DownloadRoster.aspx
      5. For each matching roster, override window.open to capture URL and fetch CSV
      6. Return merged CSV text (with _practitioner_type column added)
    """
    import io
    import pandas as pd
    from playwright.async_api import async_playwright
    from .proxy import get_proxy_config
    proxy_cfg = get_proxy_config()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(proxy=proxy_cfg, ignore_https_errors=True)
        page = await ctx.new_page()
        try:
            log.info("multi_step_checkbox: navigating to %s", base_url)
            await page.goto(base_url, wait_until="commit", timeout=120_000)
            await page.wait_for_selector(f"text={section_text}", timeout=90_000)

            # Click the section header to expand checkboxes
            await page.locator(f"text={section_text}").first.click()

            # Wait until the checkboxes in this section become visible
            # (panel expansion is CSS-animated; offsetParent flips from null → non-null)
            await page.wait_for_function(
                "() => Array.from(document.querySelectorAll('input[type=\"checkbox\"]'))"
                ".some(c => c.offsetParent !== null)",
                timeout=15_000,
            )

            # Find and check each target type's checkbox.
            # CT eLicense structure: <span><input ...></span>TypeName (No Fee Required)...
            # The label text is a TEXT NODE that immediately follows the parent <span>.
            checked = await page.evaluate(
                """(targetNames) => {
                    const visible = Array.from(
                        document.querySelectorAll('input[type="checkbox"]')
                    ).filter(c => c.offsetParent !== null);
                    const found = [];
                    for (const name of targetNames) {
                        const nameLower = name.toLowerCase();
                        const cb = visible.find(c => {
                            const textNode = c.parentElement && c.parentElement.nextSibling;
                            if (!textNode) return false;
                            const txt = (textNode.textContent || '').trim().toLowerCase();
                            return txt.startsWith(nameLower);
                        });
                        if (cb) {
                            cb.checked = true;
                            cb.dispatchEvent(new Event('change', { bubbles: true }));
                            found.push({ name, id: cb.id });
                        }
                    }
                    return found;
                }""",
                practitioner_types,
            )

            if not checked:
                raise RuntimeError(
                    f"No checkboxes found matching: {practitioner_types!r} "
                    f"in section '{section_text}' on {base_url}"
                )
            log.info("multi_step_checkbox: checked %d type(s): %s",
                     len(checked), [c["name"] for c in checked])

            # Submit — navigates to DownloadRoster.aspx
            submit = page.locator("input[type='submit'], button[type='submit']").first
            async with page.expect_navigation(wait_until="commit", timeout=120_000):
                await submit.click()

            await page.wait_for_selector("text=Roster download", timeout=90_000)

            # Discover all roster blocks on the download page
            roster_info = await page.evaluate("""() => {
                const rosters = [];
                document.querySelectorAll(
                    'input[type="submit"][value="Download"]'
                ).forEach(btn => {
                    const rosterIdnt = btn.getAttribute('RosterIdnt') || '';
                    const oc = btn.getAttribute('onclick') || '';
                    const idMatch = oc.match(/getElementById\\('([^']+)'\\)/);
                    const radioTableId = idMatch ? idMatch[1] : '';
                    let card = btn.parentElement;
                    for (let i = 0; i < 10 && card; i++) {
                        if ((card.innerText || '').includes('Roster Name')) break;
                        card = card.parentElement;
                    }
                    if (!card) return;
                    const nm = (card.innerText || '').match(
                        /Roster Name[\\s\\S]*?\\n([^\\n]+)/
                    );
                    const displayName = nm ? nm[1].trim() : '';
                    if (displayName) rosters.push({ displayName, rosterIdnt, radioTableId });
                });
                return rosters;
            }""")

            if not roster_info:
                raise RuntimeError(
                    "No roster blocks found on DownloadRoster.aspx. "
                    "Page structure may have changed."
                )

            # Match each requested type to a roster block
            dfs: list[pd.DataFrame] = []
            for ptype in practitioner_types:
                ptype_lower = ptype.lower()
                match = next(
                    (r for r in roster_info if r["displayName"].lower() == ptype_lower),
                    None,
                )
                if match is None:
                    match = next(
                        (r for r in roster_info
                         if ptype_lower in r["displayName"].lower()),
                        None,
                    )
                if match is None:
                    available = [r["displayName"] for r in roster_info]
                    log.warning("Roster not found for '%s'. Available: %s", ptype, available)
                    continue

                log.info("multi_step_checkbox: downloading '%s' ...", match["displayName"])
                result = await page.evaluate(
                    """async (args) => {
                        const radioTable = document.getElementById(args.radioTableId);
                        if (radioTable) {
                            const csvOpt = radioTable.querySelector('input[value="Comma"]');
                            if (csvOpt) {
                                csvOpt.checked = true;
                                csvOpt.dispatchEvent(new Event('change', { bubbles: true }));
                            }
                        }
                        let downloadUrl = null;
                        const origOpen = window.open;
                        window.open = (url) => { downloadUrl = url; return null; };
                        try {
                            OpenFileDownloadWindow(parseInt(args.rosterIdnt), radioTable);
                        } catch (e) {
                            window.open = origOpen;
                            throw new Error('OpenFileDownloadWindow threw: ' + e.message);
                        }
                        window.open = origOpen;
                        if (!downloadUrl) return { error: 'no download URL captured' };
                        const fullUrl = new URL(downloadUrl, window.location.href).href;
                        const resp = await fetch(fullUrl, {
                            credentials: 'include',
                            headers: { 'Accept': 'text/csv, text/plain, */*' },
                        });
                        if (!resp.ok) {
                            throw new Error('HTTP ' + resp.status + ' for ' + fullUrl);
                        }
                        return { text: await resp.text(), url: fullUrl };
                    }""",
                    {"rosterIdnt": match["rosterIdnt"], "radioTableId": match["radioTableId"]},
                )

                if isinstance(result, dict) and "error" in result:
                    raise RuntimeError(f"Download failed for '{ptype}': {result['error']}")

                csv_text = result["text"]
                df = pd.read_csv(
                    io.StringIO(csv_text), dtype=str, on_bad_lines="skip"
                )
                df.columns = df.columns.str.strip()
                df = df.fillna("")
                df["_practitioner_type"] = match["displayName"]
                dfs.append(df)
                log.info("multi_step_checkbox: '%s' → %d rows", match["displayName"], len(df))

        finally:
            await browser.close()

    if not dfs:
        raise RuntimeError("No CSV data downloaded for any practitioner type")

    merged = pd.concat(dfs, ignore_index=True)
    return merged.to_csv(index=False)


def _build_httpx_proxy_url(proxy_cfg: dict) -> Optional[str]:
    """Convert a Playwright proxy dict to an httpx-compatible proxy URL string."""
    from urllib.parse import quote, urlparse
    server = proxy_cfg.get("server", "")
    if not server:
        return None
    username = proxy_cfg.get("username", "")
    password = proxy_cfg.get("password", "")
    if username and password:
        parsed = urlparse(server)
        return (
            f"{parsed.scheme}://{quote(username, safe='')}:{quote(password, safe='')}"
            f"@{parsed.netloc}"
        )
    return server


async def _download_google_sheet_link(
    base_url: str, link_selector: str, link_selector_nth: int = 0,
    download_timeout_ms: int = 120_000,
) -> str:
    """
    Wyoming-style Google Sheets roster download:
      1. Navigate to base_url in browser to find the Google Sheets link
      2. Resolve the Google Sheets URL (via href or new-tab click)
      3. Construct CSV export URL: /d/{sheet_id}/export?format=csv
      4. Download via Playwright APIRequestContext — inherits the browser's proxy
         and cert handling, which works on corporate networks where direct httpx
         calls to docs.google.com are blocked.
    """
    from playwright.async_api import async_playwright
    from .proxy import get_proxy_config
    proxy_cfg = get_proxy_config()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            proxy=proxy_cfg, ignore_https_errors=True, accept_downloads=True,
        )
        page = await ctx.new_page()
        try:
            log.info("google_sheet_link: navigating to %s", base_url)
            await page.goto(base_url, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_selector(link_selector, timeout=30_000)

            locator = page.locator(link_selector).nth(link_selector_nth)
            href = (await locator.get_attribute("href")) or ""

            if "docs.google.com/spreadsheets" in href:
                sheet_url = href
            else:
                # Click the link — expect a new tab to open with the Google Sheet
                async with ctx.expect_page() as new_page_info:
                    await locator.click()
                new_page = await new_page_info.value
                await new_page.wait_for_load_state("domcontentloaded", timeout=30_000)
                sheet_url = new_page.url
                log.info("google_sheet_link: new tab URL = %s", sheet_url)

            if "/d/" not in sheet_url:
                raise RuntimeError(
                    f"google_sheet_link: expected Google Sheets URL but got: {sheet_url}"
                )

            # Build export URL
            if "/edit" in sheet_url:
                export_url = sheet_url.split("/edit")[0] + "/export?format=csv"
            else:
                sheet_id = sheet_url.split("/d/")[1].split("/")[0]
                export_url = (
                    f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
                )

            log.info("google_sheet_link: downloading CSV via browser page.goto from %s", export_url)
            # Chromium will trigger a download for the CSV export URL.
            # page.goto() will raise "Download is starting" — that's expected;
            # we capture the download via expect_download(), which fires concurrently.
            async with page.expect_download(timeout=download_timeout_ms) as dl_info:
                try:
                    await page.goto(export_url, timeout=download_timeout_ms)
                except Exception as e:
                    if "Download is starting" not in str(e):
                        raise
            download = await dl_info.value
            tmp_path = await download.path()
            if not tmp_path:
                raise RuntimeError(
                    f"google_sheet_link: download had no path for {export_url}"
                )
            with open(tmp_path, "rb") as fh:
                return fh.read().decode("utf-8-sig", errors="replace")
        finally:
            await browser.close()


async def _download_aithent_portal_xls(
    base_url: str, business_unit: str, proxy_cfg, download_timeout_ms: int = 120_000,
) -> str:
    """
    Nevada DPBH aithent.com portal XLS download:
      1. Navigate to portal URL (LicenseeSearch.aspx with Program=HHF&PubliSearch=Y)
      2. Select Business Unit dropdown option matching business_unit text
      3. Submit blank search → wait for results
      4. Click Excel export button → download .xls
      5. Convert XLS to CSV (header at row 11 in original) and return CSV text
    """
    import io
    import pandas as pd
    import tempfile
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
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
            log.info("aithent_portal_xls: navigating to %s", base_url)
            await page.goto(base_url, wait_until="networkidle", timeout=120_000)

            bu_sel = "select[id*='ddlBusinessUnit'], select[name*='ddlBusinessUnit']"
            await page.wait_for_selector(bu_sel, state="attached", timeout=60_000)

            bu_lower = business_unit.lower()
            # Step 1: Find and set the option value (no event yet)
            selected = await page.evaluate(
                """(buLower) => {
                    const sel = document.querySelector(
                        "select[id*='ddlBusinessUnit'], select[name*='ddlBusinessUnit']"
                    );
                    if (!sel) return null;
                    const target = Array.from(sel.options).find(o =>
                        o.text.toLowerCase().includes(buLower)
                    );
                    if (!target) return null;
                    sel.value = target.value;
                    return target.text;
                }""",
                bu_lower,
            )
            if not selected:
                raise RuntimeError(
                    f"aithent_portal_xls: Business Unit option matching {business_unit!r} not found"
                )
            log.info("aithent_portal_xls: selected business unit '%s'", selected)

            # Step 2: Fire change event and wait for the ASP.NET postback navigation
            from playwright.async_api import TimeoutError as _PWTimeout
            try:
                async with page.expect_navigation(wait_until="networkidle", timeout=90_000):
                    await page.evaluate("""() => {
                        const sel = document.querySelector(
                            "select[id*='ddlBusinessUnit'], select[name*='ddlBusinessUnit']"
                        );
                        if (sel) sel.dispatchEvent(new Event('change', { bubbles: true }));
                    }""")
            except _PWTimeout:
                # UpdatePanel partial postback may not trigger a full navigation
                try:
                    await page.wait_for_load_state("networkidle", timeout=30_000)
                except Exception:
                    pass

            # Click "Generate Excel" directly — the button is an <a> tag (ASP.NET postback link),
            # visible as soon as BU is selected; exports the full BU roster without a search step.
            log.info("aithent_portal_xls: clicking Generate Excel to export full BU roster")
            async with page.expect_download(timeout=download_timeout_ms) as dl_info:
                clicked_excel = await page.evaluate("""() => {
                    const btn = Array.from(document.querySelectorAll('a, input, button')).find(el =>
                        (el.textContent || el.value || '').trim().toLowerCase().includes('generate excel')
                        || (el.id || '').toLowerCase().includes('generateexc')
                        || (el.id || '').toLowerCase().includes('btnexcel')
                    );
                    if (btn) { btn.click(); return true; }
                    return false;
                }""")
                if not clicked_excel:
                    raise RuntimeError(
                        "aithent_portal_xls: could not find Generate Excel button"
                    )

            dl = await dl_info.value
            tmp_path = await dl.path()
            log.info("aithent_portal_xls: downloaded %s bytes", (tmp_path or "?"))

            # Convert XLS to CSV (metadata rows 0-10, header at row 11)
            df = pd.read_excel(tmp_path, header=11, engine="xlrd", dtype=str)
            df.columns = df.columns.str.strip()
            df = df.fillna("")
            csv_text = df.to_csv(index=False)
            log.info("aithent_portal_xls: converted to CSV, %d records", len(df))
            return csv_text

        finally:
            await browser.close()


async def _download_nvbop_angular_xlsx(
    base_url: str, license_type_filter: str, proxy_cfg, download_timeout_ms: int = 120_000,
) -> str:
    """
    Nevada Board of Pharmacy AngularJS portal XLSX download:
      1. Navigate to base_url (#/verifylicense)
      2. Click first radio (Personal License Search)
      3. Wait for ng-model Type <select> to appear
      4. Select matching license_type_filter option
      5. Click Search (blank) → wait for Export button
      6. Click Export To Excel → download .xlsx
      7. Convert XLSX to CSV and return CSV text
    """
    import pandas as pd
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
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
            log.info("nvbop_angular_xlsx: navigating to %s", base_url)
            await page.goto(base_url, wait_until="commit", timeout=60_000)
            await page.wait_for_selector("input[type='radio']", state="visible", timeout=60_000)

            # Click first radio — Personal License Search
            await page.locator("input[type='radio']").first.click()
            log.info("nvbop_angular_xlsx: selected Personal License Search")

            # Wait for Type <select> with ng-model binding
            type_sel = "select[ng-model='searchData.LicenseTypeId'], select[ng-model*='LicenseType']"
            await page.wait_for_selector(type_sel, state="visible", timeout=30_000)

            # Select license type via evaluate to fire AngularJS bindings
            lt_lower = license_type_filter.lower()
            selected_type = await page.evaluate(
                """(ltLower) => {
                    const sel = document.querySelector(
                        "select[ng-model='searchData.LicenseTypeId'], select[ng-model*='LicenseType']"
                    );
                    if (!sel) return null;
                    const opts = Array.from(sel.options);
                    const opt = opts.find(o => o.text.trim().toLowerCase() === ltLower)
                             || opts.find(o => o.text.trim().toLowerCase().startsWith(ltLower));
                    if (!opt) return null;
                    sel.value = opt.value;
                    sel.dispatchEvent(new Event('input',  { bubbles: true }));
                    sel.dispatchEvent(new Event('change', { bubbles: true }));
                    return opt.text;
                }""",
                lt_lower,
            )
            if not selected_type:
                raise RuntimeError(
                    f"nvbop_angular_xlsx: license type {license_type_filter!r} not found in dropdown"
                )
            log.info("nvbop_angular_xlsx: selected type '%s'", selected_type)

            # Click Search (blank)
            await page.locator("input[type='submit'][value='Search']").click()
            log.info("nvbop_angular_xlsx: submitted blank search")

            # Wait for Export button to appear (signals results loaded)
            await page.wait_for_function(
                """() => Array.from(document.querySelectorAll('button, a, input[type="button"]'))
                    .some(e => (e.textContent || e.value || '').toLowerCase().includes('export'))
                """,
                timeout=120_000,
            )
            log.info("nvbop_angular_xlsx: results loaded, triggering XLSX export")

            async with page.expect_download(timeout=download_timeout_ms) as dl_info:
                clicked_export = await page.evaluate("""() => {
                    const els = Array.from(
                        document.querySelectorAll('button, a, input[type="button"]')
                    );
                    const btn = els.find(e =>
                        (e.textContent || e.value || '').trim().toLowerCase().includes('export')
                    );
                    if (btn) { btn.click(); return true; }
                    return false;
                }""")
                if not clicked_export:
                    raise RuntimeError(
                        "nvbop_angular_xlsx: could not find Export To Excel button"
                    )

            dl = await dl_info.value
            tmp_path = await dl.path()
            log.info("nvbop_angular_xlsx: downloaded file")

            import pandas as pd
            df = pd.read_excel(tmp_path, engine="openpyxl", dtype=str)
            df.columns = df.columns.str.strip()
            df = df.fillna("")
            csv_text = df.to_csv(index=False)
            log.info("nvbop_angular_xlsx: converted to CSV, %d records", len(df))
            return csv_text

        finally:
            await browser.close()


async def _download_onedrive_excel(
    base_url: str, proxy_cfg, download_timeout_ms: int = 120_000,
) -> str:
    """OneDrive-embedded Excel roster (e.g. WV_CHIRO boc.wv.gov/roster.html).

    Pattern:
      1. Navigate to base_url and find the iframe pointing at 1drv.ms / onedrive.live.com.
      2. Translate the OneDrive sharing URL to a Microsoft Graph public-share API URL:
         https://api.onedrive.com/v1.0/shares/u!<b64>/driveItem/content
         where <b64> is the URL-safe base64 of the original 1drv.ms share URL.
         The Graph shares endpoint serves the original file bytes for public shares
         without auth — works through corporate proxies.
      3. Fetch the file via Playwright's APIRequestContext (inherits proxy + certs).
      4. Parse the XLSX/XLS via pandas, return CSV text.

    Falls back to the legacy onedrive.live.com/download?resid=... URL if the Graph
    API path is rejected (rare, but possible for some org-tenanted shares).
    """
    import base64
    import io
    import re
    import pandas as pd
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
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
            log.info("onedrive_excel: navigating to %s", base_url)
            await page.goto(base_url, wait_until="domcontentloaded", timeout=60_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass

            iframe_src = await page.evaluate("""() => {
                const frames = Array.from(document.querySelectorAll('iframe'));
                const od = frames.find(f => {
                    const s = (f.getAttribute('src') || '').toLowerCase();
                    return s.includes('1drv.ms') || s.includes('onedrive.live.com')
                        || s.includes('office.com') || s.includes('officeapps.live.com');
                });
                return od ? od.getAttribute('src') : null;
            }""")
            if not iframe_src:
                raise RuntimeError(
                    "onedrive_excel: no OneDrive iframe found on host page "
                    f"(tried 1drv.ms, onedrive.live.com on {base_url})"
                )
            log.info("onedrive_excel: iframe src = %s", iframe_src)

            # Strip query string from the iframe URL — the Graph shares API only needs
            # the canonical share path (e.g. https://1drv.ms/x/c/<id>/<token>) without
            # ?em=&wdHideGridlines=&... overrides.
            share_url = iframe_src.split("?", 1)[0]
            # Some embeds wrap the share URL in onedrive.live.com/embed?... — pull the
            # canonical share URL out if so.
            if "onedrive.live.com/embed" in share_url or "office.com" in share_url:
                # Resolve to the actual share URL via redirect
                resolver = await ctx.new_page()
                try:
                    await resolver.goto(iframe_src, wait_until="domcontentloaded", timeout=60_000)
                    share_url = resolver.url.split("?", 1)[0]
                finally:
                    await resolver.close()
            log.info("onedrive_excel: share URL = %s", share_url)

            # Build candidate download URLs. The Microsoft Graph public-share API
            # returns the file bytes directly (or a 302 to a signed download URL).
            # Browser-based fetch via JS lets us capture the response body even when
            # Chromium would otherwise render or redirect — which is what was blocking
            # the expect_download/page.goto attempts.
            b64 = base64.urlsafe_b64encode(share_url.encode("utf-8")).decode("ascii").rstrip("=")
            candidates = [
                f"https://api.onedrive.com/v1.0/shares/u!{b64}/driveItem/content",
                f"https://api.onedrive.com/v1.0/shares/u!{b64}/root/content",
                share_url + "?download=1",
            ]

            data = None
            last_err = None
            # Use the existing page (already on boc.wv.gov) so cookies / referer are
            # consistent. fetch() here uses the Chromium HTTP stack, which transparently
            # handles corporate SSL inspection (Zscaler/Netskope) — APIRequestContext
            # doesn't, hence the earlier ECONNRESET on the same URL.
            for cand_url in candidates:
                log.info("onedrive_excel: trying %s", cand_url)
                try:
                    result = await page.evaluate(
                        """async (url) => {
                            const resp = await fetch(url, {
                                method: 'GET',
                                redirect: 'follow',
                                credentials: 'omit',
                            });
                            if (!resp.ok) return { error: 'HTTP ' + resp.status };
                            const buf = await resp.arrayBuffer();
                            // Convert to base64 for the python side
                            const bytes = new Uint8Array(buf);
                            let binary = '';
                            const chunk = 0x8000;
                            for (let i = 0; i < bytes.length; i += chunk) {
                                binary += String.fromCharCode.apply(
                                    null, bytes.subarray(i, i + chunk)
                                );
                            }
                            return { b64: btoa(binary), len: bytes.length,
                                     contentType: resp.headers.get('content-type') || '' };
                        }""",
                        cand_url,
                    )
                except Exception as e:
                    last_err = e
                    log.warning("onedrive_excel: fetch eval failed for %s: %s", cand_url, str(e)[:200])
                    continue

                if isinstance(result, dict) and "error" in result:
                    last_err = result["error"]
                    log.warning("onedrive_excel: %s → %s", cand_url, result["error"])
                    continue

                if isinstance(result, dict) and "b64" in result:
                    import base64 as _b64
                    data = _b64.b64decode(result["b64"])
                    log.info(
                        "onedrive_excel: captured %d bytes (Content-Type=%s) via %s",
                        result.get("len", len(data)), result.get("contentType", ""), cand_url,
                    )
                    break

            if data is None:
                raise RuntimeError(
                    f"onedrive_excel: all download candidates failed. Last error: {last_err}"
                )

            log.info("onedrive_excel: downloaded %d bytes", len(data))
            for engine_name in ("openpyxl", "xlrd"):
                try:
                    df = pd.read_excel(io.BytesIO(data), engine=engine_name, dtype=str)
                    df.columns = df.columns.str.strip()
                    df = df.fillna("")
                    csv_text = df.to_csv(index=False)
                    log.info(
                        "onedrive_excel: parsed via %s → %d rows", engine_name, len(df)
                    )
                    return csv_text
                except Exception:
                    continue

            # Fallback: maybe the file is already CSV
            try:
                csv_text = data.decode("utf-8-sig", errors="replace")
                if csv_text.splitlines() and "," in csv_text.splitlines()[0]:
                    log.info("onedrive_excel: returning raw CSV (no excel parse)")
                    return csv_text
            except Exception:
                pass
            raise RuntimeError("onedrive_excel: downloaded file is neither XLSX, XLS, nor CSV")

        finally:
            await browser.close()


async def _download_post_form(url: str) -> str:
    """POST ASP.NET hidden-field form to receive the CSV response body."""
    from playwright.async_api import async_playwright
    from .proxy import get_proxy_config
    proxy_cfg = get_proxy_config()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(proxy=proxy_cfg, ignore_https_errors=True)
        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            result = await page.evaluate("""async () => {
                const val = id => document.querySelector('#' + id)?.value ?? '';
                const viewstate = val('__VIEWSTATE');
                const vsgen     = val('__VIEWSTATEGENERATOR');
                const evval     = val('__EVENTVALIDATION');
                const btn       = document.querySelector('input[type="submit"]');
                const btnName   = btn?.name  ?? '';
                const btnValue  = btn?.value ?? 'Get Roster';
                const params = new URLSearchParams();
                params.append('__EVENTTARGET',        '');
                params.append('__EVENTARGUMENT',      '');
                params.append('__VIEWSTATE',           viewstate);
                params.append('__VIEWSTATEGENERATOR', vsgen);
                params.append('__EVENTVALIDATION',    evval);
                if (btnName) params.append(btnName, btnValue);
                const resp = await fetch(window.location.href, {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                    body:    params.toString(),
                });
                if (!resp.ok) {
                    throw new Error('POST failed: HTTP ' + resp.status);
                }
                return await resp.text();
            }""")
            return result
        finally:
            await browser.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_csv(base_url: str, source_id: str, csv_cfg) -> Path:
    """Return the path to a fresh (possibly cached) CSV file."""
    from .proxy import get_proxy_config

    cache_dir = Path(csv_cfg.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    cached = _find_cached_csv(csv_cfg.cache_dir, source_id, csv_cfg.cache_days)
    if cached:
        return cached

    log.info("[%s] Downloading CSV (strategy=%s) ...", source_id, csv_cfg.download_strategy)
    strategy = csv_cfg.download_strategy
    dl_timeout = getattr(csv_cfg, "download_timeout_ms", 120_000)

    if strategy == "link_text":
        text = await _download_link_text(base_url, csv_cfg.link_text or "")
    elif strategy == "direct_url":
        proxy_cfg = get_proxy_config()
        text = await _download_direct_url(base_url, proxy_cfg=proxy_cfg, download_timeout_ms=dl_timeout)
    elif strategy == "link_text_xlsx":
        proxy_cfg = get_proxy_config()
        text = await _download_link_text_xlsx(
            base_url,
            csv_cfg.link_text or "",
            proxy_cfg=proxy_cfg,
            download_timeout_ms=dl_timeout,
            header_row=getattr(csv_cfg, "xlsx_header_row", 0),
        )
    elif strategy == "ohio_data_portal_csv":
        proxy_cfg = get_proxy_config()
        text = await _download_ohio_data_portal_csv(
            base_url, proxy_cfg=proxy_cfg, download_timeout_ms=dl_timeout,
        )
    elif strategy == "post_form":
        text = await _download_post_form(base_url)
    elif strategy == "multi_step_checkbox":
        text = await _download_multi_step_checkbox(
            base_url,
            csv_cfg.checkbox_section or "",
            list(csv_cfg.practitioner_types),
        )
    elif strategy == "google_sheet_link":
        text = await _download_google_sheet_link(
            base_url,
            csv_cfg.link_selector or "",
            link_selector_nth=getattr(csv_cfg, "link_selector_nth", 0),
            download_timeout_ms=dl_timeout,
        )
    elif strategy == "aithent_portal_xls":
        proxy_cfg = get_proxy_config()
        text = await _download_aithent_portal_xls(
            base_url,
            csv_cfg.business_unit or "",
            proxy_cfg=proxy_cfg,
            download_timeout_ms=dl_timeout,
        )
    elif strategy == "nvbop_angular_xlsx":
        proxy_cfg = get_proxy_config()
        text = await _download_nvbop_angular_xlsx(
            base_url,
            csv_cfg.license_type_filter or "",
            proxy_cfg=proxy_cfg,
            download_timeout_ms=dl_timeout,
        )
    elif strategy == "onedrive_excel":
        proxy_cfg = get_proxy_config()
        text = await _download_onedrive_excel(
            base_url,
            proxy_cfg=proxy_cfg,
            download_timeout_ms=dl_timeout,
        )
    else:
        raise ValueError(f"Unknown CSV download strategy: {strategy!r}")

    date_tag = datetime.now(_EST).strftime("%Y%m%d_%H%M")
    save_path = cache_dir / f"{source_id}_{date_tag}.csv"
    save_path.write_text(text, encoding=csv_cfg.encoding)
    log.info("[%s] CSV saved → %s", source_id, save_path.name)
    return save_path


def load_csv(path: Path, encoding: str = "utf-8-sig", header_row: int = 0):
    """Load CSV into a DataFrame, trying multiple encodings.

    header_row: 0-based row index of the CSV header.  Wyoming Google Sheets CSVs
    have 3 description rows before the header, so pass header_row=3 for those boards.
    """
    import pandas as pd
    for enc in (encoding, "utf-8-sig", "latin-1"):
        try:
            df = pd.read_csv(
                path, dtype=str, encoding=enc, on_bad_lines="skip",
                header=header_row,
            )
            df.columns = df.columns.str.strip()
            return df.fillna("")
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"Cannot decode {path.name} — tried utf-8-sig and latin-1")


def _find_col(df, name: str) -> str:
    """Case-insensitive column lookup that also tolerates extra whitespace."""
    for col in df.columns:
        if col.strip().upper() == name.strip().upper():
            return col
    raise KeyError(f"Column {name!r} not found (available: {list(df.columns)})")


def search_by_license_number(df, col: str, num: str) -> list[dict]:
    c = _find_col(df, col)
    col_s = df[c].str.strip()
    # 1. Exact match (case-insensitive)
    result = df[col_s.str.upper() == num.strip().upper()]
    if not result.empty:
        return result.to_dict(orient="records")
    # 2. Leading-zero normalized (e.g. "82619" matches "082619" and vice versa)
    target_norm = num.strip().lstrip("0") or "0"
    result = df[col_s.str.lstrip("0").str.upper() == target_norm.upper()]
    return result.to_dict(orient="records")


def search_by_name(df, col: str, name: str) -> list[dict]:
    """Substring, case-insensitive search against a single column."""
    c = _find_col(df, col)
    return df[df[c].str.contains(name.strip(), case=False, na=False)].to_dict(orient="records")


def search_by_multi_column(
    df,
    col_map: dict,
    license_number: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    license_type: Optional[str] = None,
    provider_type: Optional[str] = None,
) -> list[dict]:
    """AND-filter a DataFrame across multiple columns.

    col_map maps logical field names ("license_number", "first_name", "last_name",
    "license_type", "provider_type") → CSV column names. Only columns whose field
    has a non-None value are applied as filters.

    Match semantics:
      - license_number: exact match, then leading-zero-normalized exact match
      - first_name / last_name: case-insensitive substring
      - license_type / provider_type: case-insensitive exact match

    Missing columns are logged and skipped (best-effort — does NOT raise).
    """
    import pandas as pd

    mask = pd.Series([True] * len(df), index=df.index)
    applied = 0

    def _safe_col(field_name: str):
        col = col_map.get(field_name)
        if not col:
            return None
        try:
            return _find_col(df, col)
        except KeyError:
            log.warning(
                "search_by_multi_column: column %r (for field %s) not found in CSV; dropping filter",
                col, field_name,
            )
            return None

    if license_number:
        c = _safe_col("license_number")
        if c is not None:
            col_s = df[c].str.strip()
            target = license_number.strip()
            exact = col_s.str.upper() == target.upper()
            norm = col_s.str.lstrip("0").str.upper() == (target.lstrip("0") or "0").upper()
            mask &= (exact | norm)
            applied += 1

    if first_name:
        c = _safe_col("first_name")
        if c is not None:
            mask &= df[c].str.contains(first_name.strip(), case=False, na=False)
            applied += 1

    if last_name:
        c = _safe_col("last_name")
        if c is not None:
            mask &= df[c].str.contains(last_name.strip(), case=False, na=False)
            applied += 1

    if license_type:
        c = _safe_col("license_type")
        if c is not None:
            mask &= df[c].str.strip().str.upper() == license_type.strip().upper()
            applied += 1

    if provider_type:
        c = _safe_col("provider_type")
        if c is not None:
            mask &= df[c].str.strip().str.upper() == provider_type.strip().upper()
            applied += 1

    if applied == 0:
        log.warning("search_by_multi_column: no filters applied (all fields empty or columns missing)")
        return []

    return df[mask].to_dict(orient="records")
