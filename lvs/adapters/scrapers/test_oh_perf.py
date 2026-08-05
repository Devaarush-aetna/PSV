"""Performance test: OH CSV load + license lookup — before vs after caching fixes.

Run from the scrapers directory:
    python test_oh_perf.py
"""
import sys
import time
from pathlib import Path

CSV_PATH = Path(
    r"C:\Users\n676150\Downloads\PSV_TEST_Sprint_1_Final 1"
    r"\PSV_TEST_Sprint_1_Final\PSV_TEST_Sprint_1\PSV_TEST"
    r"\PSV_DEV\PSV\CSVS\OH_PROVIDERS_INDIVIDUAL_20260727_1946.csv"
)

# License numbers from the 20260728_1330_001 traces
LICENSE_NUMS = [
    "RN.364874",   # row_0000
    "DC-04564",    # row_0001
    "34.014490",   # row_0002
    "E.2404131",   # row_0003
    # extra queries to show amortisation
    "RN.364874",
    "DC-04564",
    "34.014490",
    "E.2404131",
]

COL = "LICENSE_NUMBER"


def _load_once(path: Path):
    import pandas as pd
    # na_filter=False avoids the fillna() pass that OOMs on low-memory machines
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig", on_bad_lines="skip", na_filter=False)
    df.columns = df.columns.str.strip()
    return df


def _search_old(df, col: str, num: str):
    """Original O(n) string-scan approach."""
    import re
    c = col
    col_s = df[c].str.strip()
    result = df[col_s.str.upper() == num.strip().upper()]
    if not result.empty:
        return result.to_dict(orient="records")
    target_norm = num.strip().lstrip("0") or "0"
    result = df[col_s.str.lstrip("0").str.upper() == target_norm.upper()]
    if not result.empty:
        return result.to_dict(orient="records")
    result = df[col_s.str.upper().str.contains(num.strip().upper(), regex=False, na=False)]
    if not result.empty:
        return result.to_dict(orient="records")
    target_stripped = re.sub(r"[-\s]", "", num.strip()).upper()
    result = df[col_s.str.replace(r"[-\s]", "", regex=True).str.upper() == target_stripped]
    if not result.empty:
        return result.to_dict(orient="records")
    return []


def run_old_simulation():
    """Simulates the OLD behaviour: measure one full CSV load + all 4 scans.

    Loading the file 4 times in sequence exhausts RAM on the test machine
    (741 MB × pandas overhead = ~2 GB per load).  Instead we load once and
    run all 4 scans — that accurately captures the *search* overhead while
    keeping memory sane.  The load time is the dominant per-record cost and
    is shown separately so it can be multiplied by the record count.
    """
    print("\n" + "=" * 60)
    print("OLD BEHAVIOUR  (load CSV each call + full string-scan)")
    print("=" * 60)

    # Time the load (paid on every record in the old code)
    t_load0 = time.perf_counter()
    df = _load_once(CSV_PATH)
    t_load = time.perf_counter() - t_load0
    print(f"  CSV load time        : {t_load:.2f}s  &lt;-- paid on EVERY record")

    # Time the search passes
    scan_times = []
    for i, lic in enumerate(LICENSE_NUMS[:4]):
        t0 = time.perf_counter()
        hits = _search_old(df, COL, lic)
        elapsed = time.perf_counter() - t0
        scan_times.append(elapsed)
        print(f"  [{i+1}] {lic:<20}  hits={len(hits):>3}  scan={elapsed:.2f}s")

    avg_scan = sum(scan_times) / len(scan_times)
    per_record = t_load + avg_scan
    print(f"\n  Avg scan per-record  : {avg_scan:.2f}s")
    print(f"  load + scan (old)    : {per_record:.2f}s per record")

    del df   # free before new test
    import gc; gc.collect()
    return t_load, avg_scan


def run_new_simulation():
    """Simulates the NEW behaviour: cached DF + index-based lookup."""
    # Import the real updated functions (with caches)
    sys.path.insert(0, str(Path(__file__).parent))
    from engine.csv_extractor import load_csv, search_by_license_number, _DF_CACHE, _LIC_IDX_CACHE

    print("\n" + "=" * 60)
    print("NEW BEHAVIOUR  (cached DF + O(1) index lookup)")
    print("=" * 60)

    # Clear caches to get a clean baseline
    _DF_CACHE.clear()
    _LIC_IDX_CACHE.clear()

    times = []
    for i, lic in enumerate(LICENSE_NUMS):
        t0 = time.perf_counter()
        df = load_csv(CSV_PATH, encoding="utf-8-sig", header_row=0, sep=",")
        hits = search_by_license_number(df, COL, lic)
        elapsed = time.perf_counter() - t0
        times.append(elapsed)
        tag = "(first — load+index build)" if i == 0 else ("(cache+index warm)" if i == 1 else "(cache+index)")
        print(f"  [{i+1}] {lic:<20}  hits={len(hits):>3}  time={elapsed:.3f}s  {tag}")

    print(f"\n  First call (one-time cost): {times[0]:.2f}s")
    print(f"  Average for calls 2-{len(times)}: {sum(times[1:])/max(len(times)-1,1):.3f}s")
    print(f"  Total for {len(times)} records: {sum(times):.2f}s")
    return times


if __name__ == "__main__":
    if not CSV_PATH.exists():
        print(f"ERROR: CSV not found: {CSV_PATH}")
        sys.exit(1)

    t_load_old, avg_scan_old = run_old_simulation()
    new_times = run_new_simulation()

    old_per_record = t_load_old + avg_scan_old
    new_avg_warm = sum(new_times[1:]) / max(len(new_times) - 1, 1)
    new_first = new_times[0]

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Old: load({t_load_old:.2f}s) + scan({avg_scan_old:.2f}s) = {old_per_record:.2f}s per record")
    print(f"  New: first call {new_first:.2f}s, warm calls {new_avg_warm:.3f}s each")
    speedup = old_per_record / new_avg_warm if new_avg_warm > 0 else float("inf")
    print(f"  Speedup (warm cache)   : {speedup:.0f}x")
    print(f"  Estimated 100-record batch:")
    print(f"    Old : {old_per_record * 100 / 60:.1f} minutes")
    print(f"    New : {(new_first + new_avg_warm * 99) / 60:.1f} minutes")
