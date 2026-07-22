"""Run multiple psv_test.py state batches in parallel.

Usage:
    python run_parallel.py

Modify RUNS below to add/remove states or change input/output paths.
Results are printed as each state finishes.
"""
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).parents[4]
SCRIPT = ROOT / "lvs/adapters/scrapers/psv_test.py"

RUNS = [
    {"state": "FL", "input": "Input_FL_500.xlsx", "output": "psv_FL_500_out.xlsx"},
    {"state": "KS", "input": "Input_KS_500.xlsx", "output": "psv_KS_500_out.xlsx"},
    {"state": "KY", "input": "Input_KY_500.xlsx", "output": "psv_KY_500_out.xlsx"},
    {"state": "NV", "input": "Input_NV_500.xlsx", "output": "psv_NV_500_out.xlsx"},
    {"state": "WY", "input": "Input_WY_500.xlsx", "output": "psv_WY_500_out.xlsx"},
    {"state": "MD", "input": "Input_MD_500.xlsx", "output": "psv_MD_500_out.xlsx"},
]


def run_state(cfg: dict) -> dict:
    state = cfg["state"]
    log_path = ROOT / f"psv_{state}_run.log"
    cmd = [
        sys.executable, str(SCRIPT),
        "--input",  str(ROOT / cfg["input"]),
        "--state",  state,
        "--output", str(ROOT / cfg["output"]),
        "--sheet",  "PSV Tab",
    ]
    print(f"[{state}] Started")
    with open(log_path, "w", encoding="utf-8") as log:
        result = subprocess.run(cmd, cwd=str(ROOT), stdout=log, stderr=log)
    status = "OK" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
    print(f"[{state}] {status} — log: {log_path.name}")
    return {"state": state, "status": status, "log": str(log_path)}


if __name__ == "__main__":
    print(f"Launching {len(RUNS)} states in parallel...\n")
    with ThreadPoolExecutor(max_workers=len(RUNS)) as pool:
        futures = {pool.submit(run_state, cfg): cfg["state"] for cfg in RUNS}
        for future in as_completed(futures):
            r = future.result()

    print("\nAll done.")
