"""Config-driven navigation: page load, form fill, search, results wait."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional
from urllib.parse import quote as _urlencode

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from .models import (
    BoardUnavailableError, COMBO_MODES, SearchConfig, SearchMode, SearchQuery, SiteConfig,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

async def navigate_to_search(page: Page, config: SiteConfig) -> None:
    src = config.identity.source_id
    log.info("[%s] Navigating to %s", src, config.identity.base_url)
    # Initial navigation is the canary for a down board. A connection timeout /
    # TLS drop raises here; an HTTP 5xx returns a Response with a >=500 status
    # (page.goto does NOT raise on server errors). Both mean the board is
    # unavailable — surface a distinct error the ladder can turn into a Skip,
    # rather than letting the scraper parse an error page as phantom records.
    try:
        resp = await page.goto(config.identity.base_url)
    except PlaywrightTimeout as exc:
        raise BoardUnavailableError(
            f"{src}: navigation timeout to {config.identity.base_url}"
        ) from exc
    except Exception as exc:
        # net::ERR_* (connection refused/reset/timed-out, name-not-resolved, TLS)
        msg = str(exc)
        if "net::ERR" in msg or "NS_ERROR" in msg or "ERR_" in msg:
            raise BoardUnavailableError(f"{src}: {msg[:200]}") from exc
        raise
    if resp is not None and resp.status >= 500:
        raise BoardUnavailableError(
            f"{src}: HTTP {resp.status} from {config.identity.base_url}"
        )
    await page.wait_for_load_state("domcontentloaded")


# ---------------------------------------------------------------------------
# Search-By dropdown
# ---------------------------------------------------------------------------

async def set_search_by(page: Page, config: SearchConfig, query: SearchQuery) -> bool:
    mode_cfg = next((m for m in config.modes if m.mode == query.mode), None)
    if not mode_cfg or not mode_cfg.dropdown_value:
        return True  # no dropdown needed

    dropdown_value = mode_cfg.dropdown_value
    form = config.form
    strategy = form.search_by_dropdown.strategy

    if strategy == "none":
        return True

    if strategy == "select":
        selector = form.search_by_dropdown.selector or "select"
        try:
            await page.locator(selector).first.select_option(label=dropdown_value, timeout=5000)
            log.info("Set <select> dropdown to '%s'", dropdown_value)
            await asyncio.sleep(0.5)
            return True
        except Exception as e:
            log.warning("select strategy failed (trying custom dropdown): %s", e)

    if strategy in ("select", "custom_dropdown"):
        # Custom dropdown: click each candidate trigger, check for desired item,
        # press Escape to close if wrong dropdown opened, try the next trigger.
        try:
            triggers = page.locator(
                "[class*='dropdown'],[class*='select'],[class*='chosen'],[class*='ng-select']"
            )
            count = await triggers.count()
            for i in range(count):
                trigger = triggers.nth(i)
                try:
                    tag = await trigger.evaluate("el => el.tagName.toLowerCase()")
                    if tag == "select":
                        continue
                    if not await trigger.is_visible():
                        continue
                    await trigger.click()
                    await asyncio.sleep(0.5)
                    # Check if the desired option appeared. Item selectors cover
                    # ARIA, Kendo, Bootstrap, Angular ng-select, and generic dropdowns.
                    items = page.locator(
                        f"li[role='option']:has-text('{dropdown_value}'),"
                        f"li.k-item:has-text('{dropdown_value}'),"
                        f"[role='option']:has-text('{dropdown_value}'),"
                        f"[class*='dropdown-item']:has-text('{dropdown_value}'),"
                        f"[class*='ng-option']:has-text('{dropdown_value}')"
                    )
                    if await items.count() > 0:
                        await items.first.click()
                        log.info("Clicked custom dropdown item '%s'", dropdown_value)
                        await asyncio.sleep(0.4)
                        return True
                    # Wrong dropdown opened — close it before trying the next
                    await page.keyboard.press("Escape")
                    await asyncio.sleep(0.2)
                except Exception:
                    try:
                        await page.keyboard.press("Escape")
                    except Exception:
                        pass
                    continue
        except Exception as e:
            log.warning("custom_dropdown strategy failed: %s", e)

    if strategy == "slds_combobox":
        # Salesforce Lightning Design System (SLDS) combobox: click the trigger button
        # to open the listbox, then click the matching [role="option"] item.
        trigger_sel = form.search_by_dropdown.selector or "button.slds-combobox__input"
        try:
            await page.locator(trigger_sel).first.click()
            await asyncio.sleep(0.5)
            opt = page.locator(f"[role='option']:has-text('{dropdown_value}')")
            if await opt.count() > 0:
                await opt.first.click()
                log.info("SLDS combobox: selected '%s'", dropdown_value)
                await asyncio.sleep(0.3)
                return True
            await page.keyboard.press("Escape")
        except Exception as e:
            log.warning("slds_combobox strategy failed: %s", e)

    if strategy == "radio":
        try:
            radio = page.locator(f"input[type='radio'][value*='{dropdown_value}']")
            if await radio.count() > 0:
                await radio.first.click()
                log.info("Clicked radio '%s'", dropdown_value)
                return True
        except Exception as e:
            log.warning("radio strategy failed: %s", e)

    log.warning("Could not set Search By to '%s'", dropdown_value)
    return False


# ---------------------------------------------------------------------------
# Search input fill
# ---------------------------------------------------------------------------

async def fill_search_input(page: Page, config: SearchConfig, query: SearchQuery) -> bool:
    # Per-mode override takes precedence over global form config
    mode_cfg = next((m for m in config.modes if m.mode == query.mode), None)
    if mode_cfg and mode_cfg.input_selector:
        selectors = [mode_cfg.input_selector]
    else:
        form = config.form
        selectors = [form.search_input.selector] + form.search_input.fallback_selectors

    # Angular Ivy reactive-form: set FormControl value via __ngContext__ LView traversal.
    # Needed when Playwright fill()/keyboard.type() don't update the Angular FormGroup.
    if mode_cfg and mode_cfg.angular_formgroup_key:
        fg_key = mode_cfg.angular_formgroup_key
        result = await page.evaluate("""
        ([sels, key, val]) => {
            let el = null;
            for (const s of sels) {
                try { el = document.querySelector(s.trim()); } catch(e) {}
                if (el) break;
            }
            if (!el) return {error: 'no element'};
            const ctxKey = Object.keys(el).find(k => k.startsWith('__ngContext__'));
            if (!ctxKey) return {error: 'no ngContext'};
            const lview = el[ctxKey];
            let formGroup = null;
            for (let i = 0; i < lview.length; i++) {
                const item = lview[i];
                if (item && typeof item === 'object' &&
                    '_hasOwnPendingAsyncValidator' in item &&
                    '_onCollectionChange' in item &&
                    typeof item.setValue === 'function') {
                    formGroup = item;
                    break;
                }
            }
            if (!formGroup) return {error: 'no formgroup'};
            if (!formGroup.controls) return {error: 'no controls'};
            const ctrl = formGroup.controls[key];
            if (!ctrl) return {error: 'no ctrl:' + key, available: Object.keys(formGroup.controls).join(',')};
            ctrl.setValue(val);
            ctrl.markAsDirty();
            ctrl.markAsTouched();
            formGroup.updateValueAndValidity({onlySelf: false, emitEvent: true});
            return {ok: true, v: ctrl.value};
        }
        """, [selectors, fg_key, query.query])
        if isinstance(result, dict) and result.get('ok'):
            log.info("Angular FormGroup: controls.%s = %r", fg_key, query.query)
            return True
        log.warning("Angular FormGroup set failed (%s), falling back to DOM fill", result)

    # Apply per-mode query_template if configured (e.g. "{last}, {first}" for boards
    # that require "LastName, FirstName" format in a single search field).
    mode_cfg_for_template = next((m for m in config.modes if m.mode == query.mode), None)
    fill_value = (
        _resolve_template(mode_cfg_for_template.query_template, query)
        if mode_cfg_for_template and mode_cfg_for_template.query_template
        else query.query
    )

    for sel in selectors:
        try:
            await page.wait_for_selector(sel, state="visible", timeout=5000)
            loc = page.locator(sel).first
            if config.form.use_keyboard_type:
                await loc.click()
                await page.keyboard.press("Control+a")
                await page.keyboard.press("Delete")
                await page.keyboard.type(fill_value, delay=30)
            else:
                await loc.clear()
                await loc.fill(fill_value)
            log.info("Filled search input '%s' with '%r'", sel, fill_value)
            return True
        except Exception:
            continue

    # Last resort: first visible enabled text input
    try:
        inputs = page.locator("input[type='text'], input[type='search'], input:not([type])")
        count = await inputs.count()
        for i in range(count):
            inp = inputs.nth(i)
            try:
                visible = await inp.is_visible()
                enabled = await inp.is_enabled()
            except Exception:
                continue
            if visible and enabled:
                await inp.clear()
                await inp.fill(query.query)
                log.info("Filled first visible text input with '%s'", query.query)
                return True
    except Exception as e:
        log.warning("Fallback text input fill failed: %s", e)

    log.error("Could not find search text input")
    return False


def _resolve_template(template: str, query: SearchQuery) -> str:
    """Substitute the engine's template variables.

    Supported tokens:
      {q}          full query string (auto-joined)
      {first}      first_name (or tokens before last space if no explicit field)
      {middle}     middle_name (empty string when not set)
      {last}       last_name (or last space-separated token)
      {first_full} first_name + " " + middle_name concatenated (for boards that put
                   first and middle into a single input field)
      {license}    license_number
      {type}       license_type
      {provider}   provider_type

    Explicit SearchQuery fields take precedence over `query.query` token splitting.
    """
    if query.first_name is not None or query.last_name is not None:
        first = query.first_name or ""
        last = query.last_name or ""
    else:
        parts = query.query.rsplit(" ", 1)
        first = parts[0] if len(parts) > 1 else ""
        last = parts[-1]
    middle = query.middle_name or ""
    first_full = (first + " " + middle).strip() if middle else first
    license_val = query.license_number if query.license_number is not None else query.query
    return (
        template.replace("{q}", query.query)
        .replace("{first_full}", first_full)
        .replace("{first}", first)
        .replace("{middle}", middle)
        .replace("{last}", last)
        .replace("{license}", license_val or "")
        .replace("{type}", query.license_type or "")
        .replace("{provider}", query.provider_type or "")
    )


def synthesize_combo_mode(config: SiteConfig, query: SearchQuery) -> Optional[SearchMode]:
    """Synthesize a combination SearchMode by merging existing single-field modes.

    Returns None when:
      - the mode is not a combo mode
      - the config already declares an explicit mode entry for it (caller uses that one)
      - one of the required constituent base modes is missing from the config

    Primary input conventions:
      - license_*     →  license_number primary
      - first_and_last / first_mid_last  →  last_name primary (matches FL_MQA precedent)

    Middle name handling:
      - *_mid_* modes treat middle_name as OPTIONAL. Synthesis succeeds even when there
        is no `middle_name` base mode in the config. If a `middle_name` mode with an
        input_selector IS declared, the engine wires up {middle} into that field.
        When absent, middle is silently dropped (boards with no dedicated mid field).
    """
    if query.mode not in COMBO_MODES:
        return None
    # If the YAML declares the combo mode explicitly, defer to it.
    if any(m.mode == query.mode for m in config.search.modes):
        return None

    by_name = {m.mode: m for m in config.search.modes}
    needs_license = query.mode.startswith("license")
    needs_first = "first" in query.mode
    needs_middle = "mid" in query.mode   # optional — synthesis doesn't fail without it
    needs_last = "last" in query.mode

    # Required fields — synthesis fails if any required base mode is missing/has no selector.
    required: list[str] = []
    if needs_license:
        required.append("license_number")
    if needs_first:
        required.append("first_name")
    if needs_last:
        required.append("last_name")

    for name in required:
        m = by_name.get(name)
        if not m or not m.input_selector:
            log.warning(
                "Cannot synthesize '%s' for [%s]: base mode '%s' missing or has no input_selector",
                query.mode, config.identity.source_id, name,
            )
            return None

    # Pick the primary base mode.
    primary_name = "license_number" if needs_license else "last_name"
    primary = by_name[primary_name]

    extra_inputs: dict[str, str] = dict(primary.extra_inputs or {})
    for name in required:
        if name == primary_name:
            continue
        secondary = by_name[name]
        var = {"first_name": "{first}", "last_name": "{last}", "license_number": "{license}"}[name]
        extra_inputs[secondary.input_selector] = var

    # Optional: wire up middle_name field when the config declares it.
    if needs_middle:
        mid_mode = by_name.get("middle_name")
        if mid_mode and mid_mode.input_selector:
            extra_inputs[mid_mode.input_selector] = "{middle}"
            log.info("[%s] Combo '%s': wired middle_name -> '%s'",
                     config.identity.source_id, query.mode, mid_mode.input_selector)
        else:
            log.debug("[%s] Combo '%s': no middle_name mode in config — middle skipped",
                      config.identity.source_id, query.mode)

    extra_selects: dict[str, str] = dict(primary.extra_selects or {})

    synthetic = SearchMode(
        mode=query.mode,
        dropdown_value=primary.dropdown_value,
        input_selector=primary.input_selector,
        button_selector=primary.button_selector,
        extra_inputs=extra_inputs,
        extra_selects=extra_selects,
    )
    log.info(
        "[%s] Synthesized combo mode '%s' (primary=%s, extras=%d)",
        config.identity.source_id, query.mode, primary_name, len(extra_inputs),
    )
    return synthetic


def _primary_value_for_mode(mode_name: str, query: SearchQuery) -> Optional[str]:
    """Return the explicit field value that should be typed into the primary input
    for a synthesized combo mode. None means 'fall back to query.query'.
    """
    if mode_name.startswith("license"):
        return query.license_number
    # first_and_last / first_mid_last → last_name primary
    if mode_name in ("first_and_last", "first_mid_last"):
        return query.last_name
    return None


def check_board_capability(config: SiteConfig, query: SearchQuery) -> tuple[str, Optional[str]]:
    """Check whether this board can satisfy the requested mode.

    Returns:
      ("ok", None)              — board satisfies the mode natively
      ("degrade", fallback)     — board can't do exact combo; caller should run
                                   `fallback` mode with auto-joined query string
      ("reject", reason)        — board has none of the needed fields
    """
    ident = config.identity
    # Explicit override wins.
    if ident.capabilities is not None:
        caps = set(ident.capabilities)
    else:
        caps = _auto_derive_capabilities(config)

    if query.mode in caps:
        return ("ok", None)

    if query.mode in COMBO_MODES:
        # Best-effort: pick the most-specific single-field mode the board does support.
        # license > last > first.
        fallback_priority = ["license_number", "last_name", "first_name", "name", "full_name"]
        for fb in fallback_priority:
            if fb in caps:
                # Special case: if combo includes license but board has no license capability,
                # we can still degrade to a name search. Don't degrade to license-only when
                # the user provided names — that would lose the names.
                return ("degrade", fb)
        return ("reject", f"board has no searchable fields matching mode '{query.mode}'")

    # Single-field mode requested but board doesn't have it.
    if query.mode == "first_name" and "last_name" in caps:
        return ("degrade", "last_name")
    if query.mode == "last_name" and "first_name" in caps:
        return ("degrade", "first_name")

    return ("reject", f"mode '{query.mode}' not supported by [{ident.source_id}] (capabilities: {sorted(caps)})")


def _auto_derive_capabilities(config: SiteConfig) -> set[str]:
    """Auto-derive capability set from non-empty mode selectors / search_columns / etc."""
    caps: set[str] = set()
    archetype = config.identity.archetype

    # Form-based archetypes: a mode is "capable" if it has a non-empty input_selector
    # OR a non-empty dropdown_value (radio/select-driven boards). Boards that share
    # one selector across all modes still count — the mode name controls what's typed.
    has_shared_input = bool(
        config.search.form
        and config.search.form.search_input
        and config.search.form.search_input.selector
        and config.search.form.search_input.selector.strip()
        and config.search.form.search_input.selector.strip() != "input[type='text']"
    )
    per_mode_anchored: set[str] = set()
    for m in config.search.modes:
        has_anchor = bool(m.input_selector) or bool(m.dropdown_value)
        if has_anchor:
            caps.add(m.mode)
            per_mode_anchored.add(m.mode)
    # Boards with a shared form.search_input but no per-mode selectors/dropdowns
    # (e.g. Thentia single-keyword boards) support all declared modes via that input.
    if has_shared_input and not per_mode_anchored:
        for m in config.search.modes:
            caps.add(m.mode)

    # csv_bulk: capability comes from search_columns keys.
    if archetype == "csv_bulk" and config.csv_bulk:
        for k, v in (config.csv_bulk.search_columns or {}).items():
            if v:
                caps.add(k)

    # json_api: capability comes from bodies/params keys.
    if archetype == "json_api" and config.json_api:
        for k in (config.json_api.bodies or {}):
            caps.add(k)
        for k in (config.json_api.params or {}):
            caps.add(k)
        if config.json_api.intercept_form:
            for k in (config.json_api.intercept_form.fills or {}):
                caps.add(k)

    # datatables_jsapi: capability from column_index keys.
    if archetype == "datatables_jsapi" and config.datatables:
        for k, v in (config.datatables.column_index or {}).items():
            caps.add(k)

    # filemaker_webdirect: capability from field_index keys.
    if archetype == "filemaker_webdirect" and config.filemaker:
        for k, v in (config.filemaker.field_index or {}).items():
            caps.add(k)

    # certemy: single live-filter input serves all modes — derive directly from modes list.
    if archetype == "certemy":
        for m in config.search.modes:
            caps.add(m.mode)

    # pdf_bulk: in-memory PDF search supports all declared modes directly.
    if archetype == "pdf_bulk":
        for m in config.search.modes:
            caps.add(m.mode)

    # Synthesize combo capabilities from the single-field set.
    if {"license_number", "last_name"} <= caps:
        caps.add("license_and_last")
    if {"license_number", "first_name"} <= caps:
        caps.add("license_and_first")
    if {"first_name", "last_name"} <= caps:
        caps.add("first_and_last")
        # first_mid_last synthesizes the same way as first_and_last — middle is optional
        # (silently dropped when the board config has no dedicated middle_name field).
        caps.add("first_mid_last")
    if {"license_number", "first_name", "last_name"} <= caps:
        caps.add("license_first_last")
        caps.add("license_first_mid_last")

    return caps


async def fill_extra_inputs(page: Page, mode_cfg, query: SearchQuery, partial_failures: list[str] | None = None) -> None:
    """Fill extra_inputs and extra_selects defined on the mode."""
    if not mode_cfg:
        return

    for sel, template in (mode_cfg.extra_inputs or {}).items():
        value = _resolve_template(template, query)
        try:
            await page.wait_for_selector(sel, state="visible", timeout=5000)
            loc = page.locator(sel).first
            await loc.clear()
            await loc.fill(value)
            log.info("Filled extra input '%s' with '%s'", sel, value)
        except Exception as e:
            log.warning("Extra input fill failed for '%s': %s", sel, e)
            if partial_failures is not None:
                partial_failures.append(f"extra_input '{sel}' fill failed: {e}")

    for sel, option_template in (mode_cfg.extra_selects or {}).items():
        option = _resolve_template(option_template, query) if "{" in option_template else option_template
        if not option:
            # Template resolved to empty string (e.g. {type} with no license_type mapping) —
            # skip rather than attempting to select "" which may lock the dropdown to an invalid state.
            log.debug("Skipping extra_select '%s': template resolved to empty string", sel)
            continue
        try:
            await page.wait_for_selector(sel, state="visible", timeout=5000)
            # Try by label first, then by value
            try:
                await page.locator(sel).first.select_option(label=option, timeout=3000)
            except Exception:
                await page.locator(sel).first.select_option(value=option, timeout=3000)
            log.info("Set extra select '%s' to '%s'", sel, option)
        except Exception as e:
            log.warning("Extra select set failed for '%s': %s", sel, e)
            if partial_failures is not None:
                partial_failures.append(f"extra_select '{sel}' set failed: {e}")


async def apply_orthogonal_filters(page: Page, config: SiteConfig, query: SearchQuery, partial_failures: list[str] | None = None) -> None:
    """Apply license_type and provider_type as side-filter dropdowns when both
    the SearchQuery field and the SiteIdentity selector are populated."""
    ident = config.identity
    pairs = [
        (ident.license_type_selector, query.license_type, "license_type"),
        (ident.provider_type_selector, query.provider_type, "provider_type"),
    ]
    for sel, value, label in pairs:
        if not sel or not value:
            continue
        try:
            await page.wait_for_selector(sel, state="visible", timeout=5000)
            try:
                await page.locator(sel).first.select_option(label=value, timeout=3000)
            except Exception:
                await page.locator(sel).first.select_option(value=value, timeout=3000)
            log.info("Applied %s filter '%s' via '%s'", label, value, sel)
        except Exception as e:
            log.warning("%s filter '%s' on '%s' failed: %s", label, value, sel, e)
            if partial_failures is not None:
                partial_failures.append(f"{label} filter '{value}' on '{sel}' failed: {e}")


# ---------------------------------------------------------------------------
# Search button click
# ---------------------------------------------------------------------------

async def click_search_button(page: Page, config: SearchConfig, query: SearchQuery | None = None) -> bool:
    # Per-mode override takes precedence over global form config
    if query:
        mode_cfg = next((m for m in config.modes if m.mode == query.mode), None)
        if mode_cfg and mode_cfg.button_selector:
            selectors = [mode_cfg.button_selector]
        else:
            form = config.form
            selectors = [form.search_button.selector] + form.search_button.fallback_selectors
    else:
        form = config.form
        selectors = [form.search_button.selector] + form.search_button.fallback_selectors

    # An explicitly empty selector means "no button needed" (e.g. DataTable live search
    # that filters on the input event — pressing Enter would navigate away via the
    # surrounding WordPress form).
    selectors = [s for s in selectors if s and s.strip()]
    if not selectors:
        log.info("No search button configured — board auto-filters on fill (e.g. DataTable)")
        return True

    for sel in selectors:
        try:
            await page.wait_for_selector(sel, state="visible", timeout=3000)
            loc = page.locator(sel).first
            await loc.click()
            log.info("Clicked search button '%s'", sel)
            return True
        except Exception:
            continue

    # Image/icon buttons
    try:
        imgs = page.locator("img")
        count = await imgs.count()
        for i in range(count):
            img = imgs.nth(i)
            src = (await img.get_attribute("src") or "").lower()
            alt = (await img.get_attribute("alt") or "").lower()
            if any(k in src or k in alt for k in ("search", "magnif", "glass")):
                parent = img.locator("..")
                await parent.click()
                log.info("Clicked search image button")
                return True
    except Exception as e:
        log.warning("Image button click failed: %s", e)

    # Button by text
    try:
        buttons = page.locator("button")
        count = await buttons.count()
        for i in range(count):
            btn = buttons.nth(i)
            text = (await btn.inner_text()).lower()
            if "search" in text and await btn.is_visible():
                await btn.click()
                log.info("Clicked button by text: '%s'", text.strip())
                return True
    except Exception as e:
        log.warning("Button-by-text click failed: %s", e)

    log.error("Search button not found")
    return False


# ---------------------------------------------------------------------------
# Results wait + no-results check
# ---------------------------------------------------------------------------

async def wait_for_results(page: Page, config: SearchConfig, partial_failures: list[str] | None = None) -> bool:
    """Wait for results to appear. Returns True if results found, False if no-results."""
    rw = config.results_wait
    timeout = rw.timeout_ms

    if rw.strategy == "element_visible" and rw.selector:
        # Poll every 1s and check no-results indicators on each tick so we exit
        # immediately when the board shows its empty-state overlay instead of
        # burning the full timeout on every failed lookup.
        poll_s = 1.0
        initial_delay_s = 2.0
        elapsed_ms = int(initial_delay_s * 1000)
        await asyncio.sleep(initial_delay_s)

        while elapsed_ms < timeout:
            if await is_no_results(page, config):
                log.info("No-results indicator detected early (~%dms elapsed)", elapsed_ms)
                return False
            try:
                if await page.locator(rw.selector).count() > 0:
                    return True
            except Exception:
                pass
            await asyncio.sleep(poll_s)
            elapsed_ms += int(poll_s * 1000)

        log.warning("Timed out waiting for results (strategy=%s, timeout=%dms)", rw.strategy, timeout)
        if partial_failures is not None:
            partial_failures.append(
                f"wait_for_results timed out (strategy={rw.strategy}, timeout={timeout}ms)"
            )
        return not await is_no_results(page, config)

    try:
        if rw.strategy == "network_idle":
            await page.wait_for_load_state("networkidle", timeout=timeout)
        elif rw.strategy == "url_change":
            await page.wait_for_function(
                "url => window.location.href !== url",
                arg=await page.evaluate("window.location.href"),
                timeout=timeout,
            )
        elif rw.strategy == "ajax_row_count":
            # Poll the row selector until row_count >= min_rows AND the count
            # has been stable for `stable_ticks` consecutive polls. Designed for
            # AJAX result panels (e.g. NC_MENTAL_HEALTH #btnAJAX → #MultiResultsList)
            # where the table populates after the initial network_idle fires.
            sel = rw.selector or "table tbody tr"
            poll = max(50, rw.poll_interval_ms) / 1000.0
            max_iters = max(1, int(timeout / 1000 / poll))
            prev = -1
            stable = 0
            initial_url = page.url
            for _ in range(max_iters):
                try:
                    n = await page.locator(sel).count()
                except Exception:
                    n = -1
                if n >= rw.min_rows and n == prev:
                    stable += 1
                    if stable >= rw.stable_ticks:
                        break
                else:
                    stable = 0
                prev = n
                # Early exit: board navigated directly to a detail page (e.g. single
                # license-number match → result.aspx) rather than populating an AJAX
                # table. Avoids burning the full timeout waiting for a table that will
                # never appear. Only exit if no no-results indicator is present.
                try:
                    if page.url != initial_url and not await is_no_results(page, config):
                        break
                except Exception:
                    pass
                await asyncio.sleep(poll)
        elif rw.strategy == "delay":
            await asyncio.sleep(rw.timeout_ms / 1000.0)
        elif config.iframe_search_selector:
            try:
                await page.wait_for_load_state("networkidle", timeout=25000)
            except Exception as e:
                log.debug("iframe board networkidle timeout (non-fatal): %s", e)
            await asyncio.sleep(2)
        else:
            await asyncio.sleep(2)
    except PlaywrightTimeout:
        log.warning("Timed out waiting for results (%s)", rw.strategy)
        if partial_failures is not None:
            partial_failures.append(
                f"wait_for_results timed out (strategy={rw.strategy}, timeout={timeout}ms)"
            )

    return not await is_no_results(page, config)


async def is_no_results(page: Page, config: SearchConfig) -> bool:
    try:
        content = (await page.content()).lower()
        for indicator in config.results_wait.no_results_indicators:
            if indicator.lower() in content:
                log.info("No-results indicator found: '%s'", indicator)
                return True
    except Exception as e:
        log.warning("is_no_results check failed: %s", e)
    return False


# ---------------------------------------------------------------------------
# Full search flow
# ---------------------------------------------------------------------------

async def fill_search_form(page: Page, config: SiteConfig, query: SearchQuery, partial_failures: list[str] | None = None) -> bool:
    """Execute the complete form-fill + search click sequence."""
    # Hash-route shortcut: if direct_search_url is configured, navigate to it
    # with the query URL-encoded and skip form interaction entirely.
    # Used by Thentia Cloud boards where the SPA accepts #search/{q}/{offset}/10.
    if config.search.direct_search_url:
        url = (
            config.search.direct_search_url
            .replace("{q}", _urlencode(query.query, safe=""))
            .replace("{offset}", "0")
        )
        log.info("[%s] direct_search_url: %s", config.identity.source_id, url)
        await page.goto(url)
        # Mirror standalone Thentia flow: wait for any table to appear, then
        # networkidle (catches the SPA's search API call), then a settle delay
        # so the result table re-renders with the filtered set.
        try:
            await page.wait_for_selector("table", timeout=20_000)
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        await asyncio.sleep(2.5)
        # Wait for the table to actually contain filtered rows: poll until the
        # row count stabilises or the no-results indicator appears.
        prev_count = -1
        for _ in range(8):
            try:
                count = await page.locator("table tbody tr").count()
            except Exception:
                count = 0
            if count == prev_count:
                break
            prev_count = count
            await asyncio.sleep(0.8)
        return await wait_for_results(page, config.search, partial_failures=partial_failures)

    if config.search.pre_search_click:
        _psc_timeout = config.search.pre_search_click_timeout_ms
        try:
            await page.wait_for_selector(
                config.search.pre_search_click, state="visible", timeout=_psc_timeout
            )
            await page.locator(config.search.pre_search_click).first.click()
            log.info("pre_search_click: clicked '%s'", config.search.pre_search_click)
            # Wait for any resulting navigation/re-render; fall back to short sleep if idle.
            # post_pre_search_click_wait_ms can be raised for CF-protected sites where the
            # JS challenge may still be running when the cookie button is first clicked.
            _post_psc_wait = config.search.post_pre_search_click_wait_ms
            try:
                await page.wait_for_load_state("networkidle", timeout=_post_psc_wait)
            except Exception:
                await asyncio.sleep(1.5)  # already waited _post_psc_wait ms; short buffer then proceed
        except Exception as e:
            log.warning("pre_search_click '%s' failed: %s", config.search.pre_search_click, e)

    # Iframe-embedded search form: switch to the inner frame for form interaction.
    # Results are read from the parent page after the frame's submit triggers navigation.
    search_target = page
    if config.search.iframe_search_selector:
        try:
            frame_el = await page.wait_for_selector(
                config.search.iframe_search_selector, timeout=15000
            )
            if frame_el:
                inner_frame = await frame_el.content_frame()
                if inner_frame:
                    await inner_frame.wait_for_load_state("networkidle", timeout=15000)
                    search_target = inner_frame
                    log.info("[%s] Using iframe for search: %s",
                             config.identity.source_id, config.search.iframe_search_selector)
        except Exception as e:
            log.warning("[%s] iframe_search_selector failed, using main page: %s",
                        config.identity.source_id, e)

    if config.search.search_frame_probe_selector:
        # Scan all page frames (including deeply nested about:blank frames) for the
        # first frame whose DOM contains the probe selector. Needed when the form is
        # embedded several levels deep (e.g. NC_OPTOMETRY: main→gstatic→googleusercontent
        # →about:blank) where content_frame() traversal cannot reach it.
        probe_sel = config.search.search_frame_probe_selector
        await page.wait_for_timeout(3000)  # let nested frames finish loading
        for frm in page.frames:
            try:
                if await frm.locator(probe_sel).count() > 0:
                    search_target = frm
                    log.info("[%s] search_frame_probe found frame: %s",
                             config.identity.source_id, frm.url[:80])
                    break
            except Exception:
                continue
        if search_target is page:
            log.warning("[%s] search_frame_probe_selector '%s' found no matching frame, using main page",
                        config.identity.source_id, probe_sel)

    # Combo mode synthesis: when the requested mode is a recognised combo and the
    # config has no explicit entry for it, synthesise one from the existing single-
    # field modes and inject it into config.search.modes for this call.
    effective_query = query
    synthetic = synthesize_combo_mode(config, query)
    if synthetic is not None:
        config.search.modes.append(synthetic)
    # For any combo mode (synthesized or explicitly declared in YAML), update
    # effective_query.query to the primary field value so the primary input
    # receives only its portion (e.g. last name, not "First Last").
    primary_value = _primary_value_for_mode(query.mode, query)
    if primary_value is None and query.mode in ("first_and_last", "first_mid_last"):
        # No explicit last_name field; derive from token split of the query string.
        parts = query.query.rsplit(" ", 1)
        if len(parts) > 1:
            primary_value = parts[-1]
    if primary_value is not None:
        effective_query = query.model_copy(update={"query": primary_value})

    if not await set_search_by(search_target, config.search, effective_query):
        if partial_failures is not None:
            partial_failures.append(
                f"set_search_by failed for mode '{effective_query.mode}' — results may be from wrong search mode"
            )
    await asyncio.sleep(1.5)  # SPA re-renders after dropdown change
    mode_cfg = next((m for m in config.search.modes if m.mode == query.mode), None)
    # Per-mode pre_click: switch tabs or activate a panel BEFORE filling inputs so
    # that mode-specific fields (e.g. inside a hidden Bootstrap tab) are visible/enabled.
    if mode_cfg and mode_cfg.pre_click:
        try:
            await search_target.wait_for_selector(mode_cfg.pre_click, state="visible", timeout=5000)
            await search_target.locator(mode_cfg.pre_click).first.click()
            await asyncio.sleep(0.6)
            log.info("mode pre_click: clicked '%s'", mode_cfg.pre_click)
        except Exception as e:
            log.warning("mode pre_click '%s' failed: %s", mode_cfg.pre_click, e)
    filled = await fill_search_input(search_target, config.search, effective_query)
    if not filled:
        raise RuntimeError(f"[{config.identity.source_id}] Could not fill search input for query '{query.query}'")
    # Pega Constellation: dispatch change+blur after typing to trigger server-side postValue
    # XHR. Then wait for the search button to become ENABLED (disabled attr removed), which
    # confirms the server-side LicenseLookupVal='true' response has arrived.
    if config.identity.archetype == "pega_constellation":
        mode_sel = next(
            (m.input_selector for m in config.search.modes if m.mode == effective_query.mode),
            config.search.form.search_input.selector if config.search.form else None,
        )
        if mode_sel:
            try:
                await page.evaluate(
                    """(sel) => {
                        var el = document.querySelector(sel);
                        if (el) {
                            el.dispatchEvent(new Event('change', {bubbles: true, cancelable: true}));
                            el.dispatchEvent(new Event('blur', {bubbles: true}));
                        }
                    }""",
                    mode_sel,
                )
            except Exception:
                pass
        # Wait 3s for the server-side postValue XHR chain to complete. The button enables
        # client-side (via keyup expression) faster than the server commits the search value,
        # so networkidle alone is not sufficient — a fixed delay is required.
        await asyncio.sleep(3)
    # Use the original `query` (not effective_query) so {first}/{last}/{license}
    # substitutions in extra_inputs see the full structured fields.
    await fill_extra_inputs(search_target, mode_cfg, query, partial_failures=partial_failures)
    # Combo-mode re-fill guard: some boards (e.g. KY GenSearch ASP.NET) have JS event
    # handlers on secondary fields that clear the primary input when they receive a value.
    # After filling extra_inputs, re-fill the primary field so it is always the last write.
    if query.mode in COMBO_MODES and mode_cfg and mode_cfg.input_selector:
        primary_value = _primary_value_for_mode(query.mode, query)
        if primary_value:
            try:
                loc = search_target.locator(mode_cfg.input_selector).first
                await loc.clear()
                await loc.fill(primary_value)
                log.debug("combo re-fill primary '%s' = '%s'", mode_cfg.input_selector, primary_value)
            except Exception as e:
                log.warning("combo re-fill primary '%s' failed: %s", mode_cfg.input_selector, e)
    # Apply orthogonal license_type / provider_type filters from SiteIdentity.
    await apply_orthogonal_filters(page, config, query, partial_failures=partial_failures)
    if mode_cfg and mode_cfg.submit_js:
        try:
            # Use expect_navigation so Playwright tracks the form-submit redirect.
            # Without this, wait_for_load_state("networkidle") fires on the *current*
            # pre-navigation page before the POST redirect begins.
            async with page.expect_navigation(timeout=30000, wait_until="domcontentloaded"):
                await page.evaluate(mode_cfg.submit_js)
            clicked = True
            log.info("Submitted via JS: %s", mode_cfg.submit_js)
        except Exception as e:
            log.warning("submit_js failed: %s", e)
            clicked = False
    elif config.search.submit_via_enter:
        await search_target.keyboard.press("Enter")
        log.info("submit_via_enter: pressed Enter to submit form")
        clicked = True
    else:
        clicked = await click_search_button(search_target, config.search, query)
    if not clicked:
        raise RuntimeError(f"[{config.identity.source_id}] Could not click search button")
    # After iframe submit, Clarus JS receives a postMessage from the iframe's translate
    # response and then navigates the PARENT page to ?data={...}. Give the JS time to
    # process the message, then wait for the parent page's new navigation to complete.
    if config.search.iframe_search_selector and search_target is not page:
        try:
            await asyncio.sleep(0.8)  # let Clarus JS receive and process translate response
            await page.wait_for_url(lambda u: "?data=" in u, timeout=10000)
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
    has_results = await wait_for_results(page, config.search, partial_failures=partial_failures)
    # Post-search click: some boards show results in a list/card view first and
    # require clicking a toggle (e.g. grid radio button) to switch to table view.
    # Wrap the click inside expect_navigation so Playwright registers a navigation
    # listener BEFORE the click fires — this handles ASP.NET PostBacks triggered via
    # setTimeout(0) where the navigation starts one JS tick after the click, which
    # causes a bare wait_for_load_state("networkidle") to resolve too early (on the
    # pre-PostBack idle state) and leaves page.content() racing the navigation.
    if has_results and config.search.post_search_click:
        await asyncio.sleep(1.0)
        try:
            await page.wait_for_selector(
                config.search.post_search_click, state="visible", timeout=8000
            )
            try:
                async with page.expect_navigation(timeout=10000, wait_until="networkidle"):
                    await page.locator(config.search.post_search_click).first.click()
                log.info("post_search_click: clicked and navigation settled '%s'", config.search.post_search_click)
            except Exception:
                # No full-page navigation triggered (XHR / UpdatePanel partial update).
                # Fall back to networkidle wait then a brief settle delay.
                log.info("post_search_click: no navigation detected, waiting for networkidle '%s'", config.search.post_search_click)
                try:
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    await asyncio.sleep(2.0)
        except Exception as e:
            log.warning("post_search_click '%s' failed: %s", config.search.post_search_click, e)
            if partial_failures is not None:
                partial_failures.append(
                    f"post_search_click '{config.search.post_search_click}' failed: {e}"
                )
    # Post-search expand-all: click every visible match. Used for accordion-grouped
    # results where each profession panel must be expanded to populate its rows.
    if has_results and config.search.post_search_click_all:
        sel = config.search.post_search_click_all
        await asyncio.sleep(0.8)
        try:
            elements = page.locator(sel)
            count = await elements.count()
            log.info("post_search_click_all: '%s' matched %d element(s)", sel, count)
            clicked = 0
            for i in range(count):
                el = elements.nth(i)
                try:
                    if not await el.is_visible():
                        continue
                    await el.scroll_into_view_if_needed(timeout=2000)
                    await el.click(timeout=3000)
                    clicked += 1
                    # Brief pause lets each AJAX panel kick off its load
                    await asyncio.sleep(0.4)
                except Exception:
                    continue
            log.info("post_search_click_all: clicked %d/%d match(es)", clicked, count)
            # Settle so AJAX-loaded rows render before extraction
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                await asyncio.sleep(2.0)
        except Exception as e:
            log.warning("post_search_click_all '%s' failed: %s", sel, e)
    return has_results
