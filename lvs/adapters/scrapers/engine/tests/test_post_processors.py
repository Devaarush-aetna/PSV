"""Unit tests for engine.post_processors.split_full_name.

Run with:
    cd lvs/adapters/scrapers
    python -m pytest engine/tests/test_post_processors.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running pytest from the scrapers/ dir
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from engine.post_processors import split_full_name


@pytest.mark.parametrize("full_name,expected", [
    # Trailing honorific — MI_LARA stores names like this. Regression for
    # row_0014 (Jang-en Sarah Lin): "Mrs." was parsed as the last name, which
    # dropped the real last name "Lin" and failed the post-license name gate.
    ("Jang-en Sarah Lin Mrs.", ("Jang-en", "Lin")),
    # Leading honorific
    ("Dr. John Smith", ("John", "Smith")),
    ("Mrs. Jane Doe", ("Jane", "Doe")),
    # Honorific + credential suffix together
    ("Prof Alan Turing PhD", ("Alan", "Turing")),
    # No honorific — unchanged behaviour
    ("Lindsay Marie Berishaj", ("Lindsay", "Berishaj")),
    ("Smith, John A", ("John", "Smith")),
    ("Charles Reeves, Jr., M.D.", ("Charles", "Reeves")),
])
def test_split_full_name_strips_honorifics(full_name, expected):
    assert split_full_name(full_name) == expected


def test_honorific_only_does_not_crash():
    """A degenerate name that is nothing but an honorific must not raise."""
    assert split_full_name("Mrs.") in (("", ""), ("", "Mrs."))


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
