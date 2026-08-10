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
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-process DataFrame + index caches
# Eliminates re-reading large CSVs (e.g. OH 776 MB / 2.38M rows) on every
# per-record call.  Keyed by (str(path), encoding, header_row, sep).
# _LIC_IDX_CACHE is keyed by id(df) so a refreshed download auto-rebuilds.
# ---------------------------------------------------------------------------
_DF_CACHE: dict[tuple, "pd.DataFrame"] = {}
_LIC_IDX_CACHE: dict[int, dict] = {}


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _archive_old_cache_files(cache_dir: Path, source_id: str, new_file: Path, ext: str = "csv") -> None:
    """Move old {source_id}_YYYYMMDD_HHMM.{ext} files to cache_dir/cache/ after a fresh download.

    Keeps the active cache dir clean while preserving history.
    """
    archive_dir = cache_dir / "cache"
    archive_dir.mkdir(parents=True, exist_ok=True)
    for f in cache_dir.glob(f"{source_id}_????????_????.{ext}"):
        if f == new_file:
            continue
        dest = archive_dir / f.name
        try:
            f.rename(dest)
            log.info("[%s] Archived old cache: %s → cache/%s", source_id, f.name, f.name)
        except Exception as exc:
            log.warning("[%s] Could not archive %s: %s", source_id, f.name, exc)


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
            if (datetime.now() - file_date).days < cache_days:
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


async def _download_multi_direct_url(urls: list[str], proxy_cfg=None, download_timeout_ms: int = 120_000) -> str:
    """Download multiple CSV URLs and concatenate into a single CSV string.

    Uses Playwright's expect_download mechanism (browser-native download) rather
    than JS fetch(), which is blocked by CORS on some domains (e.g. bhec.texas.gov).
    The header row from the first file is kept; subsequent files skip their header.
    Designed for boards that publish one CSV per license type sharing identical headers.
    """
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
            all_lines: list[str] = []
            for i, url in enumerate(urls):
                log.info("multi_direct_url [%d/%d]: GET %s", i + 1, len(urls), url)
                async with page.expect_download(timeout=download_timeout_ms) as dl_info:
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                    except Exception:
                        pass  # download already started; exception expected on navigation
                dl = await dl_info.value
                tmp_path = await dl.path()
                if not tmp_path:
                    raise RuntimeError(f"multi_direct_url: download path is None for {url} — {await dl.failure()}")
                raw = Path(tmp_path).read_bytes()
                for enc in ("utf-8-sig", "utf-8", "latin-1"):
                    try:
                        text = raw.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    text = raw.decode("latin-1")
                lines = [ln for ln in text.splitlines() if ln.strip()]
                if i == 0:
                    all_lines.extend(lines)       # header + data rows
                else:
                    all_lines.extend(lines[1:])   # skip duplicate header
                log.info("multi_direct_url: %s → %d data rows", url.rsplit("/", 1)[-1], len(lines) - 1)
            return "\n".join(all_lines)
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
    proxy_cfg=None,
    timeout_ms: int = 300_000,
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

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(proxy=proxy_cfg, ignore_https_errors=True)
        page = await ctx.new_page()
        try:
            log.info("multi_step_checkbox: navigating to %s", base_url)
            await page.goto(base_url, wait_until="commit", timeout=timeout_ms)
            # Use exact-match selector so e.g. "Substance Abuse Care" does NOT accidentally
            # match the broader "Behavioral/Mental Health and Substance Abuse Care" header.
            _section_selector = f'text="{section_text}"'
            await page.wait_for_selector(_section_selector, timeout=30_000)

            # Click the section header to expand checkboxes
            await page.locator(_section_selector).first.click()

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
            async with page.expect_navigation(wait_until="commit", timeout=timeout_ms):
                await submit.click()

            await page.wait_for_selector("text=Roster download", timeout=timeout_ms)

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
                # Some types (e.g. Occupational Therapist) publish "LICENSE" instead
                # of "LICENSE NO." — normalize so all types share the same column name.
                if (
                    "LICENSE NO." not in df.columns
                    or df["LICENSE NO."].str.strip().eq("").all()
                ) and "LICENSE" in df.columns:
                    df["LICENSE NO."] = df["LICENSE"]
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
    proxy_cfg: Optional[dict] = "AUTO",
) -> str:
    """
    Wyoming-style Google Sheets roster download:
      1. Navigate to base_url in browser to find the Google Sheets link
      2. Resolve the Google Sheets URL (via href or new-tab click)
      3. Construct CSV export URL: /d/{sheet_id}/export?format=csv
      4. Download via Playwright — tries four mechanisms in order:
         a) page.goto(export_url) + expect_download — Chromium handles NTLM/Kerberos proxy
            auth automatically via Windows SSO; APIRequestContext cannot (gets 407). Primary.
         a-direct) Same as (a) but in a fresh context with NO proxy — fires only when (a)
            detected a proxy block page (URLBlockedStorage).  Chromium headless launched
            without an explicit proxy goes direct; bypasses the URL-filter while keeping the
            full browser download path (redirects, cookies, Content-Disposition).
         b) ctx.request.get() without proxy — direct outbound HTTPS to Google; works when
            the corporate proxy blocks docs.google.com as "Personal Network Storage".
         c) page.inner_text("body") — some configurations render the CSV inline rather than
            triggering a download event; captured as plain text.

    proxy_cfg: pass None to force direct (no-proxy) connection; pass a Playwright proxy dict
               to force that proxy; omit (default "AUTO") to resolve from environment.
    """
    import os as _os
    import tempfile as _tempfile
    from playwright.async_api import async_playwright
    if proxy_cfg == "AUTO":
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
            _selector_found = False
            try:
                await page.wait_for_selector(link_selector, timeout=30_000)
                _selector_found = True
            except Exception:
                pass

            # If the proxy-bearing context failed to render the link selector, retry
            # the board page navigation in a fresh direct (no-proxy) context.  This
            # handles the case where a corporate proxy intercepts the .wyo.gov page,
            # strips JS, or requires auth — making the download link invisible.
            if not _selector_found and proxy_cfg:
                log.warning(
                    "google_sheet_link: link selector %r not found via proxy — "
                    "retrying board page without proxy", link_selector
                )
                ctx_direct_nav = await browser.new_context(
                    ignore_https_errors=True, accept_downloads=True,
                )
                try:
                    page_direct = await ctx_direct_nav.new_page()
                    await page_direct.goto(base_url, wait_until="domcontentloaded", timeout=60_000)
                    await page_direct.wait_for_selector(link_selector, timeout=30_000)
                    # Switch the active page/context so the rest of the function works
                    page = page_direct
                    ctx = ctx_direct_nav
                    proxy_cfg = None  # mark as direct so Attempt A uses direct context too
                    log.info("google_sheet_link: direct board page navigation succeeded")
                except Exception as _nav_exc:
                    await ctx_direct_nav.close()
                    raise RuntimeError(
                        f"google_sheet_link: link selector {link_selector!r} not found on "
                        f"{base_url} via proxy or direct navigation: {_nav_exc}"
                    )
            elif not _selector_found:
                raise RuntimeError(
                    f"google_sheet_link: link selector {link_selector!r} not found on {base_url}"
                )

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

            # ── Attempt A: page.goto + expect_download (NTLM-aware via Chromium) ──────
            # Chromium negotiates NTLM/Kerberos proxy auth automatically via Windows SSO.
            # APIRequestContext cannot do NTLM and always gets 407 from corporate proxies,
            # so page.goto + expect_download is the primary download path.
            log.info("google_sheet_link: attempt A — page.goto + expect_download: %s", export_url)
            _download_err: Exception | None = None
            try:
                async with page.expect_download(timeout=download_timeout_ms) as _dl_info:
                    try:
                        await page.goto(
                            export_url, wait_until="domcontentloaded",
                            timeout=min(30_000, download_timeout_ms),
                        )
                    except Exception:
                        pass  # navigation exception is expected when a download is triggered
                    # Quick abort checks — bail early rather than waiting the
                    # full download_timeout_ms for a download that will never fire.
                    try:
                        # Google auth redirect: sheet requires login (not public)
                        if "accounts.google.com" in page.url:
                            raise RuntimeError(
                                "google_sheet_link: attempt A — Google Sheet redirected to "
                                "accounts.google.com; sheet is not publicly shared"
                            )
                        _page_text = await page.inner_text("body", timeout=2_000)
                        if any(kw in _page_text for kw in (
                            "URLBlockedStorage", "URLBlocked", "Access Denied",
                            "blocked by", "not permitted", "Forbidden",
                        )):
                            raise RuntimeError(
                                "google_sheet_link: attempt A — proxy block page detected"
                            )
                    except RuntimeError:
                        raise
                    except Exception:
                        pass  # body not yet available — let the download timeout handle it
                _dl = await _dl_info.value
                _tmp = _tempfile.mktemp(suffix=".csv")
                await _dl.save_as(_tmp)
                try:
                    with open(_tmp, "rb") as _f:
                        _raw = _f.read()
                    log.info("google_sheet_link: attempt A succeeded — %d bytes", len(_raw))
                    for _enc in ("utf-8-sig", "utf-8", "latin-1"):
                        try:
                            return _raw.decode(_enc)
                        except UnicodeDecodeError:
                            continue
                    return _raw.decode("latin-1")
                finally:
                    try:
                        _os.unlink(_tmp)
                    except Exception:
                        pass
            except Exception as _a_exc:
                _download_err = _a_exc
                log.warning("google_sheet_link: attempt A failed (%s) — trying fallbacks", _a_exc)

            # ── Attempt A-direct: page.goto + expect_download, no proxy ─────────────
            # When Attempt A hit a proxy block page (URLBlockedStorage / Access Denied),
            # the corporate proxy filtered docs.google.com but the firewall may still
            # allow direct outbound HTTPS.  Chromium headless launched without an explicit
            # proxy setting goes direct (no system-proxy pickup in headless mode), so this
            # attempt bypasses the URL-filter while keeping the full browser download path
            # (handles redirects, Set-Cookie, Content-Disposition) that APIRequestContext lacks.
            _proxy_was_blocked = proxy_cfg and (
                "proxy block page" in str(_download_err).lower()
                or "URLBlockedStorage" in str(_download_err)
                or "urlblocked" in str(_download_err).lower()
            )
            if _proxy_was_blocked:
                log.info(
                    "google_sheet_link: attempt A-direct — no-proxy page download: %s",
                    export_url,
                )
                try:
                    _ctx_np = await browser.new_context(
                        ignore_https_errors=True, accept_downloads=True,
                    )
                    try:
                        _page_np = await _ctx_np.new_page()
                        async with _page_np.expect_download(timeout=download_timeout_ms) as _dl_info_np:
                            try:
                                await _page_np.goto(
                                    export_url, wait_until="domcontentloaded",
                                    timeout=min(30_000, download_timeout_ms),
                                )
                            except Exception:
                                pass
                            # Fast-fail if Google auth redirect (sheet not public)
                            try:
                                if "accounts.google.com" in _page_np.url:
                                    raise RuntimeError(
                                        "google_sheet_link: attempt A-direct — Google Sheet "
                                        "redirected to accounts.google.com; sheet is not public"
                                    )
                            except RuntimeError:
                                raise
                            except Exception:
                                pass
                        _dl_np = await _dl_info_np.value
                        _tmp_np = _tempfile.mktemp(suffix=".csv")
                        await _dl_np.save_as(_tmp_np)
                        try:
                            with open(_tmp_np, "rb") as _f_np:
                                _raw_np = _f_np.read()
                            log.info(
                                "google_sheet_link: attempt A-direct succeeded — %d bytes",
                                len(_raw_np),
                            )
                            for _enc in ("utf-8-sig", "utf-8", "latin-1"):
                                try:
                                    return _raw_np.decode(_enc)
                                except UnicodeDecodeError:
                                    continue
                            return _raw_np.decode("latin-1")
                        finally:
                            try:
                                _os.unlink(_tmp_np)
                            except Exception:
                                pass
                    finally:
                        await _ctx_np.close()
                except Exception as _adirect_exc:
                    log.warning(
                        "google_sheet_link: attempt A-direct failed (%s) — trying Attempt B",
                        _adirect_exc,
                    )

            # ── Attempt B: direct (no-proxy) APIRequestContext ──────────────────────
            # Works when the corporate proxy blocks docs.google.com as "Personal Network
            # Storage" but the firewall permits direct outbound HTTPS to Google.
            log.info("google_sheet_link: attempt B — direct (no-proxy) request: %s", export_url)
            try:
                ctx_direct = await browser.new_context(
                    ignore_https_errors=True, accept_downloads=True
                )
                try:
                    resp_direct = await ctx_direct.request.get(export_url, timeout=download_timeout_ms)
                    ct_direct = resp_direct.headers.get("content-type", "")
                    if resp_direct.ok and "text/html" not in ct_direct:
                        body_direct = await resp_direct.body()
                        log.info("google_sheet_link: attempt B succeeded — %d bytes", len(body_direct))
                        return body_direct.decode("utf-8-sig", errors="replace")
                    _preview = ""
                    try:
                        _preview = (await resp_direct.body())[:200].decode("utf-8", errors="replace")
                    except Exception:
                        pass
                    raise RuntimeError(
                        f"google_sheet_link: direct attempt HTTP {resp_direct.status} — {_preview[:80]}"
                    )
                finally:
                    await ctx_direct.close()
            except Exception as _b_exc:
                log.warning("google_sheet_link: attempt B failed (%s) — trying page body read", _b_exc)

            # ── Attempt C: read page body as plain CSV text ──────────────────────────
            # Some Google Sheet export configurations serve the CSV inline as plain text
            # rather than triggering a browser download event.
            log.info("google_sheet_link: attempt C — reading page body as CSV text")
            try:
                _content = await page.content()
                if "DOCTYPE" not in _content:
                    _body_text = await page.inner_text("body", timeout=5_000)
                    _lines = [ln for ln in _body_text.splitlines() if ln.strip()]
                    if _lines and len(_lines) > 1 and "," in _lines[0]:
                        log.info(
                            "google_sheet_link: attempt C succeeded — %d lines from page body",
                            len(_lines),
                        )
                        return "\n".join(_lines)
                raise RuntimeError("google_sheet_link: attempt C — page body is not CSV text")
            except RuntimeError:
                raise
            except Exception as _c_exc:
                log.warning("google_sheet_link: attempt C failed (%s)", _c_exc)

            # All attempts failed
            raise _download_err  # type: ignore[misc]
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


async def _download_mopro_zip(
    board_label: str, proxy_cfg=None, download_timeout_ms: int = 180_000,
) -> str:
    """Missouri MOPRO Salesforce LWC portal ZIP download.

    Portal: https://mopro.mo.gov/license/s/license-downloads
    Flow:
      1. Navigate and wait for the "Downloadable Listings" heading (Salesforce LWC renders async).
      2. Select board_label from the combobox (tries native <select> then Lightning combobox).
      3. Click Submit.
      4. Wait for Download button(s) and click each — one per ZIP file.
      5. Extract the tab-delimited TXT from each ZIP.
      6. Merge all TXT DataFrames and return as a tab-separated string.

    Returns the merged roster as a tab-delimited UTF-8 string (no BOM) ready for
    pandas read_csv(..., sep='\\t').
    """
    import difflib
    import io
    import zipfile
    import pandas as pd
    from playwright.async_api import async_playwright

    PORTAL_URL = "https://mopro.mo.gov/license/s/license-downloads"

    async def _select_board(page, label: str) -> None:
        label_lower = label.lower()

        # Strategy 1: native <select> element (some portal versions render one)
        sel = page.locator("select").first
        if await sel.count() > 0:
            try:
                await sel.select_option(label=label)
                await page.wait_for_timeout(300)
                return
            except Exception:
                pass
            chosen = await sel.evaluate(
                """(el, lbl) => {
                    for (const o of el.options) {
                        if (o.text.trim().toLowerCase() === lbl) {
                            el.value = o.value;
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                            el.dispatchEvent(new Event('input',  { bubbles: true }));
                            return o.text.trim();
                        }
                    }
                    for (const o of el.options) {
                        if (o.text.trim().toLowerCase().includes(lbl)) {
                            el.value = o.value;
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                            el.dispatchEvent(new Event('input',  { bubbles: true }));
                            return o.text.trim();
                        }
                    }
                    return null;
                }""",
                label_lower,
            )
            if chosen:
                await page.wait_for_timeout(300)
                return

        # Strategy 2: Salesforce Lightning combobox
        for clicker in [
            page.get_by_placeholder("Select Board here"),
            page.get_by_label("Select Board here"),
            page.locator("button[aria-haspopup='listbox']"),
            page.locator("[role='combobox']"),
            page.locator(".slds-combobox__form-element"),
        ]:
            try:
                if await clicker.count() > 0:
                    await clicker.first.click()
                    await page.wait_for_timeout(800)
                    if await page.locator("[role='option']").count() > 0:
                        break
            except Exception:
                continue
        else:
            raise RuntimeError(f"mopro_zip: could not open board dropdown for '{label}'")

        # Read available options for fuzzy match
        available: list[str] = await page.evaluate("""() =>
            Array.from(document.querySelectorAll('[role="option"]'))
                .map(el => el.textContent.trim()).filter(t => t)
        """) or []

        # Exact match first
        for locator in [
            page.locator(f"[role='option']:has-text('{label}')"),
            page.get_by_role("option", name=label),
        ]:
            if await locator.count() > 0:
                await locator.first.click()
                await page.wait_for_timeout(300)
                return

        # Fuzzy match fallback (handles minor portal name drift)
        close = difflib.get_close_matches(label, available, n=1, cutoff=0.4)
        if close:
            matched = close[0]
            log.info("mopro_zip: '%s' → fuzzy-matched portal name '%s'", label, matched)
            await page.locator(f"[role='option']:has-text('{matched}')").first.click()
            await page.wait_for_timeout(300)
            return

        raise RuntimeError(
            f"mopro_zip: option '{label}' not found in portal dropdown. "
            f"Available: {available}"
        )

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            slow_mo=150,
        )
        ctx_kwargs: dict = {
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "ignore_https_errors": True,
            "accept_downloads": True,
            "extra_http_headers": {"Accept-Language": "en-US,en;q=0.9"},
        }
        if proxy_cfg:
            ctx_kwargs["proxy"] = proxy_cfg
        ctx = await browser.new_context(**ctx_kwargs)
        page = await ctx.new_page()
        # Suppress headless detection
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        try:
            log.info("mopro_zip: navigating to %s", PORTAL_URL)
            await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=120_000)
            # Wait for the LWC component to finish rendering
            await page.get_by_role(
                "heading", name="Downloadable Listings"
            ).wait_for(timeout=90_000)
            await page.wait_for_timeout(3_000)

            log.info("mopro_zip: selecting board '%s'", board_label)
            await _select_board(page, board_label)
            # Give LWC time to commit the selection before Submit
            await page.wait_for_timeout(2_000)

            # Click Submit
            for submit_sel in [
                page.get_by_role("button", name="Submit"),
                page.locator("input[type='submit'], input[value='Submit']"),
                page.locator("button:has-text('Submit')"),
            ]:
                if await submit_sel.count() > 0:
                    await submit_sel.first.click()
                    break
            log.info("mopro_zip: submitted — waiting for Download button(s)")
            # Brief settle after Submit so LWC can start rendering download section
            await page.wait_for_timeout(2_000)

            dl_locator = page.locator(
                "input[value='Download'], button:has-text('Download')"
            )
            try:
                await dl_locator.first.wait_for(timeout=120_000)
            except Exception:
                # Save a screenshot to help diagnose what the portal rendered
                _ss_path = Path(__file__).parent.parent / f"_mopro_debug_{board_label.replace(' ', '_')}.png"
                try:
                    await page.screenshot(path=str(_ss_path), full_page=True)
                    log.warning("mopro_zip: Download button timeout for '%s'; screenshot: %s", board_label, _ss_path)
                except Exception:
                    pass
                raise
            await page.wait_for_timeout(800)

            count = await dl_locator.count()
            log.info("mopro_zip: %d ZIP file(s) available", count)

            zip_payloads: list[bytes] = []
            for i in range(count):
                log.info("mopro_zip: downloading ZIP %d/%d ...", i + 1, count)
                async with page.expect_download(timeout=download_timeout_ms) as dl_ctx:
                    await dl_locator.nth(i).click()
                dl = await dl_ctx.value
                raw = (await dl.path() and Path(await dl.path()).read_bytes()) or b""
                if not raw:
                    tmp = await dl.path()
                    raw = Path(tmp).read_bytes() if tmp else b""
                log.info("mopro_zip: ZIP %d → %d bytes", i + 1, len(raw))
                zip_payloads.append(raw)
        finally:
            await browser.close()

    # Extract TXT from each ZIP, decode, merge
    dfs: list[pd.DataFrame] = []
    for idx, zip_bytes in enumerate(zip_payloads):
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            members = zf.namelist()
            # Pick first non-filedesc .TXT member; fall back to any non-filedesc member
            data_mbr = next(
                (m for m in members
                 if not re.search(r"filedesc", m, re.IGNORECASE)
                 and m.upper().endswith(".TXT")),
                None,
            ) or next(
                (m for m in members if not re.search(r"filedesc", m, re.IGNORECASE)),
                members[0],
            )
            txt_bytes = zf.read(data_mbr)
        log.info("mopro_zip: ZIP %d member='%s' (%d bytes)", idx + 1, data_mbr, len(txt_bytes))

        if len(txt_bytes) < 10:
            log.info("mopro_zip: ZIP %d member='%s' is empty — skipping", idx + 1, data_mbr)
            continue

        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                df = pd.read_csv(
                    io.BytesIO(txt_bytes), sep="\t", dtype=str, encoding=enc,
                    on_bad_lines="skip",
                )
                df.columns = df.columns.str.strip()
                df = df.fillna("")
                if df.empty:
                    log.info("mopro_zip: ZIP %d member='%s' parsed to 0 rows — skipping", idx + 1, data_mbr)
                    break
                log.info("mopro_zip: ZIP %d → %d rows (enc=%s)", idx + 1, len(df), enc)
                dfs.append(df)
                break
            except (UnicodeDecodeError, pd.errors.EmptyDataError):
                continue

    if not dfs:
        raise RuntimeError("mopro_zip: no readable TXT data found in any downloaded ZIP")

    merged = pd.concat(dfs, ignore_index=True).fillna("") if len(dfs) > 1 else dfs[0]
    log.info("mopro_zip: merged %d record(s) from %d ZIP(s)", len(merged), len(dfs))
    return merged.to_csv(index=False, sep="\t")


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

async def get_csv(
    base_url: str, source_id: str, csv_cfg,
    proxy_cfg: Optional[dict] = "AUTO",
) -> tuple[Path, int]:
    """Return (path, effective_header_row) for a fresh (possibly cached) CSV file.

    effective_header_row is 0 when the file was produced by a multi-sheet merge or
    local_merge (clean DataFrame dump); otherwise equals csv_cfg.header_row.

    proxy_cfg: "AUTO" (default) resolves from environment; None forces direct (no-proxy);
               pass a dict to use a specific proxy for all download strategies.
    """
    if proxy_cfg == "AUTO":
        from .proxy import get_proxy_config
        _resolved_proxy = get_proxy_config()
    else:
        _resolved_proxy = proxy_cfg

    _raw_cache = Path(csv_cfg.cache_dir)
    if not _raw_cache.is_absolute():
        # Resolve relative paths against the project root (PSV_DEV/), not CWD,
        # so cache files land in PSV/CSVS/ regardless of working directory.
        # __file__ = .../PSV_DEV/lvs/adapters/scrapers/engine/csv_extractor.py
        # parents[4] = PSV_DEV/
        _raw_cache = Path(__file__).parents[4] / csv_cfg.cache_dir.lstrip("./")
    cache_dir = _raw_cache
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Determine whether the result will be a processed (header row 0) CSV.
    _is_processed = (
        bool(getattr(csv_cfg, "additional_link_selectors", []))
        or csv_cfg.download_strategy == "local_merge"
    )
    effective_header_row = 0 if _is_processed else csv_cfg.header_row

    cached = _find_cached_csv(str(cache_dir), source_id, csv_cfg.cache_days)
    if cached:
        if _is_processed:
            # Cache may have been written before the multi-sheet feature was added (raw format).
            # Detect format by peeking at the first line: if it looks like a preamble (board name /
            # description row) rather than column headers, fall back to the raw header_row.
            try:
                import pandas as _pd
                _peek = _pd.read_csv(cached, dtype=str, header=0, nrows=0, encoding=csv_cfg.encoding)
                _peek.columns = _peek.columns.str.strip()
                _first = _peek.columns[0] if len(_peek.columns) else ""
                if len(_first) > 40 or any(
                    kw in _first.lower()
                    for kw in ("wyoming", "note", "board", "license", "please", "last update")
                ):
                    effective_header_row = csv_cfg.header_row  # raw legacy cache
            except Exception:
                pass
        return cached, effective_header_row

    # Manual-placement fallback: if a file named <SOURCE_ID>_manual.csv exists in the
    # cache directory, use it directly. Allows teams to download a blocked Google Sheet
    # by hand and drop it here. Auto-detects raw format (preamble rows before header)
    # vs. processed format (header at row 0) using the same preamble check as the cache.
    _manual_path = cache_dir / f"{source_id}_manual.csv"
    if _manual_path.exists():
        log.info("[%s] Using manually-placed fallback CSV: %s", source_id, _manual_path.name)
        _manual_header_row = effective_header_row
        if _is_processed:
            try:
                import pandas as _pd_m
                _peek_m = _pd_m.read_csv(
                    _manual_path, dtype=str, header=0, nrows=0, encoding=csv_cfg.encoding
                )
                _peek_m.columns = _peek_m.columns.str.strip()
                _first_m = _peek_m.columns[0] if len(_peek_m.columns) else ""
                if len(_first_m) > 40 or any(
                    kw in _first_m.lower()
                    for kw in ("wyoming", "note", "board", "license", "please", "last update")
                ):
                    _manual_header_row = csv_cfg.header_row  # raw Google Sheet export format
            except Exception:
                pass
        return _manual_path, _manual_header_row

    log.info("[%s] Downloading CSV (strategy=%s) ...", source_id, csv_cfg.download_strategy)
    strategy = csv_cfg.download_strategy
    dl_timeout = getattr(csv_cfg, "download_timeout_ms", 120_000)

    # --- local_merge: read + normalize already-cached CSVs from peer boards ---
    if strategy == "local_merge":
        import pandas as pd
        merge_sources = getattr(csv_cfg, "merge_sources", [])
        dfs: list[pd.DataFrame] = []
        src_cache_paths: list[Path] = []
        for src_entry in merge_sources:
            src_cache = _find_cached_csv(str(cache_dir), src_entry.source_id, src_entry.cache_days)
            if not src_cache:
                log.warning("[%s] local_merge: no cached CSV for '%s' — skipping", source_id, src_entry.source_id)
                continue
            # Use the header_row declared in merge_sources. For boards with additional_link_selectors,
            # their processed cache is a DataFrame dump (header at row 0); update WY_ALL's
            # header_row for that source to 0 after the board's cache is next refreshed.
            src_df = None
            for enc in (src_entry.encoding, "utf-8-sig", "latin-1"):
                try:
                    src_df = pd.read_csv(
                        src_cache, dtype=str, encoding=enc, on_bad_lines="skip",
                        header=src_entry.header_row, sep=src_entry.separator,
                    )
                    src_df.columns = src_df.columns.str.strip()
                    src_df = src_df.fillna("")
                    break
                except UnicodeDecodeError:
                    continue
            if src_df is None:
                log.warning("[%s] local_merge: cannot decode '%s' — skipping", source_id, src_entry.source_id)
                continue
            # Rename source columns to canonical field names
            rename_map: dict[str, str] = {}
            for canonical_field, csv_col in src_entry.columns.items():
                for actual_col in src_df.columns:
                    if actual_col.strip().upper() == csv_col.strip().upper():
                        rename_map[actual_col] = canonical_field
                        break
            src_df = src_df.rename(columns=rename_map)
            keep_cols = [k for k in src_entry.columns if k in src_df.columns]
            src_df = src_df[keep_cols].copy()
            src_df["_source_board"] = src_entry.source_id
            dfs.append(src_df)
            src_cache_paths.append(src_cache)
            log.info("[%s] local_merge: %s → %d rows", source_id, src_entry.source_id, len(src_df))
        if not dfs:
            raise RuntimeError(
                f"local_merge: no data found for any of "
                f"{[s.source_id for s in merge_sources]!r}"
            )
        merged_df = pd.concat(dfs, ignore_index=True).fillna("")
        # Timestamp = max mtime of the peer board files, so the merged filename
        # reflects when the newest input was last downloaded (not the current clock).
        max_mtime = max((p.stat().st_mtime for p in src_cache_paths), default=None)
        if max_mtime is not None:
            from datetime import datetime as _dt
            date_tag = _dt.fromtimestamp(max_mtime).strftime("%Y%m%d_%H%M")
        else:
            date_tag = datetime.now().strftime("%Y%m%d_%H%M")
        save_path = cache_dir / f"{source_id}_{date_tag}.csv"
        save_path.write_text(merged_df.to_csv(index=False), encoding="utf-8")
        log.info("[%s] CSV saved → %s (%d rows total)", source_id, save_path.name, len(merged_df))
        return save_path, 0

    if strategy == "link_text":
        text = await _download_link_text(base_url, csv_cfg.link_text or "")
    elif strategy == "direct_url":
        text = await _download_direct_url(base_url, proxy_cfg=_resolved_proxy, download_timeout_ms=dl_timeout)
    elif strategy == "multi_direct_url":
        urls = getattr(csv_cfg, "multi_urls", []) or [base_url]
        text = await _download_multi_direct_url(urls, proxy_cfg=_resolved_proxy, download_timeout_ms=dl_timeout)
    elif strategy == "link_text_xlsx":
        text = await _download_link_text_xlsx(
            base_url,
            csv_cfg.link_text or "",
            proxy_cfg=_resolved_proxy,
            download_timeout_ms=dl_timeout,
            header_row=getattr(csv_cfg, "xlsx_header_row", 0),
        )
    elif strategy == "ohio_data_portal_csv":
        text = await _download_ohio_data_portal_csv(
            base_url, proxy_cfg=_resolved_proxy, download_timeout_ms=dl_timeout,
        )
    elif strategy == "post_form":
        text = await _download_post_form(base_url)
    elif strategy == "multi_step_checkbox":
        import asyncio as _asyncio
        import io as _io
        import pandas as _pd
        _sections = csv_cfg.sections
        if _sections:
            _sec_texts = await _asyncio.gather(*[
                _download_multi_step_checkbox(
                    base_url, _sec.checkbox_section, list(_sec.practitioner_types),
                    proxy_cfg=_resolved_proxy, timeout_ms=300_000,
                )
                for _sec in _sections
            ])
            _dfs = [
                _pd.read_csv(_io.StringIO(_t), dtype=str, on_bad_lines="skip").fillna("")
                for _t in _sec_texts
            ]
            text = _pd.concat(_dfs, ignore_index=True).to_csv(index=False)
        else:
            text = await _download_multi_step_checkbox(
                base_url,
                csv_cfg.checkbox_section or "",
                list(csv_cfg.practitioner_types),
                proxy_cfg=_resolved_proxy, timeout_ms=300_000,
            )
    elif strategy == "google_sheet_link":
        text = await _download_google_sheet_link(
            base_url,
            csv_cfg.link_selector or "",
            link_selector_nth=getattr(csv_cfg, "link_selector_nth", 0),
            download_timeout_ms=dl_timeout,
            proxy_cfg=_resolved_proxy,
        )
        extra_selectors = getattr(csv_cfg, "additional_link_selectors", [])
        if extra_selectors:
            import pandas as pd, io
            primary_df = pd.read_csv(
                io.StringIO(text), dtype=str, header=csv_cfg.header_row, on_bad_lines="skip"
            )
            primary_df.columns = primary_df.columns.str.strip()
            primary_df = primary_df.fillna("")
            dfs_multi: list[pd.DataFrame] = [primary_df]
            for extra_sel in extra_selectors:
                try:
                    extra_text = await _download_google_sheet_link(
                        base_url, extra_sel, download_timeout_ms=dl_timeout,
                        proxy_cfg=_resolved_proxy,
                    )
                    extra_df = pd.read_csv(
                        io.StringIO(extra_text), dtype=str, header=csv_cfg.header_row, on_bad_lines="skip"
                    )
                    extra_df.columns = extra_df.columns.str.strip()
                    extra_df = extra_df.fillna("")
                    dfs_multi.append(extra_df)
                    log.info("[%s] Additional sheet (%s): %d rows", source_id, extra_sel, len(extra_df))
                except Exception as exc:
                    raise RuntimeError(
                        f"[{source_id}] additional_link_selectors download failed for "
                        f"'{extra_sel}': {exc}"
                    ) from exc
            merged_df = pd.concat(dfs_multi, ignore_index=True).fillna("")
            date_tag = datetime.now().strftime("%Y%m%d_%H%M")
            save_path = cache_dir / f"{source_id}_{date_tag}.csv"
            save_path.write_text(merged_df.to_csv(index=False), encoding=csv_cfg.encoding)
            log.info("[%s] CSV saved → %s (%d rows)", source_id, save_path.name, len(merged_df))
            _archive_old_cache_files(cache_dir, source_id, save_path)
            return save_path, 0
    elif strategy == "aithent_portal_xls":
        text = await _download_aithent_portal_xls(
            base_url,
            csv_cfg.business_unit or "",
            proxy_cfg=_resolved_proxy,
            download_timeout_ms=dl_timeout,
        )
    elif strategy == "nvbop_angular_xlsx":
        text = await _download_nvbop_angular_xlsx(
            base_url,
            csv_cfg.license_type_filter or "",
            proxy_cfg=_resolved_proxy,
            download_timeout_ms=dl_timeout,
        )
    elif strategy == "onedrive_excel":
        text = await _download_onedrive_excel(
            base_url,
            proxy_cfg=_resolved_proxy,
            download_timeout_ms=dl_timeout,
        )
    elif strategy == "mopro_zip":
        text = await _download_mopro_zip(
            csv_cfg.board_label or "",
            proxy_cfg=_resolved_proxy,
            download_timeout_ms=dl_timeout,
        )
    else:
        raise ValueError(f"Unknown CSV download strategy: {strategy!r}")

    date_tag = datetime.now().strftime("%Y%m%d_%H%M")
    save_path = cache_dir / f"{source_id}_{date_tag}.csv"
    # mopro_zip output is already decoded Unicode — save as UTF-8 to avoid latin-1
    # encode failures from special characters (e.g. ‘ right single quote).
    save_enc = "utf-8" if strategy == "mopro_zip" else csv_cfg.encoding
    save_path.write_text(text, encoding=save_enc)
    log.info("[%s] CSV saved → %s", source_id, save_path.name)
    _archive_old_cache_files(cache_dir, source_id, save_path)
    return save_path, effective_header_row


def load_csv(
    path: Path,
    encoding: str = "utf-8-sig",
    header_row: int = 0,
    sep: str = ",",
    usecols: Optional[list[str]] = None,
):
    """Load CSV (or tab-delimited TXT) into a DataFrame, trying multiple encodings.

    header_row: 0-based row index of the CSV header.
    sep: column separator — use "\\t" for mopro_zip tab-delimited files.
    usecols: optional list of column names to load.  When provided only those
        columns are read from disk — critical for very wide CSVs like OH
        (154 columns, 2.38M rows) where loading all columns consumes 5-6 GB
        RAM.  Columns not present in the file are silently ignored.  Defaults
        to None (load all columns, preserving backward compatibility).

    Results are cached in _DF_CACHE so large files (e.g. OH 776 MB) are only
    parsed once per process regardless of how many records are processed.
    """
    import pandas as pd
    _usecols_key = tuple(sorted(usecols)) if usecols else None
    cache_key = (str(path), encoding, header_row, sep, _usecols_key)
    if cache_key in _DF_CACHE:
        log.debug("load_csv: cache hit %s", Path(str(path)).name)
        return _DF_CACHE[cache_key]
    for enc in (encoding, "utf-8-sig", "latin-1"):
        try:
            # When usecols is provided, first read the header row to resolve
            # which requested columns actually exist (handles case differences
            # and columns the caller listed that this CSV doesn't have).
            _read_usecols: Optional[list[str]] = None
            if usecols:
                try:
                    _hdr = pd.read_csv(
                        path, encoding=enc, nrows=0, sep=sep,
                        on_bad_lines="skip",
                    )
                    _avail = {c.strip().upper(): c.strip() for c in _hdr.columns}
                    _read_usecols = [
                        _avail[u.strip().upper()]
                        for u in usecols
                        if u.strip().upper() in _avail
                    ]
                    if not _read_usecols:
                        _read_usecols = None  # no overlap — fall back to all cols
                    else:
                        log.info(
                            "load_csv: column filter active — loading %d/%d columns for %s",
                            len(_read_usecols), len(_avail), Path(str(path)).name,
                        )
                except Exception:
                    _read_usecols = None

            df = pd.read_csv(
                path, dtype=str, encoding=enc, on_bad_lines="skip",
                header=header_row, sep=sep, na_filter=False,
                usecols=_read_usecols,
            )
            df.columns = df.columns.str.strip()
            _DF_CACHE[cache_key] = df
            log.info(
                "load_csv: loaded %s (%d rows, %d cols) — cached for this run",
                Path(str(path)).name, len(df), len(df.columns),
            )
            return df
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"Cannot decode {path.name} — tried utf-8-sig and latin-1")


def _find_col(df, name: str) -> str:
    """Case-insensitive column lookup that also tolerates extra whitespace."""
    for col in df.columns:
        if col.strip().upper() == name.strip().upper():
            return col
    raise KeyError(f"Column {name!r} not found (available: {list(df.columns)})")


def _build_lic_index(df, col: str) -> dict:
    """Build three sorted-array indexes for O(log n) license number lookup.

    Uses numpy argsort + searchsorted instead of pandas groupby so the index
    builds in ~2-5 s on a 2.38M-row DataFrame rather than 3-4 minutes.

    Each index entry is {"sorted": np.array, "order": np.array} where
      sorted — values sorted lexicographically (for searchsorted)
      order  — original integer positions (iloc indices) in sorted order
    """
    import numpy as np
    c = _find_col(df, col)

    def _sorted_index(arr: "np.ndarray") -> dict:
        order = np.argsort(arr, kind="stable")
        return {"sorted": arr[order], "order": order}

    upper = df[c].str.strip().str.upper().values

    norm = df[c].str.strip().str.upper().str.lstrip("0").values
    norm = np.where(norm == "", "0", norm)

    stripped = df[c].str.strip().str.upper().str.replace(r"[-\s]", "", regex=True).values

    return {
        "col": c,
        "exact": _sorted_index(upper),
        "norm": _sorted_index(norm),
        "stripped": _sorted_index(stripped),
    }


def _idx_lookup(entry: dict, value: str) -> list:
    """O(log n) lookup into a sorted-array index entry.  Returns iloc positions."""
    import numpy as np
    if not value:
        return []
    arr = entry["sorted"]
    lo = np.searchsorted(arr, value, side="left")
    hi = np.searchsorted(arr, value, side="right")
    if lo >= hi:
        return []
    return entry["order"][lo:hi].tolist()


def _get_lic_index(df, col: str) -> dict:
    """Return cached license index for *df* + *col*, building it on first access."""
    key = (id(df), col)
    if key not in _LIC_IDX_CACHE:
        log.info("Building license-number index (%d rows) for column '%s'...", len(df), col)
        _LIC_IDX_CACHE[key] = _build_lic_index(df, col)
        log.info("License-number index ready.")
    return _LIC_IDX_CACHE[key]


def search_by_license_number(df, col, num: str) -> list[dict]:
    """Search for num in col (str) or any column in col (list of str).

    When col is a list, each column is searched independently and results are
    merged (deduplicated by row position) so a record matching any column is
    returned. This supports boards like OH where the primary license number
    sits in LICENSE_NUMBER but endorsement/secondary licenses live in
    ENDORSEMENT_NUMBER_1 … ENDORSEMENT_NUMBER_7.

    Memory optimization: the sorted numpy index is only built for the PRIMARY
    column (col[0]).  Secondary/endorsement columns are searched with a fast
    vectorized pandas scan so we never allocate ~1.5 GB of numpy arrays for
    each of the 7 OH endorsement columns (which was crashing 8–16 GB machines).
    """
    if isinstance(col, list):
        # Collect iloc positions across all columns, then materialise once.
        # Also track which column each position was matched via so we can
        # override LICENSE_NUMBER/STATUS/EXPIRATION_DATE for endorsement hits.
        import numpy as np
        all_pos: list[int] = []
        seen_pos: set[int] = set()
        pos_to_col: dict[int, str] = {}  # iloc pos → matched column name
        num_u = num.strip().upper()
        num_norm = num_u.lstrip("0") or "0"
        num_stripped = re.sub(r"[-\s]", "", num_u)
        for col_idx, c_name in enumerate(col):
            try:
                c_actual = _find_col(df, c_name)
            except KeyError:
                continue
            try:
                positions: list[int] = []
                if col_idx == 0:
                    # Primary column: use the sorted numpy index for O(log n) lookup.
                    idx = _get_lic_index(df, c_name)
                    positions.extend(_idx_lookup(idx["exact"], num_u))
                    if not positions:
                        positions.extend(_idx_lookup(idx["norm"], num_norm))
                    if not positions:
                        mask_col = df[c_actual].str.strip().str.upper().str.contains(num_u, regex=False, na=False)
                        positions = list(np.where(mask_col.values)[0])
                    if not positions:
                        positions.extend(_idx_lookup(idx["stripped"], num_stripped))
                else:
                    # Endorsement / secondary columns: vectorized pandas scan to avoid
                    # building a ~1.5 GB sorted numpy index per column.  O(n) but no
                    # persistent RAM — the temporary boolean Series is GC'd immediately.
                    col_s = df[c_actual].str.strip().str.upper()
                    mask = (
                        (col_s == num_u)
                        | (col_s.str.lstrip("0") == num_norm)
                        | col_s.str.contains(num_u, regex=False, na=False)
                        | (col_s.str.replace(r"[-\s]", "", regex=True) == num_stripped)
                    )
                    positions = list(np.where(mask.values)[0])
                for p in positions:
                    if p not in seen_pos:
                        seen_pos.add(p)
                        all_pos.append(p)
                        pos_to_col[p] = c_name
            except Exception:
                continue
        if not all_pos:
            return []
        rows = df.iloc[all_pos].to_dict(orient="records")
        # For records matched via an endorsement column, override the primary
        # license/status/expiry fields so downstream scoring sees the endorsement
        # value rather than the base license (e.g. RN.364874 → APRN.CNP.0026867).
        primary_col = col[0]  # typically LICENSE_NUMBER
        for i, pos in enumerate(all_pos):
            matched_col = pos_to_col.get(pos, primary_col)
            if matched_col == primary_col:
                continue
            # Derive endorsement index from column name (ENDORSEMENT_NUMBER_N → N)
            end_idx = None
            try:
                end_idx = int(matched_col.rsplit("_", 1)[-1])
            except (ValueError, AttributeError):
                pass
            row = rows[i]
            # Always override LICENSE_NUMBER with the searched endorsement value
            row["LICENSE_NUMBER"] = num.strip()
            if end_idx is not None:
                end_status = row.get(f"ENDORSEMENT_STATUS_{end_idx}", "")
                end_expiry = row.get(f"ENDORSEMENT_EXPIRATION_DATE_{end_idx}", "")
                if end_status:
                    row["STATUS"] = end_status
                if end_expiry:
                    row["EXPIRATION_DATE"] = end_expiry
        return rows

    idx = _get_lic_index(df, col)
    c = idx["col"]
    num_u = num.strip().upper()

    # Stage 1 + 2 merged: exact match AND leading-zero-normalized match are combined
    # so that boards assigning the same numeric value with different zero-padding
    # (e.g. "5026" for one provider and "005026" for another) both surface together.
    # Previously Stage 2 only ran when Stage 1 returned nothing, silently skipping
    # zero-padded variants whenever an exact hit existed on a different provider.
    seen: set[int] = set()
    merged: list[int] = []
    for pos in _idx_lookup(idx["exact"], num_u):
        if pos not in seen:
            seen.add(pos)
            merged.append(pos)
    norm_key = num_u.lstrip("0") or "0"
    for pos in _idx_lookup(idx["norm"], norm_key):
        if pos not in seen:
            seen.add(pos)
            merged.append(pos)
    if merged:
        return df.iloc[merged].to_dict(orient="records")

    # Stage 3: Substring — can't be indexed; vectorized pandas scan
    result = df[df[c].str.strip().str.upper().str.contains(num_u, regex=False, na=False)]
    if not result.empty:
        return result.to_dict(orient="records")

    # Stage 4: Dash/space-stripped  O(log n)
    rows = _idx_lookup(idx["stripped"], re.sub(r"[-\s]", "", num_u))
    if rows:
        return df.iloc[rows].to_dict(orient="records")

    return []


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
        lic_col = col_map.get("license_number")
        if isinstance(lic_col, list):
            # OR-match across all columns in the list
            import pandas as _pd
            lic_mask = _pd.Series([False] * len(df), index=df.index)
            target = license_number.strip()
            target_u = target.upper()
            target_norm = (target_u.lstrip("0") or "0")
            for _cn in lic_col:
                try:
                    _c = _find_col(df, _cn)
                    _s = df[_c].str.strip()
                    lic_mask |= (_s.str.upper() == target_u) | (_s.str.lstrip("0").str.upper() == target_norm)
                except KeyError:
                    log.warning("search_by_multi_column: column %r not found; skipping", _cn)
            mask &= lic_mask
            applied += 1
        else:
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
