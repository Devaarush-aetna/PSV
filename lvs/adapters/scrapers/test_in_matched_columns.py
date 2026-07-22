"""Test that matched_license / matched_first / matched_last / board_name are
populated correctly for IN_PLA records (and for Fail rows from any board).

IN_PLA returns only a full_name (e.g. "HUNTSMAN, JAMES D") — no separate
licensee_first_name / licensee_last_name.  Before the fix these columns were
always blank for IN; after the fix they are split from licensee_full_name.

Run:
    python test_in_matched_columns.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))

from orchestrator.output_emitter import OutputEmitter, RowOutcome
from orchestrator.ladder import LadderResult
from orchestrator.trace import RowTrace, make_master_row_id
from orchestrator.disambiguator import ScoreBreakdown
from engine.models import LicenseRecord, LicenseStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trace(run_id: str = "TEST001", state: str = "IN") -> RowTrace:
    return RowTrace(
        master_row_id="row-0",
        run_id=run_id,
        state=state,
        prov_type="OD",
        npi_no="",
    )


def _make_in_record(full_name: str, license_number: str,
                    expiry: date = date(2027, 6, 30)) -> LicenseRecord:
    """Simulate a record returned by IN_PLA — full_name only, no split fields."""
    return LicenseRecord(
        source_id="IN_PLA",
        license_number=license_number,
        licensee_full_name=full_name,   # board returns "LAST, FIRST M"
        licensee_first_name=None,       # NOT populated by IN_PLA
        licensee_last_name=None,        # NOT populated by IN_PLA
        status=LicenseStatus.ACTIVE,
        expiration_date=expiry,
    )


def _make_pass_outcome(master_row: dict, record: LicenseRecord,
                       run_id: str = "TEST001") -> RowOutcome:
    mid = make_master_row_id(0, master_row.get("npi_no", ""))
    trace = _make_trace(run_id=run_id, state=master_row.get("lic_state", "IN"))
    trace.final_outcome = "Pass"
    trace.final_reason = ""
    bd = ScoreBreakdown(
        license_numerics=1.0, first_name=1.0, last_name=1.0,
        provider_type=1.0, state=1.0, total=0.98,
        weight_profile="license_present", gate_passed=True,
    )
    lr = LadderResult(status="Pass", best_record=record, best_breakdown=bd)
    return RowOutcome(
        master_row=master_row, master_row_id=mid,
        trace=trace, ladder_result=lr,
    )


def _make_fail_outcome(master_row: dict, reason: str = "no_records",
                       run_id: str = "TEST001") -> RowOutcome:
    mid = make_master_row_id(1, master_row.get("npi_no", ""))
    trace = _make_trace(run_id=run_id, state=master_row.get("lic_state", "IN"))
    trace.final_outcome = "Fail"
    trace.final_reason = reason
    lr = LadderResult(status="Fail", reason=reason)
    return RowOutcome(
        master_row=master_row, master_row_id=mid,
        trace=trace, ladder_result=lr,
    )


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

PASS_ROW = {
    "first_name": "James",   "middle_name": "D",   "last_name": "Huntsman",
    "lic_state":  "IN",      "prov_type":   "OD",  "lic_type": "OPERATING",
    "license_id": "07001362A",
    "epdb_pin": "EPD123", "maintained_by": "", "npi_no": "",
    "input_expiry": "", "svc_loc_state": "IN",
}

FAIL_ROWS = [
    {
        "first_name": "Jane",  "middle_name": "",  "last_name": "Doe",
        "lic_state": "IN",     "prov_type": "MD",  "lic_type": "OPERATING",
        "license_id": "99999999",
        "epdb_pin": "EPD456", "maintained_by": "", "npi_no": "",
        "input_expiry": "", "svc_loc_state": "IN",
        "_expected_reason": "no_records",
    },
    {
        "first_name": "John",  "middle_name": "",  "last_name": "Smith",
        "lic_state": "IN",     "prov_type": "RN",  "lic_type": "OPERATING",
        "license_id": "12345678",
        "epdb_pin": "EPD789", "maintained_by": "", "npi_no": "",
        "input_expiry": "", "svc_loc_state": "IN",
        "_expected_reason": "name_mismatch",
    },
]

# Different full_name formats that IN_PLA might return
FULL_NAME_FORMATS = [
    ("HUNTSMAN, JAMES D",  "James",  "Huntsman"),   # Last, First Middle
    ("HUNTSMAN, JAMES",    "James",  "Huntsman"),   # Last, First
    ("JAMES HUNTSMAN",     "James",  "Huntsman"),   # First Last (space only)
    ("DR JAMES HUNTSMAN MD", "James", "Huntsman"),  # with prefix + suffix
]


# ---------------------------------------------------------------------------
# Main test runner
# ---------------------------------------------------------------------------

def test_resolve_board_name_parts():
    """Directly test the _resolve_board_name_parts helper for all name formats."""
    print("\n" + "=" * 65)
    print("TEST 1 — _resolve_board_name_parts (unit)")
    print("=" * 65)
    all_ok = True
    for full_name, expected_first, expected_last in FULL_NAME_FORMATS:
        rec = _make_in_record(full_name, "07001362A")
        got_first, got_last = OutputEmitter._resolve_board_name_parts(rec, "Huntsman")
        ok = (got_first.upper() == expected_first.upper() and
              got_last.upper() == expected_last.upper())
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_ok = False
        print(f"  [{status}] full_name={full_name!r:35s}  "
              f"-> first={got_first!r:12s}  last={got_last!r}")
    print(f"\n  Result: {'ALL PASSED' if all_ok else 'SOME FAILED'}")
    return all_ok


def test_pass_row_standard_columns():
    """Pass row with an IN_PLA record: matched_first/last/license/board_name must all be filled."""
    print("\n" + "=" * 65)
    print("TEST 2 — Pass row: matched_* columns populated from full_name")
    print("=" * 65)

    record = _make_in_record("HUNTSMAN, JAMES D", "07001362A")
    outcome = _make_pass_outcome(PASS_ROW, record)

    emitter = OutputEmitter(run_id="TEST001", dirs={
        k: Path(".") for k in [
            "standard", "nppes", "ai_fallback", "manual",
            "add_license", "ai_add_license", "fall_out", "run_summary", "trace",
        ]
    })
    # Patch trace.write_json to no-op (we don't want file output)
    outcome.trace.write_json = lambda _dir: None

    emitter._collect_standard(outcome)
    row = emitter._standard_rows[-1]

    checks = {
        "status":          ("Pass",         row["status"]),
        "matched_license": ("07001362A",    row["matched_license"]),
        "matched_first":   ("James",        row["matched_first"]),
        "matched_last":    ("Huntsman",     row["matched_last"]),
        "board_name":      ("Indiana Professional Licensing Agency",
                                            row["board_name"]),
    }

    all_ok = True
    for col, (expected, actual) in checks.items():
        ok = actual.upper() == expected.upper() if actual else expected == ""
        status_flag = "PASS" if ok else "FAIL"
        if not ok:
            all_ok = False
        print(f"  [{status_flag}] {col:20s}  expected={expected!r:40s}  got={actual!r}")

    print(f"\n  Result: {'ALL PASSED' if all_ok else 'SOME FAILED'}")
    return all_ok


def test_fail_rows_blank_matched_columns():
    """Fail rows must have blank matched_* (no confirmed record to show)."""
    print("\n" + "=" * 65)
    print("TEST 3 — Fail rows: matched_* columns must be blank")
    print("=" * 65)

    all_ok = True
    for fr in FAIL_ROWS:
        reason = fr["_expected_reason"]
        row_data = {k: v for k, v in fr.items() if not k.startswith("_")}
        outcome = _make_fail_outcome(row_data, reason=reason)

        emitter = OutputEmitter(run_id="TEST001", dirs={
            k: Path(".") for k in [
                "standard", "nppes", "ai_fallback", "manual",
                "add_license", "ai_add_license", "fall_out", "run_summary", "trace",
            ]
        })
        outcome.trace.write_json = lambda _dir: None
        emitter._collect_standard(outcome)
        row = emitter._standard_rows[-1]

        for col in ("matched_license", "matched_first", "matched_last", "board_name"):
            val = row[col]
            ok = val == ""
            if not ok:
                all_ok = False
            flag = "PASS" if ok else "FAIL"
            print(f"  [{flag}] {row_data['last_name']:12s} | reason={reason:20s} | "
                  f"{col:20s} = {val!r} (expected blank)")

    print(f"\n  Result: {'ALL PASSED' if all_ok else 'SOME FAILED'}")
    return all_ok


def test_none_record_safe():
    """_resolve_board_name_parts must return ('','') when rec is None."""
    print("\n" + "=" * 65)
    print("TEST 4 — Safety: _resolve_board_name_parts(None) returns ('', '')")
    print("=" * 65)
    first, last = OutputEmitter._resolve_board_name_parts(None, "anything")
    ok = first == "" and last == ""
    print(f"  [{'PASS' if ok else 'FAIL'}] got ({first!r}, {last!r}), expected ('', '')")
    print(f"\n  Result: {'ALL PASSED' if ok else 'FAILED'}")
    return ok


if __name__ == "__main__":
    results = [
        test_resolve_board_name_parts(),
        test_pass_row_standard_columns(),
        test_fail_rows_blank_matched_columns(),
        test_none_record_safe(),
    ]

    print("\n" + "=" * 65)
    passed = sum(results)
    total  = len(results)
    print(f"OVERALL: {passed}/{total} test groups passed")
    print("=" * 65)
    sys.exit(0 if all(results) else 1)
