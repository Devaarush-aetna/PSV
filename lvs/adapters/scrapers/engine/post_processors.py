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


def split_full_name(full_name: str) -> tuple[str, str]:
    """Split 'Last, First Middle' or 'First Last' into (first, last)."""
    name = clean_name(full_name)
    if not name:
        return ("", "")
    if "," in name:
        parts = name.split(",", 1)
        last = parts[0].strip()
        first = parts[1].strip().split()[0] if parts[1].strip() else ""
        return (first, last)
    parts = name.split()
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
