"""Unit tests for orchestrator.disambiguator.

Run with:
    cd lvs/adapters/scrapers
    python -m pytest orchestrator/tests/test_disambiguator.py -v
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Allow running pytest from the scrapers/ dir
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from orchestrator import disambiguator as d


@dataclass
class FakeRec:
    license_number: str = ""
    licensee_first_name: str = ""
    licensee_last_name: str = ""
    licensee_middle_name: Optional[str] = None
    licensee_full_name: str = ""
    license_type: str = ""
    profession_code: str = ""
    state_code: str = ""


def _master(**kw):
    base = {"first_name": "JOHN", "last_name": "SMITH", "license_id": "17371",
            "prov_type": "MD", "lic_state": "NV"}
    base.update(kw)
    return base


# --- first_name_matches ---------------------------------------------------

def test_first_name_exact():
    assert d.first_name_matches("John", "JOHN")
    assert d.first_name_matches("john", "JOHN")


def test_first_name_nickname():
    assert d.first_name_matches("Robert", "Bob")
    assert d.first_name_matches("Kathryn", "Kathy")
    assert d.first_name_matches("William", "Bill")


def test_first_name_misspell():
    # Token sort ratio handles small typos
    assert d.first_name_matches("Deirdre", "Dierdre")


def test_first_name_no_match():
    assert not d.first_name_matches("John", "Mary")


# --- last_name -----------------------------------------------------------

def test_last_name_exact():
    assert d.last_name_matches("Smith", "SMITH")


def test_last_name_hyphenated_fallback():
    # Master "Bates-Daly" should match candidate "BATES" alone
    assert d.last_name_score("Bates-Daly", "BATES") >= 0.9


# --- license_numerics ---------------------------------------------------

def test_license_exact():
    assert d.license_numerics_match("17371", "17371")


def test_license_leading_zero():
    assert d.license_numerics_match("017371", "17371")
    assert d.license_numerics_match("4643", "04643")


def test_license_prefix():
    assert d.license_numerics_match("17371", "MD17371")


# --- provider_type -------------------------------------------------------

def test_provider_type_abbrev_in_license_type():
    assert d.provider_type_matches("MD", "Medical Doctor", "")
    assert d.provider_type_matches("DDS", "DENTAL LICENSE", "")
    assert d.provider_type_matches("OD", "Optometrist", "")


def test_provider_type_mismatch():
    # Without expansions and no overlap → False
    assert not d.provider_type_matches("MD", "Physical Therapist License", "PT")


def test_provider_type_vacuous_when_empty():
    # No prov_type → always True (cannot prove mismatch)
    assert d.provider_type_matches("", "anything", "")


# --- score_candidate gate ------------------------------------------------

def test_gate_pass_first_and_license():
    rec = FakeRec(license_number="17371", licensee_first_name="John",
                  licensee_last_name="Doe")  # last name differs
    bd = d.score_candidate(rec, _master())
    assert bd.gate_passed     # first + license → passes


def test_gate_pass_first_and_last():
    rec = FakeRec(license_number="99999", licensee_first_name="John",
                  licensee_last_name="Smith")  # license differs
    bd = d.score_candidate(rec, _master())
    assert bd.gate_passed     # first + last → passes


def test_gate_fail_first_only():
    rec = FakeRec(license_number="99999", licensee_first_name="John",
                  licensee_last_name="Doe")
    bd = d.score_candidate(rec, _master())
    assert not bd.gate_passed   # only first → fails


def test_gate_never_uses_middle():
    rec = FakeRec(license_number="99999", licensee_first_name="X",
                  licensee_last_name="Smith", licensee_middle_name="JOHN")
    # Master "JOHN" matches candidate's MIDDLE, not first — gate must FAIL
    bd = d.score_candidate(rec, _master())
    assert not bd.gate_passed


# --- weight profile selection ------------------------------------------

def test_profile_license_present_score():
    rec = FakeRec(license_number="17371", licensee_first_name="John",
                  licensee_last_name="Smith", license_type="Medical Doctor",
                  state_code="NV")
    bd = d.score_candidate(rec, _master(), weight_profile="license_present")
    # All fields match — total should be 1.0
    assert bd.total >= 0.99
    assert bd.weight_profile == "license_present"


def test_profile_name_only_excludes_license():
    rec = FakeRec(license_number="bogus", licensee_first_name="John",
                  licensee_last_name="Smith", license_type="Medical Doctor",
                  state_code="NV")
    bd = d.score_candidate(rec, _master(), weight_profile="name_only")
    # license_numerics weight is 0; rest sum to 1.0 → total still high
    assert bd.license_numerics == 0
    assert bd.total >= 0.85
    assert bd.weight_profile == "name_only"


# --- evaluate (top-level) ----------------------------------------------

def test_evaluate_selects_single_passer():
    recs = [FakeRec(license_number="17371", licensee_first_name="John",
                    licensee_last_name="Smith", license_type="Medical Doctor",
                    state_code="NV")]
    v = d.evaluate(recs, _master())
    assert v.status == "selected"
    assert v.best is recs[0]


def test_evaluate_no_gate_pass():
    recs = [FakeRec(license_number="bogus", licensee_first_name="Mary",
                    licensee_last_name="Jones")]
    v = d.evaluate(recs, _master())
    assert v.status == "no_gate_pass"


def test_evaluate_provider_type_breaks_clear_margin():
    """When provider_type is the only differentiator, the matching candidate
    wins. The margin here (0.10 from provider_type weight) is > tiebreaker
    delta, so tiebreaker_used stays False — the higher score just wins."""
    rec_md = FakeRec(license_number="17371", licensee_first_name="John",
                     licensee_last_name="Smith", license_type="Medical Doctor",
                     state_code="NV")
    rec_dds = FakeRec(license_number="17371", licensee_first_name="John",
                      licensee_last_name="Smith", license_type="Dental",
                      state_code="NV")
    v = d.evaluate([rec_dds, rec_md], _master(prov_type="MD"))
    assert v.status == "selected"
    assert v.best is rec_md
    # 0.10 weight margin > 0.02 tiebreaker delta → clear win, not a tiebreak
    assert not v.tiebreaker_used


def test_evaluate_tiebreaker_within_002():
    """Close-margin scenario where provider_type fires as a tiebreaker.
    Both candidates have all weighted fields equal EXCEPT a small fuzzy
    last-name difference that brings their totals within 0.02."""
    # Both candidates have same license, first; differ only on last-name
    # spelling AND on license_type. The rapidfuzz last-name score should be
    # close enough to put totals within 0.02.
    rec_dds = FakeRec(license_number="17371", licensee_first_name="John",
                      licensee_last_name="Smyth", license_type="Dental",
                      state_code="NV")
    rec_md = FakeRec(license_number="17371", licensee_first_name="John",
                     licensee_last_name="Smith", license_type="Medical Doctor",
                     state_code="NV")
    # Both gate-pass. Provider_type favours rec_md. Delta should be small
    # enough that the tiebreaker logic fires.
    v = d.evaluate([rec_dds, rec_md], _master(prov_type="MD"))
    assert v.status == "selected"
    assert v.best is rec_md   # MD wins either by margin or tiebreak


# --- in-memory narrowing -----------------------------------------------

def test_narrowing_reduces_to_one():
    recs = [
        FakeRec(license_number="17371", licensee_first_name="John",
                licensee_last_name="Smith", license_type="Medical Doctor"),
        FakeRec(license_number="99999", licensee_first_name="John",
                licensee_last_name="Smith", license_type="Dental"),
    ]
    narrowed, status = d.apply_narrowing(recs, _master(prov_type="MD"))
    # Step 1 numeric license + first reduces to 1 (only first has matching license)
    assert status == "selected"
    assert len(narrowed) == 1
    assert narrowed[0].license_number == "17371"


def test_narrowing_ambiguous_when_two_identical():
    rec = FakeRec(license_number="17371", licensee_first_name="John",
                  licensee_last_name="Smith", license_type="Medical Doctor")
    rec2 = FakeRec(license_number="17371", licensee_first_name="John",
                   licensee_last_name="Smith", license_type="Medical Doctor")
    narrowed, status = d.apply_narrowing([rec, rec2], _master())
    assert status == "ambiguous"
