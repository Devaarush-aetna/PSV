"""Unit tests for orchestrator.name_gate.

Run with:
    cd lvs/adapters/scrapers
    python -m pytest orchestrator/tests/test_name_gate.py -v
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

# Allow running pytest from the scrapers/ dir
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from orchestrator import name_gate


@dataclass
class FakeRec:
    licensee_first_name: str = ""
    licensee_last_name: str = ""
    licensee_full_name: str = ""
    status: object = None


# ---------------------------------------------------------------------------
# Swap-tolerant rescue (regression: MI_LARA row_0066)
# ---------------------------------------------------------------------------

def test_swapped_first_last_approves():
    """Master row has first/last transposed relative to the board record.

    The gate runs only after a board LICENSE match, so a name that matches
    exactly once transposed is the same person and must approve — not route
    to Manual. Regression for row_0066 (master "Lin / Jang-En" vs board
    "Jang-en / Lin").
    """
    master = {"first_name": "Lin", "last_name": "Jang-En", "middle_name": ""}
    rec = FakeRec(licensee_first_name="Jang-en", licensee_last_name="Lin")
    result = name_gate.evaluate_name_gate(master, rec, nppes=None)
    assert result.verdict == "approve", result
    assert result.max_score >= 0.95, result


def test_swapped_with_trailing_honorific_full_name():
    """Board stores full name with an appended honorific: 'Jang-en Sarah Lin Mrs.'.

    The trailing 'Mrs.' must be stripped AND the swap detected.
    """
    master = {"first_name": "Lin", "last_name": "Jang-En", "middle_name": ""}
    rec = FakeRec(licensee_full_name="Jang-en Sarah Lin Mrs.")
    result = name_gate.evaluate_name_gate(master, rec, nppes=None)
    assert result.verdict == "approve", result
    assert result.max_score >= 0.95, result


def test_trailing_honorific_stripped():
    """_strip_suffix_and_initial must drop a trailing honorific like 'MRS'."""
    assert name_gate._strip_suffix_and_initial("LIN MRS", is_first=False) == "LIN"
    assert name_gate._strip_suffix_and_initial("SMITH MR", is_first=False) == "SMITH"


# ---------------------------------------------------------------------------
# Guardrails: the swap rescue must NOT create false approvals
# ---------------------------------------------------------------------------

def test_genuinely_different_name_not_rescued_by_swap():
    """Two unrelated names must stay 'manual' — swap rescue must not approve."""
    master = {"first_name": "Robert", "last_name": "Anderson", "middle_name": ""}
    rec = FakeRec(licensee_first_name="Jennifer", licensee_last_name="Nguyen")
    result = name_gate.evaluate_name_gate(master, rec, nppes=None)
    assert result.verdict == "manual", result


def test_straight_exact_match_still_approves():
    """Non-swapped exact match still short-circuits to approve (skipped)."""
    master = {"first_name": "Scarlett", "last_name": "Harrison", "middle_name": ""}
    rec = FakeRec(licensee_first_name="Scarlett", licensee_last_name="Harrison")
    result = name_gate.evaluate_name_gate(master, rec, nppes=None)
    assert result.verdict == "approve", result
    assert result.skipped is True, result


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
