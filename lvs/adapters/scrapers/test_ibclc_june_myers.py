"""Live test for June Myers on IBCLC_COMMISSION board.

Tests two things:
  1. Config fix verification (unit) — first_and_last mode now sends "{first} {last}".
  2. Live browser search — actually queries iblce.useclarus.com for June Myers.

Run:
    python test_ibclc_june_myers.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# ── 1. Unit: verify config fix ──────────────────────────────────────────────
def test_config_fix() -> bool:
    print("\n" + "=" * 65)
    print("TEST 1 — Config fix: first_and_last uses {first} {last}")
    print("=" * 65)

    from engine.validate import load_config
    cfg = load_config(str(Path(__file__).parent / "sites" / "IBCLC_COMMISSION" / "config.yaml"))

    fat = next((m for m in cfg.search.modes if m.mode == "first_and_last"), None)
    ok_mode   = fat is not None
    ok_extra  = fat is not None and fat.extra_inputs.get("input[name='name_search']") == "{first} {last}"

    print(f"  [{'PASS' if ok_mode  else 'FAIL'}] first_and_last mode present in config")
    _ei_val = fat.extra_inputs.get("input[name='name_search']") if fat else "N/A"
    print(f"  [{'PASS' if ok_extra else 'FAIL'}] extra_inputs['name_search'] = {_ei_val!r}"
          "  (expected '{first} {last}')")
    print(f"  timeout_ms           = {cfg.transport.timeout_ms}  (expected 45000)")
    print(f"  navigation_timeout_ms= {cfg.transport.navigation_timeout_ms}  (expected 20000)")
    print(f"  results_wait timeout = {cfg.search.results_wait.timeout_ms}  (expected 12000)")

    all_ok = ok_mode and ok_extra
    print(f"\n  Result: {'ALL PASSED' if all_ok else 'SOME FAILED'}")
    return all_ok


# ── 2. Live browser search ───────────────────────────────────────────────────
async def test_live_search() -> bool:
    print("\n" + "=" * 65)
    print("TEST 2 — Live browser: June Myers on IBCLC_COMMISSION")
    print("=" * 65)

    from psv_test import _load_routing, run_state

    _load_routing()

    record = {
        "first_name":   "June",
        "middle_name":  "",
        "last_name":    "Myers",
        "lic_state":    "IL",
        "prov_type":    "LC",
        "lic_type":     "OPERATING",
        "license_id":   "L-301745",      # IBCLC credential (L- prefix → IBCLC_COMMISSION)
        "npi_no":       "",
        "epdb_pin":     "",
        "maintained_by": "",
        "input_expiry": "",
        "svc_loc_state": "IL",
    }

    print(f"  Record  : {record['first_name']} {record['last_name']}")
    print(f"  State   : {record['lic_state']}  |  prov_type: {record['prov_type']}")
    print(f"  License : {record['license_id']}")
    print(f"  Board   : IBCLC_COMMISSION  (iblce.useclarus.com)")
    print()

    out = Path(__file__).parent / "test_ibclc_myers_output.xlsx"
    passes, fails, skips = await run_state(
        rows=[record],
        state="IL",
        output_path=out,
        append=False,
        batch_size=1,
        timeout=90,          # give the iframe flow plenty of headroom
        sequential=True,
    )

    total = passes + fails + skips
    print(f"\n  Result : {passes} Pass / {fails} Fail / {skips} Skip  (total {total})")
    print(f"  Output : {out}")

    ok = total == 1
    print(f"\n  [{'PASS' if ok else 'FAIL'}] Row was processed (not lost/crashed)")
    if passes:
        print("  [PASS] Status = Pass  — record found on board")
    elif skips:
        print("  [INFO] Status = Skip  — board captcha/unavailable")
    else:
        print("  [INFO] Status = Fail  — no matching record returned by board")

    print(f"\n  Result: {'ALL PASSED' if ok else 'FAILED'}")
    return ok


if __name__ == "__main__":
    r1 = test_config_fix()
    r2 = asyncio.run(test_live_search())

    print("\n" + "=" * 65)
    print("FINAL SUMMARY — June Myers | IBCLC_COMMISSION")
    print("=" * 65)
    print(f"  [{'PASS' if r1 else 'FAIL'}] Config fix (first_and_last sends full name)")
    print(f"  [{'PASS' if r2 else 'FAIL'}] Live search completed without crash/timeout")
    print()
    print(f"  OVERALL: {sum([r1, r2])}/2 passed")
    print("=" * 65)
    sys.exit(0 if all([r1, r2]) else 1)
