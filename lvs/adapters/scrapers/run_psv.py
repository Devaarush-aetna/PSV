"""PSV batch verification — main entry point.

Usage:
    python run_psv.py [--states AL MD NV ...] [--batch-size 10] [--timeout 45]
                      [--sequential] [--skip-rows N] [--max-rows N]
                      [--input path/to/Input.xlsx]

Input:  PSV_DEV/Input.xlsx  (PSV Tab sheet — same format as Copy_Exp_LIC_*.xlsx)
Output: PSV_DEV/PSV_Output_YYYYMMDD_HHMM.xlsx  (combined, all states)

Reads every row in Input.xlsx, groups by License State, and runs PSV
verification for each state that has boards configured in the routing table.
States with no boards (CAPTCHA-blocked or not yet inventoried) are written
as Fail rows with reason "No board configured — state skipped".

State-level results are written incrementally — each state is saved as it
completes, so partial results survive if a later state crashes.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# Add the scrapers directory to sys.path so engine/ and psv_test are importable
sys.path.insert(0, str(Path(__file__).parent))

from psv_test import (  # noqa: E402
    _load_routing,
    _ROUTING,
    CAPTCHA_PROV_TYPES,
    load_input_rows,
    load_configs_by_source_ids,
    run_state,
    run_state_orchestrated,
    write_results,
)
from engine.proxy import get_proxy_config  # noqa: E402
from orchestrator.output_emitter import OutputEmitter  # noqa: E402

log = logging.getLogger("run_psv")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

# Project root: scrapers/ → adapters/ → lvs/ → PSV_DEV/
PSV_DEV = Path(__file__).parents[3]
DEFAULT_INPUT = Path(__file__).parents[1] / "Input.xlsx"

# States where every board site blocks automated access (CAPTCHA / IP-block).
# No boards are in the inventory for these — rows will Fail with an explanation.
CAPTCHA_STATES = {"CA", "GA", "TN", "UT", "IA", "NE", "MT"}


def _build_state_summary(state_rows: dict[str, list[dict]]) -> None:
    log.info("Input breakdown by state:")
    for st in sorted(state_rows):
        flag = " [CAPTCHA-skip]" if st in CAPTCHA_STATES else ""
        log.info("  %s: %d rows%s", st, len(state_rows[st]), flag)


def _log_proxy_preflight(run_states: list[str]) -> None:
    """Log which boards across all planned states use proxy vs not, before any browser launches."""
    proxy_cfg = get_proxy_config()
    proxy_server = proxy_cfg.get("server") if proxy_cfg else None

    if proxy_server:
        log.info("Proxy configured: %s", proxy_server)
    else:
        log.info("Proxy: not configured (boards with proxy.enabled: true will warn at run time)")

    # Collect board ids needed across all states (best-effort: check routing)
    needed: set[str] = set()
    for st in run_states:
        if st in CAPTCHA_STATES:
            continue
        for (s, _), sids in _ROUTING.items():
            if s == st:
                needed.update(sids)

    if not needed:
        return

    configs = load_configs_by_source_ids(needed)
    proxy_on, proxy_off, proxy_warn = [], [], []
    for cfg in configs:
        sid = cfg.identity.source_id
        enabled = cfg.transport.proxy.enabled
        if enabled is False:
            proxy_off.append(sid)
        elif enabled is True:
            if proxy_cfg:
                proxy_on.append(sid)
            else:
                proxy_warn.append(sid)
        else:
            if proxy_cfg:
                proxy_on.append(sid)
            # else: silently skip (optional proxy, not configured)

    if proxy_on:
        log.info("  Proxy ON  (%d boards): %s", len(proxy_on), sorted(proxy_on))
    if proxy_off:
        log.info("  Proxy OFF (%d boards, config override): %s", len(proxy_off), sorted(proxy_off))
    if proxy_warn:
        log.warning(
            "  PROXY REQUIRED but not configured (%d boards): %s — "
            "add proxy.server to psv_config.yaml or set PROXY=proxy:9119",
            len(proxy_warn), sorted(proxy_warn),
        )


async def main(args: argparse.Namespace) -> None:
    input_path = Path(args.input)
    if not input_path.exists():
        log.error("Input file not found: %s", input_path)
        sys.exit(1)

    log.info("=== PSV Batch Verification ===")
    log.info("Input: %s", input_path)

    _load_routing()
    log.info("Routing table: %d (state,prov_type) entries", len(_ROUTING))

    # Load all rows — no state filter
    all_rows = load_input_rows(str(input_path), state_filter="", sheet_name=args.sheet)
    if not all_rows:
        log.error("No rows found in %s (expected sheet 'PSV Tab')", input_path)
        sys.exit(1)
    log.info("Total rows loaded: %d", len(all_rows))

    # Slice for --skip-rows / --max-rows (applied before state grouping)
    if args.skip_rows > 0:
        all_rows = all_rows[args.skip_rows:]
        log.info("Skipped first %d rows (--skip-rows), %d remaining", args.skip_rows, len(all_rows))
    if args.max_rows > 0:
        all_rows = all_rows[: args.max_rows]
        log.info("Capped at %d rows (--max-rows)", args.max_rows)

    # Group by state
    state_rows: dict[str, list[dict]] = defaultdict(list)
    for row in all_rows:
        state_rows[row["lic_state"].upper()].append(row)

    _build_state_summary(state_rows)

    # Determine which states to run
    run_states = [s for s in sorted(state_rows) if s]
    if args.states:
        requested = {s.upper() for s in args.states}
        run_states = [s for s in run_states if s in requested]
        log.info("Filtered to requested states: %s", run_states)

    # Pre-flight: show proxy plan for all boards in this run
    _log_proxy_preflight(run_states)

    # Expose proxy to NPPES client (reads LVS_PROXY_SERVER / PROXY env vars)
    import os as _os
    _proxy_cfg = get_proxy_config()
    if _proxy_cfg and _proxy_cfg.get("server"):
        _proxy_server = _proxy_cfg["server"]
        if not _proxy_server.startswith(("http://", "https://")):
            _proxy_server = f"http://{_proxy_server}"
        _os.environ.setdefault("PROXY", _proxy_server)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")

    # If --ai-mock provided, route the agent through canned responses.
    if args.ai_mock:
        import os as _os
        _os.environ["PSV_AI_MOCK_PATH"] = args.ai_mock

    input_stem = Path(args.input).stem
    run_id = args.run_id if args.run_id else f"{input_stem}_{timestamp}_001"

    if args.legacy_output:
        output_path = PSV_DEV / f"PSV_Output_{timestamp}.xlsx"
        log.info("Legacy single-Excel output: %s", output_path)
    else:
        log.info("Outputs at PSV_DEV/Output/%s/%s/  Evidence at PSV_DEV/Evidence/", timestamp[:6], run_id)

    emitter = None if args.legacy_output else OutputEmitter(run_id=run_id)

    total_pass = total_fail = total_skip = 0
    append = False  # legacy-output appender flag

    for state in run_states:
        rows = state_rows[state]

        # CAPTCHA-blocked states: write Skip rows and continue
        if state in CAPTCHA_STATES:
            log.warning("[%s] CAPTCHA-blocked — no boards in inventory, skipping %d rows", state, len(rows))
            if args.legacy_output:
                fail_rows = [{**r, "status": "Skip",
                              "reason": f"No board configured — {state} is CAPTCHA-blocked (not in inventory)",
                              "expiry_date": ""}
                             for r in rows]
                write_results(fail_rows, output_path, append)
                append = True
            else:
                # Emit each row to the manual channel via a lightweight trace
                from orchestrator.trace import RowTrace, make_master_row_id
                from orchestrator.output_emitter import RowOutcome
                for idx, row in enumerate(rows):
                    mri = make_master_row_id(idx, row.get("npi_no", ""))
                    tr = RowTrace(master_row_id=mri, run_id=run_id, state=state,
                                  prov_type=row.get("prov_type", ""),
                                  npi_no=row.get("npi_no", ""))
                    tr.final_outcome = "Skip"
                    tr.final_reason = "state_captcha_blocked"
                    emitter.collect(RowOutcome(master_row=row, master_row_id=mri, trace=tr))
            total_skip += len(rows)
            continue

        log.info("=" * 60)
        log.info("[%s] Starting — %d rows", state, len(rows))
        try:
            if args.legacy_output:
                passes, fails, skips = await run_state(
                    rows=rows, state=state, output_path=output_path,
                    append=append, batch_size=args.batch_size,
                    timeout=args.timeout, sequential=args.sequential,
                )
                append = True
            else:
                passes, fails, skips = await run_state_orchestrated(
                    rows=rows, state=state, emitter=emitter, run_id=run_id,
                    enable_nppes=not args.no_nppes,
                    enable_ai=not args.no_ai,
                    force_ai=args.force_ai,
                    timeout=args.timeout,
                )
            total_pass += passes
            total_fail += fails
            total_skip += skips
        except Exception as exc:
            log.error("[%s] Unhandled error — writing %d rows as Fail: %s", state, len(rows), exc, exc_info=True)
            if args.legacy_output:
                fail_rows = [{**r, "status": "Fail", "reason": f"State run error: {exc}",
                              "expiry_date": ""} for r in rows]
                write_results(fail_rows, output_path, append)
                append = True
            total_fail += len(rows)

    if emitter:
        paths = emitter.flush()
        log.info("Channel files written: %s", {k: str(v) for k, v in paths.items()})

    grand_total = total_pass + total_fail + total_skip
    log.info("=" * 60)
    log.info("=== ALL STATES COMPLETE ===")
    log.info("  Pass : %d", total_pass)
    log.info("  Fail : %d", total_fail)
    log.info("  Skip : %d", total_skip)
    log.info("  Total: %d", grand_total)
    verifiable = total_pass + total_fail
    if verifiable:
        log.info("  Rate : %.1f%%", 100.0 * total_pass / verifiable)


def cli() -> None:
    p = argparse.ArgumentParser(
        description="PSV batch verification — runs all states from Input.xlsx",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--input", default=str(DEFAULT_INPUT),
        help=f"Input Excel file (default: {DEFAULT_INPUT})",
    )
    p.add_argument(
        "--states", nargs="+", metavar="ST",
        help="Limit run to specific state(s), e.g. --states MD NV FL",
    )
    p.add_argument(
        "--batch-size", type=int, default=10,
        help="Rows per batch before writing results (default: 10)",
    )
    p.add_argument(
        "--timeout", type=int, default=120,
        help="Per-board search timeout in seconds (default: 120)",
    )
    p.add_argument(
        "--sequential", action="store_true",
        help="Process rows one at a time (use for high-concurrency boards)",
    )
    p.add_argument(
        "--skip-rows", type=int, default=0,
        help="Skip the first N rows of the input (default: 0)",
    )
    p.add_argument(
        "--max-rows", type=int, default=0,
        help="Stop after processing N rows total (0 = all)",
    )
    p.add_argument(
        "--sheet", default="",
        help="Sheet name to read (default: 'PSV Tab')",
    )
    # --- Orchestrator flags ---
    p.add_argument(
        "--no-ai", action="store_true",
        help="Skip the AI agent fallback — rule-based + NPPES only",
    )
    p.add_argument(
        "--no-nppes", action="store_true",
        help="Skip the universal NPPES fetch — rule-based only",
    )
    p.add_argument(
        "--force-ai", action="store_true",
        help="Invoke the AI agent even when the rule ladder resolves",
    )
    p.add_argument(
        "--ai-mock", default=None,
        help="Path to JSON file of mock AI tool-call responses (for python-only testing)",
    )
    p.add_argument(
        "--legacy-output", action="store_true",
        help="Write the legacy single PSV_Output_*.xlsx instead of the 4-channel layout",
    )
    p.add_argument(
        "--run-id", default=None,
        help="Override the auto-generated run_id (useful for parallel runs to avoid collisions)",
    )
    args = p.parse_args()
    asyncio.run(main(args))


if __name__ == "__main__":
    cli()
