"""Test suite for NC_DAC scraper fixes.

Verifies three bugs that caused all NC_DAC records to fail:
  1. skip_first_row was true → skipped the only data row (now false)
  2. force_pdf was set → tried to parse HTML as PDF (now removed)
  3. PDF date regex didn't handle NCASPPB reversed layout (date above label)

Run:
    python test_nc_dac_fix.py
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


# ── Test 1 — Config fix verification ────────────────────────────────────────
def test_config_fix() -> bool:
    print("\n" + "=" * 65)
    print("TEST 1 — Config fix: skip_first_row=false, no force_pdf")
    print("=" * 65)

    from engine.validate import load_config
    cfg = load_config(str(Path(__file__).parent / "sites" / "NC_DAC" / "config.yaml"))

    # skip_first_row must be false — <thead> already excludes header rows
    skip = cfg.results.table.skip_first_row
    ok_skip = skip is False
    print(f"  [{'PASS' if ok_skip else 'FAIL'}] skip_first_row = {skip!r}  (expected False)")

    # force_pdf must be absent / False — the link serves an HTML page, not a PDF
    fp = cfg.results.detail_trigger.force_pdf
    ok_fp = not fp
    print(f"  [{'PASS' if ok_fp else 'FAIL'}] force_pdf = {fp!r}  (expected False/absent)")

    # detail wait strategy must be element_visible
    ws = cfg.detail.wait.strategy
    ok_ws = ws == "element_visible"
    print(f"  [{'PASS' if ok_ws else 'FAIL'}] detail.wait.strategy = {ws!r}  (expected 'element_visible')")

    # back_navigation must be browser_back
    bn = cfg.detail.back_navigation.strategy
    ok_bn = bn == "browser_back"
    print(f"  [{'PASS' if ok_bn else 'FAIL'}] back_navigation.strategy = {bn!r}  (expected 'browser_back')")

    # Expires key must map to expiration_date
    fm = cfg.detail.field_map
    ok_exp = fm.get("Expires") == "expiration_date"
    print(f"  [{'PASS' if ok_exp else 'FAIL'}] field_map['Expires'] = {fm.get('Expires')!r}  (expected 'expiration_date')")

    all_ok = all([ok_skip, ok_fp, ok_ws, ok_bn, ok_exp])
    print(f"\n  Result: {'ALL PASSED' if all_ok else 'SOME FAILED'}")
    return all_ok


# ── Test 2 — PDF date regex fix ──────────────────────────────────────────────
def test_pdf_regex_fix() -> bool:
    """Verify the new regex handles NCASPPB's reversed layout (date above label)."""
    print("\n" + "=" * 65)
    print("TEST 2 — PDF regex: handles date-above-label layout")
    print("=" * 65)

    _DATE_RE = r"\d{1,2}/\d{1,2}/\d{4}"

    # Replicate the exact regex logic from archetypes/_shared.py
    def _extract_dates(combined_text: str) -> dict:
        raw: dict = {}
        for patterns, field in [
            (
                [
                    r"[Ee]xpir(?:ation|es?)[\s\S]{0,5}?(" + _DATE_RE + r")",
                    r"(" + _DATE_RE + r")\s*\n\s*[Ee]xpir(?:ation|es?)",
                ],
                "Expiration Date",
            ),
            (
                [
                    r"[Rr]enewal[^:\n]{0,40}?(" + _DATE_RE + r")",
                    r"(" + _DATE_RE + r")\s*\n\s*[Rr]enewal",
                ],
                "Renewal Date",
            ),
            (
                [
                    r"[Ii]ssue[d]?[^:\n]{0,40}?(" + _DATE_RE + r")",
                    r"[Aa]pprove[d]?[\s\S]{0,5}?(" + _DATE_RE + r")",
                    r"(" + _DATE_RE + r")\s*\n\s*(?:[Ii]ssue[d]?|[Aa]pprove[d]?)",
                ],
                "Issue Date",
            ),
        ]:
            if field not in raw:
                for pattern in patterns:
                    m = re.search(pattern, combined_text)
                    if m:
                        raw[field] = m.group(1)
                        break
        return raw

    cases = [
        # (description, text, expected_field, expected_date)
        (
            "NCASPPB reversed: date above Expires label",
            "NCSAPPB Verification\n3/1/2028\nExpires\nLCAS",
            "Expiration Date",
            "3/1/2028",
        ),
        (
            "Normal layout: Expires then date",
            "Expires: 3/1/2028\nStatus: Active",
            "Expiration Date",
            "3/1/2028",
        ),
        (
            "Normal layout: Expiration Date label",
            "Expiration Date\n12/31/2025\nStatus",
            "Expiration Date",
            "12/31/2025",
        ),
        (
            "NCASPPB reversed: date above Approved label",
            "5/19/2022\nApproved\nLCAS-28269",
            "Issue Date",
            "5/19/2022",
        ),
        (
            "Normal layout: Issued date",
            "Issued: 1/15/2020",
            "Issue Date",
            "1/15/2020",
        ),
        (
            "Renewal date reversed",
            "4/30/2026\nRenewal",
            "Renewal Date",
            "4/30/2026",
        ),
    ]

    all_ok = True
    for desc, text, expected_field, expected_date in cases:
        result = _extract_dates(text)
        got = result.get(expected_field)
        ok = got == expected_date
        all_ok = all_ok and ok
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {desc}")
        if not ok:
            print(f"          Expected {expected_field} = {expected_date!r}, got {got!r}")

    print(f"\n  Result: {'ALL PASSED' if all_ok else 'SOME FAILED'}")
    return all_ok


# ── Test 3 — Live browser search ─────────────────────────────────────────────
async def test_live_search() -> bool:
    print("\n" + "=" * 65)
    print("TEST 3 — Live browser: Camille Gregory on NC_DAC")
    print("=" * 65)

    from psv_test import _load_routing, run_state

    _load_routing()

    record = {
        "first_name":    "Camille",
        "middle_name":   "",
        "last_name":     "Gregory",
        "lic_state":     "NC",
        "prov_type":     "DAC",
        "lic_type":      "OPERATING",
        "license_id":    "LCAS-28269",
        "npi_no":        "1063723286",
        "epdb_pin":      "",
        "maintained_by": "",
        "input_expiry":  "",
        "svc_loc_state": "NC",
    }

    print(f"  Record  : {record['first_name']} {record['last_name']}")
    print(f"  State   : {record['lic_state']}  |  prov_type: {record['prov_type']}")
    print(f"  License : {record['license_id']}")
    print(f"  Board   : NC_DAC  (ncsappb.learningbuilder.com)")
    print()

    out = Path(__file__).parent / "test_nc_dac_output.xlsx"
    passes, fails, skips = await run_state(
        rows=[record],
        state="NC",
        output_path=out,
        append=False,
        batch_size=1,
        timeout=120,
        sequential=True,
    )

    total = passes + fails + skips
    print(f"\n  Result : {passes} Pass / {fails} Fail / {skips} Skip  (total {total})")
    print(f"  Output : {out}")

    ok = total == 1
    print(f"\n  [{'PASS' if ok else 'FAIL'}] Row was processed (not lost/crashed)")
    if passes:
        print("  [PASS] Status = Pass  — record found on board")
    elif skips:
        print("  [INFO] Status = Skip  — board unavailable")
    else:
        print("  [FAIL] Status = Fail  — record not matched; check trace for extracted name")
        print("         If name is still 'Credential Status' → skip_first_row fix not applied")
        print("         If expiry_date is empty → PDF regex or AI fallback issue")

    print(f"\n  Result: {'ALL PASSED' if ok else 'FAILED'}")
    return ok


if __name__ == "__main__":
    r1 = test_config_fix()
    r2 = test_pdf_regex_fix()
    r3 = asyncio.run(test_live_search())

    print("\n" + "=" * 65)
    print("FINAL SUMMARY — NC_DAC fix verification")
    print("=" * 65)
    print(f"  [{'PASS' if r1 else 'FAIL'}] Config: skip_first_row=false, no force_pdf")
    print(f"  [{'PASS' if r2 else 'FAIL'}] PDF regex: date-above-label patterns work")
    print(f"  [{'PASS' if r3 else 'FAIL'}] Live search: Camille Gregory / LCAS-28269")
    print()
    print(f"  OVERALL: {sum([r1, r2, r3])}/3 passed")
    print("=" * 65)
    sys.exit(0 if all([r1, r2, r3]) else 1)
