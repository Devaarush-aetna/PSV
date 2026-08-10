"""AI agent fallback — multi-turn tool-calling loop on the Anthropic Claude API.
Invoked when rule-based + NPPES ladders cannot resolve a row.

The agent receives the full attempt log + NPPES record + escalation reason
as context, and may call:
    - try_search(source_id, mode, fields)   — re-run a rung (signature-deduped)
    - inspect_evidence(attempt_seq)         — read raw HTML excerpt
    - pick_candidate(source_id, candidate_index)  — commit a prior candidate
    - report_site_drift(source_id, suspected_change, fix_hint)
    - give_up(reason)                       — terminate with structured reason

Failures always populate `reason` in AiAgentResult so the output emitter can
write it to ai_fallback channel. A shared circuit breaker tracks consecutive
errors and disables AI for the session after 2 failures.

If --ai-mock <path> is supplied via env (PSV_AI_MOCK_PATH), the agent reads
canned responses from that JSON file instead of calling Anthropic. This makes
end-to-end CI tests possible without API access.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from dotenv import load_dotenv
load_dotenv()

from engine.models import SearchQuery, SiteConfig

from . import config as cfg
from . import disambiguator as disamb
from . import drift_detector
from . import trace as trace_mod
from .observability import score_groundedness as _score_groundedness
from .nppes_client import NppesRecord, NpiDiscrepancy
from .trace import (
    AttemptRecord, RowTrace,
    OUTCOME_AI_BOARD_HIT, OUTCOME_MATCH_EXACT, OUTCOME_NO_RECORDS,
    REASON_AI_CIRCUIT_BREAKER_OPEN, REASON_AI_GAVE_UP,
    REASON_AI_MAX_TURNS_EXCEEDED, REASON_AI_TOOL_ERROR,
    make_signature, normalize_query_value, serialize_candidate,
)

log = logging.getLogger(__name__)

# Executor signature mirrors orchestrator.ladder.SearchExecutor.
SearchExecutor = Callable[[SiteConfig, SearchQuery, str], Awaitable[list]]

# ---------- Circuit breaker (module-level, shared across calls) ----------
# Only non-transient errors (auth, quota, bad-request) open the breaker.
# Connection errors and timeouts are transient — they do NOT count toward the limit.
_MAX_CONSECUTIVE_ERRORS = 2
_consecutive_errors: int = 0
_circuit_open: bool = False


def _is_circuit_open() -> bool:
    return _circuit_open


def reset_circuit_breaker() -> None:
    """Reset the circuit breaker state. Call at the start of each new run."""
    global _consecutive_errors, _circuit_open
    _consecutive_errors = 0
    _circuit_open = False


def _record_success() -> None:
    global _consecutive_errors
    _consecutive_errors = 0


def _is_transient_error(exc: Exception) -> bool:
    """Return True for retry-able errors that should NOT trip the circuit breaker.
    Permanent errors (auth, bad-request) still count toward the limit.
    """
    try:
        import anthropic as _ant
        # Network-level (no HTTP response) — always transient
        if isinstance(exc, (_ant.APIConnectionError, _ant.APITimeoutError)):
            return True
        # HTTP 429 (rate limit) and HTTP 5xx (server error) — transient, not a quota burn
        if isinstance(exc, (_ant.RateLimitError, _ant.InternalServerError)):
            return True
    except ImportError:
        pass
    # httpx-level errors that surface as generic exceptions
    exc_name = type(exc).__name__
    return exc_name in ("ConnectError", "ConnectTimeout", "ReadTimeout",
                        "WriteTimeout", "RemoteProtocolError")


def _record_failure(exc: Optional[Exception] = None) -> None:
    global _consecutive_errors, _circuit_open
    if exc is not None and _is_transient_error(exc):
        log.warning(
            "AI agent transient error (not counted toward circuit breaker): %s", exc
        )
        return
    _consecutive_errors += 1
    if _consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
        _circuit_open = True
        log.warning(
            "AI agent circuit breaker OPEN after %d consecutive errors",
            _consecutive_errors,
        )


# ---------- Anthropic client (lazy init) ----------
_anthropic_client = None


def _parse_custom_headers(raw: str) -> dict[str, str]:
    """Parse newline-delimited 'Key: Value' header string (same logic as SDK)."""
    headers: dict[str, str] = {}
    for line in raw.split("\n"):
        colon = line.find(":")
        if colon >= 0:
            headers[line[:colon].strip()] = line[colon + 1:].strip()
    return headers


def _get_client():
    global _anthropic_client
    if _anthropic_client is not None:
        return _anthropic_client
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    base_url    = os.environ.get("ANTHROPIC_BASE_URL", "")
    raw_headers = os.environ.get("ANTHROPIC_CUSTOM_HEADERS", "")
    try:
        import anthropic
        init_kwargs: dict = {"api_key": api_key}
        if base_url:
            init_kwargs["base_url"] = base_url
        if raw_headers:
            parsed = _parse_custom_headers(raw_headers)
            if parsed:
                init_kwargs["default_headers"] = parsed
        _anthropic_client = anthropic.AsyncAnthropic(**init_kwargs)
        log.info(
            "Anthropic client ready: %s",
            base_url or "https://api.anthropic.com",
        )
        return _anthropic_client
    except Exception as exc:
        log.warning("Anthropic client init failed: %s", exc)
        return None


# ---------- Tool schemas (Anthropic format) ----------
_TOOL_SCHEMAS = [
    {
        "name": "try_search",
        "description": (
            "Issue a new search rung against a specific board. "
            "The signature is auto-deduped — identical previous attempts will be skipped."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source_id": {"type": "string"},
                "mode": {
                    "type": "string",
                    "enum": [
                        "license_number", "first_and_last", "last_name",
                        "first_name", "license_first_last",
                        "license_and_last", "license_and_first",
                    ],
                },
                "fields": {
                    "type": "object",
                    "properties": {
                        "license_number": {"type": "string"},
                        "first_name": {"type": "string"},
                        "last_name": {"type": "string"},
                    },
                },
            },
            "required": ["source_id", "mode", "fields"],
        },
    },
    {
        "name": "inspect_evidence",
        "description": "Read HTML excerpt from a prior attempt's evidence folder.",
        "input_schema": {
            "type": "object",
            "properties": {"attempt_seq": {"type": "integer"}},
            "required": ["attempt_seq"],
        },
    },
    {
        "name": "pick_candidate",
        "description": "Commit a candidate from a prior attempt as the answer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source_id": {"type": "string"},
                "candidate_index": {"type": "integer"},
            },
            "required": ["source_id", "candidate_index"],
        },
    },
    {
        "name": "report_site_drift",
        "description": (
            "Emit a drift report. Never auto-applies a fix; only records the suggestion."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source_id": {"type": "string"},
                "suspected_change": {"type": "string"},
                "fix_hint": {"type": "string"},
            },
            "required": ["source_id", "suspected_change", "fix_hint"],
        },
    },
    {
        "name": "give_up",
        "description": "Terminate with a structured reason code.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": (
                        "Structured code (e.g. name_mismatch) or give_up:<text>."
                    ),
                },
            },
            "required": ["reason"],
        },
    },
]

_SYSTEM_PROMPT = """You are an expert PSV (Primary Source Verification) agent.
The rule-based ladder + NPPES retry have exhausted without resolving a license
lookup. You have the full attempt log, the row's master data, and the row's
NPPES record. Your job: figure out the correct license verification.

Available tools:
- try_search(source_id, mode, fields): issue a new rung against a board.
- inspect_evidence(attempt_seq): read raw HTML from that attempt's evidence
  folder. Use this when you suspect a layout change or parsing failure.
- pick_candidate(source_id, candidate_index): commit a candidate from a prior
  attempt. Use this when you can identify the right row in already-returned data.
  The `pool_index` field on each matched_candidate is the exact candidate_index
  to pass — no try_search needed for records already in matched_candidates.
- report_site_drift(source_id, suspected_change, fix_hint): emit a drift report.
  Do NOT call this unless evidence strongly suggests the board's HTML changed.
- give_up(reason): terminate. reason MUST be one of:
    name_mismatch | license_mismatch | provider_type_mismatch |
    ambiguous_after_narrowing | no_records | nppes_not_found |
    or "give_up:<custom_text>" if none fit.

Rules:
- Never guess. Prefer give_up(reason) over a low-confidence pick_candidate.
- Identical (source_id, mode, query) was already tried — those rungs will be
  skipped if you re-issue them. Look at attempts already in the log first.
- Middle name is unreliable on board sites; do not use it as a distinguishing
  signal.
- Provider type is a useful hint but NOT a blocking criterion. Different boards
  label the same profession with different type codes (e.g. "State Medical Board"
  vs prov_type "PH"). If name and/or license match strongly, prefer
  pick_candidate over give_up(provider_type_mismatch).
- You have at most {max_turns} turns.
"""


# Token pricing for claude-sonnet-4-6 (USD per token).
# Update when model or pricing changes.
_USD_PER_INPUT_TOKEN: float = 3.00 / 1_000_000   # $3.00 / 1M
_USD_PER_OUTPUT_TOKEN: float = 15.00 / 1_000_000  # $15.00 / 1M
_AI_MODEL: str = "claude-sonnet-4-6"


@dataclass
class AiAgentResult:
    outcome: str                       # "resolved" | "gave_up" | "errored" | "skipped"
    reason: str                        # one of trace.REASON_* codes (or ai_gave_up:<text>)
    chosen_candidate: Optional[Any] = None
    chosen_breakdown: Optional[disamb.ScoreBreakdown] = None
    chosen_source_id: Optional[str] = None
    turns_used: int = 0
    tools_used: list[str] = field(default_factory=list)
    drift_reports: list[dict] = field(default_factory=list)
    raw_messages: list[dict] = field(default_factory=list)
    # Token / cost telemetry (accumulated across all turns)
    model: str = _AI_MODEL
    input_tokens: int = 0
    output_tokens: int = 0
    usd_cost: float = 0.0
    # Confidence: ScoreBreakdown.total from chosen candidate (None when not resolved)
    confidence_score: Optional[float] = None
    # Groundedness scoring (populated after resolution)
    groundedness_score: int = 0
    hallucination_risk: str = "high"


def _build_context_message(
    master_row: dict,
    nppes: Optional[NppesRecord],
    discrepancy: Optional[NpiDiscrepancy],
    trace: RowTrace,
    routing: list[dict],
) -> str:
    # Compute per-source pool offsets so each matched_candidate carries the
    # exact pool_index the AI should pass to pick_candidate().
    pool_offset: dict[str, int] = {}
    attempts_summary = []
    for a in trace.attempts:
        d = _summarize_attempt(a)
        if a.candidates and "matched_candidates" in d:
            start = pool_offset.get(a.source_id, 0)
            for i, cand in enumerate(d["matched_candidates"]):
                cand["pool_index"] = start + i
            pool_offset[a.source_id] = start + len(a.candidates)
        attempts_summary.append(d)

    payload = {
        "escalate_to_ai_reason": trace.escalate_to_ai_reason,
        "master_row": {
            "first_name": master_row.get("first_name"),
            "last_name": master_row.get("last_name"),
            "middle_name": master_row.get("middle_name"),
            "license_id": master_row.get("license_id"),
            "prov_type": master_row.get("prov_type"),
            "lic_state": master_row.get("lic_state"),
            "lic_type": master_row.get("lic_type"),
            "npi_no": master_row.get("npi_no"),
        },
        "nppes_record": _summarize_nppes(nppes),
        "npi_discrepancy": discrepancy.to_dict() if discrepancy else None,
        "attempts": attempts_summary,
        "routing": routing,
    }
    return json.dumps(payload, indent=2, default=str)


def _summarize_nppes(nppes: Optional[NppesRecord]) -> Optional[dict]:
    if not nppes:
        return None
    return {
        "npi": nppes.npi,
        "first_name": nppes.first_name,
        "last_name": nppes.last_name,
        "middle_name": nppes.middle_name,
        "credential": nppes.credential,
        "primary_taxonomy_desc": nppes.primary_taxonomy_desc,
        "license_numbers": nppes.license_numbers[:5],
        "other_names": nppes.other_names[:3],
        "fetch_status": nppes.fetch_status,
    }


def _summarize_attempt(a: AttemptRecord) -> dict:
    d = {
        "seq": a.seq,
        "source_id": a.source_id,
        "mode": a.mode,
        "query_repr": a.query_repr,
        "used_npi_data": a.used_npi_data,
        "differing_field": a.differing_field,
        "record_count": a.record_count,
        "outcome": a.outcome,
        "confidence": a.confidence,
        "evidence_dir": a.evidence_dir,
        "error_msg": a.error_msg,
    }
    if a.candidates:
        d["matched_candidates"] = [serialize_candidate(r) for r in a.candidates]
    return d


# ----- Mock-mode loader (for --ai-mock testing) --------------------------

def _load_mock_responses() -> Optional[list]:
    path = os.environ.get(cfg.AI_MOCK_PATH_ENV)
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("AI mock file %s unreadable: %s", path, exc)
        return None


# ----- The agent loop ----------------------------------------------------

async def run_ai_agent(
    master_row: dict,
    nppes: Optional[NppesRecord],
    discrepancy: Optional[NpiDiscrepancy],
    routed_configs: list[SiteConfig],
    trace: RowTrace,
    executor: SearchExecutor,
    candidate_cache: dict[str, list],
    timeout_s: int = 45,
    drift_dir: Optional["Path"] = None,
) -> AiAgentResult:
    """Multi-turn Claude tool-use loop. Returns AiAgentResult with reason always set."""
    if _is_circuit_open():
        return AiAgentResult(outcome="skipped", reason=REASON_AI_CIRCUIT_BREAKER_OPEN)

    mock = _load_mock_responses()
    use_mock = mock is not None

    client = None
    if not use_mock:
        client = _get_client()
        if client is None:
            log.debug("Anthropic API key not configured — AI agent skipped")
            return AiAgentResult(outcome="skipped", reason=REASON_AI_CIRCUIT_BREAKER_OPEN)

    routing = [
        {
            "source_id": c.identity.source_id,
            "board_url": c.identity.base_url,
            "supported_modes": sorted(
                __import__(
                    "orchestrator.capability", fromlist=["supported_modes"]
                ).supported_modes(c)
            ),
        }
        for c in routed_configs
    ]

    system_prompt = _SYSTEM_PROMPT.format(max_turns=cfg.AI_MAX_TURNS)
    context_text = _build_context_message(master_row, nppes, discrepancy, trace, routing)

    # Anthropic messages — system is separate; start with one user message.
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": context_text},
    ]

    result = AiAgentResult(outcome="errored", reason=REASON_AI_TOOL_ERROR)
    cfg_by_sid = {c.identity.source_id: c for c in routed_configs}

    for turn in range(cfg.AI_MAX_TURNS):
        result.turns_used = turn + 1
        try:
            if use_mock:
                if turn >= len(mock):
                    result.outcome = "gave_up"
                    result.reason = REASON_AI_MAX_TURNS_EXCEEDED
                    return result
                response_content = mock[turn]
                # Mock format: list of content blocks
                if not isinstance(response_content, list):
                    response_content = [{"type": "text", "text": str(response_content)}]
                stop_reason = "tool_use" if any(
                    b.get("type") == "tool_use" for b in response_content
                ) else "end_turn"
            else:
                # Inner retry loop for transient errors (rate limits, connection drops).
                # Non-transient errors (auth, bad-request) re-raise immediately to the
                # outer except so the circuit breaker counts them correctly.
                _last_exc: Optional[Exception] = None
                for _retry_n, _retry_delay in enumerate([0, 2, 5]):
                    if _retry_n > 0:
                        log.warning(
                            "AI agent turn %d transient retry %d/%d (delay=%ds): %s",
                            turn + 1, _retry_n, len([0, 2, 5]) - 1, _retry_delay, _last_exc,
                        )
                        await asyncio.sleep(_retry_delay)
                    try:
                        resp = await client.messages.create(
                            model=_AI_MODEL,
                            max_tokens=2048,
                            system=system_prompt,
                            messages=messages,
                            tools=_TOOL_SCHEMAS,
                            temperature=0,
                        )
                        _record_success()
                        response_content = [b.model_dump() for b in resp.content]
                        stop_reason = resp.stop_reason
                        if hasattr(resp, "usage") and resp.usage is not None:
                            in_tok = getattr(resp.usage, "input_tokens", 0) or 0
                            out_tok = getattr(resp.usage, "output_tokens", 0) or 0
                            result.input_tokens += in_tok
                            result.output_tokens += out_tok
                            result.usd_cost += (
                                in_tok * _USD_PER_INPUT_TOKEN
                                + out_tok * _USD_PER_OUTPUT_TOKEN
                            )
                        _last_exc = None
                        break  # success
                    except Exception as inner_exc:
                        _last_exc = inner_exc
                        if not _is_transient_error(inner_exc):
                            raise  # non-transient → outer except handles it
                else:
                    # All retries exhausted for a transient error — propagate it
                    assert _last_exc is not None
                    raise _last_exc

        except Exception as exc:
            _record_failure(exc)
            log.warning(
                "AI agent turn %d API error (%d turns remaining): %s",
                turn + 1, cfg.AI_MAX_TURNS - turn - 1, exc,
            )
            # The assistant message was never appended so the conversation state
            # is still consistent — retry on the next turn if one is available.
            if turn < cfg.AI_MAX_TURNS - 1:
                await asyncio.sleep(3)
                continue
            log.error("AI agent: all %d turns exhausted after API errors", cfg.AI_MAX_TURNS)
            result.outcome = "errored"
            result.reason = REASON_AI_TOOL_ERROR
            return result

        # Append assistant turn
        messages.append({"role": "assistant", "content": response_content})

        if stop_reason == "end_turn":
            # No tool use — treat as gave_up
            text = next(
                (b.get("text", "") for b in response_content if b.get("type") == "text"),
                "",
            )
            result.outcome = "gave_up"
            result.reason = (
                f"{REASON_AI_GAVE_UP}:{text[:120]}" if text else REASON_AI_GAVE_UP
            )
            return result

        # Process tool_use blocks
        tool_results: list[dict[str, Any]] = []
        terminated = False

        for block in response_content:
            if block.get("type") != "tool_use":
                continue
            name = block.get("name", "")
            args = block.get("input") or {}
            tool_use_id = block.get("id", "")
            result.tools_used.append(name)

            tool_result = await _dispatch_tool(
                name, args, master_row, trace, executor, cfg_by_sid,
                candidate_cache, result, timeout_s, drift_dir,
            )
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": json.dumps(tool_result, default=str)[:4000],
            })

            # Terminate on give_up or a successful pick_candidate (outcome==resolved).
            # Do NOT terminate on a failed pick_candidate (gate check returned ok=False):
            # feed the error back to the AI so it can retry or give_up cleanly, which
            # produces outcome="gave_up" rather than the misleading "errored" default.
            if name == "give_up" or result.outcome == "resolved":
                terminated = True
                break

        # Feed tool results back as a user message
        if tool_results:
            messages.append({"role": "user", "content": tool_results})

        if terminated:
            return result

    result.outcome = "gave_up"
    result.reason = REASON_AI_MAX_TURNS_EXCEEDED
    return result


async def _dispatch_tool(
    name: str,
    args: dict,
    master_row: dict,
    trace: RowTrace,
    executor: SearchExecutor,
    cfg_by_sid: dict[str, SiteConfig],
    candidate_cache: dict[str, list],
    result: AiAgentResult,
    timeout_s: int,
    drift_dir: Optional["Path"] = None,
) -> dict:
    if name == "give_up":
        reason = args.get("reason", "ai_gave_up")
        result.outcome = "gave_up"
        if reason.startswith("give_up:") or reason.startswith("ai_gave_up"):
            result.reason = reason
        elif reason in (
            trace_mod.REASON_NAME_MISMATCH, trace_mod.REASON_LICENSE_MISMATCH,
            trace_mod.REASON_PROVIDER_TYPE_MISMATCH,
            trace_mod.REASON_AMBIGUOUS_AFTER_NARROWING,
            trace_mod.REASON_NO_RECORDS, trace_mod.REASON_NPPES_NOT_FOUND,
        ):
            result.reason = reason
        else:
            result.reason = f"{REASON_AI_GAVE_UP}:{reason[:120]}"
        return {"ok": True}

    if name == "report_site_drift":
        sid = args.get("source_id", "")
        suspected = args.get("suspected_change", "")
        fix_hint = args.get("fix_hint", "")
        ev = ""
        for a in reversed(trace.attempts):
            if a.source_id == sid and a.evidence_dir:
                ev = a.evidence_dir
                break
        report = drift_detector.append_drift_report(
            source_id=sid, suspected_selector=suspected,
            evidence_dir=ev, fix_hint=fix_hint, severity="med",
            drift_dir=drift_dir, run_id=trace.run_id,
        )
        result.drift_reports.append(report)
        return {"ok": True, "report": report}

    if name == "inspect_evidence":
        seq = int(args.get("attempt_seq", 0))
        attempt = next((a for a in trace.attempts if a.seq == seq), None)
        if not attempt or not attempt.evidence_dir:
            return {"ok": False, "error": "no_evidence_dir_for_seq"}
        try:
            html_path = Path(attempt.evidence_dir) / "search_results.html"
            if not html_path.exists():
                html_path = Path(attempt.evidence_dir) / "error.html"
            if not html_path.exists():
                return {"ok": False, "error": "no_html_file"}
            text = html_path.read_text(encoding="utf-8", errors="replace")[:8000]
            return {"ok": True, "html_excerpt": text}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:200]}

    if name == "pick_candidate":
        sid = args.get("source_id", "")
        idx = int(args.get("candidate_index", -1))
        pool = candidate_cache.get(sid, [])
        if 0 <= idx < len(pool):
            chosen = pool[idx]
            bd = disamb.score_candidate(chosen, master_row, weight_profile="license_present")
            if not bd.gate_passed:
                return {
                    "ok": False,
                    "error": (
                        f"candidate {idx} failed gate (score={bd.total:.3f}, "
                        "gate_passed=False). Board data may be corrupted — give_up."
                    ),
                    "score_breakdown": bd.to_dict(),
                }
            result.outcome = "resolved"
            result.chosen_candidate = chosen
            result.chosen_breakdown = bd
            result.confidence_score = round(bd.total, 4) if bd is not None else None
            result.chosen_source_id = sid
            result.reason = "ai_pick_candidate"
            _ev = "inspect_evidence" in result.tools_used
            result.groundedness_score, result.hallucination_risk = _score_groundedness(
                _ev, result.confidence_score, result.tools_used
            )
            return {"ok": True, "chosen": _candidate_summary(chosen)}
        return {"ok": False, "error": "invalid_candidate_index"}

    if name == "try_search":
        sid = args.get("source_id", "")
        mode = args.get("mode", "")
        fields = args.get("fields") or {}
        cfg_obj = cfg_by_sid.get(sid)
        if not cfg_obj:
            return {"ok": False, "error": "unknown_source_id"}
        sq = SearchQuery(
            mode=mode,
            query=" ".join(str(v) for v in fields.values() if v),
            license_number=fields.get("license_number") or None,
            first_name=fields.get("first_name") or None,
            last_name=fields.get("last_name") or None,
        )
        norm = normalize_query_value(sq.query)
        sig = make_signature(sid, mode, norm)
        if trace.has_signature(sig):
            return {"ok": True, "skipped_duplicate": True, "record_count": 0}
        _t0 = time.monotonic()
        try:
            records = await executor(cfg_obj, sq, trace.run_id)
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:200]}
        _duration_ms = int((time.monotonic() - _t0) * 1000)
        seq = len(trace.attempts) + 1
        attempt = AttemptRecord(
            seq=seq, source_id=sid, board_url=cfg_obj.identity.base_url,
            mode=mode, query_repr=sq.query[:80], query_signature=sig,
            used_npi_data=False, record_count=len(records),
            outcome=OUTCOME_AI_BOARD_HIT if records else OUTCOME_NO_RECORDS,
            duration_ms=_duration_ms,
            evidence_dir="",
        )
        trace.append(attempt)
        candidate_cache.setdefault(sid, []).extend(records or [])

        # If the disambiguator selects exactly one unambiguous match, short-
        # circuit to Pass immediately — no need for the AI to call pick_candidate.
        if records:
            verdict = disamb.evaluate(records, master_row)
            if verdict.status == "selected" and verdict.best is not None:
                attempt.outcome = OUTCOME_MATCH_EXACT
                bd = verdict.best_breakdown
                result.outcome = "resolved"
                result.chosen_candidate = verdict.best
                result.chosen_breakdown = bd
                result.confidence_score = round(bd.total, 4) if bd is not None else None
                result.chosen_source_id = sid
                result.reason = "ai_try_search_auto_resolved"
                _ev = "inspect_evidence" in result.tools_used
                result.groundedness_score, result.hallucination_risk = _score_groundedness(
                    _ev, result.confidence_score, result.tools_used
                )
                return {
                    "ok": True,
                    "record_count": len(records),
                    "auto_resolved": True,
                    "candidates": [_candidate_summary(verdict.best)],
                    "attempt_seq": seq,
                }

        return {
            "ok": True,
            "record_count": len(records or []),
            "candidates": [_candidate_summary(r) for r in (records or [])[:5]],
            "attempt_seq": seq,
        }

    return {"ok": False, "error": f"unknown_tool:{name}"}


def _candidate_summary(rec: Any) -> dict:
    return {
        "license_number": getattr(rec, "license_number", None),
        "first_name": getattr(rec, "licensee_first_name", None),
        "last_name": getattr(rec, "licensee_last_name", None),
        "middle_name": getattr(rec, "licensee_middle_name", None),
        "license_type": getattr(rec, "license_type", None),
        "profession_code": getattr(rec, "profession_code", None),
        "status": str(getattr(rec, "status", "")),
        "expiration_date": str(getattr(rec, "expiration_date", "")),
    }
