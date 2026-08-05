"""
generate_input.py — Build proportionate input files from the source license Excel.

Usage:
    python generate_input.py --source "C:/path/to/Exp_Lic.xlsx" --state OH --count 350
    python generate_input.py --source "C:/path/to/Exp_Lic.xlsx" --state OH CT MA --count 350 100 50
    python generate_input.py --source "C:/path/to/Exp_Lic.xlsx" --state OH --count 10 20 50 1000

    # Guarantee at least 1 row per routable provider type (default when routing CSV is found):
    python generate_input.py --source "C:/path/to/Exp_Lic.xlsx" --state NC --count 250 --min-per-type 1

Rules:
  - Provider types whose proportionate share rounds to 0 are included in full (no provider left out).
  - Remaining slots are distributed among larger providers using the Largest Remainder Method.
  - --min-per-type reserves N rows per *routable* type first, then fills the remainder proportionately.
  - Output files are saved next to the source file as  Input_<count>_<STATE>.xlsx
"""

import argparse
import csv
import math
import sys
from pathlib import Path

import pandas as pd

# Default routing master path (relative to this script)
_DEFAULT_ROUTING = Path(__file__).parent / "board_routing_master.csv"


def _load_captcha_prov_types() -> set:
    """Return set of (state, prov_type) pairs that are CAPTCHA-blocked, from psv_test.py."""
    try:
        from psv_test import CAPTCHA_PROV_TYPES  # type: ignore
        return {(s.upper(), pt.upper()) for s, pt in CAPTCHA_PROV_TYPES}
    except Exception:
        return set()


def _load_routable_types(routing_csv: Path, state: str) -> set:
    """Return the set of provider type codes that have a board configured for `state`."""
    routable = set()
    try:
        with open(routing_csv, newline="", encoding="utf-8") as f:
            for row in csv.reader(f):
                if len(row) >= 2 and row[0].strip().upper() == state.upper():
                    routable.add(row[1].strip().upper())
    except FileNotFoundError:
        pass
    return routable


def _largest_remainder(counts: dict, slots: int) -> dict:
    """Allocate `slots` integers among keys proportionate to their counts."""
    total = sum(counts.values())
    if total == 0:
        return {k: 0 for k in counts}
    raw = {pt: (cnt / total) * slots for pt, cnt in counts.items()}
    floor_allocs = {pt: math.floor(v) for pt, v in raw.items()}
    leftover = slots - sum(floor_allocs.values())
    by_frac = sorted(raw.keys(), key=lambda pt: -(raw[pt] - floor_allocs[pt]))
    for i in range(leftover):
        floor_allocs[by_frac[i]] += 1
    return floor_allocs


def proportionate_sample(
    df_state: pd.DataFrame,
    target: int,
    routable_types: set = None,
    captcha_types: set = None,
    min_per_type: int = 0,
) -> pd.DataFrame:
    """Return up to `target` rows sampled proportionately by Provider Type.

    If `routable_types` and `min_per_type > 0`, reserves min_per_type rows for
    every routable (non-CAPTCHA) type that exists in the data before proportionate fill.
    """
    pt_counts = df_state["Provider Type"].value_counts()
    total = len(df_state)

    if total == 0:
        return df_state.iloc[0:0]

    if total <= target:
        print(f"  NOTE: only {total} records available — returning all of them.")
        return df_state.copy().reset_index(drop=True)

    frames = []
    reserved_rows = set()  # track index set already reserved

    # --- Phase 1: min-per-type guarantee for routable (non-CAPTCHA) types ---
    if routable_types and min_per_type > 0:
        # Exclude CAPTCHA-blocked types from the guarantee
        eligible_routable = {
            pt for pt in routable_types
            if not (captcha_types and (df_state["License State"].iloc[0].upper(), pt.upper()) in captcha_types)
        }
        present_routable = [
            pt for pt in eligible_routable
            if pt in pt_counts.index
        ]
        missing_routable = [
            pt for pt in eligible_routable
            if pt not in pt_counts.index
        ]

        guaranteed = {}
        for pt in present_routable:
            rows = df_state[df_state["Provider Type"] == pt]
            n = min(min_per_type, len(rows))
            sample = rows.sample(n=n, random_state=42)
            guaranteed[pt] = n
            reserved_rows.update(sample.index)
            frames.append(sample)

        total_reserved = sum(guaranteed.values())
        remaining_slots = target - total_reserved

        if missing_routable:
            print(f"  NOTE: {len(missing_routable)} routable type(s) have no source records "
                  f"and cannot be sampled: {', '.join(sorted(missing_routable))}")

        if remaining_slots < 0:
            print(f"  WARNING: min-per-type reservations ({total_reserved}) exceed target "
                  f"({target}). Returning all guaranteed rows ({total_reserved} rows).")
            result = pd.concat(frames, ignore_index=True)
            return result.sample(frac=1, random_state=42).reset_index(drop=True)

        # Phase 2: fill remainder proportionately from the non-reserved pool
        df_remaining = df_state.loc[~df_state.index.isin(reserved_rows)]
        if remaining_slots > 0 and not df_remaining.empty:
            remainder_sample = _proportionate_fill(df_remaining, remaining_slots)
            frames.append(remainder_sample)

        result = pd.concat(frames, ignore_index=True)
        return result.sample(frac=1, random_state=42).reset_index(drop=True)

    # --- Original logic (no min-per-type) ---
    return _proportionate_fill(df_state, target)


def _proportionate_fill(df: pd.DataFrame, target: int) -> pd.DataFrame:
    """Pure proportionate sample of `df` down to `target` rows (LRM)."""
    pt_counts = df["Provider Type"].value_counts()
    total = len(df)

    if total <= target:
        return df.copy().reset_index(drop=True)

    small, large = {}, {}
    for pt, cnt in pt_counts.items():
        if math.floor((cnt / total) * target) == 0:
            small[pt] = cnt
        else:
            large[pt] = cnt

    small_total = sum(small.values())
    remaining_slots = target - small_total

    frames = []
    if remaining_slots >= len(large):
        for pt in small:
            frames.append(df[df["Provider Type"] == pt])
        floor_allocs = _largest_remainder(large, remaining_slots) if large else {}
        for pt, n in floor_allocs.items():
            rows = df[df["Provider Type"] == pt]
            frames.append(rows.sample(n=n, random_state=42))
    else:
        print(f"  NOTE: target {target} is small relative to {len(pt_counts)} provider types — "
              f"using pure proportionate allocation (some rare types may get 0).")
        all_counts = dict(pt_counts)
        allocs = _largest_remainder(all_counts, target)
        for pt, n in allocs.items():
            if n == 0:
                continue
            rows = df[df["Provider Type"] == pt]
            frames.append(rows.sample(n=min(n, len(rows)), random_state=42))

    result = pd.concat(frames, ignore_index=True)
    return result.sample(frac=1, random_state=42).reset_index(drop=True)


def print_distribution(
    df: pd.DataFrame,
    routable_types: set = None,
    captcha_types: set = None,
    state: str = "",
) -> None:
    counts = df["Provider Type"].value_counts()
    total = len(df)
    print(f"\n  {'Provider Type':<14} {'Count':>6}  {'%':>6}  {'Status':>8}")
    print(f"  {'-'*14}  {'-'*6}  {'-'*6}  {'-'*8}")
    no_routing = []
    captcha_list = []
    for pt, cnt in counts.items():
        status = ""
        if routable_types is not None:
            pt_up = pt.upper()
            is_captcha = captcha_types and (state.upper(), pt_up) in captcha_types
            if is_captcha:
                status = "CAPTCHA"
                captcha_list.append(pt)
            elif pt_up in routable_types:
                status = "YES"
            else:
                status = "NO *"
                no_routing.append(pt)
        print(f"  {pt:<14} {cnt:>6}  {cnt/total*100:>5.1f}%  {status:>8}")
    print(f"  {'TOTAL':<14} {total:>6}")
    if captcha_list:
        print(f"\n  CAPTCHA — will be auto-skipped at runtime: {', '.join(captcha_list)}")
    if no_routing:
        print(f"  * NO ROUTING — these rows will fall out: {', '.join(no_routing)}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate proportionate PSV input files by state and record count."
    )
    parser.add_argument(
        "--source", required=True,
        help="Path to the source Excel file (e.g. Exp_Lic_06302026.xlsx)"
    )
    parser.add_argument(
        "--state", nargs="+", required=True,
        help="One or more state codes (e.g. OH CT MA)"
    )
    parser.add_argument(
        "--count", nargs="+", type=int, required=True,
        help="One or more record counts (e.g. 350  or  10 20 50 1000)"
    )
    parser.add_argument(
        "--out-dir", default=None,
        help="Output directory (default: same folder as --source)"
    )
    parser.add_argument(
        "--min-per-type", type=int, default=1, metavar="N",
        help="Guarantee at least N rows per routable provider type before proportionate fill "
             "(default: 1; set 0 to disable)"
    )
    parser.add_argument(
        "--routing", default=str(_DEFAULT_ROUTING), metavar="CSV",
        help=f"Path to board_routing_master.csv (default: {_DEFAULT_ROUTING})"
    )

    args = parser.parse_args()

    source_path = Path(args.source)
    if not source_path.exists():
        print(f"ERROR: source file not found: {source_path}")
        sys.exit(1)

    routing_path = Path(args.routing)

    out_dir = Path(args.out_dir) if args.out_dir else source_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading {source_path.name} …", end=" ", flush=True)
    df = pd.read_excel(source_path)
    print(f"{len(df):,} rows loaded.")

    captcha_types = _load_captcha_prov_types()

    states = [s.upper() for s in args.state]
    counts = args.count

    jobs = [(st, ct) for st in states for ct in counts]

    generated = []
    for state, count in jobs:
        df_state = df[df["License State"] == state].copy().reset_index(drop=True)

        if df_state.empty:
            print(f"\n[{state}] SKIP — no records found for this state.")
            continue

        routable_types = _load_routable_types(routing_path, state) if routing_path.exists() else None
        state_captcha = {pt for (st, pt) in captcha_types if st == state}

        print(f"\n[{state}] {len(df_state):,} records available -> targeting {count}")
        if routable_types:
            non_captcha_routable = routable_types - state_captcha
            print(f"  Routing master: {len(routable_types)} type(s) for {state} "
                  f"({len(state_captcha)} CAPTCHA-blocked, {len(non_captcha_routable)} automatable)  "
                  f"--min-per-type {args.min_per_type}")

        sample = proportionate_sample(
            df_state, count,
            routable_types=routable_types,
            captcha_types=captcha_types,
            min_per_type=args.min_per_type,
        )

        out_name = f"Input_{count}_{state}.xlsx"
        out_path = out_dir / out_name
        sample.to_excel(out_path, index=False)

        print_distribution(sample, routable_types, captcha_types, state)
        print(f"\n  Saved -> {out_path}")
        generated.append(str(out_path))

    print(f"\n{'='*60}")
    print(f"Done. {len(generated)} file(s) generated:")
    for f in generated:
        print(f"  {f}")


if __name__ == "__main__":
    main()
