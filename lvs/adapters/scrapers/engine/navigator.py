"""Config-driven navigation: page load, form fill, search, results wait."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional
from urllib.parse import quote as _urlencode

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from .models import COMBO_MODES, SearchConfig, SearchMode, SearchQuery, SiteConfig

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

async def navigate_to_search(page: Page, config: SiteConfig) -> None:
    log.info("[%s] Navigating to %s", config.identity.source_id, config.identity.base_url)
    await page.goto(config.identity.base_url)
    await page.wait_for_load_state("domcontentloaded")
    # SPA archetypes need extra time for JS framework to render the search form
    if config.identity.archetype in ("thentia_cloud", "ag_grid_spa"):
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        await asyncio.sleep(3)
    else:
        await asyncio.sleep(2)


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

    for sel in selectors:
        try:
            await page.wait_for_selector(sel, state="visible", timeout=5000)
            loc = page.locator(sel).first
            if config.form.use_keyboard_type:
                await loc.click()
                await page.keyboard.press("Control+a")
                await page.keyboard.press("Delete")
                await page.keyboard.type(query.query, delay=30)
            else:
                await loc.clear()
                await loc.fill(query.query)
            log.info("Filled search input '%s' with '%s'", sel, query.query)
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

    Supports: {q} = full query string, {first}/{last} = tokens before/after last space
    (or explicit fields when populated), {license} = license_number, {type} = license_type,
    {provider} = provider_type. Explicit SearchQuery fields take precedence over
    `query.query` token splitting.
    """
    if query.first_name is not None or query.last_name is not None:
        first = query.first_name or ""
        last = query.last_name or ""
    else:
        parts = query.query.rsplit(" ", 1)
        first = parts[0] if len(parts) > 1 else ""
        last = parts[-1]
    license_val = query.license_number if query.license_number is not None else query.query
    return (
        template.replace("{q}", query.query)
        .replace("{first}", first)
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
      - one of the constituent base modes is missing from the config

    Conventions for which constituent base mode supplies the primary input:
      - license_and_*  →  license_number primary
      - first_and_last  →  last_name primary (matches FL_MQA precedent)
    """
    if query.mode not in COMBO_MODES:
        return None
    # If the YAML declares the combo mode explicitly, defer to it.
    if any(m.mode == query.mode for m in config.search.modes):
        return None

    by_name = {m.mode: m for m in config.search.modes}
    needs_license = query.mode.startswith("license")
    needs_first = "first" in query.mode
    needs_last = "last" in query.mode

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
    if needs_license:
        primary_name = "license_number"
    else:
        primary_name = "last_name"
    primary = by_name[primary_name]

    extra_inputs: dict[str, str] = dict(primary.extra_inputs or {})
    for name in required:
        if name == primary_name:
            continue
        secondary = by_name[name]
        var = {"first_name": "{first}", "last_name": "{last}", "license_number": "{license}"}[name]
        extra_inputs[secondary.input_selector] = var

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
    # first_and_last → last_name primary
    if mode_name == "first_and_last":
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
    for m in config.search.modes:
        has_anchor = bool(m.input_selector) or bool(m.dropdown_value)
        if has_anchor:
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

    # Synthesize combo capabilities from the single-field set.
    if {"license_number", "last_name"} <= caps:
        caps.add("license_and_last")
    if {"license_number", "first_name"} <= caps:
        caps.add("license_and_first")
    if {"first_name", "last_name"} <= caps:
        caps.add("first_and_last")
    if {"license_number", "first_name", "last_name"} <= caps:
        caps.add("license_first_last")

    return caps


async def fill_extra_inputs(page: Page, mode_cfg, query: SearchQuery) -> None:
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

    for sel, option_template in (mode_cfg.extra_selects or {}).items():
        option = _resolve_template(option_template, query) if "{" in option_template else option_template
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


async def apply_orthogonal_filters(page: Page, config: SiteConfig, query: SearchQuery) -> None:
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

async def wait_for_results(page: Page, config: SearchConfig) -> bool:
    """Wait for results to appear. Returns True if results found, False if no-results."""
    rw = config.results_wait
    timeout = rw.timeout_ms

    try:
        if rw.strategy == "element_visible" and rw.selector:
            await page.wait_for_selector(rw.selector, timeout=timeout)
        elif rw.strategy == "network_idle":
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
                await asyncio.sleep(poll)
        else:
            await asyncio.sleep(2)
    except PlaywrightTimeout:
        log.warning("Timed out waiting for results (%s)", rw.strategy)

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

async def fill_search_form(page: Page, config: SiteConfig, query: SearchQuery) -> bool:
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
        return await wait_for_results(page, config.search)

    if config.search.pre_search_click:
        try:
            await page.wait_for_selector(
                config.search.pre_search_click, state="visible", timeout=8000
            )
            await page.locator(config.search.pre_search_click).first.click()
            log.info("pre_search_click: clicked '%s'", config.search.pre_search_click)
            # Wait for any resulting navigation/re-render; fall back to short sleep if idle
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                await asyncio.sleep(1.5)
        except Exception as e:
            log.warning("pre_search_click '%s' failed: %s", config.search.pre_search_click, e)

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
    if primary_value is None and query.mode == "first_and_last":
        # No explicit last_name field; derive from token split of the query string.
        parts = query.query.rsplit(" ", 1)
        if len(parts) > 1:
            primary_value = parts[-1]
    if primary_value is not None:
        effective_query = query.model_copy(update={"query": primary_value})

    await set_search_by(page, config.search, effective_query)
    await asyncio.sleep(1.5)  # SPA re-renders after dropdown change
    filled = await fill_search_input(page, config.search, effective_query)
    if not filled:
        raise RuntimeError(f"[{config.identity.source_id}] Could not fill search input for query '{query.query}'")
    mode_cfg = next((m for m in config.search.modes if m.mode == query.mode), None)
    # Use the original `query` (not effective_query) so {first}/{last}/{license}
    # substitutions in extra_inputs see the full structured fields.
    await fill_extra_inputs(page, mode_cfg, query)
    # Apply orthogonal license_type / provider_type filters from SiteIdentity.
    await apply_orthogonal_filters(page, config, query)
    if config.search.submit_via_enter:
        await page.keyboard.press("Enter")
        log.info("submit_via_enter: pressed Enter to submit form")
        clicked = True
    else:
        clicked = await click_search_button(page, config.search, query)
    if not clicked:
        raise RuntimeError(f"[{config.identity.source_id}] Could not click search button")
    has_results = await wait_for_results(page, config.search)
    # Post-search click: some boards show results in a list/card view first and
    # require clicking a toggle (e.g. grid radio button) to switch to table view.
    # 1s settle delay lets the ASP.NET UpdatePanel finish DOM mutation after networkidle.
    if has_results and config.search.post_search_click:
        await asyncio.sleep(1.0)
        try:
            await page.wait_for_selector(
                config.search.post_search_click, state="visible", timeout=8000
            )
            await page.locator(config.search.post_search_click).first.click()
            log.info("post_search_click: clicked '%s'", config.search.post_search_click)
            await asyncio.sleep(2.0)
        except Exception as e:
            log.warning("post_search_click '%s' failed: %s", config.search.post_search_click, e)
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
