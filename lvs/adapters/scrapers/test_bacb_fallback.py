"""Test BACB captcha fallback for IN ABA row with RBT-25-411495.

Expected behaviour:
  1. IN_PLA is searched first (primary board for IN ABA).
  2. If IN_PLA does not return a Pass, the BACB license-format check fires.
  3. Because BACB has skip:true and the license matches the BACB format,
     the row is overridden to  status=Skip / reason=board_skip_captcha.
  4. Output columns: status=Skip, match_method=Captcha Based Board.

Run:
    python test_bacb_fallback.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# --- unit tests (no network) ---
from psv_test import _is_bacb_license, _BACB_FALLBACK_STATES

UNIT_CASES = [
    # format          license_id            expect
    ("RBT-25-411495",  True),
    ("RBT-25-411495",  True),
    ("1-25-411495",    True),
    ("0-25-411495",    True),
    ("BCBA-25-411495", True),
    ("BCaBA-2-123456", True),
    ("RBT-2-99999",    True),
    # non-BACB formats
    ("07001362A",      False),
    ("12345678",       False),
    ("NJ-RN-12345",    False),
    ("",               False),
]


def test_is_bacb_license() -> bool:
    print("\n" + "=" * 65)
    print("TEST 1 — _is_bacb_license() format detection")
    print("=" * 65)
    all_ok = True
    for lic, expected in UNIT_CASES:
        got = _is_bacb_license(lic)
        ok = got == expected
        if not ok:
            all_ok = False
        flag = "PASS" if ok else "FAIL"
        print(f"  [{flag}] {lic!r:25s}  expected={expected}  got={got}")
    print(f"\n  Result: {'ALL PASSED' if all_ok else 'SOME FAILED'}")
    return all_ok


def test_bacb_fallback_states() -> bool:
    print("\n" + "=" * 65)
    print("TEST 2 — _BACB_FALLBACK_STATES contains NJ / IL / IN")
    print("=" * 65)
    all_ok = True
    for state in ("NJ", "IL", "IN"):
        ok = state in _BACB_FALLBACK_STATES
        if not ok:
            all_ok = False
        print(f"  [{'PASS' if ok else 'FAIL'}] {state} in _BACB_FALLBACK_STATES = {ok}")
    print(f"\n  Result: {'ALL PASSED' if all_ok else 'SOME FAILED'}")
    return all_ok


def test_output_emitter_skip() -> bool:
    """Simulate the full output_emitter path for a Skip row with board_skip_captcha reason."""
    print("\n" + "=" * 65)
    print("TEST 3 — OutputEmitter: Skip row with board_skip_captcha reason")
    print("=" * 65)

    from orchestrator.output_emitter import OutputEmitter, RowOutcome
    from orchestrator.ladder import LadderResult
    from orchestrator.trace import RowTrace, make_master_row_id

    master_row = {
        "first_name": "Erin",  "middle_name": "",  "last_name": "Stewart",
        "lic_state": "IN",     "prov_type": "ABA", "lic_type": "OPERATING",
        "license_id": "RBT-25-411495",
        "npi_no": "1174328330",
        "epdb_pin": "", "maintained_by": "", "input_expiry": "", "svc_loc_state": "IN",
    }

    mid = make_master_row_id(10, master_row["npi_no"])
    trace = RowTrace(
        master_row_id=mid, run_id="TEST_BACB",
        state="IN", prov_type="ABA", npi_no=master_row["npi_no"],
    )
    trace.final_outcome = "Skip"
    trace.final_reason = "board_skip_captcha"   # set by the BACB fallback block

    lr = LadderResult(status="Fail", reason="no_records")
    outcome = RowOutcome(
        master_row=master_row, master_row_id=mid,
        trace=trace, ladder_result=lr,
    )

    emitter = OutputEmitter(run_id="TEST_BACB", dirs={
        k: Path(".") for k in [
            "standard", "nppes", "ai_fallback", "manual",
            "add_license", "ai_add_license", "fall_out", "run_summary", "trace",
        ]
    })
    outcome.trace.write_json = lambda _dir: None
    emitter._collect_standard(outcome)
    row = emitter._standard_rows[-1]

    checks = {
        "status":       ("Skip",                 row["status"]),
        "match_method": ("Captcha Based Board",  row.get("match_method", "")),
        "license_id":   ("RBT-25-411495",        row.get("license_id", "")),
        # matched_* must be blank (no confirmed board record)
        "matched_license": ("", row.get("matched_license", "")),
        "matched_first":   ("", row.get("matched_first",   "")),
        "matched_last":    ("", row.get("matched_last",    "")),
    }

    all_ok = True
    for col, (expected, actual) in checks.items():
        ok = actual == expected
        if not ok:
            all_ok = False
        flag = "PASS" if ok else "FAIL"
        print(f"  [{flag}] {col:20s}  expected={expected!r:30s}  got={actual!r}")

    # Also print the manual reason for visibility
    manual_reason = emitter._compute_manual_reason(outcome)
    print(f"\n  manual_reason = {manual_reason!r}")

    print(f"\n  Result: {'ALL PASSED' if all_ok else 'SOME FAILED'}")
    return all_ok


if __name__ == "__main__":
    results = [
        test_is_bacb_license(),
        test_bacb_fallback_states(),
        test_output_emitter_skip(),
    ]

    print("\n" + "=" * 65)
    passed = sum(results)
    total = len(results)
    print(f"OVERALL: {passed}/{total} test groups passed")
    print("=" * 65)
    sys.exit(0 if all(results) else 1)
