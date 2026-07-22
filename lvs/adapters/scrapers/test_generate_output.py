"""Generate a Standard output Excel for all tested IN records.

Produces: test_standard_output.xlsx  (same format as the live pipeline)
  Green rows = Pass
  Red rows   = Fail / Skip

Records included:
  row_0183  Heather Walker   IN  DT   86030114      -> Pass (IN_PLA found)
  row_0183  Heather Walker   IN  DT   86030114      -> Fail (no match)
  row_0010  Erin Stewart     IN  ABA  RBT-25-411495 -> Skip (BACB captcha)
  row_0013  Yves Sallade     IN  ABA  1-19-39804    -> Skip (BACB captcha)
  row_0040  Melissa Graham   IN  ABA  1-14-17390    -> Skip (BACB captcha)
  row_0084  Carly Dietz      IN  ABA  1-20-42703    -> Skip (BACB captcha)

Run:
    python test_generate_output.py
"""
from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from orchestrator.output_emitter import OutputEmitter, RowOutcome
from orchestrator.ladder import LadderResult
from orchestrator.trace import RowTrace, make_master_row_id
from orchestrator.disambiguator import ScoreBreakdown
from engine.models import LicenseRecord, LicenseStatus

OUTPUT_FILE = Path(__file__).parent / "test_standard_output.xlsx"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _emitter(tmp: Path) -> OutputEmitter:
    dirs = {k: tmp for k in [
        "standard", "nppes", "ai_fallback", "manual",
        "add_license", "ai_add_license", "fall_out", "run_summary", "trace",
    ]}
    return OutputEmitter(run_id="20260722_TEST_IN", dirs=dirs)


def _pass_outcome(row_idx: int, row: dict, full_name: str, license_num: str,
                  expiry: date, source_id: str = "IN_PLA") -> RowOutcome:
    mid = make_master_row_id(row_idx, row.get("npi_no", ""))
    trace = RowTrace(master_row_id=mid, run_id="20260722_TEST_IN",
                     state=row["lic_state"], prov_type=row["prov_type"],
                     npi_no=row.get("npi_no", ""))
    trace.final_outcome = "Pass"
    trace.final_reason = ""
    bd = ScoreBreakdown(
        license_numerics=1.0, first_name=1.0, last_name=1.0,
        provider_type=1.0, state=1.0, total=0.98,
        weight_profile="license_present", gate_passed=True,
    )
    rec = LicenseRecord(
        source_id=source_id,
        license_number=license_num,
        licensee_full_name=full_name,
        licensee_first_name=None,
        licensee_last_name=None,
        status=LicenseStatus.ACTIVE,
        expiration_date=expiry,
    )
    lr = LadderResult(status="Pass", best_record=rec, best_breakdown=bd)
    return RowOutcome(master_row=row, master_row_id=mid, trace=trace, ladder_result=lr)


def _fail_outcome(row_idx: int, row: dict, reason: str = "no_records") -> RowOutcome:
    mid = make_master_row_id(row_idx, row.get("npi_no", ""))
    trace = RowTrace(master_row_id=mid, run_id="20260722_TEST_IN",
                     state=row["lic_state"], prov_type=row["prov_type"],
                     npi_no=row.get("npi_no", ""))
    trace.final_outcome = "Fail"
    trace.final_reason = reason
    lr = LadderResult(status="Fail", reason=reason)
    return RowOutcome(master_row=row, master_row_id=mid, trace=trace, ladder_result=lr)


def _skip_outcome(row_idx: int, row: dict) -> RowOutcome:
    mid = make_master_row_id(row_idx, row.get("npi_no", ""))
    trace = RowTrace(master_row_id=mid, run_id="20260722_TEST_IN",
                     state=row["lic_state"], prov_type=row["prov_type"],
                     npi_no=row.get("npi_no", ""))
    trace.final_outcome = "Skip"
    trace.final_reason = "board_skip_captcha"
    lr = LadderResult(status="Fail", reason="no_records")
    return RowOutcome(master_row=row, master_row_id=mid, trace=trace, ladder_result=lr)


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

WALKER = {
    "first_name": "Heather", "middle_name": "", "last_name": "Walker",
    "lic_state": "IN", "prov_type": "DT", "lic_type": "OPERATING",
    "license_id": "86030114", "npi_no": "1730994773",
    "epdb_pin": "", "maintained_by": "", "input_expiry": "", "svc_loc_state": "IN",
}

STEWART = {
    "first_name": "Erin", "middle_name": "", "last_name": "Stewart",
    "lic_state": "IN", "prov_type": "ABA", "lic_type": "OPERATING",
    "license_id": "RBT-25-411495", "npi_no": "1174328330",
    "epdb_pin": "", "maintained_by": "", "input_expiry": "", "svc_loc_state": "IN",
}

SALLADE = {
    "first_name": "Yves", "middle_name": "Phillipe", "last_name": "Sallade",
    "lic_state": "IN", "prov_type": "ABA", "lic_type": "OPERATING",
    "license_id": "1-19-39804", "npi_no": "1376174029",
    "epdb_pin": "", "maintained_by": "", "input_expiry": "", "svc_loc_state": "IN",
}

GRAHAM = {
    "first_name": "Melissa", "middle_name": "A", "last_name": "Graham",
    "lic_state": "IN", "prov_type": "ABA", "lic_type": "OPERATING",
    "license_id": "1-14-17390", "npi_no": "1811385644",
    "epdb_pin": "", "maintained_by": "", "input_expiry": "", "svc_loc_state": "IN",
}

DIETZ = {
    "first_name": "Carly", "middle_name": "", "last_name": "Dietz",
    "lic_state": "IN", "prov_type": "ABA", "lic_type": "OPERATING",
    "license_id": "1-20-42703", "npi_no": "1639662398",
    "epdb_pin": "", "maintained_by": "", "input_expiry": "", "svc_loc_state": "IN",
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        emitter = _emitter(tmp)

        outcomes = [
            # (row_idx, outcome_obj, label)
            (183, _pass_outcome(183, WALKER, "WALKER, HEATHER", "86030114",
                                date(2027, 9, 30)),         "Walker  DT  Pass"),
            (183, _fail_outcome(183, WALKER, "no_records"),  "Walker  DT  Fail"),
            (10,  _skip_outcome(10,  STEWART),               "Stewart ABA Skip"),
            (13,  _skip_outcome(13,  SALLADE),               "Sallade ABA Skip"),
            (40,  _skip_outcome(40,  GRAHAM),                "Graham  ABA Skip"),
            (84,  _skip_outcome(84,  DIETZ),                 "Dietz   ABA Skip"),
        ]

        for _, outcome, _ in outcomes:
            outcome.trace.write_json = lambda _d: None
            emitter._collect_standard(outcome)

        # Now write using the real _write_standard_xlsx into our output file
        # Temporarily redirect the standard dir to the parent of OUTPUT_FILE
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        emitter.dirs["standard"] = OUTPUT_FILE.parent

        # Patch run_id so the filename lands exactly where we want
        emitter.run_id = "20260722_000000_TEST_IN"
        out_path = emitter._write_standard_xlsx()

        # Rename to our desired output name
        if out_path != OUTPUT_FILE:
            out_path.rename(OUTPUT_FILE)

    # --- Console summary ---
    print("\n" + "=" * 70)
    print("OUTPUT WRITTEN TO:", OUTPUT_FILE)
    print("=" * 70)
    print(f"  {'#':<5} {'Name':<22} {'State':<6} {'Type':<5} {'License':<18} {'Status':<6} {'Match Method'}")
    print("  " + "-" * 80)

    rows = emitter._standard_rows
    for r in rows:
        name = f"{r['first_name']} {r['last_name']}"
        print(f"  {r['master_row_id'].split('_')[1]:<5} {name:<22} "
              f"{r['lic_state']:<6} {r['prov_type']:<5} {r['license_id']:<18} "
              f"{r['status']:<6} {r.get('match_method','')}")

    print()
    print(f"  Total rows : {len(rows)}")
    print(f"  Pass       : {sum(1 for r in rows if r['status']=='Pass')}")
    print(f"  Fail       : {sum(1 for r in rows if r['status']=='Fail')}")
    print(f"  Skip       : {sum(1 for r in rows if r['status']=='Skip')}")
    print("=" * 70)


if __name__ == "__main__":
    main()
    sys.exit(0)
