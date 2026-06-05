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
            "source_id": f"{state.upper().replace(' ', '_')}_{board_name[:20].upper().replace(' ', '_').replace('/', '_')}",
        })

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
