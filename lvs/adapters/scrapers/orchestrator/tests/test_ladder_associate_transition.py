"""Regression test for the NC associate→full-licence transition case.

A provider (e.g. Amber Hicks) holds two board records that share the same digits:
  - "A17686"  LCMHC Associate  — term ended 2025-06-30 (expired / superseded)
  - "17686"   LCMHC            — Active, renews 2028

NPPES carries the ASSOCIATE number "A17686". The bug: the NPPES-targeted retry
searched "A17686", got only the expired associate row, accepted it as a Pass, and
the row failed downstream on "Provider fetch after Expiry" — never trying the
numeric-only "17686" search that surfaces the ACTIVE full licence.

The fix: don't accept an already-expired NPPES-matched record; let the numeric-only
retry run. That search returns both rows, and the disambiguator's active-over-
transitioned tiebreaker selects the Active "17686".

Run with:
    cd lvs/adapters/scrapers
    python -m pytest orchestrator/tests/test_ladder_associate_transition.py -v
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engine.models import LicenseRecord, LicenseStatus
from engine.validate import load_config
from orchestrator import ladder as ladder_mod
from orchestrator.nppes_client import NppesRecord, NpiDiscrepancy
from orchestrator.trace import RowTrace


_CONFIG_PATH = (
    Path(__file__).resolve().parents[2]
    / "sites" / "NC_MENTAL_HEALTH" / "config.yaml"
)


def _rec(num, status, exp, ltype):
    return LicenseRecord(
        source_id="NC_MENTAL_HEALTH",
        license_number=num,
        licensee_first_name="Amber",
        licensee_last_name="Hicks",
        licensee_full_name="Hicks, Amber",
        license_type=ltype,
        status=status,
        expiration_date=exp,
    )


# The two board rows for Amber Hicks.
_ASSOCIATE = _rec("A17686", LicenseStatus.ACTIVE, date(2025, 6, 30), "LCMHC Associate")
_FULL_ACTIVE = _rec("17686", LicenseStatus.ACTIVE, date(2028, 6, 30), "LCMHC")
# When the board returns the multi-result set, the associate row reads "Transitioned".
_ASSOCIATE_TRANSITIONED = _rec("A17686", LicenseStatus.INACTIVE, date(2025, 6, 30), "LCMHC Associate")


def _make_executor():
    """Fake board executor keyed on the query string, mirroring the live board:
      - "17868" (wrong input license) and name searches → no records
      - "A17686" (associate number) → only the expired associate row
      - "17686" (numeric-only)       → both rows (associate + active full)
    """
    async def executor(cfg_obj, query, run_id):
        q = (query.query or "").strip().upper()
        if q == "A17686":
            return [_ASSOCIATE]
        if q == "17686":
            return [_ASSOCIATE_TRANSITIONED, _FULL_ACTIVE]
        return []
    return executor


def test_associate_transition_selects_active_full_licence():
    cfg_obj = load_config(str(_CONFIG_PATH))

    master_row = {
        "first_name": "Amber",
        "last_name": "Hicks",
        "middle_name": "",
        "license_id": "17868",   # wrong/typo input; belongs to a different person
        "prov_type": "LPC",
        "lic_state": "NC",
    }

    nppes = NppesRecord(
        npi="1407594039",
        first_name="Amber",
        last_name="Hicks",
        license_numbers=[{"number": "A17686", "state": "NC"}],
    )
    discrepancy = NpiDiscrepancy(
        differing_fields={"license_number": ("17868", "A17686")},
        extra_nppes_licenses=[{"number": "A17686", "state": "NC"}],
    )

    trace = RowTrace(
        master_row_id="row_test", run_id="testrun", state="NC",
        prov_type="LPC", npi_no="1407594039",
    )

    result = asyncio.run(ladder_mod.run_ladder(
        routed_configs=[cfg_obj],
        master_row=master_row,
        nppes_record=nppes,
        discrepancy=discrepancy,
        trace=trace,
        executor=_make_executor(),
        timeout_s=10,
    ))

    assert result.status == "Pass"
    assert result.best_record is not None
    # The Active full licence — NOT the expired associate "A17686".
    assert result.best_record.license_number == "17686"
    assert result.best_record.status == LicenseStatus.ACTIVE
    assert result.best_record.expiration_date == date(2028, 6, 30)


def test_expired_only_still_reported_when_no_active_alternative():
    """If NO active alternative exists, the expired associate is still returned
    (as a Pass whose expired date downstream flags), not silently dropped."""
    cfg_obj = load_config(str(_CONFIG_PATH))

    master_row = {
        "first_name": "Amber", "last_name": "Hicks", "middle_name": "",
        "license_id": "17868", "prov_type": "LPC", "lic_state": "NC",
    }
    nppes = NppesRecord(
        npi="1407594039", first_name="Amber", last_name="Hicks",
        license_numbers=[{"number": "A17686", "state": "NC"}],
    )
    discrepancy = NpiDiscrepancy(
        differing_fields={"license_number": ("17868", "A17686")},
        extra_nppes_licenses=[{"number": "A17686", "state": "NC"}],
    )
    trace = RowTrace(
        master_row_id="row_test2", run_id="testrun", state="NC",
        prov_type="LPC", npi_no="1407594039",
    )

    async def executor(cfg_obj, query, run_id):
        q = (query.query or "").strip().upper()
        if q == "A17686":
            return [_ASSOCIATE]
        # numeric-only "17686" finds only the same expired associate — no active row
        if q == "17686":
            return [_ASSOCIATE_TRANSITIONED]
        return []

    result = asyncio.run(ladder_mod.run_ladder(
        routed_configs=[cfg_obj],
        master_row=master_row,
        nppes_record=nppes,
        discrepancy=discrepancy,
        trace=trace,
        executor=executor,
        timeout_s=10,
    ))

    # Expired record is preserved (its stale expiry drives the downstream Fail),
    # rather than being dropped into a generic AI escalation.
    assert result.best_record is not None
    assert result.best_record.expiration_date == date(2025, 6, 30)


# ---------------------------------------------------------------------------
# Smith case: prefixed license "S-8727" whose active record is "S8727", while the
# digit-only strip "8727" matches a superseded (Transitioned) sibling credential.
# ---------------------------------------------------------------------------

def _smith(num, status, ltype="LCMHC", middle="Denine"):
    return LicenseRecord(
        source_id="NC_MENTAL_HEALTH",
        license_number=num,
        licensee_first_name="Jennifer",
        licensee_middle_name=middle,
        licensee_last_name="Smith",
        licensee_full_name=f"Smith, Jennifer {middle}".strip(),
        license_type=ltype,
        status=status,
        expiration_date=date(2028, 9, 30) if status == LicenseStatus.ACTIVE else date(2026, 9, 30),
    )


_SMITH_ACTIVE = _smith("S8727", LicenseStatus.ACTIVE)
_SMITH_8727_TRANS = _smith("8727", LicenseStatus.INACTIVE)
_SMITH_A8727_TRANS = _smith("A8727", LicenseStatus.INACTIVE)


def _smith_master():
    return {
        "first_name": "Jennifer", "last_name": "Smith", "middle_name": "Denine",
        "license_id": "S-8727", "prov_type": "LPC", "lic_state": "NC",
    }


def _smith_trace(mid):
    return RowTrace(master_row_id=f"row_{mid}", run_id="testrun", state="NC",
                    prov_type="LPC", npi_no="1992162556")


def test_smith_separator_stripped_finds_active():
    """Fix A: the separator-stripped 'S8727' rung runs before numeric-only and finds
    the ACTIVE record directly — the numeric strip '8727' (Transitioned) is never used."""
    cfg_obj = load_config(str(_CONFIG_PATH))

    async def executor(cfg_obj, query, run_id):
        q = (query.query or "").strip().upper()
        if q == "S8727":
            return [_SMITH_ACTIVE]
        if q == "8727":
            return [_SMITH_8727_TRANS]
        return []

    result = asyncio.run(ladder_mod.run_ladder(
        routed_configs=[cfg_obj], master_row=_smith_master(),
        nppes_record=None, discrepancy=None, trace=_smith_trace("a"),
        executor=executor, timeout_s=10,
    ))
    assert result.status == "Pass"
    assert result.best_record.license_number == "S8727"
    assert result.best_record.status == LicenseStatus.ACTIVE


def test_smith_transitioned_deferred_then_name_finds_active():
    """Fix B: if the prefixed 'S8727' search returns nothing (board strict), the
    numeric strip '8727' matches the Transitioned sibling — which must NOT be accepted.
    A later name search returns the person's records and the active-over-transitioned
    tiebreaker selects the ACTIVE 'S8727'."""
    cfg_obj = load_config(str(_CONFIG_PATH))

    async def executor(cfg_obj, query, run_id):
        q = (query.query or "").strip().upper()
        if q == "8727":
            return [_SMITH_8727_TRANS]           # numeric strip → Transitioned sibling
        if q in ("JENNIFER SMITH", "SMITH"):
            return [_SMITH_ACTIVE, _SMITH_8727_TRANS, _SMITH_A8727_TRANS]
        return []                                 # "S-8727" and "S8727" find nothing

    result = asyncio.run(ladder_mod.run_ladder(
        routed_configs=[cfg_obj], master_row=_smith_master(),
        nppes_record=None, discrepancy=None, trace=_smith_trace("b"),
        executor=executor, timeout_s=10,
    ))
    assert result.status == "Pass"
    assert result.best_record.license_number == "S8727"
    assert result.best_record.status == LicenseStatus.ACTIVE


# ---------------------------------------------------------------------------
# Biggerstaff case: dual-credential provider. The queried license 2007-00937 is a
# physician (NC Medical Board) licence — that board is captcha-blocked — while the
# PRIMARY routed board (NC_DENTAL) lists her as an ACTIVE dentist (#9049) under a
# different number. Per product decision: Pass via name match on the primary board.
# ---------------------------------------------------------------------------

def _dental_active_record():
    return LicenseRecord(
        source_id="NC_DENTAL",
        license_number="9049",
        licensee_first_name="Teresa",
        licensee_middle_name="Gehret",
        licensee_last_name="Biggerstaff",
        licensee_full_name="Teresa Gehret Biggerstaff",
        license_type="Dentist (Credentialing)",
        status=LicenseStatus.ACTIVE,
        expiration_date=date(2027, 3, 31),
    )


def test_biggerstaff_passes_via_primary_board_name_match():
    dental = load_config(
        str(Path(__file__).resolve().parents[2] / "sites" / "NC_DENTAL" / "config.yaml"))
    medboard = load_config(
        str(Path(__file__).resolve().parents[2] / "sites" / "NC_MEDBOARD" / "config.yaml"))

    master_row = {
        "first_name": "Teresa", "last_name": "Biggerstaff", "middle_name": "Gehret",
        "license_id": "2007-00937", "prov_type": "OR", "lic_state": "NC",
    }
    trace = RowTrace(master_row_id="row_bigg", run_id="testrun", state="NC",
                     prov_type="OR", npi_no="1023138435")

    async def executor(cfg_obj, query, run_id):
        # NC_DENTAL finds her only by name (the queried physician licence isn't here);
        # NC_MEDBOARD (captcha-blocked) returns nothing for everything.
        if cfg_obj.identity.source_id == "NC_DENTAL":
            q = (query.query or "").strip().upper()
            if q in ("TERESA BIGGERSTAFF", "BIGGERSTAFF"):
                return [_dental_active_record()]
        return []

    result = asyncio.run(ladder_mod.run_ladder(
        routed_configs=[dental, medboard],   # NC_DENTAL is the PRIMARY (index 0)
        master_row=master_row,
        nppes_record=None, discrepancy=None, trace=trace,
        executor=executor, timeout_s=10,
    ))

    assert result.status == "Pass"
    assert result.best_record is not None
    assert result.best_record.license_number == "9049"
    assert result.best_record.status == LicenseStatus.ACTIVE
