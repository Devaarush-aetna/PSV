"""NPPES Registry API client — universal NPI lookup with 30-day file cache.

Lookup key is the input row's NPI_NO Excel column. Cache lives at
PSV_DEV/PSV/Cache/NPPES/{npi}.json.  Honors PROXY env var via httpx.

NppesRecord exposes flattened canonical fields PLUS the raw JSON for the
nppes channel dump.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx

from . import config as cfg

log = logging.getLogger(__name__)

_FETCH_OK = "ok"
_FETCH_NOT_FOUND = "not_found"
_FETCH_ERROR = "http_error"
_FETCH_EMPTY_INPUT = "empty_input"


@dataclass
class NppesRecord:
    npi: str
    first_name: str = ""
    last_name: str = ""
    middle_name: Optional[str] = None
    credential: Optional[str] = None
    other_names: list[dict] = field(default_factory=list)
    license_numbers: list[dict] = field(default_factory=list)
    addresses: list[dict] = field(default_factory=list)
    taxonomies: list[dict] = field(default_factory=list)
    primary_taxonomy_code: str = ""
    primary_taxonomy_desc: str = ""
    raw: dict = field(default_factory=dict)
    fetch_status: str = _FETCH_OK

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def primary_license(self) -> dict:
        return self.license_numbers[0] if self.license_numbers else {}


def _proxy_url() -> Optional[str]:
    """Resolve a proxy URL from env. Same precedence as engine/proxy.py."""
    for k in ("LVS_PROXY_SERVER", "PROXY"):
        val = os.environ.get(k, "").strip()
        if val:
            return val if val.startswith(("http://", "https://")) else f"http://{val}"
    return None


def _cache_path(npi: str) -> Path:
    cfg.NPPES_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    return cfg.NPPES_CACHE_ROOT / f"{npi}.json"


def _cache_is_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    age_days = (time.time() - path.stat().st_mtime) / 86400.0
    return age_days <= cfg.NPPES_CACHE_DAYS


def _parse_result(npi: str, raw: dict) -> NppesRecord:
    """Map an NPPES API result item to NppesRecord."""
    results = raw.get("results") or []
    if not results:
        return NppesRecord(npi=npi, raw=raw, fetch_status=_FETCH_NOT_FOUND)
    item = results[0]

    basic = item.get("basic", {}) or {}
    first_name = basic.get("first_name", "") or ""
    last_name = basic.get("last_name", "") or basic.get("name", "")
    middle_name = basic.get("middle_name") or None
    credential = basic.get("credential") or None

    # Other names (Org / Sole Proprietor / Other Name records)
    other_names: list[dict] = []
    for o in item.get("other_names", []) or []:
        if isinstance(o, dict):
            other_names.append({
                "type": o.get("type"),
                "code": o.get("code"),
                "first_name": o.get("first_name"),
                "last_name": o.get("last_name") or o.get("organization_name"),
                "credential": o.get("credential"),
            })

    # State licenses on the record (Identifiers section in NPPES)
    license_numbers: list[dict] = []
    for ident in item.get("identifiers", []) or []:
        if not isinstance(ident, dict):
            continue
        # NPPES identifier categories: code "05" = MEDICARE, "06" = MEDICAID,
        # "08" = Other (often state license). Keep all; consumer can filter.
        license_numbers.append({
            "number": ident.get("identifier"),
            "state": ident.get("state"),
            "issuer": ident.get("issuer"),
            "code": ident.get("code"),
            "desc": ident.get("desc"),
        })

    # Addresses
    addresses: list[dict] = []
    for a in item.get("addresses", []) or []:
        if isinstance(a, dict):
            addresses.append({
                "purpose": a.get("address_purpose"),
                "address_1": a.get("address_1"),
                "address_2": a.get("address_2"),
                "city": a.get("city"),
                "state": a.get("state"),
                "postal_code": a.get("postal_code"),
                "country_code": a.get("country_code"),
            })

    # Taxonomies (primary first)
    taxonomies: list[dict] = []
    primary_code = ""
    primary_desc = ""
    for t in item.get("taxonomies", []) or []:
        if not isinstance(t, dict):
            continue
        entry = {
            "code": t.get("code"),
            "desc": t.get("desc"),
            "primary": bool(t.get("primary")),
            "state": t.get("state"),
            "license": t.get("license"),
        }
        taxonomies.append(entry)
        if entry["primary"] and not primary_code:
            primary_code = entry["code"] or ""
            primary_desc = entry["desc"] or ""
    if not primary_code and taxonomies:
        primary_code = taxonomies[0].get("code") or ""
        primary_desc = taxonomies[0].get("desc") or ""

    # Also pull state-license entries from taxonomies (richer source than identifiers)
    for t in taxonomies:
        if t.get("license"):
            license_numbers.append({
                "number": t.get("license"),
                "state": t.get("state"),
                "issuer": None,
                "code": t.get("code"),
                "desc": t.get("desc"),
            })

    return NppesRecord(
        npi=str(item.get("number") or npi),
        first_name=first_name,
        last_name=last_name,
        middle_name=middle_name,
        credential=credential,
        other_names=other_names,
        license_numbers=license_numbers,
        addresses=addresses,
        taxonomies=taxonomies,
        primary_taxonomy_code=primary_code,
        primary_taxonomy_desc=primary_desc,
        raw=item,
        fetch_status=_FETCH_OK,
    )


async def fetch_provider(npi_no: str) -> Optional[NppesRecord]:
    """Universal NPPES lookup by NPI_NO. Returns None for missing/empty input
    so the caller can skip with reason='npi_no_missing'. Returns NppesRecord
    with fetch_status='not_found' when NPPES returns 0 results, and
    fetch_status='http_error' on network failures.
    """
    npi = (npi_no or "").strip()
    if not npi or not npi.isdigit() or len(npi) != 10:
        return None  # caller writes nppes row with status='empty_input'

    path = _cache_path(npi)
    if _cache_is_fresh(path):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return _parse_result(npi, raw)
        except Exception as exc:
            log.warning("NPPES cache read failed for %s: %s", npi, exc)

    params = {"number": npi, "version": cfg.NPPES_API_VERSION}
    proxy = _proxy_url()
    client_kwargs: dict[str, Any] = {"timeout": cfg.NPPES_TIMEOUT_S}
    if proxy:
        client_kwargs["proxy"] = proxy

    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.get(cfg.NPPES_API_URL, params=params)
            resp.raise_for_status()
            raw = resp.json()
    except Exception as exc:
        log.warning("NPPES fetch failed for %s: %s", npi, exc)
        return NppesRecord(npi=npi, fetch_status=_FETCH_ERROR, raw={"error": str(exc)})

    # Cache the raw response
    try:
        path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("NPPES cache write failed for %s: %s", npi, exc)

    return _parse_result(npi, raw)


# --------------------------------------------------------------------------
# Discrepancy diff (master row vs NPPES record)
# --------------------------------------------------------------------------

@dataclass
class NpiDiscrepancy:
    differing_fields: dict[str, tuple[str, str]] = field(default_factory=dict)
    extra_nppes_licenses: list[dict] = field(default_factory=list)
    extra_nppes_addresses: list[dict] = field(default_factory=list)
    nppes_credential: Optional[str] = None
    other_name_used: bool = False

    def is_empty(self) -> bool:
        return not (self.differing_fields or self.extra_nppes_licenses)

    def to_dict(self) -> dict[str, Any]:
        return {
            "differing_fields": {k: list(v) for k, v in self.differing_fields.items()},
            "extra_nppes_licenses": self.extra_nppes_licenses,
            "extra_nppes_addresses": self.extra_nppes_addresses,
            "nppes_credential": self.nppes_credential,
            "other_name_used": self.other_name_used,
        }


def _norm_for_compare(s: str) -> str:
    return (s or "").strip().upper()


def diff_master_vs_nppes(master_row: dict, nppes: NppesRecord) -> NpiDiscrepancy:
    """Field-level diff. Used to drive the NPPES targeted-retry ladder."""
    diff = NpiDiscrepancy()
    if nppes is None or nppes.fetch_status != _FETCH_OK:
        return diff

    # First name
    m_first = _norm_for_compare(master_row.get("first_name", ""))
    n_first = _norm_for_compare(nppes.first_name)
    if m_first and n_first and m_first != n_first:
        diff.differing_fields["first_name"] = (master_row.get("first_name", ""), nppes.first_name)

    # Last name
    m_last = _norm_for_compare(master_row.get("last_name", ""))
    n_last = _norm_for_compare(nppes.last_name)
    if m_last and n_last and m_last != n_last:
        # Check if master last name matches an other_name entry first
        other_hit = any(_norm_for_compare(o.get("last_name", "")) == m_last
                        for o in nppes.other_names)
        if other_hit:
            diff.other_name_used = True
        else:
            diff.differing_fields["last_name"] = (master_row.get("last_name", ""), nppes.last_name)

    # License number: master license vs NPPES licenses (any match → no diff)
    import re as _re
    m_lic = _re.sub(r"\D", "", master_row.get("license_id", "") or "")
    nppes_lic_digits = [_re.sub(r"\D", "", str(l.get("number") or "")) for l in nppes.license_numbers]
    nppes_lic_digits = [d for d in nppes_lic_digits if d]
    if m_lic and nppes_lic_digits:
        if m_lic not in nppes_lic_digits:
            # NPPES has license(s) but none match master → record diff; expose extras
            primary = next((str(l.get("number") or "") for l in nppes.license_numbers if l.get("number")), "")
            diff.differing_fields["license_number"] = (master_row.get("license_id", ""), primary)
            diff.extra_nppes_licenses = [l for l in nppes.license_numbers if l.get("number")]
    elif not m_lic and nppes_lic_digits:
        # Master has no license but NPPES does — surface as extras to try
        diff.extra_nppes_licenses = [l for l in nppes.license_numbers if l.get("number")]

    diff.nppes_credential = nppes.credential

    # All NPPES addresses are "extra" context (we don't store master addresses)
    diff.extra_nppes_addresses = nppes.addresses

    return diff
