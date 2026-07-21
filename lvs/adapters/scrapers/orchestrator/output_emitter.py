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
from datetime import date as _date
from pathlib import Path
from typing import Any, Optional

import openpyxl
from openpyxl.styles import Font, PatternFill

from . import config as cfg
from . import disambiguator as disamb
from .disambiguator import license_numerics_match as _lic_num_match
from .ai_agent import AiAgentResult
from .ladder import LadderResult
from .nppes_client import NpiDiscrepancy, NppesRecord
from .trace import RowTrace

log = logging.getLogger(__name__)

# sites/ directory — used for lazy board_name lookups
_SITES_DIR = Path(__file__).resolve().parents[1] / "sites"

# Reason codes that mean the board could not be reached due to CAPTCHA / WAF / network block.
# Rows with these reasons get match_method="Captcha Based Board" in standard output
# and a human-readable message in the manual channel. They are never sent to add_license.
_CAPTCHA_REASONS: frozenset[str] = frozenset({
    "state_captcha_blocked",
    "prov_type_captcha_blocked",
    "board_skip_captcha",
})
_board_name_cache: dict[str, str] = {}


def _get_board_name(source_id: str) -> str:
    """Return human-readable board_name for a source_id, reading config.yaml once."""
    if not source_id:
        return ""
    if source_id in _board_name_cache:
        return _board_name_cache[source_id]
    import yaml  # noqa: PLC0415
    cfg_path = _SITES_DIR / source_id / "config.yaml"
    try:
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        name = data.get("identity", {}).get("board_name", source_id)
    except Exception:
        name = source_id
    _board_name_cache[source_id] = name
    return name


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


def _blank_state_stats() -> dict:
    return {
        "total":          0,
        "pass_rule":      0,  # Pass via rule-based (no AI, no NPI)
        "pass_ai":        0,  # AI resolved Pass (goes to manual not add_license)
        "pass_npi":       0,  # NPI substituted Pass
        "fail_rule":      0,  # Rule-based Fail
        "fail_ai":        0,  # AI attempted but failed
        "captcha":        0,  # Captcha / WAF blocked
        "mismatch":       0,  # Name/license cross-validation override to Fail
        "same_expiry":    0,  # Expiry same as input (no update needed)
        "no_expiry":      0,  # Pass but board returned no expiry date
        "manual":         0,  # Total rows in manual channel
        "add_license":    0,  # Total rows in add_license channel
        "ai_used":        0,  # Any row where AI agent ran
        "ai_resolved":    0,  # AI resolved (outcome == "resolved")
        "ai_failed":      0,  # AI ran but did not resolve
        "npi_substituted": 0, # NPI was used to find the board record
    }


@dataclass
class OutputEmitter:
    run_id: str
    dirs: dict[str, Path] = field(default_factory=dict)
    _standard_rows: list[dict] = field(default_factory=list)
    _nppes_rows: list[dict] = field(default_factory=list)
    _ai_rows: list[dict] = field(default_factory=list)
    _manual_rows: list[dict] = field(default_factory=list)
    _add_license_rows: list[dict] = field(default_factory=list)
    _state_stats: dict[str, dict] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.dirs:
            self.dirs = cfg.ensure_channel_dirs(self.run_id)

    # ----- Per-row entry point -----

    @staticmethod
    def _name_license_mismatch_reason(outcome: "RowOutcome") -> str | None:
        """Name matched but license numbers are present on both sides and don't align."""
        bd = outcome.chosen_breakdown
        rec = outcome.chosen_record
        if bd is None or rec is None:
            return None
        if bd.license_numerics >= 1.0:
            return None  # numeric match — no issue
        board_lic = (getattr(rec, "license_number", "") or "").strip()
        input_lic = (outcome.master_row.get("license_id", "") or "").strip()
        if not board_lic or not input_lic:
            return None  # one side has no license to compare
        return "Name matched but License mismatched"

    @staticmethod
    def _same_expiry_check(outcome: "RowOutcome") -> str | None:
        """If the board expiry matches the input expiry, return a manual reason.
        Future match → 'Provider has the same Expiry as input, still in 90 days'
        Past match   → 'Expired and same date in the State board'
        Returns None when either date is absent or they differ.
        """
        rec = outcome.chosen_record
        if rec is None:
            return None
        input_expiry_str = (outcome.master_row.get("input_expiry", "") or "").strip()
        if not input_expiry_str:
            return None
        board_expiry_str = _expiry_str(rec)
        if not board_expiry_str:
            return None
        try:
            input_date = _date.fromisoformat(input_expiry_str[:10])
            board_date = _date.fromisoformat(board_expiry_str[:10])
        except Exception:
            return None
        if input_date != board_date:
            return None
        today = _date.today()
        if board_date >= today:
            return "Provider has the same Expiry as input, still in 90 days"
        return "Expired and same date in the State board"

    @staticmethod
    def _license_name_mismatch_reason(outcome: "RowOutcome") -> str | None:
        """License matched numerically but first or last name doesn't align.
        Only fires when both input and board have the relevant name fields.
        """
        bd = outcome.chosen_breakdown
        rec = outcome.chosen_record
        if bd is None or rec is None:
            return None
        if bd.license_numerics < 1.0:
            return None  # license didn't match — different check handles this
        # NAME_FUZZ_MIN default is 0.7 — same threshold the disambiguator gate uses
        _THRESHOLD = 0.7
        input_first = (outcome.master_row.get("first_name", "") or "").strip()
        input_last = (outcome.master_row.get("last_name", "") or "").strip()
        first_fail = input_first and bd.first_name < _THRESHOLD
        last_fail = input_last and bd.last_name < _THRESHOLD
        if first_fail or last_fail:
            return "License matched but Name mismatched"
        return None

    @staticmethod
    def _numeric_not_exact_license_reason(outcome: "RowOutcome") -> str | None:
        """Name matched AND license numerics aligned, but the license strings are not
        identical (e.g. input '12345' vs board 'LC-12345', or '017371' vs '17371').
        The digits agree but the full value differs — flag for human confirmation.
        Returns None when the license is an exact alphanumeric match or when
        license numerics did not align at all.
        """
        import re as _re
        bd = outcome.chosen_breakdown
        rec = outcome.chosen_record
        if bd is None or rec is None:
            return None
        if bd.license_numerics < 1.0:
            return None  # no numeric alignment — handled elsewhere
        board_lic = (getattr(rec, "license_number", "") or "").strip()
        input_lic = (outcome.master_row.get("license_id", "") or "").strip()
        if not board_lic or not input_lic:
            return None
        # Strip all non-alphanumeric and compare case-insensitively
        _strip = lambda s: _re.sub(r"[^a-z0-9]", "", s.lower())
        if _strip(input_lic) == _strip(board_lic):
            return None  # effectively identical — no review needed
        return "Name matched and license numerics aligned but not exact match — manual review required"

    # Human-readable manual-reason messages, keyed by final_reason code.
    _CAPTCHA_MANUAL_REASONS: dict[str, str] = field(default_factory=lambda: {
        "state_captcha_blocked": (
            "Captcha Based Board: Entire state is blocked from automated verification "
            "(CAPTCHA / IP restriction). Manual verification required on the state board website."
        ),
        "prov_type_captcha_blocked": (
            "Captcha Based Board: This provider type is blocked from automated verification "
            "(CAPTCHA / IP restriction on state board). Manual verification required."
        ),
        "board_skip_captcha": (
            "Captcha Based Board: State board blocks automated access via CAPTCHA, McAfee, "
            "DataDome, or WAF. Manual verification required on the state board website."
        ),
    })

    def collect(self, outcome: RowOutcome) -> None:
        """Capture one row's outputs into the in-memory channel buffers.

        Architecture:
          1. Standard channel  — every row, always.
          2. NPPES / AI channels — when applicable.
          3. Compute a single manual_reason (first match wins).
          4. If manual_reason → manual channel only, never add_license.
             If no manual_reason AND Pass with expiry → add_license only.
          Standard and add_license are populated independently;
          manual and add_license are always mutually exclusive.
        """
        outcome.trace.write_json(self.dirs["trace"])
        self._collect_standard(outcome)
        self._collect_nppes(outcome)
        if outcome.ai_result is not None:
            self._collect_ai(outcome)

        manual_reason = self._compute_manual_reason(outcome)

        # Apply any retroactive standard-row overrides driven by the manual reason
        if manual_reason in (
            "Name matched but License mismatched",
            "License matched but Name mismatched",
        ):
            self._standard_rows[-1]["status"] = "Fail"
            self._standard_rows[-1]["match_method"] = "name_license_mismatch"
            self._standard_rows[-1]["reason"] = manual_reason
        elif manual_reason == "Expired and same date in the State board":
            self._standard_rows[-1]["status"] = "Fail"
            self._standard_rows[-1]["reason"] = manual_reason

        # Route: manual XOR add_license
        went_manual = False
        went_add_license = False
        if manual_reason:
            self._collect_manual(outcome, failure_reason=manual_reason)
            went_manual = True
        elif outcome.status == "Pass":
            added = self._collect_add_license(outcome)
            if added:
                went_add_license = True
            else:
                # Pass but no expiry returned — manual, never add_license
                self._collect_manual(
                    outcome,
                    failure_reason=(
                        "no_expiry_date: license verified on state board but expiration "
                        "date not returned — manual review required to confirm LicenseTermDate"
                    ),
                )
                went_manual = True

        # Per-state stats accumulation
        self._accumulate_state_stats(outcome, manual_reason, went_manual, went_add_license)

    def _compute_manual_reason(self, outcome: RowOutcome) -> str | None:
        """Single point that decides whether a row goes to manual and why.
        Returns a human-readable reason string, or None (→ eligible for add_license).

        Priority order (first match wins):
          1. Captcha / board-skip blocked
          2. AI fallback used — any layer (search or disambiguator) — Pass or Fail
          3. NPI substituted
          4. Rule-based Fail (no match found)
          5. Name ↔ license cross-validation mismatch (Pass overridden to Fail)
          6. Same expiry as input (no update needed)
        """
        _final_reason = (outcome.trace.final_reason or "").strip()

        # 1. Captcha / WAF block
        if _final_reason in _CAPTCHA_REASONS:
            return self._CAPTCHA_MANUAL_REASONS.get(_final_reason, _final_reason)

        # 2. AI fallback used — layer 1 (search) or layer 2 (disambiguator)
        #    Applies regardless of Pass/Fail outcome; never goes to add_license.
        if outcome.ai_result is not None:
            if outcome.status == "Pass":
                return (
                    "AI fallback passed — manual review required to confirm "
                    "verification result before use"
                )
            else:
                # When the root cause was a license-found-but-name-mismatch, report
                # that directly so analysts see WHY rather than a generic AI fail.
                if (_final_reason == "name_mismatch"
                        and outcome.trace.license_attempts_returned_records()):
                    return "License matched but Name mismatched"
                _ai_fail_reason = (outcome.ai_result.reason or "no_candidates")
                return f"AI fallback failed — manual review required ({_ai_fail_reason})"

        # 3. NPI substituted (standard stays Pass; human confirms the NPI-derived match)
        if outcome.status == "Pass" and outcome.ladder_result and outcome.ladder_result.npi_substituted:
            return "NPI used to fetch — manual review required"

        # 4. Rule-based Fail
        if outcome.status == "Fail":
            _fail_reason_code = outcome.reason or "no_records"
            # When a license-mode rung found a record but the name doesn't match,
            # report the mismatch clearly instead of the raw reason code.
            if (_fail_reason_code == "name_mismatch"
                    and outcome.trace.license_attempts_returned_records()):
                return "License matched but Name mismatched"
            return _fail_reason_code

        # 5. Name ↔ license cross-validation (Pass rows only beyond this point)
        mismatch = (
            self._name_license_mismatch_reason(outcome)
            or self._license_name_mismatch_reason(outcome)
        )
        if mismatch:
            return mismatch

        # 5b. Name matched + numeric license aligned but strings not identical
        numeric_not_exact = self._numeric_not_exact_license_reason(outcome)
        if numeric_not_exact:
            return numeric_not_exact

        # 6. Expiry unchanged vs input
        same_expiry = self._same_expiry_check(outcome)
        if same_expiry:
            return same_expiry

        return None  # clean Pass — eligible for add_license

    # ----- Per-state stats -----

    def _accumulate_state_stats(
        self,
        outcome: RowOutcome,
        manual_reason: str | None,
        went_manual: bool,
        went_add_license: bool,
    ) -> None:
        state = (outcome.master_row.get("lic_state") or "UNKNOWN").upper()
        if state not in self._state_stats:
            self._state_stats[state] = _blank_state_stats()
        s = self._state_stats[state]
        s["total"] += 1

        _final_reason = (outcome.trace.final_reason or "").strip()
        _ai_used = outcome.ai_result is not None
        _npi_used = bool(outcome.ladder_result and outcome.ladder_result.npi_substituted)

        # AI counters
        if _ai_used:
            s["ai_used"] += 1
            if outcome.ai_result.outcome == "resolved":
                s["ai_resolved"] += 1
                s["pass_ai"] += 1
            else:
                s["ai_failed"] += 1

        # NPI
        if _npi_used:
            s["npi_substituted"] += 1
            s["pass_npi"] += 1

        # Captcha
        if _final_reason in _CAPTCHA_REASONS:
            s["captcha"] += 1

        # Mismatch override
        if manual_reason in (
            "Name matched but License mismatched",
            "License matched but Name mismatched",
            "Name matched and license numerics aligned but not exact match — manual review required",
        ):
            s["mismatch"] += 1

        # Same expiry
        if manual_reason in (
            "Provider has the same Expiry as input, still in 90 days",
            "Expired and same date in the State board",
        ):
            s["same_expiry"] += 1

        # No expiry
        if went_manual and manual_reason and "no_expiry_date" in manual_reason:
            s["no_expiry"] += 1

        # Pass / Fail in standard (after any retroactive overrides)
        std = self._standard_rows[-1] if self._standard_rows else {}
        if std.get("status") == "Pass":
            if not _ai_used and not _npi_used:
                s["pass_rule"] += 1
        else:
            if not _ai_used and _final_reason not in _CAPTCHA_REASONS:
                s["fail_rule"] += 1

        # Channel counters
        if went_manual:
            s["manual"] += 1
        if went_add_license:
            s["add_license"] += 1

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
        _final_reason = (o.trace.final_reason or "").strip()
        if o.status != "Pass" and _final_reason in _CAPTCHA_REASONS:
            match_method = "Captcha Based Board"
        elif o.status != "Pass":
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
            "board_name": _get_board_name(getattr(rec, "source_id", "") or "") if rec else "",
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

    def _collect_manual(self, o: RowOutcome, failure_reason: str | None = None) -> None:
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
            "failure_reason": failure_reason if failure_reason is not None else o.reason,
            "attempts_used": len(o.trace.attempts),
            "trace_path": str(self.dirs["trace"] / f"{o.master_row_id}.json"),
        }
        self._manual_rows.append(row)

    def _collect_add_license(self, o: RowOutcome) -> bool:
        """AddLicense channel — one row per Pass result with a confirmed expiry date.

        Column rules (per AddLicense.xlsx template):
          EPDB                    → Input  (EPDB PIN from master row)
          State                   → Input  (License State from master row)
          MaintBy                 → Input  (Maintained By from master row)
          LicenseNumber           → Input  (verified license number from board)
          LicenseEffDate          → Blanks (intentionally empty)
          LicenseTermDate         → Updated Exp Date (expiry from board record)
          LicenseType             → Operating (LIC_TYPE_NM from master row)
          OriginalLicenseDate     → Blanks (intentionally empty)
          OverrideExistingLicense → Yes
          EPDBDone                → Blanks (filled manually post-upload)

        Returns True if the row was added, False if skipped (no expiry found → manual).
        All values written as text strings; dates as MM/DD/YYYY.
        """
        m = o.master_row
        rec = o.chosen_record

        expiry = _expiry_text(rec)
        if not expiry:
            log.info(
                "[add_license] Skipping %s %s — no expiry date returned by board (→ manual review)",
                m.get("first_name", ""), m.get("last_name", ""),
            )
            return False

        verified_license = getattr(rec, "license_number", "") or "" if rec else ""
        if not verified_license:
            verified_license = m.get("license_id", "")

        row = {
            "EPDB": str(m.get("epdb_pin", "") or ""),
            "State": str(m.get("lic_state", "") or ""),
            "MaintBy": str(m.get("maintained_by", "") or ""),
            "LicenseNumber": str(verified_license),
            "LicenseEffDate": "",
            "LicenseTermDate": expiry,          # MM/DD/YYYY text
            "LicenseType": str(m.get("lic_type", "") or ""),
            "OriginalLicenseDate": "",
            "OverrideExistingLicense": "Yes",
            "EPDBDone": "",
        }
        self._add_license_rows.append(row)
        return True

    # ----- Flush all channels to disk -----

    def flush(self) -> dict[str, Path]:
        self._reconcile_by_name()
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
        if self._state_stats:
            paths["run_summary"] = self._write_run_summary()
        log.info("Output flushed: %s", {k: str(v) for k, v in paths.items()})
        return paths

    def _rebuild_add_license_after_reconciliation(self) -> None:
        """Add entries for rows that were promoted to Pass via cross_row_name_match.
        Respects the mutual-exclusivity rule: any license already in the manual channel
        is never added to add_license.
        """
        existing_licenses = {r["LicenseNumber"] for r in self._add_license_rows}
        # Licenses that are already going to manual must not also go to add_license
        manual_licenses = {r.get("license_id", "") for r in self._manual_rows if r.get("license_id")}
        for row in self._standard_rows:
            if row.get("match_method") != "cross_row_name_match":
                continue
            if row.get("status") != "Pass":
                continue
            lic = row.get("matched_license") or row.get("license_id", "")
            if lic in existing_licenses or lic in manual_licenses:
                continue

            expiry_iso = row.get("license_expiry", "")
            expiry = _iso_to_text(expiry_iso)

            if not expiry:
                # No expiry — route to manual channel instead of add_license
                self._manual_rows.append({
                    "master_row_id": row.get("trace_path", ""),
                    "first_name": row.get("first_name", ""),
                    "middle_name": row.get("middle_name", ""),
                    "last_name": row.get("last_name", ""),
                    "lic_state": row.get("lic_state", ""),
                    "prov_type": row.get("prov_type", ""),
                    "lic_type": row.get("lic_type", ""),
                    "license_id": lic,
                    "npi_no": row.get("npi_no", ""),
                    "failure_reason": (
                        "no_expiry_date: license verified via cross_row_name_match but "
                        "expiration date not available — manual review required to confirm LicenseTermDate"
                    ),
                    "attempts_used": row.get("attempts_used", ""),
                    "trace_path": row.get("trace_path", ""),
                })
                continue

            self._add_license_rows.append({
                "EPDB": "",
                "State": str(row.get("lic_state", "") or ""),
                "MaintBy": "",
                "LicenseNumber": str(lic),
                "LicenseEffDate": "",
                "LicenseTermDate": expiry,
                "LicenseType": str(row.get("lic_type", "") or ""),
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

    def _write_run_summary(self) -> Path:
        """Write one row per state to Output/run_summary/{YYYY-MM}/{run_id}_summary.csv."""
        out = self.dirs["run_summary"] / f"{self.run_id}_summary.csv"
        _COLS = [
            "run_id", "state",
            "total", "pass_rule", "pass_ai", "pass_npi",
            "fail_rule", "fail_ai", "captcha",
            "mismatch", "same_expiry", "no_expiry",
            "manual", "add_license",
            "ai_used", "ai_resolved", "ai_failed", "npi_substituted",
        ]
        rows = []
        for state in sorted(self._state_stats):
            s = self._state_stats[state]
            row = {"run_id": self.run_id, "state": state}
            row.update({k: s.get(k, 0) for k in _COLS if k not in ("run_id", "state")})
            rows.append(row)

        # Append a TOTAL row across all states
        if len(rows) > 1:
            total_row = {"run_id": self.run_id, "state": "TOTAL"}
            for k in _COLS:
                if k in ("run_id", "state"):
                    continue
                total_row[k] = sum(r.get(k, 0) for r in rows)
            rows.append(total_row)

        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=_COLS)
            w.writeheader()
            w.writerows(rows)

        log.info("Run summary written: %s (%d state(s))", out, len(self._state_stats))
        return out

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
        # Header row (bold, no text format needed for headers)
        ws.append(_AL_COLS)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        # Data rows — every cell forced to TEXT format so Excel never
        # auto-converts dates, license numbers, or leading zeros.
        for r in self._add_license_rows:
            row_num = ws.max_row + 1
            for col_idx, h in enumerate(_AL_COLS, start=1):
                cell = ws.cell(row=row_num, column=col_idx, value=str(r.get(h, "") or ""))
                cell.number_format = "@"   # Excel TEXT format
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
    """ISO date string — used for standard output channel."""
    if rec is None:
        return ""
    d = getattr(rec, "expiration_date", None)
    if d is None:
        return ""
    try:
        return d.isoformat()
    except Exception:
        return str(d)


def _expiry_text(rec: Optional[Any]) -> str:
    """MM/DD/YYYY text string for AddLicense output. Returns '' if no expiry."""
    if rec is None:
        return ""
    d = getattr(rec, "expiration_date", None)
    if d is None:
        return ""
    try:
        if hasattr(d, "strftime"):
            return d.strftime("%m/%d/%Y")
        if isinstance(d, str) and d:
            parsed = _date.fromisoformat(d[:10])
            return parsed.strftime("%m/%d/%Y")
        return str(d)
    except Exception:
        return str(d) if d else ""


def _iso_to_text(iso: str) -> str:
    """Convert an ISO date string (YYYY-MM-DD) to MM/DD/YYYY text. Returns '' if unparseable."""
    if not iso:
        return ""
    try:
        parsed = _date.fromisoformat(iso[:10])
        return parsed.strftime("%m/%d/%Y")
    except Exception:
        return iso  # pass through as-is if already formatted or unparseable


def _diff_cell(pair: Optional[tuple[str, str]]) -> str:
    if not pair:
        return ""
    return f"{pair[0]} | {pair[1]}"
