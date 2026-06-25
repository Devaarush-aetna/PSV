"""Site-drift detector — appends suggested fixes to a CSV report.

NEVER auto-applies a fix. The agent calls report_site_drift with a hint
about what changed and how it might be fixed; we just record it for
engineering to review.
"""
from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config as cfg

log = logging.getLogger(__name__)

_REPORT_NAME = "site_drift_report.csv"
_HEADERS = (
    "timestamp", "source_id", "suspected_selector", "evidence_dir",
    "fix_hint", "severity",
)


def _report_path() -> Path:
    cfg.DRIFT_ROOT.mkdir(parents=True, exist_ok=True)
    return cfg.DRIFT_ROOT / _REPORT_NAME


def append_drift_report(source_id: str, suspected_selector: str,
                        evidence_dir: str, fix_hint: str,
                        severity: str = "med") -> dict[str, Any]:
    """Append one row. Returns the row as dict for the agent's tool result."""
    path = _report_path()
    row = {
        "timestamp": datetime.utcnow().isoformat(),
        "source_id": source_id,
        "suspected_selector": suspected_selector,
        "evidence_dir": evidence_dir,
        "fix_hint": fix_hint,
        "severity": severity,
    }
    write_header = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(_HEADERS))
        if write_header:
            w.writeheader()
        w.writerow(row)
    log.info("Drift report appended: %s -> %s", source_id, path)
    return row
