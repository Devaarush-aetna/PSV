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
    timestamp TEXT NOT NULL,
    partial_result INTEGER DEFAULT 0,
    warnings TEXT
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

CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    master_row_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    source_id TEXT NOT NULL,
    board_url TEXT,
    mode TEXT NOT NULL,
    query_repr TEXT,
    query_signature TEXT,
    used_npi_data INTEGER DEFAULT 0,
    differing_field TEXT,
    record_count INTEGER DEFAULT 0,
    outcome TEXT,
    confidence REAL,
    weight_profile_used TEXT,
    evidence_dir TEXT,
    duration_ms INTEGER DEFAULT 0,
    error_msg TEXT,
    timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attempts_run_row ON attempts(run_id, master_row_id);

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
           (run_id, source_id, stage, status, duration_ms, record_count, used_ai, error_msg, timestamp, partial_result, warnings)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            event.run_id, event.source_id, event.stage, event.status,
            event.duration_ms, event.record_count, int(event.used_ai),
            event.error_msg, event.timestamp.isoformat(),
            int(event.partial_result),
            json.dumps(event.warnings) if event.warnings else None,
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
            "partial_result": event.partial_result,
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


async def log_attempt(db: aiosqlite.Connection, run_id: str, master_row_id: str,
                      attempt: "AttemptRecordLike") -> None:
    """Persist one AttemptRecord (from orchestrator.trace) to the attempts table.
    Accepts any object with the same attribute set (duck-typed)."""
    ts = datetime.utcnow().isoformat()
    await db.execute(
        """INSERT INTO attempts
           (run_id, master_row_id, seq, source_id, board_url, mode, query_repr,
            query_signature, used_npi_data, differing_field, record_count,
            outcome, confidence, weight_profile_used, evidence_dir,
            duration_ms, error_msg, timestamp)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id, master_row_id, attempt.seq, attempt.source_id,
            attempt.board_url, attempt.mode, attempt.query_repr,
            attempt.query_signature, int(attempt.used_npi_data),
            attempt.differing_field, attempt.record_count,
            attempt.outcome, attempt.confidence, attempt.weight_profile_used,
            attempt.evidence_dir, attempt.duration_ms, attempt.error_msg, ts,
        ),
    )
    await db.commit()


# Type alias placeholder — actual class lives in orchestrator/trace.py to keep
# engine layer free of orchestrator imports.
class AttemptRecordLike:  # pragma: no cover
    seq: int
    source_id: str
    board_url: str
    mode: str
    query_repr: str
    query_signature: str
    used_npi_data: bool
    differing_field: Optional[str]
    record_count: int
    outcome: str
    confidence: Optional[float]
    weight_profile_used: Optional[str]
    evidence_dir: str
    duration_ms: int
    error_msg: Optional[str]
