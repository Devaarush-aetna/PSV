"""PSV batch tester — reads input Excel, routes each row to matching board
configs, calls verify_license(), writes Pass/Fail + reason incrementally.
Processes rows in batches (default 10) so results are saved after each batch.

Search strategy per row:
  1. Try license_number mode first (fast for active/known licenses).
  2. If no records returned, fall back to last_name mode (catches expired licenses
     still in board DB, and handles format mismatches in license IDs).

Browser reuse:
  For browser-based boards (classic_html_form), one Playwright browser is launched
  per board config at startup and reused across all searches. This avoids the 30-60s
  per-call Chromium launch overhead on Windows. Context+page are recreated per search
  (~0.5s) instead of restarting the whole browser (~35s).

  For non-browser boards (csv_bulk, socrata_api, etc.), verify_license() is used as
  normal — these open no browser.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import csv
import logging
import re
import sys
from pathlib import Path
from typing import Optional

import openpyxl
from openpyxl.styles import PatternFill, Font
from playwright.async_api import async_playwright, Browser

sys.path.insert(0, str(Path(__file__).parent))

from engine.browser import _REAL_UA, _STEALTH_ARGS
from engine.evidence import capture_evidence
from engine.extractor import extract_results_table, extract_detail, extract_th_td_multi
from engine.post_processors import apply_field_map
from engine.models import SearchQuery
from engine.output import map_to_license_record
from engine.proxy import get_proxy_config
from engine.validate import load_config
from engine.navigator import navigate_to_search, fill_search_form
from archetypes._shared import _wait_for_detail_content, _navigate_back
from run import verify_license

log = logging.getLogger("psv_test")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

# PSV Tab column indices (0-based) — fixed layout
C_FIRST_NAME = 0
C_MIDDLE_NAME = 1
C_LAST_NAME = 2
C_EPDB_PIN = 3      # EPDB PIN — used by AddLicense output channel
C_PROV_TYPE = 4
C_MAINTAINED_BY = 7 # Maintained By — used by AddLicense output channel
C_LIC_STATE = 9
C_LIC_TYPE = 10
C_LIC_ID = 11

# NPI_NO is looked up DYNAMICALLY by header name — Input.xlsx may not always
# include it. If a header cell matches one of these names, that column index
# is used; otherwise npi_no stays empty and NPPES enrichment is skipped per row.
_NPI_HEADER_ALIASES = ("NPI_NO", "NPI", "NPI ID", "NPI_ID", "NPI Number")

# Browser-based archetypes (share one Playwright browser per board)
_BROWSER_ARCHETYPES = {"classic_html_form", "aspnet_webforms", "angular_spa", "react_spa"}

# Routing table: (state_abbr, psv_prov_type) -> [source_id, ...]
_ROUTING: dict[tuple[str, str], list[str]] = {}
_ROUTING_CSV = Path(__file__).parent / "board_routing_master.csv"


def _load_routing() -> None:
    if _ROUTING_CSV.exists():
        with open(_ROUTING_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                key = (row["state"].strip().upper(), row["psv_prov_type"].strip().upper())
                _ROUTING.setdefault(key, []).append(row["source_id"].strip())
        log.info("Loaded routing for %d (state,prov_type) pairs from %s", len(_ROUTING), _ROUTING_CSV.name)
    else:
        # CSV not present — fall back to the hardcoded dict in board_routing.py
        from board_routing import ROUTING as _HARDCODED  # noqa: PLC0415
        _ROUTING.update(_HARDCODED)
        log.info("CSV not found — loaded %d routing entries from board_routing.py", len(_ROUTING))


STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}


_SUFFIX_RE = re.compile(r"\s+(?:Jr\.?|Sr\.?|II|III|IV|2nd|3rd|4th|Esq\.?)$", re.IGNORECASE)

# Socrata board type filters: (source_id, psv_prov_type) -> exact license_type/provider_type string.
# Populated from live API distinct-value queries (2026-06-22).
# IL_LICENSING uses license_type_selector="license_type" (board-level categories).
# WA_HEALTH uses provider_type_selector="credentialtype" (credential type strings).
# CO_DORA uses opaque short codes — no safe mapping; omitted (unfiltered search still works).
# DE_LICENSING has 297 granular types — omitted to avoid false-negative risk.
_SOCRATA_TYPE_MAP: dict[tuple[str, str], str] = {
    # IL_LICENSING — board-level category (safe 1:1 mapping)
    ("IL_LICENSING", "ABA"): "ADVISORY BD OF BEHAV",
    ("IL_LICENSING", "AP"):  "ACUPUNCTURE",
    ("IL_LICENSING", "CP"):  "CLIN PSYCHOLOGIST",
    ("IL_LICENSING", "DDS"): "DENTAL",
    ("IL_LICENSING", "DMD"): "DENTAL",
    ("IL_LICENSING", "DO"):  "MEDICAL BOARD",
    ("IL_LICENSING", "DP"):  "PODIATRY",
    ("IL_LICENSING", "DT"):  "DIETETIC AND NUTRITION",
    ("IL_LICENSING", "LC"):  "PROF. COUNSELOR",
    ("IL_LICENSING", "LPC"): "PROF. COUNSELOR",
    ("IL_LICENSING", "MD"):  "MEDICAL BOARD",
    ("IL_LICENSING", "MT"):  "MASSAGE LICENSING BD",
    ("IL_LICENSING", "NP"):  "ADV PRACTICE NURSE",
    ("IL_LICENSING", "NPB"): "ADV PRACTICE NURSE",
    ("IL_LICENSING", "NPS"): "ADV PRACTICE NURSE",
    ("IL_LICENSING", "NSA"): "NURSING BOARD",
    ("IL_LICENSING", "OD"):  "OPTOMETRY",
    ("IL_LICENSING", "OT"):  "OCCUPATIONAL THERAPY",
    ("IL_LICENSING", "PA"):  "PHYSICIAN ASSISTANT",
    ("IL_LICENSING", "PAB"): "PHYSICIAN ASSISTANT",
    ("IL_LICENSING", "PAS"): "PHYSICIAN ASSISTANT",
    ("IL_LICENSING", "PH"):  "MEDICAL BOARD",
    ("IL_LICENSING", "PN"):  "NURSING BOARD",
    ("IL_LICENSING", "PT"):  "PHYSICAL THERAPY",
    ("IL_LICENSING", "SH"):  "SPEECH-LANGUAGE PATH",
    ("IL_LICENSING", "SW"):  "SOCIAL WORKER",
    # WA_HEALTH — credential type string (exact match)
    ("WA_HEALTH", "DDS"): "Dentist License",
    ("WA_HEALTH", "DMD"): "Dentist License",
    ("WA_HEALTH", "DO"):  "Osteopathic Physician & Surgeon License",
    ("WA_HEALTH", "DP"):  "Podiatric Physician And Surgeon License",
    ("WA_HEALTH", "MD"):  "Physician And Surgeon License",
    ("WA_HEALTH", "NP"):  "Advanced Registered Nurse Practitioner License",
    ("WA_HEALTH", "NPB"): "Advanced Registered Nurse Practitioner License",
    ("WA_HEALTH", "OT"):  "Occupational Therapist License",
    ("WA_HEALTH", "PA"):  "Physician Assistant License",
    ("WA_HEALTH", "PAS"): "Physician Assistant License",
    ("WA_HEALTH", "PH"):  "Physician And Surgeon License",
    ("WA_HEALTH", "PM"):  "Pharmacist License",
    ("WA_HEALTH", "PN"):  "Licensed Practical Nurse",
    ("WA_HEALTH", "PT"):  "Physical Therapist License",
    ("WA_HEALTH", "RNA"): "Registered Nurse License",
    # WA SW omitted: too many LICSW subtypes; exact match would risk false negatives
    # KS_NURSING_KSBN — KSBN dropdown values (not prov_type codes)
    # Dropdown options: RN, LPN, LMHT, RNA, NP, NMW, CNS
    ("KS_NURSING_KSBN", "NP"):  "NP",
    ("KS_NURSING_KSBN", "NPB"): "NP",
    ("KS_NURSING_KSBN", "NPS"): "NP",
    ("KS_NURSING_KSBN", "NSA"): "NP",
    ("KS_NURSING_KSBN", "PN"):  "LPN",
    ("KS_NURSING_KSBN", "RN"):  "RN",
    ("KS_NURSING_KSBN", "RNA"): "RNA",
    ("KS_NURSING_KSBN", "MW"):  "NMW",
}


def _normalize(s: str) -> str:
    # Replace hyphens/apostrophes/periods with space so "Vives-Montano"=="Vives Montano"
    return re.sub(r"\s+", " ", re.sub(r"[-.']+", " ", s.upper())).strip()


def _full_name(rec) -> str:
    fn = getattr(rec, "licensee_full_name", None)
    if fn:
        return fn
    parts = []
    f = getattr(rec, "licensee_first_name", None)
    l = getattr(rec, "licensee_last_name", None)
    if f:
        parts.append(f)
    if l:
        parts.append(l)
    return " ".join(parts)


def _name_matches(rec, last: str, first: str) -> bool:
    full = _normalize(_full_name(rec))
    if not full:
        return False
    last_norm = _normalize(last) if last else ""
    if last_norm and last_norm not in full:
        # Hyphenated surname fallback: board may store only one component of the name
        # (e.g. board shows "BATES, AMY J" while PSV has "Bates-Daly" → try "BATES" or "DALY").
        if "-" in last:
            parts = [_normalize(p) for p in last.split("-") if p.strip()]
            if not any(p and p in full for p in parts):
                return False
        else:
            return False
    if first and _normalize(first) not in full:
        return False
    return True


def _license_matches(rec, lid: str) -> bool:
    if not lid:
        return True
    lic = getattr(rec, "license_number", None) or ""
    if not lic:
        # Board doesn't expose license numbers in results — accept name-only match
        return True
    lid_u = lid.upper().strip()
    rec_u = lic.upper().strip()
    if lid_u == rec_u:
        return True
    # Substring match only when at least one side is alphanumeric (prefix/suffix case
    # like "8901" ⊂ "LC8901").  Two all-digit strings must be an exact match — otherwise
    # "3940" spuriously matches inside "13940", "23940", etc.
    if lid_u.isdigit() and rec_u.isdigit():
        return False
    if lid_u in rec_u:
        # Guard: a pure-digit PSV ID (e.g., "2561") must not match a board license
        # whose digit-only content is substantially longer (e.g., "17-02561" → 7 digits).
        # Year-prefixed formats like "YY-NNNNN" would be a different number entirely.
        # Allow ≤ 2 extra digits (covers zero-padding like "4643" ↔ "04643" or "LC 04643").
        if lid_u.isdigit() and len(re.sub(r"\D", "", rec_u)) > len(lid_u) + 2:
            pass  # skip — year-prefix or totally different number
        else:
            return True
    # Numeric-only fallback: "LPC 04643" should match "LCPC 04643" since the digit
    # portion is identical (different type-prefix conventions across PSV vs board).
    lid_num = re.sub(r"\D", "", lid_u)
    rec_num = re.sub(r"\D", "", rec_u)
    if lid_num and rec_num and len(lid_num) >= 4 and lid_num == rec_num:
        return True
    # Leading-zero tolerance: "01041" == "1041" after stripping leading zeros
    if lid_num and rec_num and len(lid_num) >= 3 and lid_num.lstrip("0") == rec_num.lstrip("0"):
        return True
    return False


class PsvBrowser:
    """Shared Playwright browser for one board config. Avoids per-call browser launch overhead."""

    def __init__(self, config, browser: Browser, proxy_cfg):
        self.config = config
        self._browser = browser
        self._proxy = proxy_cfg

    async def search(self, query: SearchQuery, timeout_ms: int = 45000,
                     run_id: str = "") -> list:
        """Open a new context+page, search, extract grid rows, close context. ~15s per call.
        Captures per-rung evidence (HTML + screenshot) for browser archetypes —
        otherwise the orchestrator's per-attempt evidence column points to a
        path that was never written.
        """
        src = self.config.identity.source_id
        state = self.config.identity.state
        ctx = await self._browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=_REAL_UA,
            proxy=self._proxy,
            locale="en-US",
            timezone_id="America/New_York",
        )
        ctx.set_default_timeout(timeout_ms)
        ctx.set_default_navigation_timeout(min(timeout_ms, 30000))
        page = await ctx.new_page()
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        try:
            log.info("[%s] search mode=%s query=%s", src, query.mode, query.query)
            await navigate_to_search(page, self.config)
            has_results = await fill_search_form(page, self.config, query)
            if not has_results:
                log.info("[%s] No results for query=%s", src, query.query)
                if run_id:
                    try:
                        await capture_evidence(page, self.config.evidence,
                                                stage="search_results",
                                                run_id=run_id, source_id=src,
                                                state=state, query=query)
                    except Exception:
                        pass
                return []
            # Single-result redirect: portal goes directly to detail page instead of results list.
            single_pat = getattr(self.config.results, "single_result_url_pattern", None)
            if single_pat and single_pat in page.url:
                raw = await extract_detail(page, self.config.detail)
                if run_id:
                    try:
                        await capture_evidence(page, self.config.evidence,
                                                stage="detail_page",
                                                run_id=run_id, source_id=src,
                                                state=state, query=query)
                    except Exception:
                        pass
                return [map_to_license_record(raw, self.config, {})]
            if self.config.results.type == "th_td_multi":
                raw_rows_multi = await extract_th_td_multi(page, self.config.results)
                if run_id:
                    try:
                        await capture_evidence(page, self.config.evidence,
                                                stage="search_results",
                                                run_id=run_id, source_id=src,
                                                state=state, query=query)
                    except Exception:
                        pass
                mapped = [
                    map_to_license_record(
                        apply_field_map(r, self.config.detail.field_map),
                        self.config, {}
                    )
                    for r in raw_rows_multi
                ]
                return mapped
            raw_rows, _warn = await extract_results_table(page, self.config.results)
            if _warn:
                log.warning("[%s] extract_results_table partial: %s", src, _warn)
            if run_id:
                try:
                    await capture_evidence(page, self.config.evidence,
                                            stage="search_results",
                                            run_id=run_id, source_id=src,
                                            state=state, query=query)
                except Exception:
                    pass
            # Follow detail pages when the board stores expiry/full data only on the detail page.
            # For license_number searches with ≤5 results: visit ALL result rows' detail pages
            # (boards like FL_MQA return 2–3 rows for one APRN number; expiry is on detail only).
            # For other modes: follow detail only for single results to avoid O(N) requests.
            _has_detail = (
                self.config.results.has_detail_page
                and self.config.results.detail_trigger
            )
            _detail_limit = 5 if query.mode == "license_number" else 1
            if _has_detail and raw_rows and len(raw_rows) <= _detail_limit:
                trigger_sel = self.config.results.detail_trigger.selector
                detailed = []
                for _idx in range(len(raw_rows)):
                    try:
                        btn = page.locator(trigger_sel).nth(_idx)
                        if not await btn.is_visible(timeout=3000):
                            break
                        await btn.evaluate("el => el.removeAttribute('target')")
                        url_before = page.url
                        await btn.click()
                        try:
                            await page.wait_for_function(
                                "url => window.location.href !== url",
                                url_before,
                                timeout=self.config.detail.wait.timeout_ms,
                            )
                        except Exception:
                            pass
                        await _wait_for_detail_content(page, self.config)
                        raw = await extract_detail(page, self.config.detail)
                        if run_id:
                            try:
                                await capture_evidence(page, self.config.evidence,
                                                        stage="detail_page",
                                                        run_id=run_id, source_id=src,
                                                        state=state, query=query)
                            except Exception:
                                pass
                        detailed.append(map_to_license_record(raw, self.config, {}))
                    except Exception as det_err:
                        log.warning("[%s] detail_click idx=%d failed: %s — using summary row",
                                    src, _idx, det_err)
                        if _idx < len(raw_rows):
                            detailed.append(map_to_license_record(raw_rows[_idx], self.config, {}))
                    # Navigate back to results page before clicking the next row's detail link.
                    if _idx < len(raw_rows) - 1:
                        try:
                            await _navigate_back(page, self.config)
                            try:
                                await page.wait_for_load_state("networkidle", timeout=10000)
                            except Exception:
                                await asyncio.sleep(2)
                        except Exception as back_err:
                            log.warning("[%s] navigate_back failed at detail idx=%d: %s",
                                        src, _idx, back_err)
                            break
                if detailed:
                    return detailed
            return [map_to_license_record(r, self.config, {}) for r in raw_rows]
        except Exception as exc:
            log.warning("[%s] Error mode=%s query=%s: %s", src, query.mode, query.query, exc)
            if run_id:
                try:
                    await capture_evidence(page, self.config.evidence,
                                            stage="error",
                                            run_id=run_id, source_id=src,
                                            state=state, query=query)
                except Exception:
                    pass
            return []
        finally:
            try:
                await page.close()
                await ctx.close()
            except Exception:
                pass


async def _try_search_psv(psv_b: PsvBrowser, query: SearchQuery, timeout: int) -> list:
    """Wrap PsvBrowser.search with asyncio timeout. Never raises."""
    src = psv_b.config.identity.source_id
    try:
        return await asyncio.wait_for(
            psv_b.search(query, timeout_ms=timeout * 1000),
            timeout=float(timeout),
        )
    except asyncio.TimeoutError:
        log.warning("[%s] Timeout for mode=%s query=%s", src, query.mode, query.query)
        return []
    except Exception as exc:
        log.warning("[%s] Error for mode=%s query=%s: %s", src, query.mode, query.query, exc)
        return []


async def _try_search_api(config, query: SearchQuery, timeout: int) -> list:
    """Wrap verify_license for non-browser boards (csv_bulk, socrata, etc.). Never raises."""
    src = config.identity.source_id
    try:
        records = await asyncio.wait_for(
            verify_license(config, query, db=None),
            timeout=float(timeout),
        )
        return records or []
    except asyncio.TimeoutError:
        log.warning("[%s] Timeout for mode=%s query=%s", src, query.mode, query.query)
        return []
    except Exception as exc:
        log.warning("[%s] Error for mode=%s query=%s: %s", src, query.mode, query.query, exc)
        return []


def _match_analysis(records, last_name, first_name, license_id):
    """Return (both, name_hits, lic_hits) lists from records."""
    both = [r for r in records if _name_matches(r, last_name, first_name) and _license_matches(r, license_id)]
    name_hits = [r for r in records if _name_matches(r, last_name, first_name)]
    lic_hits = [r for r in records if _license_matches(r, license_id)]
    return both, name_hits, lic_hits


async def _try_first_and_last_psv(
    psv_b, first_name: str, last_name: str, middle_name: str,
    license_id: str, src_id: str, timeout: int,
    type_kwargs: dict | None = None,
    try_swap: bool = False,
) -> tuple | None:
    """Try first_and_last search if the board supports it. Returns (status, reason, expiry) or None.

    When try_swap=True (board has identity.try_name_swap=True), also retries with first/last
    names reversed if the standard order produces no match. Handles inputs where names are stored
    in "LastName FirstName" order without a comma delimiter.
    """
    cfg_modes = [m.mode for m in (psv_b.config.search.modes or [])]
    if "first_and_last" not in cfg_modes or not first_name or not last_name:
        return None

    async def _run_fal(f: str, l: str, label: str) -> tuple | None:
        q = SearchQuery(
            mode="first_and_last",
            query=f"{f} {l}",
            license_number=license_id,
            first_name=f,
            middle_name=middle_name or None,
            last_name=l,
            **(type_kwargs or {}),
        )
        recs = await _try_search_psv(psv_b, q, timeout)
        if not recs:
            return None
        both, name_hits, lic_hits = _match_analysis(recs, l, f, license_id)
        if both:
            return "Pass", f"Verified via {src_id} ({label})", _get_expiry(both[0])
        if name_hits and not lic_hits:
            if license_id.upper().startswith("TC"):
                return "Pass", f"Verified via {src_id} (TC — {label} name match)", _get_expiry(name_hits[0])
        if lic_hits:
            last_only = [r for r in lic_hits if _name_matches(r, l, "")]
            if last_only:
                return "Pass", f"Verified via {src_id} ({label}, license+last name match)", _get_expiry(last_only[0])
        return None

    result = await _run_fal(first_name, last_name, "first+last search")
    if result:
        return result

    # Name-swap fallback: input data sometimes stores names as "LastName FirstName" without
    # a comma separator, making it ambiguous which token is first vs last.
    if try_swap:
        log.info("[%s] Trying swapped name: first=%s last=%s", src_id, last_name, first_name)
        result = await _run_fal(last_name, first_name, "first+last swapped search")
        if result:
            return result

    return None


def _get_expiry(rec) -> str:
    """Return expiration_date as ISO string, or '' if not available."""
    d = getattr(rec, "expiration_date", None)
    if d is None:
        return ""
    try:
        return d.isoformat()
    except Exception:
        return str(d)


async def _fetch_detail_expiry(psv_b: "PsvBrowser", rec, timeout: int) -> str:
    """Secondary targeted lookup: re-search by the board's own license number to trigger
    detail_click_single and capture expiry.  Used when a multi-row search result matched
    but the detail page was never visited (so expiry is absent on the summary record).
    Only fires when the board has a detail page and the record carries a license number."""
    cfg = psv_b.config
    if not (cfg.results.has_detail_page and cfg.results.detail_trigger):
        return ""
    board_lic = getattr(rec, "license_number", None) or ""
    if not board_lic:
        return ""
    q = SearchQuery(mode="license_number", query=board_lic, license_number=board_lic)
    detail_recs = await _try_search_psv(psv_b, q, timeout)
    for dr in detail_recs:
        exp = _get_expiry(dr)
        if exp:
            return exp
    return ""


async def run_row(
    row_data: dict,
    psv_browsers: dict,   # source_id -> PsvBrowser (browser boards)
    api_configs: dict,    # source_id -> SiteConfig (non-browser boards)
    all_configs: dict,    # source_id -> SiteConfig (for routing lookup)
    timeout: int,
) -> tuple[str, str, str]:
    """Returns (status, reason, expiry_date). expiry_date is ISO string or '' for Fail rows."""
    first_name = row_data["first_name"]
    last_name = _SUFFIX_RE.sub("", row_data["last_name"]).strip()
    middle_name = row_data["middle_name"]
    license_id = row_data["license_id"]
    prov_type = row_data.get("prov_type", "").upper().strip()
    lic_state = row_data.get("lic_state", "").upper().strip()

    if not license_id:
        return "Fail", "No license ID", ""

    preferred_sids = _ROUTING.get((lic_state, prov_type), [])
    if not preferred_sids:
        return "Fail", f"No board configured for prov_type '{prov_type}'", ""

    configs_to_try_ids = [s for s in preferred_sids if s in psv_browsers or s in api_configs]
    if not configs_to_try_ids:
        return "Fail", f"Config not loaded for boards: {preferred_sids}", ""

    last_fail_reason = f"No match found in {configs_to_try_ids}"
    # Fallback: exactly-1 first+last name match with no license match.
    # Only used after all boards fail — prevents short-circuiting a better match on a later board.
    _single_name_fallback: tuple | None = None

    for src_id in configs_to_try_ids:
        is_browser = src_id in psv_browsers
        psv_b = psv_browsers.get(src_id)
        api_cfg = api_configs.get(src_id)

        # Socrata type filter: narrow SoQL query to the correct profession/board category.
        # Only set when a verified mapping exists; omitted otherwise (unfiltered search works).
        _type_val = _SOCRATA_TYPE_MAP.get((src_id, prov_type))
        if _type_val:
            _cfg = all_configs.get(src_id)
            if _cfg and getattr(_cfg.identity, "provider_type_selector", None):
                _type_kwargs: dict = {"provider_type": _type_val}
            else:
                _type_kwargs = {"license_type": _type_val}
        else:
            _type_kwargs = {}

        # --- Pass 1: search by license number ---
        q_lic = SearchQuery(
            mode="license_number",
            query=license_id,
            license_number=license_id,
            first_name=first_name or None,
            middle_name=middle_name or None,
            last_name=last_name or None,
            **_type_kwargs,
        )
        if is_browser:
            records = await _try_search_psv(psv_b, q_lic, timeout)
        else:
            records = await _try_search_api(api_cfg, q_lic, timeout)

        lic_search_garbage = False  # set when license search returns unrelated records
        if records:
            both, name_hits, lic_hits = _match_analysis(records, last_name, first_name, license_id)
            if both:
                expiry = _get_expiry(both[0])
                if not expiry and is_browser and psv_b:
                    expiry = await _fetch_detail_expiry(psv_b, both[0], timeout)
                return "Pass", f"Verified via {src_id} (license search)", expiry
            if name_hits and not lic_hits:
                if license_id.upper().startswith("TC"):
                    return "Pass", f"Verified via {src_id} (TC temp cert — name match only)", _get_expiry(name_hits[0])
                last_fail_reason = f"name found but license not matched ({len(name_hits)} records) — {src_id}"
                continue
            if lic_hits and not name_hits:
                # Last-name-only check: first name may be a nickname/variant (e.g. Kathryn→Kathy)
                last_only = [r for r in lic_hits if _name_matches(r, last_name, "")]
                if last_only:
                    return "Pass", f"Verified via {src_id} (license + last name match)", _get_expiry(last_only[0])
                # License found but name on board differs (e.g. married/maiden name change).
                # Trust the license number as the primary key when searched directly.
                return "Pass", f"Verified via {src_id} (license match — name on board differs)", _get_expiry(lic_hits[0])
            # Neither name nor license matched — board likely returned unrelated results
            # for an unrecognised license format. Fall through to last_name search.
            log.info("[%s] %d record(s) with no match at all — falling through to name search",
                     src_id, len(records))
            lic_search_garbage = True

        # --- Pass 1.5: strip non-digits and retry if license has a prefix/hyphens ---
        numeric_id = re.sub(r"\D", "", license_id)
        if not lic_search_garbage and numeric_id and numeric_id != license_id:
            log.info("[%s] No results for '%s', retrying with numeric-only: '%s'",
                     src_id, license_id, numeric_id)
            q_num = SearchQuery(
                mode="license_number",
                query=numeric_id,
                license_number=numeric_id,
                first_name=first_name or None,
                middle_name=middle_name or None,
                last_name=last_name or None,
                **_type_kwargs,
            )
            if is_browser:
                records = await _try_search_psv(psv_b, q_num, timeout)
            else:
                records = await _try_search_api(api_cfg, q_num, timeout)

            if records:
                both, name_hits, lic_hits = _match_analysis(records, last_name, first_name, numeric_id)
                if both:
                    return "Pass", f"Verified via {src_id} (numeric license search)", _get_expiry(both[0])
                if name_hits and not lic_hits:
                    last_fail_reason = f"name found but numeric license not matched ({len(name_hits)} records) — {src_id}"
                    continue
                if lic_hits and not name_hits:
                    last_only = [r for r in lic_hits if _name_matches(r, last_name, "")]
                    if last_only:
                        return "Pass", f"Verified via {src_id} (numeric license + last name match)", _get_expiry(last_only[0])
                    return "Pass", f"Verified via {src_id} (numeric license match — name on board differs)", _get_expiry(lic_hits[0])
                log.info("[%s] Numeric search also returned unrelated records — falling through to name search",
                         src_id)
                lic_search_garbage = True

        # --- Pass 2: fall back to last_name search if license searches returned nothing ---
        if not last_name:
            continue
        log.info("[%s] License search empty, trying last_name fallback for %s", src_id, license_id)
        q_name = SearchQuery(
            mode="last_name",
            query=last_name,
            license_number=license_id,
            first_name=first_name or None,
            middle_name=middle_name or None,
            last_name=last_name or None,
            **_type_kwargs,
        )

        if is_browser:
            records = await _try_search_psv(psv_b, q_name, timeout)
        else:
            records = await _try_search_api(api_cfg, q_name, timeout)

        _do_swap = is_browser and bool(getattr(
            (psv_b.config.identity if psv_b else None), "try_name_swap", False))

        if not records:
            # Pass 2.5a: last_name returned nothing — try first_and_last if board supports it
            if is_browser and first_name:
                fal = await _try_first_and_last_psv(
                    psv_b, first_name, last_name, middle_name, license_id, src_id, timeout,
                    type_kwargs=_type_kwargs, try_swap=_do_swap,
                )
                if fal:
                    return fal
            continue

        both, name_hits, lic_hits = _match_analysis(records, last_name, first_name, license_id)
        if both:
            expiry = _get_expiry(both[0])
            if not expiry and is_browser and psv_b:
                expiry = await _fetch_detail_expiry(psv_b, both[0], timeout)
            return "Pass", f"Verified via {src_id} (name search)", expiry
        if name_hits and not lic_hits:
            if license_id.upper().startswith("TC"):
                return "Pass", f"Verified via {src_id} (TC temp cert — name match only)", _get_expiry(name_hits[0])
            # Track as last-resort fallback when exactly 1 first+last match — PSV may store
            # a license from a different class (e.g. TL- telemedicine, COM- combined) that
            # does not appear in this board's search type.
            if len(name_hits) == 1 and first_name and _single_name_fallback is None:
                _single_name_fallback = (src_id, name_hits[0])
            # Pass 2.5b: name found but license not matched and results are truncated
            if is_browser and len(records) >= 20 and first_name:
                fal = await _try_first_and_last_psv(
                    psv_b, first_name, last_name, middle_name, license_id, src_id, timeout,
                    type_kwargs=_type_kwargs, try_swap=_do_swap,
                )
                if fal:
                    return fal
            last_fail_reason = f"name found but license not matched in name search ({len(name_hits)} records) — {src_id}"
            continue
        if lic_hits and not name_hits:
            last_fail_reason = f"license matched but name mismatch in name search — {src_id}"
            continue
        # Pass 2.5c: some records returned but nothing matched — try first_and_last when truncated
        if is_browser and len(records) >= 20 and first_name:
            fal = await _try_first_and_last_psv(
                psv_b, first_name, last_name, middle_name, license_id, src_id, timeout,
                type_kwargs=_type_kwargs, try_swap=_do_swap,
            )
            if fal:
                return fal
        # Pass 2.5d: small result set — try last-name-only + license for first-name spelling variants
        # (e.g. "Dierdre" vs "Deirdre").  Only safe when results are few (≤5) to avoid false positives.
        if len(records) <= 5:
            last_lic = [r for r in records if _license_matches(r, license_id) and _name_matches(r, last_name, "")]
            if last_lic:
                return "Pass", f"Verified via {src_id} (last name + license match)", _get_expiry(last_lic[0])
        last_fail_reason = f"{len(records)} record(s) from name search but no license+name match — {src_id}"

    if _single_name_fallback:
        s_id, s_rec = _single_name_fallback
        expiry = _get_expiry(s_rec)
        if not expiry and s_id in psv_browsers:
            expiry = await _fetch_detail_expiry(psv_browsers[s_id], s_rec, timeout)
        return "Pass", f"Verified via {s_id} (name match — PSV license class not in this search type)", expiry
    return "Fail", last_fail_reason, ""


def write_results(results: list[dict], output_path: Path, append: bool) -> None:
    if append and output_path.exists():
        wb = openpyxl.load_workbook(str(output_path))
        ws = wb.active
    else:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Results"
        ws.append(["First Name", "Middle Name", "Last Name",
                   "License State", "Prov Type", "License Type", "License ID",
                   "Status", "License Expiry", "Reason"])
        for cell in ws[1]:
            cell.font = Font(bold=True)

    green = PatternFill("solid", fgColor="C6EFCE")
    red = PatternFill("solid", fgColor="FFC7CE")

    for r in results:
        ws.append([
            r["first_name"], r["middle_name"], r["last_name"],
            r["lic_state"], r["prov_type"], r["lic_type"], r["license_id"],
            r["status"], r.get("expiry_date", ""),
            r["reason"] if r["status"] == "Fail" else "",
        ])
        fill = green if r["status"] == "Pass" else red
        for col in range(1, 11):
            ws.cell(row=ws.max_row, column=col).fill = fill

    wb.save(str(output_path))


def load_input_rows(input_path: str, state_filter: str, sheet_name: str = "") -> list[dict]:
    wb = openpyxl.load_workbook(input_path, read_only=True, data_only=True)
    if sheet_name and sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
    else:
        ws = wb["PSV Tab"] if "PSV Tab" in wb.sheetnames else wb.active

    all_rows = list(ws.iter_rows(min_row=1, values_only=True))
    if not all_rows:
        return []

    start = 0
    first = all_rows[0]
    v = str(first[C_LIC_STATE] or "").strip().upper() if len(first) > C_LIC_STATE else ""
    if v in ("LIC_STATE", "C_LIC_STATE", "LICENSE STATE", "STATE", "LICENSE STATE", ""):
        start = 1

    # Discover NPI_NO column index by header name (dynamic — may be absent)
    npi_col_idx: Optional[int] = None
    if start == 1:
        for i, cell in enumerate(first):
            if cell is None:
                continue
            header = str(cell).strip().upper().replace("_", " ").replace(".", "")
            for alias in _NPI_HEADER_ALIASES:
                if header == alias.upper().replace("_", " "):
                    npi_col_idx = i
                    break
            if npi_col_idx is not None:
                break

    rows_data = []
    for row in all_rows[start:]:
        if not row or all(v is None for v in row):
            continue

        def c(idx):
            val = row[idx] if idx < len(row) else None
            return str(val).strip() if val is not None and str(val) != "None" else ""

        lic_state = c(C_LIC_STATE)
        if state_filter:
            allowed = {s.strip().upper() for s in state_filter.split(",")}
            if lic_state.upper() not in allowed:
                continue

        npi_val = c(npi_col_idx) if npi_col_idx is not None else ""
        # Strip non-digits and validate as 10-digit NPI
        import re as _re
        npi_clean = _re.sub(r"\D", "", npi_val)
        if len(npi_clean) != 10:
            npi_clean = ""

        rows_data.append({
            "first_name": c(C_FIRST_NAME),
            "middle_name": c(C_MIDDLE_NAME),
            "last_name": c(C_LAST_NAME),
            "epdb_pin": c(C_EPDB_PIN),
            "prov_type": c(C_PROV_TYPE),
            "maintained_by": c(C_MAINTAINED_BY),
            "npi_no": npi_clean,
            "lic_state": lic_state,
            "lic_type": c(C_LIC_TYPE),
            "license_id": c(C_LIC_ID),
        })
    return rows_data


def load_configs_by_source_ids(source_ids: set[str]) -> list:
    """Load configs for a specific set of source_ids (from routing table). Skips missing configs."""
    sites_dir = Path(__file__).parent / "sites"
    configs = []
    for sid in sorted(source_ids):
        config_path = sites_dir / sid / "config.yaml"
        if not config_path.exists():
            log.warning("No config.yaml for routed board %s — rows needing it will fail", sid)
            continue
        try:
            cfg = load_config(str(config_path))
            if getattr(cfg.identity, "skip", False):
                reason = getattr(cfg.identity, "skip_reason", "skip=true in config")
                log.info("Skipping board %s (skip=true): %s", sid, reason)
                continue
            configs.append(cfg)
            log.info("Loaded config: %s", sid)
        except Exception as exc:
            log.warning("Failed to load config %s: %s", sid, exc)
    return configs


def _board_proxy(cfg, resolved_proxy_cfg: Optional[dict]) -> Optional[dict]:
    """Return the proxy dict to use for a specific board config.

    Rules (mirrors the config.yaml proxy.enabled semantics):
      enabled: false  → always None  (board explicitly blocks proxy, e.g. NH_OPLC)
      enabled: true   → resolved_proxy_cfg (board requires proxy — comes from env or psv_config.yaml)
      enabled: None   → resolved_proxy_cfg if available, None otherwise (follow env)
    """
    if cfg.transport.proxy.enabled is False:
        return None
    return resolved_proxy_cfg


def _log_proxy_plan(state: str, configs: list, resolved_proxy_cfg: Optional[dict]) -> None:
    """Emit a single INFO line summarising which boards use proxy and which don't."""
    using_proxy = []
    no_proxy = []
    proxy_required_but_missing = []

    for cfg in configs:
        sid = cfg.identity.source_id
        enabled = cfg.transport.proxy.enabled
        if enabled is False:
            no_proxy.append(sid)
        elif enabled is True:
            if resolved_proxy_cfg:
                using_proxy.append(sid)
            else:
                proxy_required_but_missing.append(sid)
        else:
            # None — optional, use if available
            if resolved_proxy_cfg:
                using_proxy.append(sid)
            else:
                no_proxy.append(sid)

    server = resolved_proxy_cfg.get("server") if resolved_proxy_cfg else None
    if using_proxy:
        log.info("[%s] Proxy ON  (%s): %s", state, server, using_proxy)
    if no_proxy:
        log.info("[%s] Proxy OFF (config): %s", state, no_proxy)
    if proxy_required_but_missing:
        log.warning(
            "[%s] Boards require proxy but none is configured — they will likely fail: %s. "
            "Set PROXY=proxy:9119 or add proxy.server to psv_config.yaml.",
            state, proxy_required_but_missing,
        )


async def run_state(
    rows: list[dict],
    state: str,
    output_path: Path,
    append: bool = False,
    batch_size: int = 10,
    timeout: int = 45,
    sequential: bool = False,
) -> tuple[int, int]:
    """Run PSV verification for one state. Returns (passes, fails).

    Caller must have already called _load_routing() before invoking this.
    The routing table (_ROUTING) must be populated.
    """
    # Determine which boards are needed for the prov_types in this batch
    needed_sids: set[str] = set()
    no_routing_combos: set[tuple[str, str]] = set()
    for row in rows:
        key = (row["lic_state"].upper(), row["prov_type"].upper())
        sids = _ROUTING.get(key, [])
        if sids:
            needed_sids.update(sids)
        else:
            no_routing_combos.add(key)

    if no_routing_combos:
        log.warning(
            "[%s] %d (state,prov_type) combo(s) have no board routing — those rows will Fail: %s",
            state, len(no_routing_combos), sorted(no_routing_combos),
        )

    if not needed_sids:
        log.warning("[%s] No boards in routing table for any row — all %d rows will Fail", state, len(rows))
        results = [{**r, "status": "Fail", "reason": f"No board configured for prov_type '{r['prov_type']}'",
                    "expiry_date": ""} for r in rows]
        write_results(results, output_path, append)
        return 0, len(rows)

    log.info("[%s] Routing requires %d board(s): %s", state, len(needed_sids), sorted(needed_sids))
    configs = load_configs_by_source_ids(needed_sids)
    if not configs:
        log.error("[%s] No board configs could be loaded — check sites/<board>/config.yaml", state)
        results = [{**r, "status": "Fail", "reason": "Board config not found", "expiry_date": ""} for r in rows]
        write_results(results, output_path, append)
        return 0, len(rows)

    log.info("[%s] Loaded %d board config(s): %s", state,
             len(configs), [c.identity.source_id for c in configs])

    browser_configs = [c for c in configs if c.identity.archetype in _BROWSER_ARCHETYPES]
    api_configs_list = [c for c in configs if c.identity.archetype not in _BROWSER_ARCHETYPES]
    api_configs = {c.identity.source_id: c for c in api_configs_list}

    log.info("[%s] Browser boards (%d): %s  API boards (%d): %s", state,
             len(browser_configs), [c.identity.source_id for c in browser_configs],
             len(api_configs_list), [c.identity.source_id for c in api_configs_list])

    # --- Proxy diagnostics ---
    # Resolve proxy once; boards with proxy.enabled: false will override to None.
    proxy_cfg = get_proxy_config()
    _log_proxy_plan(state, configs, proxy_cfg)

    total = len(rows)
    passes = fails = 0

    async with async_playwright() as pw:
        browser: Browser | None = None
        psv_browsers: dict = {}

        if browser_configs:
            log.info("[%s] Launching shared browser ...", state)
            browser = await pw.chromium.launch(headless=True, args=_STEALTH_ARGS)
            for cfg in browser_configs:
                board_proxy = _board_proxy(cfg, proxy_cfg)
                psv_browsers[cfg.identity.source_id] = PsvBrowser(cfg, browser, board_proxy)
            log.info("[%s] Browser ready. Boards: %s", state, list(psv_browsers.keys()))

        try:
            for batch_start in range(0, total, batch_size):
                batch = rows[batch_start: batch_start + batch_size]
                batch_num = batch_start // batch_size + 1
                total_batches = (total + batch_size - 1) // batch_size
                log.info("[%s] === Batch %d/%d (rows %d-%d of %d) ===",
                         state, batch_num, total_batches,
                         batch_start + 1, batch_start + len(batch), total)

                if sequential:
                    outcomes = []
                    for r in batch:
                        outcomes.append(await run_row(r, psv_browsers, api_configs, {}, timeout))
                else:
                    outcomes = list(await asyncio.gather(*[
                        run_row(r, psv_browsers, api_configs, {}, timeout)
                        for r in batch
                    ]))

                batch_results = []
                for row, (status, reason, expiry_date) in zip(batch, outcomes):
                    log.info("  [%s] %s %s %s %s → %s | %s%s",
                             row["lic_state"], row["prov_type"], row["last_name"], row["first_name"],
                             row["license_id"], status,
                             f"expiry={expiry_date} | " if expiry_date else "", reason)
                    if status == "Pass":
                        passes += 1
                    else:
                        fails += 1
                    batch_results.append({**row, "status": status, "reason": reason,
                                          "expiry_date": expiry_date})

                write_results(batch_results, output_path, append)
                append = True
                log.info("[%s] Saved batch. Running totals: %d Pass / %d Fail / %d done of %d",
                         state, passes, fails, batch_start + len(batch), total)

        finally:
            if browser:
                log.info("[%s] Closing shared browser...", state)
                await browser.close()

    log.info("[%s] State complete: %d Pass / %d Fail / %d Total", state, passes, fails, total)
    return passes, fails


async def run_state_orchestrated(
    rows: list[dict],
    state: str,
    emitter,                      # orchestrator.output_emitter.OutputEmitter
    run_id: str,
    enable_nppes: bool = True,
    enable_ai: bool = True,
    force_ai: bool = False,
    timeout: int = 45,
) -> tuple[int, int]:
    """Orchestrated per-state run. For every row:
       1. NPPES universal fetch
       2. Rule-based ladder (master)
       3. NPPES targeted retry (if discrepancy)
       4. AI agent (when ladders escalate, unless --no-ai)
       5. Emit to 4 channels via the shared OutputEmitter

    The emitter accumulates rows across states for one combined per-channel
    file at flush time.
    """
    from orchestrator import ladder as ladder_mod
    from orchestrator import nppes_client as nppes_mod
    from orchestrator import ai_agent as ai_mod
    from orchestrator.output_emitter import RowOutcome
    from orchestrator.trace import RowTrace, make_master_row_id

    if not _ROUTING:
        _load_routing()

    # Resolve routing for every row
    needed_sids: set[str] = set()
    for row in rows:
        key = (row["lic_state"].upper(), row["prov_type"].upper())
        for sid in _ROUTING.get(key, []):
            needed_sids.add(sid)

    configs_list = load_configs_by_source_ids(needed_sids) if needed_sids else []
    cfg_by_sid = {c.identity.source_id: c for c in configs_list}

    browser_configs = [c for c in configs_list if c.identity.archetype in _BROWSER_ARCHETYPES]
    api_configs_list = [c for c in configs_list if c.identity.archetype not in _BROWSER_ARCHETYPES]
    api_cfg_by_sid = {c.identity.source_id: c for c in api_configs_list}

    proxy_cfg = get_proxy_config()
    _log_proxy_plan(state, configs_list, proxy_cfg)

    passes = fails = 0

    async with async_playwright() as pw:
        browser = None
        psv_browsers: dict = {}
        if browser_configs:
            log.info("[%s] Launching shared browser ...", state)
            browser = await pw.chromium.launch(headless=True, args=_STEALTH_ARGS)
            for cfg_obj in browser_configs:
                board_proxy = _board_proxy(cfg_obj, proxy_cfg)
                psv_browsers[cfg_obj.identity.source_id] = PsvBrowser(cfg_obj, browser, board_proxy)

        async def executor(cfg_obj, query, run_id_arg):
            """SearchExecutor: route browser boards through PsvBrowser, others
            through verify_license."""
            sid = cfg_obj.identity.source_id
            if sid in psv_browsers:
                return await psv_browsers[sid].search(
                    query, timeout_ms=timeout * 1000, run_id=run_id_arg,
                )
            return await asyncio.wait_for(
                verify_license(cfg_obj, query, db=None),
                timeout=float(timeout),
            )

        try:
            for idx, row in enumerate(rows):
                master_row_id = make_master_row_id(idx, row.get("last_name", ""),
                                                   row.get("license_id", ""))
                trace = RowTrace(
                    master_row_id=master_row_id,
                    run_id=run_id,
                    state=state,
                    prov_type=row.get("prov_type", ""),
                    npi_no=row.get("npi_no", ""),
                )

                # --- NPPES universal fetch ---
                nppes = None
                discrepancy = None
                if enable_nppes and row.get("npi_no"):
                    try:
                        nppes = await nppes_mod.fetch_provider(row["npi_no"])
                    except Exception as exc:
                        log.warning("[%s] NPPES fetch failed for npi=%s: %s",
                                    state, row.get("npi_no"), exc)
                    if nppes:
                        trace.nppes_used = True
                        discrepancy = nppes_mod.diff_master_vs_nppes(row, nppes)
                        trace.nppes_discrepancy = discrepancy.to_dict()

                # --- Resolve routing for this row ---
                prov_type_upper = row.get("prov_type", "").upper()
                key = (row["lic_state"].upper(), prov_type_upper)
                routed_sids = _ROUTING.get(key, [])
                routed_configs = [cfg_by_sid[s] for s in routed_sids if s in cfg_by_sid]

                # Build per-board license_type map so {type} template in extra_selects
                # resolves to the board's specific dropdown value (e.g. "NP", "LPN").
                board_lt_map: dict[str, str] = {}
                for sid in routed_sids:
                    lt_val = _SOCRATA_TYPE_MAP.get((sid, prov_type_upper))
                    if lt_val:
                        board_lt_map[sid] = lt_val

                ladder_result = None
                ai_result = None

                if not routed_configs:
                    trace.final_outcome = "Fail"
                    trace.final_reason = "no_routing"
                else:
                    # --- Run rule-based ladder ---
                    ladder_result = await ladder_mod.run_ladder(
                        routed_configs=routed_configs,
                        master_row=row,
                        nppes_record=nppes,
                        discrepancy=discrepancy,
                        trace=trace,
                        executor=executor,
                        timeout_s=timeout,
                        board_license_type_map=board_lt_map,
                    )

                    # --- AI agent fallback ---
                    if enable_ai and (
                        ladder_result.status == "EscalateAi" or force_ai
                    ):
                        candidate_cache: dict[str, list] = {}
                        # Replay stored records into cache via fresh query? We
                        # don't keep records around between rungs (the executor
                        # returns them; ladder doesn't cache). For pick_candidate
                        # to work, the agent must call try_search itself first.
                        ai_result = await ai_mod.run_ai_agent(
                            master_row=row,
                            nppes=nppes,
                            discrepancy=discrepancy,
                            routed_configs=routed_configs,
                            trace=trace,
                            executor=executor,
                            candidate_cache=candidate_cache,
                            timeout_s=timeout,
                        )
                        if ai_result.outcome == "resolved":
                            trace.final_outcome = "Pass"
                        else:
                            trace.final_outcome = "Fail"
                            trace.final_reason = ai_result.reason

                outcome = RowOutcome(
                    master_row=row,
                    master_row_id=master_row_id,
                    trace=trace,
                    nppes=nppes,
                    discrepancy=discrepancy,
                    ladder_result=ladder_result,
                    ai_result=ai_result,
                )
                emitter.collect(outcome)

                if outcome.status == "Pass":
                    passes += 1
                else:
                    fails += 1
                log.info("[%s] %s %s %s %s -> %s | %s",
                         state, row["prov_type"], row["last_name"],
                         row["first_name"], row["license_id"],
                         outcome.status, outcome.reason or "ok")

        finally:
            if browser:
                await browser.close()
    log.info("[%s] State complete: %d Pass / %d Fail / %d Total",
             state, passes, fails, len(rows))
    return passes, fails


async def main_async(args: argparse.Namespace) -> None:
    _load_routing()

    state = args.state.upper()
    output_path = Path(args.output)

    log.info("Loading input rows from %s", args.input)
    rows = load_input_rows(args.input, state, getattr(args, "sheet", ""))
    log.info("Found %d rows for state %s", len(rows), state)
    if not rows:
        log.error("No rows found — check --state and input file")
        sys.exit(1)

    if args.skip_rows and args.skip_rows > 0:
        rows = rows[args.skip_rows:]
        log.info("Skipping first %d rows (--skip-rows), %d remaining", args.skip_rows, len(rows))

    if args.max_rows and args.max_rows < len(rows):
        rows = rows[: args.max_rows]
        log.info("Limiting to %d rows (--max-rows)", args.max_rows)

    passes, fails = await run_state(
        rows=rows,
        state=state,
        output_path=output_path,
        append=False,
        batch_size=args.batch_size,
        timeout=args.timeout,
        sequential=args.sequential,
    )
    log.info("=== COMPLETE: %d Pass / %d Fail / %d Total → %s ===",
             passes, fails, passes + fails, output_path)


def main() -> None:
    p = argparse.ArgumentParser(description="PSV batch tester")
    p.add_argument("--input", required=True, help="Input Excel path (PSV Tab sheet)")
    p.add_argument("--output", required=True, help="Output Excel path")
    p.add_argument("--state", required=True, help="State abbreviation (e.g. MD, WY)")
    p.add_argument("--batch-size", type=int, default=10,
                   help="Rows to process per batch before writing (default: 10)")
    p.add_argument("--timeout", type=int, default=45,
                   help="Per-board per-mode timeout in seconds (default: 45)")
    p.add_argument("--skip-rows", type=int, default=0,
                   help="Skip the first N rows of the filtered input (default: 0)")
    p.add_argument("--max-rows", type=int, default=0,
                   help="Stop after processing this many rows (0 = all)")
    p.add_argument("--sequential", action="store_true", default=False,
                   help="Process rows one at a time (avoids concurrent board overload)")
    p.add_argument("--sheet", default="",
                   help="Sheet name to read from (default: 'PSV Tab')")
    args = p.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
