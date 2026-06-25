"""
Board Onboarding Triage — answers "which archetype?" before any YAML is written.

Usage:
    python new_board_check.py
    python new_board_check.py --url https://some.portal.gov/lookup --state TX
    python new_board_check.py --output sites/TX_NEWBOARD/config.yaml
    python new_board_check.py --non-interactive  # print all archetypes reference

Step 0 of the board onboarding checklist. Run this BEFORE creating a config.yaml.
If the verdict is NEEDS_PYTHON, file a code-change ticket first.
"""
from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# Archetype decision tree
# ---------------------------------------------------------------------------

ARCHETYPES = {
    "csv_bulk": {
        "desc": "Bulk CSV/XLS/XLSX roster download + in-memory search",
        "signals": [
            "Site offers a downloadable CSV, XLS, or XLSX file of all licensees",
            "No per-name search needed — download entire roster then filter locally",
            "Examples: Wyoming boards (Google Sheets), Ohio data portal, ALBME",
        ],
        "needs_python": False,
        "python_note": None,
    },
    "pdf_bulk": {
        "desc": "Bulk PDF roster download + tabula/pdfplumber extraction",
        "signals": [
            "Site offers a PDF file listing all licensees",
            "PDF has consistent table rows (not scanned image)",
            "Examples: SD sdboards.org boards, WV Board of Medicine",
        ],
        "needs_python": False,
        "python_note": None,
    },
    "certemy": {
        "desc": "Certemy / Thentia Cloud Angular SPA with live-filter input",
        "signals": [
            "URL contains thentiacloud.net AND path starts with /webs/",
            "Page has a single search input that filters a results grid in real time",
            "Examples: NV_MEDBOARD, AZ_ACUPUNCTURE, WV_OPTOMETRY",
        ],
        "needs_python": False,
        "python_note": None,
    },
    "thentia_cloud": {
        "desc": "Thentia Cloud / portalus Angular SPA with dropdown search-by selector",
        "signals": [
            "URL contains thentiacloud.net AND path starts with /webs/portal/register",
            "Search form has a 'Search By' dropdown (License Number / Last Name / First Name)",
            "Examples: NV_ABA, OK_ADAC, AZ_PODIATRY, AZ_OT",
        ],
        "needs_python": False,
        "python_note": None,
    },
    "socrata_api": {
        "desc": "Socrata SoQL JSON API (data.*.gov datasets)",
        "signals": [
            "URL contains data.illinois.gov, data.ohio.gov, data.cityofchicago.org etc.",
            "Dataset has a Socrata resource ID (8-char alphanum, e.g. pzzh-kp68)",
            "Can build a SoQL WHERE query directly against the JSON endpoint",
            "Examples: IL_LICENSING",
        ],
        "needs_python": False,
        "python_note": None,
    },
    "socrata_bulk_csv": {
        "desc": "Socrata dataset downloaded as bulk CSV via browser (Zscaler-safe)",
        "signals": [
            "Socrata dataset but direct HTTP request is blocked by Zscaler SSL inspection",
            "Must use Playwright browser to navigate and download the CSV",
            "Examples: CO_DORA, DE_LICENSING, WA_HEALTH",
        ],
        "needs_python": False,
        "python_note": None,
    },
    "datatables_jsapi": {
        "desc": "jQuery DataTables global-filter (client-side, full roster in DOM)",
        "signals": [
            "Page loads all licensees at once into a DataTables-powered HTML table",
            "Filtering via window.jQuery('#tableId').DataTable().search(q).draw()",
            "Examples: TX_DENTAL, OK_DENTAL, HI_DIETITIANS, MS_OPTOMETRY",
        ],
        "needs_python": False,
        "python_note": None,
    },
    "filemaker_webdirect": {
        "desc": "FileMaker WebDirect / Vaadin 8 portal",
        "signals": [
            "URL contains fmi/webd or Vaadin script tags in page source",
            "Form interaction uses special JS event dispatch (not standard DOM fill)",
            "Examples: TX_CHIRO",
        ],
        "needs_python": False,
        "python_note": "Existing archetype — no new Python needed if board matches "
                        "TX_CHIRO's portal version. New Vaadin/FM versions may differ.",
    },
    "ag_grid_spa": {
        "desc": "Angular / React SPA with ag-Grid results table",
        "signals": [
            "Results grid uses ag-Grid (div.ag-root-wrapper in DOM)",
            "Page is a single-page app with client-side routing",
            "Examples: LA_MEDBOARD, MA_MDDO, WI_DSPS",
        ],
        "needs_python": False,
        "python_note": None,
    },
    "json_api": {
        "desc": "Direct JSON REST API (POST or GET, no browser needed)",
        "signals": [
            "Board exposes a JSON search endpoint callable via HTTP",
            "DevTools Network tab shows XHR/fetch returning JSON on search",
            "Examples: MA_MDDO",
        ],
        "needs_python": False,
        "python_note": "Existing archetype supports direct and intercept modes. "
                        "Board-specific request body format may need a new mode variant.",
    },
    "classic_html_form": {
        "desc": "Standard server-rendered HTML form (ASP.NET, PHP, Classic ASP, GLSuite)",
        "signals": [
            "Search form submits via standard HTTP POST or GET",
            "Results appear as an HTML table or card list on the response page",
            "Examples: TX_MEDBOARD, WV_DENTAL, KY_MEDBOARD, MS_LPC, OR_HLO",
        ],
        "needs_python": False,
        "python_note": None,
    },
    "NEEDS_PYTHON": {
        "desc": "No existing archetype matches — new Python code required",
        "signals": [
            "Salesforce LWC portal (e.g. mopro.mo.gov) — needs mopro_zip strategy",
            "Pega Constellation portal — needs XHR event dispatch",
            "CAPTCHA-gated boards (DataDome, hCaptcha, reCAPTCHA v3)",
            "OneDrive/SharePoint share links requiring authentication",
            "React SPA with custom export mechanism",
            "Any portal that doesn't fit the above 11 archetypes",
        ],
        "needs_python": True,
        "python_note": "File a GitHub issue / Jira ticket BEFORE creating any config. "
                        "Describe the portal vendor, the search mechanism, and the expected "
                        "result format. A developer must add the archetype first.",
    },
}

# ---------------------------------------------------------------------------
# YAML skeletons per archetype
# ---------------------------------------------------------------------------

def _skeleton(archetype: str, state: str, source_id: str, url: str) -> str:
    common_transport = textwrap.dedent(f"""\
        transport:
          browser: chromium
          headless: true
          viewport:
            width: 1280
            height: 900
          timeout_ms: 60000
          navigation_timeout_ms: 30000
          rate_limit:
            delay_between_requests_ms: 2000
            max_concurrent: 1
          retry:
            max_attempts: 3
            backoff_ms: [1000, 2000, 4000]
            retry_on: ["timeout", "network_error"]
          proxy:
            enabled: null   # true = require proxy; false = disable; null = use if configured

        evidence:
          capture_html: true
          capture_screenshot: true
          capture_on: ["search_results", "error"]
          storage: local

        compliance:
          tos_review_date: "YYYY-MM-DD"
          requires_captcha: false
          requires_login: false
          robots_txt_compliant: true

        smoke_test:
          mode: last_name
          query: "Smith"
          expect:
            min_records: 1
            full_name_contains: "Smith"
    """)

    identity = textwrap.dedent(f"""\
        identity:
          source_id: "{source_id}"
          board_name: "BOARD NAME"
          state: "{state.upper()}"
          country: "US"
          profession_codes: []   # e.g. [MD, DO, PA]
          base_url: "{url}"
          archetype: "{archetype}"
    """)

    if archetype == "csv_bulk":
        body = textwrap.dedent("""\
            csv_bulk:
              download_strategy: direct_url   # or: google_sheet_link | link_text | post_form | ohio_data_portal_csv
              file_url: ""                    # direct URL to CSV (for direct_url strategy)
              link_selector: ""              # CSS selector for download link (for link_text strategy)
              header_row: 0                  # 0-indexed row number of the header
              cache_days: 7
              cache_dir: "./PSV/CSVS"
              encoding: "utf-8-sig"
              search_columns:
                license_number: "LIC_NO"     # column header that holds license number
                last_name: "LAST_NAME"       # column header for last name search

            search:
              modes:
                - mode: license_number
                - mode: last_name
              form:
                search_by_dropdown:
                  strategy: none
                search_input:
                  selector: ""
                search_button:
                  selector: ""
              results_wait:
                strategy: delay
                timeout_ms: 1000
                no_results_indicators: []

            results:
              type: table
              has_detail_page: false
              pagination:
                enabled: false
                strategy: none

            detail:
              wait:
                strategy: delay
                timeout_ms: 500
              strategies: []
              field_map:
                "LIC_NO": license_number
                "LAST_NAME": last_name
                "FIRST_NAME": first_name
                "STATUS": status
                "EXPIRATION_DATE": expiration_date

            output:
              status_map:
                "Active": active
                "Inactive": inactive
                "Expired": expired
                "Suspended": suspended
                "Revoked": revoked
              date_formats:
                - "%m/%d/%Y"
                - "%Y-%m-%d"
        """)

    elif archetype == "pdf_bulk":
        body = textwrap.dedent("""\
            pdf_bulk:
              download_strategy: page_link   # or: direct_url
              link_selector: "a[href*='pdf']"
              cache_days: 7
              cache_dir: "./PSV/PDFS"
              pdf_columns:
                - last_name
                - first_name
                - license_type
                - license_number
                - issue_date
                - expiration_date

            search:
              modes:
                - mode: license_number
                - mode: last_name
              form:
                search_by_dropdown:
                  strategy: none
                search_input:
                  selector: ""
                search_button:
                  selector: ""
              results_wait:
                strategy: delay
                timeout_ms: 1000
                no_results_indicators: []

            results:
              type: table
              has_detail_page: false
              pagination:
                enabled: false
                strategy: none

            detail:
              wait:
                strategy: delay
                timeout_ms: 500
              strategies: []
              field_map: {}

            output:
              status_map:
                "Active": active
                "Inactive": inactive
                "Expired": expired
              date_formats:
                - "%m/%d/%Y"
                - "%Y-%m-%d"
        """)

    elif archetype in ("certemy", "thentia_cloud"):
        body = textwrap.dedent("""\
            search:
              modes:
                - mode: license_number
                  dropdown_value: "License Number"
                - mode: last_name
                  dropdown_value: "Last Name"
              form:
                search_by_dropdown:
                  strategy: custom_dropdown
                  selector: "select"
                search_input:
                  selector: "input[placeholder*='search']"
                  fallback_selectors:
                    - "input[type='text']"
                    - "input[type='search']"
                search_button:
                  selector: "button.btn-brand"
                  fallback_selectors:
                    - "[aria-label='Search']"
                    - "button[type='submit']"
              results_wait:
                strategy: element_visible
                selector: "table tbody tr"
                timeout_ms: 30000
                no_results_indicators:
                  - "no results"
                  - "no records found"
                  - "0 result"

            results:
              type: table
              table:
                row_selector: "table tbody tr"
                cell_selector: "td"
                columns:
                  0: "name"
                  1: "license_number"
                  2: "license_type"
                  3: "status"
              has_detail_page: false
              pagination:
                enabled: true
                strategy: next_button
                next_selector: "a[title='Next Page'], a.next, [aria-label='Next']"
                disabled_class: "disabled"

            detail:
              wait:
                strategy: delay
                timeout_ms: 1000
              strategies:
                - type: dt_dd
                - type: label_sibling
                - type: two_column_table
              field_map:
                "License Number": license_number
                "First Name": first_name
                "Last Name": last_name
                "License Type": license_type
                "License Status": status
                "Expiration Date": expiration_date

            output:
              status_map:
                "active": active
                "inactive": inactive
                "expired": expired
                "suspended": suspended
                "revoked": revoked
              date_formats:
                - "%m/%d/%Y"
                - "%Y-%m-%d"
                - "%B %d, %Y"
        """)

    elif archetype in ("socrata_api", "socrata_bulk_csv"):
        body = textwrap.dedent("""\
            search:
              modes:
                - mode: license_number
                - mode: last_name
              form:
                search_by_dropdown:
                  strategy: none
                search_input:
                  selector: ""
                search_button:
                  selector: ""
              results_wait:
                strategy: delay
                timeout_ms: 1000
                no_results_indicators: []

            results:
              type: table
              has_detail_page: false
              pagination:
                enabled: false
                strategy: none

            detail:
              wait:
                strategy: delay
                timeout_ms: 500
              strategies: []
              field_map:
                "license_number": license_number
                "licensee_name": full_name
                "license_type": license_type
                "status": status
                "expiration_date": expiration_date

            output:
              status_map:
                "Active": active
                "Inactive": inactive
                "Expired": expired
              date_formats:
                - "%m/%d/%Y"
                - "%Y-%m-%d"
        """)

    else:
        # classic_html_form, ag_grid_spa, datatables_jsapi, json_api, filemaker_webdirect
        body = textwrap.dedent("""\
            search:
              modes:
                - mode: last_name
                  input_selector: "#LAST_NAME_INPUT_ID"
                - mode: license_number
                  input_selector: "#LICENSE_NUMBER_INPUT_ID"
              form:
                search_by_dropdown:
                  strategy: none
                search_input:
                  selector: "#LAST_NAME_INPUT_ID"
                search_button:
                  selector: "#SEARCH_BUTTON_ID"
                  fallback_selectors:
                    - "input[type='submit']"
                    - "button[type='submit']"
              results_wait:
                strategy: element_visible
                selector: "table tbody tr"
                timeout_ms: 30000
                no_results_indicators:
                  - "no results found"
                  - "no records found"

            results:
              type: table
              table:
                row_selector: "table tbody tr"
                cell_selector: "td"
                skip_first_row: true
                columns:
                  0: full_name
                  1: license_number
                  2: license_type
                  3: status
                  4: expiration_date
              has_detail_page: false
              pagination:
                enabled: false
                strategy: none

            detail:
              wait:
                strategy: delay
                timeout_ms: 500
              strategies: []
              field_map: {}

            output:
              status_map:
                "Active": active
                "active": active
                "Inactive": inactive
                "Expired": expired
                "Suspended": suspended
                "Revoked": revoked
              date_formats:
                - "%m/%d/%Y"
                - "%Y-%m-%d"
                - "%B %d, %Y"
        """)

    return identity + "\n" + body + "\n" + common_transport


# ---------------------------------------------------------------------------
# Decision tree
# ---------------------------------------------------------------------------

def _ask(prompt: str, choices: list[str] | None = None) -> str:
    if choices:
        opts = "/".join(choices)
        full_prompt = f"{prompt} [{opts}]: "
    else:
        full_prompt = f"{prompt}: "
    while True:
        answer = input(full_prompt).strip().lower()
        if not choices:
            return answer
        if answer in [c.lower() for c in choices]:
            return answer
        print(f"  Please enter one of: {', '.join(choices)}")


def _yn(prompt: str) -> bool:
    return _ask(prompt, ["y", "n"]) == "y"


def run_triage(url: str = "", state: str = "") -> tuple[str, str]:
    """
    Walk through the decision tree interactively.
    Returns (archetype_key, reasoning_notes).
    """
    print()
    print("=" * 65)
    print("  Board Onboarding Triage — Step 0")
    print("=" * 65)
    print("Answer each question with y/n. At the end you'll get:")
    print("  • Recommended archetype")
    print("  • Whether Python code changes are needed first")
    print("  • A starter config.yaml skeleton")
    print()

    if url:
        print(f"  Board URL: {url}")
    if state:
        print(f"  State: {state.upper()}")
    print()

    notes: list[str] = []

    # Q1 — Certemy / Thentia Cloud (check URL first if provided)
    certemy_hint = "thentiacloud.net" in url.lower()
    print("Q1. Is this a Certemy or Thentia Cloud portal?")
    print("    (URL contains thentiacloud.net  OR  page has 'Thentia' branding)")
    if certemy_hint:
        print(f"    [URL hint: thentiacloud.net detected in {url}]")
    if _yn("   → Certemy / Thentia Cloud?"):
        print()
        print("Q1b. Does the page have a 'Search By' dropdown (License Number / Last Name)?")
        print("     (thentia_cloud) vs. a single live-filter input box (certemy)")
        if _yn("   → Has 'Search By' dropdown?"):
            notes.append("Thentia Cloud portal with search-by dropdown")
            return "thentia_cloud", "\n".join(notes)
        else:
            notes.append("Certemy/Thentia live-filter input")
            return "certemy", "\n".join(notes)

    # Q2 — Bulk file download
    print()
    print("Q2. Does the site offer a downloadable bulk roster file?")
    print("    (A link to download CSV, XLS, XLSX, or PDF of all licensees)")
    if _yn("   → Bulk file available?"):
        print()
        print("Q2b. Is it a PDF file (not spreadsheet)?")
        if _yn("   → PDF?"):
            notes.append("PDF bulk roster download")
            return "pdf_bulk", "\n".join(notes)
        else:
            notes.append("CSV/XLSX bulk roster download")
            return "csv_bulk", "\n".join(notes)

    # Q3 — Socrata
    print()
    print("Q3. Is this a Socrata open-data portal?")
    print("    (URL contains data.*.gov, data.cityof*.org, or has a Socrata resource ID)")
    socrata_hint = any(x in url.lower() for x in ["data.ohio.gov", "data.illinois.gov",
                                                    "data.ny.gov", "opendata.", "socrata"])
    if socrata_hint:
        print(f"    [URL hint: possible Socrata domain detected]")
    if _yn("   → Socrata dataset?"):
        print()
        print("Q3b. Is direct HTTP fetch blocked by corporate Zscaler/SSL inspection?")
        print("     (If yes, we must use a browser to navigate and download)")
        if _yn("   → Direct HTTP blocked?"):
            notes.append("Socrata dataset via browser (Zscaler-safe)")
            return "socrata_bulk_csv", "\n".join(notes)
        else:
            notes.append("Socrata SoQL JSON API — direct HTTP")
            return "socrata_api", "\n".join(notes)

    # Q4 — FileMaker WebDirect
    print()
    print("Q4. Is this a FileMaker WebDirect or Vaadin portal?")
    print("    (URL contains /fmi/webd  OR  page source has 'vaadin' script tags)")
    if _yn("   → FileMaker / Vaadin?"):
        print()
        print("  NOTE: FileMaker/Vaadin requires special JS event dispatch.")
        print("  The existing filemaker_webdirect archetype handles TX_CHIRO's portal.")
        print("  New FM/Vaadin versions may need a Python code change first.")
        notes.append("FileMaker WebDirect / Vaadin portal")
        return "filemaker_webdirect", "\n".join(notes)

    # Q5 — jQuery DataTables
    print()
    print("Q5. Does the page load ALL licensees at once into a DataTables grid?")
    print("    (No server-side search — entire roster is in the DOM on page load)")
    print("    (DevTools shows no XHR on search; only JS re-filters existing rows)")
    if _yn("   → jQuery DataTables client-side filter?"):
        notes.append("jQuery DataTables full-roster client-side filter")
        return "datatables_jsapi", "\n".join(notes)

    # Q6 — JSON API
    print()
    print("Q6. Does the board expose a direct JSON REST API?")
    print("    (DevTools Network tab shows XHR/fetch returning JSON on every search)")
    if _yn("   → Direct JSON API?"):
        notes.append("Direct JSON REST API endpoint")
        return "json_api", "\n".join(notes)

    # Q7 — ag-Grid SPA
    print()
    print("Q7. Is the results grid an ag-Grid inside an Angular or React SPA?")
    print("    (DevTools Elements shows div.ag-root-wrapper in the DOM)")
    if _yn("   → ag-Grid SPA?"):
        notes.append("ag-Grid Angular/React SPA")
        return "ag_grid_spa", "\n".join(notes)

    # Q8 — Standard HTML form
    print()
    print("Q8. Is this a standard server-rendered HTML search form?")
    print("    (ASP.NET WebForms, PHP, Classic ASP, GLSuite — page reloads on submit)")
    if _yn("   → Standard HTML form?"):
        notes.append("Standard server-rendered HTML form (classic_html_form)")
        return "classic_html_form", "\n".join(notes)

    # No match
    print()
    notes.append("No existing archetype matches this portal")
    return "NEEDS_PYTHON", "\n".join(notes)


# ---------------------------------------------------------------------------
# Output / display
# ---------------------------------------------------------------------------

def _print_verdict(archetype: str, notes: str, state: str, url: str,
                   source_id: str, output_path: str | None) -> None:
    info = ARCHETYPES[archetype]
    print()
    print("=" * 65)
    print("  TRIAGE RESULT")
    print("=" * 65)
    print(f"  Archetype : {archetype}")
    print(f"  Summary   : {info['desc']}")
    print(f"  Notes     : {notes}")
    print()

    if info["needs_python"]:
        print("  *** NEEDS PYTHON CODE CHANGE FIRST ***")
        print()
        print(textwrap.fill(info["python_note"] or "", width=63, initial_indent="  ",
                            subsequent_indent="  "))
        print()
        print("  Action: file a GitHub issue / Jira ticket before creating")
        print("  any config.yaml. A developer must add the archetype first.")
        print()
        print("  Do NOT proceed with config creation until the archetype exists.")
        return

    if info["python_note"]:
        print(f"  ⚠  Note: {info['python_note']}")
        print()

    print("  This board fits an existing archetype — no Python needed.")
    print()
    print("  Next steps:")
    print("    1. Create sites/XX_BOARD/config.yaml from the skeleton below")
    print("    2. Fill in selectors using browser DevTools")
    print("    3. Run:  python run.py --config sites/XX_BOARD/config.yaml "
          "--mode last_name --query Smith --headed")
    print("    4. Add smoke_test block with a known-good query")
    print("    5. Run:  python smoke_all.py --filter XX_BOARD")
    print("    6. Add to board_inventory.xlsx and board_routing_master.csv")
    print()

    skeleton = _skeleton(archetype, state or "XX", source_id or "XX_BOARD", url or "https://")
    print("-" * 65)
    print("  CONFIG SKELETON")
    print("-" * 65)
    print(skeleton)

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(skeleton, encoding="utf-8")
        print(f"  Skeleton saved to: {out}")


def _print_all_archetypes() -> None:
    print()
    print("=" * 65)
    print("  Known Archetypes Reference")
    print("=" * 65)
    for key, info in ARCHETYPES.items():
        tag = "  [NEEDS PYTHON]" if info["needs_python"] else ""
        print(f"\n  {key}{tag}")
        print(f"    {info['desc']}")
        for s in info["signals"]:
            print(f"    • {s}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(
        description="Board onboarding triage — run BEFORE creating a config.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--url", default="", help="Board URL (used as a hint in questions)")
    p.add_argument("--state", default="", help="Two-letter state abbreviation")
    p.add_argument("--source-id", default="", help="Proposed source_id (e.g. TX_NEWBOARD)")
    p.add_argument("--output", default=None,
                   help="Write skeleton to this path (e.g. sites/TX_NEWBOARD/config.yaml)")
    p.add_argument("--non-interactive", action="store_true",
                   help="Print all archetypes reference and exit (no triage)")
    args = p.parse_args()

    if args.non_interactive:
        _print_all_archetypes()
        return

    try:
        archetype, notes = run_triage(url=args.url, state=args.state)
    except (KeyboardInterrupt, EOFError):
        print("\n\nAborted.")
        sys.exit(0)

    sid = args.source_id or f"{args.state.upper() or 'XX'}_BOARD"
    _print_verdict(archetype, notes, state=args.state, url=args.url,
                   source_id=sid, output_path=args.output)


if __name__ == "__main__":
    main()
