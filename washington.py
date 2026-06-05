"""
Washington State Health Care Provider Credential Tool
=====================================================
Downloads the Health Care Provider Credential Data CSV from data.wa.gov
and allows querying by credential number, first/last name, or any combination.

File management:
  - Data stored in ./data/
  - Files named: wa_credentials_YYYYMMDD.csv + wa_credentials_YYYYMMDD.parquet
  - 7-day freshness rule: if existing file is < 7 days old, reuse it
  - Parquet conversion done once after download for fast subsequent reads

Credential number matching:
  - Full dotted format: RN.RN.60902940.MSL  →  exact match (case-insensitive)
  - Numeric-only:       60902940            →  matches the digits inside any credential
  - Mixed / partial:    RN.60902940         →  substring match

Search modes (all case-insensitive):
  --credential NUMBER
  --lastname  NAME
  --firstname NAME
  --lastname NAME --firstname NAME
  --credential NUMBER --lastname NAME   (most precise)
  ... any combination of the three flags

Usage:
    python washington_credentialing.py --credential 60902940
    python washington_credentialing.py --credential RN.RN.60902940.MSL
    python washington_credentialing.py --lastname Smith --firstname John
    python washington_credentialing.py --lastname Smith
    python washington_credentialing.py --credential 60902940 --lastname Smith
    python washington_credentialing.py --credential 60902940 --output result.json
    python washington_credentialing.py --refresh           # force re-download
    python washington_credentialing.py --show-browser      # watch the download

Requirements:
    pip install playwright pandas pyarrow
    playwright install chromium
"""

import re
import sys
import json
import logging
import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

DATASET_URL      = "https://url.usb.m.mimecastprotect.com/s/5cfvCZZEDNsQmQpNrM8CyswcBFgzB?domain=data.wa.gov"
DIRECT_CSV_URL   = "https://url.usb.m.mimecastprotect.com/s/uNIjC1VkzPuqjq9Y2pBfYtKcVT-A8?domain=data.wa.gov"
# DATA_DIR         = Path("data") / "washington"
DATA_DIR         = Path("data")
FILE_PREFIX      = "wa_credentials_"
MAX_AGE_DAYS = 7


# ═══════════════════════════════════════════════════════════
# File management
# ═══════════════════════════════════════════════════════════

def _dated_path(ext: str) -> Path:
    return DATA_DIR / f"{FILE_PREFIX}{datetime.now().strftime('%Y%m%d')}{ext}"


def _latest_file(ext: str) -> Optional[Path]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    candidates = sorted(DATA_DIR.glob(f"{FILE_PREFIX}*{ext}"), reverse=True)
    return candidates[0] if candidates else None


def _is_fresh(path: Path) -> bool:
    """True when the date embedded in the filename is < MAX_AGE_DAYS old."""
    m = re.search(r"(\d{8})", path.stem)
    if not m:
        return False
    file_date = datetime.strptime(m.group(1), "%Y%m%d").date()
    return (datetime.now().date() - file_date).days < MAX_AGE_DAYS


# ═══════════════════════════════════════════════════════════
# CSV downloader
# ═══════════════════════════════════════════════════════════

def download_csv() -> Path:
    """Download the CSV directly from the Socrata API endpoint (public dataset)."""
    import urllib.request

    target = _dated_path(".csv")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    log.info("Downloading CSV from Socrata API  (It may take several minutes) …")
    log.info("URL: %s", DIRECT_CSV_URL)

    req = urllib.request.Request(
        DIRECT_CSV_URL,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        },
    )

    CHUNK = 4 * 1024 * 1024  # 4 MB
    downloaded = 0
    with urllib.request.urlopen(req, timeout=600) as resp, open(target, "wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        while True:
            chunk = resp.read(CHUNK)
            if not chunk:
                break
            fh.write(chunk)
            downloaded += len(chunk)
            if total:
                log.info("  %.0f / %.0f MB  (%.0f%%)",
                         downloaded / 1e6, total / 1e6, downloaded / total * 100)
            else:
                log.info("  %.0f MB downloaded …", downloaded / 1e6)

    mb = target.stat().st_size / 1024 ** 2
    log.info("CSV saved: %s  (%.1f MB)", target, mb)
    return target


# ═══════════════════════════════════════════════════════════
# CSV → Parquet conversion  (one-time, for fast future reads)
# ═══════════════════════════════════════════════════════════

def _csv_to_parquet(csv_path: Path) -> Path:
    try:
        import pandas as pd
    except ImportError:
        sys.exit("Missing: pandas pyarrow\n  pip install pandas pyarrow")

    out = csv_path.with_suffix(".parquet")
    log.info("Converting CSV → Parquet for faster future reads …")
    df = pd.read_csv(
        csv_path,
        dtype=str,
        keep_default_na=False,
        low_memory=False,
        encoding="utf-8",
        encoding_errors="replace",
    )
    df.columns = [c.strip() for c in df.columns]
    df.to_parquet(out, index=False)
    mb = out.stat().st_size / 1024 ** 2
    log.info("Parquet saved: %s  (%.1f MB)", out, mb)
    return out


# ═══════════════════════════════════════════════════════════
# Data loader  (applies 7-day rule, returns pandas DataFrame)
# ═══════════════════════════════════════════════════════════

def load_data(force_download: bool = False):
    try:
        import pandas as pd
    except ImportError:
        sys.exit("Missing: pandas pyarrow\n  pip install pandas pyarrow")

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Path A: fresh Parquet exists → load in ~2 s
    pq = _latest_file(".parquet")
    if pq and _is_fresh(pq) and not force_download:
        log.info("Using cached Parquet (%s)", pq.name)
        return pd.read_parquet(pq)

    # Path B: fresh CSV exists but no Parquet → convert then load
    csv_f = _latest_file(".csv")
    if csv_f and _is_fresh(csv_f) and not force_download:
        log.info("Fresh CSV found – converting to Parquet: %s", csv_f.name)
        pq = _csv_to_parquet(csv_f)
        return pd.read_parquet(pq)

    # Path C: data is stale or --refresh → download
    reason = "--refresh requested" if force_download else "data is > 7 days old or absent"
    log.info("Downloading fresh data (%s) …", reason)
    csv_f = download_csv()
    pq = _csv_to_parquet(csv_f)
    return pd.read_parquet(pq)


# ═══════════════════════════════════════════════════════════
# Column discovery
# ═══════════════════════════════════════════════════════════

def _find_col(df, *patterns: str) -> Optional[str]:
    for pat in patterns:
        for col in df.columns:
            if re.search(pat, col, re.I):
                return col
    return None


def _discover_columns(df) -> dict:
    return {
        "credential":  _find_col(df, r"credential.?number", r"credentialnumber",
                                  r"^credential$", r"cred.?num", r"^cred\b"),
        "last_name":   _find_col(df, r"last.?name", r"lastname", r"\blname\b"),
        "first_name":  _find_col(df, r"first.?name", r"firstname", r"\bfname\b"),
        "middle_name": _find_col(df, r"middle.?name", r"middlename"),
        "status":      _find_col(df, r"credential.?status", r"^status$"),
        "birth_year":  _find_col(df, r"birth.?year"),
        "ce_due":      _find_col(df, r"ce.?due"),
        "first_issue": _find_col(df, r"first.?issu"),
        "last_issue":  _find_col(df, r"last.?issu"),
        "expiration":  _find_col(df, r"expir"),
        "action":      _find_col(df, r"action.?tak"),
    }


# ═══════════════════════════════════════════════════════════
# Credential number matching
# ═══════════════════════════════════════════════════════════

def _extract_longest_numeric(s: str) -> str:
    """Return the longest digit-run in s, e.g. 'RN.RN.60902940.MSL' → '60902940'."""
    parts = re.findall(r"\d+", s)
    return max(parts, key=len) if parts else ""


def _cred_mask(series, credential: str):
    """
    Boolean mask for credential matching:
      • Contains '.'          → exact case-insensitive equality
      • All/mostly digits     → numeric boundary match (won't confuse 1234 with 12345)
      • Everything else       → substring search
    """
    c_up  = credential.strip().upper()
    s_up  = series.str.upper().str.strip()

    if "." in c_up:
        return s_up == c_up

    numeric = _extract_longest_numeric(c_up)
    if numeric:
        pat = r"(?<![0-9])" + re.escape(numeric) + r"(?![0-9])"
        return s_up.str.contains(pat, regex=True, na=False)

    return s_up.str.contains(re.escape(c_up), regex=True, na=False)


# ═══════════════════════════════════════════════════════════
# Search
# ═══════════════════════════════════════════════════════════

def search(df, credential: str = "", last_name: str = "", first_name: str = "") -> list:
    """
    Apply filters and return matching rows as a list of dicts (JSON-ready).
    """
    import pandas as pd

    cols = _discover_columns(df)
    mask = pd.Series(True, index=df.index)

    if credential:
        col = cols.get("credential")
        if col:
            mask &= _cred_mask(df[col], credential)
        else:
            log.warning("Credential column not found – skipping credential filter.")

    if last_name:
        col = cols.get("last_name")
        if col:
            mask &= (df[col].str.upper().str.strip()
                     .str.contains(re.escape(last_name.strip().upper()), na=False))
        else:
            log.warning("Last-name column not found – skipping last-name filter.")

    if first_name:
        col = cols.get("first_name")
        if col:
            mask &= (df[col].str.upper().str.strip()
                     .str.contains(re.escape(first_name.strip().upper()), na=False))
        else:
            log.warning("First-name column not found – skipping first-name filter.")

    hits = df[mask]
    log.info("Matches found: %d", len(hits))
    # Replace NaN / NA with None for clean JSON
    return hits.where(hits.notna(), other=None).to_dict(orient="records")


# ═══════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Washington State Health Care Provider Credential Lookup",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--credential", metavar="NUMBER",
        help="Full credential (RN.RN.60902940.MSL) or numeric part (60902940)",
    )
    parser.add_argument("--lastname",     metavar="NAME",  help="Provider last name")
    parser.add_argument("--firstname",    metavar="NAME",  help="Provider first name")
    parser.add_argument("--output",       metavar="PATH",  help="Write JSON to this file")
    parser.add_argument("--refresh",      action="store_true",
                        help="Force re-download even if data is < 7 days old")
    parser.add_argument("--show-browser", "--show_browser", action="store_true",
                        help="Show the browser window during download")
    parser.add_argument("--data-dir",     metavar="PATH",
                        help="Override data directory (default: ./data)")
    parser.add_argument("--verbose",      action="store_true")
    args = parser.parse_args()

    if not any([args.credential, args.lastname, args.firstname]):
        parser.error("Provide at least one of: --credential, --lastname, --firstname")

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.data_dir:
        global DATA_DIR
        DATA_DIR = Path(args.data_dir)

    df = load_data(force_download=args.refresh)

    matches = search(
        df,
        credential = (args.credential or "").strip(),
        last_name  = (args.lastname   or "").strip(),
        first_name = (args.firstname  or "").strip(),
    )

    output = {
        "search": {
            "credential":    args.credential or "",
            "last_name":     args.lastname   or "",
            "first_name":    args.firstname  or "",
            "total_matches": len(matches),
        },
        "results": matches,
    }

    json_str = json.dumps(output, indent=2, default=str)

    if args.output:
        Path(args.output).write_text(json_str, encoding="utf-8")
        log.info("Saved → %s", args.output)
    else:
        print(json_str)

    print(
        f"\n{'═'*55}\n"
        f"  Matches found  : {len(matches)}\n"
        f"  Credential     : {args.credential or '(any)'}\n"
        f"  Last name      : {args.lastname   or '(any)'}\n"
        f"  First name     : {args.firstname  or '(any)'}\n"
        f"{'═'*55}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
