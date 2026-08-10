"""LicenseRecord serialization — JSON file output + SQLite upsert.

Output JSON files land at:
    PSV_DEV/Output/{YYYYMM}/{run_id}/{source_id}/{source_id}_{YYYYMMDD_HHMM}.json
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import aiosqlite

from .models import LicenseRecord, LicenseStatus, OutputConfig, SiteConfig
from .post_processors import (
    normalize_status,
    parse_date,
    split_full_name,
)

log = logging.getLogger(__name__)

# PSV_DEV/ — engine/output.py → engine/ → scrapers/ → adapters/ → lvs/ → PSV_DEV/
_PROJECT_ROOT = Path(__file__).resolve().parents[4]


def _next_annual_date(mmdd: str) -> date:
    """Return the next occurrence (on or after today) of the given MM-DD date.

    Used for boards where every license expires on the same calendar day each
    year (e.g. KY_OD: "03-01" → always next March 1st).
    """
    month, day = (int(p) for p in mmdd.split("-"))
    today = date.today()
    candidate = date(today.year, month, day)
    if candidate < today:
        candidate = date(today.year + 1, month, day)
    return candidate


def _json_default(obj):
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def _derive_profession_code(
    license_type: str,
    profession_codes: list[str],
    profession_code_map: dict[str, str] | None = None,
) -> str | None:
    if not profession_codes:
        return None
    lt_upper = (license_type or "").upper().strip()
    if profession_code_map and lt_upper:
        mapped = profession_code_map.get(lt_upper)
        if mapped:
            return mapped
    if len(profession_codes) > 1 and lt_upper:
        for pc in profession_codes:
            if pc.upper() in lt_upper:
                return pc
    if len(profession_codes) == 1:
        return profession_codes[0]
    return None


def map_to_license_record(
    raw: dict,
    config: SiteConfig,
    evidence: dict | None = None,
) -> LicenseRecord:
    """Map a raw extracted dict to a canonical LicenseRecord using config.output."""
    evidence = evidence or {}
    out = config.output
    mapping = out.license_record

    def resolve(template: str) -> Optional[str]:
        if not template:
            return None
        if template.startswith("{") and template.endswith("}"):
            key = template[1:-1]
            val = raw.get(key)
            return str(val).strip() if val is not None else None
        return template

    # Resolve from output.license_record template first, then fall back to
    # the canonical field names produced by apply_field_map (detail.field_map).
    full_name = resolve(mapping.get("licensee_full_name", "")) or raw.get("full_name", "")
    first = resolve(mapping.get("licensee_first_name", "")) or raw.get("first_name", "") or ""
    last = resolve(mapping.get("licensee_last_name", "")) or raw.get("last_name", "") or ""

    if full_name and not (first or last):
        first, last = split_full_name(full_name)
    elif not full_name and (first or last):
        full_name = " ".join(filter(None, [first, last]))

    license_number = resolve(mapping.get("license_number", "")) or raw.get("license_number", "")
    status_raw = resolve(mapping.get("status", "")) or raw.get("status", "")
    eff_raw = resolve(mapping.get("effective_date", "")) or raw.get("effective_date")
    exp_raw = resolve(mapping.get("expiration_date", "")) or raw.get("expiration_date")
    issue_raw = resolve(mapping.get("issue_date", "")) or raw.get("issue_date")

    # Board-level fixed annual expiration: fill in when the record has none.
    _parsed_exp = parse_date(exp_raw, out.date_formats)
    if _parsed_exp is None and out.fixed_annual_expiration_mmdd:
        _parsed_exp = _next_annual_date(out.fixed_annual_expiration_mmdd)

    disc = raw.get("disciplinary_actions", [])
    if isinstance(disc, str):
        disc = [{"raw": disc}] if disc.strip() else []

    return LicenseRecord(
        source_id=config.identity.source_id,
        license_number=license_number or "",
        licensee_first_name=first or None,
        licensee_last_name=last or None,
        licensee_full_name=full_name or None,
        licensee_middle_name=(
            resolve(mapping.get("licensee_middle_name", "")) or raw.get("licensee_middle_name", "") or None
        ),
        licensee_suffix=resolve(mapping.get("licensee_suffix", "")) or raw.get("licensee_suffix") or None,
        license_type=resolve(mapping.get("license_type", "")) or raw.get("license_type") or None,
        profession_code=_derive_profession_code(
            resolve(mapping.get("license_type", "")) or raw.get("license_type") or "",
            config.identity.profession_codes,
            profession_code_map=getattr(config.identity, "profession_code_map", None),
        ),
        status=normalize_status(status_raw, out.status_map),
        effective_date=parse_date(eff_raw, out.date_formats),
        expiration_date=_parsed_exp,
        issue_date=parse_date(issue_raw, out.date_formats),
        address=resolve(mapping.get("address", "")) or raw.get("address") or None,
        city=resolve(mapping.get("city", "")) or raw.get("city") or None,
        state_code=resolve(mapping.get("state_code", "")) or raw.get("state_code") or None,
        zip_code=resolve(mapping.get("zip_code", "")) or raw.get("zip_code") or None,
        disciplinary_actions=disc,
        out_of_state_state=raw.get("out_of_state_state") or None,
        source_url=raw.get("_source_url", ""),
        scraped_at=datetime.utcnow(),
        evidence_html_path=evidence.get("html_path"),
        evidence_screenshot_path=evidence.get("screenshot_path"),
        raw_fields=raw,
        used_ai=raw.get("_used_ai", False),
    )


def _yyyymm(run_id: str) -> str:
    if len(run_id) >= 6 and run_id[:8].isdigit():
        return f"{run_id[:4]}{run_id[4:6]}"
    return datetime.now().strftime("%Y%m")


def _dt(run_id: str) -> str:
    if (len(run_id) >= 13 and run_id[:8].isdigit()
            and run_id[8] == "_" and run_id[9:13].isdigit()):
        return run_id[:13]
    return run_id


def resolve_output_path(source_id: str, run_id: str) -> Path:
    """Return Output/{YYYYMM}/{run_id}/{source_id}/{source_id}_{dt}.json"""
    return (_PROJECT_ROOT / "Output" / _yyyymm(run_id)
            / run_id / source_id / f"{source_id}_{_dt(run_id)}.json")


async def write_output(records: list[LicenseRecord], source_id: str, run_id: str) -> str:
    """Write records to PSV_DEV/Output/{source_id}/{run_id}.json. Returns path written."""
    out_path = resolve_output_path(source_id, run_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    data = [r.model_dump() for r in records]
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=_json_default)
    log.info("Wrote %d records → %s", len(records), out_path)
    return str(out_path)


async def upsert_to_db(db: aiosqlite.Connection, records: list[LicenseRecord]) -> None:
    for r in records:
        await db.execute(
            """INSERT INTO license_records (
                source_id, license_number,
                licensee_first_name, licensee_last_name, licensee_full_name,
                licensee_middle_name, licensee_suffix,
                license_type, profession_code, status,
                effective_date, expiration_date, issue_date, last_renewal_date,
                address, city, state_code, zip_code,
                disciplinary_actions, source_url, scraped_at,
                evidence_html_path, evidence_screenshot_path, raw_fields, used_ai
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source_id, license_number) DO UPDATE SET
                licensee_first_name=excluded.licensee_first_name,
                licensee_last_name=excluded.licensee_last_name,
                licensee_full_name=excluded.licensee_full_name,
                license_type=excluded.license_type,
                status=excluded.status,
                effective_date=excluded.effective_date,
                expiration_date=excluded.expiration_date,
                issue_date=excluded.issue_date,
                address=excluded.address,
                city=excluded.city,
                state_code=excluded.state_code,
                zip_code=excluded.zip_code,
                disciplinary_actions=excluded.disciplinary_actions,
                scraped_at=excluded.scraped_at,
                evidence_html_path=excluded.evidence_html_path,
                evidence_screenshot_path=excluded.evidence_screenshot_path,
                raw_fields=excluded.raw_fields,
                used_ai=excluded.used_ai""",
            (
                r.source_id, r.license_number,
                r.licensee_first_name, r.licensee_last_name, r.licensee_full_name,
                r.licensee_middle_name, r.licensee_suffix,
                r.license_type, r.profession_code, r.status.value,
                r.effective_date.isoformat() if r.effective_date else None,
                r.expiration_date.isoformat() if r.expiration_date else None,
                r.issue_date.isoformat() if r.issue_date else None,
                r.last_renewal_date.isoformat() if r.last_renewal_date else None,
                r.address, r.city, r.state_code, r.zip_code,
                json.dumps(r.disciplinary_actions),
                r.source_url, r.scraped_at.isoformat(),
                r.evidence_html_path, r.evidence_screenshot_path,
                json.dumps(r.raw_fields, default=str),
                int(r.used_ai),
            ),
        )
    await db.commit()
    log.info("Upserted %d records to license_records table", len(records))
