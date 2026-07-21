"""CSV bulk-roster archetype."""
from __future__ import annotations

import asyncio
import logging

_DOWNLOAD_MAX_ATTEMPTS = 3
_DOWNLOAD_BACKOFF_S = [5, 15]

from engine.models import SearchQuery, SiteConfig
from engine.output import map_to_license_record, upsert_to_db
from engine.post_processors import apply_field_map
from ._shared import _emit_event

log = logging.getLogger(__name__)


async def scrape_csv_bulk(
    config: SiteConfig, query: SearchQuery, db, t0: float, run_id: str,
) -> list:
    """Download CSV roster (with caching), search in-memory, map to LicenseRecords."""
    from engine.csv_extractor import (
        get_csv, load_csv, search_by_license_number, search_by_name, search_by_multi_column,
    )
    from engine.models import COMBO_MODES

    source_id = config.identity.source_id
    csv_cfg = config.csv_bulk
    if not csv_cfg:
        log.error("[%s] csv_bulk archetype requires a csv_bulk section in config", source_id)
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, "no_csv_bulk_config")
        return []

    log.info("[%s] CSV bulk run_id=%s  query=%s/%s", source_id, run_id, query.mode, query.query)

    is_combo = query.mode in COMBO_MODES
    has_type_filter = bool(query.license_type or query.provider_type)
    search_col_or_list = csv_cfg.search_columns.get(query.mode)

    if not is_combo and not search_col_or_list:
        log.error("[%s] No search_column configured for mode '%s'", source_id, query.mode)
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, f"no_search_column:{query.mode}")
        return []

    csv_path = effective_header_row = None
    last_exc: Exception | None = None
    for _attempt in range(_DOWNLOAD_MAX_ATTEMPTS):
        try:
            csv_path, effective_header_row = await get_csv(config.identity.base_url, source_id, csv_cfg)
            break
        except Exception as exc:
            last_exc = exc
            if _attempt < _DOWNLOAD_MAX_ATTEMPTS - 1:
                delay = _DOWNLOAD_BACKOFF_S[min(_attempt, len(_DOWNLOAD_BACKOFF_S) - 1)]
                log.warning(
                    "[%s] CSV download attempt %d/%d failed (%s) — retrying in %ds",
                    source_id, _attempt + 1, _DOWNLOAD_MAX_ATTEMPTS, exc, delay,
                )
                await asyncio.sleep(delay)
            else:
                log.error(
                    "[%s] CSV download failed after %d attempts: %s",
                    source_id, _DOWNLOAD_MAX_ATTEMPTS, exc,
                )

    if csv_path is None:
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, str(last_exc))
        return []

    try:
        df = load_csv(csv_path, csv_cfg.encoding, effective_header_row, csv_cfg.separator)
    except Exception as exc:
        log.error("[%s] CSV parse failed: %s", source_id, exc)
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, str(exc))
        return []

    col_map = {
        "license_number": csv_cfg.search_columns.get("license_number") if isinstance(csv_cfg.search_columns.get("license_number"), str) else None,
        "first_name": csv_cfg.search_columns.get("first_name") if isinstance(csv_cfg.search_columns.get("first_name"), str) else None,
        "last_name": csv_cfg.search_columns.get("last_name") if isinstance(csv_cfg.search_columns.get("last_name"), str) else None,
        "license_type": csv_cfg.license_type_column,
        "provider_type": csv_cfg.provider_type_column,
    }

    if is_combo or has_type_filter:
        if is_combo:
            raw_results = search_by_multi_column(
                df, col_map,
                license_number=query.license_number or (query.query if query.mode.startswith("license") else None),
                first_name=query.first_name,
                last_name=query.last_name,
                license_type=query.license_type,
                provider_type=query.provider_type,
            )
        else:
            field_for_mode = {
                "license_number": "license_number",
                "first_name": "first_name",
                "last_name": "last_name",
            }.get(query.mode)
            kwargs = {"license_type": query.license_type, "provider_type": query.provider_type}
            if field_for_mode:
                kwargs[field_for_mode] = query.query
            raw_results = search_by_multi_column(df, col_map, **kwargs)
    elif query.mode == "license_number":
        raw_results = search_by_license_number(df, search_col_or_list, query.query)
    else:
        raw_results = search_by_name(df, search_col_or_list, query.query)

    log.info("[%s] CSV search returned %d record(s)", source_id, len(raw_results))

    records = []
    for raw in raw_results:
        raw["_source_url"] = config.identity.base_url
        mapped = apply_field_map(raw, config.detail.field_map)
        rec = map_to_license_record(mapped, config, {})
        records.append(rec)

    await _emit_event(db, run_id, source_id, "complete", "success", t0, len(records))
    if db and records:
        await upsert_to_db(db, records)
    return records
