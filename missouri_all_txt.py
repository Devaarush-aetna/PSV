"""
Missouri Division of Professional Registration — License Lookup
Source: https://mopro.mo.gov/license/s/license-downloads

Board type is MANDATORY — user selects from the numbered list below.
All ZIP files for the chosen board are downloaded (or served from a 7-day cache).
Each ZIP contains a tab-delimited TXT; all TXT files for the board are searched.

Run normally:
    python missouri_all_txt.py

Dump the live dropdown list from the portal (for updating BOARD_OPTIONS):
    python missouri_all_txt.py --list-boards
"""

import argparse
import asyncio
import difflib
import json
import re
import sys
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
from playwright.async_api import async_playwright

PORTAL_URL   = "https://mopro.mo.gov/license/s/license-downloads"
REFRESH_DAYS = 7
ROSTERS_DIR  = Path(__file__).parent / "missouri_rosters"
OUTPUT_DIR   = Path(__file__).parent / "missouri_output"

# Exact portal board names, title-cased for display.
# Run  python missouri_all_txt.py --list-boards  to print the live portal list
# if you need to verify or update this.
BOARD_OPTIONS = [
    "Accountancy",
    "Acupuncture",
    "Administration",
    "All Boards",
    "Architects, Engineers, Land Surveyors And Landscape Architects",
    "Athlete Agents",
    "Athletics",
    "Behavior Analyst",
    "Chiropractic Examiners",
    "Cosmetology And Barber Examiner",
    "Dental",
    "Dietitians",
    "Electrical Contractors",
    "Embalmers And Funeral Directors",
    "Endowed Care Cemeteries",
    "Geologists",
    "Healing Arts",
    "Hearing Instrument Specialists",
    "Interior Design",
    "Interpreters",
    "Marital And Family Therapists",
    "Massage Therapists",
    "Nursing",
    "Occupational Therapy",
    "Optometry",
    "Pharmacy",
    "Podiatry",
    "Private Investigators And Private Fire Investigator Examiners",
    "Professional Counselors",
    "Psychologists",
    "Real Estate",
    "Real Estate Appraisors",
    "Respiratory Care",
    "Social Workers",
    "Tattooing, Piercing And Branding",
    "Veterinary",
]

# ── browser context factory ───────────────────────────────────────────────────

def _new_context_kwargs() -> dict:
    return dict(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        ignore_https_errors=True,
        # Hide automation flags from Salesforce bot-detection
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
    )


async def _apply_stealth(page) -> None:
    """Override navigator.webdriver so the page can't detect headless Chrome."""
    await page.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )


def _launch_kwargs() -> dict:
    return dict(
        headless=True,
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        slow_mo=150,
    )


# ── helpers ───────────────────────────────────────────────────────────────────

def _board_slug(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def _find_col(df: pd.DataFrame, *patterns: str) -> str | None:
    for pat in patterns:
        for col in df.columns:
            if re.search(pat, col.strip(), re.IGNORECASE):
                return col
    return None


def _cached_txts(slug: str) -> list[Path] | None:
    """Return fresh cached TXT paths, or None if any are missing / stale."""
    board_dir = ROSTERS_DIR / slug
    if not board_dir.exists():
        return None
    files = sorted(board_dir.glob("*_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].txt"))
    if not files:
        return None
    fresh = []
    for f in files:
        m = re.search(r"_(\d{8})\.txt$", f.name)
        if not m:
            continue
        try:
            age = (datetime.now() - datetime.strptime(m.group(1), "%d%m%Y")).days
            if age <= REFRESH_DAYS:
                fresh.append(f)
            else:
                return None
        except ValueError:
            return None
    return fresh if fresh else None


# ── portal helpers (shared by scrape + download) ──────────────────────────────

async def _wait_for_page(page) -> None:
    """
    Navigate to the portal and wait until the page is interactive.
    Salesforce LWC pages render asynchronously — we wait for the visible
    heading text rather than a specific element type.
    """
    await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=120_000)
    # "Downloadable Listings" heading is visible in all screenshots → reliable signal
    await page.get_by_role("heading", name="Downloadable Listings", exact=False).wait_for(timeout=90_000)
    await page.wait_for_timeout(3_000)   # extra time for LWC combobox to mount


async def _open_board_dropdown(page) -> bool:
    """
    Click the board combobox to open it.
    Tries multiple strategies; returns True once options are visible.
    get_by_placeholder is the most reliable for Salesforce LWC comboboxes.
    """
    for clicker in [
        page.get_by_placeholder("Select Board here"),
        page.get_by_label("Select Board here"),
        page.locator("button[aria-haspopup='listbox']"),
        page.locator("[role='combobox']"),
        page.locator(".slds-combobox__form-element"),
        page.locator("lightning-combobox"),
    ]:
        try:
            if await clicker.count() > 0:
                await clicker.first.click()
                await page.wait_for_timeout(1_000)
                if await page.locator("[role='option']").count() > 0:
                    return True
        except Exception:
            continue
    return False


async def _read_open_options(page) -> list[str]:
    """Collect all label strings from an already-open dropdown."""
    try:
        await page.locator("[role='option']").first.wait_for(timeout=8_000)
    except Exception:
        return []
    return await page.evaluate("""() =>
        Array.from(document.querySelectorAll('[role="option"]'))
            .map(el => el.textContent.trim())
            .filter(t => t)
    """) or []


async def _select_board(page, board_label: str) -> None:
    """
    Select board_label in the portal dropdown.
    Tries three strategies in order:
      1. Native <select> via select_option (works even on hidden elements)
      2. Native <select> via direct JS dispatch (bypasses Playwright visibility check)
      3. Salesforce Lightning combobox: click trigger → click option
    """
    board_lower = board_label.lower()

    # ── Strategy 1: Playwright select_option (handles hidden <select>) ────────
    sel = page.locator("select").first
    if await sel.count() > 0:
        try:
            await sel.select_option(label=board_label)
            await page.wait_for_timeout(300)
            return
        except Exception:
            pass

        # Force via JS with change event (bypasses visibility restriction)
        chosen = await sel.evaluate("""(el, lbl) => {
            for (const o of el.options) {
                if (o.text.trim().toLowerCase() === lbl.toLowerCase()) {
                    el.value = o.value;
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('input',  { bubbles: true }));
                    return o.text.trim();
                }
            }
            // partial match fallback
            for (const o of el.options) {
                if (o.text.trim().toLowerCase().includes(lbl.toLowerCase())) {
                    el.value = o.value;
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('input',  { bubbles: true }));
                    return o.text.trim();
                }
            }
            return null;
        }""", board_lower)
        if chosen:
            await page.wait_for_timeout(300)
            return

    # ── Strategy 2: Lightning combobox ───────────────────────────────────────
    if not await _open_board_dropdown(page):
        raise RuntimeError(
            f'Could not open the board dropdown to select "{board_label}".'
        )

    available = await _read_open_options(page)

    # Exact match
    for locator in [
        page.locator(f"[role='option']:has-text('{board_label}')"),
        page.get_by_role("option", name=board_label),
    ]:
        if await locator.count() > 0:
            await locator.first.click()
            await page.wait_for_timeout(300)
            return

    # Fuzzy match — handles portal name drift (e.g. "Dentistry" → "Dental")
    close = difflib.get_close_matches(board_label, available, n=1, cutoff=0.4)
    if close:
        matched = close[0]
        print(f"  Note: '{board_label}' → matched portal name: '{matched}'")
        await page.locator(f"[role='option']:has-text('{matched}')").first.click()
        await page.wait_for_timeout(300)
        return

    # Nothing matched — save the portal list to a file so it's easy to read
    options_file = Path(__file__).parent / "missouri_rosters" / "_portal_options_found.txt"
    options_file.parent.mkdir(exist_ok=True)
    options_file.write_text("\n".join(available), encoding="utf-8")
    print(f"\n  Portal dropdown has {len(available)} option(s).")
    print(f"  Full list saved to: {options_file}")
    print(f"  Options: {available}")
    raise RuntimeError(
        f'Option "{board_label}" not found. See {options_file} for actual portal names.'
    )


# ── Playwright: fetch live board list from portal dropdown ────────────────────

async def _fetch_board_list() -> list[str]:
    """Open the portal, open the board dropdown, return all option labels."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**_launch_kwargs())
        ctx     = await browser.new_context(**_new_context_kwargs())
        page    = await ctx.new_page()
        await _apply_stealth(page)
        try:
            print("  Connecting to portal ...")
            await _wait_for_page(page)

            # Try native <select> first (instant, no click needed)
            sel = page.locator("select").first
            if await sel.count() > 0:
                opts = await sel.evaluate("""el =>
                    Array.from(el.options)
                        .filter(o => o.value && o.value.trim())
                        .map(o => o.text.trim())
                """)
                if opts:
                    return [o for o in opts if o.strip()]

            # Lightning combobox: click to open → read options
            opened = await _open_board_dropdown(page)
            if not opened:
                return []
            return await _read_open_options(page)
        finally:
            await browser.close()


def _get_board_list() -> list[str]:
    return list(BOARD_OPTIONS)


# ── Playwright: download all ZIPs for a board ────────────────────────────────

async def _download_board_zips(board_label: str) -> list[tuple[str, bytes]]:
    """Select the board, Submit, capture every ZIP download. Returns [(name, bytes)]."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**_launch_kwargs())
        ctx     = await browser.new_context(**_new_context_kwargs())
        page    = await ctx.new_page()
        await _apply_stealth(page)
        try:
            print(f"  Loading portal for: {board_label} ...")
            await _wait_for_page(page)

            await _select_board(page, board_label)
            print(f"  Selected: {board_label}")

            # Submit — try multiple button selectors
            for submit_sel in [
                page.get_by_role("button", name="Submit"),
                page.locator("input[type='submit'], input[value='Submit']"),
                page.locator("button:has-text('Submit')"),
            ]:
                if await submit_sel.count() > 0:
                    await submit_sel.first.click()
                    break
            print("  Submitted — waiting for download section(s) ...")

            # Wait for Download buttons
            dl_locator = page.locator(
                "input[value='Download'], button:has-text('Download')"
            )
            await dl_locator.first.wait_for(timeout=90_000)
            await page.wait_for_timeout(800)

            count = await dl_locator.count()
            print(f"  {count} ZIP file(s) available.")

            # Collect ZIP label names visible on page (e.g. "ACU.ZIP")
            zip_labels: list[str] = await page.evaluate(r"""() => {
                const out = [];
                document.querySelectorAll('*').forEach(el => {
                    if (el.children.length > 0) return;
                    const t = (el.textContent || '').trim();
                    if (/^[A-Z0-9]{2,8}\.ZIP$/i.test(t))
                        out.push(t.toUpperCase());
                });
                return [...new Set(out)];
            }""")
            print(f"  ZIP labels on page: {zip_labels}")

            results: list[tuple[str, bytes]] = []
            for i in range(count):
                btn      = dl_locator.nth(i)
                zip_name = zip_labels[i] if i < len(zip_labels) else f"FILE_{i + 1}.ZIP"
                print(f"  Downloading {zip_name} ...")
                async with page.expect_download(timeout=120_000) as dl_ctx:
                    await btn.click()
                dl   = await dl_ctx.value
                raw  = Path(await dl.path()).read_bytes()
                results.append((zip_name, raw))
                print(f"  Got {zip_name}: {len(raw):,} bytes")

            return results
        finally:
            await browser.close()


# ── extract ZIP → TXT ─────────────────────────────────────────────────────────

def _save_and_extract(slug: str, zip_bytes: bytes, date_tag: str) -> Path:
    board_dir = ROSTERS_DIR / slug
    board_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        members  = zf.namelist()
        data_mbr = next(
            (m for m in members
             if not re.search(r"filedesc", m, re.IGNORECASE)
             and m.upper().endswith(".TXT")),
            None,
        ) or next(
            (m for m in members if not re.search(r"filedesc", m, re.IGNORECASE)),
            members[0],
        )
        code      = re.sub(r"\.[^.]+$", "", data_mbr).upper()
        txt_bytes = zf.read(data_mbr)

    out = board_dir / f"{code}_{date_tag}.txt"
    out.write_bytes(txt_bytes)
    print(f"  Saved: {out.name}  ({len(txt_bytes):,} bytes)")
    return out


def _get_txts(board_label: str) -> list[Path]:
    ROSTERS_DIR.mkdir(exist_ok=True)
    slug   = _board_slug(board_label)
    cached = _cached_txts(slug)

    if cached:
        for f in cached:
            m   = re.search(r"_(\d{8})\.txt$", f.name)
            age = (datetime.now() - datetime.strptime(m.group(1), "%d%m%Y")).days
            print(f"  Using cached: {f.name}  ({age} day(s) old)")
        return cached

    print(f"Downloading roster(s) for: {board_label} ...")
    zip_list = asyncio.run(_download_board_zips(board_label))
    date_tag = datetime.now().strftime("%d%m%Y")
    return [_save_and_extract(slug, zb, date_tag) for _, zb in zip_list]


# ── data loading & search ─────────────────────────────────────────────────────

def _load_txt(path: Path) -> pd.DataFrame:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            df = pd.read_csv(path, sep="\t", dtype=str, encoding=enc, on_bad_lines="skip")
            df.columns = df.columns.str.strip()
            return df.fillna("")
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"Cannot decode {path.name}")


def _trailing_digits(s: str) -> str:
    s = re.sub(r"^\d+\.", "", s.strip())
    m = re.search(r"(\d+)$", s)
    return (m.group(1).lstrip("0") or "0") if m else s.strip()


def _search_by_license(df: pd.DataFrame, num: str) -> pd.DataFrame:
    col = _find_col(df, r"lic.*num", r"license.*num", r"num.*lic")
    if col is None:
        raise KeyError(f"No license-number column. Available: {list(df.columns)}")
    target = _trailing_digits(num)
    col_s  = df[col].str.strip()
    result = df[col_s.str.upper() == num.strip().upper()]
    if not result.empty:
        return result
    result = df[col_s.apply(_trailing_digits) == target]
    if not result.empty:
        return result
    return df[col_s.str.contains(re.escape(num.strip()), case=False, na=False)]


def _search_by_name(df: pd.DataFrame, first: str, last: str) -> pd.DataFrame:
    mask = pd.Series(True, index=df.index)
    if first:
        col = _find_col(df, r"first.*name", r"prc_first", r"fname")
        if col:
            mask &= df[col].str.contains(re.escape(first.strip()), case=False, na=False)
    if last:
        col = _find_col(df, r"last.*name", r"prc_last", r"lname")
        if col:
            mask &= df[col].str.contains(re.escape(last.strip()), case=False, na=False)
    return df[mask]


# ── display ───────────────────────────────────────────────────────────────────

def _g(row: dict, *patterns: str) -> str:
    for pat in patterns:
        for k, v in row.items():
            if k.startswith("_"):
                continue
            if re.search(pat, k, re.IGNORECASE):
                val = str(v).strip()
                if val:
                    return val
    return "N/A"


def _print_results(rows: list[dict], board_label: str) -> None:
    for i, r in enumerate(rows, 1):
        addr1 = _g(r, r"ba_address$",  r"^address$",  r"addr.*1")
        addr2 = _g(r, r"ba_2address",  r"addr.*2")
        addr  = " ".join(p for p in [addr1, addr2] if p != "N/A")
        print(f"\n  --- Result {i} ---")
        print(f"  Board             : {board_label}")
        print(f"  License #         : {_g(r, r'lic.*num')}")
        print(f"  Name              : {_g(r, r'first.*name', r'prc_first')} "
              f"{_g(r, r'last.*name', r'prc_last')}")
        print(f"  Middle Name       : {_g(r, r'middle.*name', r'prc_middle')}")
        print(f"  Suffix            : {_g(r, r'suffix')}")
        print(f"  Status            : {_g(r, r'lst_desc', r'lic.*status', r'^status')}")
        print(f"  Expiration Status : {_g(r, r'les_desc', r'exp.*status')}")
        print(f"  Issue Date        : {_g(r, r'orig.*issue', r'issue.*date', r'lic_orig')}")
        print(f"  Expiration Date   : {_g(r, r'exp.*date', r'lic_exp')}")
        print(f"  Classification    : {_g(r, r'clas_desc', r'classif')}")
        print(f"  Cert Type         : {_g(r, r'ctype_desc', r'cert.*type')}")
        print(f"  Cert Level        : {_g(r, r'cl_desc', r'cert.*level')}")
        print(f"  DBA Name          : {_g(r, r'dba')}")
        print(f"  Entity Name       : {_g(r, r'entity')}")
        print(f"  Address           : {addr or 'N/A'}")
        print(f"  City/St/Zip       : {_g(r, r'ba_city', r'^city')}, "
              f"{_g(r, r'ba_state', r'^state')}  {_g(r, r'ba_zip', r'^zip')}")
        print(f"  County            : {_g(r, r'ba_cnty', r'county')}")
        print(f"  Country           : {_g(r, r'ba_cntry', r'country')}")
        print(f"  Discipline Status : {_g(r, r'ld_desc', r'disciplin')}")
        print(f"  Source File       : {r.get('_source_file', 'N/A')}")


# ── board menu ────────────────────────────────────────────────────────────────

def _show_board_menu(boards: list[str]) -> str:
    print("Available Board Types:")
    print("-" * 54)
    for i, name in enumerate(boards, 1):
        print(f"  {i:>3}. {name}")
    print("-" * 54)
    while True:
        raw = input(f"Select board (1–{len(boards)}): ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(boards):
            return boards[int(raw) - 1]
        print(f"  Please enter a number between 1 and {len(boards)}.")


# ── entry points ──────────────────────────────────────────────────────────────

def cmd_list_boards() -> None:
    """Print the live portal board names (use to verify / update BOARD_OPTIONS)."""
    print("Fetching live board list from portal — a browser window will open briefly ...")
    try:
        boards = asyncio.run(_fetch_board_list())
    except Exception as exc:
        print(f"Error: {exc}")
        return
    if not boards:
        print("No options retrieved — portal structure may have changed.")
        return
    print(f"\nFound {len(boards)} board(s) in portal dropdown:\n")
    for i, b in enumerate(boards, 1):
        print(f"  {i:>3}. {b}")
    print("\nCompare with BOARD_OPTIONS in the script and update if needed.")


def main() -> None:
    print("=" * 62)
    print("   Missouri Professional Registration — License Lookup")
    print("=" * 62)
    print()

    # ── Step 1: Board selection (live from portal, cached 30 days) ───────────
    board_list  = _get_board_list()
    board_label = _show_board_menu(board_list)
    print(f"\nSelected: {board_label}\n")

    # ── Step 2: Search method ─────────────────────────────────────────────────
    print("Search By:")
    print("  1. Licensee Name (First / Last)")
    print("  2. License Number")
    print()
    while True:
        choice = input("Enter choice (1 or 2): ").strip()
        if choice in ("1", "2"):
            break
        print("  Invalid choice. Please enter 1 or 2.")
    print()

    first = last = lic = ""
    if choice == "1":
        first = input("First Name (or press Enter to skip): ").strip()
        last  = input("Last Name  (or press Enter to skip): ").strip()
        if not first and not last:
            print("Error: Please enter at least a first or last name.")
            return
        query_info = {
            "type":       "BY_NAME",
            "first_name": first,
            "last_name":  last,
            "board":      board_label,
        }
        safe_f       = first.replace(" ", "_") or "ANY"
        safe_l       = last.replace(" ", "_")  or "ANY"
        out_filename = f"MO_{safe_f}_{safe_l}_{_board_slug(board_label)[:30]}.json"
    else:
        lic = input("License Number: ").strip()
        if not lic:
            print("Error: License number cannot be empty.")
            return
        query_info = {
            "type":           "BY_LICENSE_NUMBER",
            "license_number": lic,
            "board":          board_label,
        }
        out_filename = f"MO_{lic.replace(' ', '_')}.json"

    print()

    # ── Step 3: Get TXT files (cache or download) ─────────────────────────────
    txt_paths = _get_txts(board_label)
    if not txt_paths:
        print("ERROR: No data files obtained.")
        return

    # ── Step 4: Search ────────────────────────────────────────────────────────
    all_rows: list[dict] = []
    for path in txt_paths:
        df = _load_txt(path)
        print(f"Loaded {len(df):,} records from {path.name}.")
        try:
            result_df = (
                _search_by_name(df, first, last)
                if choice == "1"
                else _search_by_license(df, lic)
            )
        except KeyError as e:
            print(f"  Warning: {e} — skipping {path.name}")
            continue
        rows = result_df.to_dict(orient="records")
        for row in rows:
            row["_source_file"] = path.name
        all_rows.extend(rows)

    total = len(all_rows)
    if total == 0:
        print("\nNo results found.")
    else:
        print(f"\nFound {total} result(s):")
        _print_results(all_rows, board_label)

    # ── Step 5: Save JSON ─────────────────────────────────────────────────────
    downloaded_dates = []
    for p in txt_paths:
        m = re.search(r"_(\d{8})\.txt$", p.name)
        if m:
            downloaded_dates.append(
                datetime.strptime(m.group(1), "%d%m%Y").strftime("%Y-%m-%d")
            )

    OUTPUT_DIR.mkdir(exist_ok=True)
    out_file = OUTPUT_DIR / out_filename
    out_file.write_text(
        json.dumps(
            {
                "query":                query_info,
                "source_files":         [p.name for p in txt_paths],
                "source_downloaded_on": downloaded_dates,
                "total_results":        total,
                "results":              all_rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nFull response saved to: {out_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--list-boards", action="store_true")
    args, _ = parser.parse_known_args()

    if args.list_boards:
        cmd_list_boards()
    else:
        main()
