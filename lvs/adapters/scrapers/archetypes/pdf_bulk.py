"""PDF bulk-roster archetype."""
from __future__ import annotations

import logging
from pathlib import Path

from engine.models import SearchQuery, SiteConfig
from engine.output import map_to_license_record, upsert_to_db
from engine.post_processors import apply_field_map
from engine.proxy import get_proxy_config
from ._shared import _emit_event

log = logging.getLogger(__name__)


async def scrape_pdf_bulk(
    config: SiteConfig, query: SearchQuery, db, t0: float, run_id: str,
) -> list:
    """Download PDF roster(s), extract tables, search in-memory."""
    from engine.pdf_extractor import (
        discover_pdf_url,
        download_pdf,
        extract_table_data,
        search_all_by_last_name,
        search_by_combination,
        search_by_license_number,
        search_by_name,
    )
    from engine.models import COMBO_MODES

    source_id = config.identity.source_id
    pdf_cfg = config.pdf_bulk
    if not pdf_cfg:
        log.error("[%s] pdf_bulk archetype requires pdf_bulk config block", source_id)
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, "no_pdfs_configured")
        return []

    cache_dir = pdf_cfg.cache_dir.replace("{source_id}", source_id)
    _cache_path = Path(cache_dir)
    if not _cache_path.is_absolute():
        # __file__ → archetypes/ → scrapers/ → adapters/ → lvs/ → PSV_DEV/
        cache_dir = str(Path(__file__).parents[4] / cache_dir.lstrip("./"))
    q = query.query.strip()

    proxy_cfg = get_proxy_config()
    resolved_entries = list(pdf_cfg.pdfs)
    if pdf_cfg.download_strategy == "page_link":
        from engine.models import PdfEntry
        discovery_url = pdf_cfg.base_url or config.identity.base_url
        try:
            if not resolved_entries:
                discovered_url = discover_pdf_url(
                    discovery_url, pdf_cfg.link_selector, proxy_cfg
                )
                log.info("[%s] page_link discovered PDF URL: %s", source_id, discovered_url)
                resolved_entries = [PdfEntry(url=discovered_url, format="default")]
            else:
                rebuilt = []
                for entry in resolved_entries:
                    sel = entry.link_selector or pdf_cfg.link_selector
                    discovered_url = discover_pdf_url(discovery_url, sel, proxy_cfg)
                    log.info(
                        "[%s] page_link discovered PDF URL (%s/%s): %s",
                        source_id, entry.format, sel, discovered_url,
                    )
                    rebuilt.append(PdfEntry(
                        url=discovered_url,
                        format=entry.format,
                        license_prefix=entry.license_prefix,
                    ))
                resolved_entries = rebuilt
        except Exception as exc:
            log.error("[%s] page_link discovery failed: %s", source_id, exc)
            await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, "pdf_discovery_failed")
            return []

    if not resolved_entries:
        log.error("[%s] pdf_bulk archetype requires pdf_bulk.pdfs list or page_link strategy", source_id)
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, "no_pdfs_configured")
        return []

    all_pdf_data: list[tuple[list, str]] = []
    for entry in resolved_entries:
        if not entry.url:
            continue
        if query.mode == "license_number" and entry.license_prefix:
            if not q.upper().startswith(entry.license_prefix.upper()):
                continue
        try:
            pdf_path = download_pdf(entry.url, cache_dir, pdf_cfg.cache_days)
            records, fmt = extract_table_data(pdf_path)
            all_pdf_data.append((records, entry.format if entry.format != "default" else fmt))
        except Exception as exc:
            log.warning("[%s] Failed to load PDF %s: %s", source_id, entry.url, exc)

    if not all_pdf_data:
        for entry in resolved_entries:
            if not entry.url:
                continue
            try:
                pdf_path = download_pdf(entry.url, cache_dir, pdf_cfg.cache_days)
                records, fmt = extract_table_data(pdf_path)
                all_pdf_data.append((records, entry.format if entry.format != "default" else fmt))
            except Exception as exc:
                log.warning("[%s] PDF fallback load failed: %s", source_id, exc)

    if not all_pdf_data:
        log.error("[%s] No PDFs could be loaded", source_id)
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, "pdf_load_failed")
        return []

    raw_results: list[dict] = []
    is_combo = query.mode in COMBO_MODES
    has_type_filter = bool(query.license_type or query.provider_type)

    if is_combo or has_type_filter:
        for records, fmt in all_pdf_data:
            raw_results.extend(search_by_combination(
                records, fmt,
                license_number=query.license_number or (q if query.mode.startswith("license") else None),
                first_name=query.first_name,
                last_name=query.last_name,
                license_type=query.license_type,
                provider_type=query.provider_type,
            ))
    elif query.mode == "license_number":
        for records, fmt in all_pdf_data:
            found = search_by_license_number(q, records, fmt)
            if found:
                raw_results.append(found)
                break
    elif query.mode == "last_name":
        for records, fmt in all_pdf_data:
            raw_results.extend(search_all_by_last_name(q, records, fmt))
    elif query.mode in ("first_name", "full_name"):
        parts = q.split(None, 1)
        fn, ln = (parts[0], parts[1]) if len(parts) == 2 else (q, "")
        for records, fmt in all_pdf_data:
            found = search_by_name(fn, ln, records, fmt)
            if found:
                raw_results.append(found)

    log.info("[%s] PDF search returned %d record(s)", source_id, len(raw_results))

    result_records = []
    for raw in raw_results:
        mapped = apply_field_map(raw, config.detail.field_map)
        rec = map_to_license_record(mapped, config, {})
        result_records.append(rec)

    await _emit_event(db, run_id, source_id, "complete", "success", t0, len(result_records))
    if db and result_records:
        await upsert_to_db(db, result_records)
    return result_records
