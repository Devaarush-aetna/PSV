"""Live test for NC PT board fix.

Fixes applied:
  1. proxy: enabled: false  — corporate proxy IP was Cloudflare-blocked (Ray a2555bdf1fb2ef9d)
  2. Column mapping corrected: actual table = license_number | status | full_name
     (config previously had: full_name | license_number | status)

Test record: Erin Pugh / P17968 (PT) — manually confirmed present on the board.

Run:
    python test_nc_pt_fix.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


# ── 1. Unit: verify config changes ──────────────────────────────────────────
def test_config_fix() -> bool:
    print("\n" + "=" * 65)
    print("TEST 1 — Config: proxy disabled + column mapping corrected")
    print("=" * 65)

    from engine.validate import load_config
    cfg = load_config(str(Path(__file__).parent / "sites" / "NC_PT" / "config.yaml"))

    ok_proxy = not cfg.transport.proxy.enabled
    print(f"  [{'PASS' if ok_proxy else 'FAIL'}] proxy.enabled = {cfg.transport.proxy.enabled}  (expected False)")

    cols = cfg.results.table.columns if cfg.results and cfg.results.table else {}
    ok_col0 = cols.get(0) == "license_number"
    ok_col1 = cols.get(1) == "status"
    ok_col2 = cols.get(2) == "full_name"
    print(f"  [{'PASS' if ok_col0 else 'FAIL'}] column 0 = {cols.get(0)!r}  (expected 'license_number')")
    print(f"  [{'PASS' if ok_col1 else 'FAIL'}] column 1 = {cols.get(1)!r}  (expected 'status')")
    print(f"  [{'PASS' if ok_col2 else 'FAIL'}] column 2 = {cols.get(2)!r}  (expected 'full_name')")

    all_ok = ok_proxy and ok_col0 and ok_col1 and ok_col2
    print(f"\n  Result: {'ALL PASSED' if all_ok else 'SOME FAILED'}")
    return all_ok


# ── 2. Live browser search ───────────────────────────────────────────────────
async def test_live_search() -> bool:
    print("\n" + "=" * 65)
    print("TEST 2 — Live browser: 4 NC PT records on NC_PT")
    print("=" * 65)

    from psv_test import _load_routing, run_state

    _load_routing()

    records = [
        {"first_name": "Erin",     "last_name": "Pugh",   "license_id": "P17968", "prov_type": "PT"},
        {"first_name": "Terrence", "last_name": "Kramer", "license_id": "P8003",  "prov_type": "PT"},
        {"first_name": "Chris",    "last_name": "Crusan", "license_id": "P2343",  "prov_type": "PT"},
        {"first_name": "Danielle", "last_name": "McCoy",  "license_id": "P9618",  "prov_type": "PT"},
    ]
    rows = [
        {
            "first_name": r["first_name"], "middle_name": "", "last_name": r["last_name"],
            "lic_state": "NC", "prov_type": r["prov_type"], "lic_type": "OPERATING",
            "license_id": r["license_id"], "npi_no": "", "epdb_pin": "",
            "maintained_by": "", "input_expiry": "", "svc_loc_state": "NC",
        }
        for r in records
    ]

    for r in records:
        print(f"  {r['first_name']} {r['last_name']} / {r['license_id']}")
    print()

    out = Path(__file__).parent / "test_nc_pt_output.xlsx"
    passes, fails, skips = await run_state(
        rows=rows, state="NC", output_path=out,
        append=False, batch_size=4, timeout=180, sequential=True,
    )

    total = passes + fails + skips
    print(f"\n  Result : {passes} Pass / {fails} Fail / {skips} Skip  (total {total})")
    ok = passes == 4
    print(f"\n  [{'PASS' if ok else 'FAIL'}] All 4 records Pass")
    return ok


if __name__ == "__main__":
    r1 = test_config_fix()
    r2 = asyncio.run(test_live_search())

    print("\n" + "=" * 65)
    print("FINAL SUMMARY — 4 NC PT records")
    print("=" * 65)
    print(f"  [{'PASS' if r1 else 'FAIL'}] Config: proxy disabled + columns corrected")
    print(f"  [{'PASS' if r2 else 'FAIL'}] Live search completed")
    print()
    print(f"  OVERALL: {sum([r1, r2])}/2 passed")
    print("=" * 65)
    sys.exit(0 if all([r1, r2]) else 1)
