"""PSYPACT E.Passport / Mobility Number lookup via directory.psypact.gov.

Uses Playwright to navigate the Accredible-backed PSYPACT directory and
extract the Mobility # from the authorization credential page at
authorizations.psypact.gov.

Entry point expected by psv_test.verify_psypact():
    async def run_scraper(first, middle, last, output_dir, headless) -> list[dict]

Each returned dict has shape:
    {"license_data": {"mobility_number": "18696", "expiration_date": "May 31, 2027"}}
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

log = logging.getLogger(__name__)

_DIR_URL = "https://directory.psypact.gov/"
_SEARCH_URL = "https://api.accredible.com/spotlight/v1/spotlight_directories/1160/users/search"
_PROFILE_URL = "https://api.accredible.com/spotlight/v1/spotlight_directories/1160/users/{uid}"

# JS snippets (same as original — browser-side fetch to avoid CORS from extension context)
_JS_POST = """
    async ({url, body}) => {
        try {
            const r = await fetch(url, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: body
            });
            return await r.json();
        } catch (e) {
            return {error: String(e)};
        }
    }
    """

_JS_GET = """
    async (url) => {
        try {
            const r = await fetch(url);
            return await r.json();
        } catch (e) {
            return {error: String(e)};
        }
    }
    """


def _name_matches(profile_name: str, first: str, last: str) -> bool:
    """Return True if both first and last name appear in the profile name.

    Handles suffixes (Jr., Psy.D.) and hyphenated last names by checking
    word-level containment rather than exact equality.
    """
    words = re.split(r"[\s\-]+", profile_name.upper())
    first_words = re.split(r"[\s\-]+", first.upper())
    last_words = re.split(r"[\s\-]+", last.upper())
    return (
        any(w in words for w in first_words if w)
        and any(w in words for w in last_words if w)
    )


def _extract_mobility(text: str) -> str | None:
    """Extract Mobility # from the authorizations.psypact.gov page body text.

    The page renders the credential data in two sections:
      1. Card section (value then label):
             18696
             May 31, 2027
             Mobility #
             Expiration Date
      2. Detail section (label then value):
             Mobility Number:
             Expiration Date:
             18696
             May 31, 2027
    """
    # Detail section: label → value
    m = re.search(
        r"Mobility\s+Number[:\s]*\n(?:Expiration\s+Date[:\s]*\n)?(\d+)",
        text, re.IGNORECASE,
    )
    if m:
        return m.group(1)
    # Card section: value → label
    m = re.search(
        r"(\d{3,6})\n[A-Za-z]+ \d{1,2},? \d{4}\nMobility\s*#",
        text, re.IGNORECASE,
    )
    if m:
        return m.group(1)
    return None


def _extract_expiry(text: str) -> str | None:
    """Extract expiration date string from the credential page.

    Card section layout (value before label):
        18696
        May 31, 2027
        Mobility #
        Expiration Date

    Detail section layout (label before value, mobility number first):
        Mobility Number:
        Expiration Date:
        18696
        May 31, 2027
    """
    # Detail section: mobility first, then expiry
    m = re.search(
        r"Expiration\s+Date[:\s]*\n\d+\n([A-Za-z]+ \d{1,2},? \d{4})",
        text, re.IGNORECASE,
    )
    if m:
        return m.group(1)
    # Card section: expiry before labels
    m = re.search(
        r"([A-Za-z]+ \d{1,2},? \d{4})\nMobility\s*#\nExpiration\s+Date",
        text, re.IGNORECASE,
    )
    if m:
        return m.group(1)
    return None


async def run_scraper(
    first: str,
    middle: str,
    last: str,
    output_dir: str = "",
    headless: bool = True,
) -> list[dict]:
    """Search PSYPACT directory by name; return all matching mobility numbers."""
    results: list[dict] = []
    name = f"{first} {last}".strip()

    from playwright.async_api import async_playwright  # noqa: PLC0415

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=headless)
            page = await browser.new_page()

            # Navigate to directory first — establishes session cookies that
            # allow the browser-side JS fetch to hit the Accredible API.
            await page.goto(_DIR_URL)
            await page.wait_for_timeout(2000)

            # POST name search → list of profile stubs
            # The Accredible Spotlight API requires the structured text_search_query
            # payload; a bare {"name": ...} body is silently ignored and returns 0 profiles.
            # Search by last name (broader) and filter by first name after.
            # Retry up to 3 times: the API occasionally returns 0 profiles for valid records
            # when hit in rapid succession (rate-limiting / session expiry). Re-navigating
            # to _DIR_URL before each retry refreshes the session cookie.
            _search_payload = json.dumps({
                "page": "1",
                "page_size": 50,
                "order": "",
                "groups": [],
                "organizations": [],
                "lat": None,
                "lng": None,
                "viewport": None,
                "skills": [],
                "skill_category_ids": [],
                "availability": [],
                "custom_attributes": [],
                "text_search_query": {"fields": ["name"], "value": last or name},
            })

            profiles: list[Any] = []
            for _attempt in range(3):
                search_result = await page.evaluate(
                    _JS_POST,
                    {"url": _SEARCH_URL, "body": _search_payload},
                )
                if search_result.get("error"):
                    log.warning("[PSYPACT] Search error: %s", search_result["error"])
                    await browser.close()
                    return results
                profiles = search_result.get("profiles") or []
                log.debug(
                    "[PSYPACT] Search '%s' → %d profiles (attempt %d/3)",
                    name, len(profiles), _attempt + 1,
                )
                if profiles:
                    break
                if _attempt < 2:
                    log.debug("[PSYPACT] Empty result — waiting 3 s before retry")
                    await page.wait_for_timeout(3000)
                    await page.goto(_DIR_URL)
                    await page.wait_for_timeout(2000)

            for profile in profiles:
                uid = profile.get("id")
                profile_name = profile.get("name") or ""
                if not _name_matches(profile_name, first, last):
                    continue

                # Fetch full profile — includes directory_credentials list
                try:
                    profile_data = await page.evaluate(
                        _JS_GET, _PROFILE_URL.format(uid=uid)
                    )
                    user = profile_data.get("user") or {}
                    credentials = user.get("directory_credentials") or []

                    if not credentials:
                        log.debug("[PSYPACT] %s — no directory_credentials", profile_name)
                        continue

                    for cred in credentials:
                        if cred.get("expired"):
                            log.debug("[PSYPACT] %s — credential expired", profile_name)
                            continue

                        # `url` is the URL of the PSYPACT authorization credential page
                        body_url = cred.get("url") or ""
                        if not body_url:
                            continue

                        try:
                            await page.goto(body_url)
                            await page.wait_for_timeout(2000)
                            text = await page.inner_text("body")

                            mobility = _extract_mobility(text)
                            expiry = _extract_expiry(text)

                            if not mobility:
                                log.debug(
                                    "[PSYPACT] %s — could not extract mobility from %s",
                                    profile_name, body_url,
                                )
                                continue

                            log.debug(
                                "[PSYPACT] %s — mobility=%s expiry=%s",
                                profile_name, mobility, expiry,
                            )
                            results.append({
                                "license_data": {
                                    "mobility_number": mobility,
                                    "expiration_date": expiry or "",
                                }
                            })

                        except Exception as exc:
                            log.warning("[PSYPACT] Failed to load %s: %s", body_url, exc)

                except Exception as exc:
                    log.warning("[PSYPACT] [%s] Error loading profile %s: %s",
                                name, uid, exc)

            await browser.close()

    except Exception as exc:
        log.warning("[PSYPACT] Scraper error: %s", exc, exc_info=True)

    return results
