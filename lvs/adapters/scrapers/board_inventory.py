"""
Board inventory utility — reads Resources_Aetna_01Jun2026.xlsx and emits
a filtered YAML list of qualifying boards (no captcha, web-scrapable).

Usage:
  python board_inventory.py                           # print to stdout
  python board_inventory.py --output qualifying.yaml  # write to file
  python board_inventory.py --state NV               # filter by state
  python board_inventory.py --stats                  # summary statistics only
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

try:
    import openpyxl
except ImportError:
    print("ERROR: openpyxl not installed. Run: pip install openpyxl")
    sys.exit(1)

EXCEL_PATH = Path(__file__).parent.parent.parent.parent / "Resources_Aetna_01Jun2026.xlsx"

# Map auto-generated source_ids (from Excel board_name truncation) to the canonical
# engine source_id used in sites/XX_BOARD/config.yaml.
_SOURCE_ID_OVERRIDES: dict[str, str] = {
    "VIRGINIA_VIRGINIA_DEPARTMENT_": "VA_DHP",
}

# Boards that have engine configs but are NOT in the Excel (e.g. from standalone scripts).
# These are injected into the qualifying board list automatically.
_EXTRA_BOARDS: list[dict] = [
    {
        "state": "Illinois",
        "provider_name": "All Provider Types",
        "board_name": "Illinois Department of Financial and Professional Regulation — Professional License Data",
        "url": "https://data.illinois.gov/resource/pzzh-kp68.json",
        "captcha": "No",
        "cost": "Free",
        "ingestion_type": "Web scraping/ API",
        "profession_type": "All Providers (63 IDFPR license types)",
        "has_license_number": "Yes",
        "notes": "Socrata dataset pzzh-kp68 on data.illinois.gov. Integrated from standalone illinois.py. "
                 "No proxy required. socrata_bulk_csv archetype.",
        "source_id": "IL_LICENSING",
    },
    # ── Mississippi boards (integrated from standalone scripts, session 31) ──────
    {
        "state": "Mississippi",
        "provider_name": "Chiropractors",
        "board_name": "Mississippi Board of Chiropractic Examiners",
        "url": "https://www.msbce.ms.gov/secure/licenseverification.asp",
        "captcha": "No",
        "cost": "Free",
        "ingestion_type": "Web scraping",
        "profession_type": "DC",
        "has_license_number": "No",
        "notes": "Classic ASP form. last_name search only. Table results (bgcolor=#FFFFFF). "
                 "classic_html_form archetype.",
        "source_id": "MS_CHIRO",
    },
    {
        "state": "Mississippi",
        "provider_name": "Optometrists",
        "board_name": "Mississippi State Board of Optometry",
        "url": "https://www.ms.gov/msbo/license_renewal/home/licenseverification",
        "captcha": "No",
        "cost": "Free",
        "ingestion_type": "Web scraping",
        "profession_type": "OD",
        "has_license_number": "Yes",
        "notes": "DataTables client-side search. Full roster loaded at page load; jQuery DataTables "
                 "global filter searches all rows. datatables_jsapi archetype.",
        "source_id": "MS_OPTOMETRY",
    },
    {
        "state": "Mississippi",
        "provider_name": "Physical Therapists",
        "board_name": "Mississippi Board of Physical Therapy",
        "url": "https://www.msbpt.ms.gov/secure/licenseverification.asp",
        "captcha": "No",
        "cost": "Free",
        "ingestion_type": "Web scraping",
        "profession_type": "PT, PTA",
        "has_license_number": "Yes",
        "notes": "Classic ASP form. license_number or last_name search. Fieldset card results "
                 "(fieldset.frameset2). classic_html_form archetype.",
        "source_id": "MS_PT",
    },
    # ── Missouri boards (integrated from missouri_all_txt.py standalone, session 31) ─
    {
        "state": "Missouri",
        "provider_name": "MD, DO, PA, RP, RCP",
        "board_name": "Missouri State Board of Healing Arts",
        "url": "https://mopro.mo.gov/license/s/license-downloads",
        "captcha": "No",
        "cost": "Free",
        "ingestion_type": "Web scraping",
        "profession_type": "MD, DO, PA, RP, RCP, EMT",
        "has_license_number": "Yes",
        "notes": "Salesforce LWC portal. ZIP download containing tab-delimited TXT roster. "
                 "board_label: 'Healing Arts'. Requires mopro_zip csv_bulk strategy. "
                 "csv_bulk archetype. Use missouri_all_txt.py standalone until engine supports mopro_zip.",
        "source_id": "MO_HEALING_ARTS",
    },
    {
        "state": "Missouri",
        "provider_name": "RN, LPN, APRN",
        "board_name": "Missouri State Board of Nursing",
        "url": "https://mopro.mo.gov/license/s/license-downloads",
        "captcha": "No",
        "cost": "Free",
        "ingestion_type": "Web scraping",
        "profession_type": "RN, LPN, APRN",
        "has_license_number": "Yes",
        "notes": "Salesforce LWC portal. ZIP download containing tab-delimited TXT roster. "
                 "board_label: 'Nursing'. Requires mopro_zip csv_bulk strategy. "
                 "csv_bulk archetype. Use missouri_all_txt.py standalone until engine supports mopro_zip.",
        "source_id": "MO_NURSING",
    },
    {
        "state": "Missouri",
        "provider_name": "DDS, DMD, RDH, DA",
        "board_name": "Missouri Dental Board",
        "url": "https://mopro.mo.gov/license/s/license-downloads",
        "captcha": "No",
        "cost": "Free",
        "ingestion_type": "Web scraping",
        "profession_type": "DDS, DMD, RDH, DA",
        "has_license_number": "Yes",
        "notes": "Salesforce LWC portal. ZIP download containing tab-delimited TXT roster. "
                 "board_label: 'Dental'. Requires mopro_zip csv_bulk strategy. "
                 "csv_bulk archetype. Use missouri_all_txt.py standalone until engine supports mopro_zip.",
        "source_id": "MO_DENTAL",
    },
    {
        "state": "Missouri",
        "provider_name": "Optometrists",
        "board_name": "Missouri State Board of Optometry",
        "url": "https://mopro.mo.gov/license/s/license-downloads",
        "captcha": "No",
        "cost": "Free",
        "ingestion_type": "Web scraping",
        "profession_type": "OD",
        "has_license_number": "Yes",
        "notes": "Salesforce LWC portal. ZIP download containing tab-delimited TXT roster. "
                 "board_label: 'Optometry'. Requires mopro_zip csv_bulk strategy. "
                 "csv_bulk archetype. Use missouri_all_txt.py standalone until engine supports mopro_zip.",
        "source_id": "MO_OPTOMETRY",
    },
    {
        "state": "Missouri",
        "provider_name": "RPh, PharmD, PharmTech",
        "board_name": "Missouri Board of Pharmacy",
        "url": "https://mopro.mo.gov/license/s/license-downloads",
        "captcha": "No",
        "cost": "Free",
        "ingestion_type": "Web scraping",
        "profession_type": "RPh, PharmD, PharmTech",
        "has_license_number": "Yes",
        "notes": "Salesforce LWC portal. ZIP download containing tab-delimited TXT roster. "
                 "board_label: 'Pharmacy'. Requires mopro_zip csv_bulk strategy. "
                 "csv_bulk archetype. Use missouri_all_txt.py standalone until engine supports mopro_zip.",
        "source_id": "MO_PHARMACY",
    },
    # ── Michigan boards (integrated from MI_All_scraper_v1.py standalone, session 31) ─
    {
        "state": "Michigan",
        "provider_name": "All License Types",
        "board_name": "Michigan Department of Licensing and Regulatory Affairs (LARA)",
        "url": "https://aca-prod.accela.com/MILARA/GeneralProperty/PropertyLookUp.aspx?isLicensee=Y&TabName=Home",
        "captcha": "No",
        "cost": "Free",
        "ingestion_type": "Web scraping",
        "profession_type": "MD, DO, RN, LPN, APRN, DDS, RPh, PT, OT, PA, DC, OD, SW, LPC, MFT, SLP, AUD, Psych (all LARA types)",
        "has_license_number": "Yes",
        "notes": "Accela Citizen Access portal (aca-prod.accela.com/MILARA). Licensee lookup via "
                 "PropertyLookUp.aspx?isLicensee=Y. Results in gdvRefLicenseeList grid. "
                 "Accela injects zero-width Unicode chars — stripped by post-processor. "
                 "classic_html_form archetype. Requires PROXY=proxy:9119.",
        "source_id": "MI_LARA",
    },
]

_SKIP_INGESTION = {
    "manual", "csv download", "api",
    "direct pdf download (verification-20260515.pdf)",
    "direct pdf download (verification-20260517.pdf)",
    "-", "na", "none",
    "through email ", "through email", "yet to explore",
    "manual\\csv download", "manual\\pdf download",
    "manual/roster download", "apify( tool)", "cloudflare",
    "csv download/api/query data", "manual (via email)",
}


def _is_captcha(v) -> bool:
    return bool(v) and str(v).strip().lower().startswith("yes")


def _is_web_scrapable(v) -> bool:
    if v is None:
        return False
    vl = str(v).strip().lower()
    if vl in _SKIP_INGESTION:
        return False
    return "web" in vl or "scraping" in vl


def load_qualifying_boards(excel_path: str | Path = EXCEL_PATH, state_filter: str | None = None) -> list[dict]:
    wb = openpyxl.load_workbook(str(excel_path), read_only=True, data_only=True)
    ws = wb["Sheet1"]
    rows = list(ws.iter_rows(values_only=True))

    boards = []
    for row in rows[1:]:
        state = str(row[0] or "").strip()
        provider = str(row[1] or "").strip()
        board_name = str(row[2] or "").strip()
        url = str(row[3] or "").strip()
        captcha = row[4]
        cost = str(row[5] or "").strip()
        ingestion = str(row[6] or "").strip()
        profession = str(row[7] or "").strip()
        has_license_num = str(row[8] or "").strip()
        notes = str(row[9] or "").strip()

        if _is_captcha(captcha):
            continue
        if not _is_web_scrapable(ingestion):
            continue
        if state_filter and state.lower() != state_filter.lower():
            continue

        auto_sid = f"{state.upper().replace(' ', '_')}_{board_name[:20].upper().replace(' ', '_').replace('/', '_')}"
        source_id = _SOURCE_ID_OVERRIDES.get(auto_sid, auto_sid)
        boards.append({
            "state": state,
            "provider_name": provider,
            "board_name": board_name,
            "url": url,
            "captcha": str(captcha or "No"),
            "cost": cost,
            "ingestion_type": ingestion,
            "profession_type": profession,
            "has_license_number": has_license_num,
            "notes": notes,
            "source_id": source_id,
        })

    # Inject boards not in the Excel (e.g. from standalone scripts)
    for extra in _EXTRA_BOARDS:
        if state_filter and extra["state"].lower() != state_filter.lower():
            continue
        boards.append(extra)

    return boards


def main():
    p = argparse.ArgumentParser(description="LVS Board Inventory — qualifying boards from Excel")
    p.add_argument("--output", default=None, help="Write YAML to file instead of stdout")
    p.add_argument("--state", default=None, help="Filter by state abbreviation (e.g. NV, MA)")
    p.add_argument("--stats", action="store_true", help="Print summary statistics only")
    p.add_argument("--excel", default=str(EXCEL_PATH), help="Path to Excel file")
    args = p.parse_args()

    boards = load_qualifying_boards(args.excel, state_filter=args.state)

    if args.stats:
        from collections import Counter
        states = Counter(b["state"] for b in boards)
        ing = Counter(b["ingestion_type"] for b in boards)
        print(f"Total qualifying boards: {len(boards)}")
        print(f"States: {len(states)}")
        print("\nTop states:")
        for s, n in states.most_common(15):
            print(f"  {s:20s}: {n}")
        print("\nIngestion types:")
        for i, n in ing.most_common():
            print(f"  {i:50s}: {n}")
        return

    output_data = {"qualifying_boards": boards, "total": len(boards)}
    yaml_str = yaml.dump(output_data, default_flow_style=False, allow_unicode=True, sort_keys=False)

    if args.output:
        Path(args.output).write_text(yaml_str, encoding="utf-8")
        print(f"Wrote {len(boards)} qualifying boards to {args.output}")
    else:
        print(yaml_str)


if __name__ == "__main__":
    main()
