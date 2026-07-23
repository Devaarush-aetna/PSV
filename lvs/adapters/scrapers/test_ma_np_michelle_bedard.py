"""Live test: MA NP Michelle Bedard — license RN2297635 on MA_HEALTH.

Run:
    python test_ma_np_michelle_bedard.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


async def test_live_search() -> bool:
    print("\n" + "=" * 65)
    print("TEST — Live browser: MA NP Michelle Bedard on MA_HEALTH")
    print("=" * 65)

    from psv_test import _load_routing, run_state

    _load_routing()

    record = {
        "first_name":    "Michelle",
        "middle_name":   "M",
        "last_name":     "Bedard",
        "lic_state":     "MA",
        "prov_type":     "NP",
        "lic_type":      "OPERATING",
        "license_id":    "RN2297635",
        "npi_no":        "",
        "epdb_pin":      "",
        "maintained_by": "",
        "input_expiry":  "",
        "svc_loc_state": "MA",
    }

    print(f"  Record  : {record['first_name']} {record['middle_name']} {record['last_name']}")
    print(f"  State   : {record['lic_state']}  |  prov_type: {record['prov_type']}")
    print(f"  License : {record['license_id']}")
    print(f"  Board   : MA_HEALTH  (checkahealthlicense.mass.gov)")
    print()

    out = Path(__file__).parent / "test_ma_np_michelle_bedard_output.xlsx"
    passes, fails, skips = await run_state(
        rows=[record],
        state="MA",
        output_path=out,
        append=False,
        batch_size=1,
        timeout=90,
        sequential=True,
    )

    total = passes + fails + skips
    print(f"\n  Result : {passes} Pass / {fails} Fail / {skips} Skip  (total {total})")
    print(f"  Output : {out}")

    ok = total == 1
    print(f"\n  [{'PASS' if ok else 'FAIL'}] Row was processed (not lost/crashed)")
    if passes:
        print("  [PASS] Status = Pass  — record found on MA_HEALTH board")
    elif skips:
        print("  [INFO] Status = Skip  — board captcha/unavailable")
    else:
        print("  [INFO] Status = Fail  — no matching record returned by board")

    return ok


if __name__ == "__main__":
    result = asyncio.run(test_live_search())

    print("\n" + "=" * 65)
    print("FINAL SUMMARY — Michelle Bedard | MA NP | RN2297635")
    print("=" * 65)
    print(f"  [{'PASS' if result else 'FAIL'}] Live search completed without crash/timeout")
    print("=" * 65)
    sys.exit(0 if result else 1)
