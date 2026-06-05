"""
Massachusetts BORIM License Lookup — MD / DO / AP
API: https://api.medboard.mass.gov/api-public/search

Run:
    python Massachusetts_MD_DO_AP_API.py
"""
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

API_URL = "https://api.medboard.mass.gov/api-public/search"

REQUEST_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://findmydoctor.mass.gov",
    "Referer": "https://findmydoctor.mass.gov/",
}


async def call_api(payload: dict) -> dict:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            # Navigate to the site so the browser uses the corporate proxy/network
            await page.goto("https://findmydoctor.mass.gov/", wait_until="domcontentloaded")

            # Call the API from inside the browser via fetch() — uses browser network stack
            # page.evaluate() accepts only one arg, so bundle url + payload into a dict
            result = await page.evaluate(
                """async (args) => {
                    const resp = await fetch(args.url, {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json",
                            "Accept": "application/json, text/plain, */*"
                        },
                        body: JSON.stringify(args.payload)
                    });
                    if (!resp.ok) {
                        throw new Error("API error " + resp.status + ": " + await resp.text());
                    }
                    return await resp.json();
                }""",
                {"url": API_URL, "payload": payload},
            )
            return result
        finally:
            await browser.close()


def print_results(data: list):
    for i, r in enumerate(data, 1):
        orig  = (r.get("originalDate")    or "")[:10] or "N/A"
        start = (r.get("startDate")       or "")[:10] or "N/A"
        exp   = (r.get("expirationDate")  or "")[:10] or "N/A"
        print(f"\n  --- Result {i} ---")
        print(f"  Name                : {r.get('fullName')}")
        print(f"  License #           : {r.get('licenseNumber')}")
        print(f"  License Type        : {r.get('licenseMetaName')}")
        print(f"  Status              : {r.get('profileStatus')}")
        print(f"  Degree              : {r.get('degree')}")
        print(f"  Original Issue Date : {orig}")
        print(f"  Latest Issue Date   : {start}")
        print(f"  Expiration Date     : {exp}")
        print(f"  Specialties         : {r.get('specialties')}")
        print(f"  Hospitals           : {r.get('hospitals')}")
        print(f"  Accepts Medicaid    : {r.get('acceptsMedicaid')}")
        print(f"  Accepts New Patients: {r.get('acceptsNewPatients')}")


def main():
    print("=" * 52)
    print("   Massachusetts BORIM License Lookup")
    print("   Covers: MD / DO / AP")
    print("=" * 52)
    print()
    print("Search By:")
    print("  1. Licensee Name")
    print("  2. License Number")
    print()

    while True:
        choice = input("Enter choice (1 or 2): ").strip()
        if choice in ("1", "2"):
            break
        print("  Invalid choice. Please enter 1 or 2.")

    print()

    if choice == "1":
        first_name = input("Enter First Name : ").strip()
        last_name  = input("Enter Last Name  : ").strip()
        if not first_name and not last_name:
            print("Error: Please enter at least a first or last name.")
            return
        payload = {
            "licenseMetaId": None,
            "firstName": first_name,
            "lastName": last_name,
            "titles": [],
            "specialties": [],
            "cities": [],
            "searchType": "BY_PHYSICIAN_NAME",
            "showOnlyAuthorizedForHerbalTherapy": False,
        }
        safe_first = first_name.replace(" ", "_") or "ANY"
        safe_last  = last_name.replace(" ", "_")  or "ANY"
        out_filename = f"MA_{safe_first}_{safe_last}.json"

    else:
        license_number = input("Enter License Number: ").strip()
        if not license_number:
            print("Error: License number cannot be empty.")
            return
        payload = {
            "licenseMetaId": None,
            "licenseNumber": license_number,
            "searchType": "BY_LICENSE_NUMBER",
            "showOnlyAuthorizedForHerbalTherapy": False,
        }
        out_filename = f"MA_{license_number}.json"

    print("\nSearching ...")
    result = asyncio.run(call_api(payload))

    data  = result.get("results", {}).get("data", [])
    total = result.get("results", {}).get("totalDataCount", 0)

    if total == 0 or not data:
        print("No results found.")
    else:
        print(f"\nFound {total} result(s):")
        print_results(data)

    out_dir = Path(__file__).parent / "output"
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / out_filename
    out_file.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nFull response saved to: {out_file}")


if __name__ == "__main__":
    main()
