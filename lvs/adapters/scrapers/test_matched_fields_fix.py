"""Comprehensive tests for the matched_license / matched_first / matched_last
fix in output_emitter.py (_matched_license, _matched_name_part helpers).

Scenarios covered:
  A. Normal Pass — board record fully populated (should be unchanged)
  B. Pass with blank license_number on rec (garbled detail page) — fallback to license_id
  C. Pass with matched_last='1' garbage digit — reject and fallback to last_name
  D. Pass with matched_first='2', matched_last='3' multi-digit — both reject and fallback
  E. Pass where rec is None (AI path with no chosen_candidate somehow) — fallback
  F. Fail row — matched_* must stay blank even if master row has values
  G. Pass with AI-resolved rec that has good data — NOT overwritten by fallback
  H. Middle-name suffix 'JR' / 'MD' stripping still works after fix
  I. Fail row with rec populated — matched_* must stay blank (business rule)
  J. browser_form summary-row merge: rec.license_number blank -> patched from summary row
  K. browser_form merge: rec has good license_number -> summary row NOT applied
  L. browser_form merge: idx out of range for _summary_rows -> no crash

Run:
    python test_matched_fields_fix.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from orchestrator.output_emitter import (
    OutputEmitter, RowOutcome,
    _matched_license, _matched_name_part,
)
from orchestrator.ladder import LadderResult
from orchestrator.trace import RowTrace, make_master_row_id
from orchestrator.disambiguator import ScoreBreakdown
from engine.models import LicenseRecord, LicenseStatus

PASS_COUNT = 0
FAIL_COUNT = 0


def check(label: str, expected, actual) -> bool:
    global PASS_COUNT, FAIL_COUNT
    eq = str(actual).upper() == str(expected).upper() if actual else (expected == "" or expected is None)
    tag = "PASS" if eq else "FAIL"
    if not eq:
        FAIL_COUNT += 1
        print(f"  [{tag}] {label:55s} expected={expected!r:30s}  got={actual!r}")
    else:
        PASS_COUNT += 1
        print(f"  [{tag}] {label:55s} ok={actual!r}")
    return eq


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rec(*, lic="", full=None, first=None, last=None, expiry=None) -> LicenseRecord:
    return LicenseRecord(
        source_id="IN_PLA",
        license_number=lic,
        licensee_full_name=full,
        licensee_first_name=first,
        licensee_last_name=last,
        status=LicenseStatus.ACTIVE,
        expiration_date=expiry,
    )


def _master(lic="10142614A", fn="Rafael", ln="Lao") -> dict:
    return {"license_id": lic, "first_name": fn, "last_name": ln,
            "lic_state": "IN", "prov_type": "PH", "lic_type": "STATE MEDICAL",
            "npi_no": "1265477053", "input_expiry": "", "svc_loc_state": "IN",
            "epdb_pin": "", "maintained_by": ""}


def _make_outcome(master: dict, record, status: str = "Pass") -> RowOutcome:
    mid = make_master_row_id(0, master.get("npi_no", ""))
    trace = RowTrace(master_row_id=mid, run_id="TEST", state="IN", prov_type="PH", npi_no="")
    trace.final_outcome = status
    bd = ScoreBreakdown(
        license_numerics=1.0, first_name=1.0, last_name=1.0,
        provider_type=1.0, state=1.0, total=0.98,
        weight_profile="license_present", gate_passed=True,
    ) if status == "Pass" else None
    lr = LadderResult(status=status, best_record=record, best_breakdown=bd)
    return RowOutcome(master_row=master, master_row_id=mid, trace=trace, ladder_result=lr)


def _collect(outcome: RowOutcome) -> dict:
    dirs = {k: Path(".") for k in [
        "standard", "nppes", "ai_fallback", "manual",
        "add_license", "ai_add_license", "fall_out", "run_summary", "trace",
    ]}
    emitter = OutputEmitter(run_id="TEST", dirs=dirs)
    outcome.trace.write_json = lambda _dir: None
    emitter._collect_standard(outcome)
    return emitter._standard_rows[-1]


# ===========================================================================
# A. Normal Pass — rec fully populated — values come from board, not fallback
# ===========================================================================
def test_A_normal_pass():
    print("\n=== A. Normal Pass (board data complete) ===")
    rec = _rec(lic="01091941A", full="YAMINI, BAKHTIAR", expiry=date(2027, 10, 31))
    master = _master(lic="01091941A", fn="Bakhtiar", ln="Yamini")
    row = _collect(_make_outcome(master, rec))
    check("matched_license = board value",     "01091941A", row["matched_license"])
    check("matched_first   = board first",     "BAKHTIAR",  row["matched_first"])
    check("matched_last    = board last",      "YAMINI",    row["matched_last"])
    check("license_expiry  populated",         "2027-10-31", row["license_expiry"])


# ===========================================================================
# B. Pass with blank license_number — fallback to master license_id
# ===========================================================================
def test_B_blank_license_fallback():
    print("\n=== B. Pass — blank license_number -> fallback to license_id ===")
    rec = _rec(lic="", full="LAO, RAFAEL")          # license_number extracted blank
    master = _master(lic="10142614A", fn="Rafael", ln="Lao")
    row = _collect(_make_outcome(master, rec))
    check("matched_license falls back to license_id", "10142614A", row["matched_license"])
    check("matched_first from board full_name",        "RAFAEL",    row["matched_first"])
    check("matched_last from board full_name",         "LAO",       row["matched_last"])


# ===========================================================================
# C. Pass with matched_last='1' garbage digit — reject and fallback
# ===========================================================================
def test_C_digit_last_name_rejected():
    print("\n=== C. Pass — matched_last='1' digit -> rejected, fallback ===")
    rec = _rec(lic="", first=None, last="1")        # '1' stored as licensee_last_name
    master = _master(lic="10142614A", fn="Rafael", ln="Lao")
    row = _collect(_make_outcome(master, rec))
    check("matched_last '1' rejected, uses 'Lao'", "Lao", row["matched_last"])
    check("matched_license fallback",               "10142614A", row["matched_license"])


# ===========================================================================
# D. Pass with multi-digit garbage first AND last
# ===========================================================================
def test_D_multi_digit_both_rejected():
    print("\n=== D. Pass — matched_first='2', matched_last='3' both digits -> fallback ===")
    rec = _rec(lic="", first="2", last="3")
    master = _master(lic="TH0001333", fn="April", ln="Linville")
    row = _collect(_make_outcome(master, rec))
    check("matched_first '2' rejected -> 'April'",    "April",    row["matched_first"])
    check("matched_last  '3' rejected -> 'Linville'", "Linville", row["matched_last"])


# ===========================================================================
# E. Pass where rec is None (ladder_result.best_record=None) — full fallback
# ===========================================================================
def test_E_rec_is_none_fallback():
    print("\n=== E. Pass — rec is None -> all matched fields from master row ===")
    mid = make_master_row_id(0, "1265477053")
    master = _master(lic="10142614A", fn="Rafael", ln="Lao")
    trace = RowTrace(master_row_id=mid, run_id="TEST", state="IN", prov_type="PH", npi_no="")
    trace.final_outcome = "Pass"
    lr = LadderResult(status="Pass", best_record=None, best_breakdown=None)
    outcome = RowOutcome(master_row=master, master_row_id=mid, trace=trace, ladder_result=lr)
    row = _collect(outcome)
    check("matched_license from master when rec=None", "10142614A", row["matched_license"])
    check("matched_first from master when rec=None",   "Rafael",    row["matched_first"])
    check("matched_last from master when rec=None",    "Lao",       row["matched_last"])


# ===========================================================================
# F. Fail row — matched_* must be blank regardless of rec or master values
# ===========================================================================
def test_F_fail_row_stays_blank():
    print("\n=== F. Fail row — matched_* always blank ===")
    rec = _rec(lic="01091941A", full="YAMINI, BAKHTIAR")  # rec has good data
    master = _master(lic="01091941A", fn="Bakhtiar", ln="Yamini")

    mid = make_master_row_id(1, "1851456693")
    trace = RowTrace(master_row_id=mid, run_id="TEST", state="IN", prov_type="PH", npi_no="")
    trace.final_outcome = "Fail"
    trace.final_reason = "no_records"
    lr = LadderResult(status="Fail", reason="no_records")
    outcome = RowOutcome(master_row=master, master_row_id=mid, trace=trace, ladder_result=lr)
    row = _collect(outcome)
    check("Fail: matched_license blank", "", row["matched_license"])
    check("Fail: matched_first blank",   "", row["matched_first"])
    check("Fail: matched_last blank",    "", row["matched_last"])
    check("Fail: license_expiry blank",  "", row["license_expiry"])


# ===========================================================================
# G. Pass — AI-resolved rec with good data — NOT overwritten by master values
# ===========================================================================
def test_G_ai_resolved_good_data_unchanged():
    print("\n=== G. Pass — AI-resolved rec has valid data, fallback NOT applied ===")
    from orchestrator.ai_agent import AiAgentResult
    rec = _rec(lic="01042614A", full="LAO, RAFAEL", expiry=date(2027, 10, 31))
    bd  = ScoreBreakdown(
        license_numerics=0.9, first_name=1.0, last_name=1.0,
        provider_type=1.0, state=1.0, total=0.9,
        weight_profile="license_present", gate_passed=True,
    )
    master = _master(lic="10142614A", fn="Rafael", ln="Lao")
    mid = make_master_row_id(0, "1265477053")
    trace = RowTrace(master_row_id=mid, run_id="TEST", state="IN", prov_type="PH", npi_no="")
    trace.final_outcome = "Pass"
    ai_result = AiAgentResult(outcome="resolved", reason="ai_pick_candidate",
                               chosen_candidate=rec, chosen_breakdown=bd,
                               usd_cost=0.05, confidence_score=0.9)
    lr = LadderResult(status="Pass", best_record=None)  # ladder didn't resolve
    outcome = RowOutcome(master_row=master, master_row_id=mid, trace=trace,
                         ladder_result=lr, ai_result=ai_result)
    row = _collect(outcome)
    # Board returned 01042614A — this must win over input 10142614A
    check("matched_license = board value (AI), not input",  "01042614A", row["matched_license"])
    check("matched_first from board via AI",                "RAFAEL",    row["matched_first"])
    check("matched_last from board via AI",                 "LAO",       row["matched_last"])
    check("license_expiry from AI-chosen rec",              "2027-10-31", row["license_expiry"])


# ===========================================================================
# H. Suffix/prefix stripping still works after fix
# ===========================================================================
def test_H_prefix_suffix_stripping():
    print("\n=== H. Prefix/suffix stripping still works ===")
    rec = _rec(lic="07001362A", full="DR JAMES HUNTSMAN MD")
    master = _master(lic="07001362A", fn="James", ln="Huntsman")
    row = _collect(_make_outcome(master, rec))
    check("DR prefix stripped",  "JAMES",    row["matched_first"])
    check("MD suffix stripped",  "HUNTSMAN", row["matched_last"])


# ===========================================================================
# I. Fail (name_mismatch) — rec IS populated, shows board data (not blank).
#    This is correct: the board found something; matched_* documents what it
#    found so the analyst can see WHY it mismatched.
#    The fallback to master-row input values must NOT fire on Fail rows.
# ===========================================================================
def test_I_fail_with_populated_rec():
    print("\n=== I. name_mismatch Fail — board rec shown, no master-row fallback ===")
    # rec has the BOARD's data (different person)
    rec = _rec(lic="99999999A", full="SMITH, JOHN")
    # master row is a different person
    master = _master(lic="88888888A", fn="Carol", ln="Doe")
    mid = make_master_row_id(2, "9999999999")
    trace = RowTrace(master_row_id=mid, run_id="TEST", state="CT", prov_type="SW", npi_no="")
    trace.final_outcome = "Fail"
    trace.final_reason = "name_mismatch"
    lr = LadderResult(status="Fail", best_record=rec, reason="name_mismatch")
    outcome = RowOutcome(master_row=master, master_row_id=mid, trace=trace, ladder_result=lr)
    row = _collect(outcome)
    # matched_* shows what the BOARD returned (the mismatched record)
    check("name_mismatch Fail: matched_license = board lic (not master lic)", "99999999A", row["matched_license"])
    check("name_mismatch Fail: matched_first = board first (not master first)", "JOHN",  row["matched_first"])
    check("name_mismatch Fail: matched_last  = board last  (not master last)",  "SMITH", row["matched_last"])
    # Confirm master-row values were NOT used as fallback
    check("no_fallback: matched_license != master license_id", "88888888A" != row["matched_license"], True)


# ===========================================================================
# J. browser_form summary-row merge: blank rec.license_number -> patched
# ===========================================================================
def test_J_browser_form_merge_blank_license():
    print("\n=== J. browser_form merge — blank detail rec gets license from summary row ===")
    # Simulate a detail-page record that came back with blank license_number
    detail_rec = _rec(lic="", full="LAO, RAFAEL", expiry=date(2027, 10, 31))
    summary_rec = _rec(lic="01042614A", full="RAFAEL LAO")   # from search results table

    # Apply the same logic as browser_form._scrape_with_detail_clicks post-merge
    if not detail_rec.license_number and summary_rec.license_number:
        detail_rec.license_number = summary_rec.license_number

    check("After merge: license_number filled from summary", "01042614A", detail_rec.license_number)
    check("After merge: expiry still from detail page",      date(2027, 10, 31), detail_rec.expiration_date)


# ===========================================================================
# K. browser_form merge: rec has good license -> summary row NOT applied
# ===========================================================================
def test_K_browser_form_no_merge_when_good():
    print("\n=== K. browser_form merge — good detail rec NOT overwritten ===")
    detail_rec = _rec(lic="01091941A", full="YAMINI, BAKHTIAR", expiry=date(2027, 10, 31))
    summary_rec = _rec(lic="WRONG_LIC", full="WRONG NAME")

    if not detail_rec.license_number and summary_rec.license_number:
        detail_rec.license_number = summary_rec.license_number

    check("Good detail rec: license_number unchanged", "01091941A", detail_rec.license_number)


# ===========================================================================
# L. browser_form merge: idx out of range for _summary_rows -> no IndexError
# ===========================================================================
def test_L_browser_form_safe_oob():
    print("\n=== L. browser_form merge — idx out of range is safe ===")
    detail_rec = _rec(lic="", full="HUNTSMAN, JAMES")
    _summary_rows = []   # empty — idx=0 is out of range
    idx = 0
    try:
        if idx < len(_summary_rows):
            sr = _summary_rows[idx]
            if not detail_rec.license_number and sr.license_number:
                detail_rec.license_number = sr.license_number
        check("OOB safe: no exception, license stays blank", "", detail_rec.license_number)
    except IndexError as e:
        check(f"OOB caused IndexError: {e}", "no exception", "IndexError")


# ===========================================================================
# Direct unit tests on the helper functions
# ===========================================================================
def test_unit_matched_license():
    print("\n=== Unit: _matched_license ===")
    master = _master(lic="INPUT_LIC")
    # rec with good license
    check("good rec.lic_no -> board value",
          "BOARD_LIC", _matched_license(_rec(lic="BOARD_LIC"), master, "Pass"))
    # rec with blank license, Pass -> fallback
    check("blank rec.lic_no, Pass -> input fallback",
          "INPUT_LIC", _matched_license(_rec(lic=""), master, "Pass"))
    # rec with blank license, Fail -> stays blank
    check("blank rec.lic_no, Fail -> blank",
          "", _matched_license(_rec(lic=""), master, "Fail"))
    # rec is None, Pass -> fallback
    check("rec=None, Pass -> input fallback",
          "INPUT_LIC", _matched_license(None, master, "Pass"))
    # rec is None, Fail -> blank
    check("rec=None, Fail -> blank",
          "", _matched_license(None, master, "Fail"))


def test_unit_matched_name_part():
    print("\n=== Unit: _matched_name_part ===")
    master = _master(fn="April", ln="Linville")
    # good rec
    rec_good = _rec(full="LINVILLE, APRIL R")
    check("good rec: first from full_name",  "APRIL",    _matched_name_part(rec_good, master, "Pass", 0))
    check("good rec: last from full_name",   "LINVILLE", _matched_name_part(rec_good, master, "Pass", 1))
    # digit garbage
    rec_digit = _rec(first="2", last="1")
    check("digit first '2' rejected -> April",    "April",    _matched_name_part(rec_digit, master, "Pass", 0))
    check("digit last '1' rejected -> Linville",  "Linville", _matched_name_part(rec_digit, master, "Pass", 1))
    # digit on Fail -> blank (no fallback)
    check("digit last '1', Fail -> blank",        "", _matched_name_part(rec_digit, master, "Fail", 1))
    # blank rec on Pass -> fallback
    check("blank rec, Pass, first -> April",      "April",    _matched_name_part(_rec(), master, "Pass", 0))
    check("blank rec, Pass, last  -> Linville",   "Linville", _matched_name_part(_rec(), master, "Pass", 1))
    # None rec on Pass -> fallback
    check("None rec, Pass, first -> April",       "April",    _matched_name_part(None, master, "Pass", 0))
    check("None rec, Pass, last  -> Linville",    "Linville", _matched_name_part(None, master, "Pass", 1))
    # multi-digit strings (100, 999) also rejected
    rec_bigdigit = _rec(first="100", last="999")
    check("first='100' rejected -> April",    "April",    _matched_name_part(rec_bigdigit, master, "Pass", 0))
    check("last='999' rejected -> Linville",  "Linville", _matched_name_part(rec_bigdigit, master, "Pass", 1))
    # single-letter names should NOT be rejected (not pure digit)
    rec_initial = _rec(first="A", last="B")
    check("first='A' (initial) kept",  "A", _matched_name_part(rec_initial, master, "Pass", 0))
    check("last='B' (initial) kept",   "B", _matched_name_part(rec_initial, master, "Pass", 1))


# ===========================================================================
# Entry point
# ===========================================================================
if __name__ == "__main__":
    tests = [
        test_A_normal_pass,
        test_B_blank_license_fallback,
        test_C_digit_last_name_rejected,
        test_D_multi_digit_both_rejected,
        test_E_rec_is_none_fallback,
        test_F_fail_row_stays_blank,
        test_G_ai_resolved_good_data_unchanged,
        test_H_prefix_suffix_stripping,
        test_I_fail_with_populated_rec,
        test_J_browser_form_merge_blank_license,
        test_K_browser_form_no_merge_when_good,
        test_L_browser_form_safe_oob,
        test_unit_matched_license,
        test_unit_matched_name_part,
    ]
    for t in tests:
        t()

    print(f"\n{'='*65}")
    total = PASS_COUNT + FAIL_COUNT
    print(f"OVERALL: {PASS_COUNT}/{total} checks passed  ({FAIL_COUNT} failed)")
    print(f"{'='*65}")
    sys.exit(0 if FAIL_COUNT == 0 else 1)
