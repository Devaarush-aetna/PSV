"""Per-row rule-based ladder driver.

Three nested loops with signature dedup:
  Loop 1: across boards routed for (state, prov_type)
  Loop 2: across canonical rungs the board supports + master row populates
  Loop 3: in-memory disambiguation-narrowing rungs

If the master ladder exhausts and NPPES data is available, runs a targeted
retry ladder using ONLY the fields that differ between master and NPPES.

Every rung's signature (source_id, mode, normalized_query) is deduped via
RowTrace.seen_signatures — the same query is never run twice across master
+ NPPES retries.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from engine.models import SearchQuery, SiteConfig

from . import capability, config as cfg
from . import disambiguator as disamb
from . import trace as trace_mod
from .nppes_client import NppesRecord, NpiDiscrepancy
from .trace import (
    AttemptRecord, RowTrace,
    OUTCOME_AMBIGUOUS, OUTCOME_ERROR, OUTCOME_LICENSE_MISMATCH,
    OUTCOME_MATCH_EXACT, OUTCOME_MATCH_VIA_DISAMBIGUATOR, OUTCOME_NAME_MISMATCH,
    OUTCOME_NARROWED, OUTCOME_NO_RECORDS, OUTCOME_PROVIDER_TYPE_MISMATCH,
    OUTCOME_SKIPPED_DUPLICATE,
    REASON_AMBIGUOUS_AFTER_NARROWING, REASON_LICENSE_MISMATCH,
    REASON_NAME_MISMATCH, REASON_NO_RECORDS, REASON_PROVIDER_TYPE_MISMATCH,
    make_signature, normalize_query_value,
)

log = logging.getLogger(__name__)


# A SearchExecutor is the function that actually runs ONE query against ONE
# board and returns a list of records. It's injected by the caller (psv_test)
# so the ladder doesn't need to know about Playwright/PsvBrowser/dispatcher
# internals.
SearchExecutor = Callable[[SiteConfig, SearchQuery, str], Awaitable[list]]


@dataclass
class PlannedAttempt:
    mode: str
    query: SearchQuery
    normalized_query: str
    driving_field: Optional[str] = None  # set for NPPES retry rungs


@dataclass
class LadderResult:
    status: str             # "Pass" | "EscalateAi" | "Fail"
    best_record: Optional[Any] = None
    best_breakdown: Optional[disamb.ScoreBreakdown] = None
    reason: Optional[str] = None    # one of trace.REASON_* codes
    npi_substituted: bool = False
    tiebreaker_used: bool = False
    weight_profile_used: str = "license_present"


def _build_query(mode: str, master_row: dict, override_fields: Optional[dict] = None,
                 provider_type_override: Optional[str] = None,
                 license_type_override: Optional[str] = None,
                 ) -> tuple[SearchQuery, str]:
    """Build a SearchQuery for a given mode + master row. Returns (query, normalized_value)."""
    o = override_fields or {}
    first = o.get("first_name", master_row.get("first_name") or None)
    last = o.get("last_name", master_row.get("last_name") or None)
    lic = o.get("license_id", master_row.get("license_id") or None)

    # Normalize query value used for signature dedup + folder labels
    if mode == "license_number":
        query_str = lic or ""
    elif mode == "license_numeric_only":
        query_str = re.sub(r"\D", "", lic or "")
    elif mode == "first_name":
        query_str = first or ""
    elif mode == "last_name":
        query_str = last or ""
    elif mode in ("first_and_last", "first_and_last_typed"):
        query_str = f"{first} {last}".strip() if first and last else (last or first or "")
    else:
        query_str = lic or last or first or ""

    # Synthetic modes — engine sees canonical mode name
    if mode == "license_numeric_only":
        actual_mode = "license_number"
    elif mode == "first_and_last_typed":
        actual_mode = "first_and_last"
    else:
        actual_mode = mode

    # license_numeric_only also overrides license_number on the structured field
    license_number = re.sub(r"\D", "", lic or "") if mode == "license_numeric_only" else lic

    sq = SearchQuery(
        mode=actual_mode,
        query=query_str,
        license_number=license_number or None,
        first_name=first,
        last_name=last,
        # middle_name DELIBERATELY OMITTED — never used per user spec
        provider_type=provider_type_override or None,
        license_type=license_type_override or None,
    )
    return sq, normalize_query_value(query_str)


def build_attempt_plan(config: SiteConfig, master_row: dict,
                       license_type: Optional[str] = None,
                       ) -> list[PlannedAttempt]:
    """Filter the canonical ladder by board capability + populated fields.

    license_type: board-specific dropdown value (e.g. "NP", "LPN") to set on every
    SearchQuery for boards that require a license-type selector (via {type} template
    in extra_selects). Resolved from _SOCRATA_TYPE_MAP by the caller.
    """
    plans: list[PlannedAttempt] = []
    seen_norms: set[tuple[str, str]] = set()  # (mode_key, normalized_value) within this board

    for mode in capability.applicable_modes(config, master_row):
        # For first_and_last_typed, inject the dropdown value from prov_type_values.
        pt_override: Optional[str] = None
        if mode == "first_and_last_typed":
            pt_override = config.identity.prov_type_values.get(master_row.get("prov_type", ""))
            if not pt_override:
                continue  # mapping missing at runtime — skip gracefully

        query, norm = _build_query(mode, master_row, provider_type_override=pt_override,
                                   license_type_override=license_type)
        if not norm:
            continue
        # Skip if license_numeric_only would produce identical value to plain license_number
        # (avoids redundant sig — but loop guard would catch it anyway).
        key = (mode, norm)
        if key in seen_norms:
            continue
        seen_norms.add(key)
        plans.append(PlannedAttempt(mode=mode, query=query, normalized_query=norm))
    return plans


def build_targeted_retry_plan(config: SiteConfig, master_row: dict,
                              nppes: NppesRecord,
                              discrepancy: NpiDiscrepancy,
                              license_type: Optional[str] = None,
                              ) -> list[PlannedAttempt]:
    """Build NPPES retry rungs — only test the fields that differ."""
    plans: list[PlannedAttempt] = []
    caps = capability.supported_modes(config)

    # First-name diff: try first_and_last with NPPES first
    if "first_name" in discrepancy.differing_fields and "first_and_last" in caps:
        if nppes.first_name and master_row.get("last_name"):
            override = {"first_name": nppes.first_name}
            sq, norm = _build_query("first_and_last", master_row, override, license_type_override=license_type)
            plans.append(PlannedAttempt(mode="first_and_last", query=sq,
                                         normalized_query=norm, driving_field="first_name"))

    # Last-name diff: last_name with NPPES last; first_and_last with NPPES last
    if "last_name" in discrepancy.differing_fields:
        if "last_name" in caps and nppes.last_name:
            override = {"last_name": nppes.last_name}
            sq, norm = _build_query("last_name", master_row, override, license_type_override=license_type)
            plans.append(PlannedAttempt(mode="last_name", query=sq,
                                         normalized_query=norm, driving_field="last_name"))
        if "first_and_last" in caps and nppes.last_name and master_row.get("first_name"):
            override = {"last_name": nppes.last_name}
            sq, norm = _build_query("first_and_last", master_row, override, license_type_override=license_type)
            plans.append(PlannedAttempt(mode="first_and_last", query=sq,
                                         normalized_query=norm, driving_field="last_name"))

    # Extra NPPES licenses: try license_number for each
    if "license_number" in caps:
        for lic_entry in discrepancy.extra_nppes_licenses[:5]:  # cap at 5 to bound work
            num = (lic_entry.get("number") or "").strip()
            if not num:
                continue
            override = {"license_id": num}
            sq, norm = _build_query("license_number", master_row, override)
            plans.append(PlannedAttempt(mode="license_number", query=sq,
                                         normalized_query=norm, driving_field="license_number"))
            # Also try numeric-only form
            num_only = re.sub(r"\D", "", num)
            if num_only and num_only != num:
                override2 = {"license_id": num_only}
                sq2, norm2 = _build_query("license_number", master_row, override2)
                plans.append(PlannedAttempt(mode="license_number", query=sq2,
                                             normalized_query=norm2,
                                             driving_field="license_number"))

    return plans


# --------------------------------------------------------------------------
# Detail-page secondary fetch helper
# --------------------------------------------------------------------------

async def _fetch_detail_record(
    cfg_obj: SiteConfig, record: Any, trace: RowTrace,
    executor: SearchExecutor, timeout_s: int,
) -> Any:
    """Re-search by the board's own license number to trigger detail_click_single
    and return an expiry-enriched record.  Used when a multi-row rung found the
    right candidate but never visited the detail page (so expiration_date is None).
    Returns the original record unchanged when not applicable or on failure."""
    if not (getattr(cfg_obj.results, "has_detail_page", False)
            and getattr(cfg_obj.results, "detail_trigger", None)):
        return record
    board_lic = (getattr(record, "license_number", None) or "").strip()
    if not board_lic:
        return record
    sig = make_signature(cfg_obj.identity.source_id, "license_number", board_lic)
    if trace.has_signature(sig):
        return record  # already attempted this exact query
    q = SearchQuery(mode="license_number", query=board_lic, license_number=board_lic)
    try:
        detail_records = await asyncio.wait_for(
            executor(cfg_obj, q, trace.run_id), timeout=float(timeout_s),
        )
        trace.seen_signatures.add(sig)
        for dr in detail_records:
            if getattr(dr, "expiration_date", None) is not None:
                return dr
    except Exception as exc:
        log.debug("[%s] detail expiry re-fetch failed for '%s': %s",
                  cfg_obj.identity.source_id, board_lic, exc)
    return record


# --------------------------------------------------------------------------
# The driver
# --------------------------------------------------------------------------

async def run_ladder(
    routed_configs: list[SiteConfig],
    master_row: dict,
    nppes_record: Optional[NppesRecord],
    discrepancy: Optional[NpiDiscrepancy],
    trace: RowTrace,
    executor: SearchExecutor,
    timeout_s: int = 45,
    board_license_type_map: Optional[dict[str, str]] = None,
) -> LadderResult:
    """Run the per-row ladder. Updates `trace` in place and returns LadderResult."""
    if not routed_configs:
        trace.final_outcome = "Fail"
        trace.final_reason = trace_mod.REASON_NO_RECORDS
        return LadderResult(status="Fail", reason=trace_mod.REASON_NO_RECORDS)

    last_specific_reason: Optional[str] = None

    blm = board_license_type_map or {}

    # ===================== Loop 1: master ladder over boards =====================
    for cfg_obj in routed_configs:
        lt_for_board = blm.get(cfg_obj.identity.source_id)
        plans = build_attempt_plan(cfg_obj, master_row, license_type=lt_for_board)
        if not plans:
            continue

        # ============== Loop 2: rungs on this board ==============
        for plan in plans:
            sig = make_signature(cfg_obj.identity.source_id, plan.mode, plan.normalized_query)
            if trace.has_signature(sig):
                trace.append(_skipped_attempt(cfg_obj, plan, sig, trace))
                continue

            attempt, records = await _execute_one(
                cfg_obj, plan, sig, master_row, trace, executor, timeout_s,
                used_npi=False, differing_field=None,
            )
            trace.append(attempt)

            verdict = await _evaluate_records(records, master_row, trace)
            attempt.confidence = verdict.best_breakdown.total if verdict.best_breakdown else None
            attempt.weight_profile_used = verdict.best_breakdown.weight_profile if verdict.best_breakdown else None

            if verdict.status == "selected":
                best = verdict.best
                if getattr(best, "expiration_date", None) is None:
                    best = await _fetch_detail_record(
                        cfg_obj, best, trace, executor, timeout_s,
                    )
                attempt.outcome = OUTCOME_MATCH_EXACT
                trace.final_outcome = "Pass"
                return LadderResult(
                    status="Pass",
                    best_record=best,
                    best_breakdown=verdict.best_breakdown,
                    tiebreaker_used=verdict.tiebreaker_used,
                    weight_profile_used=verdict.best_breakdown.weight_profile,
                )

            if verdict.status == "narrow":
                attempt.outcome = OUTCOME_NARROWED
                # ====== Loop 3: in-memory narrowing ======
                narrowed_pool, narrowed_status = disamb.apply_narrowing(
                    verdict.gate_passers, master_row,
                )
                if narrowed_status == "selected" and narrowed_pool:
                    chosen = narrowed_pool[0]
                    if getattr(chosen, "expiration_date", None) is None:
                        chosen = await _fetch_detail_record(
                            cfg_obj, chosen, trace, executor, timeout_s,
                        )
                    bd = disamb.score_candidate(
                        chosen, master_row,
                        weight_profile=_pick_profile(trace),
                    )
                    attempt.outcome = OUTCOME_MATCH_VIA_DISAMBIGUATOR
                    trace.final_outcome = "Pass"
                    return LadderResult(
                        status="Pass", best_record=chosen, best_breakdown=bd,
                        weight_profile_used=bd.weight_profile,
                    )
                # Still ambiguous → escalate to AI
                attempt.outcome = OUTCOME_AMBIGUOUS
                trace.escalate_to_ai_reason = REASON_AMBIGUOUS_AFTER_NARROWING
                last_specific_reason = REASON_AMBIGUOUS_AFTER_NARROWING
                # Fall through to next rung to give it another shot? No — per spec,
                # narrowing failure stops THIS board. Continue to next board.
                break

            if verdict.status == "no_gate_pass":
                if records:
                    attempt.outcome = _diagnose_failure_outcome(records, master_row)
                    last_specific_reason = _outcome_to_reason(attempt.outcome)
                else:
                    attempt.outcome = OUTCOME_NO_RECORDS
                # try next rung on this board

            if verdict.status == "ambiguous":
                attempt.outcome = OUTCOME_AMBIGUOUS
                trace.escalate_to_ai_reason = REASON_AMBIGUOUS_AFTER_NARROWING
                last_specific_reason = REASON_AMBIGUOUS_AFTER_NARROWING
                break  # stop this board

        # end of rung loop for this board
        if trace.escalate_to_ai_reason:
            # Don't bother with remaining boards if narrowing was already ambiguous
            # — go to NPPES retry path. (Could also try other boards; conservative.)
            break

    # ===================== NPPES targeted retry =====================
    if nppes_record and discrepancy and not discrepancy.is_empty():
        trace.nppes_used = True
        for cfg_obj in routed_configs:
            lt_for_board = blm.get(cfg_obj.identity.source_id)
            retry_plans = build_targeted_retry_plan(cfg_obj, master_row, nppes_record, discrepancy,
                                                     license_type=lt_for_board)
            for plan in retry_plans:
                sig = make_signature(cfg_obj.identity.source_id, plan.mode, plan.normalized_query)
                if trace.has_signature(sig):
                    trace.append(_skipped_attempt(cfg_obj, plan, sig, trace,
                                                   used_npi=True,
                                                   differing_field=plan.driving_field))
                    continue

                attempt, records = await _execute_one(
                    cfg_obj, plan, sig, master_row, trace, executor, timeout_s,
                    used_npi=True, differing_field=plan.driving_field,
                )
                trace.append(attempt)
                verdict = await _evaluate_records(records, master_row, trace)
                attempt.confidence = verdict.best_breakdown.total if verdict.best_breakdown else None
                attempt.weight_profile_used = verdict.best_breakdown.weight_profile if verdict.best_breakdown else None

                if verdict.status == "selected":
                    best = verdict.best
                    if getattr(best, "expiration_date", None) is None:
                        best = await _fetch_detail_record(
                            cfg_obj, best, trace, executor, timeout_s,
                        )
                    attempt.outcome = OUTCOME_MATCH_VIA_DISAMBIGUATOR
                    trace.final_outcome = "Pass"
                    return LadderResult(
                        status="Pass", best_record=best,
                        best_breakdown=verdict.best_breakdown,
                        npi_substituted=True,
                        tiebreaker_used=verdict.tiebreaker_used,
                        weight_profile_used=verdict.best_breakdown.weight_profile,
                    )
                if verdict.status == "narrow":
                    narrowed_pool, narrowed_status = disamb.apply_narrowing(
                        verdict.gate_passers, master_row,
                    )
                    if narrowed_status == "selected" and narrowed_pool:
                        chosen = narrowed_pool[0]
                        bd = disamb.score_candidate(
                            chosen, master_row,
                            weight_profile=_pick_profile(trace),
                        )
                        attempt.outcome = OUTCOME_MATCH_VIA_DISAMBIGUATOR
                        trace.final_outcome = "Pass"
                        return LadderResult(
                            status="Pass", best_record=chosen, best_breakdown=bd,
                            npi_substituted=True,
                            weight_profile_used=bd.weight_profile,
                        )
                    attempt.outcome = OUTCOME_AMBIGUOUS
                if verdict.status == "no_gate_pass":
                    if records:
                        attempt.outcome = _diagnose_failure_outcome(records, master_row)
                    else:
                        attempt.outcome = OUTCOME_NO_RECORDS

    # Both ladders exhausted.
    final_reason = (trace.escalate_to_ai_reason
                    or last_specific_reason
                    or REASON_NO_RECORDS)
    trace.escalate_to_ai_reason = trace.escalate_to_ai_reason or final_reason
    trace.final_outcome = "EscalateAi"
    trace.final_reason = final_reason
    return LadderResult(status="EscalateAi", reason=final_reason)


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------

async def _execute_one(cfg_obj: SiteConfig, plan: PlannedAttempt, sig: str,
                       master_row: dict, trace: RowTrace,
                       executor: SearchExecutor, timeout_s: int,
                       used_npi: bool, differing_field: Optional[str]
                       ) -> tuple[AttemptRecord, list]:
    """Run one rung. Captures duration, error, evidence_dir."""
    src_id = cfg_obj.identity.source_id
    state = cfg_obj.identity.state
    seq = len(trace.attempts) + 1
    t0 = time.time()
    error_msg: Optional[str] = None
    records: list = []

    try:
        records = await asyncio.wait_for(
            executor(cfg_obj, plan.query, trace.run_id),
            timeout=float(timeout_s),
        )
    except asyncio.TimeoutError:
        error_msg = f"timeout_{timeout_s}s"
        log.warning("[%s] timeout mode=%s sig=%s", src_id, plan.mode, sig)
    except Exception as exc:
        error_msg = str(exc)[:300]
        log.warning("[%s] error mode=%s sig=%s: %s", src_id, plan.mode, sig, exc)

    duration_ms = int((time.time() - t0) * 1000)

    # Evidence dir is computed from run_id + state + source_id + query label
    # We import lazily because engine.evidence has Playwright deps; we only
    # need the path resolver, not the capture call.
    try:
        from engine.evidence import resolve_evidence_path, _query_label  # type: ignore
        ev_dir = str(resolve_evidence_path(
            src_id, trace.run_id, state=state,
            query_label=_query_label(plan.query),
        ))
    except Exception:
        ev_dir = ""

    attempt = AttemptRecord(
        seq=seq,
        source_id=src_id,
        board_url=cfg_obj.identity.base_url,
        mode=plan.mode,
        query_repr=plan.query.query[:80],
        query_signature=sig,
        used_npi_data=used_npi,
        differing_field=differing_field,
        record_count=len(records),
        outcome="" if not error_msg else OUTCOME_ERROR,
        evidence_dir=ev_dir,
        duration_ms=duration_ms,
        error_msg=error_msg,
    )
    return attempt, records or []


def _skipped_attempt(cfg_obj: SiteConfig, plan: PlannedAttempt, sig: str,
                     trace: RowTrace, used_npi: bool = False,
                     differing_field: Optional[str] = None) -> AttemptRecord:
    return AttemptRecord(
        seq=len(trace.attempts) + 1,
        source_id=cfg_obj.identity.source_id,
        board_url=cfg_obj.identity.base_url,
        mode=plan.mode,
        query_repr=plan.query.query[:80],
        query_signature=sig,
        used_npi_data=used_npi,
        differing_field=differing_field,
        record_count=0,
        outcome=OUTCOME_SKIPPED_DUPLICATE,
    )


def _pick_profile(trace: RowTrace) -> str:
    """Choose disambiguator weight profile based on whether license-based
    attempts have returned any records yet."""
    return "license_present" if trace.license_attempts_returned_records() else "name_only"


async def _evaluate_records(records: list, master_row: dict, trace: RowTrace
                            ) -> disamb.DisambiguationVerdict:
    if not records:
        return disamb.DisambiguationVerdict(status="no_gate_pass")
    profile = _pick_profile(trace)
    return disamb.evaluate(records, master_row, weight_profile=profile)


def _diagnose_failure_outcome(records: list, master_row: dict) -> str:
    """For records that returned but didn't pass the gate — pick the most
    specific outcome code so the trace tells us WHY (name vs license vs type).
    """
    m_first = (master_row.get("first_name") or "").upper()
    m_last = (master_row.get("last_name") or "").upper()
    m_lic = master_row.get("license_id") or ""
    m_pt = (master_row.get("prov_type") or "").upper()

    def _parts(rec):
        f = getattr(rec, "licensee_first_name", "") or ""
        l = getattr(rec, "licensee_last_name", "") or ""
        if not f and not l:
            full = getattr(rec, "licensee_full_name", "") or ""
            toks = full.split()
            if len(toks) >= 2:
                f, l = toks[0], toks[-1]
        return f, l

    any_first_match = any(
        disamb.first_name_matches(m_first, _parts(r)[0])
        for r in records
    )
    any_last_match = any(
        disamb.last_name_matches(m_last, _parts(r)[1])
        for r in records
    )
    any_lic_match = any(
        disamb.license_numerics_match(m_lic, getattr(r, "license_number", "") or "")
        for r in records
    ) if m_lic else False

    if any_first_match and any_last_match and m_lic and not any_lic_match:
        return OUTCOME_LICENSE_MISMATCH
    if (any_first_match or any_last_match) and not (any_first_match and (any_last_match or any_lic_match)):
        return OUTCOME_NAME_MISMATCH
    if m_pt and not any(
        disamb.provider_type_matches(m_pt,
                                     getattr(r, "license_type", "") or "",
                                     getattr(r, "profession_code", "") or "")
        for r in records
    ):
        return OUTCOME_PROVIDER_TYPE_MISMATCH
    return OUTCOME_NAME_MISMATCH


def _outcome_to_reason(outcome: str) -> str:
    return {
        OUTCOME_NAME_MISMATCH: REASON_NAME_MISMATCH,
        OUTCOME_LICENSE_MISMATCH: REASON_LICENSE_MISMATCH,
        OUTCOME_PROVIDER_TYPE_MISMATCH: REASON_PROVIDER_TYPE_MISMATCH,
        OUTCOME_AMBIGUOUS: REASON_AMBIGUOUS_AFTER_NARROWING,
        OUTCOME_NARROWED: REASON_AMBIGUOUS_AFTER_NARROWING,
    }.get(outcome, REASON_NO_RECORDS)
