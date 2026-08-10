"""Verify the bug fixes described in the row-level output review.

Fix 1 — row_0561: license_numerics_match center-digits check is now directional.
Fix 2 — row_0608: trailing commas are stripped from credential suffix tokens.
Fix 3 — row_0127: RowOutcome.status returns Fail when AI resolved but gate_passed=False.
Fix 4 — rows 0081/0097/0279/0286/0340/0371: name_match_no_license only fires when the
         board returns a license that conflicts; when board exposes no license, accept
         the name match and route to AIAddLicense.
Fix A — rows 0120/0257: license anchor in disambiguator.evaluate() requires last_name>=0.4
         to prevent selecting a different person (BENNETT vs LESSLER, BAILEY vs IAMS)
         whose license shares numeric digits but has a different type prefix.
Fix B — rows 0007/0191: _name_high_conf in ladder.run_ladder() falls back to name_only
         scoring when license_numerics==0.0 so a perfect name+prov_type match bypasses
         AI escalation and routes to AIAddLicense via output_emitter.
Fix C (new) — row_0191 Hilliard: _pick_profile now accepts current_mode; returns name_only
         when current_mode is a name-mode search, preventing a license-number collision
         with a different person from corrupting the weight profile.
Fix D (new) — row_0007 Paracha: apply_narrowing prefers active/non-expired records over
         inactive/expired ones when multiple candidates tie on name scores.
Fix E (new) — row_0120 Lessler: score_candidate expands a single-letter initial (e.g.
         "R.") in licensee_first_name to the master first name when it appears verbatim
         in the board's full name string (e.g. "LESSLER, R. WILLIAM").

Run:
    cd lvs/adapters/scrapers
    python test_fixes_verification.py
"""
from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from orchestrator.disambiguator import (
    license_numerics_match,
    first_name_score,
    score_candidate,
    evaluate as _evaluate,
    apply_narrowing,
    ScoreBreakdown,
)
from orchestrator.output_emitter import OutputEmitter, RowOutcome, _matched_name_part, _clean_matched_name
from orchestrator.ladder import LadderResult, _pick_profile
from orchestrator.trace import RowTrace, AttemptRecord, make_master_row_id
from engine.models import LicenseRecord, LicenseStatus


PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
results: list[tuple[str, bool]] = []


def check(label: str, condition: bool) -> None:
    tag = PASS if condition else FAIL
    print(f"  [{tag}]  {label}")
    results.append((label, condition))


# ---------------------------------------------------------------------------
# Fix 1 — license_numerics_match directional center-digits
# ---------------------------------------------------------------------------
print("\n=== Fix 1: license_numerics_match center-digits (row_0561) ===")

# The bug: "03919" (input digits of DC-03919) was found inside "35039195"
# (board digits of 35.039195) -> spurious match.
check(
    "DC-03919 vs 35.039195 should NOT match (was false-positive before fix)",
    not license_numerics_match("DC-03919", "35.039195"),
)

# Valid KSBN case: board strips leading state prefix from input -> board is shorter.
check(
    "KSBN 5384002 (input) vs 84002 (board) should still match",
    license_numerics_match("5384002", "84002"),
)
check(
    "KSBN 5378516022 (input) vs 78516 (board) should still match",
    license_numerics_match("5378516022", "78516"),
)

# Other valid cases must not be broken.
check(
    "Identical digits still match",
    license_numerics_match("DC-03919", "DC-03919"),
)
check(
    "Leading-zero strip still works: 017371 vs 17371",
    license_numerics_match("017371", "17371"),
)
check(
    "endswith match: 031234 (board) vs 1234 (input)",
    license_numerics_match("1234", "031234"),
)
check(
    "Reverse should not match: 03919 (input) vs 35039195 (board) — shorter IN longer is blocked",
    not license_numerics_match("03919", "35039195"),
)

# ---------------------------------------------------------------------------
# Fix 2 — comma stripping in name suffix detection
# ---------------------------------------------------------------------------
print("\n=== Fix 2: credential suffix with trailing comma (row_0608) ===")

clean_fn = _clean_matched_name

check(
    '"D.C.," should be stripped as credential suffix -> empty string',
    clean_fn("D.C.,") == "",
)
check(
    '"D.C." (no comma) should also be stripped',
    clean_fn("D.C.") == "",
)
check(
    '"DC," (no dots) should be stripped',
    clean_fn("DC,") == "",
)
check(
    'Real name "David" should be preserved',
    clean_fn("David") == "David",
)
check(
    '"David D.C.," -> only credential token stripped -> "David"',
    clean_fn("David D.C.,") == "David",
)

clean_fn = _clean_matched_name

# Now test that _matched_name_part falls back to master-row first_name when
# board returned "D.C.," as licensee_first_name (the row_0608 scenario).
THARP = {
    "first_name": "David", "middle_name": "A", "last_name": "Tharp",
    "lic_state": "OH", "prov_type": "DC", "lic_type": "OPERATING",
    "license_id": "DC-04187", "npi_no": "1700170263",
    "epdb_pin": "9709833", "maintained_by": "", "input_expiry": "",
}

rec_dc_comma = LicenseRecord(
    source_id="OH_CHIRO",
    license_number="DC-04187",
    licensee_first_name="D.C.,",   # the problematic board value
    licensee_last_name="Tharp",
    status=LicenseStatus.ACTIVE,
    expiration_date=date(2028, 3, 31),
)
matched_first = _matched_name_part(rec_dc_comma, THARP, "Pass", 0)
check(
    f'_matched_name_part with first_name="D.C.," falls back to "David" -> got {matched_first!r}',
    matched_first == "David",
)
matched_last = _matched_name_part(rec_dc_comma, THARP, "Pass", 1)
check(
    f'_matched_name_part last_name="Tharp" stays "Tharp" -> got {matched_last!r}',
    matched_last == "Tharp",
)

# Also test score_candidate doesn't score "D.C.," as a real first name.
class _FakeRec:
    source_id = "OH_CHIRO"
    license_number = "DC-04187"
    licensee_first_name = "D.C.,"
    licensee_last_name = "Tharp"
    licensee_full_name = "D.C., Tharp"
    license_type = "CHIROPRACTOR LICENSE"
    profession_code = ""
    state_code = "OH"
    raw_fields: dict = {}
    expiration_date = date(2028, 3, 31)

bd = score_candidate(_FakeRec(), THARP, weight_profile="license_present")
check(
    f"score_candidate: c_first='D.C.,' detected as credential -> falls back to master 'David' -> first_name={bd.first_name:.3f} == 1.0",
    bd.first_name == 1.0,
)
check(
    f"score_candidate: gate_passed=True after c_first credential fallback (lic+last both match) -> {bd.gate_passed}",
    bd.gate_passed is True,
)

# ---------------------------------------------------------------------------
# Fix 3 — RowOutcome.status returns Fail when AI resolved but gate_passed=False
# ---------------------------------------------------------------------------
print("\n=== Fix 3: RowOutcome.status when AI resolved + gate_passed=False (row_0127) ===")

from orchestrator.ai_agent import AiAgentResult  # type: ignore

WALTZ = {
    "first_name": "Kristin", "middle_name": "Colleen", "last_name": "Waltz",
    "lic_state": "OH", "prov_type": "SW", "lic_type": "OPERATING",
    "license_id": "I.160261-SUPV", "npi_no": "1922333509",
    "epdb_pin": "8370334", "maintained_by": "", "input_expiry": "",
}

mid = make_master_row_id(127, "1922333509")
trace = RowTrace(master_row_id=mid, run_id="20260808_TEST",
                 state="OH", prov_type="SW", npi_no="1922333509")
trace.final_outcome = "Pass"

# Breakdown matching the real output: first_name=0.933, last_name=0.2, gate_passed=False
wrong_bd = ScoreBreakdown(
    license_numerics=0.0, first_name=0.933, last_name=0.2,
    provider_type=0.0, state=0.0,
    weight_profile="license_present", total=0.32, gate_passed=False,
)
rec_wrong = LicenseRecord(
    source_id="OH_CSWMFT",
    license_number="CPA.34692",
    licensee_first_name="KRISTINE",
    licensee_last_name="ACREE",
    status=LicenseStatus.ACTIVE,
    expiration_date=date(2028, 12, 31),
)

# Simulate AI "resolved" but chose the wrong candidate
ai_res = AiAgentResult(
    outcome="resolved",
    chosen_candidate=rec_wrong,
    chosen_breakdown=wrong_bd,
    reason="ai chose wrong person",
)

outcome = RowOutcome(
    master_row=WALTZ, master_row_id=mid,
    trace=trace,
    ladder_result=LadderResult(status="EscalateAi"),
    ai_result=ai_res,
)

check(
    "status == 'Fail' when AI resolved but gate_passed=False",
    outcome.status == "Fail",
)
check(
    "status != 'Pass' (must not be forced to Pass)",
    outcome.status != "Pass",
)

# Confirm that a proper AI resolution with gate_passed=True still returns Pass.
good_bd = ScoreBreakdown(
    license_numerics=1.0, first_name=1.0, last_name=1.0,
    provider_type=1.0, state=1.0,
    weight_profile="license_present", total=0.98, gate_passed=True,
)
ai_good = AiAgentResult(
    outcome="resolved",
    chosen_candidate=rec_wrong,
    chosen_breakdown=good_bd,
    reason="",
)
outcome_good = RowOutcome(
    master_row=WALTZ, master_row_id=mid,
    trace=trace,
    ladder_result=LadderResult(status="EscalateAi"),
    ai_result=ai_good,
)
check(
    "status == 'Pass' when AI resolved AND gate_passed=True",
    outcome_good.status == "Pass",
)

# ---------------------------------------------------------------------------
# Fix 4 — name_match_no_license: only escalate when board HAS a conflicting
#          license. When board exposes no license, accept the name match.
# Affects: row_0081, row_0097, row_0279, row_0286, row_0340, row_0371
# ---------------------------------------------------------------------------
print("\n=== Fix 4: name_match_no_license only on conflicting board license ===")

from orchestrator.output_emitter import OutputEmitter, _REASONS_FOR_AI_ADD_LICENSE


def _name_match_no_lic_should_escalate(detail_lic: str, input_lic: str) -> bool:
    """Mirror the new condition in ladder.py: escalate only when board has a
    non-empty license number that doesn't match the input.
    Strips detail_lic to mirror (getattr(...) or "").strip() in ladder.py."""
    detail_lic = detail_lic.strip()
    return bool(detail_lic and not license_numerics_match(input_lic, detail_lic))


check(
    "Empty board license + any input lic -> should NOT escalate (was broken before)",
    not _name_match_no_lic_should_escalate("", "16365949"),
)
check(
    "Board license present and non-matching -> SHOULD escalate (existing behavior preserved)",
    _name_match_no_lic_should_escalate("99999999", "16365949"),
)
check(
    "Board license matches input -> should NOT escalate",
    not _name_match_no_lic_should_escalate("16365949", "16365949"),
)
check(
    "Board license is whitespace-only (treated as empty) -> should NOT escalate",
    not _name_match_no_lic_should_escalate("   ", "16365949"),
)

# Test output_emitter: Pass row with no board license routes to AIAddLicense reason.
print("\n--- Fix 4 output routing ---")

FORTUNA = {
    "first_name": "Kelly", "middle_name": "", "last_name": "Fortuna",
    "lic_state": "OH", "prov_type": "NP", "lic_type": "OPERATING",
    "license_id": "16365949", "npi_no": "1053135830",
    "epdb_pin": "8294701", "maintained_by": "", "input_expiry": "",
}
mid_fortuna = make_master_row_id(81, "1053135830")
trace_fortuna = RowTrace(
    master_row_id=mid_fortuna, run_id="20260808_TEST",
    state="OH", prov_type="NP", npi_no="1053135830",
)
trace_fortuna.final_outcome = "Pass"

# Board record with NO license number (the typical OH NP board result-table behaviour)
rec_no_lic = LicenseRecord(
    source_id="OH_NURSING",
    license_number="",        # board doesn't expose license on results page
    licensee_first_name="Kelly",
    licensee_last_name="Fortuna",
    status=LicenseStatus.ACTIVE,
    expiration_date=date(2027, 4, 30),
)
bd_name_only = ScoreBreakdown(
    license_numerics=0.0,   # no license to compare
    first_name=1.0, last_name=1.0,
    provider_type=1.0, state=1.0,
    weight_profile="name_only", total=1.0, gate_passed=True,
)
lr_fortuna = LadderResult(status="Pass", best_record=rec_no_lic, best_breakdown=bd_name_only)
outcome_fortuna = RowOutcome(
    master_row=FORTUNA, master_row_id=mid_fortuna,
    trace=trace_fortuna, ladder_result=lr_fortuna,
)

import tempfile as _tempfile, pathlib as _pathlib
with _tempfile.TemporaryDirectory() as _tmp:
    _dirs = {k: _pathlib.Path(_tmp) for k in [
        "standard", "nppes", "ai_fallback", "manual",
        "add_license", "ai_add_license", "fall_out", "run_summary", "trace",
    ]}
    _emitter = OutputEmitter(run_id="20260808_TEST", dirs=_dirs)
    _manual_reason = _emitter._compute_manual_reason(outcome_fortuna)

_EXPECTED_REASON = "Name verified: board record does not expose license number"
check(
    f"Pass row with no board license -> manual_reason == {_EXPECTED_REASON!r}",
    _manual_reason == _EXPECTED_REASON,
)
check(
    "New reason is in _REASONS_FOR_AI_ADD_LICENSE (routes to AIAddLicense, not Manual)",
    _EXPECTED_REASON in _REASONS_FOR_AI_ADD_LICENSE,
)

# Sanity: when board DOES have a matching license, manual_reason should be None.
rec_lic_match = LicenseRecord(
    source_id="OH_NURSING",
    license_number="16365949",
    licensee_first_name="Kelly",
    licensee_last_name="Fortuna",
    status=LicenseStatus.ACTIVE,
    expiration_date=date(2027, 4, 30),
)
bd_lic_match = ScoreBreakdown(
    license_numerics=1.0,
    first_name=1.0, last_name=1.0,
    provider_type=1.0, state=1.0,
    weight_profile="license_present", total=0.98, gate_passed=True,
)
lr_match = LadderResult(status="Pass", best_record=rec_lic_match, best_breakdown=bd_lic_match)
outcome_match = RowOutcome(
    master_row=FORTUNA, master_row_id=mid_fortuna,
    trace=trace_fortuna, ladder_result=lr_match,
)
with _tempfile.TemporaryDirectory() as _tmp2:
    _dirs2 = {k: _pathlib.Path(_tmp2) for k in [
        "standard", "nppes", "ai_fallback", "manual",
        "add_license", "ai_add_license", "fall_out", "run_summary", "trace",
    ]}
    _emitter2 = OutputEmitter(run_id="20260808_TEST", dirs=_dirs2)
    _reason_match = _emitter2._compute_manual_reason(outcome_match)

check(
    "Pass row with matching board license -> manual_reason is None (clean AddLicense)",
    _reason_match is None,
)

# ---------------------------------------------------------------------------
# Fix 5 — rows 0286/0340/0371: name_match_no_license should NOT fire when the
#          name-only verdict is high-confidence (gate_passed=True, score >= threshold)
#          even when the board returns a different license number.
#          Fix 5b: output routing — Pass + board has different license → AIAddLicense.
#          Fix 5c: PH provider type now matches "MEDICAL BOARD" (row_0562).
# ---------------------------------------------------------------------------
print("\n=== Fix 5: name_match_no_license high-confidence bypass + PH expansion ===")

from orchestrator.disambiguator import provider_type_matches


# 5a. Condition logic: high-confidence flag mirrors ladder.py's _name_high_conf
def _name_match_lic_diff_should_escalate(
    detail_lic: str, input_lic: str,
    gate_passed: bool, total: float,
    threshold: float = 0.85,
) -> bool:
    """Mirror new condition in ladder.py (Place 1):
    escalate only when board has a non-matching license AND the name match is
    NOT high-confidence (gate_passed=True AND total >= THRESHOLD_NAME_PROFILE)."""
    detail_lic = detail_lic.strip()
    high_conf = gate_passed and total >= threshold
    return bool(detail_lic and not license_numerics_match(input_lic, detail_lic) and not high_conf)


check(
    "Board has different license + low-confidence score -> SHOULD escalate",
    _name_match_lic_diff_should_escalate("99999999", "86050463", gate_passed=True, total=0.70),
)
check(
    "Board has different license + high-confidence score (1.0) -> should NOT escalate (row_0286)",
    not _name_match_lic_diff_should_escalate("8690476", "86050463", gate_passed=True, total=1.0),
)
check(
    "Board has different license + score=0.95, gate_passed=True -> should NOT escalate (row_0371)",
    not _name_match_lic_diff_should_escalate("8899892", "16225913", gate_passed=True, total=0.95),
)
check(
    "Board has different license + gate_passed=False -> SHOULD escalate (safety: gate must hold)",
    _name_match_lic_diff_should_escalate("8690476", "86050463", gate_passed=False, total=1.0),
)

# 5b. Output routing: Pass row with non-empty board license that differs from input
JERNIGAN = {
    "first_name": "Sarah", "middle_name": "", "last_name": "Jernigan",
    "lic_state": "OH", "prov_type": "DT", "lic_type": "OPERATING",
    "license_id": "86050463", "npi_no": "1427714013",
    "epdb_pin": "8690476", "maintained_by": "", "input_expiry": "",
}
mid_jernigan = make_master_row_id(286, "1427714013")
trace_jernigan = RowTrace(
    master_row_id=mid_jernigan, run_id="20260808_TEST",
    state="OH", prov_type="DT", npi_no="1427714013",
)
trace_jernigan.final_outcome = "Pass"

rec_diff_lic = LicenseRecord(
    source_id="OH_PROVIDERS_INDIVIDUAL",
    license_number="8690476",        # board's own numbering, different from input 86050463
    licensee_first_name="Sarah",
    licensee_last_name="Jernigan",
    status=LicenseStatus.ACTIVE,
    expiration_date=date(2027, 6, 30),
)
bd_diff_lic = ScoreBreakdown(
    license_numerics=0.0,   # no numeric match
    first_name=1.0, last_name=1.0,
    provider_type=1.0, state=1.0,
    weight_profile="name_only", total=1.0, gate_passed=True,
)
lr_diff_lic = LadderResult(status="Pass", best_record=rec_diff_lic, best_breakdown=bd_diff_lic)
outcome_diff_lic = RowOutcome(
    master_row=JERNIGAN, master_row_id=mid_jernigan,
    trace=trace_jernigan, ladder_result=lr_diff_lic,
)

import tempfile as _tempfile2, pathlib as _pathlib2
with _tempfile2.TemporaryDirectory() as _tmp3:
    _dirs3 = {k: _pathlib2.Path(_tmp3) for k in [
        "standard", "nppes", "ai_fallback", "manual",
        "add_license", "ai_add_license", "fall_out", "run_summary", "trace",
    ]}
    _emitter3 = OutputEmitter(run_id="20260808_TEST", dirs=_dirs3)
    _reason_diff = _emitter3._compute_manual_reason(outcome_diff_lic)

_EXPECTED_DIFF_REASON = "Name match accepted: board uses different license numbering"
check(
    f"Pass row with different board license -> manual_reason == {_EXPECTED_DIFF_REASON!r}",
    _reason_diff == _EXPECTED_DIFF_REASON,
)
check(
    "New reason is in _REASONS_FOR_AI_ADD_LICENSE (routes to AIAddLicense, not Manual)",
    _EXPECTED_DIFF_REASON in _REASONS_FOR_AI_ADD_LICENSE,
)

# Sanity: board license that MATCHES input → manual_reason is None (clean AddLicense)
rec_same_lic = LicenseRecord(
    source_id="OH_PROVIDERS_INDIVIDUAL",
    license_number="86050463",
    licensee_first_name="Sarah",
    licensee_last_name="Jernigan",
    status=LicenseStatus.ACTIVE,
    expiration_date=date(2027, 6, 30),
)
bd_same_lic = ScoreBreakdown(
    license_numerics=1.0,
    first_name=1.0, last_name=1.0,
    provider_type=1.0, state=1.0,
    weight_profile="license_present", total=0.98, gate_passed=True,
)
lr_same = LadderResult(status="Pass", best_record=rec_same_lic, best_breakdown=bd_same_lic)
outcome_same = RowOutcome(
    master_row=JERNIGAN, master_row_id=mid_jernigan,
    trace=trace_jernigan, ladder_result=lr_same,
)
with _tempfile2.TemporaryDirectory() as _tmp4:
    _dirs4 = {k: _pathlib2.Path(_tmp4) for k in [
        "standard", "nppes", "ai_fallback", "manual",
        "add_license", "ai_add_license", "fall_out", "run_summary", "trace",
    ]}
    _emitter4 = OutputEmitter(run_id="20260808_TEST", dirs=_dirs4)
    _reason_same = _emitter4._compute_manual_reason(outcome_same)

check(
    "Pass row with matching board license -> manual_reason is None (clean AddLicense)",
    _reason_same is None,
)

# 5c. PH provider type now matches "Medical Board" style names (row_0562)
print("\n--- Fix 5c: PH expansion includes MEDICAL BOARD ---")

check(
    "provider_type_matches('PH', 'State Medical Board of Ohio', '') -> True (row_0562)",
    provider_type_matches("PH", "State Medical Board of Ohio", ""),
)
check(
    "provider_type_matches('PH', 'PHARMACIST LICENSE', '') -> True (existing pharmacy case)",
    provider_type_matches("PH", "PHARMACIST LICENSE", ""),
)
check(
    "provider_type_matches('PH', 'State Board of Nursing', '') -> False (not a PH board)",
    not provider_type_matches("PH", "State Board of Nursing", ""),
)
check(
    "provider_type_matches('MD', 'State Medical Board of Ohio', '') -> True (MD still works)",
    provider_type_matches("MD", "State Medical Board of Ohio", ""),
)

# ---------------------------------------------------------------------------
# Fix A — rows 0120/0257: license anchor requires last_name >= 0.4
#
# Before the fix, the anchor accepted a single gate-passer when
# license_numerics==1.0 AND first_name>=0.5, ignoring last_name.
# This caused BENNETT (last_name≈0.286) and BAILEY (last_name≈0.2) to be
# selected instead of LESSLER and IAMS when a suffix-different license
# (OPT.003278 vs E.003278, FD.009648 vs PT009648) produced a numeric match.
# After the fix: last_name >= 0.4 is also required.
# ---------------------------------------------------------------------------
print("\n=== Fix A: license anchor requires last_name >= 0.4 (rows 0120, 0257) ===")

from orchestrator.disambiguator import evaluate as _evaluate, ScoreBreakdown


class _FakeCandRecord:
    """Minimal stand-in for a LicenseRecord used inside evaluate()."""
    def __init__(self, lic, first, last):
        self.license_number = lic
        self.licensee_first_name = first
        self.licensee_last_name = last
        self.source_id = "OH_PROVIDERS_INDIVIDUAL"
        self.license_type = ""
        self.profession_code = ""
        self.state_code = "OH"
        self.raw_fields = {}
        self.expiration_date = None


# Simulate row_0120: NPI substitution found BENNETT (OPT.003278) — numeric digits
# match input E.0003278, first_name=1.0 (WILLIAM), last_name=0.286 (LESSLER vs BENNETT).
# With the old anchor (no last_name check): would return "selected" → WRONG person.
# With Fix A (last_name >= 0.4): should return NOT "selected" → AI finds LESSLER.
LESSLER_MASTER = {
    "first_name": "William", "middle_name": "", "last_name": "Lessler",
    "lic_state": "OH", "prov_type": "LPC", "lic_type": "OPERATING",
    "license_id": "E.0003278", "npi_no": "1700869211",
    "epdb_pin": "7255875", "maintained_by": "", "input_expiry": "",
}

bennett_rec = _FakeCandRecord("OPT.003278", "WILLIAM", "BENNETT")

# Use score_candidate to reproduce the real breakdown
bd_bennett = score_candidate(bennett_rec, LESSLER_MASTER, weight_profile="license_present")
check(
    f"BENNETT license_numerics==1.0 (OPT.003278 vs E.0003278 share digits) -> {bd_bennett.license_numerics}",
    bd_bennett.license_numerics == 1.0,
)
check(
    f"BENNETT first_name==1.0 (both WILLIAM) -> {bd_bennett.first_name}",
    bd_bennett.first_name == 1.0,
)
check(
    f"BENNETT last_name < 0.4 (BENNETT vs LESSLER) -> {bd_bennett.last_name:.3f}",
    bd_bennett.last_name < 0.4,
)

verdict_bennett = _evaluate([bennett_rec], LESSLER_MASTER, weight_profile="license_present")
check(
    f"evaluate([BENNETT]) should NOT select (last_name<0.4) -> got status={verdict_bennett.status!r}",
    verdict_bennett.status != "selected",
)

# Confirm the anchor still fires when last_name IS >= 0.4 (name-change case).
# E.g. "Duric Zinka" → board has "LEWANDOWSKI, ZINKA D": last_name≈0.0 but
# that case has a different last_name setup. Use a synthetic case: same person,
# license matches, first matches, last=0.5 (maiden vs married name).
class _FakeRecLastMatch:
    license_number = "E.0003278"
    licensee_first_name = "WILLIAM"
    licensee_last_name = "LSSLER"   # minor misspelling → last_name ~0.83
    source_id = "OH_PROVIDERS_INDIVIDUAL"
    license_type = ""
    profession_code = ""
    state_code = "OH"
    raw_fields = {}
    expiration_date = None

bd_close_last = score_candidate(_FakeRecLastMatch(), LESSLER_MASTER, weight_profile="license_present")
check(
    f"Close-last record last_name >= 0.4 (slight misspelling) -> {bd_close_last.last_name:.3f}",
    bd_close_last.last_name >= 0.4,
)
verdict_close = _evaluate([_FakeRecLastMatch()], LESSLER_MASTER, weight_profile="license_present")
check(
    f"evaluate([close-last]) should select (license+first+last all good) -> got {verdict_close.status!r}",
    verdict_close.status == "selected",
)

# ---------------------------------------------------------------------------
# Fix B — rows 0007/0191: _name_high_conf falls back to name_only scoring
#
# Before the fix: when the board returns a record with a different license number
# (license_numerics=0.0), the license_present-weighted total is only ~0.65
# (first=1.0*0.30 + last=1.0*0.20 + pt=1.0*0.10 + state=1.0*0.05 = 0.65),
# below the 0.85 threshold → _name_high_conf=False → escalates to AI →
# AI sees license mismatch and calls give_up("license_mismatch").
#
# After the fix: fallback recomputes with name_only weights
# (first=1.0*0.40 + last=1.0*0.30 + pt=1.0*0.25 + state=1.0*0.05 = 1.0 >= 0.85)
# → _name_high_conf=True → ladder accepts the match and returns Pass →
# output_emitter routes to AIAddLicense via "Name match accepted: board uses
# different license numbering".
# ---------------------------------------------------------------------------
print("\n=== Fix B: name_only fallback for _name_high_conf (rows 0007, 0191) ===")

# Helper that mirrors the fixed _name_high_conf logic in ladder.py.
def _compute_name_high_conf(bd, threshold=0.85):
    high_conf = bd.gate_passed and bd.total >= threshold
    if not high_conf and bd.gate_passed and bd.license_numerics == 0.0:
        name_only_total = (
            bd.first_name * 0.40 + bd.last_name * 0.30
            + bd.provider_type * 0.25 + bd.state * 0.05
        )
        high_conf = name_only_total >= threshold
    return high_conf


# Row 0007: Paracha / PH / 25MA11601000 — board has different license, name+pt match perfectly.
bd_paracha_license_present = ScoreBreakdown(
    license_numerics=0.0,   # board license differs
    first_name=1.0,         # UMERA == UMERA
    last_name=1.0,          # PARACHA == PARACHA
    provider_type=1.0,      # PH -> State Medical Board
    state=1.0,
    weight_profile="license_present",
    total=(0.0 * 0.35 + 1.0 * 0.30 + 1.0 * 0.20 + 1.0 * 0.10 + 1.0 * 0.05),
    gate_passed=True,
)
check(
    f"Paracha license_present total={bd_paracha_license_present.total:.2f} < 0.85 (below threshold without fallback)",
    bd_paracha_license_present.total < 0.85,
)
check(
    "Paracha _name_high_conf=True after name_only fallback (perfect first+last+pt+state)",
    _compute_name_high_conf(bd_paracha_license_present) is True,
)

# Row 0191: Hilliard / PH / 35.149691 — same pattern.
bd_hilliard_license_present = ScoreBreakdown(
    license_numerics=0.0,
    first_name=1.0,
    last_name=1.0,
    provider_type=1.0,
    state=1.0,
    weight_profile="license_present",
    total=(0.0 * 0.35 + 1.0 * 0.30 + 1.0 * 0.20 + 1.0 * 0.10 + 1.0 * 0.05),
    gate_passed=True,
)
check(
    "Hilliard _name_high_conf=True after name_only fallback",
    _compute_name_high_conf(bd_hilliard_license_present) is True,
)

# Partial name match should NOT get high_conf via fallback (last_name=0.3 < threshold).
bd_partial = ScoreBreakdown(
    license_numerics=0.0,
    first_name=1.0,
    last_name=0.3,
    provider_type=1.0,
    state=1.0,
    weight_profile="license_present",
    total=0.0 * 0.35 + 1.0 * 0.30 + 0.3 * 0.20 + 1.0 * 0.10 + 1.0 * 0.05,
    gate_passed=True,
)
_partial_name_only = 1.0 * 0.40 + 0.3 * 0.30 + 1.0 * 0.25 + 1.0 * 0.05  # = 0.79
check(
    f"Partial last_name=0.3 name_only_total={_partial_name_only:.2f} < 0.85 -> _name_high_conf stays False",
    not _compute_name_high_conf(bd_partial),
)

# gate_passed=False must not be accepted even with perfect scores.
bd_no_gate = ScoreBreakdown(
    license_numerics=0.0,
    first_name=1.0, last_name=1.0, provider_type=1.0, state=1.0,
    weight_profile="license_present",
    total=0.65, gate_passed=False,
)
check(
    "gate_passed=False -> _name_high_conf=False even with perfect name scores",
    not _compute_name_high_conf(bd_no_gate),
)

# Output routing: Pass row with different board license + high-conf name match
# should route to "Name match accepted: board uses different license numbering"
# which is in _REASONS_FOR_AI_ADD_LICENSE (AIAddLicense channel).
print("\n--- Fix B output routing (rows 0007/0191) ---")

PARACHA = {
    "first_name": "Umera", "middle_name": "", "last_name": "Paracha",
    "lic_state": "OH", "prov_type": "PH", "lic_type": "STATE MEDICAL",
    "license_id": "25MA11601000", "npi_no": "1558717850",
    "epdb_pin": "6914224", "maintained_by": "", "input_expiry": "",
}
mid_paracha = make_master_row_id(7, "1558717850")
trace_paracha = RowTrace(
    master_row_id=mid_paracha, run_id="20260808_TEST",
    state="OH", prov_type="PH", npi_no="1558717850",
)
trace_paracha.final_outcome = "Pass"

# Board record for Paracha: different license number (e.g. board uses MA.11601000),
# name matches perfectly.
rec_paracha_diff_lic = LicenseRecord(
    source_id="OH_PROVIDERS_INDIVIDUAL",
    license_number="MA.11601000",   # board's own format
    licensee_first_name="UMERA",
    licensee_last_name="PARACHA",
    status=LicenseStatus.ACTIVE,
    expiration_date=date(2027, 9, 30),
)
bd_paracha_pass = ScoreBreakdown(
    license_numerics=0.0,
    first_name=1.0, last_name=1.0, provider_type=1.0, state=1.0,
    # After name_only fallback fires, ladder re-scores with name_only profile;
    # total becomes 1.0 (not the license_present 0.65) — bypasses step 1.7.
    weight_profile="name_only",
    total=1.0, gate_passed=True,
)
lr_paracha = LadderResult(status="Pass", best_record=rec_paracha_diff_lic, best_breakdown=bd_paracha_pass)
outcome_paracha = RowOutcome(
    master_row=PARACHA, master_row_id=mid_paracha,
    trace=trace_paracha, ladder_result=lr_paracha,
)

with _tempfile.TemporaryDirectory() as _tmp5:
    _dirs5 = {k: Path(_tmp5) for k in [
        "standard", "nppes", "ai_fallback", "manual",
        "add_license", "ai_add_license", "fall_out", "run_summary", "trace",
    ]}
    _emitter5 = OutputEmitter(run_id="20260808_TEST", dirs=_dirs5)
    _reason_paracha = _emitter5._compute_manual_reason(outcome_paracha)

_DIFF_LIC_REASON = "Name match accepted: board uses different license numbering"
check(
    f"Paracha Pass row + different board license -> manual_reason == {_DIFF_LIC_REASON!r}",
    _reason_paracha == _DIFF_LIC_REASON,
)
check(
    "Paracha reason is in _REASONS_FOR_AI_ADD_LICENSE (routes to AIAddLicense)",
    _DIFF_LIC_REASON in _REASONS_FOR_AI_ADD_LICENSE,
)

# ---------------------------------------------------------------------------
# Fix F — row_0011: initial-as-first + row_0003: apostrophe name join
#
# Fix F-1 (Hamlet): OH board stores "M." (bare initial) as the first_name for
#   "MARY LYNNETTE HAMLET". Fix E (verbatim full-name lookup) doesn't fire because
#   "MARY" doesn't appear in "Hamlet , M. Lynnette". New case (b) in the initial
#   block: if the initial char matches the first letter of master's first name,
#   expand c_first → m_first so the gate and scorer see a full match.
#
# Fix F-2 (Bishop): Board stores "DeAndrea" (no apostrophe) while master has
#   "De'Andrea". _normalize_name converts "De'Andrea" → "DE ANDREA" (two tokens),
#   so the first-token comparison "DE" vs "DEANDREA" fails. New check in
#   first_name_matches / first_name_score: join all tokens ("DEANDREA") and compare.
# ---------------------------------------------------------------------------
print("\n=== Fix F-1: initial-as-first expansion (row_0011 Mary Hamlet) ===")

import tempfile as _tempfile2

HAMLET_MASTER = {
    "first_name": "Mary", "middle_name": "Lynnette", "last_name": "Hamlet",
    "lic_state": "OH", "prov_type": "LPC", "lic_type": "OPERATING",
    "license_id": "E.0800380", "npi_no": "1013268952",
    "epdb_pin": "", "maintained_by": "", "input_expiry": "",
}

# Actual board record as seen in run 20260808_2121_001:
#   licensee_name_raw = "Hamlet , M. Lynnette"
#   parsed_first = "M."  (bare initial — "MARY" does NOT appear verbatim in full_name)
#   parsed_last  = "Hamlet"
class _HamletBoardRec:
    license_number = "E.0800380"
    licensee_first_name = "M."
    licensee_last_name = "Hamlet"
    licensee_full_name = "Hamlet , M. Lynnette"
    source_id = "OH_PROVIDERS_INDIVIDUAL"
    license_type = "Licensed Professional Clinical Counselor"
    profession_code = ""
    state_code = "OH"
    raw_fields = {}
    expiration_date = None

bd_hamlet = score_candidate(_HamletBoardRec(), HAMLET_MASTER, weight_profile="license_present")
check(
    "Hamlet: license_numerics==1.0 (E.0800380 matches)",
    bd_hamlet.license_numerics == 1.0,
)
check(
    f"Hamlet: gate_passed==True after initial expansion M.->Mary -> {bd_hamlet.gate_passed}",
    bd_hamlet.gate_passed,
)
check(
    f"Hamlet: first_name==1.0 (expanded M. to Mary for scoring) -> {bd_hamlet.first_name}",
    bd_hamlet.first_name == 1.0,
)
check(
    f"Hamlet: last_name==1.0 (Hamlet==Hamlet) -> {bd_hamlet.last_name}",
    bd_hamlet.last_name == 1.0,
)

verdict_hamlet = _evaluate([_HamletBoardRec()], HAMLET_MASTER, weight_profile="license_present")
check(
    f"Hamlet: evaluate().status=='selected' -> {verdict_hamlet.status}",
    verdict_hamlet.status == "selected",
)

# Sanity: different last name + different license must NOT gate-pass via initial expansion
class _NotHamletRec:
    license_number = "E.9999999"
    licensee_first_name = "M."
    licensee_last_name = "Jones"
    licensee_full_name = "Jones , M."
    source_id = "OH_PROVIDERS_INDIVIDUAL"
    license_type = "LPC"
    profession_code = ""
    state_code = "OH"
    raw_fields = {}
    expiration_date = None

bd_not_hamlet = score_candidate(_NotHamletRec(), HAMLET_MASTER, weight_profile="license_present")
check(
    "M. Jones + different license -> gate_passed==False (last mismatch blocks initial expansion)",
    not bd_not_hamlet.gate_passed,
)

# Fix C (middle-name-as-first) still works for boards that store the middle name directly
class _HamletMiddleFirstRec:
    license_number = "E.0800380"
    licensee_first_name = "LYNNETTE"
    licensee_last_name = "HAMLET"
    licensee_full_name = ""
    source_id = "OH_PROVIDERS_INDIVIDUAL"
    license_type = "LPC"
    profession_code = ""
    state_code = "OH"
    raw_fields = {}
    expiration_date = None

bd_hamlet_mid = score_candidate(_HamletMiddleFirstRec(), HAMLET_MASTER, weight_profile="license_present")
check(
    f"Hamlet middle-first (LYNNETTE): gate_passed==True via middle-name-as-first -> {bd_hamlet_mid.gate_passed}",
    bd_hamlet_mid.gate_passed,
)

print("\n=== Fix F-2: apostrophe join in first_name_matches (row_0003 De'Andrea Bishop) ===")

from orchestrator.disambiguator import first_name_matches, first_name_score

check(
    "first_name_matches(\"De'Andrea\", \"DeAndrea\") -> True (join DE+ANDREA=DEANDREA)",
    first_name_matches("De'Andrea", "DeAndrea"),
)
check(
    "first_name_score(\"De'Andrea\", \"DeAndrea\") -> 1.0",
    first_name_score("De'Andrea", "DeAndrea") == 1.0,
)
check(
    "first_name_matches(\"O'Brien\", \"OBrien\") -> True",
    first_name_matches("O'Brien", "OBrien"),
)
check(
    "first_name_matches(\"De'Andrea\", \"Andrea\") -> False (partial doesn't match)",
    not first_name_matches("De'Andrea", "Andrea"),
)

# Full gate test: master De'Andrea Bishop + board DeAndrea Bishop
BISHOP_MASTER = {
    "first_name": "De'Andrea", "middle_name": "", "last_name": "Bishop",
    "lic_state": "OH", "prov_type": "NP", "lic_type": "OPERATING",
    "license_id": "APRN.CNP.0039346", "npi_no": "1538807854",
    "epdb_pin": "", "maintained_by": "", "input_expiry": "",
}

class _BishopBoardRec:
    license_number = "APRN.CNP.0039346"
    licensee_first_name = "DeAndrea"
    licensee_last_name = "Bishop"
    licensee_full_name = "Bishop , DeAndrea Chanel"
    source_id = "OH_PROVIDERS_INDIVIDUAL"
    license_type = "Registered Nurse (RN)"
    profession_code = ""
    state_code = "OH"
    raw_fields = {}
    expiration_date = None

bd_bishop = score_candidate(_BishopBoardRec(), BISHOP_MASTER, weight_profile="license_present")
check(
    f"Bishop: gate_passed==True (De'Andrea joins to DEANDREA==DeAndrea) -> {bd_bishop.gate_passed}",
    bd_bishop.gate_passed,
)
check(
    f"Bishop: first_name==1.0 -> {bd_bishop.first_name}",
    bd_bishop.first_name == 1.0,
)
verdict_bishop = _evaluate([_BishopBoardRec()], BISHOP_MASTER, weight_profile="license_present")
check(
    f"Bishop: evaluate().status=='selected' -> {verdict_bishop.status}",
    verdict_bishop.status == "selected",
)

# ---------------------------------------------------------------------------
# Fix C (new): _pick_profile returns name_only for name-mode searches
#   even when a prior license-mode search returned records (Hilliard fix)
# ---------------------------------------------------------------------------
print("\n=== Fix C (new): _pick_profile uses name_only for name-mode searches (row_0191 Hilliard) ===")

# Build a RowTrace that simulates: seq=1 license_number found a DIFFERENT person
# (Candice Weiner-Johnson). This makes license_attempts_returned_records()=True.
_mid_hilliard = make_master_row_id(191, "1083779018")
_trace_hilliard_sim = RowTrace(
    master_row_id=_mid_hilliard, run_id="20260808_TEST",
    state="OH", prov_type="PH", npi_no="1083779018",
)
_fake_lic_attempt = AttemptRecord(
    seq=1, source_id="OH_PROVIDERS_INDIVIDUAL",
    board_url="", mode="license_number",
    query_repr="35.149691", query_signature="OH_PROVIDERS_INDIVIDUAL:license_number:35.149691",
    record_count=1, outcome="name_mismatch",
)
_trace_hilliard_sim.append(_fake_lic_attempt)

check(
    "_trace_hilliard_sim.license_attempts_returned_records() == True",
    _trace_hilliard_sim.license_attempts_returned_records(),
)
check(
    "_pick_profile with license_mode=True, no current_mode -> license_present",
    _pick_profile(_trace_hilliard_sim) == "license_present",
)
check(
    "_pick_profile with current_mode='first_and_last' -> name_only (name mode overrides)",
    _pick_profile(_trace_hilliard_sim, current_mode="first_and_last") == "name_only",
)
check(
    "_pick_profile with current_mode='last_name' -> name_only",
    _pick_profile(_trace_hilliard_sim, current_mode="last_name") == "name_only",
)
check(
    "_pick_profile with current_mode='license_number' + prior records -> license_present",
    _pick_profile(_trace_hilliard_sim, current_mode="license_number") == "license_present",
)

HILLIARD_MASTER_C = {
    "first_name": "Michael", "last_name": "Hilliard",
    "license_id": "35.149691", "prov_type": "PH", "lic_state": "OH",
}

class _HilliardBoardRec:
    license_number = "35.140551"       # board's different license
    licensee_first_name = "MICHAEL"
    licensee_last_name = "HILLIARD"
    source_id = "OH_PROVIDERS_INDIVIDUAL"
    license_type = "Doctor of Medicine (MD)"
    profession_code = ""
    state_code = "TX"
    raw_fields = {}
    expiration_date = date(2027, 1, 31)
    licensee_full_name = "HILLIARD, MICHAEL"
    status = "Active"

bd_hilliard_lic_present = score_candidate(_HilliardBoardRec(), HILLIARD_MASTER_C, weight_profile="license_present")
bd_hilliard_name_only   = score_candidate(_HilliardBoardRec(), HILLIARD_MASTER_C, weight_profile="name_only")
check(
    f"Hilliard license_present total={bd_hilliard_lic_present.total:.2f} < 0.85 (wrong profile blocks)",
    bd_hilliard_lic_present.total < 0.85,
)
check(
    f"Hilliard name_only first={bd_hilliard_name_only.first_name:.2f} last={bd_hilliard_name_only.last_name:.2f} both >= 0.85 (name anchor fires)",
    bd_hilliard_name_only.first_name >= 0.85 and bd_hilliard_name_only.last_name >= 0.85,
)

# With the correct name_only profile, evaluate([hilliard_rec]) should select
verdict_hilliard = _evaluate([_HilliardBoardRec()], HILLIARD_MASTER_C, weight_profile="name_only")
check(
    f"evaluate([Hilliard]) with name_only -> selected (got {verdict_hilliard.status})",
    verdict_hilliard.status == "selected",
)

# ---------------------------------------------------------------------------
# Fix D (new): apply_narrowing prefers active/non-expired over inactive/expired
#   when multiple records tie on name (Paracha fix — two records same name)
# ---------------------------------------------------------------------------
print("\n=== Fix D (new): apply_narrowing active-record tiebreaker (row_0007 Paracha) ===")

PARACHA_NARROWING_MASTER = {
    "first_name": "Umera", "last_name": "Paracha",
    "license_id": "25MA11601000", "prov_type": "PH", "lic_state": "OH",
}

class _ParachaActive:
    license_number = "35.141990"
    licensee_first_name = "UMERA"
    licensee_last_name = "PARACHA"
    source_id = "OH_PROVIDERS_INDIVIDUAL"
    license_type = "Doctor of Medicine (MD)"
    profession_code = ""
    state_code = None
    raw_fields = {}
    expiration_date = date(2027, 9, 30)
    licensee_full_name = "PARACHA, UMERA"
    status = "Active"

class _ParachaInactive:
    license_number = "57.246643"
    licensee_first_name = "UMERA"
    licensee_last_name = "PARACHA"
    source_id = "OH_PROVIDERS_INDIVIDUAL"
    license_type = "Training Certificate (MD)"
    profession_code = ""
    state_code = None
    raw_fields = {}
    expiration_date = date(2021, 3, 31)     # expired
    licensee_full_name = "PARACHA, UMERA"
    status = "Inactive"

# Without Fix D: both records should score identically -> ambiguous
pool_both = [_ParachaActive(), _ParachaInactive()]
narrowed, nd_status = apply_narrowing(pool_both, PARACHA_NARROWING_MASTER)
check(
    f"apply_narrowing([active, inactive]) -> selected (not ambiguous, got {nd_status!r})",
    nd_status == "selected",
)
check(
    "apply_narrowing returns the ACTIVE Paracha record (35.141990)",
    len(narrowed) == 1 and getattr(narrowed[0], "license_number", "") == "35.141990",
)

# Sanity: two active records with same name should still go ambiguous
class _ParachaActive2:
    license_number = "35.999999"
    licensee_first_name = "UMERA"
    licensee_last_name = "PARACHA"
    source_id = "OH_PROVIDERS_INDIVIDUAL"
    license_type = "Doctor of Medicine (MD)"
    profession_code = ""
    state_code = None
    raw_fields = {}
    expiration_date = date(2027, 9, 30)
    licensee_full_name = "PARACHA, UMERA"
    status = "Active"

pool_two_active = [_ParachaActive(), _ParachaActive2()]
_, nd_status_two = apply_narrowing(pool_two_active, PARACHA_NARROWING_MASTER)
check(
    f"apply_narrowing([active, active]) -> ambiguous when both active (got {nd_status_two!r})",
    nd_status_two == "ambiguous",
)

# ---------------------------------------------------------------------------
# Fix E (new): score_candidate handles single-letter initial first names
#   LESSLER, R. WILLIAM -> c_first="R.", m_first="William" found in full name
# ---------------------------------------------------------------------------
print("\n=== Fix E (new): single-letter initial first-name matching (row_0120 Lessler) ===")

LESSLER_MASTER_E = {
    "first_name": "William", "last_name": "Lessler",
    "license_id": "E.0003278", "prov_type": "LPC", "lic_state": "OH",
}

class _LesslerBoardRec:
    license_number = "E.0003278"
    licensee_first_name = "R."
    licensee_last_name = "LESSLER"
    source_id = "OH_PROVIDERS_INDIVIDUAL"
    license_type = "Licensed Professional Counselor (LPC)"
    profession_code = ""
    state_code = "OH"
    raw_fields = {}
    expiration_date = date(2026, 9, 30)
    licensee_full_name = "LESSLER, R. WILLIAM"
    status = "Active"

bd_lessler = score_candidate(_LesslerBoardRec(), LESSLER_MASTER_E, weight_profile="license_present")
check(
    f"Lessler gate_passed==True (William found in full name via initial expansion) -> {bd_lessler.gate_passed}",
    bd_lessler.gate_passed,
)
check(
    f"Lessler first_name score >= 0.9 (William matched after initial expansion) -> {bd_lessler.first_name:.3f}",
    bd_lessler.first_name >= 0.9,
)
check(
    f"Lessler license_numerics==1.0 (E.0003278 exact match) -> {bd_lessler.license_numerics}",
    bd_lessler.license_numerics == 1.0,
)

# Verify full evaluate selects Lessler
verdict_lessler = _evaluate([_LesslerBoardRec()], LESSLER_MASTER_E, weight_profile="license_present")
check(
    f"evaluate([Lessler]) -> selected (got {verdict_lessler.status})",
    verdict_lessler.status == "selected",
)

# Sanity: single-letter initial with m_first NOT in full name should not expand
class _LesslerDifferentFirstRec:
    license_number = "E.0003278"
    licensee_first_name = "R."
    licensee_last_name = "LESSLER"
    source_id = "OH_PROVIDERS_INDIVIDUAL"
    license_type = "Licensed Professional Counselor (LPC)"
    profession_code = ""
    state_code = "OH"
    raw_fields = {}
    expiration_date = date(2026, 9, 30)
    licensee_full_name = "LESSLER, R. ROBERT"   # no WILLIAM in full name
    status = "Active"

bd_lessler_no_match = score_candidate(_LesslerDifferentFirstRec(), LESSLER_MASTER_E, weight_profile="license_present")
check(
    f"Lessler initial='R.' with full name 'R. ROBERT' -> first_name < 0.9 (William not in name) -> {bd_lessler_no_match.first_name:.3f}",
    bd_lessler_no_match.first_name < 0.9,
)

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print()
print("=" * 60)
passed = sum(1 for _, ok in results if ok)
failed = sum(1 for _, ok in results if not ok)
print(f"  {passed} passed   {failed} failed   ({len(results)} total)")
print("=" * 60)
sys.exit(0 if failed == 0 else 1)
