"""AttemptRecord + RowTrace dataclasses + JSON serializer.

A RowTrace is the per-input-row attempt log — it records every rung tried,
which board, which mode, what came back, and where the evidence landed. The
AI agent reads it as context if it gets invoked. The output emitter persists
it to PSV_DEV/Output/_traces/{YYYY-MM}/{run_id}/{master_row_id}.json.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
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
OUTCOME_PROVIDER_TYPE_MISMATCH = "provider_type_mismatch"
OUTCOME_ERROR = "error"
OUTCOME_SKIPPED_DUPLICATE = "skipped_duplicate"

# Final-outcome / failure-reason taxonomy (the structured codes the user
# clarified must always populate the `reason` column).
REASON_NAME_MISMATCH = "name_mismatch"
REASON_LICENSE_MISMATCH = "license_mismatch"
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    final_outcome: str = ""           # "Pass" | "Fail" | "EscalatedAi" | "Resolved"
    final_reason: Optional[str] = None  # one of REASON_* codes when not Pass

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
            if a.mode in ("license_number", "license_numeric_only",
                          "license_first_last", "license_and_last",
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
        }

    def write_json(self, trace_dir: Path) -> Path:
        trace_dir.mkdir(parents=True, exist_ok=True)
        path = trace_dir / f"{_FS_UNSAFE.sub('_', self.master_row_id)}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8")
        return path


def make_master_row_id(row_index: int, last_name: str, license_id: str) -> str:
    """Stable per-row key for trace files / nppes channel correlation."""
    safe_last = _FS_UNSAFE.sub("_", (last_name or "_")[:20])
    safe_lic = _FS_UNSAFE.sub("_", (license_id or "_")[:20])
    return f"row_{row_index:04d}_{safe_last}_{safe_lic}"
