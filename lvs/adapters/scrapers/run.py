"""
Universal scraper CLI — driven entirely by per-board config.yaml files.

Usage:
  python run.py --config sites/NV_MEDBOARD/config.yaml --mode license_number --query "12345"
  python run.py --config sites/NV_CHIRO/config.yaml   --mode last_name      --query "Smith" --headed
  python run.py --config sites/MA_HEALTH/config.yaml  --mode name            --query "Smith" --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add parent to path so engine imports work when run from this directory
sys.path.insert(0, str(Path(__file__).parent))

from engine.models import SearchQuery, SiteConfig
from engine.output import write_output
from engine.telemetry import init_db
from engine.validate import load_config

# Archetype dispatcher — verify_license is now implemented in archetypes/dispatcher.py.
# We import and re-export it here so existing callers (psv_test.py, smoke_all.py,
# excel_runner.py) that do `from run import verify_license` continue to work unchanged.
from archetypes.dispatcher import verify_license  # noqa: F401  (re-export)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("run")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args():
    p = argparse.ArgumentParser(description="LVS Universal License Scraper")
    p.add_argument("--config", required=True, help="Path to board config.yaml")
    p.add_argument("--mode", default=None, help="Search mode (e.g. license_number, last_name, name). Optional when structured fields are provided.")
    p.add_argument("--query", default=None, help="Search string for legacy single-mode invocation.")
    p.add_argument("--license-number", dest="license_number", default=None, help="License number value")
    p.add_argument("--first-name", dest="first_name", default=None, help="First name value")
    p.add_argument("--middle-name", dest="middle_name", default=None, help="Middle name value (optional; used in first_mid_last / license_first_mid_last combos)")
    p.add_argument("--last-name", dest="last_name", default=None, help="Last name value")
    p.add_argument("--license-type", dest="license_type", default=None, help="Orthogonal filter: license type / sub-category (Active, Permanent, ...)")
    p.add_argument("--provider-type", dest="provider_type", default=None, help="Orthogonal filter: provider type (MD, DO, RN, LPN, PA, NP, ...)")
    p.add_argument("--headed", action="store_true", help="Run browser in headed (visible) mode")
    p.add_argument("--dry-run", action="store_true", help="Validate config and print plan; no browser")
    p.add_argument("--output", default=None, help="Output JSON path (default: output/{source_id}_{ts}.json)")
    p.add_argument("--db", default="./lvs_scrape.db", help="SQLite DB path (default: ./lvs_scrape.db)")
    p.add_argument("--evidence-dir", default=None, help="Override evidence base path")
    args = p.parse_args()

    has_structured = any([args.license_number, args.first_name, args.middle_name, args.last_name])
    if not args.mode and not has_structured:
        p.error("must provide either --mode/--query or one of --license-number/--first-name/--middle-name/--last-name")
    return args


def _derive_mode_from_flags(license_number, first_name, middle_name, last_name) -> Optional[str]:
    has_lic = bool(license_number)
    has_first = bool(first_name)
    has_mid = bool(middle_name)
    has_last = bool(last_name)
    if has_lic and has_first and has_last and has_mid:
        return "license_first_mid_last"
    if has_lic and has_first and has_last:
        return "license_first_last"
    if has_first and has_last and has_mid:
        return "first_mid_last"
    if has_lic and has_last:
        return "license_and_last"
    if has_lic and has_first:
        return "license_and_first"
    if has_first and has_last:
        return "first_and_last"
    if has_lic:
        return "license_number"
    if has_first or has_mid:
        return "first_name"
    if has_last:
        return "last_name"
    return None


async def _main():
    args = _parse_args()

    config: SiteConfig = load_config(args.config)

    if args.evidence_dir:
        config.evidence.local_path = args.evidence_dir + "/{month}/{source_id}/{run_id}/"

    derived = _derive_mode_from_flags(
        args.license_number, args.first_name, args.middle_name, args.last_name
    )
    mode = args.mode or derived or "last_name"

    if args.dry_run:
        print(f"DRY RUN — config valid")
        print(f"  source_id      : {config.identity.source_id}")
        print(f"  board          : {config.identity.board_name}")
        print(f"  archetype      : {config.identity.archetype}")
        print(f"  url            : {config.identity.base_url}")
        print(f"  mode           : {mode} ({'auto-derived' if not args.mode else 'explicit'})")
        print(f"  query          : {args.query}")
        print(f"  license_number : {args.license_number}")
        print(f"  first_name     : {args.first_name}")
        print(f"  middle_name    : {args.middle_name}")
        print(f"  last_name      : {args.last_name}")
        print(f"  license_type   : {args.license_type}")
        print(f"  provider_type  : {args.provider_type}")
        print(f"  headless       : {not args.headed}")
        return

    query = SearchQuery(
        mode=mode,
        query=args.query or "",
        license_number=args.license_number,
        first_name=args.first_name,
        middle_name=args.middle_name,
        last_name=args.last_name,
        license_type=args.license_type,
        provider_type=args.provider_type,
    )

    db = await init_db(args.db)
    try:
        records = await verify_license(
            config=config,
            query=query,
            db=db,
            headless_override=not args.headed if args.headed else None,
        )
    finally:
        await db.close()

    if not records:
        print("No records found.")
        return

    _out_ts = datetime.utcnow()
    _out_ts_str = _out_ts.strftime("%Y%m%d_%H%M%S")
    out_run_id = args.output or _out_ts_str
    output_path = await write_output(records, config.identity.source_id, out_run_id)

    print(f"\nDone. {len(records)} record(s) written to {output_path}")
    for r in records[:3]:
        name = r.licensee_full_name or f"{r.licensee_first_name or ''} {r.licensee_last_name or ''}".strip()
        print(f"  [{r.license_number}] {name} — {r.status.value} — exp {r.expiration_date}")
    if len(records) > 3:
        print(f"  ... and {len(records) - 3} more")


def main():
    asyncio.run(_main())


if __name__ == "__main__":
    main()
