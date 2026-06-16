"""
smoke_all.py — Regression gate for all board configs.

Runs every config that has a smoke_test block and is not skipped.
Configs without a smoke_test block are reported as MISSING.

Usage
-----
    # Run all runnable boards (sequential, headless)
    python smoke_all.py

    # Run specific boards
    python smoke_all.py --filter NV_MEDBOARD KS_DENTAL WA_HEALTH

    # Run up to 3 browsers in parallel
    python smoke_all.py --concurrency 3

    # Run headed (visible browser) for debugging
    python smoke_all.py --filter NV_BOP --headed

    # List what would run without actually running anything
    python smoke_all.py --dry-run

Exit codes
----------
    0  all runnable boards PASSED (or no runnable boards found)
    1  one or more boards FAILED
    2  argument / config error
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path
from typing import Optional

import yaml

# ---------------------------------------------------------------------------
# Bootstrap: make engine importable from this script's directory
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent))

from engine.models import SearchQuery, SiteConfig
from engine.validate import load_config
from run import verify_license

# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------
PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
MISSING = "MISSING"


# ---------------------------------------------------------------------------
# Result checking
# ---------------------------------------------------------------------------

def _record_to_dict(rec) -> dict:
    if hasattr(rec, "model_dump"):
        return rec.model_dump()
    if hasattr(rec, "__dict__"):
        return rec.__dict__
    return dict(rec)


_NAME_KEYS = ("licensee_full_name", "licensee_last_name", "licensee_first_name", "license_number")


def _first_nonempty(records: list) -> dict:
    """Return the first record dict that has at least one non-blank name/license field."""
    for rec in records:
        d = _record_to_dict(rec)
        if any(str(d.get(k) or "").strip() for k in _NAME_KEYS):
            return d
    return _record_to_dict(records[0])  # fallback


def check_result(records: list, config: SiteConfig) -> tuple[str, str]:
    """Return (status, detail_string) given a list of LicenseRecord objects."""
    st = config.smoke_test
    expect = st.expect

    if not records:
        return FAIL, "no records returned"

    if len(records) < expect.min_records:
        return FAIL, f"got {len(records)} record(s), expected >= {expect.min_records}"

    # Skip blank/spacer rows that some boards insert; find first row with real data.
    d = _first_nonempty(records)

    if expect.license_number:
        actual = str(d.get("license_number") or "")
        if actual != expect.license_number:
            return FAIL, f"license_number: got '{actual}', expected '{expect.license_number}'"

    if expect.status:
        actual = str(d.get("status") or "").lower().strip()
        if hasattr(d.get("status"), "value"):
            actual = d["status"].value
        expected = expect.status.lower().strip()
        if actual != expected:
            return FAIL, f"status: got '{actual}', expected '{expected}'"

    if expect.full_name_contains:
        full = (
            str(d.get("licensee_full_name") or "")
            or f"{d.get('licensee_first_name') or ''} {d.get('licensee_last_name') or ''}".strip()
        )
        if expect.full_name_contains.lower() not in full.lower():
            return FAIL, f"full_name '{full}' does not contain '{expect.full_name_contains}'"

    # Summary detail for the PASS line
    lic = d.get("license_number", "?") or "?"
    name = (
        str(d.get("licensee_full_name") or "")
        or f"{d.get('licensee_first_name') or ''} {d.get('licensee_last_name') or ''}".strip()
        or "?"
    )
    status_val = d.get("status")
    status_str = status_val.value if hasattr(status_val, "value") else str(status_val or "?")
    detail = f"[{lic}] {name} - {status_str}"
    if len(records) > 1:
        detail += f" (+{len(records) - 1} more)"
    return PASS, detail


# ---------------------------------------------------------------------------
# Per-board runner
# ---------------------------------------------------------------------------

async def run_one(
    config_path: Path,
    headed: bool,
    semaphore: asyncio.Semaphore,
    per_board_timeout: int = 180,
    force_skip: bool = False,
) -> tuple[str, str, str, str, str, str]:
    """
    Returns (source_id, mode, query, status, detail, elapsed).
    Never raises — exceptions become FAIL with the error message as detail.
    """
    async with semaphore:
        try:
            config = load_config(str(config_path))
        except Exception as e:
            return config_path.parent.name, "-", "-", FAIL, f"config load error: {e}", "0s"

        source_id = config.identity.source_id

        # Auto-skip captcha boards regardless of smoke_test.skip
        if config.compliance.requires_captcha:
            mode = config.smoke_test.mode if config.smoke_test else "-"
            query = config.smoke_test.query if config.smoke_test else "-"
            return source_id, mode, query, SKIP, "requires_captcha: true", "0s"

        if not config.smoke_test:
            return source_id, "-", "-", MISSING, "no smoke_test block — add one", "0s"

        st = config.smoke_test
        if st.skip and not force_skip:
            return source_id, st.mode, st.query, SKIP, st.skip_reason, "0s"

        query = SearchQuery(
            mode=st.mode,
            query=st.query or "",
            license_number=st.license_number,
            first_name=st.first_name,
            last_name=st.last_name,
            license_type=st.license_type,
            provider_type=st.provider_type,
        )
        t0 = time.time()
        try:
            records = await asyncio.wait_for(
                verify_license(config, query, headless_override=not headed),
                timeout=per_board_timeout,
            )
            elapsed = f"{time.time() - t0:.1f}s"
            status, detail = check_result(records, config)
        except asyncio.TimeoutError:
            elapsed = f"{time.time() - t0:.1f}s"
            status = FAIL
            detail = f"board_timeout: hung for >{per_board_timeout}s — check site or increase --board-timeout"
        except Exception as e:
            elapsed = f"{time.time() - t0:.1f}s"
            status = FAIL
            detail = str(e).splitlines()[0][:100]

        return source_id, st.mode, st.query, status, detail, elapsed


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

async def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Regression smoke-test runner for all board configs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--filter", nargs="*", metavar="SOURCE_ID",
        help="Only test the listed boards (e.g. NV_MEDBOARD KS_DENTAL)",
    )
    parser.add_argument(
        "--headed", action="store_true",
        help="Run browser in headed (visible) mode",
    )
    parser.add_argument(
        "--concurrency", type=int, default=1,
        help="Max parallel browsers (default: 1)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be tested without running anything",
    )
    parser.add_argument(
        "--board-timeout", type=int, default=180, metavar="SECONDS",
        help="Per-board wall-clock timeout in seconds (default: 180). Board is marked FAIL if it exceeds this.",
    )
    parser.add_argument(
        "--force-skip", action="store_true",
        help="Attempt to run boards marked skip:true (useful for checking if a blocked site has recovered).",
    )
    args = parser.parse_args()

    scrapers_dir = Path(__file__).parent
    config_paths = sorted(scrapers_dir.glob("sites/*/config.yaml"))

    if args.filter:
        filter_set = {s.upper() for s in args.filter}
        config_paths = [p for p in config_paths if p.parent.name.upper() in filter_set]

    if not config_paths:
        print("No config files found.", file=sys.stderr)
        return 2

    if args.dry_run:
        print(f"\nDRY RUN — {len(config_paths)} config(s) found:\n")
        for p in config_paths:
            try:
                cfg = load_config(str(p))
                st = cfg.smoke_test
                if cfg.compliance.requires_captcha:
                    tag = f"SKIP (captcha)"
                elif not st:
                    tag = "MISSING smoke_test"
                elif st.skip:
                    tag = f"SKIP ({st.skip_reason[:60]})"
                else:
                    tag = f"WOULD RUN: mode={st.mode} query={st.query}"
                print(f"  {cfg.identity.source_id:<22} {tag}")
            except Exception as e:
                print(f"  {p.parent.name:<22} ERROR: {e}")
        return 0

    print(f"\nSmoke-testing {len(config_paths)} board config(s)  "
          f"[concurrency={args.concurrency}, headed={args.headed}]\n")

    semaphore = asyncio.Semaphore(args.concurrency)
    tasks = [
        run_one(p, args.headed, semaphore, per_board_timeout=args.board_timeout, force_skip=args.force_skip)
        for p in config_paths
    ]
    results = await asyncio.gather(*tasks)

    # ---------------------------------------------------------------------------
    # Print results table
    # ---------------------------------------------------------------------------
    col_w = (22, 14, 24, 8)
    header = (
        f"{'Board':<{col_w[0]}} {'Mode':<{col_w[1]}} {'Query':<{col_w[2]}} {'Status':<{col_w[3]}} Detail"
    )
    sep = "-" * (sum(col_w) + 60)
    print(header)
    print(sep)

    def _safe(s):
        # Windows console (cp1252) chokes on Unicode chars sometimes returned by
        # boards (smart quotes, arrows, em-dashes). ASCII-encode with replacement
        # so a single odd character doesn't crash the whole summary print.
        try:
            return str(s).encode("ascii", "replace").decode("ascii")
        except Exception:
            return repr(s)

    passes = fails = skips = missing = 0
    for source_id, mode, query, status, detail, elapsed in results:
        label = {"PASS": "PASS", "FAIL": "FAIL", "SKIP": "SKIP", "MISSING": "MISSING"}[status]
        time_str = f" ({elapsed})" if status in (PASS, FAIL) else ""
        print(
            f"{_safe(source_id):<{col_w[0]}} {_safe(mode):<{col_w[1]}} {_safe(query):<{col_w[2]}} "
            f"{label:<{col_w[3]}} {_safe(detail)}{time_str}"
        )
        if status == PASS:
            passes += 1
        elif status == FAIL:
            fails += 1
        elif status == SKIP:
            skips += 1
        elif status == MISSING:
            missing += 1

    print(sep)
    print(f"\nSummary: {passes} PASS  {fails} FAIL  {skips} SKIP  {missing} MISSING\n")

    if missing:
        print(
            f"  [!] {missing} board(s) have no smoke_test block.\n"
            f"      Add one to each config.yaml before shipping that board.\n"
        )
    if fails:
        print(
            f"  [FAIL] {fails} board(s) FAILED.\n"
            f"         Fix before merging any engine changes.\n"
        )

    return 0 if fails == 0 else 1


def main() -> None:
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
