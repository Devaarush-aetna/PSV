"""Unit tests for first_and_last two-pass license fallback in search_by_multi_column.

Covers the bug where first_and_last mode returned 0 records because
query.license_number (from master_row, possibly a different board's license)
was AND-filtered together with first_name/last_name, blocking the name match.

Run with:
    cd lvs/adapters/scrapers
    python -m pytest test_csv_bulk_first_and_last.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.csv_extractor import search_by_multi_column


# ---------------------------------------------------------------------------
# Shared fixture: a minimal CT-ELICENSE-style DataFrame
# ---------------------------------------------------------------------------

COL_MAP = {
    "license_number": "LICENSE NO.",
    "first_name": "FIRST NAME",
    "last_name": "LAST NAME",
}

def _make_df():
    return pd.DataFrame([
        {"LICENSE NO.": "009012", "FIRST NAME": "JULIA",   "LAST NAME": "GIVENS"},
        {"LICENSE NO.": "000001", "FIRST NAME": "THOMAS",  "LAST NAME": "RYAN"},
        {"LICENSE NO.": "000004", "FIRST NAME": "JULIA",   "LAST NAME": "SMITH"},   # same first, diff last
        {"LICENSE NO.": "000005", "FIRST NAME": "SARAH",   "LAST NAME": "GIVENS"},  # same last, diff first
    ])


# ---------------------------------------------------------------------------
# 1. First pass: license + name both match → single precise result
# ---------------------------------------------------------------------------

def test_first_and_last_license_matches_returns_one_record():
    df = _make_df()
    results = search_by_multi_column(
        df, COL_MAP,
        license_number="009012",
        first_name="Julia",
        last_name="Givens",
    )
    assert len(results) == 1
    assert results[0]["FIRST NAME"] == "JULIA"
    assert results[0]["LAST NAME"] == "GIVENS"


# ---------------------------------------------------------------------------
# 2. First pass: license from a different board → 0; name-only → finds her
#    This is the Julia Givens / CT_ELICENSE regression case.
# ---------------------------------------------------------------------------

def test_first_and_last_mismatched_license_returns_zero():
    """Without the fallback, a non-matching license_number silently kills results."""
    df = _make_df()
    results = search_by_multi_column(
        df, COL_MAP,
        license_number="RN-99999",   # wrong board's license
        first_name="Julia",
        last_name="Givens",
    )
    assert results == []


def test_first_and_last_name_only_finds_record():
    """Name-only search (license_number=None) always finds the person."""
    df = _make_df()
    results = search_by_multi_column(
        df, COL_MAP,
        license_number=None,
        first_name="Julia",
        last_name="Givens",
    )
    assert len(results) == 1
    assert results[0]["LICENSE NO."] == "009012"


# ---------------------------------------------------------------------------
# 3. Leading-zero normalization still works in first pass
# ---------------------------------------------------------------------------

def test_first_and_last_leading_zero_normalized():
    df = _make_df()
    # "9012" should normalize-match "009012"
    results = search_by_multi_column(
        df, COL_MAP,
        license_number="9012",
        first_name="Julia",
        last_name="Givens",
    )
    assert len(results) == 1
    assert results[0]["LICENSE NO."] == "009012"


# ---------------------------------------------------------------------------
# 4. Name-only: multiple people with same first name → returns all, not filtered
# ---------------------------------------------------------------------------

def test_first_only_returns_multiple():
    df = _make_df()
    results = search_by_multi_column(
        df, COL_MAP,
        license_number=None,
        first_name="Julia",
        last_name=None,
    )
    assert len(results) == 2  # Julia Givens + Julia Smith


# ---------------------------------------------------------------------------
# 5. License-inclusive combo modes should NOT fall back (strict AND preserved)
#    Simulates license_and_last / license_first_last behavior.
# ---------------------------------------------------------------------------

def test_license_and_name_mode_strict_no_match():
    """When the caller intentionally passes a license+name combo, 0 means 0."""
    df = _make_df()
    # Wrong license + correct name → 0 (expected: no fallback at this layer)
    results = search_by_multi_column(
        df, COL_MAP,
        license_number="000001",   # Thomas Ryan's license
        first_name="Julia",
        last_name="Givens",
    )
    assert results == []


# ---------------------------------------------------------------------------
# 6. Case-insensitive matching
# ---------------------------------------------------------------------------

def test_case_insensitive_name_match():
    df = _make_df()
    results = search_by_multi_column(
        df, COL_MAP,
        license_number=None,
        first_name="julia",
        last_name="GIVENS",
    )
    assert len(results) == 1


# ---------------------------------------------------------------------------
# 7. No filters applied → returns empty (guard against open scan)
# ---------------------------------------------------------------------------

def test_no_filters_returns_empty():
    df = _make_df()
    results = search_by_multi_column(
        df, COL_MAP,
        license_number=None,
        first_name=None,
        last_name=None,
    )
    assert results == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
