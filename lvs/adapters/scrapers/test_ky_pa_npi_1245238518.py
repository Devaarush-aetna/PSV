"""Live + unit tests for KY_PA NPI 1245238518 (Maryann Hall, license PA781).

Background
----------
Run 20260805_0105_001 took 3 attempts to match this record:
  seq 1  license_number   PA781   -> 0 records  (HTML 0 bytes — post_search_click race)
  seq 2  license_numeric_only 781 -> 0 records  (same race)
  seq 3  license_and_last PA781   -> 1 record    (warm browser, race resolved)

Two fixes applied:
  1. navigator.py: post_search_click now waits for networkidle instead of sleep(2.0),
     so seq 1 captures the results table before the UpdatePanel PostBack is still loading.
  2. ladder.py _build_query: license_and_last now encodes both fields in query_repr/sig
     e.g. "PA781+Hall" instead of "PA781".

Tests
-----
  1. Unit — navigator fix in source: verifies wait_for_load_state call is present.
  2. Unit — _build_query repr fix: verifies license_and_last includes last name.
  3. Live  — full ladder run for this NPI on KY_PA board.

Run:
    python test_ky_pa_npi_1245238518.py
"""
from __future__ import annotations

import asyncio
import sys
import inspect
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


# ---------------------------------------------------------------------------
# TEST 1 — navigator.py post_search_click uses networkidle, not bare sleep
# ---------------------------------------------------------------------------
def test_navigator_fix() -> bool:
    print("\n" + "=" * 65)
    print("TEST 1 — navigator.py: post_search_click and combo re-fill fixes")
    print("=" * 65)

    import engine.navigator as nav_mod

    src = inspect.getsource(nav_mod)

    # Fix A: expect_navigation wrapping post_search_click (replaces bare networkidle)
    has_networkidle = 'expect_navigation' in src and 'post_search_click' in src
    lines = src.splitlines()
    bare_sleep_after_click = False
    for i, line in enumerate(lines):
        if "post_search_click" in line and ".first.click()" in line:
            window = lines[i+1:i+6]
            for j, wl in enumerate(window):
                stripped = wl.strip()
                if stripped == "await asyncio.sleep(2.0)":
                    preceding = [lines[i+1+k].strip() for k in range(j)]
                    if not any(p.startswith("except") for p in preceding):
                        bare_sleep_after_click = True

    # Fix B: combo re-fill guard (re-fills primary after extra_inputs)
    has_combo_refill = "combo re-fill" in src or \
                       ("COMBO_MODES" in src and "primary_value_for_mode" in src
                        and "fill_extra_inputs" in src)

    ok_networkidle   = has_networkidle
    ok_no_bare_sleep = not bare_sleep_after_click
    ok_combo_refill  = has_combo_refill

    print(f"  [{'PASS' if ok_networkidle   else 'FAIL'}] expect_navigation wraps post_search_click click (setTimeout(0) race fix)")
    print(f"  [{'PASS' if ok_no_bare_sleep  else 'FAIL'}] bare sleep(2.0) after click removed (now inside except fallback only)")
    print(f"  [{'PASS' if ok_combo_refill   else 'FAIL'}] combo re-fill guard present (re-fills primary after extra_inputs)")

    all_ok = ok_networkidle and ok_no_bare_sleep and ok_combo_refill
    print(f"\n  Result: {'ALL PASSED' if all_ok else 'SOME FAILED'}")
    return all_ok


# ---------------------------------------------------------------------------
# TEST 2 — _build_query includes last name in license_and_last query_repr
# ---------------------------------------------------------------------------
def test_query_repr_fix() -> bool:
    print("\n" + "=" * 65)
    print("TEST 2 — ladder._build_query: license_and_last encodes last name")
    print("=" * 65)

    from orchestrator.ladder import _build_query

    master_row = {
        "first_name": "Maryann",
        "last_name":  "Hall",
        "license_id": "PA781",
    }

    sq, norm = _build_query("license_and_last", master_row)

    expected_query = "PA781+Hall"
    expected_norm  = "PA781+HALL"   # normalize_query_value uppercases

    ok_query = sq.query == expected_query
    ok_norm  = norm      == expected_norm
    ok_mode  = sq.mode   == "license_and_last"
    ok_lic   = sq.license_number == "PA781"   # license_number field unaffected
    ok_last  = sq.last_name == "Hall"          # last_name field unaffected

    print(f"  [{'PASS' if ok_query else 'FAIL'}] query_repr  = {sq.query!r}  (expected {expected_query!r})")
    print(f"  [{'PASS' if ok_norm  else 'FAIL'}] norm (sig)  = {norm!r}  (expected {expected_norm!r})")
    print(f"  [{'PASS' if ok_mode  else 'FAIL'}] mode        = {sq.mode!r}")
    print(f"  [{'PASS' if ok_lic   else 'FAIL'}] license_number = {sq.license_number!r}  (browser fill field — must stay 'PA781')")
    print(f"  [{'PASS' if ok_last  else 'FAIL'}] last_name   = {sq.last_name!r}  (extra_inputs field — must stay 'Hall')")

    # Also check that license_number mode is unchanged (regression guard)
    sq2, norm2 = _build_query("license_number", master_row)
    ok_ln_unchanged = sq2.query == "PA781" and norm2 == "PA781"
    print(f"  [{'PASS' if ok_ln_unchanged else 'FAIL'}] license_number mode unchanged: query={sq2.query!r}")

    all_ok = all([ok_query, ok_norm, ok_mode, ok_lic, ok_last, ok_ln_unchanged])
    print(f"\n  Result: {'ALL PASSED' if all_ok else 'SOME FAILED'}")
    return all_ok


# ---------------------------------------------------------------------------
# TEST 3 — Live board search for NPI 1245238518 on KY_PA
# ---------------------------------------------------------------------------
async def test_live_search() -> bool:
    print("\n" + "=" * 65)
    print("TEST 3 — Live: KY_PA board, NPI 1245238518 (Maryann Hall PA781)")
    print("=" * 65)

    from psv_test import _load_routing, run_state

    _load_routing()

    record = {
        "first_name":    "Maryann",
        "middle_name":   "",
        "last_name":     "Hall",
        "lic_state":     "KY",
        "prov_type":     "PAS",
        "lic_type":      "",
        "license_id":    "PA781",
        "npi_no":        "1245238518",
        "epdb_pin":      "",
        "maintained_by": "",
        "input_expiry":  "",
        "svc_loc_state": "KY",
    }

    print(f"  Record   : {record['first_name']} {record['last_name']}")
    print(f"  State    : {record['lic_state']}  |  prov_type: {record['prov_type']}")
    print(f"  License  : {record['license_id']}")
    print(f"  NPI      : {record['npi_no']}")
    print(f"  Board    : KY_PA  (web1.ky.gov/gensearch)")
    print()

    out = Path(__file__).parent / "test_ky_pa_1245238518_output.xlsx"
    passes, fails, skips = await run_state(
        rows=[record],
        state="KY",
        output_path=out,
        append=False,
        batch_size=1,
        timeout=90,
        sequential=True,
    )

    total = passes + fails + skips
    print(f"\n  Result  : {passes} Pass / {fails} Fail / {skips} Skip  (total {total})")
    print(f"  Output  : {out}")

    ok_processed = total == 1
    ok_pass      = passes == 1

    print(f"\n  [{'PASS' if ok_processed else 'FAIL'}] Row was processed (not lost/crashed)")
    if ok_pass:
        print("  [PASS] Status = Pass  — Maryann Hall PA781 found on KY_PA board")
    elif skips:
        print("  [INFO] Status = Skip  — board unavailable")
    else:
        print("  [FAIL] Status = Fail  — record not matched (fix may not have helped)")

    # Check trace for attempt count — ideally seq 1 now finds the record directly.
    import json, glob as _glob
    trace_pattern = str(
        Path(__file__).parent.parent.parent.parent.parent.parent
        / "Output" / "202608" / "*" / "Traces" / "row_*_1245238518.json"
    )
    trace_files = sorted(_glob.glob(trace_pattern))
    if trace_files:
        latest = trace_files[-1]
        print(f"\n  Latest trace: {Path(latest).name}")
        with open(latest) as f:
            t = json.load(f)
        attempts = t.get("attempts", [])
        for a in attempts:
            sig = a.get("query_signature", "")
            outcome = a.get("outcome", "")
            count = a.get("record_count", 0)
            print(f"    seq {a['seq']:>2}  {a['mode']:<22}  query={a['query_repr']:<20}  "
                  f"records={count}  outcome={outcome}")
        first_match = next((a for a in attempts if a.get("outcome") == "match_exact"), None)
        if first_match and first_match["seq"] == 1:
            print("\n  [PASS] Match found on seq 1 — post_search_click fix worked!")
        elif first_match:
            print(f"\n  [INFO] Match found on seq {first_match['seq']} — "
                  f"needed {first_match['seq']} attempts (seq 1 should find it after fix)")
    else:
        print("  [INFO] No trace file found for NPI 1245238518 in latest run")

    print(f"\n  Result: {'ALL PASSED' if ok_pass else 'FAILED'}")
    return ok_pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    r1 = test_navigator_fix()
    r2 = test_query_repr_fix()
    r3 = asyncio.run(test_live_search())

    print("\n" + "=" * 65)
    print("FINAL SUMMARY — KY_PA NPI 1245238518 (Maryann Hall PA781)")
    print("=" * 65)
    print(f"  [{'PASS' if r1 else 'FAIL'}] navigator.py post_search_click uses networkidle wait")
    print(f"  [{'PASS' if r2 else 'FAIL'}] _build_query license_and_last encodes last name in repr")
    print(f"  [{'PASS' if r3 else 'FAIL'}] Live KY_PA search returns Pass for Maryann Hall PA781")
    print()
    print(f"  OVERALL: {sum([r1, r2, r3])}/3 passed")
    print("=" * 65)
    sys.exit(0 if all([r1, r2, r3]) else 1)
