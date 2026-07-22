"""Test BACB captcha fallback for the three IN ABA records with 1-YY-NNNNN license format.

Records under test:
  row_0013  Yves Phillipe Sallade   1-19-39804
  row_0040  Melissa A Graham        1-14-17390
  row_0084  Carly Dietz             1-20-42703

Expected for each:
  status          = Skip
  match_method    = Captcha Based Board
  matched_license = (blank)
  matched_first   = (blank)
  matched_last    = (blank)

Run:
    python test_bacb_batch.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from psv_test import _is_bacb_license
from orchestrator.output_emitter import OutputEmitter, RowOutcome
from orchestrator.ladder import LadderResult
from orchestrator.trace import RowTrace, make_master_row_id

RECORDS = [
    {
        "row_idx": 13,
        "first_name": "Yves",    "middle_name": "Phillipe", "last_name": "Sallade",
        "lic_state": "IN",       "prov_type": "ABA",        "lic_type": "OPERATING",
        "license_id": "1-19-39804",
        "npi_no": "1376174029",
        "epdb_pin": "", "maintained_by": "", "input_expiry": "", "svc_loc_state": "IN",
    },
    {
        "row_idx": 40,
        "first_name": "Melissa", "middle_name": "A",        "last_name": "Graham",
        "lic_state": "IN",       "prov_type": "ABA",        "lic_type": "OPERATING",
        "license_id": "1-14-17390",
        "npi_no": "1811385644",
        "epdb_pin": "", "maintained_by": "", "input_expiry": "", "svc_loc_state": "IN",
    },
    {
        "row_idx": 84,
        "first_name": "Carly",   "middle_name": "",         "last_name": "Dietz",
        "lic_state": "IN",       "prov_type": "ABA",        "lic_type": "OPERATING",
        "license_id": "1-20-42703",
        "npi_no": "1639662398",
        "epdb_pin": "", "maintained_by": "", "input_expiry": "", "svc_loc_state": "IN",
    },
]


def _simulate_skip_outcome(rec: dict) -> dict:
    """Simulate the state after the BACB fallback block fires:
    trace.final_outcome='Skip', trace.final_reason='board_skip_captcha'.
    Returns the standard-output row dict produced by OutputEmitter."""
    mid = make_master_row_id(rec["row_idx"], rec["npi_no"])
    trace = RowTrace(
        master_row_id=mid, run_id="TEST_BACB_BATCH",
        state=rec["lic_state"], prov_type=rec["prov_type"], npi_no=rec["npi_no"],
    )
    trace.final_outcome = "Skip"
    trace.final_reason = "board_skip_captcha"

    lr = LadderResult(status="Fail", reason="no_records")
    outcome = RowOutcome(
        master_row={k: v for k, v in rec.items() if k != "row_idx"},
        master_row_id=mid,
        trace=trace,
        ladder_result=lr,
    )

    emitter = OutputEmitter(run_id="TEST_BACB_BATCH", dirs={
        k: Path(".") for k in [
            "standard", "nppes", "ai_fallback", "manual",
            "add_license", "ai_add_license", "fall_out", "run_summary", "trace",
        ]
    })
    outcome.trace.write_json = lambda _dir: None
    emitter._collect_standard(outcome)
    return emitter._standard_rows[-1]


def run_tests() -> bool:
    print("\n" + "=" * 70)
    print("TEST 1 — BACB license format detection (1-YY-NNNNN = BCBA type)")
    print("=" * 70)
    fmt_ok = True
    for rec in RECORDS:
        lic = rec["license_id"]
        detected = _is_bacb_license(lic)
        flag = "PASS" if detected else "FAIL"
        if not detected:
            fmt_ok = False
        print(f"  [{flag}] {lic!r:18s}  -> BACB format detected = {detected}")
    print(f"\n  Result: {'ALL PASSED' if fmt_ok else 'SOME FAILED'}")

    print("\n" + "=" * 70)
    print("TEST 2 — OutputEmitter columns for each record after BACB fallback")
    print("=" * 70)

    EXPECTED_BLANK = ("matched_license", "matched_first", "matched_last")
    rows_ok = True

    for rec in RECORDS:
        name = f"{rec['first_name']} {rec['last_name']}"
        lic  = rec["license_id"]
        print(f"\n  -- {name} | {lic} --")

        row = _simulate_skip_outcome(rec)

        checks = {
            "status":          ("Skip",                row["status"]),
            "match_method":    ("Captcha Based Board", row.get("match_method", "")),
            "license_id":      (lic,                   row.get("license_id", "")),
            "matched_license": ("",                    row.get("matched_license", "")),
            "matched_first":   ("",                    row.get("matched_first",   "")),
            "matched_last":    ("",                    row.get("matched_last",    "")),
        }

        rec_ok = True
        for col, (expected, actual) in checks.items():
            ok = actual == expected
            if not ok:
                rec_ok = False
                rows_ok = False
            flag = "PASS" if ok else "FAIL"
            print(f"    [{flag}] {col:20s}  expected={expected!r:30s}  got={actual!r}")

        print(f"    => {'PASS' if rec_ok else 'FAIL'}")

    print(f"\n  Result: {'ALL PASSED' if rows_ok else 'SOME FAILED'}")

    print("\n" + "=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)
    all_ok = fmt_ok and rows_ok
    print(f"  Format detection : {'PASS' if fmt_ok  else 'FAIL'}")
    print(f"  Column values    : {'PASS' if rows_ok else 'FAIL'}")
    print()
    print(f"  {'ALL TESTS PASSED' if all_ok else 'SOME TESTS FAILED'}")
    print()

    # Print the expected output table
    if all_ok:
        print("  Expected output rows:")
        print(f"  {'Name':<25} {'License':<15} {'Status':<6} {'Match Method':<22} {'matched_*'}")
        print("  " + "-" * 80)
        for rec in RECORDS:
            name = f"{rec['first_name']} {rec['last_name']}"
            print(f"  {name:<25} {rec['license_id']:<15} {'Skip':<6} {'Captcha Based Board':<22} (all blank)")

    print("=" * 70)
    return all_ok


if __name__ == "__main__":
    ok = run_tests()
    sys.exit(0 if ok else 1)
