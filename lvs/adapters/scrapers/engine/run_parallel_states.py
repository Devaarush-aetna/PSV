"""
run_parallel_states.py — Launch PSV verification for multiple states in parallel.

Each state gets its own Input_<PREFIX>_<STATE>.xlsx and a unique run_id so their
output channels land in separate Output/ subdirectories.

Usage:
    python run_parallel_states.py                          # default: NV KY FL KS, prefix 200
    python run_parallel_states.py --states FL KS KY --prefix 100
    python run_parallel_states.py --no-ai --states NV KY

Logs per process are written to:
    PSV_DEV/logs/run_<STATE>_<run_id>.log

After all processes complete, prints a summary table.
"""

import argparse
import subprocess
import sys
import time
import threading
import os
from datetime import datetime
from pathlib import Path

PSV_DEV = Path(__file__).parents[4]
RUN_PSV = PSV_DEV / "lvs" / "adapters" / "scrapers" / "run_psv.py"
PYTHON = PSV_DEV / ".venv" / "Scripts" / "python.exe"
LOGS_DIR = PSV_DEV / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# --- CLI parsing -----------------------------------------------------------
_parser = argparse.ArgumentParser(add_help=True)
_parser.add_argument("--states", nargs="+", default=["NV", "KY", "FL", "KS"],
                     metavar="ST", help="State abbreviations to run (default: NV KY FL KS)")
_parser.add_argument("--prefix", default="200",
                     help="Row-count prefix on Input files, e.g. 100 → Input_100_<STATE>.xlsx (default: 200)")
_parser.add_argument("--no-ai", action="store_true", help="Pass --no-ai to run_psv.py")
_parser.add_argument("--no-nppes", action="store_true", help="Pass --no-nppes to run_psv.py")
_parser.add_argument("--force-ai", action="store_true", help="Pass --force-ai to run_psv.py")
_args = _parser.parse_args()

STATES = [s.upper() for s in _args.states]
PREFIX = _args.prefix

extra_flags = []
if _args.no_ai:
    extra_flags.append("--no-ai")
if _args.no_nppes:
    extra_flags.append("--no-nppes")
if _args.force_ai:
    extra_flags.append("--force-ai")


def stream_log(proc, log_path: Path, state: str):
    """Stream stdout+stderr from proc into log_path and to console with [STATE] prefix."""
    with open(log_path, "w", encoding="utf-8") as f:
        for line in iter(proc.stdout.readline, b""):
            text = line.decode("utf-8", errors="replace").rstrip()
            f.write(text + "\n")
            f.flush()
            safe = text.encode("ascii", errors="replace").decode("ascii")
            print(f"[{state}] {safe}", flush=True)


def launch(state: str, run_id: str) -> tuple:
    input_file = PSV_DEV / f"Input_{PREFIX}_{state}.xlsx"
    if not input_file.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")
    log_path = LOGS_DIR / f"run_{state}_{run_id}.log"

    cmd = [
        str(PYTHON), str(RUN_PSV),
        "--input", str(input_file),
        "--states", state,
        "--run-id", run_id,
        *extra_flags,
    ]
    print(f"[{state}] Starting: {' '.join(cmd)}")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(PSV_DEV / "lvs" / "adapters" / "scrapers"),
    )
    start_time = time.time()

    t = threading.Thread(target=stream_log, args=(proc, log_path, state), daemon=True)
    t.start()

    return proc, log_path, start_time, t


def main():
    launch_ts = datetime.now().strftime("%Y%m%d_%H%M")

    print("=" * 70)
    print(f"PSV Parallel Run — {launch_ts}")
    print(f"States: {STATES}  |  Prefix: {PREFIX}  |  Extra flags: {extra_flags or 'none'}")
    print("=" * 70)

    # Pre-flight: verify all input files exist before launching anything
    missing = [s for s in STATES if not (PSV_DEV / f"Input_{PREFIX}_{s}.xlsx").exists()]
    if missing:
        for s in missing:
            print(f"ERROR: Input_{PREFIX}_{s}.xlsx not found in {PSV_DEV}")
        sys.exit(2)

    jobs = {}
    for state in STATES:
        run_id = f"{launch_ts}_{state}_001"
        proc, log_path, start_time, thread = launch(state, run_id)
        jobs[state] = {"proc": proc, "log": log_path, "start": start_time,
                       "thread": thread, "run_id": run_id}
        time.sleep(0.5)  # tiny stagger to avoid filesystem race on startup

    print(f"\nAll {len(STATES)} processes launched. Waiting for completion...\n")

    results = {}
    for state, info in jobs.items():
        rc = info["proc"].wait()
        info["thread"].join(timeout=5)
        elapsed = time.time() - info["start"]
        results[state] = {"rc": rc, "elapsed": elapsed, "run_id": info["run_id"],
                          "log": info["log"]}
        status = "DONE" if rc == 0 else f"EXIT({rc})"
        print(f"[{state}] {status} in {elapsed:.0f}s — run_id={info['run_id']}")

    print("\n" + "=" * 70)
    print("PARALLEL RUN SUMMARY")
    print("=" * 70)
    print(f"{'State':<6} {'Status':<12} {'Elapsed':>10}  {'Run ID'}")
    print("-" * 70)
    all_ok = True
    for state in STATES:
        r = results[state]
        status = "OK" if r["rc"] == 0 else f"FAILED({r['rc']})"
        if r["rc"] != 0:
            all_ok = False
        print(f"{state:<6} {status:<12} {r['elapsed']:>8.0f}s  {r['run_id']}")
    print("-" * 70)
    print(f"Overall: {'ALL PASSED' if all_ok else 'SOME FAILED'}")
    print()

    # Print output locations
    print("Output directories:")
    output_root = PSV_DEV / "Output"
    for state in STATES:
        r = results[state]
        ym = r["run_id"][:6]  # YYYYMM
        out_dir = output_root / ym / r["run_id"]
        exists = "exists" if out_dir.exists() else "NOT FOUND"
        print(f"  {state}: {out_dir}  [{exists}]")
    print()
    print("Log files:")
    for state in STATES:
        print(f"  {state}: {results[state]['log']}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
