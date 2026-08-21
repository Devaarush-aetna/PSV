"""Orchestrator paths, thresholds, and env-driven knobs.

Resolves project root (PSV_DEV/) the same way engine/evidence.py does:
  orchestrator/config.py -> orchestrator/ -> scrapers/ -> adapters/ -> lvs/ -> PSV_DEV/
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parents[4]

# Output / Evidence / Cache roots (all at PSV_DEV/ root)
OUTPUT_ROOT: Path = PROJECT_ROOT / "Output"
EVIDENCE_ROOT: Path = PROJECT_ROOT / "Evidence"
# Legacy fallback paths — per-run folders now live under Output/{YYYYMM}/{run_id}/
TRACE_ROOT: Path = OUTPUT_ROOT / "_traces"
DRIFT_ROOT: Path = OUTPUT_ROOT / "_drift"
NPPES_CACHE_ROOT: Path = PROJECT_ROOT / "PSV" / "Cache" / "NPPES"

# Channel folder names (nested under Output/{YYYYMM}/{run_id}/ at write time)
_CH_STANDARD      = "Standard"
_CH_NPPES         = "NPPES"
_CH_AI_FALLBACK   = "AIFallback"
_CH_MANUAL        = "Manual"
_CH_ADD_LICENSE   = "AddLicense"
_CH_AI_ADD_LICENSE = "AIAddLicense"
_CH_RUN_SUMMARY   = "RunSummary"
_CH_TRACES        = "Traces"
_CH_DRIFT         = "Drift"
_CH_FALL_OUT      = "FallOut"

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

# Post-license name gate thresholds (name_gate.py)
NAME_GATE_THRESHOLD: float = 0.80      # max(nppes_score, epdb_score) >= this → approve AddLicense
NAME_GATE_AI_BAND_LOW: float = 0.70    # in [0.70, 0.80) → route to AI disambiguator

# AI agent
AI_MAX_TURNS: int = int(os.environ.get("PSV_AI_MAX_TURNS", "12"))
AI_MOCK_PATH_ENV: str = "PSV_AI_MOCK_PATH"   # set by --ai-mock CLI flag

# Telemetry DB lives at PSV_DEV/lvs/adapters/scrapers/lvs_scrape.db (unchanged).
TELEMETRY_DB_PATH: Path = PROJECT_ROOT / "lvs" / "adapters" / "scrapers" / "lvs_scrape.db"


def yyyy_mm_from_run_id(run_id: str) -> str:
    """20260623_1402... -> 202606.  Falls back to current month if malformed."""
    if len(run_id) >= 6 and run_id[:8].isdigit():
        return f"{run_id[:4]}{run_id[4:6]}"
    from datetime import datetime
    return datetime.now().strftime("%Y%m")


def date_time_from_run_id(run_id: str) -> str:
    """20260701_2133_001 -> 20260701_2133.  Falls back to run_id if malformed."""
    if (len(run_id) >= 13
            and run_id[:8].isdigit()
            and run_id[8] == "_"
            and run_id[9:13].isdigit()):
        return run_id[:13]
    return run_id


def ensure_channel_dirs(run_id: str) -> dict[str, Path]:
    """Create Output/{YYYYMM}/{run_id}/{Channel}/ folders for this run; return them as a dict."""
    ym = yyyy_mm_from_run_id(run_id)
    base = OUTPUT_ROOT / ym / run_id
    dirs = {
        "standard":       base / _CH_STANDARD,
        "nppes":          base / _CH_NPPES,
        "ai_fallback":    base / _CH_AI_FALLBACK,
        "manual":         base / _CH_MANUAL,
        "add_license":    base / _CH_ADD_LICENSE,
        "ai_add_license": base / _CH_AI_ADD_LICENSE,
        "run_summary":    base / _CH_RUN_SUMMARY,
        "trace":          base / _CH_TRACES,
        "drift":          base / _CH_DRIFT,
        "fall_out":       base / _CH_FALL_OUT,
    }
    for p in dirs.values():
        p.mkdir(parents=True, exist_ok=True)
    NPPES_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    return dirs
