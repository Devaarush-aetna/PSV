"""Disambiguation: gate + dual-profile scorer + tiebreaker.

Per the plan:
- Gate: a candidate is selectable only when
    (first AND license) OR (first AND last) match.
  Middle name is NEVER used.
- Two weight profiles:
    license_present: license_num 0.35 / first 0.30 / last 0.20 /
                     provider_type 0.10 / state 0.05, threshold 0.90
    name_only:       first 0.40 / last 0.30 / provider_type 0.25 /
                     state 0.05, threshold 0.85
- Tiebreaker: when top two are within 0.02, provider_type match wins.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from rapidfuzz import fuzz

from . import config as cfg

log = logging.getLogger(__name__)

# Common US English nicknames -> canonical. Bidirectional via the helper.
_NICKNAME_PAIRS: list[tuple[str, str]] = [
    ("ROBERT", "BOB"), ("ROBERT", "BOBBY"), ("ROBERT", "ROB"),
    ("WILLIAM", "BILL"), ("WILLIAM", "BILLY"), ("WILLIAM", "WILL"),
    ("RICHARD", "RICK"), ("RICHARD", "RICKY"), ("RICHARD", "DICK"),
    ("JAMES", "JIM"), ("JAMES", "JIMMY"),
    ("JOHN", "JOHNNY"), ("JOHN", "JACK"),
    ("MICHAEL", "MIKE"), ("MICHAEL", "MICKEY"),
    ("CHRISTOPHER", "CHRIS"),
    ("DANIEL", "DAN"), ("DANIEL", "DANNY"),
    ("THOMAS", "TOM"), ("THOMAS", "TOMMY"),
    ("EDWARD", "ED"), ("EDWARD", "EDDIE"), ("EDWARD", "TED"),
    ("CHARLES", "CHARLIE"), ("CHARLES", "CHUCK"),
    ("KENNETH", "KEN"), ("KENNETH", "KENNY"),
    ("MATTHEW", "MATT"),
    ("ANTHONY", "TONY"),
    ("DONALD", "DON"), ("DONALD", "DONNIE"),
    ("STEPHEN", "STEVE"), ("STEVEN", "STEVE"),
    ("ANDREW", "ANDY"), ("ANDREW", "DREW"),
    ("KATHRYN", "KATHY"), ("KATHRYN", "KATE"), ("KATHRYN", "KATIE"),
    ("KATHERINE", "KATHY"), ("KATHERINE", "KATE"), ("KATHERINE", "KATIE"),
    ("ELIZABETH", "LIZ"), ("ELIZABETH", "BETH"), ("ELIZABETH", "BETTY"), ("ELIZABETH", "LIZZY"),
    ("MARGARET", "MAGGIE"), ("MARGARET", "PEGGY"), ("MARGARET", "MEG"),
    ("PATRICIA", "PAT"), ("PATRICIA", "PATTY"), ("PATRICIA", "TRISH"),
    ("JENNIFER", "JEN"), ("JENNIFER", "JENNY"),
    ("REBECCA", "BECKY"), ("REBECCA", "BECCA"),
    ("DEBORAH", "DEB"), ("DEBORAH", "DEBBIE"),
    ("BARBARA", "BARB"), ("BARBARA", "BARBIE"),
    ("SUSAN", "SUE"), ("SUSAN", "SUZIE"),
    ("CYNTHIA", "CINDY"),
    ("THERESA", "TERRY"), ("THERESA", "TERESA"),
    ("PAMELA", "PAM"),
    ("SAMANTHA", "SAM"),
    ("ALEXANDER", "ALEX"), ("ALEXANDRA", "ALEX"),
    ("BENJAMIN", "BEN"),
    ("DEIRDRE", "DIERDRE"),
]
_NICK_MAP: dict[str, set[str]] = {}
for _a, _b in _NICKNAME_PAIRS:
    _NICK_MAP.setdefault(_a, set()).add(_b)
    _NICK_MAP.setdefault(_b, set()).add(_a)
    _NICK_MAP[_a].add(_a)
    _NICK_MAP[_b].add(_b)


def _normalize_name(s: str) -> str:
    """Upper, collapse whitespace, hyphens/apostrophes -> space."""
    if not s:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[-.']+", " ", str(s).upper())).strip()


def _numeric_only(s: str) -> str:
    return re.sub(r"\D", "", str(s or ""))


# Suffixes boards append after the last name that are NOT part of it.
_NAME_SUFFIXES = {
    "II", "III", "IV", "V", "JR", "JR.", "SR", "SR.",
    "ESQ", "ESQ.", "PHD", "MD", "DO", "DDS", "DMD", "RN",
    "APRN", "DNP", "NP", "PA", "PT", "OT", "DC", "OD",
}


def _split_full_name(full_name: str, master_last: str) -> tuple[str, str]:
    """Split a board-returned full name into (first, last) for scoring.

    Handles two common board formats:
    1. "Last, First [Middle]"  (comma-separated — most state licensing boards)
    2. "First [Middle] Last"   (space-only)

    Also:
    - Strips trailing legal/credential suffixes (II, JR, MD, RN …)
    - Uses master_last word-count to extract compound last names (e.g. "RODRIGUEZ PESTANA")
    """
    # ---- Format 1: "Last, First [Middle]" ----
    if ',' in full_name:
        comma_idx = full_name.index(',')
        raw_last = full_name[:comma_idx].strip()
        rest = full_name[comma_idx + 1:].strip()

        last_toks = raw_last.upper().split()
        while last_toks and last_toks[-1].rstrip('.') in _NAME_SUFFIXES:
            last_toks = last_toks[:-1]

        rest_toks = rest.upper().split()
        while rest_toks and rest_toks[-1].rstrip('.') in _NAME_SUFFIXES:
            rest_toks = rest_toks[:-1]

        first_tok = rest_toks[0] if rest_toks else ''
        last_str = ' '.join(last_toks) if last_toks else ''
        return first_tok, last_str

    # ---- Format 2: "First [Middle] Last" ----
    toks = full_name.upper().split()
    while toks and toks[-1].rstrip(".") in _NAME_SUFFIXES:
        toks = toks[:-1]
    if not toks:
        return "", ""
    if len(toks) == 1:
        return toks[0], toks[0]

    master_last_norm = _normalize_name(master_last or "")
    master_last_words = len(master_last_norm.split()) if master_last_norm else 1

    if master_last_words >= 2 and len(toks) > master_last_words:
        return toks[0], " ".join(toks[-master_last_words:])

    return toks[0], toks[-1]


# --------------------------------------------------------------------------
# Per-field comparators (return float 0..1 OR bool that we treat as 1.0/0.0)
# --------------------------------------------------------------------------

def first_name_matches(master_first: str, candidate_first: str) -> bool:
    """rapidfuzz token_sort_ratio >= NAME_FUZZ_MIN, plus nickname dictionary."""
    if not master_first or not candidate_first:
        return False
    m = _normalize_name(master_first)
    c = _normalize_name(candidate_first)
    if not m or not c:
        return False
    # Take only the first token of each (handles "John P." -> "JOHN")
    m_tok = m.split(" ")[0]
    c_tok = c.split(" ")[0]
    if m_tok == c_tok:
        return True
    if fuzz.token_sort_ratio(m_tok, c_tok) >= cfg.NAME_FUZZ_MIN:
        return True
    # Nickname dictionary
    aliases = _NICK_MAP.get(m_tok, {m_tok})
    if c_tok in aliases:
        return True
    return False


def first_name_score(master_first: str, candidate_first: str) -> float:
    """0..1 fractional score for the first name field."""
    if not master_first or not candidate_first:
        return 0.0
    m_tok = _normalize_name(master_first).split(" ")[0]
    c_tok = _normalize_name(candidate_first).split(" ")[0]
    if not m_tok or not c_tok:
        return 0.0
    if m_tok == c_tok:
        return 1.0
    if c_tok in _NICK_MAP.get(m_tok, set()):
        return 1.0
    return fuzz.token_sort_ratio(m_tok, c_tok) / 100.0


def last_name_score(master_last: str, candidate_last: str) -> float:
    if not master_last or not candidate_last:
        return 0.0
    m = _normalize_name(master_last)
    c = _normalize_name(candidate_last)
    if not m or not c:
        return 0.0
    if m == c:
        return 1.0
    # Hyphenated surname fallback: any component substring match counts strongly.
    if "-" in master_last:
        parts = [_normalize_name(p) for p in master_last.split("-") if p.strip()]
        if any(p and p in c for p in parts):
            return 0.95
    return fuzz.token_sort_ratio(m, c) / 100.0


def last_name_matches(master_last: str, candidate_last: str) -> bool:
    return last_name_score(master_last, candidate_last) >= (cfg.NAME_FUZZ_MIN / 100.0)


def license_numerics_match(master_lic: str, candidate_lic: str) -> bool:
    """Reuses the digit-strip + leading-zero tolerance from psv_test._license_matches."""
    if not master_lic or not candidate_lic:
        return False
    m = _numeric_only(master_lic)
    c = _numeric_only(candidate_lic)
    if not m or not c:
        return False
    if m == c:
        return True
    if m.lstrip("0") == c.lstrip("0"):
        return True
    # Substring match only when both have content and lengths are close
    if len(m) >= 4 and m in c and abs(len(c) - len(m)) <= 2:
        return True
    return False


def provider_type_matches(prov_type: str, candidate_license_type: str,
                          candidate_profession_code: str = "") -> bool:
    """Master prov_type (e.g. 'MD', 'OD', 'DDS') vs candidate's license_type
    and profession_code strings. Case-insensitive substring match; abbreviation
    aware via the existing _SOCRATA_TYPE_MAP that psv_test.py uses for routing.

    The map maps (source_id, prov_type) -> license_type/provider_type strings
    seen on boards. For disambiguation we just check whether the prov_type
    abbreviation OR any mapped synonym appears in the candidate's type strings.
    """
    if not prov_type:
        return True   # no master prov_type to compare → vacuous match
    pt = prov_type.upper().strip()
    blobs: list[str] = []
    if candidate_license_type:
        blobs.append(str(candidate_license_type).upper())
    if candidate_profession_code:
        blobs.append(str(candidate_profession_code).upper())
    if not blobs:
        # Candidate has no type info at all → cannot prove mismatch; don't
        # penalize. Return True (vacuous match).
        return True
    combined = " ".join(blobs)
    if pt in combined:
        return True
    # PSV prov_type code → full-name expansions seen on board license_type fields.
    # Codes match board_routing_master.csv 2-3 letter abbreviations.
    expansions = {
        "MD": ("MEDICAL DOCTOR", "PHYSICIAN", "MEDICAL BOARD"),
        "DO": ("OSTEOPATHIC", "OSTEOPATHY"),
        "DDS": ("DENTIST", "DENTAL"),
        "DMD": ("DENTIST", "DENTAL"),
        "DN": ("DENTIST", "DENTAL"),
        "OD": ("OPTOMETRIST", "OPTOMETRY"),
        "DPM": ("PODIATRIC", "PODIATRY"),
        "DP": ("PODIATRIC", "PODIATRY"),
        "PA": ("PHYSICIAN ASSISTANT",),
        "PAS": ("PHYSICIAN ASSISTANT", "PHYSICIANS ASSISTANT"),
        "PAB": ("PHYSICIAN ASSISTANT", "PHYSICIANS ASSISTANT"),
        "RN": ("REGISTERED NURSE", "NURSING", "NURSE REGISTERED"),
        "RNA": ("NURSE ANESTHETIST", "ANESTHESIA", "CRNA"),
        "NP": ("NURSE PRACTITIONER", "ADVANCED PRACTICE", "ARNP", "APRN", "ADVANCED REGISTERED"),
        "NPB": ("ADVANCED PRACTICE", "ARNP", "APRN"),
        "NPS": ("PSYCHIATRIC", "MENTAL HEALTH", "ADVANCED PRACTICE"),
        "PN": ("PRACTICAL NURSE", "LPN", "LICENSED PRACTICAL"),
        "GNC": ("NURSING ASSISTANT", "CERTIFIED NURSING", "CNA"),
        "PT": ("PHYSICAL THERAPIST", "PHYSICAL THERAPY"),
        "OT": ("OCCUPATIONAL THERAPIST", "OCCUPATIONAL THERAPY"),
        "SW": ("SOCIAL WORKER", "SOCIAL WORK", "LCSW", "LCSWA", "LMSW"),
        "LCSW": ("LICENSED CLINICAL SOCIAL", "SOCIAL WORK"),
        "LPC": ("PROFESSIONAL COUNSELOR", "MENTAL HEALTH COUNSEL", "COUNSEL"),
        "LC": ("LICENSED COUNSEL", "COUNSEL", "MENTAL HEALTH COUNSEL"),
        "MFT": ("MARRIAGE", "FAMILY THERAPIST"),
        "DC": ("CHIROPRACT",),
        "DAC": ("ADDICTION COUNSEL", "DRUG ABUSE", "SUBSTANCE ABUSE"),
        "AP": ("ACUPUNCTUR", "ORIENTAL MEDICINE"),
        "AU": ("AUDIOLOGIST", "AUDIOLOGY"),
        "SH": ("HEARING AID", "AUDIOLOGY", "AUDIOLOGIST"),
        "ST": ("SPEECH", "SPEECH-LANGUAGE", "SPEECH LANGUAGE"),
        "CP": ("PSYCHOLOGIST", "PSYCHOLOGY"),
        "PC": ("PSYCHOLOGIST", "PSYCHOLOGY"),
        "PH": ("PHARMACIST", "PHARMACY"),
        "PM": ("PHARMACY",),
        "DT": ("DIETITIAN", "DIETETICS", "NUTRITIONIST", "NUTRITION"),
        "NUT": ("NUTRITIONIST", "NUTRITION"),
        "MT": ("MASSAGE", "MARRIAGE", "FAMILY THERAPIST", "MFT"),
        "MW": ("MIDWIFE", "MIDWIFERY", "ADV PRACTICE", "APRN", "CNM", "NMW"),
        "MST": ("MASSAGE",),
        "ABA": ("APPLIED BEHAVIOR", "BEHAVIOR ANALYST", "BEHAVIORAL ANALYST"),
        "OP": ("OPTICIAN",),
        "OR": ("OPTICIAN",),
        "PE": ("PERFUSIONIST", "PERFUSION"),
        "ND": ("NATUROPATH",),
        "NSA": ("ANESTHESIOLOGIST ASSISTANT",),
        "ART": ("ART THERAPIST", "RADIOLOGIC"),
        "PAS_OPHTHALMIC": ("OPHTHALMIC", "ORTHOTIST", "PROSTHET"),
    }
    for syn in expansions.get(pt, ()):
        if syn in combined:
            return True
    return False


def state_matches(master_state: str, candidate_state: str) -> bool:
    """Vacuous-true when candidate has no state_code populated — many board
    archetypes (e.g. FL_MQA) omit it since the entire board is state-specific
    and the candidate's origin board already implies the state. Returning
    False there would unfairly cost 0.05 in score for every clean match.
    """
    if not candidate_state:
        return True   # vacuous — candidate is from a state-scoped board
    if not master_state:
        return False
    return master_state.upper().strip() == candidate_state.upper().strip()


# --------------------------------------------------------------------------
# Score breakdown
# --------------------------------------------------------------------------

@dataclass
class ScoreBreakdown:
    license_numerics: float = 0.0
    first_name: float = 0.0
    last_name: float = 0.0
    provider_type: float = 0.0
    state: float = 0.0
    middle_name: float = 0.0
    weight_profile: str = "license_present"
    total: float = 0.0
    gate_passed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "license_numerics": round(self.license_numerics, 3),
            "first_name": round(self.first_name, 3),
            "last_name": round(self.last_name, 3),
            "provider_type": round(self.provider_type, 3),
            "state": round(self.state, 3),
            "middle_name": round(self.middle_name, 3),
            "weight_profile": self.weight_profile,
            "total": round(self.total, 3),
            "gate_passed": self.gate_passed,
        }


_WEIGHTS_LICENSE = {
    "license_numerics": 0.35,
    "first_name": 0.30,
    "last_name": 0.20,
    "provider_type": 0.10,
    "state": 0.05,
}
_WEIGHTS_NAME_ONLY = {
    "license_numerics": 0.00,
    "first_name": 0.40,
    "last_name": 0.30,
    "provider_type": 0.25,
    "state": 0.05,
}


def score_candidate(candidate: Any, master_row: dict,
                    weight_profile: str = "license_present") -> ScoreBreakdown:
    """Return a ScoreBreakdown 0..1. `candidate` is a LicenseRecord (or any
    object exposing .licensee_first_name, .licensee_last_name, .license_number,
    .license_type, .profession_code, .state_code).
    """
    # If the board's summary table doesn't include a license number (common for boards
    # like KS_BSRB where only the detail page has it), using license_present weights
    # gives license_numerics=0 which drags total below threshold even for perfect
    # name matches. Downgrade to name_only when the candidate has no license number.
    c_lic_raw = getattr(candidate, "license_number", "") or ""
    effective_profile = (
        "name_only"
        if weight_profile == "license_present" and not c_lic_raw.strip()
        else weight_profile
    )

    m_first = master_row.get("first_name", "")
    m_last = master_row.get("last_name", "")
    m_lic = master_row.get("license_id", "")
    m_pt = (master_row.get("prov_type") or "").upper()
    m_state = (master_row.get("lic_state") or "").upper()

    # Upgrade name_only → license_present when the candidate carries a matching
    # license number — e.g. a board whose keyword search ignores license prefixes
    # (Certemy) so the license_number rung returns 0, but the last_name rung finds
    # the record and its license_number field matches the input exactly.
    if (effective_profile == "name_only"
            and c_lic_raw.strip()
            and license_numerics_match(m_lic, c_lic_raw)):
        effective_profile = "license_present"

    weights = _WEIGHTS_LICENSE if effective_profile == "license_present" else _WEIGHTS_NAME_ONLY

    c_first = getattr(candidate, "licensee_first_name", "") or ""
    c_last = getattr(candidate, "licensee_last_name", "") or ""
    c_lic = c_lic_raw  # reuse the already-read value from above
    c_lic_type = getattr(candidate, "license_type", "") or ""
    c_prof_code = getattr(candidate, "profession_code", "") or ""
    c_state = getattr(candidate, "state_code", "") or ""

    # If candidate has full name but no parsed first/last, split intelligently:
    # strip suffixes, use master last-name word count for compound last names.
    if not c_first and not c_last:
        full = getattr(candidate, "licensee_full_name", "") or ""
        if full.strip():
            c_first, c_last = _split_full_name(full, m_last)

    breakdown = ScoreBreakdown(weight_profile=effective_profile)

    # Score each field, weighted.
    if weights["license_numerics"] > 0:
        breakdown.license_numerics = 1.0 if license_numerics_match(m_lic, c_lic) else 0.0
    breakdown.first_name = first_name_score(m_first, c_first)
    breakdown.last_name = last_name_score(m_last, c_last)
    breakdown.provider_type = 1.0 if provider_type_matches(m_pt, c_lic_type, c_prof_code) else 0.0
    breakdown.state = 1.0 if state_matches(m_state, c_state) else 0.0
    # middle_name explicitly 0

    total = sum(getattr(breakdown, k) * w for k, w in weights.items())
    breakdown.total = round(total, 4)

    # Gate: (first AND license) OR (first AND last). Middle never used.
    first_ok = first_name_matches(m_first, c_first)
    last_ok = last_name_matches(m_last, c_last)
    lic_ok = license_numerics_match(m_lic, c_lic) if (m_lic and c_lic) else False
    breakdown.gate_passed = bool(first_ok and (lic_ok or last_ok))

    return breakdown


# --------------------------------------------------------------------------
# Top-level: evaluate a candidate list
# --------------------------------------------------------------------------

@dataclass
class DisambiguationVerdict:
    status: str                            # selected | narrow | ambiguous | no_gate_pass
    best: Optional[Any] = None             # the chosen candidate (or None)
    best_breakdown: Optional[ScoreBreakdown] = None
    gate_passers: list[Any] = field(default_factory=list)
    all_breakdowns: list[ScoreBreakdown] = field(default_factory=list)
    tiebreaker_used: bool = False


def evaluate(candidates: list[Any], master_row: dict,
             weight_profile: str = "license_present") -> DisambiguationVerdict:
    """Score every candidate and decide the next step."""
    if not candidates:
        return DisambiguationVerdict(status="no_gate_pass")

    scored: list[tuple[Any, ScoreBreakdown]] = []
    for cand in candidates:
        bd = score_candidate(cand, master_row, weight_profile=weight_profile)
        scored.append((cand, bd))

    breakdowns = [bd for _, bd in scored]
    gate_passers = [(c, bd) for c, bd in scored if bd.gate_passed]

    if not gate_passers:
        return DisambiguationVerdict(
            status="no_gate_pass",
            all_breakdowns=breakdowns,
        )

    # Sort gate-passers by total descending
    gate_passers.sort(key=lambda x: x[1].total, reverse=True)

    threshold = (cfg.THRESHOLD_LICENSE_PROFILE
                 if weight_profile == "license_present"
                 else cfg.THRESHOLD_NAME_PROFILE)

    top_cand, top_bd = gate_passers[0]

    if len(gate_passers) == 1:
        if top_bd.total >= threshold:
            return DisambiguationVerdict(
                status="selected", best=top_cand, best_breakdown=top_bd,
                gate_passers=[c for c, _ in gate_passers], all_breakdowns=breakdowns,
            )
        # License anchor: exact license + partial first name match → accept regardless
        # of last name. Handles name-change cases (e.g. "Duric Zinka" → board has
        # "LEWANDOWSKI, ZINKA D") where last name differs but license is definitive.
        if (top_bd.weight_profile == "license_present"
                and top_bd.license_numerics == 1.0
                and top_bd.first_name >= 0.5):
            return DisambiguationVerdict(
                status="selected", best=top_cand, best_breakdown=top_bd,
                gate_passers=[c for c, _ in gate_passers], all_breakdowns=breakdowns,
            )
        # Name anchor: name_only profile, single candidate, both first AND last name
        # independently match strongly (≥0.85 each). Provider_type may drag the
        # weighted total below the 0.85 threshold (e.g. TC/TP temp licenses where
        # prov_type is unknown), but if the name itself is unambiguous the record
        # is the right person. Accept it.
        if (top_bd.weight_profile == "name_only"
                and top_bd.first_name >= 0.85
                and top_bd.last_name >= 0.85):
            return DisambiguationVerdict(
                status="selected", best=top_cand, best_breakdown=top_bd,
                gate_passers=[c for c, _ in gate_passers], all_breakdowns=breakdowns,
            )
        # Single passer but below threshold → ambiguous (escalate).
        return DisambiguationVerdict(
            status="ambiguous", best=top_cand, best_breakdown=top_bd,
            gate_passers=[c for c, _ in gate_passers], all_breakdowns=breakdowns,
        )

    # Multiple gate passers.
    second_cand, second_bd = gate_passers[1]
    delta = top_bd.total - second_bd.total

    if delta <= cfg.TIEBREAKER_DELTA:
        # Tiebreaker: candidate whose provider_type matches wins.
        m_pt = (master_row.get("prov_type") or "").upper()
        if m_pt:
            top_pt_ok = top_bd.provider_type >= 1.0
            second_pt_ok = second_bd.provider_type >= 1.0
            if top_pt_ok and not second_pt_ok:
                return DisambiguationVerdict(
                    status="selected", best=top_cand, best_breakdown=top_bd,
                    gate_passers=[c for c, _ in gate_passers],
                    all_breakdowns=breakdowns, tiebreaker_used=True,
                )
            if second_pt_ok and not top_pt_ok:
                return DisambiguationVerdict(
                    status="selected", best=second_cand, best_breakdown=second_bd,
                    gate_passers=[c for c, _ in gate_passers],
                    all_breakdowns=breakdowns, tiebreaker_used=True,
                )
        # Close scores AND tiebreaker indeterminate → ambiguous; ladder may narrow.
        return DisambiguationVerdict(
            status="narrow", best=None,
            gate_passers=[c for c, _ in gate_passers], all_breakdowns=breakdowns,
        )

    # Clear winner by margin.
    if top_bd.total >= threshold:
        return DisambiguationVerdict(
            status="selected", best=top_cand, best_breakdown=top_bd,
            gate_passers=[c for c, _ in gate_passers], all_breakdowns=breakdowns,
        )
    return DisambiguationVerdict(
        status="ambiguous", best=top_cand, best_breakdown=top_bd,
        gate_passers=[c for c, _ in gate_passers], all_breakdowns=breakdowns,
    )


# --------------------------------------------------------------------------
# In-memory narrowing rungs (no new browser query)
# --------------------------------------------------------------------------

def apply_narrowing(candidates: list[Any], master_row: dict
                    ) -> tuple[list[Any], str]:
    """Three in-memory filter steps over `candidates`:
      1. numeric_license_part + first_name
      2. first_name + last_name (middle excluded)
      3. provider_type
    Returns (narrowed_candidates, status_after_narrowing).
    """
    m_first = master_row.get("first_name", "")
    m_last = master_row.get("last_name", "")
    m_lic = master_row.get("license_id", "")
    m_pt = (master_row.get("prov_type") or "").upper()

    pool = list(candidates)

    # Step 0: exact full license string match (prefix + digits).
    # FL_MQA returns dual entries for the same person (e.g. APRN9245838 + RN9245838).
    # Digit-only match selects both; exact string match selects exactly one.
    if m_lic:
        m_lic_norm = m_lic.upper().replace(" ", "")
        step0 = [c for c in pool
                 if (getattr(c, "license_number", "") or "").upper().replace(" ", "") == m_lic_norm]
        if len(step0) == 1:
            return step0, "selected"
        if step0:
            pool = step0
        else:
            # Step 0b: digit-normalized fallback. Handles prefix variants where the board
            # strips a non-numeric prefix that PSV retains (e.g. "TMP-163007" vs "163007").
            m_digits = _numeric_only(m_lic)
            if len(m_digits) >= 4:
                step0b = [c for c in pool
                          if _numeric_only(getattr(c, "license_number", "") or "") == m_digits]
                if len(step0b) == 1:
                    return step0b, "selected"
                if step0b:
                    pool = step0b

    # Step 1: numeric license + first name
    if m_lic and m_first:
        step1 = [c for c in pool
                 if license_numerics_match(m_lic, getattr(c, "license_number", "") or "")
                 and first_name_matches(m_first, getattr(c, "licensee_first_name", "") or "")]
        if len(step1) == 1:
            return step1, "selected"
        if step1:
            pool = step1

    # Step 2: first + last (no middle)
    if m_first and m_last:
        step2 = [c for c in pool
                 if first_name_matches(m_first, getattr(c, "licensee_first_name", "") or "")
                 and last_name_matches(m_last, getattr(c, "licensee_last_name", "") or "")]
        if len(step2) == 1:
            return step2, "selected"
        if step2:
            pool = step2

    # Step 2.5: first_name + provider_type (catches common last-name collisions
    # like "John Marshall" with 7 results where prov_type distinguishes the person).
    if m_first and m_pt:
        step2_5 = [c for c in pool
                   if first_name_matches(m_first, getattr(c, "licensee_first_name", "") or "")
                   and provider_type_matches(m_pt,
                                             getattr(c, "license_type", "") or "",
                                             getattr(c, "profession_code", "") or "")]
        if len(step2_5) == 1:
            return step2_5, "selected"
        if step2_5:
            pool = step2_5

    # Step 3: provider_type
    if m_pt:
        step3 = [c for c in pool
                 if provider_type_matches(m_pt,
                                          getattr(c, "license_type", "") or "",
                                          getattr(c, "profession_code", "") or "")]
        if len(step3) == 1:
            return step3, "selected"
        if step3:
            pool = step3

    if len(pool) == 1:
        return pool, "selected"
    if len(pool) == 0:
        return [], "no_gate_pass"
    return pool, "ambiguous"
