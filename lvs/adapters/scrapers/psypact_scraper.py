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
    first_words = re.split(r"[\s\-]+", first.upper())
    last_words = re.split(r"[\s\-]+", last.upper())
    name_upper = profile_name.upper()
    return (
        any(w in name_upper for w in first_words)
        and any(w in name_upper for w in last_words)
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
    m = re.search(
        r"Mobility\s+Number[:\s]*\n(?:Expiration\s+Date[:\s]*\n)?(\d+)",
        text, re.IGNORECASE,
    )
    if m:
        return m.group(1)
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
    m = re.search(
        r"Expiration\s+Date[:\s]*\n\d+\n([A-Za-z]+ \d{1,2},? \d{4})",
        text, re.IGNORECASE,
    )
    if m:
        return m.group(1)
    m = re.search(
        r"([A-Za-z]+ \d{1,2},? \d{4})\nMobility\s*#\nExpiration\s+Date",
        text, re.IGNORECASE,
    )
    if m:
        return m.group(1)
    return None


async def run_scraper(
    first: str, middle: str, last: str, output_dir: str, headless: bool
) -> list[dict]:
    """Search PSYPACT directory by name; return all matching mobility numbers."""
    results = []
    name = " ".join(x.strip() for x in [first, last] if x.strip())

    from playwright.async_api import async_playwright

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=headless)
            page = await browser.new_page()

            # Navigate to the directory first to establish Accredible session context
            await page.goto(_DIR_URL)
            await page.wait_for_timeout(2000)

            # Search via the Accredible spotlight API (same-origin fetch)
            search_result = await page.evaluate(
                _JS_POST,
                {"url": _SEARCH_URL, "body": json.dumps({"name": name})},
            )

            if not isinstance(search_result, dict) or search_result.get("error"):
                log.warning("[PSYPACT] Search error: %s", search_result)
                await browser.close()
                return results

            profiles = search_result.get("profiles") or []

            for profile in profiles:
                uid = profile.get("id")
                profile_name = profile.get("name") or profile.get("full_name") or ""

                if not uid:
                    continue
                if not _name_matches(profile_name, first, last):
                    log.debug("[PSYPACT] Name mismatch: '%s' vs '%s %s'", profile_name, first, last)
                    continue

                # Fetch full profile to get credentials list
                profile_data = await page.evaluate(
                    _JS_GET, _PROFILE_URL.format(uid=uid)
                )

                user = {}
                if isinstance(profile_data, dict):
                    user = profile_data.get("user") or profile_data.get("profile") or {}
                credentials = user.get("directory_credentials") or user.get("credentials") or []

                for cred in credentials:
                    body_url = cred.get("url") or cred.get("body_url") or cred.get("credential_url")
                    if not body_url:
                        continue

                    try:
                        await page.goto(body_url)
                        await page.wait_for_timeout(1500)
                        text = await page.inner_text("body")
                        mobility = _extract_mobility(text)
                        expiry = _extract_expiry(text)
                        if mobility:
                            results.append({
                                "license_data": {
                                    "mobility_number": mobility,
                                    "expiration_date": expiry or "",
                                }
                            })
                    except Exception as exc:
                        log.warning("[PSYPACT] Credential fetch failed (%s): %s", body_url, exc)
                        continue

            await browser.close()

    except Exception as exc:
        log.warning("[PSYPACT] run_scraper error for %s %s: %s", first, last, exc, exc_info=True)

    return results
