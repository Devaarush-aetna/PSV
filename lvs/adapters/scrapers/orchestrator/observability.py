"""AI fallback observability — token capture, cost estimation, groundedness scoring.

Consumed by:
  ai_agent.py       — populates AiObsRecord per invocation
  output_emitter.py — writes obs fields to ai_fallback CSV
  psv_test.py       — builds run_summary rows
  engine/telemetry.py — log_psv_run / log_psv_ai_call write to SQLite
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# claude-sonnet-4-6 pricing ($/1M tokens)
COST_INPUT_PER_1M: float = 3.00
COST_OUTPUT_PER_1M: float = 15.00


@dataclass
class TurnTokenLog:
    turn: int
    input_tokens: int
    output_tokens: int


@dataclass
class AiObsRecord:
    """Per-invocation AI observability bundle attached to AiAgentResult."""

    turn_token_logs: list[TurnTokenLog] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    model: str = "claude-sonnet-4-6"

    # Groundedness signals
    evidence_inspected: bool = False        # inspect_evidence tool was called
    search_count: int = 0                   # number of try_search calls
    turns_before_pick: int = 0             # turn index when pick_candidate fired
    candidate_score_at_pick: Optional[float] = None  # disambiguator score at pick
    hallucination_risk: str = "high"        # low | medium | high
    groundedness_score: int = 0             # 0-3


def compute_cost_usd(input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD from token counts."""
    return (
        input_tokens  / 1_000_000 * COST_INPUT_PER_1M
        + output_tokens / 1_000_000 * COST_OUTPUT_PER_1M
    )


def score_groundedness(
    evidence_inspected: bool,
    candidate_score_at_pick: Optional[float],
    tools_used: list[str],
) -> tuple[int, str]:
    """Return (score 0-3, hallucination_risk).

    Rubric:
      +1  inspect_evidence was called before pick_candidate
      +1  disambiguator score at pick >= 0.85
      +1  at least one try_search was performed

    Risk mapping: 3→low, 2→low, 1→medium, 0→high
    """
    score = 0
    if evidence_inspected:
        score += 1
    if candidate_score_at_pick is not None and candidate_score_at_pick >= 0.85:
        score += 1
    if "try_search" in tools_used:
        score += 1

    risk = {3: "low", 2: "low", 1: "medium", 0: "high"}[score]
    return score, risk


def build_run_summary_row(
    run_id: str,
    state: str,
    rows_processed: int,
    passes: int,
    fails: int,
    ai_rows: list[dict],
    mean_latency_ms: Optional[float],
    circuit_breaker_trips: int,
    started_at: str,
    finished_at: str,
) -> dict:
    """Aggregate one state's OutputEmitter._ai_rows into a single summary dict."""
    total_input  = sum(r.get("input_tokens") or 0 for r in ai_rows)
    total_output = sum(r.get("output_tokens") or 0 for r in ai_rows)
    total_cost   = sum(r.get("cost_usd") or 0.0 for r in ai_rows)
    ai_count     = len(ai_rows)
    ai_resolved  = sum(1 for r in ai_rows if r.get("outcome") == "resolved")

    pass_rate = round(passes / rows_processed, 4) if rows_processed else 0.0
    ai_rate   = round(ai_count / rows_processed, 4) if rows_processed else 0.0

    return {
        "run_id":               run_id,
        "state":                state,
        "rows_processed":       rows_processed,
        "passes":               passes,
        "fails":                fails,
        "pass_rate":            pass_rate,
        "ai_rows":              ai_count,
        "ai_rate":              ai_rate,
        "ai_resolved":          ai_resolved,
        "total_input_tokens":   total_input,
        "total_output_tokens":  total_output,
        "total_tokens":         total_input + total_output,
        "total_cost_usd":       round(total_cost, 6),
        "mean_latency_ms":      round(mean_latency_ms, 1) if mean_latency_ms else "",
        "circuit_breaker_trips": circuit_breaker_trips,
        "started_at":           started_at,
        "finished_at":          finished_at,
    }
