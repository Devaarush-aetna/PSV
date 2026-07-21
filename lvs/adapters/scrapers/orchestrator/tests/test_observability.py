"""Unit tests for orchestrator/observability.py."""
from __future__ import annotations

import pytest
from orchestrator.observability import (
    AiObsRecord,
    TurnTokenLog,
    build_run_summary_row,
    compute_cost_usd,
    score_groundedness,
)


class TestComputeCostUsd:
    def test_input_only(self):
        assert compute_cost_usd(1_000_000, 0) == pytest.approx(3.00)

    def test_output_only(self):
        assert compute_cost_usd(0, 1_000_000) == pytest.approx(15.00)

    def test_mixed(self):
        # 500k input + 100k output
        expected = (0.5 * 3.00) + (0.1 * 15.00)
        assert compute_cost_usd(500_000, 100_000) == pytest.approx(expected)

    def test_zero(self):
        assert compute_cost_usd(0, 0) == pytest.approx(0.0)


class TestScoreGroundedness:
    def test_full_score(self):
        score, risk = score_groundedness(True, 0.90, ["try_search", "inspect_evidence"])
        assert score == 3
        assert risk == "low"

    def test_zero_score(self):
        score, risk = score_groundedness(False, None, [])
        assert score == 0
        assert risk == "high"

    def test_partial_two_low(self):
        score, risk = score_groundedness(True, 0.90, [])
        assert score == 2
        assert risk == "low"

    def test_partial_one_medium(self):
        score, risk = score_groundedness(False, None, ["try_search"])
        assert score == 1
        assert risk == "medium"

    def test_score_below_threshold(self):
        # candidate_score = 0.80 → no +1 for score
        score, risk = score_groundedness(True, 0.80, ["try_search"])
        assert score == 2
        assert risk == "low"

    def test_score_exact_threshold(self):
        # 0.85 → +1
        score, risk = score_groundedness(False, 0.85, [])
        assert score == 1
        assert risk == "medium"


class TestBuildRunSummaryRow:
    def _make_ai_rows(self, n_resolved=2, n_gave_up=1):
        rows = []
        for i in range(n_resolved):
            rows.append({
                "outcome": "resolved",
                "input_tokens": 1000,
                "output_tokens": 200,
                "cost_usd": 0.006,
            })
        for i in range(n_gave_up):
            rows.append({
                "outcome": "gave_up",
                "input_tokens": 500,
                "output_tokens": 100,
                "cost_usd": 0.003,
            })
        return rows

    def test_basic(self):
        ai_rows = self._make_ai_rows(2, 1)
        row = build_run_summary_row(
            run_id="test_run",
            state="KS",
            rows_processed=10,
            passes=7,
            fails=3,
            ai_rows=ai_rows,
            mean_latency_ms=1234.5,
            circuit_breaker_trips=0,
            started_at="2026-06-25T10:00:00",
            finished_at="2026-06-25T10:05:00",
        )
        assert row["run_id"] == "test_run"
        assert row["state"] == "KS"
        assert row["rows_processed"] == 10
        assert row["pass_rate"] == pytest.approx(0.7)
        assert row["ai_rows"] == 3
        assert row["ai_resolved"] == 2
        assert row["total_input_tokens"] == 2500
        assert row["total_output_tokens"] == 500
        assert row["total_tokens"] == 3000
        assert row["total_cost_usd"] == pytest.approx(0.015, abs=1e-6)
        assert row["mean_latency_ms"] == pytest.approx(1234.5)
        assert row["circuit_breaker_trips"] == 0

    def test_zero_division_guard(self):
        row = build_run_summary_row(
            run_id="r", state="KS",
            rows_processed=0, passes=0, fails=0,
            ai_rows=[], mean_latency_ms=None,
            circuit_breaker_trips=0,
            started_at="", finished_at="",
        )
        assert row["pass_rate"] == 0.0
        assert row["ai_rate"] == 0.0

    def test_missing_obs_fields(self):
        # ai_rows with missing keys should sum to 0 (not raise)
        ai_rows = [{"outcome": "resolved"}]
        row = build_run_summary_row(
            run_id="r", state="KS",
            rows_processed=5, passes=4, fails=1,
            ai_rows=ai_rows, mean_latency_ms=None,
            circuit_breaker_trips=0,
            started_at="", finished_at="",
        )
        assert row["total_input_tokens"] == 0
        assert row["total_cost_usd"] == 0.0
