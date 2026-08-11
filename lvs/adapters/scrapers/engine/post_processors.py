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
    # Medical degrees
    "MD", "DO", "DPM", "DDS", "DMD", "OD", "PHD", "PSYD",
    "DPT", "DC", "ND",
    # Nursing / advanced practice
    "RN", "LPN", "LVN", "APRN", "DNP", "CNM", "NP",
    # PA
    "PA",
    # Behavioral health
    "LCSW", "LMFT", "LPC", "LCPC", "LMHC", "BCBA", "BCABAD", "BCABA", "RBT",
    # PT / OT / SLP / AUD
    "PT", "OT", "SLP", "AUD",
    # Pharmacy
    "PHARMD", "RPH",
    # Fellowship designations
    "FACP", "FACS", "FACOG", "FAAP",
})


def _strip_name_suffixes(parts: list[str]) -> list[str]:
    """Pop trailing credential/generational tokens from a name parts list."""
    while parts and re.sub(r"[.\-]", "", parts[-1]).upper() in _NAME_SUFFIX_SET:
        parts = parts[:-1]
    return parts


def split_full_name(full_name: str) -> tuple[str, str]:
    """Split 'Last, First Middle' or 'First Last' into (first, last).

    Strips trailing credential/generational suffixes (MD, DPM, Jr., etc.) so that
    boards appending credentials after the name (e.g. 'Victor McNamara DPM') don't
    end up with the credential stored as the last name.
    """
    name = clean_name(full_name)
    if not name:
        return ("", "")
    if "," in name:
        raw_last, _, rest = name.partition(",")
        parts_last = _strip_name_suffixes(raw_last.strip().split())
        last = " ".join(parts_last)
        # Strip credential suffixes from the rest portion too. If all tokens after
        # the comma are credentials (e.g. "BAILEY SHEVENELL, PA"), the comma separates
        # "First Last" from a credential — raw_last holds "First Last", not just "Last".
        rest_parts = _strip_name_suffixes(rest.strip().split())
        if not rest_parts:
            # "First Last, Credential" format: split parts_last into first/last.
            if len(parts_last) >= 2:
                return (parts_last[0], parts_last[-1])
            return ("", last)
        first = rest_parts[0]
        return (first, last)
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
