"""
Standalone test: scrape Expiration Date from PA_PALS detail page.

Root cause (discovered): getAssetDetail() stores PersonId/LicenseId in
localStorage then opens #!/page/searchresult in a NEW TAB (_blank).
The detail controller (SearchResultController) reads localStorage and
POSTs to api/Search/GetPersonOrFacilityDetails.

Strategy (this script):
  1. Navigate to #!/page/search, do a license search
  2. Extract PersonId, LicenseId, LicenseNumber from Angular scope
  3. Call api/Search/GetPersonOrFacilityDetails directly via fetch()
  4. Parse ExpirationDate from JSON response
  5. Fall-back: set localStorage + navigate to #!/page/searchresult
     and capture the XHR response made by Angular controller

Usage:
    python test_pa_pals_detail.py
    python test_pa_pals_detail.py --headed
    python test_pa_pals_detail.py --license RN692848
    python test_pa_pals_detail.py --license PS002970L --headed
"""
import argparse
import asyncio
import json
import sys
from playwright.async_api import async_playwright

URL = "https://www.pals.pa.gov/#!/page/search"
DEFAULT_LICENSE = "PS002970L"

DETAIL_API = "api/Search/GetPersonOrFacilityDetails"
DETAIL_API_FULL = f"https://www.pals.pa.gov/{DETAIL_API}"


async def scrape(license_no: str, headed: bool) -> dict:
    captured_detail: dict = {}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not headed)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        async def on_response(response):
            url = response.url
            if DETAIL_API in url or "GetPersonOrFacility" in url:
                try:
                    body = await response.body()
                    data = json.loads(body)
                    captured_detail["url"] = url
                    captured_detail["data"] = data
                    print(f"  [CAPTURED] {url}")
                    print(f"             keys={list(data.keys()) if isinstance(data, dict) else type(data)}")
                except Exception as e:
                    print(f"  [RESPONSE ERROR] {url}: {e}")

        page.on("response", on_response)

        # ------------------------------------------------------------------ #
        # STEP 1: Navigate + search
        # ------------------------------------------------------------------ #
        print(f"\n[1] Navigating to {URL}")
        await page.goto(URL)
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(3)

        print(f"[2] Searching for license: {license_no}")
        await page.locator("#LicenseNo").wait_for(state="visible", timeout=15000)
        await page.locator("#LicenseNo").fill(license_no)
        await page.locator("button.btn-primary:has-text('Search')").click()

        print("[3] Waiting for result rows...")
        row_sel = "#DataTables_Table_3 tbody tr"
        found = False
        for _ in range(60):
            n = await page.locator(row_sel).count()
            if n > 0:
                t = await page.locator(row_sel).first.inner_text()
                if "No data available" not in t and "Select" not in t:
                    print(f"    ✓ {n} row(s). First: {t.strip()[:100]}")
                    found = True
                    break
            await asyncio.sleep(0.5)

        if not found:
            await browser.close()
            return {"error": "no_search_results"}

        # ------------------------------------------------------------------ #
        # STEP 2: Extract PersonId, LicenseId from Angular scope
        # ------------------------------------------------------------------ #
        scope_data = await page.evaluate("""() => {
            const table = document.getElementById('DataTables_Table_3');
            let el = table;
            while (el) {
                const s = angular.element(el).scope();
                if (s && s.search && s.search.PersonDetails && s.search.PersonDetails.length > 0) {
                    const pd = s.search.PersonDetails[0];
                    return {
                        ok: true,
                        PersonId:      pd.PersonId,
                        LicenseId:     pd.LicenseId,
                        LicenseNumber: pd.LicenseNumber,
                        IsFacility:    pd.IsFacility || 0,
                    };
                }
                el = el.parentElement;
            }
            return {error: 'scope not found'};
        }""")
        print(f"\n[4] Scope: PersonId={scope_data.get('PersonId')}  "
              f"LicenseId={scope_data.get('LicenseId')}  "
              f"LicenseNumber={scope_data.get('LicenseNumber')}")

        person_id    = scope_data.get("PersonId")
        license_id   = scope_data.get("LicenseId")
        is_facility  = scope_data.get("IsFacility", 0)

        if not person_id or not license_id:
            await browser.close()
            return {"error": "no_scope_ids", "scope_data": scope_data}

        # ------------------------------------------------------------------ #
        # STEP 3: Call api/Search/GetPersonOrFacilityDetails directly
        # ------------------------------------------------------------------ #
        print(f"\n[5] Calling {DETAIL_API} directly via fetch()...")
        direct_result = await page.evaluate("""async ([personId, licenseId, licNum, isFacility]) => {
            const body = {
                PersonId:      String(personId),
                LicenseNumber: licNum,
                IsFacility:    String(isFacility),
                LicenseId:     String(licenseId),
            };
            try {
                const resp = await fetch('api/Search/GetPersonOrFacilityDetails', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    credentials: 'include',
                    body: JSON.stringify(body),
                });
                const text = await resp.text();
                return {status: resp.status, body: text.substring(0, 2000)};
            } catch(e) {
                return {error: String(e)};
            }
        }""", [person_id, license_id, license_no, is_facility])

        print(f"    status={direct_result.get('status')}  body={direct_result.get('body', direct_result.get('error', ''))[:200]}")

        # Try to parse expiry from direct result
        direct_expiry = None
        detail_json = None
        body_text = direct_result.get("body", "")
        if body_text and direct_result.get("status") not in (401, 403):
            try:
                detail_json = json.loads(body_text)
                direct_expiry = _find_expiry(detail_json)
                print(f"    => DIRECT EXPIRY: {direct_expiry}")
                if isinstance(detail_json, dict):
                    print(f"    => top-level keys: {list(detail_json.keys())[:20]}")
                elif isinstance(detail_json, list) and detail_json:
                    print(f"    => list[0] keys: {list(detail_json[0].keys())[:20]}")
            except Exception as e:
                print(f"    => parse error: {e}")

        # ------------------------------------------------------------------ #
        # STEP 4: If direct call failed/empty, use new-tab navigation approach
        # ------------------------------------------------------------------ #
        # getAssetDetail() stores in localStorage then opens #!/page/searchresult
        # in a new tab. We replicate that here: set localStorage + navigate to
        # the searchresult state in the current page.
        if not direct_expiry:
            print(f"\n[6] Direct API returned no expiry — trying localStorage + navigate approach...")

            # Set localStorage keys exactly as getAssetDetail does
            await page.evaluate("""([licNum, personId, licId, isFacility]) => {
                localStorage["SearchPersonOrFacility_LicenseNo"]   = licNum;
                localStorage["SearchPersonOrFacility_Id"]          = String(personId);
                localStorage["SearchPersonOrFacility_IsFacility"]  = String(isFacility);
                localStorage["SearchPersonOrFacility_LicenseId"]   = String(licId);
            }""", [license_no, person_id, license_id, is_facility])
            print("    localStorage set")

            # Listen for the detail API call from the controller
            detail_api_future: asyncio.Future = asyncio.get_event_loop().create_future()

            async def on_response2(response):
                url = response.url
                if "GetPersonOrFacilityDetails" in url or DETAIL_API in url:
                    try:
                        body = await response.body()
                        if not detail_api_future.done():
                            detail_api_future.set_result({"url": url, "body": body.decode("utf-8", errors="ignore")})
                    except Exception:
                        pass

            page.on("response", on_response2)

            # Navigate to the detail state — same origin, Angular will load SearchResultController
            detail_url = "https://www.pals.pa.gov/#!/page/searchresult"
            print(f"    Navigating to {detail_url}")
            await page.goto(detail_url, wait_until="domcontentloaded")
            await asyncio.sleep(2)

            # Also try Angular $state navigation
            await page.evaluate("""() => {
                try {
                    const inj = angular.element(document.body).injector();
                    if (inj) {
                        const state = inj.get('$state');
                        state.go('page.searchresult');
                    }
                } catch(e) {}
            }""")

            # Wait up to 15 s for the API call
            print("    Waiting for SearchResultController API call...")
            try:
                api_result = await asyncio.wait_for(detail_api_future, timeout=15)
                body_text2 = api_result["body"]
                print(f"    Captured: {api_result['url']}")
                print(f"    Body preview: {body_text2[:200]}")
                try:
                    detail_json2 = json.loads(body_text2)
                    direct_expiry = _find_expiry(detail_json2)
                    detail_json = detail_json2
                    print(f"    => EXPIRY: {direct_expiry}")
                    if isinstance(detail_json2, dict):
                        print(f"    => keys: {list(detail_json2.keys())[:20]}")
                    elif isinstance(detail_json2, list) and detail_json2:
                        print(f"    => list[0] keys: {list(detail_json2[0].keys())[:20]}")
                except Exception as e:
                    print(f"    => parse error: {e}")
            except asyncio.TimeoutError:
                print("    ✗ Timeout — no API call intercepted")

        # ------------------------------------------------------------------ #
        # STEP 5: Also try GetPersonOrFacilityDetails with integer ids
        # ------------------------------------------------------------------ #
        if not direct_expiry:
            print(f"\n[7] Trying with integer PersonId/LicenseId...")
            direct_result2 = await page.evaluate("""async ([personId, licenseId, licNum, isFacility]) => {
                const variants = [
                    {PersonId: personId,    LicenseNumber: licNum, IsFacility: isFacility,    LicenseId: licenseId},
                    {PersonId: personId,    LicenseNumber: licNum, IsFacility: 0,              LicenseId: licenseId},
                    {PersonId: personId,    LicenseNumber: licNum, IsFacility: false,          LicenseId: licenseId},
                    {PersonId: String(personId), LicenseId: String(licenseId), LicenseNumber: licNum, IsFacility: '0'},
                ];
                const results = [];
                for (const body of variants) {
                    try {
                        const resp = await fetch('api/Search/GetPersonOrFacilityDetails', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            credentials: 'include',
                            body: JSON.stringify(body),
                        });
                        const text = await resp.text();
                        results.push({body_sent: JSON.stringify(body), status: resp.status, response: text.substring(0, 500)});
                    } catch(e) {
                        results.push({body_sent: JSON.stringify(body), error: String(e)});
                    }
                }
                return results;
            }""", [person_id, license_id, license_no, is_facility])

            for variant in direct_result2:
                status = variant.get("status", "ERR")
                resp = variant.get("response", variant.get("error", ""))
                print(f"    [{status}] {variant.get('body_sent','')[:60]} => {resp[:80]}")
                if isinstance(status, int) and status not in (401, 403, 404, 500):
                    try:
                        parsed = json.loads(resp)
                        expiry = _find_expiry(parsed)
                        if expiry:
                            direct_expiry = expiry
                            detail_json = parsed
                            print(f"           EXPIRY FOUND: {expiry}")
                    except Exception:
                        pass

        # ------------------------------------------------------------------ #
        # STEP 6: Print all license-related keys from detail_json
        # ------------------------------------------------------------------ #
        if detail_json:
            print(f"\n[8] Full detail response analysis...")
            _dump_expiry_fields(detail_json, indent=4)

        await browser.close()

    return {
        "person_id":    person_id,
        "license_id":   license_id,
        "license_no":   license_no,
        "expiry":       direct_expiry,
        "detail_json":  detail_json,
    }


def _find_expiry(data, depth: int = 0) -> str | None:
    """Recursively search for expiry/expiration date fields."""
    if depth > 5:
        return None
    if isinstance(data, dict):
        for k, v in data.items():
            if any(x in k.lower() for x in ("expir", "expirat", "expire")):
                if v:
                    return str(v)
        for v in data.values():
            result = _find_expiry(v, depth + 1)
            if result:
                return result
    elif isinstance(data, list):
        for item in data[:5]:
            result = _find_expiry(item, depth + 1)
            if result:
                return result
    return None


def _dump_expiry_fields(data, indent: int = 0, path: str = ""):
    """Print all fields that look like dates or license info."""
    prefix = " " * indent
    if isinstance(data, dict):
        for k, v in data.items():
            full_path = f"{path}.{k}" if path else k
            if isinstance(v, (dict, list)):
                print(f"{prefix}{k}:")
                _dump_expiry_fields(v, indent + 2, full_path)
            else:
                if any(x in k.lower() for x in ("expir", "date", "lic", "status", "issue", "type", "name", "number")):
                    print(f"{prefix}{k} = {v!r}")
    elif isinstance(data, list):
        for i, item in enumerate(data[:3]):
            print(f"{prefix}[{i}]:")
            _dump_expiry_fields(item, indent + 2, f"{path}[{i}]")
            if i >= 1:
                print(f"{prefix}  ... ({len(data) - i - 1} more)")
                break


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--license", default=DEFAULT_LICENSE)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    result = asyncio.run(scrape(args.license, args.headed))

    print("\n" + "=" * 60)
    print("RESULT SUMMARY")
    print("=" * 60)
    print(f"  License        : {result.get('license_no')}")
    print(f"  PersonId       : {result.get('person_id')}")
    print(f"  LicenseId      : {result.get('license_id')}")
    print(f"  Expiry date    : {result.get('expiry') or '*** NOT FOUND ***'}")
    print("=" * 60)

    if not result.get("expiry"):
        print("\nDiagnostic: detail_json sample:")
        if result.get("detail_json"):
            txt = json.dumps(result["detail_json"], default=str)
            print(txt[:500])
        else:
            print("  (none captured)")
        sys.exit(1)


if __name__ == "__main__":
    main()
