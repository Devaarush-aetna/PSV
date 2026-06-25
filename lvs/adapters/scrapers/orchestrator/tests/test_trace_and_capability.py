"""Unit tests for trace dedup + capability filtering.

Run with:
    cd lvs/adapters/scrapers
    python -m pytest orchestrator/tests/test_trace_and_capability.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from orchestrator import capability, trace as trace_mod


# --- trace.normalize + signature -----------------------------------------

def test_normalize_query_value_strips_leading_zeros():
    assert trace_mod.normalize_query_value("017371") == "17371"


def test_normalize_query_value_uppercases():
    assert trace_mod.normalize_query_value("smith") == "SMITH"


def test_normalize_query_value_collapses_whitespace():
    assert trace_mod.normalize_query_value("  John  Smith  ") == "JOHN SMITH"


def test_signature_dedup():
    t = trace_mod.RowTrace(master_row_id="r1", run_id="20260623_1402",
                           state="NV", prov_type="MD")
    sig = trace_mod.make_signature("NV_MEDBOARD", "license_number",
                                    trace_mod.normalize_query_value("17371"))
    assert not t.has_signature(sig)
    a = trace_mod.AttemptRecord(seq=1, source_id="NV_MEDBOARD",
                                 board_url="", mode="license_number",
                                 query_repr="17371", query_signature=sig)
    t.append(a)
    assert t.has_signature(sig)


def test_license_attempts_returned_records():
    t = trace_mod.RowTrace(master_row_id="r1", run_id="20260623_1402",
                           state="NV", prov_type="MD")
    a1 = trace_mod.AttemptRecord(seq=1, source_id="NV_MEDBOARD", board_url="",
                                  mode="license_number", query_repr="x",
                                  query_signature="s1", record_count=0)
    a2 = trace_mod.AttemptRecord(seq=2, source_id="NV_MEDBOARD", board_url="",
                                  mode="last_name", query_repr="x",
                                  query_signature="s2", record_count=5)
    t.append(a1)
    assert not t.license_attempts_returned_records()
    t.append(a2)
    assert not t.license_attempts_returned_records()
    # Now add a license attempt that DID return rows
    a3 = trace_mod.AttemptRecord(seq=3, source_id="NV_MEDBOARD", board_url="",
                                  mode="license_first_last", query_repr="x",
                                  query_signature="s3", record_count=2)
    t.append(a3)
    assert t.license_attempts_returned_records()


# --- capability ---------------------------------------------------------

def test_required_fields_for():
    assert capability.required_fields_for("license_number") == ("license_id",)
    assert capability.required_fields_for("first_and_last") == ("first_name", "last_name")
    assert capability.required_fields_for("license_first_last") == ("license_id", "first_name", "last_name")


def test_canonical_ladder_order():
    ladder = capability.CANONICAL_LADDER
    assert ladder[0] == "license_number"
    assert ladder[1] == "license_numeric_only"
    assert "first_and_last" in ladder
    assert "last_name" in ladder
    assert ladder[-1] == "first_name"


def test_applicable_modes_filters_by_master_row():
    """A row with only first_name should yield only first_name rung —
    capability test using a real config so we know it's wired correctly."""
    from engine.validate import load_config
    cfg_path = Path(__file__).resolve().parents[2] / "sites" / "NV_MEDBOARD" / "config.yaml"
    if not cfg_path.exists():
        return  # skip if config absent
    cfg = load_config(str(cfg_path))
    master = {"first_name": "John", "last_name": "", "license_id": ""}
    modes = capability.applicable_modes(cfg, master)
    assert "first_name" in modes
    assert "license_number" not in modes  # license_id is empty
    assert "first_and_last" not in modes  # last_name is empty
