"""verify_license dispatcher — routes to the correct archetype module."""
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

from engine.models import SearchQuery, SiteConfig
from engine.navigator import check_board_capability
from ._shared import _emit_event

log = logging.getLogger(__name__)


async def verify_license(
    config: SiteConfig,
    query: SearchQuery,
    db=None,
    headless_override: bool | None = None,
) -> list:
    source_id = config.identity.source_id
    _ts = datetime.utcnow()
    _date_str = _ts.strftime("%Y%m%d")
    _time_str = _ts.strftime("%H%M%S")
    # Evidence directory counter for a unique run_id within the same second
    _ev_source_dir = Path(__file__).parents[4] / "Evidence" / source_id
    _today_seq = (
        sum(1 for d in _ev_source_dir.iterdir() if d.is_dir() and d.name.startswith(_date_str))
        if _ev_source_dir.exists()
        else 0
    )
    run_id = f"{_date_str}_{_time_str}_{_today_seq + 1:03d}"
    t0 = time.time()

    log.info("[%s] run_id=%s  query=%s/%s", source_id, run_id, query.mode, query.query)

    cap_status, fallback = check_board_capability(config, query)
    if cap_status == "reject":
        log.error("[%s] Capability reject: %s", source_id, fallback)
        await _emit_event(db, run_id, source_id, "capability", "reject", t0, 0, fallback)
        return []
    if cap_status == "degrade" and fallback:
        log.warning(
            "[%s] Board cannot satisfy mode '%s' natively; degrading to '%s' with auto-joined query='%s'",
            source_id, query.mode, fallback, query.query,
        )
        query = query.model_copy(update={"mode": fallback})

    archetype = config.identity.archetype

    if archetype == "socrata_api":
        from .socrata import scrape_socrata_api
        return await scrape_socrata_api(config, query, db, t0, run_id)

    if archetype == "socrata_bulk_csv":
        from .socrata import scrape_socrata_bulk_csv
        return await scrape_socrata_bulk_csv(config, query, db, t0, run_id)

    if archetype == "pdf_bulk":
        from .pdf_bulk import scrape_pdf_bulk
        return await scrape_pdf_bulk(config, query, db, t0, run_id)

    if archetype == "csv_bulk":
        from .csv_bulk import scrape_csv_bulk
        return await scrape_csv_bulk(config, query, db, t0, run_id)

    if archetype == "certemy":
        from .certemy import scrape_certemy
        return await scrape_certemy(config, query, db, t0, run_id)

    if archetype == "json_api":
        from .json_api import scrape_json_api
        return await scrape_json_api(config, query, db, t0, run_id)

    if archetype == "datatables_jsapi":
        from .datatables import scrape_datatables_jsapi
        return await scrape_datatables_jsapi(config, query, db, t0, run_id)

    if archetype == "filemaker_webdirect":
        from .filemaker import scrape_filemaker_webdirect
        return await scrape_filemaker_webdirect(config, query, db, t0, run_id)

    if archetype == "psypact":
        from .psypact import scrape_psypact
        return await scrape_psypact(config, query, db, t0, run_id)

    if archetype == "ny_credentials":
        from .ny_credentials import scrape_ny_credentials
        return await scrape_ny_credentials(config, query, db, t0, run_id)

    # All remaining archetypes use the browser form loop
    from .browser_form import scrape_browser
    return await scrape_browser(config, query, db, t0, run_id, headless_override=headless_override)
