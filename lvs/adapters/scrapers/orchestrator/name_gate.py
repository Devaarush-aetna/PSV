"""Post-license name validation gate.

After a board license match (Pass), validates that the board-returned name is
consistent with one of two reference sources:

  1. NPPES name  (from NPI registry — checked first; short-circuits on approve)
  2. EPDB name   (from the input master row — checked second)

Routing by max(nppes_score, epdb_score):
  ≥ 0.80  → "approve"   → AddLicense as normal
  [0.70, 0.80) → "ai_review" → force AI disambiguator → AI_ADD_LICENSE + Manual
  < 0.70  → "manual"   → Manual channel only

The gate is skipped (verdict="approve", skipped=True) when the EPDB name already
exactly matches the board name after cleanup.

Name cleanup applied to ALL names before scoring:
  - _normalize_name()  (uppercase + hyphen/apostrophe/period → space)
  - Strip credential/generational suffixes (MD, RN, Jr, etc.)
  - Strip interior single-letter middle initials
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from . import config as cfg
from . import disambiguator as disamb

# Reuse the pre-normalised suffix frozenset from disambiguator.
_SUFFIX_NORM: frozenset[str] = disamb._NAME_SUFFIXES_NORM


# --------------------------------------------------------------------------
# Name cleanup
# --------------------------------------------------------------------------

def _clean_for_gate(raw: str) -> tuple[str, str]:
    """Split a raw full-name or first/last pair into (first_clean, last_clean)
    suitable for fuzzy scoring.

    If *raw* contains a comma it is treated as "Last, First [Middle]" format.
    Otherwise it is treated as "First [Middle] Last".

    Returns (first_clean, last_clean) both uppercased, hyphen/apostrophe→space,
    suffixes stripped, middle initial removed.
    """
    # Normalise whitespace and punctuation
    normed = disamb._normalize_name(raw)
    if not normed:
        return "", ""

    # Split on comma: "SMITH, JOHN A" → last="SMITH", rest="JOHN A"
    if "," in normed:
        comma_idx = normed.index(",")
        last_part = normed[:comma_idx].strip()
        first_part = normed[comma_idx + 1:].strip()
    else:
        toks = normed.split()
        if len(toks) == 1:
            return toks[0], toks[0]
        first_part = toks[0]
        last_part = " ".join(toks[1:])

    first_clean = _strip_suffix_and_initial(first_part, is_first=True)
    last_clean = _strip_suffix_and_initial(last_part, is_first=False)
    return first_clean, last_clean


def _clean_pair(first_raw: str, last_raw: str) -> tuple[str, str]:
    """Normalize a pre-split first/last pair (e.g. from a board record)."""
    first_n = disamb._normalize_name(first_raw)
    last_n = disamb._normalize_name(last_raw)
    first_clean = _strip_suffix_and_initial(first_n, is_first=True)
    last_clean = _strip_suffix_and_initial(last_n, is_first=False)
    return first_clean, last_clean


def _strip_suffix_and_initial(text: str, is_first: bool) -> str:
    """Strip honorific prefixes, credential/generational suffixes, and middle
    initials from a name component.  Single-letter tokens that are NOT the
    first token are treated as middle initials and removed.
    """
    toks = text.split()
    # Strip trailing suffixes (MD, RN, Jr, etc.)
    while toks:
        norm_tok = re.sub(r"[.\-]", "", toks[-1]).upper()
        if norm_tok in _SUFFIX_NORM:
            toks = toks[:-1]
        else:
            break
    # Strip leading honorific prefixes (Dr, Mr, Prof, etc.)
    while toks:
        norm_tok = re.sub(r"[.\-]", "", toks[0]).upper()
        if norm_tok in disamb._NAME_PREFIXES_NORM:
            toks = toks[1:]
        else:
            break
    # Strip interior single-letter middle initials (keep index-0 token)
    if not is_first:
        # For last-name tokens, strip leading single letters too (e.g. "A SMITH")
        toks = [t for i, t in enumerate(toks) if not (len(t) == 1 and i > 0)]
    else:
        # For first-name tokens, strip any single-letter tokens after index 0
        toks = [toks[0]] + [t for t in toks[1:] if len(t) > 1] if toks else []
    return " ".join(toks)


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def _name_pair_score(f1: str, l1: str, f2: str, l2: str) -> float:
    """Average of first_name_score and last_name_score.

    Falls back to the single available component when one side is empty.
    Returns 0.0 when both sides have no usable content.
    """
    has_first = bool(f1 and f2)
    has_last = bool(l1 and l2)
    if not has_first and not has_last:
        return 0.0
    if has_first and has_last:
        return (disamb.first_name_score(f1, f2) + disamb.last_name_score(l1, l2)) / 2.0
    if has_last:
        return disamb.last_name_score(l1, l2)
    return disamb.first_name_score(f1, f2)


# --------------------------------------------------------------------------
# Result dataclass
# --------------------------------------------------------------------------

@dataclass
class NameGateResult:
    """Result of evaluate_name_gate()."""
    epdb_score: Optional[float]       # None when short-circuited on NPPES
    nppes_score: Optional[float]      # None when NPPES unavailable
    max_score: float
    verdict: str                      # "approve" | "ai_review" | "manual"
    skipped: bool = False             # True when EPDB already exactly matches board


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def evaluate_name_gate(
    master_row: dict,
    board_record: Any,
    nppes: Optional[Any] = None,
    approve_threshold: float = cfg.NAME_GATE_THRESHOLD,
    ai_band_low: float = cfg.NAME_GATE_AI_BAND_LOW,
) -> NameGateResult:
    """Post-license name validation gate.

    Checks NPPES name first (short-circuits on approve), then EPDB name.

    Args:
        master_row:        Input row dict with "first_name" and "last_name".
        board_record:      Board-scraped record with licensee_first/last/full_name.
        nppes:             NppesRecord (or None).
        approve_threshold: Score above which the gate approves (default 0.80).
        ai_band_low:       Lower bound of the AI-review band (default 0.70).

    Returns:
        NameGateResult
    """
    # ---- Extract and clean board name ----
    board_first_raw = (getattr(board_record, "licensee_first_name", "") or "").strip()
    board_last_raw = (getattr(board_record, "licensee_last_name", "") or "").strip()

    if not board_first_raw and not board_last_raw:
        board_full = (getattr(board_record, "licensee_full_name", "") or "").strip()
        if board_full:
            # _split_full_name returns (first, last) already in upper/cleaned form
            board_first_raw, board_last_raw = disamb._split_full_name(
                board_full, master_row.get("last_name", "")
            )

    board_first, board_last = _clean_pair(board_first_raw, board_last_raw)

    # If the board record has no name at all, we cannot score — approve silently.
    if not board_first and not board_last:
        return NameGateResult(
            epdb_score=None, nppes_score=None,
            max_score=1.0, verdict="approve", skipped=True,
        )

    # ================================================================
    # Step 1: NPPES name (checked FIRST — short-circuits on approve)
    # ================================================================
    nppes_score: Optional[float] = None
    if nppes is not None and getattr(nppes, "fetch_status", None) == "ok":
        n_first_raw = (nppes.first_name or "").strip()
        n_last_raw = (nppes.last_name or "").strip()
        if n_first_raw or n_last_raw:
            n_first, n_last = _clean_pair(n_first_raw, n_last_raw)
            nppes_score = _name_pair_score(n_first, n_last, board_first, board_last)

            # Also check NPPES other_names (maiden name, alias, etc.)
            for other in (getattr(nppes, "other_names", None) or []):
                if not isinstance(other, dict):
                    continue
                o_first_raw = (other.get("first_name") or "").strip()
                o_last_raw = (other.get("last_name") or "").strip()
                if o_first_raw or o_last_raw:
                    o_first, o_last = _clean_pair(o_first_raw, o_last_raw)
                    other_score = _name_pair_score(o_first, o_last, board_first, board_last)
                    if other_score > (nppes_score or 0.0):
                        nppes_score = other_score

            # Short-circuit: NPPES alone clears the threshold
            if nppes_score is not None and nppes_score >= approve_threshold:
                return NameGateResult(
                    epdb_score=None,
                    nppes_score=round(nppes_score, 4),
                    max_score=round(nppes_score, 4),
                    verdict="approve",
                )

    # ================================================================
    # Step 2: EPDB name (from master_row)
    # ================================================================
    epdb_first_raw = (master_row.get("first_name") or "").strip()
    epdb_last_raw = (master_row.get("last_name") or "").strip()
    epdb_first, epdb_last = _clean_pair(epdb_first_raw, epdb_last_raw)

    # Skip gate when EPDB name already exactly matches the cleaned board name
    if (epdb_first == board_first and epdb_last == board_last
            and epdb_first and epdb_last):
        return NameGateResult(
            epdb_score=1.0,
            nppes_score=round(nppes_score, 4) if nppes_score is not None else None,
            max_score=1.0,
            verdict="approve",
            skipped=True,
        )

    epdb_score = _name_pair_score(epdb_first, epdb_last, board_first, board_last)

    # ================================================================
    # Determine verdict from max of available scores
    # ================================================================
    scores = [s for s in [nppes_score, epdb_score] if s is not None]
    max_score = max(scores) if scores else 0.0

    if max_score >= approve_threshold:
        verdict = "approve"
    elif max_score >= ai_band_low:
        verdict = "ai_review"
    else:
        verdict = "manual"

    return NameGateResult(
        epdb_score=round(epdb_score, 4),
        nppes_score=round(nppes_score, 4) if nppes_score is not None else None,
        max_score=round(max_score, 4),
        verdict=verdict,
    )
