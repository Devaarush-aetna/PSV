"""Shared helpers used by multiple archetype modules."""
from __future__ import annotations

import asyncio
import io
import logging
import re
import time

from engine.evidence import capture_evidence
from engine.extractor import extract_detail
from engine.ai_fallback import extract_with_ai, should_use_ai_fallback
from engine.models import SiteConfig, TelemetryEvent
from engine.telemetry import log_scrape_event

log = logging.getLogger(__name__)


async def _emit_event(
    db, run_id, source_id, stage, status, t0, count,
    error=None, partial_result=False, warnings=None,
):
    if db is None:
        return
    event = TelemetryEvent(
        run_id=run_id,
        source_id=source_id,
        stage=stage,
        status=status,
        duration_ms=int((time.time() - t0) * 1000),
        record_count=count,
        error_msg=error,
        partial_result=partial_result,
        warnings=warnings or [],
    )
    await log_scrape_event(db, event)


async def _try_out_of_state_tab(page, config: SiteConfig, raw: dict) -> None:
    """For FL_MQA T-prefix licenses: if expiry is missing from the main detail page,
    click the 'Out of State' secondary tab and extract expiry + originating state name.
    Mutates `raw` in place; no-ops on any error."""
    oos = config.detail.out_of_state_tab
    if not oos.enabled:
        return
    license_num = (raw.get("license_number") or "").strip()
    if not license_num.upper().startswith(oos.trigger_license_prefix.upper()):
        return
    if raw.get("expiration_date"):
        return
    try:
        tab_loc = page.locator(oos.tab_selector)
        visible = False
        count = await tab_loc.count()
        for i in range(count):
            try:
                if await tab_loc.nth(i).is_visible(timeout=3000):
                    await tab_loc.nth(i).evaluate("el => el.removeAttribute('target')")
                    visible = True
                    await tab_loc.nth(i).click()
                    await asyncio.sleep(1)  # allow JS tab switch to update DOM
                    break
            except Exception:
                continue
        if not visible:
            log.debug("[%s] Out of State tab not found for license '%s'",
                      config.identity.source_id, license_num)
            return
        # Wait for tab content to load (networkidle handles both AJAX tabs and navigation tabs)
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            await asyncio.sleep(2)
        # Extract using the same detail strategies + field_map (returns mapped field names)
        tab_raw = await extract_detail(page, config.detail)
        # extract_detail applies field_map, so keys are mapped names (e.g. "expiration_date"),
        # not the raw HTML labels.  Look up by mapped name directly.
        exp_val = tab_raw.get("expiration_date")
        state_val = tab_raw.get("state_code") or tab_raw.get("out_of_state_state")
        if exp_val:
            raw["expiration_date"] = exp_val
            raw["_out_of_state_expiry_used"] = True
        if state_val:
            raw["out_of_state_state"] = str(state_val).strip()
        log.info("[%s] Out of State tab: expiry=%s state=%s for license '%s'",
                 config.identity.source_id, exp_val, state_val, license_num)
    except Exception as exc:
        log.debug("[%s] Out of State tab fetch failed for '%s': %s",
                  config.identity.source_id, license_num, exc)


async def _scrape_pdf_detail(page, href: str, config: SiteConfig) -> dict:
    """Download a PDF linked from the results table and extract key-value fields via PyMuPDF."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        log.warning("PyMuPDF not installed — cannot extract PDF detail for '%s'", href)
        return {}

    try:
        from urllib.parse import urljoin
        abs_url = href if href.startswith("http") else urljoin(page.url, href)
        resp = await page.request.get(abs_url)
        if resp.status != 200:
            log.warning("PDF download failed for '%s': HTTP %d", href, resp.status)
            return {}
        pdf_bytes = await resp.body()
    except Exception as exc:
        log.warning("PDF download failed for '%s': %s", href, exc)
        return {}

    try:
        doc = fitz.open(stream=io.BytesIO(pdf_bytes), filetype="pdf")
        # Primary: default text extraction (preserves inline layout order)
        text = "\n".join(p.get_text() for p in doc)
        # Fallback: block-sort mode reconstructs reading order for multi-column
        # certificate PDFs where labels and values land on separate content streams.
        text_blocks = "\n".join(
            b[4] for p in doc for b in sorted(p.get_text("blocks"), key=lambda b: (round(b[1], 1), b[0]))
            if isinstance(b[4], str)
        )
    except Exception as exc:
        log.warning("PDF parse failed for '%s': %s", href, exc)
        return {}

    raw: dict = {}
    for candidate_text in (text, text_blocks):
        for line in candidate_text.splitlines():
            line = line.strip()
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip().rstrip(".")
                value = value.strip()
                if key and value and key not in raw:
                    raw[key] = value

    # Supplement with regex patterns for date fields not captured as key:value lines.
    # Run against both text variants so label+date on adjacent lines are caught.
    # Patterns cover two layouts:
    #   (a) label then date on same or next line  — e.g. "Expires\n3/1/2028"
    #   (b) date then label on next line          — e.g. "3/1/2028\nExpires"
    #       (NCASPPB/LearningBuilder certificates list the date row above the label row)
    combined_text = text + "\n" + text_blocks
    _DATE_RE = r"\d{1,2}/\d{1,2}/\d{4}"
    for patterns, field in [
        (
            [
                r"[Ee]xpir(?:ation|es?)[\s\S]{0,20}?(" + _DATE_RE + r")",
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
                r"[Ii]ssue[d]?[\s\S]{0,15}?(" + _DATE_RE + r")",
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

    log.info("PDF detail extracted %d field(s) from '%s'", len(raw), abs_url)
    return raw


async def _scrape_one_detail(page, config: SiteConfig, run_id: str, db) -> dict:
    evidence = await capture_evidence(
        page, config.evidence, stage="detail_page", run_id=run_id,
        source_id=config.identity.source_id, state=config.identity.state,
    )
    raw = await extract_detail(page, config.detail)

    if should_use_ai_fallback(raw):
        html = await page.content()
        ai_data = await extract_with_ai(
            html=html,
            field_map=config.detail.field_map,
            source_id=config.identity.source_id,
            run_id=run_id,
            db=db,
        )
        raw.update(ai_data)

    # FL T-license secondary check: fetch expiry + originating state from Out of State tab
    # when the main detail page has no expiration date.
    await _try_out_of_state_tab(page, config, raw)

    raw.update(evidence)
    return raw


async def _navigate_back(page, config: SiteConfig) -> None:
    nav = config.detail.back_navigation
    if nav.strategy == "browser_back":
        await page.go_back()
    elif nav.strategy == "breadcrumb_click" and nav.selector:
        try:
            close_btn = page.locator(nav.selector)
            count = await close_btn.count()
            clicked = False
            for i in range(count):
                btn = close_btn.nth(i)
                try:
                    if await btn.is_visible():
                        await btn.click(timeout=5000)
                        clicked = True
                        break
                except Exception:
                    continue
            if clicked:
                log.info("breadcrumb_click: clicked '%s'", nav.selector)
            elif count > 0:
                log.warning("breadcrumb_click: no visible instance of '%s' found — skipping back", nav.selector)
            else:
                log.warning("breadcrumb_click: selector '%s' not found — skipping back", nav.selector)
        except Exception as e:
            log.warning("breadcrumb_click failed: %s", e)
    elif nav.strategy == "url_navigate" and nav.url_fragment:
        base = config.identity.base_url
        target = base.rstrip("/").rsplit("/", 1)[0] + "/" + nav.url_fragment.lstrip("/")
        await page.goto(target)
    elif nav.strategy == "escape_key":
        await page.keyboard.press("Escape")
    else:
        await page.go_back()

    if nav.wait_after_ms > 0:
        await asyncio.sleep(nav.wait_after_ms / 1000.0)


async def _wait_for_detail_content(page, config: SiteConfig) -> None:
    """Wait for detail page content to render."""
    dw = config.detail.wait
    if dw.strategy == "element_visible":
        wait_sels = ([dw.selector] if dw.selector else []) + dw.fallback_selectors
        for sel in wait_sels:
            try:
                await page.wait_for_selector(sel, state="visible", timeout=dw.timeout_ms)
                log.debug("Detail content visible via selector '%s'", sel)
                if config.identity.archetype in ("ag_grid_spa", "thentia_cloud"):
                    try:
                        await page.wait_for_function(
                            """() => {
                                const rows = document.querySelectorAll(
                                    'div.detail-container tr[ng-repeat], div.detail-container tr[ng-repeat-end]'
                                );
                                if (rows.length === 0) return false;
                                const cell = rows[0].querySelector('td.ng-binding');
                                return cell && cell.textContent.trim().length > 0;
                            }""",
                            timeout=10000,
                        )
                    except Exception:
                        await asyncio.sleep(3)
                return
            except Exception:
                continue
        return

    archetype = config.identity.archetype
    if archetype in ("thentia_cloud", "ag_grid_spa"):
        try:
            await page.wait_for_function(
                """() => {
                    const dts = document.querySelectorAll('dl dt, dl dt.ng-binding');
                    if (dts.length > 0 && Array.from(dts).some(dt => dt.textContent.trim().length > 1)) {
                        return true;
                    }
                    const h = document.querySelector('h1.ng-binding, h2.ng-binding, h3.ng-binding');
                    return h && h.textContent.trim().length > 1 && !h.textContent.includes('SHARED_LABEL');
                }""",
                timeout=35000,
            )
        except Exception:
            await asyncio.sleep(10)
            try:
                await page.wait_for_function(
                    """() => {
                        const dts = document.querySelectorAll('dl dt');
                        if (dts.length > 0 && Array.from(dts).some(dt => dt.textContent.trim().length > 1)) return true;
                        const h = document.querySelector('h1.ng-binding, h2.ng-binding, h3.ng-binding');
                        return h && h.textContent.trim().length > 1 && !h.textContent.includes('SHARED_LABEL');
                    }""",
                    timeout=15000,
                )
            except Exception:
                pass
    else:
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        for sel in config.detail.wait.fallback_selectors:
            try:
                await page.wait_for_selector(sel, timeout=5000)
                break
            except Exception:
                continue


async def _set_iteration_value(page, mi_cfg, value: str) -> None:
    """Set an iteration value (select option / input fill / URL navigate)."""
    if mi_cfg.field_kind == "select":
        try:
            await page.locator(mi_cfg.field_selector).first.select_option(value=value, timeout=5000)
        except Exception:
            try:
                await page.locator(mi_cfg.field_selector).first.select_option(label=value, timeout=5000)
            except Exception as e:
                log.warning("multi_iteration: select '%s' failed: %s", value, e)
    elif mi_cfg.field_kind == "input":
        loc = page.locator(mi_cfg.field_selector).first
        await loc.clear()
        await loc.fill(value)
    # url_replace handled at navigation time
