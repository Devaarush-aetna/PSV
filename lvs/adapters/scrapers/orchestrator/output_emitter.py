"""4-channel output writer + per-row trace persister.

Channels (all under PSV_DEV/Output/{channel}/{YYYY-MM}/{run_id}.{ext}):
  - standard      Excel + CSV   every input row
  - nppes         CSV           every row, full NPPES record + diff vs master
  - ai_fallback   CSV           every row where the AI agent ran
  - manual        CSV           every unresolved row, with structured failure_reason

The standard channel reuses psv_test.write_results for the Excel sheet and
adds a CSV sibling.
"""
from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import openpyxl
from openpyxl.styles import Font, PatternFill

from . import config as cfg
from . import disambiguator as disamb
from .ai_agent import AiAgentResult
from .ladder import LadderResult
from .nppes_client import NpiDiscrepancy, NppesRecord
from .trace import RowTrace

log = logging.getLogger(__name__)


# Per-row outcome bundle that the runner builds and hands to emit_row().
@dataclass
class RowOutcome:
    master_row: dict
    master_row_id: str
    trace: RowTrace
    nppes: Optional[NppesRecord] = None
    discrepancy: Optional[NpiDiscrepancy] = None
    ladder_result: Optional[LadderResult] = None
    ai_result: Optional[AiAgentResult] = None

    @property
    def status(self) -> str:
        if self.ai_result and self.ai_result.outcome == "resolved":
            return "Pass"
        if self.ladder_result and self.ladder_result.status == "Pass":
            return "Pass"
        return "Fail"

    @property
    def chosen_record(self) -> Optional[Any]:
        if self.ai_result and self.ai_result.outcome == "resolved":
            return self.ai_result.chosen_candidate
        if self.ladder_result:
            return self.ladder_result.best_record
        return None

    @property
    def chosen_breakdown(self) -> Optional[disamb.ScoreBreakdown]:
        if self.ai_result and self.ai_result.outcome == "resolved":
            return self.ai_result.chosen_breakdown
        if self.ladder_result:
            return self.ladder_result.best_breakdown
        return None

    @property
    def reason(self) -> str:
        if self.status == "Pass":
            return ""
        if self.ai_result and self.ai_result.reason:
            return self.ai_result.reason
        if self.ladder_result and self.ladder_result.reason:
            return self.ladder_result.reason
        if self.trace.final_reason:
            return self.trace.final_reason
        return "no_records"


@dataclass
class OutputEmitter:
    run_id: str
    dirs: dict[str, Path] = field(default_factory=dict)
    _standard_rows: list[dict] = field(default_factory=list)
    _nppes_rows: list[dict] = field(default_factory=list)
    _ai_rows: list[dict] = field(default_factory=list)
    _manual_rows: list[dict] = field(default_factory=list)
    _add_license_rows: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.dirs:
            self.dirs = cfg.ensure_channel_dirs(self.run_id)

    # ----- Per-row entry point -----

    def collect(self, outcome: RowOutcome) -> None:
        """Capture one row's outputs into the in-memory channel buffers, AND
        persist the per-row trace JSON immediately (so a crash mid-run still
        leaves a debugging trail)."""
        outcome.trace.write_json(self.dirs["trace"])
        self._collect_standard(outcome)
        self._collect_nppes(outcome)
        if outcome.ai_result is not None:
            self._collect_ai(outcome)
        if outcome.status == "Fail":
            self._collect_manual(outcome)
        if outcome.status == "Pass":
            self._collect_add_license(outcome)

    # ----- Channel collectors -----

    def _collect_standard(self, o: RowOutcome) -> None:
        m = o.master_row
        rec = o.chosen_record
        bd = o.chosen_breakdown
        ev = ""
        for a in reversed(o.trace.attempts):
            if a.evidence_dir:
                ev = a.evidence_dir
                break

        # Match method
        if o.status != "Pass":
            match_method = "none"
        elif o.ai_result and o.ai_result.outcome == "resolved":
            match_method = "ai_fuzzy"
        elif o.ladder_result and o.ladder_result.npi_substituted:
            match_method = "npi_substituted_exact"
        elif o.ladder_result and o.ladder_result.tiebreaker_used:
            match_method = "tiebreak_provider_type"
        else:
            match_method = "exact_license" if (
                bd and bd.license_numerics >= 1.0
            ) else "exact_name"

        row = {
            "first_name": m.get("first_name", ""),
            "middle_name": m.get("middle_name", ""),
            "last_name": m.get("last_name", ""),
            "lic_state": m.get("lic_state", ""),
            "prov_type": m.get("prov_type", ""),
            "lic_type": m.get("lic_type", ""),
            "license_id": m.get("license_id", ""),
            "npi_no": m.get("npi_no", ""),
            "status": o.status,
            "license_expiry": _expiry_str(rec),
            "matched_license": getattr(rec, "license_number", "") or "" if rec else "",
            "matched_first": getattr(rec, "licensee_first_name", "") or "" if rec else "",
            "matched_last": getattr(rec, "licensee_last_name", "") or "" if rec else "",
            "match_method": match_method,
            "fuzzy_score": (round(bd.total, 3) if bd else ""),
            "weight_profile": (bd.weight_profile if bd else ""),
            "tiebreaker_used": bool(o.ladder_result and o.ladder_result.tiebreaker_used)
                                or bool(o.ai_result and o.ai_result.outcome == "resolved"),
            "ai_fallback_used": o.ai_result is not None,
            "ai_outcome": (o.ai_result.outcome if o.ai_result else ""),
            "npi_substituted": bool(o.ladder_result and o.ladder_result.npi_substituted),
            "secondary_check_passed": bool(bd and bd.gate_passed),
            "provider_type_matched": bool(bd and bd.provider_type >= 1.0),
            "attempts_used": len(o.trace.attempts),
            "evidence_dir": ev,
            "trace_path": str(self.dirs["trace"] / f"{o.master_row_id}.json"),
            "reason": o.reason,
            "fuzzy_breakdown": json.dumps(bd.to_dict()) if bd else "",
        }
        self._standard_rows.append(row)

    def _collect_nppes(self, o: RowOutcome) -> None:
        n = o.nppes
        d = o.discrepancy
        if n is None:
            row = {
                "master_row_id": o.master_row_id,
                "npi_no": o.master_row.get("npi_no", ""),
                "fetch_status": "empty_input" if not o.master_row.get("npi_no") else "not_found",
            }
        else:
            primary_lic = n.primary_license
            row = {
                "master_row_id": o.master_row_id,
                "npi_no": n.npi,
                "nppes_first": n.first_name,
                "nppes_last": n.last_name,
                "nppes_middle": n.middle_name or "",
                "nppes_credential": n.credential or "",
                "nppes_primary_taxonomy": n.primary_taxonomy_desc,
                "nppes_primary_taxonomy_code": n.primary_taxonomy_code,
                "nppes_primary_license_no": primary_lic.get("number", "") if primary_lic else "",
                "nppes_primary_license_state": primary_lic.get("state", "") if primary_lic else "",
                "extra_license_count": max(0, len(n.license_numbers) - 1),
                "has_other_names": len(n.other_names) > 0,
                "fetch_status": n.fetch_status,
            }
            if d:
                df = d.differing_fields
                row["diff_first_name"] = _diff_cell(df.get("first_name"))
                row["diff_last_name"] = _diff_cell(df.get("last_name"))
                row["diff_license_number"] = _diff_cell(df.get("license_number"))
                row["other_name_used"] = d.other_name_used
            else:
                row.update({"diff_first_name": "", "diff_last_name": "",
                            "diff_license_number": "", "other_name_used": False})
        self._nppes_rows.append(row)

    def _collect_ai(self, o: RowOutcome) -> None:
        ai = o.ai_result
        assert ai is not None
        row = {
            "master_row_id": o.master_row_id,
            "lic_state": o.master_row.get("lic_state", ""),
            "prov_type": o.master_row.get("prov_type", ""),
            "first_name": o.master_row.get("first_name", ""),
            "last_name": o.master_row.get("last_name", ""),
            "license_id": o.master_row.get("license_id", ""),
            "outcome": ai.outcome,
            "reason": ai.reason,
            "turns_used": ai.turns_used,
            "tools_used": ",".join(ai.tools_used),
            "chosen_source_id": ai.chosen_source_id or "",
            "chosen_license": (getattr(ai.chosen_candidate, "license_number", "") or ""
                                if ai.chosen_candidate else ""),
            "drift_count": len(ai.drift_reports),
        }
        self._ai_rows.append(row)

    def _collect_manual(self, o: RowOutcome) -> None:
        row = {
            "master_row_id": o.master_row_id,
            "first_name": o.master_row.get("first_name", ""),
            "middle_name": o.master_row.get("middle_name", ""),
            "last_name": o.master_row.get("last_name", ""),
            "lic_state": o.master_row.get("lic_state", ""),
            "prov_type": o.master_row.get("prov_type", ""),
            "lic_type": o.master_row.get("lic_type", ""),
            "license_id": o.master_row.get("license_id", ""),
            "npi_no": o.master_row.get("npi_no", ""),
            "failure_reason": o.reason,
            "attempts_used": len(o.trace.attempts),
            "trace_path": str(self.dirs["trace"] / f"{o.master_row_id}.json"),
        }
        self._manual_rows.append(row)

    def _collect_add_license(self, o: RowOutcome) -> None:
        """AddLicense channel — one row per Pass result.

        Column rules (per story requirements):
          EPDB                   → EPDB PIN from master row (col 3)
          State                  → License State from master row
          MaintBy                → Maintained By from master row (col 7)
          LicenseNumber          → verified license number from board record
          LicenseEffDate         → blank (intentionally empty)
          LicenseTermDate        → expiry date from board record
          LicenseType            → LIC_TYPE_NM from Alteryx / master row (col 10)
          OriginalLicenseDate    → blank (intentionally empty)
          OverrideExistingLicense → "Yes" for all records
          EPDBDone               → blank (filled manually post-upload)
        """
        m = o.master_row
        rec = o.chosen_record
        # Use verified license from board; fall back to input license_id
        verified_license = getattr(rec, "license_number", "") or "" if rec else ""
        if not verified_license:
            verified_license = m.get("license_id", "")
        row = {
            "EPDB": m.get("epdb_pin", ""),
            "State": m.get("lic_state", ""),
            "MaintBy": m.get("maintained_by", ""),
            "LicenseNumber": verified_license,
            "LicenseEffDate": "",           # always blank per rules
            "LicenseTermDate": _expiry_str(rec),
            "LicenseType": m.get("lic_type", ""),
            "OriginalLicenseDate": "",      # always blank per rules
            "OverrideExistingLicense": "Yes",
            "EPDBDone": "",                 # filled manually
        }
        self._add_license_rows.append(row)

    # ----- Flush all channels to disk -----

    def flush(self) -> dict[str, Path]:
        self._reconcile_by_name()
        # Re-collect add_license for any rows that were reconciled to Pass
        self._rebuild_add_license_after_reconciliation()
        paths: dict[str, Path] = {}
        if self._standard_rows:
            paths["standard_xlsx"] = self._write_standard_xlsx()
            paths["standard_csv"] = self._write_csv("standard", self._standard_rows)
        if self._nppes_rows:
            paths["nppes"] = self._write_csv("nppes", self._nppes_rows)
        if self._ai_rows:
            paths["ai_fallback"] = self._write_csv("ai_fallback", self._ai_rows)
        if self._manual_rows:
            paths["manual"] = self._write_csv("manual", self._manual_rows)
        if self._add_license_rows:
            paths["add_license"] = self._write_add_license_xlsx()
        log.info("Output flushed: %s", {k: str(v) for k, v in paths.items()})
        return paths

    def _rebuild_add_license_after_reconciliation(self) -> None:
        """Add entries for rows that were promoted to Pass via cross_row_name_match."""
        existing_licenses = {r["LicenseNumber"] for r in self._add_license_rows}
        for row in self._standard_rows:
            if row.get("match_method") != "cross_row_name_match":
                continue
            if row.get("status") != "Pass":
                continue
            lic = row.get("matched_license") or row.get("license_id", "")
            if lic in existing_licenses:
                continue
            self._add_license_rows.append({
                "EPDB": "",   # not available via reconciled row
                "State": row.get("lic_state", ""),
                "MaintBy": "",
                "LicenseNumber": lic,
                "LicenseEffDate": "",
                "LicenseTermDate": row.get("license_expiry", ""),
                "LicenseType": row.get("lic_type", ""),
                "OriginalLicenseDate": "",
                "OverrideExistingLicense": "Yes",
                "EPDBDone": "",
            })
            existing_licenses.add(lic)

    def _reconcile_by_name(self) -> None:
        """Post-run pass: for every non-Pass row, check if another row in this
        run already passed for the same (first, last, state, prov_type). If so,
        copy the matched record fields from the passing row and mark as Pass with
        match_method='cross_row_name_match'. Handles the common case where the
        same provider appears twice with different license-ID formats (e.g. '46101'
        and '5346101') — the license fetch for one format succeeds; the other
        resolves here via name match rather than hitting AI/manual.
        """
        def _norm(s: str) -> str:
            return (s or "").strip().upper()

        # Build lookup of passing rows by (first, last, state, prov_type).
        # When multiple passing rows share the same name key (genuinely different
        # providers with the same name), skip reconciliation for that key to avoid
        # cross-contamination.
        pass_lookup: dict[tuple, dict] = {}
        ambiguous_keys: set[tuple] = set()
        for row in self._standard_rows:
            if row.get("status") != "Pass":
                continue
            key = (
                _norm(row.get("first_name", "")),
                _norm(row.get("last_name", "")),
                _norm(row.get("lic_state", "")),
                _norm(row.get("prov_type", "")),
            )
            if not key[0] or not key[1]:
                continue  # no name → cannot reconcile
            if key in pass_lookup:
                ambiguous_keys.add(key)
            else:
                pass_lookup[key] = row

        reconciled = 0
        for row in self._standard_rows:
            if row.get("status") == "Pass":
                continue
            key = (
                _norm(row.get("first_name", "")),
                _norm(row.get("last_name", "")),
                _norm(row.get("lic_state", "")),
                _norm(row.get("prov_type", "")),
            )
            if not key[0] or not key[1]:
                continue
            if key in ambiguous_keys or key not in pass_lookup:
                continue
            src = pass_lookup[key]
            row["status"] = "Pass"
            row["match_method"] = "cross_row_name_match"
            row["matched_license"] = src.get("matched_license", "")
            row["matched_first"] = src.get("matched_first", "")
            row["matched_last"] = src.get("matched_last", "")
            row["license_expiry"] = src.get("license_expiry", "")
            row["fuzzy_score"] = src.get("fuzzy_score", "")
            row["weight_profile"] = src.get("weight_profile", "")
            row["source_run_row"] = src.get("license_id", "")  # for audit
            reconciled += 1

        if reconciled:
            log.info("Post-run name reconciliation: resolved %d row(s) via cross_row_name_match",
                     reconciled)
            # Remove reconciled rows from manual channel (they are now Pass)
            reconciled_ids = {
                r["license_id"]
                for r in self._standard_rows
                if r.get("match_method") == "cross_row_name_match"
            }
            self._manual_rows = [
                r for r in self._manual_rows
                if r.get("license_id") not in reconciled_ids
            ]

    def _write_csv(self, channel: str, rows: list[dict]) -> Path:
        if not rows:
            return Path()
        out = self.dirs[channel] / f"{self.run_id}.csv"
        # Union of all keys across rows
        keys: list[str] = []
        seen: set[str] = set()
        for r in rows:
            for k in r.keys():
                if k not in seen:
                    seen.add(k)
                    keys.append(k)
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in rows:
                w.writerow(r)
        return out

    def _write_add_license_xlsx(self) -> Path:
        out = self.dirs["add_license"] / f"{self.run_id}_AddLicense.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "AddLicense"
        _AL_COLS = [
            "EPDB", "State", "MaintBy", "LicenseNumber",
            "LicenseEffDate", "LicenseTermDate", "LicenseType",
            "OriginalLicenseDate", "OverrideExistingLicense", "EPDBDone",
        ]
        ws.append(_AL_COLS)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for r in self._add_license_rows:
            ws.append([r.get(h, "") for h in _AL_COLS])
        wb.save(str(out))
        return out

    def _write_standard_xlsx(self) -> Path:
        out = self.dirs["standard"] / f"{self.run_id}.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "PSV Results"
        if not self._standard_rows:
            wb.save(str(out))
            return out
        headers = list(self._standard_rows[0].keys())
        ws.append(headers)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        green = PatternFill("solid", fgColor="C6EFCE")
        red = PatternFill("solid", fgColor="FFC7CE")
        for r in self._standard_rows:
            ws.append([r.get(h, "") for h in headers])
            fill = green if r.get("status") == "Pass" else red
            row_idx = ws.max_row
            for col in range(1, len(headers) + 1):
                ws.cell(row=row_idx, column=col).fill = fill
        wb.save(str(out))
        return out


# ----- Helpers ------------------------------------------------------------

def _expiry_str(rec: Optional[Any]) -> str:
    if rec is None:
        return ""
    d = getattr(rec, "expiration_date", None)
    if d is None:
        return ""
    try:
        return d.isoformat()
    except Exception:
        return str(d)


def _diff_cell(pair: Optional[tuple[str, str]]) -> str:
    if not pair:
        return ""
    return f"{pair[0]} | {pair[1]}"
