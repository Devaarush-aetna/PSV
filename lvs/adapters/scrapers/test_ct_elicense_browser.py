"""Live test: CT_ELICENSE_BROWSER — Sandra DeAtley and Josefina Cespedes.

Tests that the Details button is found and clicked, and that expiry date
comes back from the popup modal.

Run:
    cd lvs/adapters/scrapers
    python test_ct_elicense_browser.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("test_ct_browser")


CASES = [
    {
        # last_name "DeAtley" returns 1 result (INACTIVE LCSW 58.014883)
        # vs license_number "14883" which returns 26 results (suffix match across all boards)
        "label": "Sandra DeAtley (SW, last_name search — INACTIVE, should have expiry 08/31/2025)",
        "mode":  "last_name",
        "query": "DeAtley",
        "first_name": "Sandra",
        "last_name":  "DeAtley",
    },
    {
        # last_name "Cespedes" returns ~6 results including JOSEFINA A CESPEDES (ACTIVE LCSW 58.016214)
        "label": "Josefina Cespedes (SW, last_name search — ACTIVE license SW 016214)",
        "mode":  "last_name",
        "query": "Cespedes",
        "first_name": "Josefina",
        "last_name":  "Cespedes",
    },
    {
        # Robyn Scatena — CT PH (Pharmacist), license 48635
        # Using license_number mode so we skip the pagination issue
        # (last_name "Scatena" puts Robyn on page 2; license number is direct)
        "label": "Robyn Scatena (CT PH, license_number search — 48635)",
        "mode":  "license_number",
        "query": "48635",
        "first_name": "Robyn",
        "last_name":  "Scatena",
    },
]


async def run_single(config, query_obj, label: str) -> list:
    from archetypes.browser_form import scrape_browser
    t0 = time.time()
    run_id = f"test_{int(t0)}"
    print(f"\n{'='*65}")
    print(f"  CASE: {label}")
    print(f"  Mode: {query_obj.mode}  |  Query: {query_obj.query!r}")
    print(f"{'='*65}")
    records = await scrape_browser(config, query_obj, db=None, t0=t0, run_id=run_id, headless_override=True)
    print(f"\n  >> {len(records)} record(s) returned")
    for i, r in enumerate(records, 1):
        print(f"\n  Record #{i}:")
        print(f"    full_name      : {r.licensee_full_name!r}")
        print(f"    first_name     : {r.licensee_first_name!r}")
        print(f"    last_name      : {r.licensee_last_name!r}")
        print(f"    license_number : {r.license_number!r}")
        print(f"    license_type   : {r.license_type!r}")
        print(f"    status         : {r.status}")
        print(f"    issue_date     : {r.issue_date}")
        print(f"    expiration_date: {r.expiration_date}")
        if r.expiration_date:
            print(f"    [OK] EXPIRY DATE CAPTURED from Details popup")
        else:
            print(f"    [MISS] expiry_date is MISSING -- check detail extraction")
    return records


async def main():
    from engine.validate import load_config
    from engine.models import SearchQuery

    config_path = Path(__file__).parent / "sites" / "CT_ELICENSE_BROWSER" / "config.yaml"
    config = load_config(str(config_path))
    print(f"\nLoaded config: {config.identity.source_id} — {config.identity.board_name}")
    print(f"Detail trigger selector: {config.results.detail_trigger.selector if config.results.detail_trigger else 'NONE'}")
    print(f"Detail strategies: {[s['type'] for s in config.detail.strategies]}")
    print(f"Results wait selector: {config.search.results_wait.selector}")

    all_records = []
    for case in CASES:
        q = SearchQuery(
            mode=case["mode"],
            query=case["query"],
            first_name=case.get("first_name", ""),
            last_name=case.get("last_name", ""),
        )
        records = await run_single(config, q, case["label"])
        all_records.extend(records)
        await asyncio.sleep(2)

    print(f"\n{'='*65}")
    print(f"TOTAL: {len(all_records)} record(s) across {len(CASES)} test case(s)")

    missing_expiry = [r for r in all_records if not r.expiration_date]
    missing_name   = [r for r in all_records if not r.licensee_full_name]
    if missing_expiry:
        print(f"  [MISS] {len(missing_expiry)} record(s) missing expiration_date")
    if missing_name:
        print(f"  [MISS] {len(missing_name)} record(s) missing full_name")
    if not missing_expiry and not missing_name and all_records:
        print(f"  [OK] All records have name and expiration_date")
    print(f"{'='*65}")


if __name__ == "__main__":
    asyncio.run(main())
