"""Orchestrator paths, thresholds, and env-driven knobs.

Resolves project root (PSV_DEV/) the same way engine/evidence.py does:
  orchestrator/config.py -> orchestrator/ -> scrapers/ -> adapters/ -> lvs/ -> PSV_DEV/
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parents[4]

# Output / Evidence / Cache / Trace roots (all at PSV_DEV/ root)
OUTPUT_ROOT: Path = PROJECT_ROOT / "Output"
EVIDENCE_ROOT: Path = PROJECT_ROOT / "Evidence"
TRACE_ROOT: Path = OUTPUT_ROOT / "_traces"
DRIFT_ROOT: Path = OUTPUT_ROOT / "_drift"
NPPES_CACHE_ROOT: Path = PROJECT_ROOT / "PSV" / "Cache" / "NPPES"

# Channel subfolders (year/month-partitioned at write time)
STANDARD_DIR: Path = OUTPUT_ROOT / "standard"
NPPES_DIR: Path = OUTPUT_ROOT / "nppes"
AI_FALLBACK_DIR: Path = OUTPUT_ROOT / "ai_fallback"
MANUAL_DIR: Path = OUTPUT_ROOT / "manual"
ADD_LICENSE_DIR: Path = OUTPUT_ROOT / "add_license"

# NPPES Registry API
NPPES_API_URL: str = "https://npiregistry.cms.hhs.gov/api/"
NPPES_API_VERSION: str = "2.1"
NPPES_CACHE_DAYS: int = 30
NPPES_TIMEOUT_S: float = 15.0

# Disambiguator thresholds
THRESHOLD_LICENSE_PROFILE: float = 0.90
THRESHOLD_NAME_PROFILE: float = 0.85
TIEBREAKER_DELTA: float = 0.02
NAME_FUZZ_MIN: int = 90       # rapidfuzz cutoff for "first/last name matches"

# AI agent
AI_MAX_TURNS: int = int(os.environ.get("PSV_AI_MAX_TURNS", "8"))
AI_MOCK_PATH_ENV: str = "PSV_AI_MOCK_PATH"   # set by --ai-mock CLI flag

# Telemetry DB lives at PSV_DEV/lvs/adapters/scrapers/lvs_scrape.db (unchanged).
TELEMETRY_DB_PATH: Path = PROJECT_ROOT / "lvs" / "adapters" / "scrapers" / "lvs_scrape.db"


def yyyy_mm_from_run_id(run_id: str) -> str:
    """20260623_1402... -> 2026-06.  Falls back to current month if malformed."""
    if len(run_id) >= 6 and run_id[:8].isdigit():
        return f"{run_id[:4]}-{run_id[4:6]}"
    from datetime import datetime
    return datetime.now().strftime("%Y-%m")


def ensure_channel_dirs(run_id: str) -> dict[str, Path]:
    """Create channel/{YYYY-MM}/ folders for this run; return them as a dict."""
    ym = yyyy_mm_from_run_id(run_id)
    dirs = {
        "standard": STANDARD_DIR / ym,
        "nppes": NPPES_DIR / ym,
        "ai_fallback": AI_FALLBACK_DIR / ym,
        "manual": MANUAL_DIR / ym,
        "add_license": ADD_LICENSE_DIR / ym,
        "trace": TRACE_ROOT / ym / run_id,
    }
    for p in dirs.values():
        p.mkdir(parents=True, exist_ok=True)
    DRIFT_ROOT.mkdir(parents=True, exist_ok=True)
    NPPES_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    return dirs
