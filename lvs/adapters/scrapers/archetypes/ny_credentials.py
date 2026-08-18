"""NY_CREDENTIALS archetype — NYSED Office of the Professions verification API.

The public verification page (eservices.nysed.gov/professions/verification-search)
is a Vue SPA that is a thin client over a clean public JSON API:

    base = https://api.nysed.gov/rosa/V2
    auth = header  x-oapi-key: <static key embedded in the site's vsc.js bundle>

Three endpoints are used:
  GET /professions
        → [{professionCode, profession}, ...]  (reference list; not fetched per query)
  GET /byProfessionAndName?professionCode=ALL&name=<LAST FIRST>
        → {content: [ {name, profession, professionCode, licenseNumber, address,
                       dateOfLicensure}, ... ]}   (summary rows; NO status)
        NB: capped at 10 results, `page`/`size` params are ignored (no pagination).
  GET /byProfessionAndLicenseNumber?professionCode=<code>&licenseNumber=<num>
        → {name, profession, professionCode, licenseNumber, status,
           dateOfLicensure, registeredThroughDate, address, ...}  (full detail)
        NB: professionCode=ALL is rejected here (HTTP 408); a specific code is required.

Every field is nested as {"value": ..., "label": ...}.

Flow per query (validated at 24/25 on real records):
  1. PRIMARY — name search with professionCode=ALL. The returned row carries the
     licensee's real professionCode + licenseNumber, so it auto-resolves the profession
     without any provider_type → code guessing. Name-gate the ≤10 candidates against the
     master first/last, prefer the row whose license number matches the input.
  2. Detail lookup for the chosen row (byProfessionAndLicenseNumber) → status + dates.
  3. FALLBACK — license-first. When name search finds nothing (common-name >10 overflow,
     or the input carries a former/maiden name), iterate the provider_type's candidate
     profession codes against byProfessionAndLicenseNumber with the (prefix-normalized)
     license number; first non-empty response wins.

Config carries the API surface in the existing json_api section (no new model fields):
    json_api.endpoint_url  = "https://api.nysed.gov/rosa/V2"   (base; endpoints appended)
    json_api.headers       = {"x-oapi-key": "..."}
    json_api.timeout_ms
And provider_type → candidate codes in identity.profession_code_map, values comma-joined
(e.g. {"ABA": "071,078"}).
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from engine.evidence import _query_label, resolve_evidence_path
from engine.models import LicenseRecord, SearchQuery, SiteConfig
from engine.output import upsert_to_db
from engine.post_processors import normalize_status, parse_date
from engine.proxy import get_proxy_config
from orchestrator.disambiguator import (
    first_name_matches,
    last_name_matches,
    license_numerics_match,
)
from ._shared import _emit_event

log = logging.getLogger(__name__)

_DEFAULT_BASE = "https://api.nysed.gov/rosa/V2"


def _v(field) -> str:
    """Unwrap a NYSED {"value": ..., "label": ...} field to its string value."""
    if isinstance(field, dict):
        val = field.get("value")
    else:
        val = field
    if val is None:
        return ""
    return str(val).strip()


def _norm_tok(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", (s or "")).upper()


def _split_ny_name(full: str, in_first: str = "", in_last: str = "") -> tuple[str, str]:
    """NYSED renders names as 'LAST [LAST2 ...] FIRST [MIDDLE ...]'. Return (first, last).

    The surname may be multiple tokens (DI NITTO, MONTES DE OCA, HARBORD ANSMAN), so a
    naive 'token0=last, token1=first' split is wrong. When the input first name is known,
    locate it among the tokens: everything before it is the surname. Falls back to the
    naive split when the input can't be aligned.
    """
    toks = re.sub(r"\s+", " ", (full or "").strip()).split()
    if not toks:
        return ("", "")
    if len(toks) == 1:
        return ("", toks[0])
    # Align on the input first name: tokens before it are the (possibly multi-token) surname.
    if in_first:
        inf = _norm_tok(in_first)
        for i in range(1, len(toks)):
            t = _norm_tok(toks[i])
            if t == inf or first_name_matches(in_first, toks[i]):
                return (toks[i], " ".join(toks[:i]))
    # Align on a multi-token input surname at the start (e.g. "DI NITTO" == "Dinitto").
    if in_last:
        inl = _norm_tok(in_last)
        acc = ""
        for i in range(len(toks) - 1):
            acc += _norm_tok(toks[i])
            if acc == inl:
                return (toks[i + 1], " ".join(toks[: i + 1]))
    return (toks[1], toks[0])


def _split_address(addr: str) -> tuple[Optional[str], Optional[str]]:
    """'DRYDEN NY' → (city='DRYDEN', state='NY'). Trailing 2-letter token = state."""
    addr = (addr or "").strip()
    if not addr:
        return (None, None)
    toks = addr.split()
    if len(toks) >= 2 and len(toks[-1]) == 2 and toks[-1].isalpha():
        return (" ".join(toks[:-1]) or None, toks[-1].upper())
    return (addr, None)


def _normalize_license(lic: str) -> list[str]:
    """Candidate forms of an input license number to try against the API.

    NYSED stores bare numeric strings; input rows carry prefixes/suffixes the API
    strips ('F356564' → '356564', '005491-1' → '005491', 'N006160' → '006160').
    Returns de-duplicated candidates, most-specific first.
    """
    lic = (lic or "").strip()
    if not lic:
        return []
    cands = [lic]
    # Strip a single leading letter prefix (F/N/...)
    m = re.match(r"^[A-Za-z](\d.*)$", lic)
    if m:
        cands.append(m.group(1))
    # Drop a trailing '-NN' segment suffix
    base = lic.split("-")[0]
    if base and base != lic:
        cands.append(base)
        m2 = re.match(r"^[A-Za-z](\d.*)$", base)
        if m2:
            cands.append(m2.group(1))
    # Strip all non-alphanumerics as a last resort
    stripped = re.sub(r"[^A-Za-z0-9]", "", lic)
    if stripped and stripped not in cands:
        cands.append(stripped)
    seen: list[str] = []
    for c in cands:
        if c and c not in seen:
            seen.append(c)
    return seen


def _name_consistent(first: str, last: str, cand_full: str) -> bool:
    """Loose guard for exact-license hits: does any input name token appear among the
    candidate's name tokens? Tolerant of married/reordered/multi-token names (NYSED
    renders 'HOLMES JENNA BERGMAN WENTZEL' for an input of Jenna Wentzel) while still
    rejecting an unrelated person who happens to share a license number in another
    profession. Returns True when there is nothing to check against.
    """
    inp = [t for t in re.split(r"[^A-Za-z0-9]+", f"{first} {last}".upper()) if len(t) > 1]
    if not inp:
        return True
    cand = [t for t in re.split(r"[^A-Za-z0-9]+", (cand_full or "").upper()) if len(t) > 1]
    cset = set(cand)
    for tok in inp:
        if tok in cset:
            return True
        # fuzzy per-token (handles minor spelling/OCR/transliteration differences)
        for ct in cand:
            if last_name_matches(tok, ct):
                return True
    return False


def _candidate_codes(config: SiteConfig, query: SearchQuery) -> list[str]:
    """Candidate NYSED profession codes for the row's provider_type (if the pipeline
    passed one — it usually does not). Used only as a priority hint for the sweep."""
    pmap = getattr(config.identity, "profession_code_map", {}) or {}
    prov = (query.provider_type or "").upper().strip()
    raw = pmap.get(prov, "")
    return [c.strip() for c in raw.split(",") if c.strip()]


# Per-process API response cache, keyed by (base, path, sorted-params). The pipeline
# re-queries the same license/name across many ladder rungs; caching makes rungs 2+ instant.
_GET_CACHE: dict[tuple, tuple[int, object]] = {}

# Profession-code list cache, keyed by API base — one /professions call per process.
_PROFESSIONS_CACHE: dict[str, list[str]] = {}

# Codes the /professions endpoint lists but that behave like "ALL" for a license lookup
# (return HTTP 408 "must choose a specific profession") — skip them in the sweep.
_ALL_LIKE_CODES = {"ALL", "029"}


async def _all_profession_codes(base: str, getter) -> list[str]:
    """Every specific profession code (cached). `getter` is the request helper."""
    if base in _PROFESSIONS_CACHE:
        return _PROFESSIONS_CACHE[base]
    codes: list[str] = []
    try:
        st, body = await getter("professions", {})
        if st == 200 and isinstance(body, list):
            for p in body:
                c = str(p.get("professionCode") or "").strip()
                if c and c not in _ALL_LIKE_CODES and c not in codes:
                    codes.append(c)
    except Exception:
        codes = []
    _PROFESSIONS_CACHE[base] = codes
    return codes


def _ordered_codes(config: SiteConfig, query: SearchQuery,
                   name_codes: list[str], all_codes: list[str]) -> list[str]:
    """Priority-ordered profession codes for the license sweep:
    provider_type codes → name-search codes → board's configured NY professions →
    every remaining profession. De-duplicated, order preserved."""
    pmap = getattr(config.identity, "profession_code_map", {}) or {}
    configured: list[str] = []
    for raw in pmap.values():
        for c in str(raw).split(","):
            c = c.strip()
            if c and c not in _ALL_LIKE_CODES:
                configured.append(c)
    order: list[str] = []
    for src in (_candidate_codes(config, query), name_codes, configured, all_codes):
        for c in src:
            if c and c not in _ALL_LIKE_CODES and c not in order:
                order.append(c)
    return order


def _record_from_detail(detail: dict, config: SiteConfig, source_url: str,
                        query: Optional[SearchQuery] = None) -> LicenseRecord:
    """Build a LicenseRecord from a byProfessionAndLicenseNumber detail object."""
    out = config.output
    full_name = _v(detail.get("name"))
    in_first = (query.first_name or "") if query else ""
    in_last = (query.last_name or "") if query else ""
    first, last = _split_ny_name(full_name, in_first, in_last)
    city, state = _split_address(_v(detail.get("address")))
    return LicenseRecord(
        source_id=config.identity.source_id,
        license_number=_v(detail.get("licenseNumber")),
        licensee_first_name=first or None,
        licensee_last_name=last or None,
        licensee_full_name=full_name or None,
        license_type=_v(detail.get("profession")) or None,
        profession_code=_v(detail.get("professionCode")) or None,
        status=normalize_status(_v(detail.get("status")), out.status_map),
        issue_date=parse_date(_v(detail.get("dateOfLicensure")), out.date_formats),
        expiration_date=parse_date(_v(detail.get("registeredThroughDate")), out.date_formats),
        address=_v(detail.get("address")) or None,
        city=city,
        state_code=state,
        source_url=source_url,
        raw_fields=detail,
    )


async def scrape_ny_credentials(
    config: SiteConfig, query: SearchQuery, db, t0: float, run_id: str,
) -> list[LicenseRecord]:
    import httpx

    source_id = config.identity.source_id
    api = config.json_api
    base = ((api.endpoint_url if api else "") or _DEFAULT_BASE).rstrip("/")
    headers = {"Accept": "application/json"}
    if api and api.headers:
        headers.update(api.headers)
    timeout_ms = (api.timeout_ms if api else 30000) or 30000

    if "x-oapi-key" not in {k.lower() for k in headers}:
        log.error("[%s] missing x-oapi-key in json_api.headers", source_id)
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, "no_api_key")
        return []

    log.info("[%s] NY_CREDENTIALS run_id=%s query=%s first=%s last=%s lic=%s prov=%s",
             source_id, run_id, query.mode, query.first_name, query.last_name,
             query.license_number, query.provider_type)

    # Pure-API board: use httpx (no browser) — the Vue SPA's own JSON API is reachable
    # directly and this avoids ~4s of Playwright startup per ladder rung. NY runs proxy-off
    # (the corporate proxy 407s this API); trust_env=False ignores HTTP(S)_PROXY env vars.
    client_kwargs: dict = {"headers": headers, "timeout": timeout_ms / 1000.0,
                           "verify": False, "trust_env": False}
    if config.transport.proxy.enabled is not False:
        pc = get_proxy_config()
        if pc and pc.get("server"):
            client_kwargs["proxy"] = pc["server"]
            client_kwargs["trust_env"] = True
    records: list[LicenseRecord] = []
    evidence: list[dict] = []

    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            if True:
                async def _get(path: str, params: dict) -> tuple[int, object]:
                    # Per-process response cache. The pipeline re-invokes this board across
                    # ~9-13 ladder rungs per row (license_number, license_and_last,
                    # first_and_last, …), each of which would otherwise re-run the same name
                    # search + 88-code license sweep. Deterministic API → cache by (path,
                    # params) so rungs 2+ for the same license/name are served instantly.
                    ck = (base, path, tuple(sorted(params.items())))
                    if ck in _GET_CACHE:
                        status, body = _GET_CACHE[ck]
                        evidence.append({"url": f"{base}/{path}", "status": status,
                                         "body": body, "cached": True})
                        return status, body
                    resp = await client.get(f"{base}/{path}", params=params)
                    status = resp.status_code
                    body: object = None
                    if status == 200:
                        try:
                            body = resp.json()
                        except Exception:
                            body = None
                    evidence.append({"url": str(resp.url), "status": status,
                                     "body": body if status == 200 else resp.text})
                    _GET_CACHE[ck] = (status, body)
                    return status, body

                chosen_detail: Optional[dict] = None
                first = (query.first_name or "").strip()
                last = (query.last_name or "").strip()
                lic_num = (query.license_number or "").strip()

                # ── STEP 1: name search (harvest codes + direct exact match) ──
                # NYSED's name endpoint returns each candidate's profession code AND
                # license number. If a candidate's license matches the input, that is an
                # exact identification — done. We also harvest the candidates' codes to
                # prioritize the license sweep below. (The pipeline does NOT pass
                # provider_type, so the archetype cannot rely on it for code selection.)
                gated: list[dict] = []
                name_codes: list[str] = []
                if last or first:
                    name_q = f"{last} {first}".strip()
                    st, body = await _get("byProfessionAndName",
                                          {"professionCode": "ALL", "name": name_q})
                    cands = (body or {}).get("content", []) if isinstance(body, dict) else []
                    log.info("[%s] name '%s' → %d candidate(s)", source_id, name_q, len(cands))
                    for c in cands:
                        code = _v(c.get("professionCode"))
                        if code:
                            name_codes.append(code)
                        # Loose token-presence gate — tolerant of name order and multi-token
                        # surnames (the endpoint returns "LAST[ LAST2] FIRST [MIDDLE]").
                        if _name_consistent(first, last, _v(c.get("name"))):
                            gated.append(c)
                            if lic_num and license_numerics_match(lic_num, _v(c.get("licenseNumber"))):
                                st2, detail = await _get(
                                    "byProfessionAndLicenseNumber",
                                    {"professionCode": code, "licenseNumber": _v(c.get("licenseNumber"))})
                                if st2 == 200 and isinstance(detail, dict):
                                    chosen_detail = detail
                                    break

                # ── STEP 2: license-first sweep (exact) ──────────────────────
                # Primary matcher for common/married/reordered/hyphenated names whose
                # holder falls outside the name endpoint's 10-row cap. Sweeps profession
                # codes for the exact license number, in priority order:
                #   provider_type codes (if ever passed) → codes seen in the name search
                #   → the board's configured NY professions → every remaining profession.
                # Only a NAME-CONSISTENT hit is accepted: a license number can exist under
                # several professions for DIFFERENT people, and the input may pair a person
                # with the wrong number (e.g. Nicole Pesce listed with 220621, which actually
                # belongs to physician David Robbins). Returning a name-mismatched record
                # would risk a false "license match / name differs → verified" downstream, so
                # if no candidate's name matches, we return nothing (honest no-record).
                if chosen_detail is None and lic_num:
                    all_codes = await _all_profession_codes(base, _get)
                    order = _ordered_codes(config, query, name_codes, all_codes)
                    log.info("[%s] license-sweep lic=%s over %d codes",
                             source_id, lic_num, len(order))
                    for lic in _normalize_license(lic_num):
                        for code in order:
                            st, detail = await _get(
                                "byProfessionAndLicenseNumber",
                                {"professionCode": code, "licenseNumber": lic})
                            if (st == 200 and isinstance(detail, dict)
                                    and _name_consistent(first, last, _v(detail.get("name")))):
                                chosen_detail = detail
                                break
                        if chosen_detail is not None:
                            break

                # ── STEP 3: name-only fallback — ONLY when no license was given ──
                # When a license IS provided but no name-consistent record carries it,
                # returning a same-name person with a DIFFERENT license would be a false
                # match (the input license is wrong or not in NY). So the name-only pick
                # is used solely for name-based queries with no license to verify.
                if chosen_detail is None and not lic_num and gated:
                    pick = gated[0]
                    st3, detail = await _get(
                        "byProfessionAndLicenseNumber",
                        {"professionCode": _v(pick.get("professionCode")),
                         "licenseNumber": _v(pick.get("licenseNumber"))})
                    if st3 == 200 and isinstance(detail, dict):
                        chosen_detail = detail

                if chosen_detail is not None:
                    src = f"{base}/byProfessionAndLicenseNumber"
                    records.append(_record_from_detail(chosen_detail, config, src, query))
    except Exception as exc:
        log.error("[%s] NY_CREDENTIALS fetch failed: %s", source_id, exc)
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, str(exc))
        return []

    # Persist raw API traffic as evidence (no page/screenshot for a pure-API board).
    want = config.evidence.capture_on or []
    if evidence and ("search_results" in want or "error" in want):
        try:
            import json as _json
            ql = _query_label(query) if query is not None else ""
            ev_dir = resolve_evidence_path(source_id, run_id, state=config.identity.state,
                                           query_label=ql)
            ev_dir.mkdir(parents=True, exist_ok=True)
            stem = "_".join(filter(None, [source_id, ql, "api"]))
            with open(ev_dir / f"{stem}.json", "w", encoding="utf-8") as f:
                _json.dump(evidence, f, indent=2, default=str)
        except Exception as e:
            log.debug("[%s] evidence write skipped: %s", source_id, e)

    log.info("[%s] NY_CREDENTIALS returning %d record(s)", source_id, len(records))
    await _emit_event(db, run_id, source_id, "complete", "success", t0, len(records))
    if db and records:
        await upsert_to_db(db, records)
    return records
