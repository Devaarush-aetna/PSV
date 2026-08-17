"""End-to-end capability check for MI_LARA (Michigan LARA / Accela MiPLUS).

Runs real license-number searches through the actual scraper path
(verify_license -> scrape_browser), exercising:
  search -> results row -> click License Number link -> detail page -> extraction

Each license below has KNOWN ground truth taken from the live site screenshots,
so we can verify field-by-field that the scraper extracts correctly — especially
the expiration date and status.

Run:
    python test_mi_lara_e2e.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from engine.validate import load_config          # noqa: E402
from engine.models import SearchQuery            # noqa: E402
from archetypes.dispatcher import verify_license  # noqa: E402

# (license_number, expected_type, expected_status, expected_expiry, expected_name)
CASES = [
    ("1101022239", "Accountant - Licensed",         "Active",     "07/31/2027", "Amy L Rodriguez"),
    ("1101014233", "Accountant - Licensed",         "Superseded", "",           "Amy M Brozgold"),
    ("1101007966", "Accountant - Licensed",         "Lapsed",     "12/31/2009", "Ambrose J Rouble"),
    ("1701002215", "Barber",                        "Lapsed",     "09/30/1980", "Ambrose A Lajiness"),
    ("6852093802", "Bachelors Social Worker Limited","Lapsed",    "07/26/2024", "Ambrosia Rain Naramore-Winfrey"),
]


def _fmt(d) -> str:
    if d is None:
        return ""
    try:
        return d.strftime("%m/%d/%Y")
    except Exception:
        return str(d)


async def main() -> None:
    cfg = load_config(str(Path(__file__).parent / "sites" / "MI_LARA" / "config.yaml"))
    print(f"Loaded config: {cfg.identity.source_id}  archetype={cfg.identity.archetype}")
    print(f"Base URL: {cfg.identity.base_url}\n")

    summary = []
    for lic, exp_type, exp_status, exp_expiry, exp_name in CASES:
        print("=" * 90)
        print(f"LICENSE {lic}  (expect: {exp_name} | {exp_type} | {exp_status} | exp={exp_expiry or '(none)'})")
        print("=" * 90)
        query = SearchQuery(mode="license_number", license_number=lic, query=lic)
        try:
            recs = await verify_license(cfg, query, db=None, headless_override=True)
        except Exception as exc:
            print(f"  !! SCRAPE RAISED: {exc}\n")
            summary.append((lic, "ERROR", str(exc)))
            continue

        if not recs:
            print("  !! NO RECORDS RETURNED\n")
            summary.append((lic, "NO_RECORDS", ""))
            continue

        # Prefer the record whose license_number matches exactly.
        rec = next((r for r in recs if (r.license_number or "").strip() == lic), recs[0])
        got_expiry = _fmt(rec.expiration_date)
        got_issue = _fmt(rec.issue_date)
        got_name = rec.licensee_full_name or " ".join(
            x for x in (rec.licensee_first_name, rec.licensee_middle_name, rec.licensee_last_name) if x
        )

        print(f"  records returned : {len(recs)}")
        print(f"  license_number   : {rec.license_number}")
        print(f"  license_type     : {rec.license_type}")
        print(f"  name             : {got_name}")
        print(f"  status           : {rec.status}")
        print(f"  issue_date       : {got_issue}")
        print(f"  expiration_date  : {got_expiry}    (expected {exp_expiry or '(none)'})")
        print(f"  county/state     : {rec.state_code} / raw county={rec.raw_fields.get('county')}")
        print(f"  source_url       : {rec.source_url}")
        print(f"  evidence_html    : {rec.evidence_html_path}")

        checks = []
        checks.append(("license_number", rec.license_number == lic))
        checks.append(("expiry", got_expiry == exp_expiry))
        checks.append(("type_nonempty", bool(rec.license_type)))
        checks.append(("name_nonempty", bool(got_name)))
        ok = all(v for _, v in checks)
        fails = [k for k, v in checks if not v]
        print(f"  RESULT           : {'PASS' if ok else 'FAIL ' + str(fails)}")
        print(f"  raw_fields       : {rec.raw_fields}\n")
        summary.append((lic, "PASS" if ok else "FAIL", ",".join(fails)))

    print("\n" + "#" * 90)
    print("SUMMARY")
    print("#" * 90)
    for lic, res, note in summary:
        print(f"  {lic:<14} {res:<10} {note}")


if __name__ == "__main__":
    asyncio.run(main())
