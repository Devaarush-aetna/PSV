"""Multi-strategy extraction cascade: dt/dd, label/sibling, field-class, tables, AG Grid."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from playwright.async_api import Page

from .models import DetailConfig, DetailSection, ResultsConfig
from .post_processors import apply_field_map

log = logging.getLogger(__name__)

_ZW_RE = re.compile("[​‌‍﻿]")


def _clean_value(v: str) -> str:
    """Strip zero-width chars and normalise whitespace.

    Accela (and others) inject non-breaking / unicode spaces (nbsp U+00A0,
    narrow-nbsp U+202F, ideographic space, ...) between words. Python's r'\s'
    matches all of them, so a single collapse converts them to ASCII spaces
    and squeezes runs -- e.g. 'Amy L Rodriguez' -> 'Amy L Rodriguez'.
    """
    return re.sub(r"\s+", " ", _ZW_RE.sub("", v)).strip()

# ---------------------------------------------------------------------------
# Strategy implementations
# ---------------------------------------------------------------------------

async def _extract_heading_name(page: Page) -> dict:
    """Extract licensee name from page heading (Angular ng-binding or generic h1/h2/h6)."""
    result: dict = {}
    try:
        for sel in ("h1.ng-binding", "h2.ng-binding", "h3.ng-binding", "h1", "h2", "h3",
                    "h6[class*='headline6']", ".panel-heading"):
            loc = page.locator(sel)
            count = await loc.count()
            for i in range(min(count, 5)):
                text = (await loc.nth(i).inner_text()).strip()
                # "Profile for NAME" pattern (e.g. KS_KSBHADA).
                # Use find() rather than startswith() — inner_text() concatenates child
                # <a> link text before the text node, so the phrase may not be at index 0.
                _pi = text.lower().find("profile for ")
                if _pi != -1:
                    name = text[_pi + len("profile for "):]
                    if name:
                        result["Name"] = name
                        return result
                words = text.split()
                if 1 < len(words) <= 7 and not any(
                    kw in text.lower()
                    for kw in ("board", "license", "nevada", "state", "search", "portal", "register",
                               "result", "detail", "verification", "information", "occupational",
                               "health", "credentialing", "optometry", "pharmacy", "dental", "profile",
                               "directory", "psycholog", "registry", "lookup", "definition",
                               "regulatory", "permitting", "regulation")
                ):
                    # Strip "Name - Credential" pattern (e.g. "Katlyn Crisp - Permanent AUD")
                    # so suffix-stripping in split_full_name doesn't corrupt the last name.
                    name_text = text.split(" - ")[0].strip() if " - " in text else text
                    result["Name"] = name_text
                    return result
    except Exception as e:
        log.debug("heading_name extraction failed: %s", e)
    return result


async def _extract_dt_dd(page: Page) -> dict:
    result: dict = {}
    try:
        dts = page.locator("dt")
        count = await dts.count()
        for i in range(count):
            dt = dts.nth(i)
            label = (await dt.inner_text()).strip().rstrip(":")
            if not label:
                continue
            dd = dt.locator("xpath=following-sibling::dd[1]")
            if await dd.count() > 0:
                value = (await dd.first.inner_text()).strip()
                if label:
                    result[label] = value
    except Exception as e:
        log.debug("dt_dd strategy failed: %s", e)
    return result


async def _extract_label_sibling(page: Page) -> dict:
    result: dict = {}
    try:
        labels = page.locator("label")
        count = await labels.count()
        for i in range(count):
            lbl = labels.nth(i)
            label_text = (await lbl.inner_text()).strip().rstrip(":")
            if not label_text:
                continue

            # label[for] — resolve by ID
            for_id = await lbl.get_attribute("for")
            if for_id:
                target = page.locator(f"#{for_id}")
                if await target.count() > 0:
                    result[label_text] = (await target.first.inner_text()).strip()
                    continue

            # following-sibling of the label itself
            sibling = lbl.locator("xpath=following-sibling::*[1]")
            if await sibling.count() > 0:
                sib_tag = await sibling.first.evaluate("el => el.tagName.toLowerCase()")
                if sib_tag not in ("label", "th"):
                    result[label_text] = (await sibling.first.inner_text()).strip()
                    continue
                if sib_tag == "label":
                    sib_class = await sibling.first.evaluate("el => el.className")
                    if "normal-m" in sib_class:
                        result[label_text] = (await sibling.first.inner_text()).strip()
                        continue
            # Bootstrap col-* pattern: label is alone in a col div; value is in
            # the parent's next sibling div (e.g. NC_SLP_AUD viewer.aspx).
            parent_sibling = lbl.locator("xpath=../following-sibling::div[1]")
            if await parent_sibling.count() > 0:
                ps_text = (await parent_sibling.first.inner_text()).strip()
                if ps_text and "\n" not in ps_text:
                    result[label_text] = ps_text
    except Exception as e:
        log.debug("label_sibling strategy failed: %s", e)
    return result


async def _extract_field_label_value(page: Page) -> dict:
    result: dict = {}
    try:
        # forge-typography--headline5 covers Tyler Technologies Forge UI (used by CAVU eLicense / CT eLicense)
        labels = page.locator("[class*='field-label'],[class*='fieldLabel'],[class*='fieldlabel'],[class*='infoTitle'],[class*='rlabel'],[class*='forge-typography--headline5']")
        count = await labels.count()
        for i in range(count):
            lbl = labels.nth(i)
            label_text = (await lbl.inner_text()).strip().rstrip(":")
            if not label_text:
                continue
            # Try [class*='field-value'] sibling
            value_el = lbl.locator(
                "xpath=following-sibling::*[contains(@class,'field-value') or "
                "contains(@class,'fieldValue') or contains(@class,'value')][1]"
            )
            if await value_el.count() > 0:
                result[label_text] = (await value_el.first.inner_text()).strip()
            else:
                # next sibling that isn't a label
                sibling = lbl.locator("xpath=following-sibling::*[1]")
                if await sibling.count() > 0:
                    sib_class = (await sibling.first.get_attribute("class") or "").lower()
                    if "label" not in sib_class:
                        result[label_text] = (await sibling.first.inner_text()).strip()
    except Exception as e:
        log.debug("field_label_value strategy failed: %s", e)
    return result


async def _extract_two_column_table(page: Page) -> dict:
    result: dict = {}
    try:
        tables = page.locator("table")
        count = await tables.count()
        for t in range(count):
            table = tables.nth(t)
            rows = table.locator("tr")
            row_count = await rows.count()
            for r in range(row_count):
                row = rows.nth(r)
                cells = row.locator("td")
                cell_count = await cells.count()
                if cell_count == 2:
                    key = (await cells.nth(0).inner_text()).strip().rstrip(":")
                    val = (await cells.nth(1).inner_text()).strip()
                    if key and "\n" not in key and "\t" not in key:
                        result[key] = val
    except Exception as e:
        log.debug("two_column_table strategy failed: %s", e)
    return result


async def _extract_th_td_table(page: Page) -> dict:
    """Extract from tables where each row has one <th> (label) and one <td> (value)."""
    result: dict = {}
    try:
        tables = page.locator("table")
        count = await tables.count()
        for t in range(count):
            table = tables.nth(t)
            rows = table.locator("tr")
            row_count = await rows.count()
            for r in range(row_count):
                row = rows.nth(r)
                ths = row.locator("th")
                tds = row.locator("td")
                th_count = await ths.count()
                td_count = await tds.count()
                if th_count == 1 and td_count == 1:
                    key = (await ths.nth(0).inner_text()).strip().rstrip(":")
                    val = (await tds.nth(0).inner_text()).strip()
                    if key and "\n" not in key and "\t" not in key:
                        result[key] = val
    except Exception as e:
        log.debug("th_td_table strategy failed: %s", e)
    return result


async def _extract_four_column_table(page: Page) -> dict:
    """Extract label-value pairs from tables where cells alternate label/value.
    Reads ALL pairs in each row (cells 0-1, 2-3, 4-5, …) so 8-cell rows
    (e.g. Indiana Details.aspx) are fully captured. Backwards-compatible with
    boards that only have 4 cells per row."""
    result: dict = {}
    try:
        tables = page.locator("table")
        count = await tables.count()
        for t in range(count):
            table = tables.nth(t)
            rows = table.locator("tr")
            row_count = await rows.count()
            for r in range(row_count):
                row = rows.nth(r)
                cells = row.locator("td")
                cell_count = await cells.count()
                if cell_count >= 4:
                    for i in range(0, cell_count - 1, 2):
                        k = (await cells.nth(i).inner_text()).strip().rstrip(":")
                        v = (await cells.nth(i + 1).inner_text()).strip()
                        # First-writer-wins: primary section appears before secondary sections
                        # (e.g. IN_PLA "Related Licenses" must not overwrite primary "Lic #").
                        if k and "\n" not in k and "\t" not in k and k not in result:
                            result[k] = v
    except Exception as e:
        log.debug("four_column_table strategy failed: %s", e)
    return result


async def _extract_header_mapped_table(page: Page) -> list[dict]:
    records: list[dict] = []
    try:
        tables = page.locator("table")
        count = await tables.count()
        for t in range(count):
            table = tables.nth(t)
            headers: list[str] = []
            thead_cells = table.locator("thead th")
            if await thead_cells.count() > 0:
                for i in range(await thead_cells.count()):
                    headers.append((await thead_cells.nth(i).inner_text()).strip())
            if not headers:
                continue
            rows = table.locator("tbody tr")
            for r in range(await rows.count()):
                row = rows.nth(r)
                cells = row.locator("td")
                rec: dict = {}
                for c in range(min(await cells.count(), len(headers))):
                    rec[headers[c]] = (await cells.nth(c).inner_text()).strip()
                if rec:
                    records.append(rec)
    except Exception as e:
        log.debug("header_mapped_table strategy failed: %s", e)
    return records


# ---------------------------------------------------------------------------
# Section table extraction (named sections, e.g. "Board Actions")
# ---------------------------------------------------------------------------

async def _extract_section_table(page: Page, section: DetailSection) -> list[dict]:
    records: list[dict] = []
    try:
        # Locate the table — prefer explicit CSS selector over heading-text search
        if section.selector:
            tbl = page.locator(section.selector).first
            if await tbl.count() == 0:
                return records
        else:
            heading = page.get_by_text(section.name, exact=True)
            if await heading.count() == 0:
                heading = page.get_by_text(section.name, exact=False)
            if await heading.count() == 0:
                return records
            tbl = heading.first.locator("xpath=following::table[1]")
            if await tbl.count() == 0:
                tbl = heading.first.locator("xpath=ancestor::*[1]//table[1]")
            if await tbl.count() == 0:
                return records

        headers: list[str] = []
        first_row = tbl.locator("tr").first
        cells = first_row.locator("th, td")
        for i in range(await cells.count()):
            headers.append((await cells.nth(i).inner_text()).strip())

        data_rows = tbl.locator("tr")
        start = 1 if headers else 0
        for r in range(start, await data_rows.count()):
            row = data_rows.nth(r)
            row_cells = row.locator("td")
            rec: dict = {}
            for c in range(min(await row_cells.count(), len(headers))):
                raw_key = headers[c] if headers else str(c)
                mapped_key = section.columns.get(raw_key, raw_key) if section.columns else raw_key
                rec[mapped_key] = (await row_cells.nth(c).inner_text()).strip()
            if rec:
                records.append(rec)
    except Exception as e:
        log.debug("Section '%s' extraction failed: %s", section.name, e)
    return records


# ---------------------------------------------------------------------------
# AG Grid extraction (MA Health and similar)
# ---------------------------------------------------------------------------

_AG_FALLBACK_COLUMNS = [
    "License Number", "License Type", "License Status",
    "First Name", "Middle Name", "Last Name", "Suffix",
    "Organization Name", "Address",
    "Issue Date", "Last Issue/Renewal Date", "Expiration Date",
]


async def extract_ag_grid(page: Page, fallback_columns: list[str] | None = None) -> list[dict]:
    """Extract all rows from an AG Grid, handling virtual scroll."""
    fb = fallback_columns or _AG_FALLBACK_COLUMNS
    records: list[dict] = []
    seen: set[str] = set()

    # Get headers
    headers: list[str] = []
    try:
        header_cells = page.locator("div.ag-header-cell")
        count = await header_cells.count()
        for i in range(count):
            cell = header_cells.nth(i)
            text_el = cell.locator("div.ag-header-cell-text")
            if await text_el.count() > 0:
                text = (await text_el.first.inner_text()).strip()
            else:
                text = (await cell.get_attribute("aria-label") or "").strip()
            headers.append(text)
        while headers and not headers[-1]:
            headers.pop()
        if not headers:
            headers = fb
    except Exception as e:
        log.warning("AG Grid header extraction failed, using fallback columns: %s", e)
        headers = fb

    log.info("AG Grid headers: %s", headers)

    # Scroll container candidates
    scroll_container = None
    for sel in ["div.ag-body-viewport", "div.ag-center-cols-viewport", "div.ag-body-clipper"]:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                scroll_container = loc.first
                break
        except Exception:
            pass

    last_count = 0
    for _ in range(30):
        # Collect rows
        rows = page.locator("div[role='row'].ag-row")
        if await rows.count() == 0:
            rows = page.locator("div.ag-row:not(.ag-header-row):not(.ag-row-group)")

        for r in range(await rows.count()):
            row = rows.nth(r)
            cells = row.locator("div[role='gridcell'], div.ag-cell")
            rec: dict = {}
            for c in range(min(await cells.count(), len(headers))):
                val = (await cells.nth(c).inner_text()).strip()
                col = headers[c] if c < len(headers) else str(c)
                rec[col] = val
                if c == 0:
                    # capture hyperlink if present
                    link = cells.nth(c).locator("a")
                    if await link.count() > 0:
                        href = await link.first.get_attribute("href")
                        rec[f"{col}_url"] = href or ""

            key = rec.get("License Number", "")
            if rec and key not in seen:
                seen.add(key)
                records.append(rec)

        if len(records) == last_count and _ > 0:
            break
        last_count = len(records)

        # Scroll
        try:
            if scroll_container:
                await scroll_container.evaluate("el => { el.scrollTop += 500; }")
            else:
                await page.evaluate("window.scrollBy(0, 500)")
        except Exception as e:
            log.debug("AG Grid scroll step failed (stopping scroll): %s", e)
            break
        await asyncio.sleep(0.5)

    log.info("AG Grid extracted %d records", len(records))
    return records


# ---------------------------------------------------------------------------
# Element-ID extraction (classic ASP.NET sites with no label elements)
# ---------------------------------------------------------------------------

async def _extract_element_ids(page: Page, id_map: dict) -> dict:
    """Extract fields directly from elements by DOM ID (e.g. <span id='Lic_no'>)."""
    result: dict = {}
    for elem_id, field_label in id_map.items():
        try:
            sel = elem_id if "[" in elem_id else f"#{elem_id}"
            el = page.locator(sel)
            if await el.count() > 0:
                text = _ZW_RE.sub("", (await el.first.inner_text()).strip()).strip()
                result[field_label] = text
        except Exception as e:
            log.debug("element_ids: failed to read %s: %s", elem_id, e)
    return result


# ---------------------------------------------------------------------------
# Primary extraction entry point
# ---------------------------------------------------------------------------

async def _extract_strong_label(page: Page) -> dict:
    """Handles pages where labels are <strong> tags inside <li> or <p> elements,
    value is the remaining text in the same parent (e.g. KS_KSBHADA)."""
    result: dict = {}
    try:
        labels = page.locator("li > strong, p > strong")
        count = await labels.count()
        for i in range(count):
            lbl = labels.nth(i)
            label_text = (await lbl.inner_text()).strip().rstrip(":")
            if not label_text:
                continue
            parent = lbl.locator("xpath=..")
            parent_text = (await parent.inner_text()).strip()
            value = parent_text.replace((await lbl.inner_text()).strip(), "").strip()
            if label_text and value:
                result[label_text] = value
    except Exception as e:
        log.debug("strong_label strategy failed: %s", e)
    return result


async def _extract_br_column_table(page: Page) -> dict:
    """Handles tables where a single row has two cells: labels in col[0] and
    values in col[1], each separated by <br> tags (e.g. GLSuite boards)."""
    result: dict = {}
    try:
        rows = page.locator("table tr")
        count = await rows.count()
        for r in range(count):
            cells = rows.nth(r).locator("td")
            if await cells.count() != 2:
                continue
            c0_html = await cells.nth(0).inner_html()
            if "<br" not in c0_html.lower():
                continue
            labels_text = await cells.nth(0).inner_text()
            values_text = await cells.nth(1).inner_text()
            labels_lines = [l.strip() for l in labels_text.split("\n")]
            values_lines = [v.strip() for v in values_text.split("\n")]
            for label, val in zip(labels_lines, values_lines):
                key = label.rstrip(":")
                if key and val:
                    result[key] = val
    except Exception as e:
        log.debug("br_column_table strategy failed: %s", e)
    return result


async def extract_detail(page: Page, config: DetailConfig) -> dict:
    """Run strategy cascade and section extraction; return merged field dict."""
    combined: dict = {}

    # Scope all extractions to a sub-element when configured (e.g. Kendo UI Window popup
    # at [data-role='window']).  This prevents the page's search-form labels from
    # contaminating popup-only field extractions.
    ctx: Any = page
    if config.scope_selector:
        scoped = page.locator(config.scope_selector)
        if await scoped.count() > 0:
            ctx = scoped.first
            log.debug("detail extraction scoped to '%s'", config.scope_selector)

    # First: extract name from heading (works for Angular/Thentia Cloud sites)
    heading_data = await _extract_heading_name(ctx)
    if heading_data:
        combined.update(heading_data)

    strategy_fns = {
        "dt_dd": _extract_dt_dd,
        "label_sibling": _extract_label_sibling,
        "field_label_value": _extract_field_label_value,
        "two_column_table": _extract_two_column_table,
        "th_td_table": _extract_th_td_table,
        "four_column_table": _extract_four_column_table,
        "br_column_table": _extract_br_column_table,
        "strong_label": _extract_strong_label,
    }

    for strategy in config.strategies:
        stype = strategy.get("type", "")
        fn = strategy_fns.get(stype)
        if fn:
            data = await fn(ctx)
            if data:
                combined.update(data)
                log.debug("Strategy '%s' yielded %d fields", stype, len(data))
        elif stype == "header_mapped_table":
            records = await _extract_header_mapped_table(ctx)
            if records:
                combined["_table_records"] = records
        elif stype == "element_ids":
            id_map = strategy.get("id_map", {})
            data = await _extract_element_ids(ctx, id_map)
            if data:
                combined.update(data)
                log.debug("Strategy 'element_ids' yielded %d fields", len(data))
        elif stype == "custom_js":
            script = strategy.get("script", "")
            if script:
                try:
                    data = await page.evaluate(script)
                    if isinstance(data, dict) and data:
                        combined.update(data)
                        log.debug("Strategy 'custom_js' yielded %d fields", len(data))
                except Exception as e:
                    log.debug("custom_js detail strategy failed: %s", e)

    # Section tables (always scoped via ctx)
    for section in config.sections:
        records = await _extract_section_table(ctx, section)
        combined[section.field] = records
        # If columns mapping was provided, flatten the first record's fields into
        # the top-level combined dict so they're available for output.license_record
        # templates (e.g. NV_DENTAL license_number/status/issue_date from nested table).
        if section.columns and records:
            for k, v in records[0].items():
                if k not in combined:
                    combined[k] = v

    # Strip Accela zero-width Unicode + normalise nbsp/unicode spaces on all
    # string values before field mapping (e.g. "Amy\xa0L\xa0Rodriguez").
    combined = {
        k: (_clean_value(v) if isinstance(v, str) else v)
        for k, v in combined.items()
    }

    # Apply field map
    if config.field_map:
        combined = apply_field_map(combined, config.field_map)

    combined["_source_url"] = page.url
    return combined


async def extract_vertical_kv(page_or_frame, vkv) -> list[dict]:
    """Extract records from a vertical label:value layout (no <table>).

    Walks `label_selector` nodes inside `container_selector`, treats each
    `record_marker_label` occurrence as the start of a new record, and assigns
    the text immediately following each label as its value. Labels are mapped
    via `field_map` to canonical field names.
    """
    records: list[dict] = []
    try:
        # JS-based scan is the cleanest way to walk DOM + extract sibling text in one shot.
        raw = await page_or_frame.evaluate(
            """({containerSelector, labelSelector, markerLabel}) => {
                const root = document.querySelector(containerSelector);
                if (!root) return [];
                const labels = [...root.querySelectorAll(labelSelector)];
                const out = [];
                let current = null;
                const markerLc = markerLabel.toLowerCase().replace(/[:\\s]+$/, '');
                for (const lbl of labels) {
                    const labelText = (lbl.textContent || '').trim().replace(/[:\\s]+$/, '');
                    if (!labelText) continue;
                    // Capture sibling text or parent-residual text
                    let value = '';
                    const next = lbl.nextSibling;
                    if (next && next.nodeType === 3) {
                        value = (next.textContent || '').trim();
                    }
                    if (!value) {
                        const sib = lbl.nextElementSibling;
                        if (sib) value = (sib.textContent || '').trim();
                    }
                    if (!value) {
                        const parentText = (lbl.parentElement?.textContent || '').trim();
                        value = parentText.replace(lbl.textContent || '', '').replace(/^[:\\s-]+/, '').trim();
                    }
                    if (labelText.toLowerCase() === markerLc) {
                        if (current && Object.keys(current).length) out.push(current);
                        current = {};
                    }
                    if (current !== null) {
                        current[labelText] = value;
                    }
                }
                if (current && Object.keys(current).length) out.push(current);
                return out;
            }""",
            {
                "containerSelector": vkv.container_selector,
                "labelSelector": vkv.label_selector,
                "markerLabel": vkv.record_marker_label,
            },
        )
        if vkv.max_records and len(raw) > vkv.max_records:
            raw = raw[: vkv.max_records]
        for r in raw:
            mapped: dict = {}
            for k, v in r.items():
                canonical = vkv.field_map.get(k) or vkv.field_map.get(k.lower())
                if canonical:
                    mapped[canonical] = v
                else:
                    mapped[k] = v
            records.append(mapped)
    except Exception as e:
        log.warning("extract_vertical_kv failed: %s", e)
    return records


async def extract_th_td_multi(page: Page, config: ResultsConfig) -> list[dict]:
    """Extract multiple records from a page where each container holds th/td key-value rows.

    One record per element matching `th_td_multi.container_selector`.
    Each <tr><th>key</th><td>value</td></tr> inside a container becomes one field.
    """
    records: list[dict] = []
    cfg = config.th_td_multi
    if not cfg:
        return records
    try:
        containers = page.locator(cfg.container_selector)
        count = await containers.count()
        for i in range(count):
            container = containers.nth(i)
            rec: dict = {}
            rows = container.locator("tr")
            row_count = await rows.count()
            for r in range(row_count):
                row = rows.nth(r)
                ths = row.locator("th")
                tds = row.locator("td")
                if await ths.count() == 1 and await tds.count() == 1:
                    key = (await ths.nth(0).inner_text()).strip().rstrip(":")
                    val = (await tds.nth(0).inner_text()).strip()
                    if key:
                        rec[key] = val
            if rec:
                records.append(rec)
    except Exception as e:
        log.warning("extract_th_td_multi failed: %s", e)
    return records


async def extract_results_table(page: Page, config: ResultsConfig) -> tuple[list[dict], str | None]:
    """Extract rows directly from a results table (no detail page click).

    Returns (records, warning_or_None). Callers should append warning to partial_failures
    when it is non-None.
    """
    records: list[dict] = []
    if config.type == "ag_grid":
        return await extract_ag_grid(page, config.ag_grid_columns or None), None

    tbl_cfg = config.table
    if not tbl_cfg:
        return records, None

    # custom_js: run arbitrary JS and return records directly
    if tbl_cfg.custom_js:
        try:
            raw = await page.evaluate(tbl_cfg.custom_js)
            if isinstance(raw, list):
                return [r for r in raw if isinstance(r, dict)], None
        except Exception as e:
            log.warning("custom_js extraction failed: %s", e)
            return records, f"custom_js extraction failed: {e}"

    # vertical_kv layout: no <table>, scan labelled fields
    if tbl_cfg.vertical_kv:
        ctx = page
        if tbl_cfg.iframe_selector:
            try:
                # query iframe element and grab content_frame()
                el = await page.query_selector(tbl_cfg.iframe_selector)
                if el:
                    frm = await el.content_frame()
                    if frm:
                        ctx = frm
            except Exception as e:
                log.warning("vertical_kv iframe '%s' resolution failed: %s", tbl_cfg.iframe_selector, e)
        return await extract_vertical_kv(ctx, tbl_cfg.vertical_kv), None

    # iframe-scoped table extraction
    ctx = page
    if tbl_cfg.iframe_selector:
        try:
            el = await page.query_selector(tbl_cfg.iframe_selector)
            if el:
                frm = await el.content_frame()
                if frm:
                    ctx = frm
                    log.info("Extracting from iframe '%s'", tbl_cfg.iframe_selector)
        except Exception as e:
            log.warning("iframe_selector '%s' resolution failed: %s", tbl_cfg.iframe_selector, e)

    if tbl_cfg.iframe_probe_selector:
        # Scan all live frames for the first one containing the probe selector.
        # Handles deeply nested frames (e.g. about:blank inside googleusercontent)
        # where content_frame() traversal cannot reach.
        probe_sel = tbl_cfg.iframe_probe_selector
        for frm in page.frames:
            try:
                if await frm.locator(probe_sel).count() > 0:
                    ctx = frm
                    log.info("iframe_probe_selector matched frame: %s", frm.url[:80])
                    break
            except Exception:
                continue

    try:
        if tbl_cfg.table_selector is not None and tbl_cfg.table_index is not None:
            table = ctx.locator(tbl_cfg.table_selector).nth(tbl_cfg.table_index)
            rows = table.locator(tbl_cfg.row_selector)
        else:
            rows = ctx.locator(tbl_cfg.row_selector)
        count = await rows.count()
        start = 1 if tbl_cfg.skip_first_row else 0
        for i in range(start, count):
            row = rows.nth(i)
            cells = row.locator(tbl_cfg.cell_selector)
            rec: dict = {}
            for idx, field_name in tbl_cfg.columns.items():
                if idx < await cells.count():
                    rec[field_name] = _ZW_RE.sub("", (await cells.nth(idx).inner_text()).strip()).strip()
            # Apply column_patterns: extract additional fields via regex from cell text.
            for idx, pattern_map in (tbl_cfg.column_patterns or {}).items():
                if idx < await cells.count():
                    cell_text = (await cells.nth(idx).inner_text()).strip()
                    for extra_field, pattern in pattern_map.items():
                        m = re.search(pattern, cell_text)
                        if m:
                            rec[extra_field] = m.group(1).strip()
            if rec and any(v for v in rec.values() if isinstance(v, str) and v.strip()):
                if any(not rec.get(f, "").strip() for f in (tbl_cfg.required_fields or [])):
                    continue
                records.append(rec)

    except Exception as e:
        log.warning("extract_results_table failed: %s", e)
        return records, f"extract_results_table failed: {e}"

    # Deduplicate rows that differ only by an unmapped column (e.g. supervising physician).
    if tbl_cfg.deduplicate_by:
        _seen_keys: set[tuple] = set()
        _deduped: list = []
        for rec in records:
            key = tuple((rec.get(f) or "").strip().upper() for f in tbl_cfg.deduplicate_by)
            if key not in _seen_keys:
                _seen_keys.add(key)
                _deduped.append(rec)
        records = _deduped

    return records, None
