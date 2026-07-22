"""Standalone test for NJ provider types MT (Massage Therapist) and GNC (Genetic Counselor).

Records are defined inline — no Excel input required.
Output is written to test_nj_mt_gnc_output.xlsx and a summary is printed to console.

Usage:
    python test_nj_mt_gnc.py
    python test_nj_mt_gnc.py --output custom_output.xlsx --timeout 60 --sequential
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from psv_test import _load_routing, run_state

# ---------------------------------------------------------------------------
# Test records from the NJ MT / GNC pilot batch
# ---------------------------------------------------------------------------
TEST_RECORDS: list[dict] = [
    {
        "first_name": "David",    "middle_name": "Elias", "last_name": "Jeraiseh",
        "lic_state": "NJ",        "prov_type": "MT",      "lic_type": "OPERATING",
        "license_id": "37FI00224600",
        "npi_no":     "1841700333",
        "epdb_pin": "", "maintained_by": "", "input_expiry": "", "svc_loc_state": "NJ",
    },
    {
        "first_name": "Veronica", "middle_name": "",      "last_name": "Holbrook",
        "lic_state": "NJ",        "prov_type": "MT",      "lic_type": "OPERATING",
        "license_id": "37FI00195900",
        "npi_no":     "1447833967",
        "epdb_pin": "", "maintained_by": "", "input_expiry": "", "svc_loc_state": "NJ",
    },
    {
        "first_name": "Robin",    "middle_name": "L",     "last_name": "Godshalk",
        "lic_state": "NJ",        "prov_type": "GNC",     "lic_type": "OPERATING",
        "license_id": "25MJ00009700",
        "npi_no":     "1659518322",
        "epdb_pin": "", "maintained_by": "", "input_expiry": "", "svc_loc_state": "NJ",
    },
    {
        "first_name": "Martha",   "middle_name": "Jean",  "last_name": "Eidmann-Hicks",
        "lic_state": "NJ",        "prov_type": "MT",      "lic_type": "OPERATING",
        "license_id": "37FI00154700",
        "npi_no":     "1366653214",
        "epdb_pin": "", "maintained_by": "", "input_expiry": "", "svc_loc_state": "NJ",
    },
    {
        "first_name": "Susan",    "middle_name": "M",     "last_name": "Beinart",
        "lic_state": "NJ",        "prov_type": "MT",      "lic_type": "OPERATING",
        "license_id": "37FI00106500",
        "npi_no":     "1316093503",
        "epdb_pin": "", "maintained_by": "", "input_expiry": "", "svc_loc_state": "NJ",
    },
    {
        "first_name": "Alexandra","middle_name": "",      "last_name": "Franc",
        "lic_state": "NJ",        "prov_type": "MT",      "lic_type": "OPERATING",
        "license_id": "37FI00254400",
        "npi_no":     "1063929776",
        "epdb_pin": "", "maintained_by": "", "input_expiry": "", "svc_loc_state": "NJ",
    },
    {
        "first_name": "Nicole",   "middle_name": "",      "last_name": "Wengrofsky",
        "lic_state": "NJ",        "prov_type": "GNC",     "lic_type": "OPERATING",
        "license_id": "25MJ00058800",
        "npi_no":     "1861092785",
        "epdb_pin": "", "maintained_by": "", "input_expiry": "", "svc_loc_state": "NJ",
    },
    {
        "first_name": "Deena",    "middle_name": "",      "last_name": "Dubrow",
        "lic_state": "NJ",        "prov_type": "GNC",     "lic_type": "OPERATING",
        "license_id": "25MJ00106300",
        "npi_no":     "1487526463",
        "epdb_pin": "", "maintained_by": "", "input_expiry": "", "svc_loc_state": "NJ",
    },
    {
        "first_name": "Louis",    "middle_name": "J.",    "last_name": "Scurti",
        "lic_state": "NJ",        "prov_type": "MT",      "lic_type": "OPERATING",
        "license_id": "37FI00150400",
        "npi_no":     "1306827357",
        "epdb_pin": "", "maintained_by": "", "input_expiry": "", "svc_loc_state": "NJ",
    },
    {
        "first_name": "Carla",    "middle_name": "",      "last_name": "Vitola",
        "lic_state": "NJ",        "prov_type": "MT",      "lic_type": "OPERATING",
        "license_id": "37FI00193200",
        "npi_no":     "1154811727",
        "epdb_pin": "", "maintained_by": "", "input_expiry": "", "svc_loc_state": "NJ",
    },
    {
        "first_name": "Paul",     "middle_name": "",      "last_name": "Maranski",
        "lic_state": "NJ",        "prov_type": "MT",      "lic_type": "OPERATING",
        "license_id": "37FI00231700",
        "npi_no":     "1598411712",
        "epdb_pin": "", "maintained_by": "", "input_expiry": "", "svc_loc_state": "NJ",
    },
    {
        "first_name": "Sara",     "middle_name": "",      "last_name": "Betancourt",
        "lic_state": "NJ",        "prov_type": "MT",      "lic_type": "OPERATING",
        "license_id": "37FA00034100",
        "npi_no":     "1770289647",
        "epdb_pin": "", "maintained_by": "", "input_expiry": "", "svc_loc_state": "NJ",
    },
    {
        "first_name": "Cecilia",  "middle_name": "",      "last_name": "Blauvelt",
        "lic_state": "NJ",        "prov_type": "MT",      "lic_type": "OPERATING",
        "license_id": "37FI00191900",
        "npi_no":     "1114305646",
        "epdb_pin": "", "maintained_by": "", "input_expiry": "", "svc_loc_state": "NJ",
    },
]


async def main(output_path: Path, timeout: int, sequential: bool) -> None:
    _load_routing()

    mt_count  = sum(1 for r in TEST_RECORDS if r["prov_type"] == "MT")
    gnc_count = sum(1 for r in TEST_RECORDS if r["prov_type"] == "GNC")
    print(f"\nNJ MT/GNC test batch: {len(TEST_RECORDS)} records  "
          f"(MT={mt_count}, GNC={gnc_count})")
    print(f"Output  : {output_path}")
    print(f"Timeout : {timeout}s per row  |  Sequential: {sequential}\n")

    passes, fails, skips = await run_state(
        rows=TEST_RECORDS,
        state="NJ",
        output_path=output_path,
        append=False,
        batch_size=10,
        timeout=timeout,
        sequential=sequential,
    )

    total = passes + fails + skips
    print("\n" + "=" * 55)
    print(f"  NJ MT/GNC results: {passes} Pass / {fails} Fail / {skips} Skip / {total} Total")
    print(f"  Results saved to : {output_path}")
    print("=" * 55 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test NJ MT and GNC records against NJ_DCA")
    parser.add_argument("--output", default="test_nj_mt_gnc_output.xlsx",
                        help="Output Excel file path (default: test_nj_mt_gnc_output.xlsx)")
    parser.add_argument("--timeout", type=int, default=45,
                        help="Per-row timeout in seconds (default: 45)")
    parser.add_argument("--sequential", action="store_true",
                        help="Process rows one at a time instead of concurrently within a batch")
    args = parser.parse_args()

    asyncio.run(main(
        output_path=Path(args.output),
        timeout=args.timeout,
        sequential=args.sequential,
    ))
