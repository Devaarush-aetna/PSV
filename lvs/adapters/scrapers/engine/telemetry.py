"""SQLite telemetry: scrape_events and ai_touchpoints tables + structured logging."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

import aiosqlite

from .models import TelemetryEvent

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scrape_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_ms INTEGER DEFAULT 0,
    record_count INTEGER DEFAULT 0,
    used_ai INTEGER DEFAULT 0,
    error_msg TEXT,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_touchpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    model TEXT NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS license_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    license_number TEXT NOT NULL,
    licensee_first_name TEXT,
    licensee_last_name TEXT,
    licensee_full_name TEXT,
    licensee_middle_name TEXT,
    licensee_suffix TEXT,
    license_type TEXT,
    profession_code TEXT,
    status TEXT,
    effective_date TEXT,
    expiration_date TEXT,
    issue_date TEXT,
    last_renewal_date TEXT,
    address TEXT,
    city TEXT,
    state_code TEXT,
    zip_code TEXT,
    disciplinary_actions TEXT,
    source_url TEXT,
    scraped_at TEXT,
    evidence_html_path TEXT,
    evidence_screenshot_path TEXT,
    raw_fields TEXT,
    used_ai INTEGER DEFAULT 0,
    UNIQUE(source_id, license_number)
);
"""


async def init_db(db_path: str) -> aiosqlite.Connection:
    db = await aiosqlite.connect(db_path)
    await db.executescript(_SCHEMA)
    await db.commit()
    log.info("Telemetry DB initialised at %s", db_path)
    return db


async def log_scrape_event(db: aiosqlite.Connection, event: TelemetryEvent) -> None:
    await db.execute(
        """INSERT INTO scrape_events
           (run_id, source_id, stage, status, duration_ms, record_count, used_ai, error_msg, timestamp)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            event.run_id, event.source_id, event.stage, event.status,
            event.duration_ms, event.record_count, int(event.used_ai),
            event.error_msg, event.timestamp.isoformat(),
        ),
    )
    await db.commit()
    log.info(
        json.dumps({
            "event": "scrape_event",
            "run_id": event.run_id,
            "source_id": event.source_id,
            "stage": event.stage,
            "status": event.status,
            "duration_ms": event.duration_ms,
            "record_count": event.record_count,
            "used_ai": event.used_ai,
        })
    )


async def log_ai_touchpoint(
    db: aiosqlite.Connection,
    run_id: str,
    source_id: str,
    stage: str,
    prompt_tokens: int,
    completion_tokens: int,
    model: str,
) -> None:
    ts = datetime.utcnow().isoformat()
    await db.execute(
        """INSERT INTO ai_touchpoints
           (run_id, source_id, stage, prompt_tokens, completion_tokens, model, timestamp)
           VALUES (?,?,?,?,?,?,?)""",
        (run_id, source_id, stage, prompt_tokens, completion_tokens, model, ts),
    )
    await db.commit()
    log.info(
        json.dumps({
            "event": "ai_touchpoint",
            "run_id": run_id,
            "source_id": source_id,
            "stage": stage,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        })
    )
