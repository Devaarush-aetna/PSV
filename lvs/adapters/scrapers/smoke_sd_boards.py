#!/usr/bin/env python3
"""
smoke_sd_boards.py — Independent smoke test for the 7 South Dakota board configs.

Boards covered
--------------
  SD_AUDIOLOGY  pdf_bulk  sdboards.org/healthdept/audiology/verify/
  SD_CHIRO      csv_bulk  bocelicensing.appssd.sd.gov/Licensees
  SD_OPT        csv_bulk  optometry.appssd.sd.gov/Licensees
  SD_PODIATRY   pdf_bulk  sdboards.org/healthdept/podiatry/verify/
  SD_PSYCH      pdf_bulk  sdboards.org/dss/psych/verify/
  SD_PT         pdf_bulk  sdboards.org/healthdept/physicaltherapy/verify/
  SD_SPEECH     pdf_bulk  sdboards.org/healthdept/SpeechPath/verify/

Usage
-----
  # Run from the scrapers directory
  cd C:\\Users\\n661685\\PSV_DEV\\lvs\\adapters\\scrapers
  python smoke_sd_boards.py

  # Headed browser (watch it live)
  python smoke_sd_boards.py --headed

  # Single board
  python smoke_sd_boards.py --filter SD_CHIRO

  # Increased timeout (default 180s per board)
  python smoke_sd_boards.py --timeout 240

Exit codes
----------
  0  all boards PASSED
  1  one or more boards FAILED
  2  argument / config error
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Bootstrap: make engine importable when run from scrapers/ directory
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

from engine.models import SearchQuery, SiteConfig
from engine.validate import load_config
from run import verify_license

# ---------------------------------------------------------------------------
# Board manifest — all 7 newly added SD configs
# ---------------------------------------------------------------------------
SD_BOARD_IDS = [
    "SD_AUDIOLOGY",
    "SD_CHIRO",
    "SD_OPT",
    "SD_PODIATRY",
    "SD_PSYCH",
    "SD_PT",
    "SD_SPEECH",
]

# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------
PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

# ---------------------------------------------------------------------------
# Result field helpers (handle both Pydantic models and plain dicts)
# ---------------------------------------------------------------------------
_NAME_KEYS = (
    "licensee_full_name",
    "licensee_last_name",
    "licensee_first_name",
    "license_number",
)


def _to_dict(rec) -> dict:
    if hasattr(rec, "model_dump"):
        return rec.model_dump()
    if hasattr(rec, "__dict__"):
        return rec.__dict__
    return dict(rec)


def _first_meaningful(records: list) -> dict:
    """Return the first record that has at least one non-blank name/license field."""
    for rec in records:
        d = _to_dict(rec)
        if any(str(d.get(k) or "").strip() for k in _NAME_KEYS):
            return d
    return _to_dict(records[0])


def _status_str(d: dict) -> str:
    val = d.get("status")
    if val is None:
        return "?"
    return val.value if hasattr(val, "value") else str(val)


def _name_str(d: dict) -> str:
    full = str(d.get("licensee_full_name") or "").strip()
    if full:
        return full
    first = str(d.get("licensee_first_name") or "").strip()
    last = str(d.get("licensee_last_name") or "").strip()
    return f"{first} {last}".strip() or "?"


def check_result(records: list, config: SiteConfig) -> tuple[str, str]:
    """Validate records against smoke_test.expect block. Returns (status, detail)."""
    expect = config.smoke_test.expect

    if not records:
        return FAIL, "no records returned"

    if len(records) < expect.min_records:
        return FAIL, f"got {len(records)} record(s), expected >= {expect.min_records}"

    d = _first_meaningful(records)

    if expect.license_number:
        actual = str(d.get("license_number") or "")
        if actual != expect.license_number:
            return FAIL, f"license_number: got '{actual}', expected '{expect.license_number}'"

    if expect.status:
        actual = _status_str(d).lower().strip()
        expected = expect.status.lower().strip()
        if actual != expected:
            return FAIL, f"status: got '{actual}', expected '{expected}'"

    if expect.full_name_contains:
        name = _name_str(d)
        if expect.full_name_contains.lower() not in name.lower():
            return FAIL, f"name '{name}' does not contain '{expect.full_name_contains}'"

    lic = str(d.get("license_number") or "?")
    detail = f"[{lic}] {_name_str(d)} - {_status_str(d)}"
    if len(records) > 1:
        detail += f" (+{len(records) - 1} more)"
    return PASS, detail


# ---------------------------------------------------------------------------
# Per-board async runner
# ---------------------------------------------------------------------------

async def run_board(
    board_id: str,
    headed: bool,
    semaphore: asyncio.Semaphore,
    timeout_secs: int,
) -> tuple[str, str, str, str, str, str]:
    """
    Returns (source_id, mode, query, status, detail, elapsed_str).
    Never raises — all exceptions become FAIL.
    """
    config_path = _HERE / "sites" / board_id / "config.yaml"

    async with semaphore:
        if not config_path.exists():
            return board_id, "-", "-", FAIL, f"config.yaml not found: {config_path}", "0s"

        try:
            config = load_config(str(config_path))
        except Exception as exc:
            return board_id, "-", "-", FAIL, f"config load error: {exc}", "0s"

        source_id = config.identity.source_id

        if not config.smoke_test:
            return source_id, "-", "-", FAIL, "no smoke_test block in config.yaml", "0s"

        st = config.smoke_test
        if st.skip:
            return source_id, st.mode, st.query, SKIP, st.skip_reason or "skip: true", "0s"

        if config.compliance.requires_captcha:
            return source_id, st.mode, st.query, SKIP, "requires_captcha: true", "0s"

        query = SearchQuery(
            mode=st.mode,
            query=st.query or "",
            license_number=st.license_number,
            first_name=st.first_name,
            middle_name=st.middle_name,
            last_name=st.last_name,
            license_type=st.license_type,
            provider_type=st.provider_type,
        )

        t0 = time.time()
        try:
            records = await asyncio.wait_for(
                verify_license(config, query, headless_override=not headed),
                timeout=timeout_secs,
            )
            elapsed = f"{time.time() - t0:.1f}s"
            status, detail = check_result(records, config)
        except asyncio.TimeoutError:
            elapsed = f"{time.time() - t0:.1f}s"
            status = FAIL
            detail = f"timed out after {timeout_secs}s"
        except Exception as exc:
            elapsed = f"{time.time() - t0:.1f}s"
            status = FAIL
            detail = str(exc).splitlines()[0][:120]

        return source_id, st.mode, st.query, status, detail, elapsed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Independent smoke test runner for the 7 South Dakota board configs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--filter", nargs="*", metavar="BOARD_ID",
        help="Limit to specific board IDs (e.g. SD_CHIRO SD_PT)",
    )
    parser.add_argument(
        "--headed", action="store_true",
        help="Run browser in headed (visible) mode",
    )
    parser.add_argument(
        "--concurrency", type=int, default=1, metavar="N",
        help="Max parallel browsers (default: 1; increase for faster runs)",
    )
    parser.add_argument(
        "--timeout", type=int, default=180, metavar="SECONDS",
        help="Per-board timeout in seconds (default: 180)",
    )
    args = parser.parse_args()

    target_ids = SD_BOARD_IDS
    if args.filter:
        filter_set = {s.upper() for s in args.filter}
        target_ids = [b for b in SD_BOARD_IDS if b.upper() in filter_set]
        unknown = filter_set - {b.upper() for b in SD_BOARD_IDS}
        if unknown:
            print(f"WARNING: unknown board IDs ignored: {', '.join(sorted(unknown))}", file=sys.stderr)

    if not target_ids:
        print("No boards to test.", file=sys.stderr)
        return 2

    print(f"\nSD Board Smoke Tests — {len(target_ids)} board(s)  "
          f"[concurrency={args.concurrency}, timeout={args.timeout}s, headed={args.headed}]\n")
    print(f"  {'Board':<20} {'Mode':<16} {'Query':<12} {'Status':<6}  {'Detail'}")
    print(f"  {'-'*20} {'-'*16} {'-'*12} {'-'*6}  {'-'*50}")

    semaphore = asyncio.Semaphore(args.concurrency)
    tasks = [
        run_board(bid, args.headed, semaphore, args.timeout)
        for bid in target_ids
    ]

    results = await asyncio.gather(*tasks)

    counts = {PASS: 0, FAIL: 0, SKIP: 0}
    for source_id, mode, query, status, detail, elapsed in results:
        counts[status] = counts.get(status, 0) + 1
        status_col = f"[{status}]"
        print(f"  {source_id:<20} {mode:<16} {query:<12} {status_col:<8} {detail}  ({elapsed})")

    print(f"\n  Results: {counts[PASS]} PASS / {counts[FAIL]} FAIL / {counts[SKIP]} SKIP\n")

    if counts[FAIL] > 0:
        print("FAILED — one or more SD boards did not pass smoke test.", file=sys.stderr)
        return 1

    print("All SD boards PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
