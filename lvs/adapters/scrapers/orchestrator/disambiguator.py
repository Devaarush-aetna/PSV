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
import unicodedata
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
    ("JOSEPH", "JOE"), ("JOSEPH", "JOEY"),
    ("ANDREW", "ANDY"), ("ANDREW", "DREW"),
    ("KATHRYN", "KATHY"), ("KATHRYN", "KATE"), ("KATHRYN", "KATIE"),
    ("KATHERINE", "KATHY"), ("KATHERINE", "KATE"), ("KATHERINE", "KATIE"),
    ("KATHLEEN", "KATHY"), ("KATHLEEN", "KATHIE"), ("KATHLEEN", "KATE"), ("KATHLEEN", "KATIE"),
    ("KATHLEEN", "KATH"),
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
    ("NATHANIEL", "NATHAN"), ("NATHANIEL", "NAT"),
    ("DEIRDRE", "DIERDRE"),
    ("GREGORY", "GREG"),
    ("TIMOTHY", "TIM"), ("TIMOTHY", "TIMMY"),
    ("NICHOLAS", "NICK"), ("NICHOLAS", "NICKY"),
    ("NICHOLAS", "NIKLOS"), ("NICHOLAS", "NIKOLAS"), ("NICHOLAS", "NIKOLAOS"),
    ("NICK", "NIKOLAS"),
    ("RICHARD", "RICK"), ("RICHARD", "RICH"), ("RICHARD", "DICK"),
    ("GERALD", "JERRY"),
    ("LAWRENCE", "LARRY"),
    ("RAYMOND", "RAY"),
    ("FREDRICK", "FRED"), ("FREDERICK", "FRED"),
    ("GERALD", "GERRY"),
    ("EUGENE", "GENE"),
    ("PHILLIP", "PHIL"), ("PHILIP", "PHIL"),
    ("JAMES", "JIM"), ("JAMES", "JIMMY"),
    ("WILLIAM", "WILL"), ("WILLIAM", "BILL"), ("WILLIAM", "BILLY"),
    ("MICHAEL", "MIKE"), ("MICHAEL", "MIKEY"),
    ("ROBERT", "BOB"), ("ROBERT", "BOBBY"), ("ROBERT", "ROB"),
    ("JOHN", "JOHNNY"), ("JOHN", "JACK"),
    ("DAVID", "DAVE"),
    ("RICHARD", "RICKY"),
    ("JESSICA", "JESS"),
    ("KIMBERLY", "KIM"),
    ("MELISSA", "MISSY"),
    ("AMANDA", "MANDY"),
    ("STEPHANIE", "STEPH"),
    ("CHRISTINA", "CHRIS"), ("CHRISTINE", "CHRIS"),
    ("JACQUELINE", "JACKIE"),
    ("CAROL", "CARRIE"),
    ("DOROTHY", "DOTTIE"), ("DOROTHY", "DOT"),
    ("VIRGINIA", "GINNY"),
    ("EVELYN", "EVIE"),
    ("BEVERLY", "BEV"),
    ("ZACHARY", "ZAC"), ("ZACHARY", "ZACH"), ("ZACHARY", "ZACK"),
    ("JONATHAN", "JON"), ("JONATHAN", "JONNY"),
    ("JOSHUA", "JOSH"),
    ("BENJAMIN", "BENJI"), ("BENJAMIN", "BENNY"),
    ("VICTORIA", "VICKY"), ("VICTORIA", "TORI"),
    ("ABIGAIL", "ABBY"), ("ABIGAIL", "ABBIE"),
    ("ALEXIS", "LEXI"),
    ("CAMILLE", "CAMI"),
    ("VALERIE", "VAL"),
    ("ZACHARY", "ZACH"),
    ("JONATHAN", "JON"), ("JONATHAN", "JONNY"),
    ("NATHANIEL", "NATE"),
    ("ALLISON", "ALLIE"),
    ("BRITTANY", "BRIT"), ("BRITTANY", "BRITT"),
    ("HEATHER", "HEATH"),
    ("MEREDITH", "MERI"),
]
_NICK_MAP: dict[str, set[str]] = {}
for _a, _b in _NICKNAME_PAIRS:
    _NICK_MAP.setdefault(_a, set()).add(_b)
    _NICK_MAP.setdefault(_b, set()).add(_a)
    _NICK_MAP[_a].add(_a)
    _NICK_MAP[_b].add(_b)


def _normalize_name(s: str) -> str:
    """Upper, collapse whitespace, hyphens/apostrophes -> space. Strips Unicode accents (é→e, ñ→n)."""
    if not s:
        return ""
    # Decompose accented characters (e.g. é→e+combining-acute) then drop combining marks.
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[-.']+", " ", s.upper())).strip()


def _numeric_only(s: str) -> str:
    return re.sub(r"\D", "", str(s or ""))


# Suffixes boards append after the last name that are NOT part of it.
# Stored with and without periods — both forms are recognised in _split_full_name via
# _NAME_SUFFIXES_NORM (all-uppercase, dots/hyphens removed), so "M.D." and "MD" match.
_NAME_SUFFIXES = {
    # Generational / legal
    "I", "II", "III", "IV", "V", "JR", "JR.", "SR", "SR.", "ESQ", "ESQ.",
    # Medical degrees (plain + dotted)
    "MD", "M.D.", "DO", "D.O.", "DPM", "D.P.M.", "DDS", "D.D.S.", "DMD", "D.M.D.",
    "OD", "O.D.", "PHD", "PH.D.", "PSYD", "PSY.D.", "DPT", "D.P.T.", "DC", "D.C.",
    "ND", "N.D.",
    # Nursing / advanced practice
    "RN", "R.N.", "LPN", "LVN", "APRN", "DNP", "CNM", "NP",
    # PA
    "PA",
    # Behavioral health
    "LCSW", "LMFT", "LPC", "LCPC", "LMHC", "BCBA", "BCABA", "RBT",
    "LGSW", "LMSW", "CSW", "MSW", "MCOUN",
    # PT / OT / SLP / AUD (incl. assistant + registered variants)
    "PT", "PTA", "LPT",
    "OT", "OTA", "OT-A", "OTR", "OTRL", "OTR/L", "COTA", "COTAL",
    "SLP", "AUD", "ST", "STA",
    # Respiratory care
    "RCP", "LRCP", "RRT", "CRT",
    # Genetic counseling
    "LGC", "CGC",
    # Dietetics / nutrition / massage
    "RDN", "LMT", "MST",
    # Business-entity suffixes (boards occasionally list practice entities)
    "LLC", "LLP", "PLLC",
    # Pharmacy
    "PHARMD", "PHARM.D.", "RPH",
    # Fellowship designations
    "FACP", "FACS", "FACOG", "FAAP",
}

# Pre-normalised (uppercase, dots/hyphens stripped) for O(1) lookup.
_NAME_SUFFIXES_NORM: frozenset[str] = frozenset(
    re.sub(r"[.\-]", "", s).upper() for s in _NAME_SUFFIXES
)

# Honorific / title prefixes and credential-type codes that some boards prepend
# to the name field (e.g. Idaho DOPL returns "LD MARISSA RUDLEY" where "LD" is
# the Licensed Dietitian credential code, not part of the name).
_NAME_PREFIXES_NORM: frozenset[str] = frozenset({
    "DR", "MR", "MRS", "MS", "MISS", "PROF", "REV", "PASTOR", "RABBI",
    "SISTER", "BROTHER",
    # Board-prepended credential type codes (ID_DOPL and similar)
    "LD",
})


def _strip_name_affixes(tokens: list[str]) -> list[str]:
    """Remove leading honorific/title prefixes and trailing credential suffixes.

    Works on an already-normalised token list (uppercase, hyphens→spaces).
    Returns a new list; never mutates the input.
    """
    toks = list(tokens)
    while toks and re.sub(r"[.\-,]", "", toks[0]) in _NAME_PREFIXES_NORM:
        toks = toks[1:]
    while toks and re.sub(r"[.\-,]", "", toks[-1]) in _NAME_SUFFIXES_NORM:
        toks = toks[:-1]
    # Honorifics sometimes appear at the END of board-stored names (e.g. "Jang-en Sarah Lin Mrs.")
    # Strip them from the trailing position too so they don't get parsed as the last name.
    while toks and re.sub(r"[.\-,]", "", toks[-1]) in _NAME_PREFIXES_NORM:
        toks = toks[:-1]
    return toks


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
        # "Last, First, Suffix" (two commas) — replace the second comma so
        # "Joseph, III" becomes "Joseph  III" before tokenising.  Without this
        # the first token becomes "JOSEPH," with a trailing comma that leaks
        # into c_first and can corrupt the matched_first output field.
        rest = re.sub(r",", " ", rest)

        rest_toks = [t.rstrip(',') for t in rest.upper().split()]
        while rest_toks and re.sub(r"[.\-,]", "", rest_toks[-1]) in _NAME_SUFFIXES_NORM:
            rest_toks = rest_toks[:-1]
        while rest_toks and re.sub(r"[.\-,]", "", rest_toks[0]) in _NAME_PREFIXES_NORM:
            rest_toks = rest_toks[1:]
        # Strip leading generational/credential suffixes that appear before the first name
        # when boards write "Last, Jr., First" (two-comma format).
        # e.g. "Slaughter, Jr., Dale Jeffery" → rest_toks=["JR.", "DALE", "JEFFERY"]
        # Without this, first_tok would be "JR." instead of "DALE".
        while rest_toks and re.sub(r"[.\-,]", "", rest_toks[0]) in _NAME_SUFFIXES_NORM:
            rest_toks = rest_toks[1:]

        # If everything after the comma was a suffix (e.g. "George Joseph Vesper, Jr."),
        # the comma is separating a suffix, not Last from First.  Fall through to Format 2
        # using raw_last (the name before the comma) as the full name.
        if not rest_toks:
            full_name = raw_last
        else:
            last_toks = [t.rstrip(',') for t in raw_last.upper().split()]
            while last_toks and re.sub(r"[.\-,]", "", last_toks[-1]) in _NAME_SUFFIXES_NORM:
                last_toks = last_toks[:-1]
            first_tok = rest_toks[0]
            last_str = ' '.join(last_toks) if last_toks else ''
            if not first_tok and len(last_toks) >= 2:
                master_last_norm = _normalize_name(master_last or "")
                master_last_words = len(master_last_norm.split()) if master_last_norm else 1
                if master_last_words >= 2 and len(last_toks) > master_last_words:
                    return last_toks[0], " ".join(last_toks[-master_last_words:])
                return last_toks[0], last_toks[-1]
            return first_tok, last_str

    # ---- Format 2: "First [Middle] Last" ----
    toks = [t.rstrip(',') for t in full_name.upper().split()]
    # Pre-compute master_last_norm so the suffix loop can protect the last name token.
    # Without this, a last name like "Do" (Vietnamese) would be stripped as the
    # credential abbreviation "DO" (Doctor of Osteopathy) and the record would fail.
    master_last_norm = _normalize_name(master_last or "")
    while toks and re.sub(r"[.\-,]", "", toks[-1]) in _NAME_SUFFIXES_NORM:
        # Never strip the last token when it exactly matches master_last — it IS the
        # last name, not a trailing credential (e.g. last="Do" vs suffix "DO").
        if master_last_norm and _normalize_name(toks[-1]) == master_last_norm:
            break
        toks = toks[:-1]
    # Some boards append honorifics at the END (e.g. "Jang-en Sarah Lin Mrs.").
    # Strip trailing honorifics too so they don't get parsed as the last name.
    while toks and re.sub(r"[.\-,]", "", toks[-1]) in _NAME_PREFIXES_NORM:
        toks = toks[:-1]
    while toks and re.sub(r"[.\-,]", "", toks[0]) in _NAME_PREFIXES_NORM:
        toks = toks[1:]
    if not toks:
        return "", ""
    if len(toks) == 1:
        return toks[0], toks[0]

    # Count space-separated words in the ORIGINAL master_last, not the normalised version.
    # _normalize_name converts hyphens to spaces, inflating the count for hyphenated names
    # like "Jang-En" (1 token on the board) → "JANG EN" (2 norm words) → wrongly grabs 2
    # trailing board tokens. Space-splitting the original correctly gives 1 for "Jang-En"
    # and 2 for "Rodriguez Pestana", matching how boards tokenise compound last names.
    master_last_words = len(master_last.split()) if master_last else 1

    if master_last_words >= 2 and len(toks) > master_last_words:
        return toks[0], " ".join(toks[-master_last_words:])

    # Some boards display short last names first without a comma (e.g. "DO JESSICA"
    # instead of "DO, JESSICA").  When exactly 2 tokens and the first token matches
    # master_last exactly (but the last token does not), treat it as "Last First" order.
    if len(toks) == 2 and master_last_norm:
        first_tok_norm = _normalize_name(toks[0])
        last_tok_norm = _normalize_name(toks[-1])
        if first_tok_norm == master_last_norm and last_tok_norm != master_last_norm:
            return toks[1], toks[0]

    return toks[0], toks[-1]


# --------------------------------------------------------------------------
# Per-field comparators (return float 0..1 OR bool that we treat as 1.0/0.0)
# --------------------------------------------------------------------------

def first_name_matches(master_first: str, candidate_first: str) -> bool:
    """rapidfuzz token_sort_ratio >= NAME_FUZZ_MIN, plus nickname dictionary."""
    if not master_first or not candidate_first:
        return False
    m_toks = _strip_name_affixes(_normalize_name(master_first).split())
    c_toks = _strip_name_affixes(_normalize_name(candidate_first).split())
    if not m_toks or not c_toks:
        return False
    m_tok = m_toks[0]
    c_tok = c_toks[0]
    if m_tok == c_tok:
        return True
    if fuzz.token_sort_ratio(m_tok, c_tok) >= cfg.NAME_FUZZ_MIN:
        return True
    # Nickname dictionary
    aliases = _NICK_MAP.get(m_tok, {m_tok})
    if c_tok in aliases:
        return True
    # Apostrophe/hyphen join: "De'Andrea" normalises to ["DE", "ANDREA"] while
    # the board stores the concatenated form "DeAndrea" → "DEANDREA".
    # Joining all master tokens gives the same string and should match exactly.
    m_joined = "".join(m_toks)
    c_joined = "".join(c_toks)
    if m_joined == c_joined:
        return True
    if m_joined and c_joined and fuzz.token_sort_ratio(m_joined, c_joined) >= cfg.NAME_FUZZ_MIN:
        return True
    return False


def first_name_score(master_first: str, candidate_first: str) -> float:
    """0..1 fractional score for the first name field."""
    if not master_first or not candidate_first:
        return 0.0
    m_toks = _strip_name_affixes(_normalize_name(master_first).split())
    c_toks = _strip_name_affixes(_normalize_name(candidate_first).split())
    m_tok = m_toks[0] if m_toks else ""
    c_tok = c_toks[0] if c_toks else ""
    if not m_tok or not c_tok:
        return 0.0
    if m_tok == c_tok:
        return 1.0
    if c_tok in _NICK_MAP.get(m_tok, set()):
        return 1.0
    # Apostrophe/hyphen join (see first_name_matches for explanation)
    m_joined = "".join(m_toks)
    c_joined = "".join(c_toks)
    if m_joined and c_joined and m_joined == c_joined:
        return 1.0
    return fuzz.token_sort_ratio(m_tok, c_tok) / 100.0


def last_name_score(master_last: str, candidate_last: str) -> float:
    if not master_last or not candidate_last:
        return 0.0
    m = " ".join(_strip_name_affixes(_normalize_name(master_last).split()))
    c = " ".join(_strip_name_affixes(_normalize_name(candidate_last).split()))
    if not m or not c:
        return 0.0
    if m == c:
        return 1.0
    # Hyphenated surname fallback: any component substring match counts strongly.
    # Check BOTH sides — board may carry the hyphen even when EPDB doesn't.
    for _raw, _other in ((master_last, c), (candidate_last, m)):
        if "-" in _raw:
            parts = [" ".join(_strip_name_affixes(_normalize_name(p).split()))
                     for p in _raw.split("-") if p.strip()]
            if any(p and p in _other for p in parts):
                return 0.95
    # Compound/multi-surname: one name is a contiguous word-span of the other.
    # Handles both directions:
    #   "DA COSTA"        vs "DA COSTA GOMEZ"  →  0.95
    #   "GOMEZ"           vs "DA COSTA GOMEZ"  →  0.95
    #   "DA COSTA GOMEZ"  vs "DA COSTA"        →  0.95
    m_words = m.split()
    c_words = c.split()
    if len(m_words) != len(c_words):
        shorter, longer = (
            (m_words, c_words) if len(m_words) < len(c_words) else (c_words, m_words)
        )
        n = len(shorter)
        for i in range(len(longer) - n + 1):
            if longer[i : i + n] == shorter:
                return 0.95
    # Space-collapse fallback: "DO PICO" == "DOPICO" when spaces removed.
    if m.replace(" ", "") == c.replace(" ", ""):
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
    # Substring match: board prepends a state/type code so the input appears at the
    # TAIL of the board value (e.g. "031234" for input "1234").  Use endswith to
    # prevent matching input digits that form a leading prefix of a different number
    # (e.g. "1495" leading "14959" — different numbers, not a format difference).
    if len(m) >= 4 and c.endswith(m) and abs(len(c) - len(m)) <= 2:
        return True
    # Center-digits match: board stores only the core of a prefixed/suffixed input
    # e.g. KSBN "5384002" (input) vs "84002" (board), or "5378516022" vs "78516".
    # Check is intentionally asymmetric: the board value (c) must be a substring of
    # the input (m), not the reverse.  The reverse direction ("input digits appear
    # inside the board's longer number") fires on unrelated licenses that happen to
    # share a short digit run (e.g. DC-03919 → "03919" inside "35039195").
    if len(c) >= 5 and c in m and len(m) - len(c) <= 5:
        return True
    # Versioned credential match: both sides share a leading ≥ 5-digit group regardless of
    # renewal suffix (e.g. "40215-DI-1" vs "40215-DI-3" where the trailing cycle changes).
    m_g = re.match(r"^(\d{5,})\D", master_lic.upper().strip())
    c_g = re.match(r"^(\d{5,})\D", candidate_lic.upper().strip())
    if m_g and c_g and m_g.group(1) == c_g.group(1):
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
    # Also check with periods stripped so dotted abbreviations like "D.C.", "O.T.",
    # "P.T.", "D.P.M." match their code equivalents ("DC", "OT", "PT", "DP").
    combined_nodot = combined.replace(".", "")
    if pt in combined or pt in combined_nodot:
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
        "DP": ("PODIATRIC", "PODIATRY", "PODIATRIST"),
        "PA": ("PHYSICIAN ASSISTANT",),
        "PAS": ("PHYSICIAN ASSISTANT", "PHYSICIANS ASSISTANT", "PHYSICIAN ASSOCIATE"),
        "PAB": ("PHYSICIAN ASSISTANT", "PHYSICIANS ASSISTANT", "PHYSICIAN ASSOCIATE"),
        "RN": ("REGISTERED NURSE", "NURSING", "NURSE REGISTERED"),
        "RNA": ("NURSE ANESTHETIST", "ANESTHESIA", "CRNA", "REGISTERED NURSE", "ADVANCED PRACTICE REGISTERED NURSE"),
        "NP": ("NURSE PRACTITIONER", "ADVANCED PRACTICE", "ARNP", "APRN", "PRESCRIPTIVE AUTHORITY", "ADVANCED REGISTERED", "REGISTERED NURSE PRACTITIONER", "REGISTERED NURSE","DELEGATING NURSE","ADVANCED PRACTICE REGISTERED NURSE","CHIROPRACTOR LICENSE"),
        "NPB": ("ADVANCED PRACTICE", "ARNP", "APRN", "ADVANCED PRACTICE REGISTERED NURSE", "REGISTERED NURSE"),
        "NPS": ("PSYCHIATRIC", "MENTAL HEALTH", "ADVANCED PRACTICE", "PSYCHOLOGIST"),
        "PN": ("PRACTICAL NURSE", "LPN", "LICENSED PRACTICAL", "ADVANCED PRACTICE REGISTERED NURSE", "REGISTERED NURSE","ADV. PRACTICE NURSE - RESIDENT"),
        "GNC": ("NURSING ASSISTANT", "CERTIFIED NURSING", "CNA","GENETIC COUNSELOR", "GENETIC COUNSELING"),
        "PT": ("PHYSICAL THERAPIST", "PHYSICAL THERAPY", "PHYSICAL THERAPIST"),
        "OT": ("OCCUPATIONAL THERAPIST", "OCCUPATIONAL THERAPY"),
        "SW": ("SOCIAL WORKER", "SOCIAL WORK", "LCSW", "LCSWA", "LMSW","MENTAL HEALTH COUNSELOR"),
        "LCSW": ("LICENSED CLINICAL SOCIAL", "SOCIAL WORK"),
        "LPC": ("PROFESSIONAL COUNSELOR", "PROFESSIONAL COUNSELOR ASSOCIATE", "MENTAL HEALTH COUNSEL", "MENTAL HEALTH ASSOC", "COUNSEL", "CPC", "LICENSED ALCOHOL AND DRUG COUNSELOR", "LICENSED BEHAVIOR ANALYST", "SUBSTANCE USE DISORDER PROFESSIONAL CERTIFICATION", "LCMHC", "LCMHCA", "LCMHC ASSOCIATE", "LCMHC SUPERVISOR"),
        "LC": ("LICENSED COUNSEL", "COUNSEL", "MENTAL HEALTH COUNSEL", "REGISTERED NURSE"),
        "MFT": ("MASSAGE", "MARRIAGE", "MARITAL", "FAMILY THERAPIST", "MFT"),
        "DC": ("CHIROPRACT",),
        "DAC": ("ADDICTION COUNSEL", "DRUG ABUSE", "SUBSTANCE ABUSE", "ALCOHOL AND DRUG", "DRUG AND ALCOHOL", "SUBSTANCE USE DISORDER PROFESSIONAL CERTIFICATION"),
        "AP": ("ACUPUNCTUR", "ORIENTAL MEDICINE", "LAC", "DOM", "OMD"),
        "AU": ("AUDIOLOGIST", "AUDIOLOGY"),
        "SH": ("HEARING AID", "AUDIOLOGY", "AUDIOLOGIST", "SPEECH AND LANGUAGE PATHOLOGIST", "SPEECH LANGUAGE", "SLP", "SPEECH-LANGUAGE PATHOLOGY"),
        "ST": ("SPEECH", "SPEECH-LANGUAGE", "SPEECH LANGUAGE", "SLP",
               "SPEECH-LANGUAGE PATHOLOG", "SPEECH LANGUAGE PATHOLOG", "PERMANENT SLP"),
        "CP": ("PSYCHOLOGIST", "PSYCHOLOGY", "PROFESSIONAL COUNSELOR", "LICENSED ALCOHOL AND DRUG COUNSELOR", "SOCIAL WORKER INDEPENDENT CLINICAL LICENSE", "LCMHC", "LCMHC ASSOCIATE", "LCMHC SUPERVISOR"),
        "PC": ("PSYCHOLOGIST", "PSYCHOLOGY"),
        "PH": ("PHARMACIST", "PHARMACY",
               "MEDICAL DOCTOR", "PHYSICIAN", "OSTEOPATHIC",
               "MEDICAL BOARD"),  # OH/WY: "State Medical Board" columns for physician rows
        "PM": ("PHARMACY", "PHARMACIST"),
        "DT": ("DIETITIAN", "DIETETICS", "NUTRITIONIST", "NUTRITION","DIETETIC TECHNICIAN", "DIETITIAN CERTIFICATION"),
        "NUT": ("NUTRITIONIST", "NUTRITION"),
        "MT": ("MASSAGE", "MARRIAGE", "MARITAL", "FAMILY THERAPIST", "MFT"),
        "MW": ("MIDWIFE", "MIDWIFERY", "NURSE MIDWIFE", "ADV PRACTICE", "APRN", "CNM", "NMW", "REGISTERED NURSE"),
        "MST": ("MASSAGE",),
        "ABA": ("APPLIED BEHAVIOR", "BEHAVIOR ANALYST", "BEHAVIORAL ANALYST","APPLIED BEHAVIOR ANALYST","APPLIED BEHAVIOR ANALYSTS"),
        "OP": ("OPTICIAN", "OPTOMETRIST", "OPTOMETRY"),
        "OR": ("OPTICIAN", "DENTIST", "DENTAL", "ORAL SURGERY", "ORALOGY"),
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
    # Some boards map a legacy/old numeric ID to raw_fields["legacy_license_number"]
    # (e.g. KY_MULTIBOARD col3 "Legacy Number" vs col4 "License Number"). When the
    # input contains the legacy ID, fall through to it for scoring and gate checks.
    _raw_fields = getattr(candidate, "raw_fields", None) or {}
    c_lic_legacy = (_raw_fields.get("legacy_license_number") or "").upper().strip()
    c_lic_type = getattr(candidate, "license_type", "") or ""
    c_prof_code = getattr(candidate, "profession_code", "") or ""
    c_state = getattr(candidate, "state_code", "") or ""

    # Defense-in-depth: if c_last was stored as a bare credential suffix (e.g. "DPM",
    # "M.D."), the initial parse grabbed the wrong token. Re-split from the original
    # full name using the master last-name word count to get the real last name.
    if c_last and re.sub(r"[.\-,]", "", c_last.strip()).upper() in _NAME_SUFFIXES_NORM:
        full = getattr(candidate, "licensee_full_name", "") or ""
        if full.strip():
            c_first_new, c_last_new = _split_full_name(full, m_last)
            if c_last_new:
                c_first, c_last = c_first_new, c_last_new

    if c_first and re.sub(r"[.\-,]", "", c_first.strip()).upper() in _NAME_SUFFIXES_NORM:
        full = getattr(candidate, "licensee_full_name", "") or ""
        if full.strip():
            c_first_new, c_last_new = _split_full_name(full, m_last)
            if c_first_new and c_first_new.upper() not in _NAME_SUFFIXES_NORM:
                c_first, c_last = c_first_new, c_last_new

    # Defense-in-depth: c_first empty + c_last set is the fingerprint of a
    # single-token collapse — the extraction-layer split_full_name (no master_last)
    # stripped the last-name token as a credential suffix (e.g. "Jessica Do" →
    # strip "DO" → ["Jessica"] → returns ("", "Jessica")).
    # Re-split from the raw full name using master_last to recover the real split.
    if not c_first and c_last:
        full = getattr(candidate, "licensee_full_name", "") or ""
        if full.strip():
            c_first_new, c_last_new = _split_full_name(full, m_last)
            if c_first_new and c_last_new:
                c_first, c_last = c_first_new, c_last_new

    # Defense-in-depth: if c_first was stored as a credential type (e.g. "D.C.,"
    # for chiropractors on boards that put the credential in the first_name column),
    # fall back to the master-row first name. Normalize with the same raw-token
    # approach as _clean_matched_name (not via _normalize_name which converts dots
    # to spaces, splitting "D.C." into ["D", "C"] before the suffix check can fire).
    if c_first and re.sub(r"[.\-,]", "", c_first.strip()).upper() in _NAME_SUFFIXES_NORM:
        c_first = m_first
    # If candidate has full name but no parsed first/last, split intelligently:
    # strip suffixes, use master last-name word count for compound last names.
    if not c_first and not c_last:
        full = getattr(candidate, "licensee_full_name", "") or ""
        if full.strip():
            c_first, c_last = _split_full_name(full, m_last)

    # Single-letter initial: some boards store a bare first-name initial in the
    # first_name column.  Two sub-cases:
    # (a) Board has "LESSLER, R. WILLIAM" — the full name contains the real first
    #     name "WILLIAM" verbatim so we expand c_first to m_first for scoring.
    # (b) Board has "HAMLET , M. Lynnette" — master first "MARY" does NOT appear in
    #     the full name (only "M." does), but the initial "M" is the first letter of
    #     "MARY".  We still expand c_first → m_first so gate and scoring see the match.
    #     Safety: the gate below still requires license OR last_name to also match.
    if (m_first and c_first
            and len(re.sub(r"[.\-,\s]", "", c_first)) == 1):
        _initial_char = re.sub(r"[.\-,\s]", "", c_first).upper()
        _full_name = (getattr(candidate, "licensee_full_name", "") or "").upper()
        _m_first_norm_toks = _strip_name_affixes(_normalize_name(m_first).split())
        if m_first.upper() in _full_name.split():
            # Case (a): full first name found verbatim in board's combined name
            c_first = m_first
        elif (_initial_char and _m_first_norm_toks
              and _m_first_norm_toks[0].startswith(_initial_char)):
            # Case (b): initial matches first letter of master's first name
            c_first = m_first

    breakdown = ScoreBreakdown(weight_profile=effective_profile)

    # Score each field, weighted.
    if weights["license_numerics"] > 0:
        _lic_match = license_numerics_match(m_lic, c_lic) or (
            bool(c_lic_legacy) and license_numerics_match(m_lic, c_lic_legacy)
        )
        breakdown.license_numerics = 1.0 if _lic_match else 0.0
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
    lic_ok = (
        (license_numerics_match(m_lic, c_lic) if (m_lic and c_lic) else False)
        or (bool(m_lic and c_lic_legacy) and license_numerics_match(m_lic, c_lic_legacy))
    )
    breakdown.gate_passed = bool(first_ok and (lic_ok or last_ok))

    # Swapped-name: input(first,last) == candidate(last,first), gated on license match.
    if not breakdown.gate_passed and lic_ok:
        _sw_first = first_name_matches(m_first, c_last)
        _sw_last = last_name_matches(m_last, c_first)
        if _sw_first and _sw_last:
            breakdown.gate_passed = True
            breakdown.first_name = first_name_score(m_first, c_last)
            breakdown.last_name = last_name_score(m_last, c_first)
            breakdown.total = round(
                sum(getattr(breakdown, k) * w for k, w in weights.items()), 4
            )

    # Middle-name-as-first: some boards store middle name in the first_name field
    # (e.g. OH CSV has "HAMLET , LYNNETTE M" when master is "MARY LYNNETTE HAMLET").
    # Accept when: gate still fails, license matches, last matches, master middle = c_first.
    if not breakdown.gate_passed and lic_ok:
        _m_mid = (master_row.get("middle_name") or "").strip()
        _last_ok = last_name_matches(m_last, c_last)
        if _m_mid and first_name_matches(_m_mid, c_first) and _last_ok:
            breakdown.gate_passed = True
            breakdown.first_name = first_name_score(_m_mid, c_first)
            breakdown.last_name = last_name_score(m_last, c_last)
            breakdown.total = round(
                sum(getattr(breakdown, k) * w for k, w in weights.items()), 4
            )

    # Exact-license + last-name confirmation: the license number matches exactly and
    # the last name matches, but the first name does not. This is the fingerprint of
    # master data that stored a middle name or nickname in the first_name field
    # (e.g. master "Peters Burkhalter" vs board "Kerrigan Burkhalter", license 5479;
    # or master "Renee McCurry" vs board "Kalie McCurry", license 4891). A license
    # number is unique per board, so license + last name is a near-conclusive identity
    # match. Pass the gate but keep the honest (low) first_name score so the total lands
    # in the review band — this surfaces the board record and flags the first-name
    # discrepancy instead of giving up. NOTE: this does NOT fire when the last name is
    # also corrupted (both name fields wrong), which correctly stays a manual case.
    if not breakdown.gate_passed and lic_ok and last_ok:
        breakdown.gate_passed = True

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
    gate_passers.sort(key=lambda x: (x[1].license_numerics, x[1].total), reverse=True)

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
        # License anchor: exact license + EXACT first name match → accept regardless
        # of last name. Handles name-change cases (e.g. "Duric Zinka" → board has
        # "LEWANDOWSKI, ZINKA D") where last name differs but license is definitive.
        # first_name == 1.0 means exact or nickname match only (fuzzy first-name mismatches
        # are routed to manual review). The last_name >= 0.4 guard prevents anchoring on
        # a completely different person when multiple licenses share the same numeric digits
        # but different type prefixes (e.g. "FD.009648" vs "PT.009648"). The provider_type
        # > 0.0 arm handles marriage/divorce name-change cases: same person, same license
        # type, exact first-name match, but completely different last name. Requiring
        # provider_type > 0.0 ensures we do NOT anchor when the board record has an
        # unknown/empty license type (which could indicate a different person with
        # coincidentally shared digits).
        if (top_bd.weight_profile == "license_present"
                and top_bd.license_numerics == 1.0
                and top_bd.first_name == 1.0
                and (top_bd.last_name >= 0.4
                     or top_bd.provider_type > 0.0)):
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
        # Tiebreaker 1: candidate whose license matches the input wins.
        # Checked before provider_type because the license number is a stronger
        # identifier than the license-type label on the board record.
        m_lic = (master_row.get("license_id") or "")
        if m_lic:
            _top_raw = (getattr(top_cand, "raw_fields", None) or {})
            _sec_raw = (getattr(second_cand, "raw_fields", None) or {})
            top_lic_ok = (
                license_numerics_match(m_lic, getattr(top_cand, "license_number", "") or "")
                or license_numerics_match(m_lic, _top_raw.get("legacy_license_number") or "")
            )
            second_lic_ok = (
                license_numerics_match(m_lic, getattr(second_cand, "license_number", "") or "")
                or license_numerics_match(m_lic, _sec_raw.get("legacy_license_number") or "")
            )
            if top_lic_ok and not second_lic_ok:
                return DisambiguationVerdict(
                    status="selected", best=top_cand, best_breakdown=top_bd,
                    gate_passers=[c for c, _ in gate_passers],
                    all_breakdowns=breakdowns, tiebreaker_used=True,
                )
            if second_lic_ok and not top_lic_ok:
                return DisambiguationVerdict(
                    status="selected", best=second_cand, best_breakdown=second_bd,
                    gate_passers=[c for c, _ in gate_passers],
                    all_breakdowns=breakdowns, tiebreaker_used=True,
                )
        # Tiebreaker 1b: active licence beats a non-active term of the SAME credential.
        # A provider often appears more than once: an associate/old term and the
        # current full/renewed term. These share one certificate number (e.g. the
        # associate "A17151" now "Transitioned" + the full "17151" now "Active") or the
        # exact same name. When the two tied candidates are the same person, always
        # prefer the record whose status is ACTIVE over any non-active status
        # (Transitioned, Expired, Inactive, Pending, Unknown, …). Requiring same-person
        # keeps this from choosing an active stranger over an inactive genuine match.
        def _is_active(cand) -> bool:
            st = getattr(cand, "status", None)
            return str(getattr(st, "value", st) or "").lower() == "active"

        _top_num = _numeric_only(getattr(top_cand, "license_number", "") or "")
        _sec_num = _numeric_only(getattr(second_cand, "license_number", "") or "")
        _top_full = _normalize_name(getattr(top_cand, "licensee_full_name", "") or "")
        _sec_full = _normalize_name(getattr(second_cand, "licensee_full_name", "") or "")
        _same_person = (
            (bool(_top_num) and _top_num == _sec_num)
            or (bool(_top_full) and _top_full == _sec_full)
        )
        if _same_person and _is_active(top_cand) and not _is_active(second_cand):
            return DisambiguationVerdict(
                status="selected", best=top_cand, best_breakdown=top_bd,
                gate_passers=[c for c, _ in gate_passers],
                all_breakdowns=breakdowns, tiebreaker_used=True,
            )
        if _same_person and _is_active(second_cand) and not _is_active(top_cand):
            return DisambiguationVerdict(
                status="selected", best=second_cand, best_breakdown=second_bd,
                gate_passers=[c for c, _ in gate_passers],
                all_breakdowns=breakdowns, tiebreaker_used=True,
            )
        # Tiebreaker 2: middle initial match. When the input has a middle name/initial
        # and one candidate's full name carries the matching initial, prefer that candidate.
        # This resolves same-first-last ties like "Sarah C Fuller" where one board record
        # is "Sarah Catherine Fuller" and another is "Sarah Elizabeth Fuller".
        m_mid = (master_row.get("middle_name") or "").strip()
        if m_mid:
            m_mid_initial = m_mid[0].upper()
            _m_last_words = len((master_row.get("last_name") or "").split())

            def _cand_middle_initial(cand) -> str:
                full = (getattr(cand, "licensee_full_name", "") or "").strip()
                if not full:
                    return ""
                parts = full.split()
                # middle tokens: after first token, before last _m_last_words tokens
                mid_tokens = parts[1: max(1, len(parts) - _m_last_words)]
                return mid_tokens[0][0].upper() if mid_tokens else ""

            top_mid_ok = _cand_middle_initial(top_cand) == m_mid_initial
            sec_mid_ok = _cand_middle_initial(second_cand) == m_mid_initial
            if top_mid_ok and not sec_mid_ok:
                return DisambiguationVerdict(
                    status="selected", best=top_cand, best_breakdown=top_bd,
                    gate_passers=[c for c, _ in gate_passers],
                    all_breakdowns=breakdowns, tiebreaker_used=True,
                )
            if sec_mid_ok and not top_mid_ok:
                return DisambiguationVerdict(
                    status="selected", best=second_cand, best_breakdown=second_bd,
                    gate_passers=[c for c, _ in gate_passers],
                    all_breakdowns=breakdowns, tiebreaker_used=True,
                )
        # Tiebreaker 3: candidate whose provider_type matches wins.
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
        # Close scores AND all tiebreakers indeterminate → narrow; ladder may apply_narrowing.
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

    # Step 4: prefer active/non-expired over inactive/expired candidates.
    # When the same person has two records (e.g. an active OH license and an
    # expired out-of-state one), the active record is the correct match.
    import datetime as _dt
    _today = _dt.date.today()

    def _is_active(c: Any) -> bool:
        status = (getattr(c, "status", None) or "").upper()
        if status in ("ACTIVE", "ACTIVE - CURRENT", "CURRENT"):
            return True
        exp = getattr(c, "expiration_date", None)
        if exp:
            try:
                if isinstance(exp, str):
                    for _fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
                        try:
                            exp = _dt.datetime.strptime(exp, _fmt).date()
                            break
                        except ValueError:
                            continue
                if isinstance(exp, (_dt.date, _dt.datetime)):
                    return exp >= _today
            except Exception:
                pass
        return False

    step4 = [c for c in pool if _is_active(c)]
    if len(step4) == 1:
        return step4, "selected"
    if step4:
        pool = step4

    return pool, "ambiguous"
