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
from datetime import date
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from engine.models import BoardUnavailableError, LicenseStatus, SearchQuery, SiteConfig

from . import capability, config as cfg
from . import disambiguator as disamb
from . import trace as trace_mod
from .nppes_client import NppesRecord, NpiDiscrepancy
from .trace import (
    AttemptRecord, RowTrace,
    OUTCOME_AMBIGUOUS, OUTCOME_BOARD_UNAVAILABLE, OUTCOME_ERROR, OUTCOME_LICENSE_MISMATCH,
    OUTCOME_MATCH_EXACT, OUTCOME_MATCH_VIA_DISAMBIGUATOR, OUTCOME_NAME_MISMATCH,
    OUTCOME_NAME_MATCH_NO_LICENSE,
    OUTCOME_NARROWED, OUTCOME_NO_RECORDS, OUTCOME_PROVIDER_TYPE_MISMATCH,
    OUTCOME_SKIPPED_DUPLICATE,
    REASON_AMBIGUOUS_AFTER_NARROWING, REASON_BOARD_UNAVAILABLE, REASON_LICENSE_MISMATCH,
    REASON_NAME_MATCH_NO_LICENSE,
    REASON_NAME_MISMATCH, REASON_NO_RECORDS, REASON_PROVIDER_TYPE_MISMATCH,
    make_signature, normalize_query_value,
)

log = logging.getLogger(__name__)

# (source_id, prov_type) pairs where provider-type comparison is suppressed.
# WY_PHYSICIAN is the board used for WY PH: it only returns MD/DO license types,
# so a PH prov_type would always mismatch — skip the check entirely for this combo.
# CO_DORA / CP: CO_DORA stores LPC license types but EPDB sometimes carries prov_type=CP
# for the same person; the license-number match is authoritative, so skip the type gate.
_SKIP_PROV_TYPE_CHECK: frozenset[tuple[str, str]] = frozenset({
    ("WY_PHYSICIAN", "PH"),
    ("CO_DORA", "CP"),
})


# A SearchExecutor is the function that actually runs ONE query against ONE
# board and returns a list of records. It's injected by the caller (psv_test)
# so the ladder doesn't need to know about Playwright/PsvBrowser/dispatcher
# internals.
SearchExecutor = Callable[[SiteConfig, SearchQuery, str], Awaitable[list]]

# Modes that search by name only (no license number submitted to the board).
# The "name_match_no_license" guard only fires for these — when the board found
# a record via a license-number search, that's already implicit confirmation.
_NAME_MODES: frozenset[str] = frozenset({
    "first_name", "last_name", "last_name_last_word", "first_and_last", "first_and_last_typed",
})


@dataclass
class PlannedAttempt:
    mode: str
    query: SearchQuery
    normalized_query: str
    driving_field: Optional[str] = None  # set for NPPES retry rungs


def _is_temp_permit(license_id: str) -> bool:
    """True when the license ID is a known internal tracking code that will never
    match the board's permanent license number.

    KY Medical Board: "TP" prefix (e.g. TP318, TP768) = Kentucky Temporary Permit.
    KY Physician Assistant: "TC" prefix (e.g. TC145, TC165) = Temporary Certification.
    KY Surgical Anesthetist: "TSA" prefix (e.g. TSA083) = Temporary SA permit.
    The board stores permanent license numbers (e.g. PA3822, SA464, 59975); these
    tracking codes only appear in internal client records, not the public database.
    For these, name-match alone is sufficient to verify — no license-numerics check.
    """
    if not license_id:
        return False
    upper = license_id.upper()
    # TP### — KY Temporary Physician
    if upper.startswith("TP") and upper[2:].isdigit():
        return True
    # TC### or TCO### — KY Temporary PA Certification (O vs 0 OCR variant included)
    if upper.startswith("TC") and len(upper) > 2:
        suffix = upper[2:]
        if suffix.isdigit() or (suffix.startswith("O") and suffix[1:].isdigit()):
            return True
    # TSA### — KY Temporary Surgical Anesthetist
    if upper.startswith("TSA") and upper[3:].isdigit():
        return True
    return False


def _apply_dash_format(digits: str, fmt: str) -> str:
    """Apply dash-format spec to a digit string.

    fmt is "N1-N2-N3..." where Ni are group lengths summing to len(digits).
    Example: _apply_dash_format("5383371052", "2-5-3") → "53-83371-052".
    Returns empty string if digits length doesn't match the spec.
    """
    try:
        groups = [int(n) for n in fmt.split("-")]
    except ValueError:
        return ""
    if sum(groups) != len(digits):
        return ""
    parts, pos = [], 0
    for g in groups:
        parts.append(digits[pos: pos + g])
        pos += g
    return "-".join(parts)


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
    if mode in ("license_number", "license_number_exact"):
        query_str = lic or ""
    elif mode == "license_numeric_only":
        _digits = re.sub(r"\D", "", lic or "")
        query_str = _digits.lstrip("0") or _digits
    elif mode in ("license_formatted", "license_middle_group"):
        # query_str is set by caller via override_fields["license_id"] already reformatted
        query_str = lic or ""
    elif mode == "first_name":
        query_str = first or ""
    elif mode == "last_name":
        query_str = last or ""
    elif mode == "last_name_last_word":
        query_str = (last or "").rsplit(" ", 1)[-1]
    elif mode in ("first_and_last", "first_and_last_typed"):
        query_str = f"{first} {last}".strip() if first and last else (last or first or "")
    elif mode == "license_and_last":
        query_str = f"{lic}+{last}" if lic and last else (lic or last or "")
    elif mode == "license_and_first":
        query_str = f"{lic}+{first}" if lic and first else (lic or first or "")
    elif mode in ("license_first_last", "license_first_mid_last"):
        parts = [p for p in (lic, first, last) if p]
        query_str = "+".join(parts) if parts else ""
    else:
        query_str = lic or last or first or ""

    # Synthetic modes — engine sees canonical mode name
    if mode in ("license_numeric_only", "license_formatted", "license_middle_group",
                "license_number_exact"):
        actual_mode = "license_number"
    elif mode == "first_and_last_typed":
        actual_mode = "first_and_last"
    elif mode == "last_name_last_word":
        actual_mode = "last_name"
    else:
        actual_mode = mode

    # license_numeric_only also overrides license_number on the structured field.
    # Strip leading zeros to match boards that store numbers without them (e.g. "993819" not "0993819").
    # Consistent with normalize_query_value which already strips leading zeros for signatures.
    if mode == "license_numeric_only":
        _d = re.sub(r"\D", "", lic or "")
        license_number = _d.lstrip("0") or _d or None
    else:
        license_number = lic

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

        # Temp-permit licenses (e.g. TP318) must NOT be stripped to bare digits ("318")
        # for license_numeric_only — that produces hundreds of unrelated hits on large boards
        # (e.g. KY_MEDBOARD returns 73 records for "318"), which contaminates the weight profile
        # and makes the disambiguator treat this as a license-present context.
        if mode == "license_numeric_only" and _is_temp_permit(
            master_row.get("license_id") or ""
        ):
            continue

        # last_name_last_word only adds value when the last name is compound (has a space).
        # Skip it when the last token equals the full last name (no transformation needed).
        if mode == "last_name_last_word":
            ln = (master_row.get("last_name") or "").strip()
            if " " not in ln:
                continue

        # License-number rungs identify the record by number alone; adding a
        # board-level type filter causes misses when the input prov_type differs
        # from the actual license_type on the board (e.g. PN input but APRN on
        # board after a credential upgrade).  Only apply the type filter on
        # name-based rungs where it narrows an otherwise large result set.
        lt_for_mode = license_type if mode in _NAME_MODES else None
        query, norm = _build_query(mode, master_row, provider_type_override=pt_override,
                                   license_type_override=lt_for_mode)
        if not norm:
            continue
        # Skip if license_numeric_only would produce identical value to plain license_number
        # (avoids redundant sig — but loop guard would catch it anyway).
        key = (mode, norm)
        if key in seen_norms:
            continue
        seen_norms.add(key)
        plans.append(PlannedAttempt(mode=mode, query=query, normalized_query=norm))

    # Synthetic: license_separators_stripped — remove separators (-, space, ., /) while
    # KEEPING any alpha prefix, e.g. "S-8727" → "S8727". license_numeric_only strips the
    # meaningful "S" and can match a DIFFERENT sibling credential ("8727", often a
    # superseded/Transitioned term); this variant preserves the prefix so the correct
    # (active) record is found. Inserted before license_numeric_only so the prefixed
    # form is tried first.
    _sep_raw_lic = master_row.get("license_id") or ""
    _sep_stripped = re.sub(r"[-\s./]", "", _sep_raw_lic)
    _sep_numeric = re.sub(r"\D", "", _sep_raw_lic)
    if (_sep_stripped
            and _sep_stripped != _sep_raw_lic       # separators actually present
            and _sep_stripped != _sep_numeric        # has an alpha prefix (else numeric_only covers it)
            and "license_number" in capability.supported_modes(config)):
        _sq, _snorm = _build_query("license_number", master_row,
                                   {"license_id": _sep_stripped},
                                   license_type_override=None)
        _skey = ("license_number", _snorm)
        if _skey not in seen_norms:
            seen_norms.add(_skey)
            _num_idx = next((i for i, p in enumerate(plans)
                             if p.mode == "license_numeric_only"), None)
            _ins_at = _num_idx if _num_idx is not None else (
                max((i for i, p in enumerate(plans) if p.mode == "license_number"),
                    default=-1) + 1
            )
            plans.insert(_ins_at, PlannedAttempt(mode="license_number", query=_sq,
                                                 normalized_query=_snorm))

    # Rung 0 — exact leading-zero search.
    # When the input license_id starts with "0" (e.g. "01486"), insert an attempt at the
    # very front of the plan that searches with the raw value before any normalization rung
    # strips the leading zeros.  The normalized_query is set to the raw lic (not passed
    # through normalize_query_value which would strip the zero) so the dedup key is unique:
    #   ("license_number_exact", "01486")  ≠  ("license_number", "1486")
    # Both rungs will fire; if the board finds the leading-zero form first the ladder stops.
    _r0_lic = master_row.get("license_id") or ""
    if (
        _r0_lic.startswith("0")
        and len(_r0_lic) > 1
        and "license_number" in capability.supported_modes(config)
    ):
        _r0_query, _ = _build_query(
            "license_number_exact", master_row, license_type_override=license_type
        )
        _r0_key = ("license_number_exact", _r0_lic)
        if _r0_key not in seen_norms:
            seen_norms.add(_r0_key)
            plans.insert(
                0,
                PlannedAttempt(
                    mode="license_number_exact",
                    query=_r0_query,
                    normalized_query=_r0_lic,
                ),
            )

    # Synthetic: license_formatted — try dashed format when board specifies license_dash_format
    # and the raw license is all-digits (e.g. "5383371052" → "53-83371-052" for KSBN).
    # Inserted right after license_numeric_only (before name-based modes) so that all
    # license attempts precede the name fallback.
    dash_fmt = getattr(config.search, "license_dash_format", None)
    if dash_fmt and "license_number" in capability.supported_modes(config):
        raw_lic = master_row.get("license_id") or ""
        digits = re.sub(r"\D", "", raw_lic)
        if digits and digits == raw_lic:  # only for pure-digit inputs (no prefix/dashes already)
            formatted = _apply_dash_format(digits, dash_fmt)
            if formatted and formatted != raw_lic:
                override = {"license_id": formatted}
                fq, fnorm = _build_query("license_formatted", master_row, override,
                                         license_type_override=license_type)
                fkey = ("license_formatted", fnorm)
                if fkey not in seen_norms:
                    seen_norms.add(fkey)
                    # Insert after the last license mode, before the first name mode
                    last_lic_idx = max(
                        (i for i, p in enumerate(plans)
                         if p.mode in ("license_number", "license_numeric_only")),
                        default=-1,
                    )
                    plans.insert(last_lic_idx + 1,
                                 PlannedAttempt(mode="license_formatted", query=fq,
                                                normalized_query=fnorm))

    # Synthetic: license_formatted — try prefix-dash format when board specifies
    # license_prefix_dash and the raw license matches ^([A-Za-z]+)(\d+)$
    # (e.g. "L301745" → "L-301745" for IBCLC_COMMISSION).
    # Also handles pure-numeric inputs (e.g. "288572" → "L-288572") when the board
    # is known to use L-XXXXXX style credentials (license_prefix_dash implies "L-" prefix).
    _LICENSE_BEARING_MODES = {"license_number", "license_numeric_only", "license_and_last"}
    if getattr(config.search, "license_prefix_dash", False) and \
            _LICENSE_BEARING_MODES & capability.supported_modes(config):
        raw_lic = master_row.get("license_id") or ""
        m_alpha = re.match(r'^([A-Za-z]+)(\d+)$', raw_lic)
        m_digit = re.match(r'^\d+$', raw_lic)
        _fmt_candidates = []
        if m_alpha:
            _fmt_candidates.append(f"{m_alpha.group(1)}-{m_alpha.group(2)}")
        elif m_digit:
            # Pure-numeric input on a prefix-dash board → try "L-{digits}"
            _fmt_candidates.append(f"L-{raw_lic}")
        for formatted in _fmt_candidates:
            if formatted != raw_lic:
                override = {"license_id": formatted}
                fq, fnorm = _build_query("license_formatted", master_row, override,
                                         license_type_override=license_type)
                fkey = ("license_formatted", fnorm)
                if fkey not in seen_norms:
                    seen_norms.add(fkey)
                    last_lic_idx = max(
                        (i for i, p in enumerate(plans)
                         if p.mode in ("license_number", "license_numeric_only",
                                       "license_and_last")),
                        default=-1,
                    )
                    plans.insert(last_lic_idx + 1,
                                 PlannedAttempt(mode="license_formatted", query=fq,
                                                normalized_query=fnorm))

    # Synthetic: license_digit_pad — zero-pad the digit portion to N digits when the board
    # config specifies license_digit_pad (e.g. MD_PHYSICIANS: letter + exactly 7 digits,
    # VA_DHP: pure digits padded to exactly 10).
    # Handles three cases:
    #   1. Input already has a letter prefix: "D90369" → "D0090369"  (pad=7)
    #   2. Pure-digit input with license_digit_prefixes: "90369" + prefix "D" → "D0090369"
    #   3. Pure-digit input, no prefix: "12345" → "0000012345"  (pad=10, VA_DHP)
    digit_pad = getattr(config.search, "license_digit_pad", None)
    if digit_pad and "license_number" in capability.supported_modes(config):
        raw_lic_dp = master_row.get("license_id") or ""
        m_alpha = re.match(r'^([A-Za-z]+)(\d+)$', raw_lic_dp)
        digit_pfxs = getattr(config.search, "license_digit_prefixes", None) or []

        dp_candidates: list[str] = []
        if m_alpha and len(m_alpha.group(2)) < digit_pad:
            # Case 1: letter-prefixed input, pad digit portion
            dp_candidates.append(f"{m_alpha.group(1)}{m_alpha.group(2).zfill(digit_pad)}")
        elif re.match(r'^\d+$', raw_lic_dp) and digit_pfxs:
            # Case 2: pure-digit input — attach each configured prefix + pad
            for pfx in digit_pfxs:
                dp_candidates.append(f"{pfx}{raw_lic_dp.zfill(digit_pad)}")
        elif re.match(r'^\d+$', raw_lic_dp) and len(raw_lic_dp) < digit_pad:
            # Case 3: pure-digit input, no prefix — zero-pad to digit_pad digits.
            # Used by VA_DHP where all license numbers are exactly 10 digits.
            dp_candidates.append(raw_lic_dp.zfill(digit_pad))
        else:
            # Case 4: mixed-format input with separators (e.g. "CDRH.0071196").
            # Extract the digit run, apply prefix+pad combos the same as Case 2.
            # Handles boards like MD_PHYSICIANS where the input may carry a foreign
            # prefix (FDA CDRH ID) but the state license digits are still present.
            _dp_digits = re.sub(r'\D', '', raw_lic_dp)
            if _dp_digits and digit_pfxs and len(_dp_digits) <= digit_pad:
                for pfx in digit_pfxs:
                    dp_candidates.append(f"{pfx}{_dp_digits.zfill(digit_pad)}")

        for dp_fmt in dp_candidates:
            if dp_fmt == raw_lic_dp:
                continue
            override_dp = {"license_id": dp_fmt}
            dpq, dpnorm = _build_query("license_formatted", master_row, override_dp,
                                       license_type_override=license_type)
            dpkey = ("license_formatted", dpnorm)
            if dpkey not in seen_norms:
                seen_norms.add(dpkey)
                last_lic_idx_dp = max(
                    (i for i, p in enumerate(plans)
                     if p.mode in ("license_number", "license_numeric_only",
                                   "license_formatted")),
                    default=-1,
                )
                plans.insert(last_lic_idx_dp + 1,
                             PlannedAttempt(mode="license_formatted", query=dpq,
                                            normalized_query=dpnorm))

    # Synthetic: license_middle_group — for boards with license_dash_format (3-group)
    # and a pre-dashed input (e.g. "13-86228-111", "53-83739-032", "14-138727-052"),
    # extract the center segment as the search key ("86228", "83739", "138727").
    # Splits on "-" directly so any middle-group width is handled (5-digit, 6-digit, etc.).
    # Handles KSBN inputs where the DB prefix varies (53-, 13-, 14-) but the board only
    # indexes the bare center digits.
    if dash_fmt and "license_number" in capability.supported_modes(config):
        raw_lic_mg = master_row.get("license_id") or ""
        parts_mg = raw_lic_mg.split("-")
        if len(parts_mg) == 3:
            middle_mg = parts_mg[1].strip()
            lic_digits_mg = re.sub(r"\D", "", raw_lic_mg)
            if middle_mg and middle_mg not in (raw_lic_mg, lic_digits_mg):
                override_mg = {"license_id": middle_mg}
                mq, mnorm = _build_query("license_middle_group", master_row,
                                          override_mg,
                                          license_type_override=license_type)
                mkey = ("license_middle_group", mnorm)
                if mkey not in seen_norms:
                    seen_norms.add(mkey)
                    last_lic_idx_mg = max(
                        (i for i, p in enumerate(plans)
                         if p.mode in ("license_number", "license_numeric_only",
                                       "license_formatted")),
                        default=-1,
                    )
                    plans.insert(last_lic_idx_mg + 1,
                                 PlannedAttempt(mode="license_middle_group",
                                                query=mq,
                                                normalized_query=mnorm))

    # Synthetic: license_middle_group from unsegmented long-digit input.
    # When a board uses an N-digit dash format (e.g. "2-5-3" = 10 digits) but the raw
    # license has N+1 pure digits (e.g. 11 digits), treat it as having a wider middle
    # group (prefix and suffix widths stay the same, middle absorbs the extra digit).
    # Example: dash_fmt="2-5-3", input="12345678123" (11 digits) →
    #   prefix=2, suffix=3, middle=digits[2:8]="345678" → search "345678".
    # This covers KSBN where some records carry an 11-digit storage key but the board
    # only indexes the 6-digit center segment.
    if dash_fmt and "license_number" in capability.supported_modes(config):
        raw_lic_long = master_row.get("license_id") or ""
        digits_long = re.sub(r"\D", "", raw_lic_long)
        try:
            _dash_groups = [int(n) for n in dash_fmt.split("-")]
        except ValueError:
            _dash_groups = []
        if (
            raw_lic_long == digits_long          # pure-digit input (no dashes/letters)
            and len(_dash_groups) == 3           # 3-group format configured
            and len(digits_long) == sum(_dash_groups) + 1  # exactly one digit longer
        ):
            _prefix_len = _dash_groups[0]
            _suffix_len = _dash_groups[-1]
            middle_long = digits_long[_prefix_len: len(digits_long) - _suffix_len]
            if middle_long:
                override_long = {"license_id": middle_long}
                mq_long, mnorm_long = _build_query("license_middle_group", master_row,
                                                    override_long,
                                                    license_type_override=license_type)
                mkey_long = ("license_middle_group", mnorm_long)
                if mkey_long not in seen_norms:
                    seen_norms.add(mkey_long)
                    last_lic_idx_long = max(
                        (i for i, p in enumerate(plans)
                         if p.mode in ("license_number", "license_numeric_only",
                                       "license_formatted")),
                        default=-1,
                    )
                    plans.insert(last_lic_idx_long + 1,
                                 PlannedAttempt(mode="license_middle_group",
                                                query=mq_long,
                                                normalized_query=mnorm_long))

    return plans


def build_targeted_retry_plan(config: SiteConfig, master_row: dict,
                              nppes: NppesRecord,
                              discrepancy: NpiDiscrepancy,
                              license_type: Optional[str] = None,
                              ) -> list[PlannedAttempt]:
    """Build NPPES retry rungs — only test the fields that differ."""
    plans: list[PlannedAttempt] = []
    _seen_norms: set[tuple[str, str]] = set()   # (mode, normalized_query) dedup
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

    # Extra NPPES licenses: try all license-based search modes the board supports.
    # Some boards (e.g. IBCLC_COMMISSION) don't expose a bare 'license_number' mode
    # but do support 'license_and_last' or 'license_formatted' — use those instead.
    _lic_retry_modes = [m for m in ("license_number", "license_and_last", "license_formatted")
                        if m in caps]

    def _add_plan(mode: str, sq: str, norm: str, field: str) -> None:
        key = (mode, norm)
        if key in _seen_norms:
            return
        _seen_norms.add(key)
        plans.append(PlannedAttempt(mode=mode, query=sq, normalized_query=norm,
                                     driving_field=field))

    for _mode in _lic_retry_modes:
        for lic_entry in discrepancy.extra_nppes_licenses[:5]:  # cap at 5 to bound work
            num = (lic_entry.get("number") or "").strip()
            if not num:
                continue
            # Guard: combo modes need non-license fields present in master_row;
            # without them _build_query degrades to a plain license search (duplicate).
            _extra_required = [f for f in capability.required_fields_for(_mode) if f != "license_id"]
            if not all(master_row.get(f) for f in _extra_required):
                continue
            override = {"license_id": num}
            sq, norm = _build_query(_mode, master_row, override)
            _add_plan(_mode, sq, norm, "license_number")
            # Also try numeric-only form (license_number mode only)
            if _mode == "license_number":
                num_only = re.sub(r"\D", "", num)
                if num_only and num_only != num:
                    override2 = {"license_id": num_only}
                    sq2, norm2 = _build_query(_mode, master_row, override2)
                    _add_plan(_mode, sq2, norm2, "license_number")

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
    # Use a _detail_expiry suffix so this secondary call doesn't collide with
    # the main license_number signature already recorded by the first search pass.
    # The first pass returned records from the results table (no expiry); this call
    # specifically triggers detail-page visits to pick up the expiration_date.
    detail_sig = make_signature(cfg_obj.identity.source_id, "license_number_detail_expiry", board_lic)
    if trace.has_signature(detail_sig):
        return record
    # Include first/last name from the matched record so PsvBrowser.search can
    # narrow to the specific row rather than visiting every detail page in the set.
    board_fn = (getattr(record, "licensee_first_name", None) or "").strip() or None
    board_ln = (getattr(record, "licensee_last_name", None) or "").strip() or None
    # Some boards (e.g. AR_ABESLPA) store full_name without separate first/last fields.
    # Capture it so _name_ok can still filter when board_fn/board_ln are unavailable.
    board_full = (getattr(record, "licensee_full_name", None) or "").strip().upper() or None
    # Prefer license_and_last when we have a last name and the board supports it:
    # narrows results to the specific person rather than everyone sharing the
    # numeric license prefix (e.g. SP#139 search returns SP#1390..SP#1399 too).
    _cfg_modes = {m.mode for m in (cfg_obj.search.modes or [])}
    _detail_mode = (
        "license_and_last"
        if (board_ln and "license_and_last" in _cfg_modes)
        else "license_number"
    )
    q = SearchQuery(
        mode=_detail_mode, query=board_lic, license_number=board_lic,
        first_name=board_fn, last_name=board_ln,
    )
    try:
        detail_records = await asyncio.wait_for(
            executor(cfg_obj, q, trace.run_id), timeout=float(timeout_s),
        )
        trace.seen_signatures.add(detail_sig)

        def _name_ok(dr) -> bool:
            # When the original record has a known name, only accept a detail record
            # that matches it — boards like KS_GLSUITE reuse the same numeric license
            # number across license types, so the re-fetch can return many people with
            # the same number and we must not swap in the wrong one.
            # Only reject on name mismatch when the detail record actually has a name.
            # If heading extraction fails (e.g. KSBHADA h3 with embedded link text),
            # the detail record has empty first/last — don't discard it when the board
            # license number already uniquely identifies the record.
            if board_fn or board_ln:
                dr_fn = (getattr(dr, "licensee_first_name", None) or "").strip().upper()
                dr_ln = (getattr(dr, "licensee_last_name", None) or "").strip().upper()
                if not (dr_fn or dr_ln):
                    return True
                return (dr_fn == (board_fn or "").upper()
                        and dr_ln == (board_ln or "").upper())
            if board_full:
                # Board uses full_name (no separate first/last). Filter by token overlap
                # so "Montgomery, Janet" rejects "Welch-Jackson, Leann" sharing only "A#139".
                dr_full = (getattr(dr, "licensee_full_name", None) or "").strip().upper()
                dr_fn2 = (getattr(dr, "licensee_first_name", None) or "").strip().upper()
                dr_ln2 = (getattr(dr, "licensee_last_name", None) or "").strip().upper()
                dr_any = dr_full or f"{dr_fn2} {dr_ln2}".strip()
                if not dr_any:
                    return True
                board_tokens = set(board_full.replace(",", " ").split())
                detail_tokens = set(dr_any.replace(",", " ").split())
                return bool(board_tokens & detail_tokens)
            return True

        # First pass: prefer a detail record whose license number EXACTLY matches the
        # one we re-searched. Boards that return several rows for the same person (e.g.
        # the associate "A17686" alongside the full "17686") share a name, so a
        # name-only match is ambiguous and could pick the wrong row's (stale) expiry.
        board_lic_k = board_lic.upper().strip()
        for dr in detail_records:
            if ((getattr(dr, "license_number", "") or "").upper().strip() == board_lic_k
                    and getattr(dr, "expiration_date", None) is not None
                    and _name_ok(dr)):
                return dr
        # Second pass: fall back to any name-matching record that carries an expiry.
        for dr in detail_records:
            if getattr(dr, "expiration_date", None) is not None and _name_ok(dr):
                return dr
    except Exception as exc:
        log.debug("[%s] detail expiry re-fetch failed for '%s': %s",
                  cfg_obj.identity.source_id, board_lic, exc)
    return record


# --------------------------------------------------------------------------
# The driver
# --------------------------------------------------------------------------

def _out_of_state_reason(record: Any) -> Optional[str]:
    """Return 'out_of_state:<STATE>' when the record was verified via the Out of State tab."""
    state = getattr(record, "out_of_state_state", None)
    return f"out_of_state:{state}" if state else None


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
    # Soft-match fallback: a name-only Pass with license_numerics=0.0 when
    # more boards remain.  We store it and keep searching for an exact-license
    # hit on a later board; if nothing better is found, we return this.
    soft_match: Optional[LadderResult] = None
    # True when the stored soft_match came from the PRIMARY (first-routed) board. Used to
    # accept a strong name identity on the primary board when the queried license belongs
    # to a different board that couldn't confirm it (e.g. NC_DENTAL finds the provider by
    # name while the physician licence lives on the captcha-blocked NC_MEDBOARD).
    soft_match_primary: bool = False
    # Deferred fail: name was found but license didn't match.  We defer the
    # return so the NPPES retry section can try the correct credential number
    # (e.g. IBCLC input has state-level ID; NPPES has the real L-XXXXXX).
    deferred_fail: Optional[LadderResult] = None
    # Expired NPPES fallback: an NPPES-substituted license+name match whose record is
    # already expired (or non-active). We don't accept it immediately — a later retry
    # plan (e.g. the numeric-only "17686" after the associate "A17686") may find the
    # ACTIVE record. If none does, we return this so the row reports a clean expired
    # Fail rather than escalating to AI.
    expired_nppes_fallback: Optional[LadderResult] = None
    # Same idea for the master ladder (Loop 1): a license search can match a superseded
    # sibling credential when the input license was degraded to bare digits (e.g. input
    # "S-8727" → numeric-only "8727" matches the Transitioned "8727" while the ACTIVE
    # record is "S8727"). Hold the superseded match and prefer an active one from a later
    # rung; return this only if no active record is found anywhere.
    transitioned_fallback: Optional[LadderResult] = None
    # Fallback for exact-license-match ambiguous cases: preserves the best board
    # candidate so the expiry date is available for AIAddLicense routing if AI gives up.
    _license_exact_fallback: Optional[LadderResult] = None
    _stop_boards = False

    blm = board_license_type_map or {}

    # ===================== Loop 1: master ladder over boards =====================
    for board_idx, cfg_obj in enumerate(routed_configs):
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

            # Board site is down/erroring — stop trying further rungs on it (they
            # would all time out too) and move to the next routed board. Preserve
            # the board_unavailable outcome (don't let the no_gate_pass branch
            # below overwrite it with no_records).
            if attempt.outcome == OUTCOME_BOARD_UNAVAILABLE:
                break

            verdict = await _evaluate_records(records, master_row, trace,
                                              current_mode=plan.mode)
            attempt.confidence = verdict.best_breakdown.total if verdict.best_breakdown else None
            attempt.weight_profile_used = verdict.best_breakdown.weight_profile if verdict.best_breakdown else None

            if verdict.status == "selected":
                best = verdict.best
                bd = verdict.best_breakdown
                lic_numerics = bd.license_numerics if bd else 1.0
                has_lic_id = bool(master_row.get("license_id"))
                is_last_board = (board_idx == len(routed_configs) - 1)
                # If name-only match with zero license score and more boards remain,
                # store as a soft fallback and continue searching for an exact hit.
                # Only defer name-mode searches — license-mode hits confirm via the
                # search itself even when the board omits license_number from results.
                if has_lic_id and lic_numerics == 0.0 and not is_last_board \
                        and plan.mode in _NAME_MODES:
                    if soft_match is None:
                        if getattr(best, "expiration_date", None) is None:
                            best = await _fetch_detail_record(
                                cfg_obj, best, trace, executor, timeout_s,
                            )
                        soft_match = LadderResult(
                            status="Pass",
                            best_record=best,
                            best_breakdown=bd,
                            tiebreaker_used=verdict.tiebreaker_used,
                            weight_profile_used=bd.weight_profile,
                        )
                        soft_match_primary = (board_idx == 0)
                    attempt.candidates = [best]
                    attempt.outcome = OUTCOME_MATCH_EXACT
                    break  # stop rungs on this board; continue to next board
                if getattr(best, "expiration_date", None) is None:
                    best = await _fetch_detail_record(
                        cfg_obj, best, trace, executor, timeout_s,
                    )
                # If the input has a license but scoring couldn't confirm it
                # (lic_numerics == 0.0), do a final check on the (possibly
                # detail-enriched) record before accepting the name-only match.
                # Re-check covers boards that only expose license_number on the
                # detail page. Only fail when the detail record also has a
                # license that doesn't match — an absent board license stays Fail.
                # Skip for license-mode searches: the board confirmed the match
                # by returning the record in response to a license query.
                _name_only_rescore = False
                if has_lic_id and lic_numerics == 0.0 and plan.mode in _NAME_MODES:
                    _detail_lic = (getattr(best, "license_number", "") or "").strip()
                    _input_lic = (master_row.get("license_id") or "").strip()
                    # Temp/internal tracking codes (TC, TP, TSA prefix) never appear
                    # on the board — name match alone is sufficient for these.
                    # Only escalate when the board returns a license number that
                    # conflicts with the input. When the board exposes NO license at
                    # all (_detail_lic is empty), accept the name match — the board
                    # simply doesn't surface license numbers on its results table.
                    # Also accept when the name-only verdict is high-confidence
                    # (gate_passed=True AND score >= name_only threshold): different
                    # boards may use different license numbering systems; the identity
                    # is confirmed by name. Output_emitter routes these to AIAddLicense.
                    _name_high_conf = (
                        bd.gate_passed and bd.total >= cfg.THRESHOLD_NAME_PROFILE
                    )
                    # Fallback: when the board returns a different license format but
                    # the name + provider type match is perfect (license_numerics==0.0
                    # because the board uses its own numbering), re-score with name_only
                    # weights. Rows 0007/0191: board has different license; name score
                    # with license_present profile is 0.65 (below threshold), but
                    # name_only score is 1.0 — accept as high-confidence name match.
                    # Set _name_only_rescore so the breakdown gets updated before
                    # return — output_emitter step 1.7 checks total against 0.70,
                    # and we need the name_only total (1.0) not the license_present
                    # total (0.65) so step 5b routes to AIAddLicense instead of Manual.
                    if not _name_high_conf and bd.gate_passed and bd.license_numerics == 0.0:
                        _name_only_total = (
                            bd.first_name * 0.40 + bd.last_name * 0.30
                            + bd.provider_type * 0.25 + bd.state * 0.05
                        )
                        if _name_only_total >= cfg.THRESHOLD_NAME_PROFILE:
                            _name_high_conf = True
                            _name_only_rescore = True
                    if (not _is_temp_permit(_input_lic)
                            and _detail_lic
                            and not disamb.license_numerics_match(_input_lic, _detail_lic)
                            and not _name_high_conf):
                        attempt.outcome = OUTCOME_NAME_MATCH_NO_LICENSE
                        attempt.candidates = verdict.gate_passers[:10]
                        trace.escalate_to_ai_reason = REASON_NAME_MATCH_NO_LICENSE
                        _stop_boards = True
                        break  # break rung loop; outer board loop checks _stop_boards
                if _name_only_rescore:
                    bd = disamb.score_candidate(best, master_row, weight_profile="name_only")
                # Active-preference deferral: when the matched record is non-active/expired
                # AND its number differs from the input (after stripping separators), it is
                # a superseded sibling matched via digit-only degradation — e.g. input
                # "S-8727" degraded to "8727" hit the Transitioned "8727" while the ACTIVE
                # record is "S8727". Don't accept it yet; a later rung (the separator-
                # stripped or name search) may surface the active record. Preserve it as a
                # fallback so a genuinely-superseded-only licence still reports.
                _b_status = getattr(best, "status", None)
                _b_exp = getattr(best, "expiration_date", None)
                _b_nonactive = (
                    _b_status in (LicenseStatus.INACTIVE, LicenseStatus.EXPIRED,
                                  LicenseStatus.REVOKED, LicenseStatus.SUSPENDED)
                    or bool(_b_exp is not None and _b_exp < date.today())
                )
                _in_lic_ss = re.sub(r"[-\s./]", "", (master_row.get("license_id") or "")).upper()
                _b_lic_ss = re.sub(r"[-\s./]", "", (getattr(best, "license_number", "") or "")).upper()
                if (_b_nonactive and has_lic_id and _in_lic_ss and _b_lic_ss
                        and _b_lic_ss != _in_lic_ss):
                    if transitioned_fallback is None:
                        transitioned_fallback = LadderResult(
                            status="Pass", best_record=best, best_breakdown=bd,
                            tiebreaker_used=verdict.tiebreaker_used,
                            weight_profile_used=bd.weight_profile,
                            reason=_out_of_state_reason(best),
                        )
                    attempt.outcome = _diagnose_failure_outcome(
                        [best], master_row, cfg_obj.identity.source_id
                    )
                    attempt.candidates = [best]
                    continue  # try next rung — a later one may find the active record
                attempt.outcome = OUTCOME_MATCH_EXACT
                trace.final_outcome = "Pass"
                return LadderResult(
                    status="Pass",
                    best_record=best,
                    best_breakdown=bd,
                    tiebreaker_used=verdict.tiebreaker_used,
                    weight_profile_used=bd.weight_profile,
                    reason=_out_of_state_reason(best),
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
                        weight_profile=_pick_profile(trace, master_row),
                    )
                    _nrw_lic_num = bd.license_numerics if bd else 1.0
                    _nrw_has_lic = bool(master_row.get("license_id"))
                    _nrw_name_only_rescore = False
                    if _nrw_has_lic and _nrw_lic_num == 0.0 and plan.mode in _NAME_MODES:
                        _nrw_detail_lic = (getattr(chosen, "license_number", "") or "").strip()
                        _nrw_input_lic = (master_row.get("license_id") or "").strip()
                        _nrw_high_conf = (
                            bd.gate_passed and bd.total >= cfg.THRESHOLD_NAME_PROFILE
                        )
                        if not _nrw_high_conf and bd.gate_passed and bd.license_numerics == 0.0:
                            _nrw_name_only_total = (
                                bd.first_name * 0.40 + bd.last_name * 0.30
                                + bd.provider_type * 0.25 + bd.state * 0.05
                            )
                            if _nrw_name_only_total >= cfg.THRESHOLD_NAME_PROFILE:
                                _nrw_high_conf = True
                                _nrw_name_only_rescore = True
                        if (not _is_temp_permit(_nrw_input_lic)
                                and _nrw_detail_lic
                                and not disamb.license_numerics_match(
                                    _nrw_input_lic, _nrw_detail_lic)
                                and not _nrw_high_conf):
                            attempt.outcome = OUTCOME_NAME_MATCH_NO_LICENSE
                            attempt.candidates = narrowed_pool[:10]
                            trace.escalate_to_ai_reason = REASON_NAME_MATCH_NO_LICENSE
                            _stop_boards = True
                            break  # break rung loop
                    if _nrw_name_only_rescore:
                        bd = disamb.score_candidate(chosen, master_row, weight_profile="name_only")
                    attempt.outcome = OUTCOME_MATCH_VIA_DISAMBIGUATOR
                    trace.final_outcome = "Pass"
                    return LadderResult(
                        status="Pass", best_record=chosen, best_breakdown=bd,
                        weight_profile_used=bd.weight_profile,
                        reason=_out_of_state_reason(chosen),
                    )
                # Still ambiguous → escalate to AI
                attempt.outcome = OUTCOME_AMBIGUOUS
                attempt.candidates = verdict.gate_passers[:10]
                trace.escalate_to_ai_reason = REASON_AMBIGUOUS_AFTER_NARROWING
                last_specific_reason = REASON_AMBIGUOUS_AFTER_NARROWING
                # When the top gate-passer has an exact license match, preserve it so
                # the expiry date is available for AIAddLicense routing if AI gives up.
                if _license_exact_fallback is None and verdict.gate_passers:
                    _top_gp = verdict.gate_passers[0]
                    _top_gp_bd = disamb.score_candidate(
                        _top_gp, master_row, weight_profile=_pick_profile(trace, master_row)
                    )
                    if _top_gp_bd.license_numerics == 1.0:
                        if getattr(_top_gp, "expiration_date", None) is None:
                            _top_gp = await _fetch_detail_record(
                                cfg_obj, _top_gp, trace, executor, timeout_s
                            )
                        _license_exact_fallback = LadderResult(
                            status="EscalateAi",
                            best_record=_top_gp,
                            best_breakdown=_top_gp_bd,
                            weight_profile_used=_top_gp_bd.weight_profile,
                        )
                # Fall through to next rung to give it another shot? No — per spec,
                # narrowing failure stops THIS board. Continue to next board.
                break

            if verdict.status == "no_gate_pass":
                if records:
                    attempt.outcome = _diagnose_failure_outcome(records, master_row, cfg_obj.identity.source_id)
                    last_specific_reason = _outcome_to_reason(attempt.outcome)
                    attempt.candidates = records[:10]
                else:
                    attempt.outcome = OUTCOME_NO_RECORDS
                # try next rung on this board

            if verdict.status == "ambiguous":
                attempt.outcome = OUTCOME_AMBIGUOUS
                attempt.candidates = records[:10]
                trace.escalate_to_ai_reason = REASON_AMBIGUOUS_AFTER_NARROWING
                last_specific_reason = REASON_AMBIGUOUS_AFTER_NARROWING
                # When license matched exactly, preserve best candidate so if AI gives up
                # the expiry is available for AIAddLicense routing (name-change case).
                if (_license_exact_fallback is None
                        and verdict.best_breakdown is not None
                        and verdict.best_breakdown.license_numerics == 1.0
                        and verdict.best is not None):
                    _fb = verdict.best
                    if getattr(_fb, "expiration_date", None) is None:
                        _fb = await _fetch_detail_record(
                            cfg_obj, _fb, trace, executor, timeout_s
                        )
                    _license_exact_fallback = LadderResult(
                        status="EscalateAi",
                        best_record=_fb,
                        best_breakdown=verdict.best_breakdown,
                        weight_profile_used=verdict.best_breakdown.weight_profile,
                    )
                break  # stop this board

        # end of rung loop for this board
        if trace.escalate_to_ai_reason or _stop_boards:
            break

    # If a soft-match (name-only, zero license score) was stored and no exact
    # license match was found on any subsequent board, do a final license check
    # on the detail-enriched record before accepting it.
    if soft_match is not None:
        _sm_input_lic = (master_row.get("license_id") or "").strip()
        if _sm_input_lic and not _is_temp_permit(_sm_input_lic):
            _sm_detail_lic = (getattr(soft_match.best_record, "license_number", "") or "").strip()
            _sm_bd = soft_match.best_breakdown
            _sm_high_conf = bool(
                _sm_bd is not None
                and _sm_bd.gate_passed
                and _sm_bd.total >= cfg.THRESHOLD_NAME_PROFILE
            )
            _sm_name_only_rescore = False
            if (not _sm_high_conf and _sm_bd is not None
                    and _sm_bd.gate_passed and _sm_bd.license_numerics == 0.0):
                _sm_name_only_total = (
                    _sm_bd.first_name * 0.40 + _sm_bd.last_name * 0.30
                    + _sm_bd.provider_type * 0.25 + _sm_bd.state * 0.05
                )
                if _sm_name_only_total >= cfg.THRESHOLD_NAME_PROFILE:
                    _sm_high_conf = True
                    _sm_name_only_rescore = True
            # Primary-board name identity: when the soft match came from the PRIMARY routed
            # board, the name identity is strong (first & last both >= 0.9), and the record
            # is ACTIVE, accept it even though the queried license number doesn't match this
            # board. This covers dual-credential providers whose queried licence lives on a
            # different board that couldn't confirm it — e.g. a physician licence on the
            # captcha-blocked NC_MEDBOARD while NC_DENTAL (primary) lists the provider as an
            # active dentist. The provider_type score is 0 (checked as one profession, found
            # as another), which alone would keep the name_only total below threshold.
            if (not _sm_high_conf and soft_match_primary and _sm_bd is not None
                    and _sm_bd.first_name >= 0.9 and _sm_bd.last_name >= 0.9
                    and getattr(soft_match.best_record, "status", None) == LicenseStatus.ACTIVE):
                _sm_high_conf = True
                _sm_name_only_rescore = True
            if (_sm_detail_lic
                    and not disamb.license_numerics_match(_sm_input_lic, _sm_detail_lic)
                    and not _sm_high_conf):
                trace.escalate_to_ai_reason = REASON_NAME_MATCH_NO_LICENSE
                return LadderResult(
                    status="EscalateAi",
                    best_breakdown=soft_match.best_breakdown,
                    reason=REASON_NAME_MATCH_NO_LICENSE,
                    weight_profile_used=soft_match.weight_profile_used,
                )
            if _sm_name_only_rescore and soft_match.best_record is not None:
                _sm_new_bd = disamb.score_candidate(
                    soft_match.best_record, master_row, weight_profile="name_only"
                )
                soft_match.best_breakdown = _sm_new_bd
                soft_match.weight_profile_used = "name_only"
        trace.final_outcome = "Pass"
        if soft_match.best_record and not soft_match.reason:
            soft_match.reason = _out_of_state_reason(soft_match.best_record)
        return soft_match

    # ===================== NPPES targeted retry =====================
    if nppes_record and discrepancy and not discrepancy.is_empty():
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
                verdict = await _evaluate_records(records, master_row, trace,
                                                  current_mode=plan.mode)
                attempt.confidence = verdict.best_breakdown.total if verdict.best_breakdown else None
                attempt.weight_profile_used = verdict.best_breakdown.weight_profile if verdict.best_breakdown else None

                if verdict.status == "selected":
                    best = verdict.best
                    _sel_bd = verdict.best_breakdown
                    # Guard: NPI retry is using a DIFFERENT license (license_numerics=0.0)
                    # but the input license was already found on the board in a prior attempt
                    # (ambiguous due to duplicate CSV rows, not a missing record).
                    # Substituting a different license here would show the wrong license as
                    # verified — escalate to AI instead so it can pick the right record.
                    if (plan.driving_field == "license_number"
                            and _sel_bd is not None and _sel_bd.license_numerics == 0.0):
                        _input_lic = (master_row.get("license_id") or "").strip()
                        if _input_lic and _input_license_found_in_prior_attempts(trace, _input_lic, master_row):
                            attempt.outcome = OUTCOME_AMBIGUOUS
                            attempt.candidates = [best] if best else []
                            trace.escalate_to_ai_reason = (
                                trace.escalate_to_ai_reason or trace_mod.REASON_AMBIGUOUS_AFTER_NARROWING
                            )
                            continue
                    # Enrich with the detail-page expiry BEFORE deciding acceptance:
                    # many boards only expose the expiration date on the detail page,
                    # and we must know whether this record is already expired before
                    # accepting it as a Pass.
                    if getattr(best, "expiration_date", None) is None:
                        best = await _fetch_detail_record(
                            cfg_obj, best, trace, executor, timeout_s,
                        )
                    # Don't accept a non-active OR already-expired NPPES-matched record
                    # yet — a later retry plan may find the active record. This is the
                    # associate→full transition case: NPPES carries the associate number
                    # "A17686" (LCMHC Associate, term ended 2025-06-30) whose record may
                    # still read "Active", while the numeric-only retry "17686" returns
                    # the current ACTIVE full licence (the disambiguator's active-over-
                    # transitioned tiebreaker then selects it). Checking the expiry DATE
                    # — not just the status label — is what catches the associate row,
                    # since its status field alone is not in the skip set below.
                    _nppes_rec_status = getattr(best, "status", None) if best else None
                    _best_exp = getattr(best, "expiration_date", None) if best else None
                    _best_expired = bool(_best_exp is not None and _best_exp < date.today())
                    if (_nppes_rec_status in (
                            LicenseStatus.INACTIVE, LicenseStatus.EXPIRED,
                            LicenseStatus.REVOKED, LicenseStatus.SUSPENDED,
                        ) or _best_expired):
                        attempt.outcome = _diagnose_failure_outcome(
                            [best], master_row, cfg_obj.identity.source_id
                        ) if best else OUTCOME_NO_RECORDS
                        attempt.candidates = [best] if best else []
                        # Remember the first expired/non-active NPPES match. If no active
                        # alternative surfaces on a later retry plan, we return this so
                        # the row reports a clean expired Fail instead of escalating to AI.
                        if best is not None and expired_nppes_fallback is None:
                            expired_nppes_fallback = LadderResult(
                                status="Pass", best_record=best,
                                best_breakdown=_sel_bd,
                                npi_substituted=True,
                                tiebreaker_used=verdict.tiebreaker_used,
                                weight_profile_used=(_sel_bd.weight_profile if _sel_bd else None),
                                reason=_out_of_state_reason(best),
                            )
                        continue
                    attempt.outcome = OUTCOME_MATCH_VIA_DISAMBIGUATOR
                    trace.final_outcome = "Pass"
                    return LadderResult(
                        status="Pass", best_record=best,
                        best_breakdown=verdict.best_breakdown,
                        npi_substituted=True,
                        tiebreaker_used=verdict.tiebreaker_used,
                        weight_profile_used=verdict.best_breakdown.weight_profile,
                        reason=_out_of_state_reason(best),
                    )
                if verdict.status == "ambiguous" and verdict.best_breakdown is not None:
                    # NPPES retry found a candidate that passed the gate but scored below
                    # threshold solely because the INPUT license ≠ board license (license_numerics=0).
                    # The board license was confirmed by the NPPES-guided search itself, so if
                    # first + last name match strongly, accept the record.
                    # Same guard as above: if the input license was already on the board,
                    # don't substitute a different license — escalate to AI.
                    _abd = verdict.best_breakdown
                    if _abd.first_name >= 0.85 and _abd.last_name >= 0.85:
                        if (plan.driving_field == "license_number" and _abd.license_numerics == 0.0):
                            _input_lic = (master_row.get("license_id") or "").strip()
                            if _input_lic and _input_license_found_in_prior_attempts(trace, _input_lic, master_row):
                                attempt.outcome = OUTCOME_AMBIGUOUS
                                attempt.candidates = verdict.gate_passers[:10]
                                trace.escalate_to_ai_reason = (
                                    trace.escalate_to_ai_reason or trace_mod.REASON_AMBIGUOUS_AFTER_NARROWING
                                )
                                continue
                        best = verdict.best
                        if getattr(best, "expiration_date", None) is None:
                            best = await _fetch_detail_record(
                                cfg_obj, best, trace, executor, timeout_s,
                            )
                        # Same expired/non-active guard as the "selected" branch: an
                        # already-expired NPPES-substituted record (e.g. the associate
                        # "A17686") is not accepted yet — a later retry plan (numeric-only
                        # "17686") may surface the ACTIVE full licence. Preserve it as a
                        # fallback so a genuinely-expired-only licence still reports cleanly.
                        _abd_status = getattr(best, "status", None) if best else None
                        _abd_exp = getattr(best, "expiration_date", None) if best else None
                        _abd_expired = bool(_abd_exp is not None and _abd_exp < date.today())
                        if (_abd_status in (
                                LicenseStatus.INACTIVE, LicenseStatus.EXPIRED,
                                LicenseStatus.REVOKED, LicenseStatus.SUSPENDED,
                            ) or _abd_expired):
                            attempt.outcome = _diagnose_failure_outcome(
                                [best], master_row, cfg_obj.identity.source_id
                            ) if best else OUTCOME_NO_RECORDS
                            attempt.candidates = [best] if best else []
                            if best is not None and expired_nppes_fallback is None:
                                expired_nppes_fallback = LadderResult(
                                    status="Pass", best_record=best,
                                    best_breakdown=_abd,
                                    npi_substituted=True,
                                    weight_profile_used=_abd.weight_profile,
                                    reason=_out_of_state_reason(best),
                                )
                            continue
                        attempt.outcome = OUTCOME_MATCH_VIA_DISAMBIGUATOR
                        trace.final_outcome = "Pass"
                        return LadderResult(
                            status="Pass", best_record=best,
                            best_breakdown=_abd,
                            npi_substituted=True,
                            weight_profile_used=_abd.weight_profile,
                            reason=_out_of_state_reason(best),
                        )
                    attempt.outcome = OUTCOME_AMBIGUOUS
                    attempt.candidates = verdict.gate_passers[:10]
                if verdict.status == "narrow":
                    narrowed_pool, narrowed_status = disamb.apply_narrowing(
                        verdict.gate_passers, master_row,
                    )
                    if narrowed_status == "selected" and narrowed_pool:
                        chosen = narrowed_pool[0]
                        bd = disamb.score_candidate(
                            chosen, master_row,
                            weight_profile=_pick_profile(trace, master_row),
                        )
                        attempt.outcome = OUTCOME_MATCH_VIA_DISAMBIGUATOR
                        trace.final_outcome = "Pass"
                        return LadderResult(
                            status="Pass", best_record=chosen, best_breakdown=bd,
                            npi_substituted=True,
                            weight_profile_used=bd.weight_profile,
                            reason=_out_of_state_reason(chosen),
                        )
                    attempt.outcome = OUTCOME_AMBIGUOUS
                    attempt.candidates = verdict.gate_passers[:10]
                if verdict.status == "no_gate_pass":
                    if records:
                        attempt.outcome = _diagnose_failure_outcome(records, master_row, cfg_obj.identity.source_id)
                        attempt.candidates = records[:10]
                    else:
                        attempt.outcome = OUTCOME_NO_RECORDS

    # NPPES retry exhausted with no ACTIVE match found, but we did match an expired/
    # non-active NPPES-substituted record. Return it now so the row reports a proper
    # expired Fail downstream rather than a generic AI escalation. (Reached only when
    # no later retry plan produced an active record — e.g. the numeric-only variant
    # also found nothing current.)
    if expired_nppes_fallback is not None:
        trace.final_outcome = "Pass"
        return expired_nppes_fallback

    # No active record found anywhere, but Loop 1 matched a superseded sibling credential
    # (e.g. the Transitioned "8727" for input "S-8727"). Return it so the row reports that
    # record rather than escalating to AI / no-match.
    if transitioned_fallback is not None:
        trace.final_outcome = "Pass"
        return transitioned_fallback

    # NPPES retry exhausted — if a name_match_no_license was deferred, return it now.
    if deferred_fail is not None:
        trace.final_outcome = "Fail"
        trace.final_reason = REASON_NAME_MATCH_NO_LICENSE
        return deferred_fail

    # Board(s) unavailable: if at least one routed board was down AND no board
    # ever produced an evaluable response (every attempt was board_unavailable,
    # a transient error, or a skipped duplicate), classify the row as a Skip so
    # it is retried when the board recovers — instead of burning the AI fallback
    # on a site that never answered and mislabelling it a no_records/name issue.
    _reached_a_board = any(
        a.outcome not in (OUTCOME_BOARD_UNAVAILABLE, OUTCOME_ERROR, OUTCOME_SKIPPED_DUPLICATE)
        for a in trace.attempts
    )
    if not _reached_a_board and any(
        a.outcome == OUTCOME_BOARD_UNAVAILABLE for a in trace.attempts
    ):
        trace.final_outcome = "Skip"
        trace.final_reason = REASON_BOARD_UNAVAILABLE
        return LadderResult(status="Fail", reason=REASON_BOARD_UNAVAILABLE)

    # Both ladders exhausted.
    final_reason = (trace.escalate_to_ai_reason
                    or last_specific_reason
                    or REASON_NO_RECORDS)
    trace.escalate_to_ai_reason = trace.escalate_to_ai_reason or final_reason
    trace.final_outcome = "EscalateAi"
    trace.final_reason = final_reason
    return LadderResult(
        status="EscalateAi",
        reason=final_reason,
        best_record=_license_exact_fallback.best_record if _license_exact_fallback else None,
        best_breakdown=_license_exact_fallback.best_breakdown if _license_exact_fallback else None,
        weight_profile_used=_license_exact_fallback.weight_profile_used if _license_exact_fallback else "license_present",
    )


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------

def _input_license_found_in_prior_attempts(
    trace: RowTrace, input_lic: str, master_row: dict
) -> bool:
    """Return True if any non-NPI attempt already returned a candidate whose license
    matches the input license AND whose last name matches the input person.

    Both conditions are required: license-only would fire on a different person who
    happens to share the same license number (different type), incorrectly blocking a
    legitimate NPI substitution."""
    norm_input = input_lic.lstrip("0") or "0"
    input_last = (master_row.get("last_name") or "").strip().upper()
    for attempt in trace.attempts:
        if attempt.used_npi_data:
            continue
        for cand in (attempt.candidates or []):
            cand_lic = (getattr(cand, "license_number", "") or "").strip()
            if not cand_lic:
                continue
            if (cand_lic.lstrip("0") or "0") != norm_input:
                continue
            cand_last = (getattr(cand, "licensee_last_name", "") or "").strip().upper()
            if input_last and cand_last and input_last == cand_last:
                return True
    return False


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
    board_unavailable = False
    records: list = []

    effective_timeout = float(
        getattr(cfg_obj.transport, "ladder_timeout_s", None) or timeout_s
    )
    try:
        records = await asyncio.wait_for(
            executor(cfg_obj, plan.query, trace.run_id),
            timeout=effective_timeout,
        )
    except BoardUnavailableError as exc:
        error_msg = str(exc)[:300]
        board_unavailable = True
        log.warning("[%s] board unavailable mode=%s sig=%s: %s", src_id, plan.mode, sig, exc)
    except asyncio.TimeoutError:
        error_msg = f"timeout_{int(effective_timeout)}s"
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
        outcome=(OUTCOME_BOARD_UNAVAILABLE if board_unavailable
                 else "" if not error_msg else OUTCOME_ERROR),
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


def _pick_profile(trace: RowTrace, master_row: Optional[dict] = None,
                  current_mode: Optional[str] = None) -> str:
    """Choose disambiguator weight profile based on whether license-based
    attempts have returned any records yet.

    Temp-permit licenses (TP prefix) always use name_only: the board only
    stores the permanent license number, so license matching is meaningless.

    Name-mode searches always use name_only regardless of whether a prior
    license search found a different person (license-number collision with a
    different provider contaminates license_attempts_returned_records).
    """
    if master_row and _is_temp_permit(master_row.get("license_id") or ""):
        return "name_only"
    if current_mode in _NAME_MODES:
        return "name_only"
    return "license_present" if trace.license_attempts_returned_records() else "name_only"


async def _evaluate_records(records: list, master_row: dict, trace: RowTrace,
                             current_mode: Optional[str] = None,
                            ) -> disamb.DisambiguationVerdict:
    if not records:
        return disamb.DisambiguationVerdict(status="no_gate_pass")
    profile = _pick_profile(trace, master_row, current_mode=current_mode)
    return disamb.evaluate(records, master_row, weight_profile=profile)


def _diagnose_failure_outcome(records: list, master_row: dict,
                              source_id: str = "") -> str:
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
            if full.strip():
                f, l = disamb._split_full_name(full, m_last)
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
    if m_pt and (source_id, m_pt) not in _SKIP_PROV_TYPE_CHECK and not any(
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
        OUTCOME_NAME_MATCH_NO_LICENSE: REASON_NAME_MATCH_NO_LICENSE,
        OUTCOME_PROVIDER_TYPE_MISMATCH: REASON_PROVIDER_TYPE_MISMATCH,
        OUTCOME_AMBIGUOUS: REASON_AMBIGUOUS_AFTER_NARROWING,
        OUTCOME_NARROWED: REASON_AMBIGUOUS_AFTER_NARROWING,
    }.get(outcome, REASON_NO_RECORDS)
