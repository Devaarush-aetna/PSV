"""LicenseRecord serialization — JSON file output + SQLite upsert."""
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


def _json_default(obj):
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


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
        profession_code=config.identity.profession_codes[0] if config.identity.profession_codes else None,
        status=normalize_status(status_raw, out.status_map),
        effective_date=parse_date(eff_raw, out.date_formats),
        expiration_date=parse_date(exp_raw, out.date_formats),
        issue_date=parse_date(issue_raw, out.date_formats),
        address=resolve(mapping.get("address", "")) or raw.get("address") or None,
        city=resolve(mapping.get("city", "")) or raw.get("city") or None,
        state_code=resolve(mapping.get("state_code", "")) or raw.get("state_code") or None,
        zip_code=resolve(mapping.get("zip_code", "")) or raw.get("zip_code") or None,
        disciplinary_actions=disc,
        source_url=raw.get("_source_url", ""),
        scraped_at=datetime.utcnow(),
        evidence_html_path=evidence.get("html_path"),
        evidence_screenshot_path=evidence.get("screenshot_path"),
        raw_fields=raw,
        used_ai=raw.get("_used_ai", False),
    )


async def write_output(records: list[LicenseRecord], output_path: str) -> None:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    data = [r.model_dump() for r in records]
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=_json_default)
    log.info("Wrote %d records to %s", len(records), output_path)


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
                status=excluded.status,
                expiration_date=excluded.expiration_date,
                scraped_at=excluded.scraped_at,
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
