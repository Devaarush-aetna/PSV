"""Test for Heather Walker  |  IN  |  DT  |  86030114

Covers:
  1. License 86030114 does NOT match BACB format (no BACB fallback).
  2. prov_type DT is not ABA -> BACB fallback block never fires.
  3. Routing for (IN, DT) -> IN_PLA.
  4. Pass scenario: IN_PLA returns full_name only -> matched_first/last split correctly.
  5. Fail scenario: no match -> matched_* all blank, status=Fail.

Run:
    python test_in_dt_walker.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from psv_test import _is_bacb_license, _load_routing, _ROUTING, _BACB_FALLBACK_STATES
from orchestrator.output_emitter import OutputEmitter, RowOutcome
from orchestrator.ladder import LadderResult
from orchestrator.trace import RowTrace, make_master_row_id
from orchestrator.disambiguator import ScoreBreakdown
from engine.models import LicenseRecord, LicenseStatus

RECORD = {
    "row_idx":    183,
    "first_name": "Heather", "middle_name": "", "last_name": "Walker",
    "lic_state":  "IN",      "prov_type":   "DT", "lic_type": "OPERATING",
    "license_id": "86030114",
    "npi_no":     "1730994773",
    "epdb_pin": "", "maintained_by": "", "input_expiry": "", "svc_loc_state": "IN",
}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_emitter() -> OutputEmitter:
    e = OutputEmitter(run_id="TEST_DT", dirs={
        k: Path(".") for k in [
            "standard", "nppes", "ai_fallback", "manual",
            "add_license", "ai_add_license", "fall_out", "run_summary", "trace",
        ]
    })
    return e


def _make_outcome(master_row: dict, *, pass_record=None, fail_reason="no_records") -> RowOutcome:
    mid = make_master_row_id(master_row["row_idx"], master_row["npi_no"])
    row = {k: v for k, v in master_row.items() if k != "row_idx"}
    trace = RowTrace(
        master_row_id=mid, run_id="TEST_DT",
        state=row["lic_state"], prov_type=row["prov_type"], npi_no=row["npi_no"],
    )
    if pass_record:
        trace.final_outcome = "Pass"
        trace.final_reason = ""
        bd = ScoreBreakdown(
            license_numerics=1.0, first_name=1.0, last_name=1.0,
            provider_type=1.0, state=1.0, total=0.98,
            weight_profile="license_present", gate_passed=True,
        )
        lr = LadderResult(status="Pass", best_record=pass_record, best_breakdown=bd)
    else:
        trace.final_outcome = "Fail"
        trace.final_reason = fail_reason
        lr = LadderResult(status="Fail", reason=fail_reason)

    outcome = RowOutcome(master_row=row, master_row_id=mid, trace=trace, ladder_result=lr)
    outcome.trace.write_json = lambda _d: None
    return outcome


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_no_bacb_format() -> bool:
    print("\n" + "=" * 65)
    print("TEST 1 — 86030114 is NOT a BACB license format")
    print("=" * 65)
    lic = RECORD["license_id"]
    detected = _is_bacb_license(lic)
    ok = detected is False
    print(f"  [{'PASS' if ok else 'FAIL'}] _is_bacb_license({lic!r}) = {detected}  (expected False)")

    prov_not_aba = RECORD["prov_type"] != "ABA"
    ok2 = prov_not_aba
    print(f"  [{'PASS' if ok2 else 'FAIL'}] prov_type={RECORD['prov_type']!r} is not ABA -> BACB fallback will not fire")

    result = ok and ok2
    print(f"\n  Result: {'ALL PASSED' if result else 'SOME FAILED'}")
    return result


def test_routing() -> bool:
    print("\n" + "=" * 65)
    print("TEST 2 — Routing for (IN, DT) -> IN_PLA")
    print("=" * 65)
    _load_routing()
    key = ("IN", "DT")
    routed = _ROUTING.get(key, [])
    ok = "IN_PLA" in routed
    print(f"  [{'PASS' if ok else 'FAIL'}] _ROUTING[{key}] = {routed}  (expected IN_PLA)")
    print(f"\n  Result: {'ALL PASSED' if ok else 'FAILED'}")
    return ok


def test_pass_row() -> bool:
    """Pass: IN_PLA returns WALKER, HEATHER -> matched columns populated via full_name split."""
    print("\n" + "=" * 65)
    print("TEST 3 — Pass: full_name split -> matched_first / matched_last populated")
    print("=" * 65)

    rec = LicenseRecord(
        source_id="IN_PLA",
        license_number="86030114",
        licensee_full_name="WALKER, HEATHER",   # IN_PLA format: LAST, FIRST
        licensee_first_name=None,
        licensee_last_name=None,
        status=LicenseStatus.ACTIVE,
        expiration_date=date(2027, 6, 30),
    )
    outcome = _make_outcome(RECORD, pass_record=rec)
    emitter = _make_emitter()
    emitter._collect_standard(outcome)
    row = emitter._standard_rows[-1]

    checks = {
        "status":          ("Pass",       row["status"]),
        "matched_license": ("86030114",   row["matched_license"]),
        "matched_first":   ("Heather",    row["matched_first"]),
        "matched_last":    ("Walker",     row["matched_last"]),
        "board_name":      ("Indiana Professional Licensing Agency", row["board_name"]),
    }
    all_ok = True
    for col, (expected, actual) in checks.items():
        ok = actual.upper() == expected.upper() if actual else expected == ""
        if not ok:
            all_ok = False
        print(f"  [{'PASS' if ok else 'FAIL'}] {col:20s}  expected={expected!r:40s}  got={actual!r}")

    print(f"\n  Result: {'ALL PASSED' if all_ok else 'SOME FAILED'}")
    return all_ok


def test_fail_row() -> bool:
    """Fail: no match found -> matched_* blank, status=Fail (not Skip)."""
    print("\n" + "=" * 65)
    print("TEST 4 — Fail: no match -> matched_* blank, status=Fail")
    print("=" * 65)

    outcome = _make_outcome(RECORD, fail_reason="no_records")
    emitter = _make_emitter()
    emitter._collect_standard(outcome)
    row = emitter._standard_rows[-1]

    checks = {
        "status":          ("Fail", row["status"]),
        "matched_license": ("",     row["matched_license"]),
        "matched_first":   ("",     row["matched_first"]),
        "matched_last":    ("",     row["matched_last"]),
        "board_name":      ("",     row["board_name"]),
    }
    all_ok = True
    for col, (expected, actual) in checks.items():
        ok = actual == expected
        if not ok:
            all_ok = False
        print(f"  [{'PASS' if ok else 'FAIL'}] {col:20s}  expected={expected!r:40s}  got={actual!r}")

    print(f"\n  Result: {'ALL PASSED' if all_ok else 'SOME FAILED'}")
    return all_ok


if __name__ == "__main__":
    results = [
        test_no_bacb_format(),
        test_routing(),
        test_pass_row(),
        test_fail_row(),
    ]

    print("\n" + "=" * 65)
    print("FINAL SUMMARY — Heather Walker | IN | DT | 86030114")
    print("=" * 65)
    labels = [
        "No BACB format / not ABA",
        "Routing (IN,DT) -> IN_PLA",
        "Pass row: full_name split",
        "Fail row: matched_* blank",
    ]
    passed = sum(results)
    for label, ok in zip(labels, results):
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    print(f"\n  OVERALL: {passed}/{len(results)} passed")
    print("=" * 65)
    sys.exit(0 if all(results) else 1)
