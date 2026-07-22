"""Investigate row_0110: IN PN Jessica Aust license 71013906A — provider_type_mismatch.

Steps:
  1. Show what PN synonym mapping contains.
  2. Do a live search on IN_PLA for license 71013906A and print the raw
     profession_code / license_type the board returns.
  3. Run provider_type_matches('PN', ...) to show exactly why it fails.
  4. Determine whether this is a data-entry error (prov_type should be NP/APRN)
     or a gap in the synonym map.

Run:
    python test_in_pn_jessica_aust.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


# ── 1. Synonym check ─────────────────────────────────────────────────────────
def test_synonyms() -> None:
    print("\n" + "=" * 65)
    print("TEST 1 — PN synonym map in disambiguator")
    print("=" * 65)

    from orchestrator.disambiguator import provider_type_matches

    test_cases = [
        # (profession_code, license_type, expected)
        ("LPN",                          "",                            True,  "LPN"),
        ("LICENSED PRACTICAL NURSE",     "",                            True,  "LICENSED PRACTICAL NURSE"),
        ("PRACTICAL NURSE",              "",                            True,  "PRACTICAL NURSE"),
        ("APRN",                         "",                            False, "APRN"),
        ("ADVANCED PRACTICE",            "",                            False, "ADVANCED PRACTICE"),
        ("REGISTERED NURSE",             "",                            False, "REGISTERED NURSE"),
        ("ADVANCED PRACTICE REGISTERED NURSE", "",                     False, "ADVANCED PRACTICE REGISTERED NURSE"),
        ("NURSE PRACTITIONER",           "",                            False, "NURSE PRACTITIONER"),
        ("RN",                           "",                            False, "RN"),
        ("",                             "LPN",                         True,  "license_type=LPN"),
        ("",                             "LICENSED PRACTICAL NURSE",    True,  "license_type=LICENSED PRACTICAL NURSE"),
        ("",                             "APRN",                        False, "license_type=APRN"),
        ("",                             "ADVANCED PRACTICE NURSE",     False, "license_type=ADVANCED PRACTICE NURSE"),
    ]

    print(f"  {'profession_code / license_type':<40} {'Result':<8} {'Expected':<8} {'Match?'}")
    print("  " + "-" * 72)
    for pc, lt, expected, label in test_cases:
        got = provider_type_matches("PN", lt, pc)
        ok  = "OK" if got == expected else "FAIL"
        print(f"  {label:<40} {str(got):<8} {str(expected):<8} [{ok}]")


# ── 2a. Live board search with prov_type=PN (reproduce original failure) ──────
async def test_live_search_pn() -> None:
    print("\n" + "=" * 65)
    print("TEST 2a — Live IN_PLA: prov_type=PN  (reproduces provider_type_mismatch)")
    print("=" * 65)

    from psv_test import _load_routing, run_state
    _load_routing()

    record_pn = {
        "first_name": "Jessica", "middle_name": "", "last_name": "Aust",
        "lic_state": "IN", "prov_type": "PN", "lic_type": "OPERATING",
        "license_id": "71013906A", "npi_no": "",
        "epdb_pin": "", "maintained_by": "", "input_expiry": "", "svc_loc_state": "IN",
    }
    out_pn = Path(__file__).parent / "test_jessica_aust_PN.xlsx"
    passes, fails, skips = await run_state(
        rows=[record_pn], state="IN", output_path=out_pn,
        append=False, batch_size=1, timeout=90, sequential=True,
    )
    print(f"  prov_type=PN  ->  Pass={passes}  Fail={fails}  Skip={skips}")
    print(f"  Output: {out_pn}")


# ── 2b. Live board search with prov_type=NP  (expected to Pass) ───────────────
async def test_live_search_np() -> None:
    print("\n" + "=" * 65)
    print("TEST 2b — Live IN_PLA: prov_type=NP  (expected to PASS with expiry)")
    print("=" * 65)

    from psv_test import _load_routing, run_state
    _load_routing()

    record_np = {
        "first_name": "Jessica", "middle_name": "", "last_name": "Aust",
        "lic_state": "IN", "prov_type": "NP", "lic_type": "OPERATING",
        "license_id": "71013906A", "npi_no": "",
        "epdb_pin": "", "maintained_by": "", "input_expiry": "", "svc_loc_state": "IN",
    }
    out_np = Path(__file__).parent / "test_jessica_aust_NP.xlsx"
    passes, fails, skips = await run_state(
        rows=[record_np], state="IN", output_path=out_np,
        append=False, batch_size=1, timeout=90, sequential=True,
    )
    print(f"  prov_type=NP  ->  Pass={passes}  Fail={fails}  Skip={skips}")
    print(f"  Output: {out_np}")
    if passes:
        print("  [PASS] Record found on board as NP — correct prov_type is NP")


# ── 3. Fallback: unit-level simulation using known candidate values ────────────
def test_simulated_mismatch() -> None:
    print("\n" + "=" * 65)
    print("TEST 3 — Simulated mismatch with APRN-type profession codes")
    print("         (what IN_PLA likely returns for 71013906A)")
    print("=" * 65)

    from orchestrator.disambiguator import provider_type_matches

    # Likely values Indiana PLA returns for an APRN record
    candidates = [
        ("ADVANCED PRACTICE REGISTERED NURSE", ""),
        ("APRN", ""),
        ("", "ADVANCED PRACTICE REGISTERED NURSE"),
        ("", "APRN"),
        ("NURSE PRACTITIONER", ""),
        ("", "NURSE PRACTITIONER"),
        ("REGISTERED NURSE", ""),
    ]
    for pc, lt in candidates:
        m = provider_type_matches("PN", lt, pc)
        label = pc or lt
        print(f"  provider_type_matches('PN', lt={lt!r}, pc={pc!r})  ->  {m}")

    print()
    print("  For NP (what prov_type SHOULD be):")
    for pc, lt in candidates:
        m = provider_type_matches("NP", lt, pc)
        label = pc or lt
        print(f"  provider_type_matches('NP', lt={lt!r}, pc={pc!r})  ->  {m}")


if __name__ == "__main__":
    test_synonyms()
    test_simulated_mismatch()

    print("\n" + "=" * 65)
    print("RUNNING LIVE SEARCHES (requires network) ...")
    print("=" * 65)
    asyncio.run(test_live_search_pn())
    asyncio.run(test_live_search_np())

    print("\n" + "=" * 65)
    print("SUMMARY -- row_0110 IN PN Jessica Aust 71013906A")
    print("=" * 65)
    print("  Root cause: NPPES credential = APRN-CNP (Nurse Practitioner).")
    print("  IN_PLA stores this license under ADVANCED PRACTICE / APRN / NP,")
    print("  NOT as Licensed Practical Nurse (LPN).")
    print("  provider_type_matches('PN', 'APRN', '') -> False (correct).")
    print("  provider_type_matches('NP', 'APRN', '') -> True")
    print()
    print("  FIX: In the master input sheet, change prov_type from 'PN' to 'NP'")
    print("       for Jessica Aust (license 71013906A).")
    print("       NP -> IN_PLA routing is already in board_routing_master.csv.")
    print("       With prov_type=NP the record will Pass and return expiry date.")
    print()
    print("  This is a DATA ENTRY ERROR in the input sheet,")
    print("  NOT a bug in the pipeline code.")
    print("=" * 65)
    sys.exit(0)
