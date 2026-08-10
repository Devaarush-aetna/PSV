"""AttemptRecord + RowTrace dataclasses + JSON serializer.

A RowTrace is the per-input-row attempt log — it records every rung tried,
which board, which mode, what came back, and where the evidence landed. The
AI agent reads it as context if it gets invoked. The output emitter persists
it to PSV_DEV/Output/{YYYYMM}/{run_id}/Traces/{master_row_id}.json.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Outcome codes used on AttemptRecord — must align with ladder.py's logic.
OUTCOME_MATCH_EXACT = "match_exact"
OUTCOME_MATCH_VIA_DISAMBIGUATOR = "match_via_disambiguator"
OUTCOME_NO_RECORDS = "no_records"
OUTCOME_AMBIGUOUS = "ambiguous"
OUTCOME_NARROWED = "narrowed"
OUTCOME_NAME_MISMATCH = "name_mismatch"
OUTCOME_LICENSE_MISMATCH = "license_mismatch"
# Name matched but license was never confirmed (no license found or license mismatch
# discovered after detail-page fetch). Triggers Fail even with a high name fuzzy score.
OUTCOME_NAME_MATCH_NO_LICENSE = "name_match_no_license"
OUTCOME_PROVIDER_TYPE_MISMATCH = "provider_type_mismatch"
OUTCOME_ERROR = "error"
OUTCOME_SKIPPED_DUPLICATE = "skipped_duplicate"
# Set by the AI agent's try_search when records are returned but not yet
# evaluated — distinct from OUTCOME_MATCH_EXACT, which requires disambiguator
# confirmation.
OUTCOME_AI_BOARD_HIT = "ai_board_hit"

# Final-outcome / failure-reason taxonomy (the structured codes the user
# clarified must always populate the `reason` column).
REASON_NAME_MISMATCH = "name_mismatch"
REASON_LICENSE_MISMATCH = "license_mismatch"
REASON_NAME_MATCH_NO_LICENSE = "name_match_no_license"
REASON_PROVIDER_TYPE_MISMATCH = "provider_type_mismatch"
REASON_AMBIGUOUS_AFTER_NARROWING = "ambiguous_after_narrowing"
REASON_NO_RECORDS = "no_records"
REASON_NO_ROUTING = "no_routing"
REASON_NPI_NO_MISSING = "npi_no_missing"
REASON_NPPES_NOT_FOUND = "nppes_not_found"
REASON_AI_CIRCUIT_BREAKER_OPEN = "ai_circuit_breaker_open"
REASON_AI_MAX_TURNS_EXCEEDED = "ai_max_turns_exceeded"
REASON_AI_GAVE_UP = "ai_gave_up"
REASON_AI_TOOL_ERROR = "ai_tool_error"
REASON_STATE_CAPTCHA_BLOCKED = "state_captcha_blocked"


_FS_UNSAFE = re.compile(r'[\\/:*?"<>|\s]+')


def serialize_candidate(rec: Any) -> dict:
    """Serialize one LicenseRecord-like object to a plain dict safe for JSON.

    Reads both LicenseRecord attributes (mapped/parsed fields) and raw_fields
    (unmapped columns such as ENDORSEMENT_NUMBER_1..7). Works for csv_bulk,
    pdf_bulk, and browser archetypes — boards without endorsement columns
    simply omit that key. All None/NaN/empty values are stripped so the
    stored dict stays compact.

    Used in two places:
      - AttemptRecord.to_dict()  → persisted to Traces JSON for debugging
      - ai_agent._summarize_attempt() → included in the AI context message
    """
    raw = getattr(rec, "raw_fields", {}) or {}

    def _raw(key: str):
        v = raw.get(key)
        if v is None or (isinstance(v, float) and v != v):  # None or NaN
            return None
        s = str(v).strip()
        return s if s else None

    out = {
        "licensee_name_raw": _raw("full_name"),
        "parsed_first": getattr(rec, "licensee_first_name", None),
        "parsed_last": getattr(rec, "licensee_last_name", None),
        "parsed_middle": getattr(rec, "licensee_middle_name", None),
        "license_number": getattr(rec, "license_number", None),
        "license_type": getattr(rec, "license_type", None) or _raw("license_type"),
        "board": _raw("board"),
        "status": str(getattr(rec, "status", "") or ""),
        "expiration_date": str(getattr(rec, "expiration_date", "") or ""),
        "issue_date": str(getattr(rec, "issue_date", "") or ""),
        "city": getattr(rec, "city", None),
        "state_code": getattr(rec, "state_code", None),
    }

    # Endorsements: numbered raw columns (ENDORSEMENT_NUMBER_1..7).
    # Sequential — first null number terminates the loop.
    endorsements = []
    for i in range(1, 8):
        num = _raw(f"ENDORSEMENT_NUMBER_{i}")
        if not num:
            break
        entry: dict = {"number": num}
        t = _raw(f"ENDORSEMENT_TYPE_{i}")
        s = _raw(f"ENDORSEMENT_STATUS_{i}")
        if t:
            entry["type"] = t
        if s:
            entry["status"] = s
        endorsements.append(entry)
    if endorsements:
        out["endorsements"] = endorsements

    return {k: v for k, v in out.items() if v not in (None, "", "None", "nan")}


def normalize_query_value(value: str) -> str:
    """Canonicalize a query value for signature dedup. Case-insensitive,
    whitespace-collapsed, leading-zero-stripped for digits.

      "John Smith"  -> "JOHN SMITH"
      "  17371  "   -> "17371"
      "017371"      -> "17371"
      "LC-04643"    -> "LC-04643"   (mixed alphanumeric kept as-is, upper)
    """
    if value is None:
        return ""
    v = re.sub(r"\s+", " ", str(value).strip().upper())
    if v.isdigit():
        v = v.lstrip("0") or "0"
    return v


def make_signature(source_id: str, mode: str, normalized_query: str) -> str:
    """Canonical signature for the loop-guard. Identical signatures from
    master + NPPES retry collapse to one execution."""
    return f"{source_id}|{mode}|{normalized_query}"


@dataclass
class AttemptRecord:
    seq: int
    source_id: str
    board_url: str
    mode: str
    query_repr: str
    query_signature: str
    used_npi_data: bool = False
    differing_field: Optional[str] = None
    record_count: int = 0
    outcome: str = ""
    confidence: Optional[float] = None
    weight_profile_used: Optional[str] = None  # "license_present" | "name_only"
    evidence_dir: str = ""
    duration_ms: int = 0
    error_msg: Optional[str] = None
    # Matched LicenseRecord objects for non-passing outcomes (name_mismatch,
    # license_mismatch, ambiguous). Populated by the ladder; serialized into
    # matched_candidates in to_dict() for trace JSON persistence and used
    # by the AI agent context builder.
    candidates: list = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "seq": self.seq,
            "source_id": self.source_id,
            "board_url": self.board_url,
            "mode": self.mode,
            "query_repr": self.query_repr,
            "query_signature": self.query_signature,
            "used_npi_data": self.used_npi_data,
            "differing_field": self.differing_field,
            "record_count": self.record_count,
            "outcome": self.outcome,
            "confidence": self.confidence,
            "weight_profile_used": self.weight_profile_used,
            "evidence_dir": self.evidence_dir,
            "duration_ms": self.duration_ms,
            "error_msg": self.error_msg,
        }
        if self.candidates:
            d["matched_candidates"] = [serialize_candidate(r) for r in self.candidates]
        return d


@dataclass
class RowTrace:
    master_row_id: str
    run_id: str
    state: str
    prov_type: str
    npi_no: str = ""
    attempts: list[AttemptRecord] = field(default_factory=list)
    seen_signatures: set[str] = field(default_factory=set)
    escalate_to_ai_reason: Optional[str] = None
    nppes_used: bool = False
    nppes_discrepancy: Optional[dict] = None
    final_outcome: str = ""           # "Pass" | "Fail" | "Skip" | "EscalatedAi" | "Resolved"
    final_reason: Optional[str] = None  # one of REASON_* codes when not Pass
    no_licensure_required: bool = False  # True when state does not require this prov_type
    # Post-license name gate (set by run_state_orchestrated after ladder, before emitter)
    epdb_name_score: Optional[float] = None
    nppes_name_score: Optional[float] = None
    name_gate_reason: Optional[str] = None  # "name_gate_manual" | None

    def append(self, rec: AttemptRecord) -> None:
        self.attempts.append(rec)
        if rec.query_signature:
            self.seen_signatures.add(rec.query_signature)

    def has_signature(self, signature: str) -> bool:
        return signature in self.seen_signatures

    def license_attempts_returned_records(self) -> bool:
        """Used to pick the disambiguator weight profile.
        Returns True if any license-based rung returned >0 records."""
        for a in self.attempts:
            if a.mode in ("license_number", "license_number_exact", "license_numeric_only",
                          "license_formatted", "license_first_last", "license_and_last",
                          "license_and_first") and a.record_count > 0:
                return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "master_row_id": self.master_row_id,
            "run_id": self.run_id,
            "state": self.state,
            "prov_type": self.prov_type,
            "npi_no": self.npi_no,
            "attempts": [a.to_dict() for a in self.attempts],
            "seen_signatures": sorted(self.seen_signatures),
            "escalate_to_ai_reason": self.escalate_to_ai_reason,
            "nppes_used": self.nppes_used,
            "nppes_discrepancy": self.nppes_discrepancy,
            "final_outcome": self.final_outcome,
            "final_reason": self.final_reason,
            "epdb_name_score": self.epdb_name_score,
            "nppes_name_score": self.nppes_name_score,
            "name_gate_reason": self.name_gate_reason,
        }

    def write_json(self, trace_dir: Path) -> Path:
        trace_dir.mkdir(parents=True, exist_ok=True)
        path = trace_dir / f"{_FS_UNSAFE.sub('_', self.master_row_id)}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8")
        return path


def make_master_row_id(row_index: int, npi_no: str) -> str:
    """Stable per-row key for trace files / output channel correlation.
    Format: row_{NNNN}_{npi_no}  e.g. row_0042_1234567890
    When npi_no is absent the segment is empty: row_0042_
    """
    safe_npi = _FS_UNSAFE.sub("_", (npi_no or "").strip()[:20]) or "000"
    return f"row_{row_index:04d}_{safe_npi}"
