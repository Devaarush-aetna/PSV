"""
preflight_csv_cache.py — Pre-warm CSV bulk caches before a PSV run.

For every csv_bulk board whose cached file is missing or expired (> cache_days
old by filename timestamp), downloads a fresh copy using the board's configured
download strategy.  Proxy is picked up automatically from the environment:

    PROXY=proxy:9119 python preflight_csv_cache.py

Usage
-----
    # Check and refresh all stale csv_bulk boards
    python preflight_csv_cache.py

    # Check specific boards
    python preflight_csv_cache.py --boards WY_CHIRO WY_DENTAL WY_PT

    # Re-download even if cache is still valid
    python preflight_csv_cache.py --force

    # Show status without downloading
    python preflight_csv_cache.py --dry-run

    # Parallel downloads (e.g. 3 at once)
    python preflight_csv_cache.py --concurrency 3

Exit codes
----------
    0  all boards have a valid cache (fresh or newly downloaded)
    1  one or more boards failed to download
    2  argument / config error
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Bootstrap: make engine importable from this script's directory
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent))

from engine.csv_extractor import _find_cached_csv, get_csv
from engine.validate import load_config

# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------
FRESH      = "FRESH"       # valid cached file within TTL
DOWNLOADED = "DOWNLOADED"  # freshly downloaded this run
SKIPPED    = "SKIPPED"     # --dry-run or not a csv_bulk board
FAILED     = "FAILED"      # download attempt raised an exception


# ---------------------------------------------------------------------------
# Cache-dir resolver (mirrors csv_extractor.get_csv logic)
# ---------------------------------------------------------------------------

def _resolve_cache_dir(csv_cfg) -> Path:
    """Resolve cache_dir relative to project root, same as csv_extractor."""
    raw = Path(csv_cfg.cache_dir)
    if raw.is_absolute():
        return raw
    # __file__ = .../PSV_DEV/lvs/adapters/scrapers/preflight_csv_cache.py
    # parents[3] = PSV_DEV/
    return Path(__file__).parents[3] / csv_cfg.cache_dir.lstrip("./")


# ---------------------------------------------------------------------------
# Days-remaining helper
# ---------------------------------------------------------------------------

def _cache_age_info(cache_dir: Path, source_id: str, cache_days: int) -> tuple[Optional[Path], Optional[int]]:
    """Return (cached_path_or_None, age_days_or_None).  age_days is None when no file found."""
    import re
    if not cache_dir.exists():
        return None, None
    for f in sorted(cache_dir.glob(f"{source_id}_????????_????.csv"), reverse=True):
        m = re.search(r"_(\d{8}_\d{4})\.csv$", f.name)
        if not m:
            continue
        try:
            file_date = datetime.strptime(m.group(1), "%Y%m%d_%H%M")
            age = (datetime.now() - file_date).days
            return f, age
        except ValueError:
            continue
    return None, None


# ---------------------------------------------------------------------------
# Per-board worker
# ---------------------------------------------------------------------------

async def check_one(
    config_path: Path,
    force: bool,
    dry_run: bool,
    semaphore: asyncio.Semaphore,
) -> tuple[str, str, str, int]:
    """Return (source_id, status, detail, elapsed_ms)."""
    try:
        config = load_config(str(config_path))
    except Exception as e:
        return config_path.parent.name, FAILED, f"config load error: {e}", 0

    source_id = config.identity.source_id
    if config.identity.archetype != "csv_bulk":
        return source_id, SKIPPED, "not csv_bulk", 0

    csv_cfg = config.csv_bulk
    if csv_cfg is None:
        return source_id, SKIPPED, "no csv_bulk config", 0

    # local_merge boards are virtual — they aggregate other boards' caches
    if getattr(csv_cfg, "download_strategy", "") == "local_merge":
        return source_id, SKIPPED, "local_merge (virtual)", 0

    cache_dir = _resolve_cache_dir(csv_cfg)
    cached_path, age_days = _cache_age_info(cache_dir, source_id, csv_cfg.cache_days)

    if cached_path and age_days is not None and age_days < csv_cfg.cache_days and not force:
        ttl_left = csv_cfg.cache_days - age_days
        return source_id, FRESH, f"{cached_path.name}  (age={age_days}d, {ttl_left}d remaining)", 0

    if dry_run:
        if cached_path:
            detail = f"STALE  {cached_path.name}  (age={age_days}d > {csv_cfg.cache_days}d TTL)"
        else:
            detail = "MISSING  no cached file found"
        return source_id, SKIPPED, detail, 0

    # --- Download ---
    async with semaphore:
        t0 = time.monotonic()
        try:
            new_path, _ = await get_csv(config.identity.base_url, source_id, csv_cfg)
            elapsed = int((time.monotonic() - t0) * 1000)
            return source_id, DOWNLOADED, new_path.name, elapsed
        except Exception as e:
            elapsed = int((time.monotonic() - t0) * 1000)
            return source_id, FAILED, str(e)[:120], elapsed


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

async def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Pre-warm CSV bulk caches before a PSV run.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--boards", nargs="*", metavar="SOURCE_ID",
        help="Only check the listed boards (e.g. WY_CHIRO WY_DENTAL)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-download even if the cached file is still within TTL",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show cache status without downloading anything",
    )
    parser.add_argument(
        "--concurrency", type=int, default=2, metavar="N",
        help="Max parallel downloads (default: 2)",
    )
    args = parser.parse_args()

    scrapers_dir = Path(__file__).parent
    config_paths = sorted(scrapers_dir.glob("sites/*/config.yaml"))

    if args.boards:
        filter_set = {s.upper() for s in args.boards}
        config_paths = [p for p in config_paths if p.parent.name.upper() in filter_set]
        if not config_paths:
            print(f"No configs found for: {args.boards}", file=sys.stderr)
            return 2

    semaphore = asyncio.Semaphore(args.concurrency)
    tasks = [check_one(p, args.force, args.dry_run, semaphore) for p in config_paths]
    results = await asyncio.gather(*tasks)

    # Filter to csv_bulk boards only (drop non-csv_bulk SKIPs from display)
    rows = [(sid, status, detail, ms) for sid, status, detail, ms in results
            if status != SKIPPED or not detail.startswith("not csv_bulk")]

    # Sort: FAILED first, then DOWNLOADED, SKIPPED (stale), FRESH
    order = {FAILED: 0, DOWNLOADED: 1, SKIPPED: 2, FRESH: 3}
    rows.sort(key=lambda r: (order.get(r[1], 9), r[0]))

    # ---------------------------------------------------------------------------
    # Print results table
    # ---------------------------------------------------------------------------
    col_w = (28, 11, 8)
    sep = "-" * 110
    print()
    print(f"  {'Board':<{col_w[0]}}  {'Status':<{col_w[1]}}  {'Time':>{col_w[2]}}  Detail")
    print(sep)

    counts = {FRESH: 0, DOWNLOADED: 0, SKIPPED: 0, FAILED: 0}
    for source_id, status, detail, elapsed_ms in rows:
        time_str = f"{elapsed_ms/1000:.1f}s" if elapsed_ms else ""
        print(f"  {source_id:<{col_w[0]}}  {status:<{col_w[1]}}  {time_str:>{col_w[2]}}  {detail}")
        counts[status] = counts.get(status, 0) + 1

    print(sep)
    mode = "DRY RUN — " if args.dry_run else ""
    print(
        f"\n{mode}Summary: "
        f"{counts[FRESH]} FRESH  "
        f"{counts[DOWNLOADED]} DOWNLOADED  "
        f"{counts[SKIPPED]} SKIPPED  "
        f"{counts[FAILED]} FAILED\n"
    )

    if counts[FAILED]:
        print(f"  [FAILED] {counts[FAILED]} board(s) could not be downloaded.", file=sys.stderr)
        print("  Set PROXY=proxy:9119 if behind McAfee, or run from a non-corporate network.", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
