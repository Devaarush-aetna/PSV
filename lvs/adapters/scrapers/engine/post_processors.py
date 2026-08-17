"""Date parsing, status normalization, and name utilities."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

from .models import LicenseStatus

_DEFAULT_DATE_FORMATS = [
    "%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y",
    "%B %d, %Y", "%b %d, %Y", "%m/%d/%y",
]


def parse_date(value: str | None, formats: list[str] | None = None) -> Optional[date]:
    if not value:
        return None
    value = value.strip()
    # Strip trailing time component — boards like KY return "2/28/2027 0:00:00"
    # which standard date formats don't cover.
    if " " in value and re.match(r".*\d{1,2}:\d{2}", value):
        value = value.split(" ")[0]
    for fmt in (formats or _DEFAULT_DATE_FORMATS):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def normalize_status(value: str | None, status_map: dict[str, str] | None = None) -> LicenseStatus:
    if not value:
        return LicenseStatus.UNKNOWN
    normalized = value.strip().lower()
    smap = {k.lower(): v for k, v in (status_map or {}).items()}
    mapped = smap.get(normalized)
    if mapped:
        try:
            return LicenseStatus(mapped.lower())
        except ValueError:
            pass
    for member in LicenseStatus:
        if member.value in normalized:
            return member
    return LicenseStatus.UNKNOWN


def clean_name(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value.strip())


# Trailing tokens to strip before extracting a last name from a full-name string.
# Boards frequently append credentials (MD, DPM, RN) and generational suffixes (Jr, III)
# after the last name. Stored de-dotted/de-hyphenated — matching normalizes tokens the
# same way before lookup.
_NAME_SUFFIX_SET: frozenset[str] = frozenset({
    # Generational / legal
    "II", "III", "IV", "V", "JR", "SR", "ESQ",
    # Business-entity suffixes (boards occasionally list practice entities)
    "LLC", "LLP", "PLLC", "PC", "PA",  # NB: "PA" also = physician assistant
    # Medical degrees
    "MD", "DO", "DPM", "DDS", "DMD", "OD", "PHD", "PSYD",
    "DPT", "DC", "ND",
    # Nursing / advanced practice
    "RN", "LPN", "LVN", "APRN", "DNP", "CNM", "NP",
    # Behavioral health
    "LCSW", "LMFT", "LPC", "LCPC", "LMHC", "BCBA", "BCABAD", "BCABA", "RBT",
    "LGSW", "LMSW", "CSW",
    # PT / OT / SLP / AUD (incl. assistant and registered variants)
    "PT", "PTA", "LPT",
    "OT", "OTA", "OTR", "OTRL", "COTA", "COTAL",
    "SLP", "AUD", "ST", "STA",
    # Respiratory care
    "RCP", "LRCP", "RRT", "CRT",
    # Genetic counseling
    "LGC", "CGC", "GC",
    # Dietetics / nutrition / massage
    "RDN", "LMT", "MST",
    # Pharmacy
    "PHARMD", "RPH",
    # Fellowship designations
    "FACP", "FACS", "FACOG", "FAAP",
})


def _norm_token(token: str) -> str:
    """Normalize a name token for suffix lookup: drop all non-alphanumerics
    (dots, dashes, and stray commas) and upper-case. 'OT-A' → 'OTA', 'Jr.,' → 'JR'."""
    return re.sub(r"[^A-Za-z0-9]", "", token).upper()


def _is_suffix_token(token: str) -> bool:
    return _norm_token(token) in _NAME_SUFFIX_SET


def _strip_name_suffixes(parts: list[str]) -> list[str]:
    """Pop trailing credential/generational tokens from a name parts list."""
    while parts and _is_suffix_token(parts[-1]):
        parts = parts[:-1]
    return parts


def split_full_name(full_name: str) -> tuple[str, str]:
    """Split a board-rendered name into (first, last).

    Handles both orderings seen across boards:
      - 'Last, First Middle'                 → ('First', 'Last')
      - 'First Middle Last, CREDENTIAL(s)'   → ('First', 'Last')
      - 'First Last, Jr., M.D.'              → ('First', 'Last')
      - 'First Middle Last' (no comma)       → ('First', 'Last')

    The comma is ambiguous: it may separate Last-from-First, or merely separate
    trailing credential/generational suffixes (MD, OT-A, LRCP, ST, Jr., LLC, …)
    from a natural 'First … Last' name. We resolve this by splitting on commas and
    discarding trailing segments that are composed *entirely* of suffix tokens; if
    only one real segment remains, the comma was a credential separator, not a
    name-order separator.
    """
    name = clean_name(full_name)
    if not name:
        return ("", "")

    if "," in name:
        segments = [s.strip() for s in name.split(",") if s.strip()]
        # Drop trailing segments that are purely credential/suffix tokens
        # (e.g. "Charles Reeves, Jr., M.D." → drop "Jr." and "M.D.").
        while len(segments) > 1 and all(_is_suffix_token(t) for t in segments[-1].split()):
            segments.pop()

        if len(segments) <= 1:
            # Comma only separated credentials → remaining text is 'First … Last'.
            parts = _strip_name_suffixes((segments[0] if segments else "").split())
            if not parts:
                return ("", "")
            if len(parts) == 1:
                return ("", parts[0])
            return (parts[0], parts[-1])

        # Two or more real segments → 'Last, First Middle [, Credentials]'.
        parts_last = _strip_name_suffixes(segments[0].split())
        last = " ".join(parts_last) if parts_last else segments[0]
        rest_parts = _strip_name_suffixes(" ".join(segments[1:]).split())
        if not rest_parts:
            # Nothing usable after the comma — fall back to treating segment 0
            # as a whole 'First Last' name.
            if len(parts_last) >= 2:
                return (parts_last[0], parts_last[-1])
            return ("", last)
        return (rest_parts[0], last)

    parts = _strip_name_suffixes(name.split())
    if not parts:
        return ("", "")
    if len(parts) == 1:
        return ("", parts[0])
    return (parts[0], parts[-1])


def apply_field_map(raw: dict, field_map: dict[str, str]) -> dict:
    """Map raw scraped keys to canonical field names; keep originals as fallback."""
    result: dict = {}
    # Normalize both sides: lowercase + strip trailing colon (many boards render
    # labels as "Field Name:" in HTML, which would otherwise cause misses).
    normalized_map = {k.strip().rstrip(":").strip().lower(): v for k, v in field_map.items()}
    for key, val in raw.items():
        canonical = normalized_map.get(key.strip().rstrip(":").strip().lower())
        if canonical:
            result[canonical] = val
        else:
            result[key] = val
    return result
