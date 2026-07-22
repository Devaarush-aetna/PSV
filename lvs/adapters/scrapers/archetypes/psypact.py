"""PSYPACT directory archetype (Accredible Spotlight API + authorizations page).

Flow per query:
  1. POST https://api.accredible.com/spotlight/v1/spotlight_directories/1160/users/search
     with text_search_query.value = last_name  →  list of profiles + credential status
  2. GET  https://api.accredible.com/spotlight/v1/spotlight_directories/1160/users/{uuid}
     →  directory_credentials[0].url  (https://authorizations.psypact.gov/{cred_uuid})
  3. Navigate to credential URL  →  extract Mobility Number + expiry date
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from engine.evidence import capture_evidence
from engine.models import LicenseRecord, LicenseStatus, SearchQuery, SiteConfig
from engine.output import upsert_to_db
from engine.proxy import get_proxy_config
from ._shared import _emit_event

log = logging.getLogger(__name__)

_SPOTLIGHT_SEARCH = (
    "https://api.accredible.com/spotlight/v1/spotlight_directories/1160/users/search"
)
_SPOTLIGHT_USER = (
    "https://api.accredible.com/spotlight/v1/spotlight_directories/1160/users/{uuid}"
)


def _parse_date(s: str | None):
    if not s:
        return None
    from engine.post_processors import parse_date as _pd
    return _pd(s)


def _extract_mobility_number(text: str) -> str | None:
    """Extract PSYPACT Mobility Number from credential page innerText.

    The page renders the number in two places:
      "Mobility Number:\nExpiration Date:\n21951"  (details panel, values below labels)
      "21951\nMay D, YYYY\nMobility #"             (certificate visual)
    """
    # Primary: label then value (values appear after both labels in a 2-col grid)
    m = re.search(r"Mobility Number[:\s]+(?:Expiration Date[:\s]+)?\s*(\d{4,6})", text, re.IGNORECASE)
    if m:
        return m.group(1)
    # Fallback: number immediately before "Mobility #"
    m = re.search(r"(\d{4,6})\s*\nMobility #", text)
    if m:
        return m.group(1)
    return None


def _extract_expiry(text: str) -> str | None:
    m = re.search(r"EXPIRES ON\s*\n([A-Za-z]+ \d+, \d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})", text)
    if m:
        return m.group(1)
    # Also try "May 7, 2027" after "Expiration Date:"
    m = re.search(r"Expiration Date:\s*\n([A-Za-z]+ \d+, \d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})", text)
    if m:
        return m.group(1)
    return None


async def scrape_psypact(
    config: SiteConfig, query: SearchQuery, db, t0: float, run_id: str,
) -> list[LicenseRecord]:
    source_id = config.identity.source_id
    log.info("[%s] PSYPACT run_id=%s  query=%s/%s", source_id, run_id, query.mode, query.query)

    from playwright.async_api import async_playwright

    proxy_cfg = get_proxy_config()
    records: list[LicenseRecord] = []

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                ctx = await browser.new_context(
                    viewport={"width": 1440, "height": 900},
                    proxy=proxy_cfg,
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                )
                page = await ctx.new_page()
                page.set_default_timeout(30_000)

                # ── Step 1: search Accredible Spotlight API ──────────────────
                search_payload = json.dumps({
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
                    "text_search_query": {"fields": ["name"], "value": query.query},
                })

                search_result = await page.evaluate(
                    f"""async () => {{
                        const r = await fetch("{_SPOTLIGHT_SEARCH}", {{
                            method: "POST",
                            headers: {{"Content-Type": "application/json"}},
                            body: {json.dumps(search_payload)},
                        }});
                        return await r.json();
                    }}"""
                )

                profiles = (search_result or {}).get("profiles", [])
                log.info("[%s] Spotlight returned %d profile(s) for %r", source_id, len(profiles), query.query)

                if not profiles:
                    await _emit_event(db, run_id, source_id, "complete", "success", t0, 0)
                    return []

                # ── Filter by first name if available ───────────────────────
                first_name = (query.first_name or "").strip().lower()
                last_name = query.query.strip().lower()

                def _name_matches(profile_name: str) -> bool:
                    parts = profile_name.strip().lower().split()
                    if not parts:
                        return False
                    # Last name must match (last word in profile name)
                    if parts[-1] != last_name:
                        return False
                    # If first name available, check it
                    if first_name and parts[0] != first_name:
                        return False
                    return True

                matched = [p for p in profiles if _name_matches(p.get("name", ""))]
                if not matched:
                    log.info("[%s] No name match among %d profile(s)", source_id, len(profiles))
                    await _emit_event(db, run_id, source_id, "complete", "success", t0, 0)
                    return []

                log.info("[%s] %d name-matched profile(s)", source_id, len(matched))

                for profile in matched:
                    profile_uuid = profile["id"]
                    profile_name = profile.get("name", "")
                    creds = profile.get("credentials", [])

                    # Check if any credential is active
                    active_cred = next((c for c in creds if not c.get("expired", True)), None)
                    if active_cred is None and creds:
                        # All expired — still process, mark expired
                        active_cred = creds[0]

                    if not creds:
                        log.info("[%s] Profile %s has no credentials", source_id, profile_name)
                        continue

                    # ── Step 2: get credential URL from user detail API ──────
                    user_url = _SPOTLIGHT_USER.format(uuid=profile_uuid)
                    user_result = await page.evaluate(
                        f"""async () => {{
                            const r = await fetch("{user_url}");
                            return await r.json();
                        }}"""
                    )

                    user_data = (user_result or {}).get("user", {})
                    dir_creds = user_data.get("directory_credentials", [])
                    if not dir_creds:
                        log.warning("[%s] No directory_credentials for %s", source_id, profile_name)
                        continue

                    # Pick the non-expired credential URL
                    dir_cred = next((c for c in dir_creds if not c.get("expired", True)), dir_creds[0])
                    cred_url = dir_cred.get("url", "")
                    issue_date_str = dir_cred.get("issue_date", "")
                    cred_title = dir_cred.get("title", "")
                    is_expired = dir_cred.get("expired", False)

                    if not cred_url:
                        log.warning("[%s] No credential URL for %s", source_id, profile_name)
                        continue

                    # ── Step 3: navigate to authorizations.psypact.gov ──────
                    log.info("[%s] Navigating to %s", source_id, cred_url)
                    try:
                        await page.goto(cred_url, wait_until="networkidle", timeout=30_000)
                        await asyncio.sleep(2.0)
                    except Exception as nav_exc:
                        log.warning("[%s] Navigation to credential page failed: %s", source_id, nav_exc)
                        continue

                    # Dismiss cookie banner if present
                    try:
                        await page.click('button:has-text("Got it")', timeout=1500)
                        await asyncio.sleep(0.5)
                    except Exception:
                        pass

                    cred_text = await page.evaluate("() => document.body.innerText")

                    await capture_evidence(
                        page, config.evidence,
                        stage="search_results", run_id=run_id,
                        source_id=source_id, state=config.identity.state, query=query,
                    )

                    mobility_number = _extract_mobility_number(cred_text)
                    expiry_str = _extract_expiry(cred_text)

                    log.info(
                        "[%s] %s → Mobility#=%s expired=%s",
                        source_id, profile_name, mobility_number, is_expired,
                    )

                    # Split name
                    name_parts = profile_name.strip().split()
                    first = name_parts[0] if name_parts else ""
                    last = name_parts[-1] if len(name_parts) > 1 else ""

                    # Determine status
                    if is_expired:
                        status = LicenseStatus.EXPIRED
                    else:
                        status = LicenseStatus.ACTIVE

                    rec = LicenseRecord(
                        source_id=source_id,
                        license_number=mobility_number or "",
                        licensee_first_name=first,
                        licensee_last_name=last,
                        licensee_full_name=profile_name,
                        license_type=cred_title or "Authority to Practice Interjurisdictional Telepsychology",
                        status=status,
                        issue_date=_parse_date(issue_date_str),
                        expiration_date=_parse_date(expiry_str),
                    )
                    records.append(rec)

            finally:
                await browser.close()

    except Exception as exc:
        log.error("[%s] PSYPACT scrape failed: %s", source_id, exc)
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, str(exc))
        return []

    log.info("[%s] PSYPACT returning %d record(s)", source_id, len(records))
    await _emit_event(db, run_id, source_id, "complete", "success", t0, len(records))
    if db and records:
        await upsert_to_db(db, records)
    return records
