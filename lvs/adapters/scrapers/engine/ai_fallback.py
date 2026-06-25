"""
Claude Opus AI Fallback — generic ReAct PSV extractor.

Invoked when rule-based extraction yields < MIN_FIELDS_THRESHOLD meaningful
fields, OR when auto-disambiguation cannot reduce multiple candidates to one.

Replaces the legacy Azure OpenAI GPT-4 implementation.  All board/state-
specific logic lives in the caller's SiteConfig; this module is board-agnostic.

Two modes (AILayer):
  LAYER_1_FETCHER       — zero records from the rule-based ladder; AI attempts
                          every available search combination from scratch.
  LAYER_2_DISAMBIGUATOR — multiple candidates; AI works the disambiguation
                          ladder on the candidate set already provided.

ReAct loop pattern (from florida_react_prompt.py):
  Thought / Action / Observation  (repeated)
  FinalAnswer: { JSON }

Circuit breaker: after MAX_CONSECUTIVE_ERRORS consecutive connection errors
the module disables AI for the remainder of the process.
"""
from __future__ import annotations

import csv
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
_ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
_ANTHROPIC_MODEL   = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8")
_CLAUDE_CONFIGURED = bool(_ANTHROPIC_API_KEY)

_MIN_FIELDS_THRESHOLD  = 3
_MAX_HTML_CHARS        = 20_000
_MAX_TOKENS_RESPONSE   = 8_192
_MAX_CONSECUTIVE_ERRORS = 2

_consecutive_errors = 0
_circuit_open       = False   # True → AI disabled for this process

# ── Notification / drift log paths ────────────────────────────────────────────
_ENGINE_DIR             = Path(__file__).parent
NOTIFICATION_LOG_PATH   = _ENGINE_DIR / "portal_notifications.json"
SITE_DRIFT_REPORT_PATH  = _ENGINE_DIR / "site_drift_report.csv"

# ── Confidence thresholds (PSV spec §6) ───────────────────────────────────────
CONFIDENCE_AUTO_SELECT = 90
CONFIDENCE_BEST_MATCH  = 80

try:
    import anthropic as _anthropic
    _client = _anthropic.AsyncAnthropic(api_key=_ANTHROPIC_API_KEY) if _CLAUDE_CONFIGURED else None
    _CLAUDE_AVAILABLE = _CLAUDE_CONFIGURED
except Exception:
    _CLAUDE_AVAILABLE = False
    _client = None
    log.warning("Anthropic SDK not available — AI fallback disabled")


# =============================================================================
# DATA CLASSES  (board-agnostic; callers populate from SiteConfig)
# =============================================================================

class AILayer(str, Enum):
    """Which AI fallback mode is active for this invocation."""
    LAYER_1_FETCHER       = "LAYER_1_FETCHER"
    LAYER_2_DISAMBIGUATOR = "LAYER_2_DISAMBIGUATOR"


class FailureReason(str, Enum):
    """Why a rule-based step did not produce a confident result."""
    ZERO_RECORDS          = "ZERO_RECORDS"
    MULTIPLE_UNRESOLVED   = "MULTIPLE_UNRESOLVED"
    CRITERIA_MISSING      = "CRITERIA_MISSING"
    FILTER_AMBIGUOUS      = "FILTER_AMBIGUOUS"
    SECONDARY_CHECK_FAIL  = "SECONDARY_CHECK_FAIL"
    URL_ERROR             = "URL_ERROR"
    UI_CHANGED            = "UI_CHANGED"
    CAPTCHA_BLOCKED       = "CAPTCHA_BLOCKED"
    UNEXPECTED_RESPONSE   = "UNEXPECTED_RESPONSE"


_PORTAL_ALERT_REASONS = {
    FailureReason.URL_ERROR,
    FailureReason.UI_CHANGED,
    FailureReason.CAPTCHA_BLOCKED,
    FailureReason.UNEXPECTED_RESPONSE,
}

_REASON_DESCRIPTIONS: Dict[FailureReason, str] = {
    FailureReason.ZERO_RECORDS:
        "Search returned 0 records",
    FailureReason.MULTIPLE_UNRESOLVED:
        "Multiple records found; disambiguation did not isolate one",
    FailureReason.CRITERIA_MISSING:
        "Required search field absent — step skipped",
    FailureReason.FILTER_AMBIGUOUS:
        "Disambiguation filter matched more than one record",
    FailureReason.SECONDARY_CHECK_FAIL:
        "Record found but scraped first+last did not match master name",
    FailureReason.URL_ERROR:
        "All portal URLs failed (HTTP error / timeout / empty page)",
    FailureReason.UI_CHANGED:
        "Portal page loaded but expected HTML structure was not found",
    FailureReason.CAPTCHA_BLOCKED:
        "CAPTCHA challenge detected — automated access blocked",
    FailureReason.UNEXPECTED_RESPONSE:
        "Server responded but content was unrecognisable",
}


@dataclass
class StepFailure:
    """Structured record of why a single rule-based step failed."""
    step_name:     str
    reason:        FailureReason
    records_found: int         = 0
    detail:        str         = ""
    candidates:    List[Dict]  = field(default_factory=list)

    def to_prompt_line(self) -> str:
        desc  = _REASON_DESCRIPTIONS[self.reason]
        count = f" ({self.records_found} record(s))" if self.records_found else ""
        extra = f" | {self.detail}"                  if self.detail        else ""
        alert = " [PORTAL ALERT]"                   if self.reason in _PORTAL_ALERT_REASONS else ""
        return f"  - {self.step_name}: {desc}{count}{extra}{alert}"


@dataclass
class NPIEnrichmentResult:
    """Outcome of the NPPES enrichment step (optional; pass None to skip)."""
    npi:                    Optional[str]  = None
    nppes_first_name:       Optional[str]  = None
    nppes_last_name:        Optional[str]  = None
    nppes_credential:       Optional[str]  = None
    nppes_state:            Optional[str]  = None
    other_names:            List[str]      = field(default_factory=list)
    discrepancy_found:      bool           = False
    discrepancy_detail:     str            = ""
    npi_substituted:        bool           = False
    npi_substituted_result: Optional[str]  = None
    other_name_match:       bool           = False
    lookup_failed:          bool           = False


@dataclass
class SecondaryCheckResult:
    """Result of the secondary identity check (scraped name vs master name)."""
    check_passed:  bool = True
    scraped_first: str  = ""
    scraped_last:  str  = ""
    master_first:  str  = ""
    master_last:   str  = ""
    fail_reason:   str  = ""


# =============================================================================
# PORTAL NOTIFICATION SYSTEM
# =============================================================================

class AlertType(str, Enum):
    URL_CHANGED             = "URL_CHANGED"
    UI_STRUCTURE_CHANGED    = "UI_STRUCTURE_CHANGED"
    PORTAL_DOWN             = "PORTAL_DOWN"
    CAPTCHA_DETECTED        = "CAPTCHA_DETECTED"
    UNEXPECTED_RESPONSE     = "UNEXPECTED_RESPONSE"
    AI_SERVICE_ERROR        = "AI_SERVICE_ERROR"        # Anthropic API down / circuit open
    NPI_SERVICE_DOWN        = "NPI_SERVICE_DOWN"        # NPPES registry unreachable
    MANUAL_REVIEW_REQUIRED  = "MANUAL_REVIEW_REQUIRED"  # AI returned NO_RECORD


_REASON_TO_ALERT: Dict[FailureReason, AlertType] = {
    FailureReason.URL_ERROR:           AlertType.URL_CHANGED,
    FailureReason.UI_CHANGED:          AlertType.UI_STRUCTURE_CHANGED,
    FailureReason.CAPTCHA_BLOCKED:     AlertType.CAPTCHA_DETECTED,
    FailureReason.UNEXPECTED_RESPONSE: AlertType.UNEXPECTED_RESPONSE,
}

_ALERT_SUGGESTIONS: Dict[AlertType, str] = {
    AlertType.URL_CHANGED:
        "Verify the board portal URL. Update base_url in the board's config.yaml and re-test.",
    AlertType.UI_STRUCTURE_CHANGED:
        "Portal page loaded but field selectors no longer matched. "
        "Inspect the HTML and update the scraper's CSS/XPath selectors in config.yaml.",
    AlertType.PORTAL_DOWN:
        "All board portal URLs unreachable. Check network and portal maintenance schedule.",
    AlertType.CAPTCHA_DETECTED:
        "Increase request delays, rotate user-agents, or switch to manual-assisted flow.",
    AlertType.UNEXPECTED_RESPONSE:
        "Inspect raw response body and update the parser if the format has changed.",
    AlertType.AI_SERVICE_ERROR:
        "Anthropic API returned repeated connection errors. Verify ANTHROPIC_API_KEY, "
        "check quota/rate limits, and confirm network access to api.anthropic.com. "
        "AI fallback is disabled for the remainder of this run.",
    AlertType.NPI_SERVICE_DOWN:
        "NPPES NPI registry (npiregistry.cms.hhs.gov) was unreachable. "
        "NPI enrichment steps were skipped. Verify network access and retry.",
    AlertType.MANUAL_REVIEW_REQUIRED:
        "AI fallback exhausted all search steps and returned NO_RECORD. "
        "Review the evidence directory (screenshots + HTML) and complete verification manually.",
}


@dataclass
class PortalAlert:
    alert_type:       AlertType
    source_id:        str
    step_name:        str
    detail:           str
    affected_urls:    List[str] = field(default_factory=list)
    raised_at:        str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    suggested_action: str = ""

    def __post_init__(self) -> None:
        if not self.suggested_action:
            self.suggested_action = _ALERT_SUGGESTIONS.get(self.alert_type, "Investigate manually.")

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["alert_type"] = self.alert_type.value
        return d


class _NotificationLog:
    """Persists portal alerts to JSON; suppresses duplicates within 24 h."""

    def __init__(self, path: Path = NOTIFICATION_LOG_PATH) -> None:
        self._path = path

    def raise_alert(self, alert: PortalAlert) -> None:
        alerts = self._load()
        for existing in alerts:
            if (
                existing.get("alert_type") == alert.alert_type.value
                and existing.get("step_name") == alert.step_name
                and existing.get("source_id") == alert.source_id
            ):
                age_hours = (
                    datetime.now(timezone.utc)
                    - datetime.fromisoformat(existing["raised_at"])
                ).total_seconds() / 3600
                if age_hours < 24:
                    return
        alerts.append(alert.to_dict())
        self._save(alerts)
        log.warning(
            "[PORTAL ALERT] %s on %s / %s: %s | Action: %s",
            alert.alert_type.value, alert.source_id, alert.step_name,
            alert.detail, alert.suggested_action,
        )

    def get_recent(self, hours: float = 48) -> List[Dict]:
        cutoff = datetime.now(timezone.utc)
        return [
            a for a in self._load()
            if (cutoff - datetime.fromisoformat(a["raised_at"])).total_seconds() / 3600 <= hours
        ]

    def write_site_drift(
        self, source_id: str, run_id: str, drift_entries: List[Dict]
    ) -> None:
        """Append site-drift rows reported by Claude to the CSV report file."""
        if not drift_entries:
            return
        _DRIFT_FIELDS = [
            "timestamp", "source_id", "run_id",
            "step", "url", "observed_change", "suggested_config_fix",
        ]
        write_header = not SITE_DRIFT_REPORT_PATH.exists()
        try:
            with SITE_DRIFT_REPORT_PATH.open("a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=_DRIFT_FIELDS, extrasaction="ignore")
                if write_header:
                    writer.writeheader()
                ts = datetime.now(timezone.utc).isoformat()
                for entry in drift_entries:
                    writer.writerow({
                        "timestamp":            ts,
                        "source_id":            source_id,
                        "run_id":               run_id,
                        "step":                 entry.get("step", ""),
                        "url":                  entry.get("url", ""),
                        "observed_change":      entry.get("observed_change", ""),
                        "suggested_config_fix": entry.get("suggested_config_fix", ""),
                    })
            log.info(
                "[%s] Site drift: %d row(s) appended to %s",
                source_id, len(drift_entries), SITE_DRIFT_REPORT_PATH,
            )
        except Exception as exc:
            log.warning("[%s] Failed to write site_drift report: %s", source_id, exc)

    def _load(self) -> List[Dict]:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []

    def _save(self, alerts: List[Dict]) -> None:
        self._path.write_text(
            json.dumps(alerts, indent=2, ensure_ascii=False), encoding="utf-8"
        )


_notification_log = _NotificationLog()


def _raise_alerts_from_failures(
    failures: List[StepFailure], source_id: str, board_urls: List[str]
) -> List[PortalAlert]:
    raised: List[PortalAlert] = []
    for f in failures:
        if f.reason not in _PORTAL_ALERT_REASONS:
            continue
        alert = PortalAlert(
            alert_type=_REASON_TO_ALERT[f.reason],
            source_id=source_id,
            step_name=f.step_name,
            detail=f.detail or _REASON_DESCRIPTIONS[f.reason],
            affected_urls=board_urls,
        )
        _notification_log.raise_alert(alert)
        raised.append(alert)
    return raised


_AI_ALERT_TYPE_MAP: Dict[str, AlertType] = {
    "URL_CHANGED":          AlertType.URL_CHANGED,
    "UI_STRUCTURE_CHANGED": AlertType.UI_STRUCTURE_CHANGED,
    "PORTAL_DOWN":          AlertType.PORTAL_DOWN,
    "CAPTCHA_DETECTED":     AlertType.CAPTCHA_DETECTED,
    "UNEXPECTED_RESPONSE":  AlertType.UNEXPECTED_RESPONSE,
}


def _persist_ai_notifications(notifications: List[Dict], source_id: str) -> None:
    """Persist portal notifications returned inside Claude's FinalAnswer JSON."""
    for n in notifications:
        at_str = n.get("alert_type", "")
        at = _AI_ALERT_TYPE_MAP.get(at_str)
        if at is None:
            log.debug("[%s] AI returned unknown alert_type %r — skipping", source_id, at_str)
            continue
        alert = PortalAlert(
            alert_type       = at,
            source_id        = n.get("source_id", source_id),
            step_name        = "ai_reported",
            detail           = n.get("detail", "AI-reported portal alert"),
            affected_urls    = [n["affected_url"]] if n.get("affected_url") else [],
            suggested_action = n.get("suggested_action", ""),
        )
        _notification_log.raise_alert(alert)


# =============================================================================
# PROMPT BUILDER  (generic — works for any board/state)
# =============================================================================

_SYSTEM_PROMPT_TEMPLATE = """\
You are an expert PSV (Primary Source Verification) AI Agent for professional
license verification. You work across any US state licensing board.

Your mission: find and return the single most accurate license record for a
provider from the board's public verification portal. You are invoked only
when all prior automated rule-based steps have failed. You are the last
automated safeguard before the case goes to manual review.

You operate in one of two LAYERS. The layer is stated in the user message.

==============================================================================
SECTION 1 — AI LAYER MODES
==============================================================================

LAYER 1 — FETCHER
  Triggered when the rule-based ladder returned zero records, OR the secondary
  identity check failed on a previously scraped record.
  Your job: attempt every available search combination from scratch.
  Use NPI enrichment context to substitute NPI-derived values where the master
  data may differ from the board's record.

LAYER 2 — DISAMBIGUATOR
  Triggered when the rule-based ladder returned multiple candidates that
  auto-disambiguation could not reduce to one.
  Your job: do NOT re-run the full search ladder. Work through the
  DISAMBIGUATION LADDER (Section 2b) on the candidates already provided.
  Only fall back to live searches if the entire D-ladder is exhausted.

==============================================================================
SECTION 2a — SEARCH SEQUENCE (LAYER 1 only)
==============================================================================

Attempt in order. Skip a step ONLY if the required field is absent, and
document why.

  AI-1  SearchBoard  — license_id only
  AI-2  SearchBoard  — last_name + first_name + profession
  AI-3  SearchBoard  — last_name + first_name
  AI-4  SearchBoard  — last_name only
  AI-5  SearchBoard  — first_name only

  ** NPI SUBSTITUTION (if npi_discrepancy_found and not yet tried) **
  AI-4b SearchBoard  — nppes_last_name + nppes_first_name
  AI-1b SearchBoard  — license_id from NPPES (if NPPES has a different ID)

  AI-6  SearchNPPES  — npi only
  AI-7  SearchNPPES  — first_name + last_name + npi + state
  AI-8  SearchNPPES  — first_name + last_name + state
  AI-9  SearchNPPES  — last_name only
  AI-10 SearchNPPES  — first_name only

  ** OTHER-NAME STEPS (if npi_other_name_match) **
  AI-9b SearchNPPES  — other_name_last + other_name_first (per other name)

  AI-11 AnalyzeScreenshot — visually read each provided screenshot
  AI-12 ParseHTML          — parse each provided HTML file

For each search that returns candidates, apply NameMatch before accepting:
  Score ≥ 90  → qualifies for AUTO_SELECT.
  Score ≥ 80  → qualifies for BEST_MATCH.
  Score  < 80 → discard.

==============================================================================
SECTION 2b — DISAMBIGUATION LADDER (LAYER 2 only)
==============================================================================

Work through these tiers on the provided candidate set. Stop at first winner.

  D-1  Exact license ID match (case-insensitive) against master license_id.
  D-2  Exact name match (rules 3a–3b) against master first + last.
  D-3  Exact NPI match against master NPI (if NPI present).
  D-4  Fuzzy name score ≥ 90 (rules 3a–3c) — one candidate above floor.
  D-5  AnalyzeScreenshot / ParseHTML — visually compare candidates.
       If still ambiguous → return NO_RECORD with all candidates listed.

After every D-tier win, run the secondary identity check. If it fails,
do NOT accept the candidate; continue to the next tier. If all tiers fail
the secondary check → NO_RECORD.

==============================================================================
SECTION 3 — NAME MATCHING RULES
==============================================================================

3a. CASE — ignore letter case entirely.
3b. PUNCTUATION — strip hyphens, apostrophes, periods, extra spaces.
3c. LAST NAME WEIGHT — score = (0.6 × last_score) + (0.4 × first_score).
    Each individual score is 100 for exact match under 3a–3b, else 0.
3d. LICENSE ID OVERRIDE — exact license ID match (case-insensitive) accepts
    the record regardless of name score. Flag as BEST_MATCH if name < 80.

==============================================================================
SECTION 4 — NPI ENRICHMENT HANDLING
==============================================================================

If discrepancy_found = true:
  Master name differs from NPPES. Try NPI-substituted searches (AI-4b, AI-1b).
  Set npi_discrepancy_used = true in output.

If other_name_match = true:
  NPI matched only on an alternate/other name. Set npi_other_name_match = true.
  Flag the record for review.

If npi_substituted = true (already tried before AI fallback):
  Do not repeat those same queries. Focus on alternative combinations.

If lookup_failed = true:
  NPPES was unreachable. Skip all SearchNPPES steps. Raise a portal alert.

==============================================================================
SECTION 5 — SECONDARY IDENTITY CHECK
==============================================================================

Compare scraped first + last against master first + last (middle name skipped).
  check_passed = true  → selected record's names match master.
  check_passed = false → only acceptable under rule 3d (license ID override);
                          flag as BEST_MATCH and populate secondary_check_passed.

==============================================================================
SECTION 6 — PROVENANCE FLAGS  (all required in every FinalAnswer)
==============================================================================

match_method:
  "exact_license"          — matched by license ID alone (rule 3d)
  "exact_name"             — matched by name alone (rules 3a–3b, score 100)
  "npi_substituted_exact"  — match succeeded after substituting NPI values
  "ai_fuzzy"               — resolved with fuzzy score ≥ 90 but < 100
  "none"                   — no match; manual review required

npi_discrepancy_used  : true if NPI-derived names were used in any query.
npi_other_name_match  : true if NPI matched only on an alternate name.
npi_source_flag       : true if any NPI-derived field was injected into a query.
ai_fallback_used      : always true.
ai_layer              : 1 or 2.
manual_flag           : true when status = NO_RECORD.
secondary_check_passed: true/false per selected record.
fuzzy_score           : null on exact path; numeric 0–100 otherwise.
master_row_id         : echo from search criteria block.
evidence_dir          : echo from search criteria block.
source_id             : echo the board source_id from the search criteria.

==============================================================================
SECTION 7 — URL FAILURE AND PORTAL NOTIFICATIONS
==============================================================================

Classify any URL failure:
  URL_CHANGED          : HTTP 404, permanent redirect, domain not found.
  UI_STRUCTURE_CHANGED : Page loaded but expected elements absent.
  PORTAL_DOWN          : All URLs for this board unreachable simultaneously.
  CAPTCHA_DETECTED     : CAPTCHA challenge returned.
  UNEXPECTED_RESPONSE  : Maintenance page / wrong content type / parse error.

Add to notifications[] in FinalAnswer. Add to site_drift[] for structural
changes. Try all known board URLs before giving up. Never auto-apply config
changes — only suggest them in site_drift[].suggested_config_fix.

==============================================================================
SECTION 8 — SCREENSHOT AND HTML EVIDENCE PROTOCOL
==============================================================================

AnalyzeScreenshot — visually read the image:
  Extract every visible field: name, license number, profession, status,
  issue date, expiration date, address, NPI, discipline actions.
  Apply NameMatch to each name found.
  Record the screenshot path in evidence_sources.

ParseHTML — read the saved page source:
  Locate table rows, label-value pairs, dl/dt/dd, form fields.
  Map each to standard schema fields.
  Apply NameMatch to every name found.

==============================================================================
SECTION 9 — CONFIDENCE SCORING
==============================================================================

Base score from NameMatch (Section 3), then boosters (cap at 100):
  +5  Exact license ID match
  +3  Exact NPI match
  +2  Profession code matches
  +2  State matches
  +3  Record sourced from screenshot or HTML when live URL was unavailable

  ≥ 90  AUTO_SELECT  — commit to standard_output.
  ≥ 80  BEST_MATCH   — output with warning; manual verification advised.
  < 80  NO_RECORD    — escalate to manual review; include all candidates.

==============================================================================
SECTION 10 — AVAILABLE ACTIONS
==============================================================================

SearchBoard(license_id, first_name, last_name, profession, state, board_url)
  Search the board's public verification portal.
  Returns: list of candidate records, or an error identifying the failed URL.

SearchNPPES(npi, first_name, last_name, state)
  Query NPPES NPI Registry (https://npiregistry.cms.hhs.gov/api/?version=2.1).
  Returns: list of candidate records, or an error.

NameMatch(search_first, search_last, record_first, record_last)
  Compute name similarity using rules 3a–3c.
  Returns: score 0–100 and which rules fired.

SecondaryCheck(scraped_first, scraped_last, master_first, master_last)
  Verify scraped first + last match master.
  Returns: passed (bool), normalised names, mismatch detail.

AnalyzeScreenshot(screenshot_path, question)
  Visually read a screenshot and extract field values.

ParseHTML(html_path, fields)
  Read saved HTML and extract named fields.

FinalAnswer(json_object)
  Emit the final JSON result. Must be the very last action.

==============================================================================
SECTION 11 — PROHIBITED BEHAVIOURS
==============================================================================

  - Do NOT invent or fabricate license numbers, names, or dates.
  - Do NOT apply typo tolerance, abbreviation, or nickname matching.
    Only rules 3a–3d apply.
  - Do NOT declare NO_RECORD until ALL applicable steps are tried or skipped
    with a documented reason.
  - Do NOT skip NameMatch when a candidate name differs from the search.
  - Do NOT output anything other than the final JSON in FinalAnswer.
  - Do NOT assume a URL is dead without trying all known fallback URLs.
  - Do NOT suppress portal notifications.
  - Do NOT auto-apply config changes — only suggest in site_drift[].
  - Do NOT use NPI-derived values without setting npi_source_flag = true.

==============================================================================
SECTION 12 — OUTPUT JSON SCHEMA  (return exactly this in FinalAnswer)
==============================================================================

{{
  "status":     "AUTO_SELECT" | "BEST_MATCH" | "NO_RECORD",
  "confidence": <number 0–100>,

  "record": {{
    "license_id":      "<string or null>",
    "first_name":      "<string or null>",
    "last_name":       "<string or null>",
    "profession":      "<string or null>",
    "license_status":  "<string or null>",
    "issue_date":      "<string or null>",
    "expiration_date": "<string or null>",
    "npi":             "<string or null>",
    "state":           "<string or null>",
    "source_url":      "<URL or file path>",
    "source_type":     "BOARD_PORTAL" | "NPPES" | "SCREENSHOT" | "HTML"
  }} | null,

  "provenance": {{
    "match_method":           "exact_license" | "exact_name" | "npi_substituted_exact" | "ai_fuzzy" | "none",
    "npi_discrepancy_used":   <bool>,
    "npi_other_name_match":   <bool>,
    "npi_source_flag":        <bool>,
    "ai_fallback_used":       true,
    "ai_layer":               1 | 2,
    "manual_flag":            <bool>,
    "secondary_check_passed": <bool>,
    "fuzzy_score":            <number or null>,
    "master_row_id":          "<string or null>",
    "evidence_dir":           "<path or null>",
    "source_id":              "<board source_id>"
  }},

  "candidates": [
    {{
      "license_id":     "<string or null>",
      "first_name":     "<string or null>",
      "last_name":      "<string or null>",
      "profession":     "<string or null>",
      "license_status": "<string or null>",
      "npi":            "<string or null>",
      "confidence":     <number 0–100>,
      "source_url":     "<string or null>",
      "source_type":    "BOARD_PORTAL" | "NPPES" | "SCREENSHOT" | "HTML"
    }}
  ],

  "failure_analysis": {{
    "summary":  "<why the rule-based cascade failed overall>",
    "per_step": {{
      "STEP_NAME": "<reason and implication>",
      "...": "..."
    }}
  }},

  "notifications": [
    {{
      "alert_type":       "URL_CHANGED" | "UI_STRUCTURE_CHANGED" | "PORTAL_DOWN" | "CAPTCHA_DETECTED" | "UNEXPECTED_RESPONSE",
      "source_id":        "<board source_id>",
      "affected_url":     "<string>",
      "detail":           "<what was observed>",
      "suggested_action": "<what the dev team should do>"
    }}
  ],

  "site_drift": [
    {{
      "step":                 "<AI-N or D-N where drift was observed>",
      "url":                  "<the URL that exhibited the change>",
      "observed_change":      "<what was different>",
      "suggested_config_fix": "<what the scraper engineer should change in config.yaml>"
    }}
  ],

  "reasoning":          "<step-by-step account of every decision>",
  "name_match_details": "<which rules (3a–3d) fired and score breakdown>",
  "ai_steps_completed": ["AI-1", "..."],
  "ai_steps_skipped":   {{"AI-N": "<reason>"}},
  "url_fallbacks_used": ["<url>"],
  "evidence_sources":   ["<screenshot or html path>"]
}}
"""

_USER_PROMPT_TEMPLATE = """\
==============================================================================
MASTER ROW IDENTIFIER
==============================================================================
  master_row_id : {master_row_id}
  evidence_dir  : {evidence_dir}

==============================================================================
BOARD / SOURCE CONTEXT
==============================================================================
  source_id     : {source_id}
  board_name    : {board_name}
  state         : {state}
  board_url     : {board_url}

==============================================================================
AI LAYER AND INVOCATION MODE
==============================================================================
  layer         : {ai_layer_value}
  reason        : {ai_layer_reason}

==============================================================================
SEARCH CRITERIA (master row values)
==============================================================================
{search_criteria_json}

==============================================================================
NPI ENRICHMENT RESULTS
==============================================================================
{npi_block}

==============================================================================
SECONDARY IDENTITY CHECK RESULT
==============================================================================
{secondary_block}

==============================================================================
RULE-BASED STEPS ALREADY ATTEMPTED
==============================================================================
{steps_block}

==============================================================================
FAILURE SUMMARY
==============================================================================
{failure_summary}

{candidates_block}\
==============================================================================
EVIDENCE AVAILABLE
==============================================================================
{evidence_block}

==============================================================================
PORTAL ALERTS RAISED BY RULE-BASED STEPS
==============================================================================
{alerts_block}

==============================================================================
INSTRUCTIONS
==============================================================================
{layer_instructions}

Use the ReAct format for every action:

  Thought    : <reason about what to try next>
  Action     : <ActionName(params)>
  Observation: <what the action returned>

Repeat until confident, then emit:

  FinalAnswer: {{ ... }}

Requirements:
  - Populate ALL provenance flags in the "provenance" block.
  - Raise a portal notification for every URL or UI anomaly.
  - Set match_method to exactly one allowed value.
  - Run SecondaryCheck before accepting any candidate as the final record.
  - Include failure_analysis.per_step for every rule-based step that failed.

Begin your ReAct chain now.
"""

_LAYER1_INSTRUCTIONS = """\
You are in LAYER 1 — FETCHER mode.
  - Work through AI-1 to AI-12 as defined in Section 2a.
  - If npi_discrepancy_found, include the NPI-substituted steps (AI-4b, AI-1b).
  - If npi_other_name_match, include other-name NPPES steps (AI-9b).
  - Apply NameMatch to every candidate. Run SecondaryCheck before accepting.
  - Use screenshot / HTML evidence if live URLs fail.
  - You must attempt or explicitly skip every AI step before giving NO_RECORD.\
"""

_LAYER2_INSTRUCTIONS = """\
You are in LAYER 2 — DISAMBIGUATOR mode.
  - Do NOT re-run the full search ladder.
  - Work through D-1 → D-5 on the candidate set in the CANDIDATES block.
  - Apply NameMatch to each candidate at every tier.
  - Run SecondaryCheck before accepting any candidate as the final record.
  - Only fall back to live searches if the entire D-1→D-5 ladder is exhausted.\
"""


class _ReActPromptBuilder:
    """
    Assembles the system + user prompt for a generic PSV ReAct AI fallback.

    Parameters
    ----------
    source_id       : Board identifier (e.g. "NV_MEDBOARD")
    board_name      : Human-readable board name (e.g. "Nevada State Medical Board")
    state           : Two-letter state code
    board_url       : Primary verification portal URL from config.yaml
    master_row_id   : Input row identifier for audit correlation
    license_id      : License number being verified
    first_name      : Provider first name from master row
    last_name       : Provider last name from master row
    profession      : Profession code or description
    npi             : 10-digit NPI (optional)
    evidence_dir    : Path to the per-row evidence folder
    ai_layer        : LAYER_1_FETCHER or LAYER_2_DISAMBIGUATOR
    npi_result      : NPIEnrichmentResult (optional)
    secondary_check : SecondaryCheckResult (optional)
    step_failures   : List[StepFailure] from the rule-based cascade
    screenshots     : Paths to screenshot images
    html_files      : Paths to saved HTML files
    field_map       : {raw_column: canonical_field} from config.yaml
    """

    def __init__(
        self,
        source_id:       str                             = "",
        board_name:      str                             = "",
        state:           str                             = "",
        board_url:       str                             = "",
        master_row_id:   str                             = "",
        license_id:      str                             = "",
        first_name:      str                             = "",
        last_name:       str                             = "",
        profession:      str                             = "",
        npi:             str                             = "",
        evidence_dir:    str                             = "",
        ai_layer:        AILayer                         = AILayer.LAYER_1_FETCHER,
        npi_result:      Optional[NPIEnrichmentResult]   = None,
        secondary_check: Optional[SecondaryCheckResult]  = None,
        step_failures:   Optional[List[StepFailure]]     = None,
        screenshots:     Optional[List[str]]             = None,
        html_files:      Optional[List[str]]             = None,
        field_map:       Optional[Dict[str, str]]        = None,
    ) -> None:
        self.source_id       = source_id
        self.board_name      = board_name or source_id
        self.state           = state
        self.board_url       = board_url
        self.master_row_id   = master_row_id
        self.license_id      = license_id
        self.first_name      = first_name
        self.last_name       = last_name
        self.profession      = profession
        self.npi             = npi
        self.evidence_dir    = evidence_dir
        self.ai_layer        = ai_layer
        self.npi_result      = npi_result      or NPIEnrichmentResult()
        self.secondary_check = secondary_check or SecondaryCheckResult()
        self.step_failures   = step_failures   or []
        self.screenshots     = screenshots     or []
        self.html_files      = html_files      or []
        self.field_map       = field_map       or {}
        self._alerts = _raise_alerts_from_failures(
            self.step_failures, self.source_id, [self.board_url] if self.board_url else []
        )
        if self.npi_result.lookup_failed and self.source_id:
            _notification_log.raise_alert(PortalAlert(
                alert_type    = AlertType.NPI_SERVICE_DOWN,
                source_id     = self.source_id,
                step_name     = "nppes_enrichment",
                detail        = (
                    "NPPES NPI registry was unreachable during this verification run. "
                    "NPI enrichment steps were skipped."
                ),
                affected_urls = ["https://npiregistry.cms.hhs.gov/api/"],
            ))

    def build(self) -> Dict[str, str]:
        return {"system": _SYSTEM_PROMPT_TEMPLATE.format(), "user": self._build_user()}

    @staticmethod
    def _esc(s: str) -> str:
        return s.replace("{", "{{").replace("}", "}}")

    def _build_user(self) -> str:
        criteria = {
            "source_id":  self.source_id  or None,
            "state":      self.state      or None,
            "license_id": self.license_id or None,
            "first_name": self.first_name or None,
            "last_name":  self.last_name  or None,
            "profession": self.profession or None,
            "npi":        self.npi        or None,
        }
        if self.field_map:
            criteria["output_fields"] = list(set(self.field_map.values()))

        ai_layer_reason = (
            "Both the rule-based ladder and NPPES enrichment returned zero records, "
            "OR the secondary identity check failed on a scraped record."
            if self.ai_layer == AILayer.LAYER_1_FETCHER else
            "The rule-based ladder returned multiple candidates that "
            "auto-disambiguation could not reduce to one."
        )

        # NPI block
        n = self.npi_result
        npi_lines = [
            f"  npi                  : {n.npi or '(not provided)'}",
            f"  nppes_first_name     : {n.nppes_first_name or '(not found)'}",
            f"  nppes_last_name      : {n.nppes_last_name or '(not found)'}",
            f"  nppes_credential     : {n.nppes_credential or '(not found)'}",
            f"  nppes_state          : {n.nppes_state or '(not found)'}",
            f"  discrepancy_found    : {n.discrepancy_found}",
        ]
        if n.discrepancy_found and n.discrepancy_detail:
            npi_lines.append(f"  discrepancy_detail   : {n.discrepancy_detail}")
        npi_lines.append(f"  npi_substituted      : {n.npi_substituted}")
        if n.npi_substituted and n.npi_substituted_result:
            npi_lines.append(f"  substitution_result  : {n.npi_substituted_result}")
        npi_lines.append(f"  other_name_match     : {n.other_name_match}")
        if n.other_names:
            npi_lines.append(f"  other_names          : {'; '.join(n.other_names)}")
        npi_lines.append(f"  lookup_failed        : {n.lookup_failed}")
        if n.lookup_failed:
            npi_lines.append("  [PORTAL ALERT] NPPES was unreachable. Do not call SearchNPPES.")

        # Secondary check block
        sc = self.secondary_check
        if sc.check_passed:
            secondary_block = "  check_passed : true  (secondary check was not the reason for AI fallback)"
        else:
            secondary_block = (
                f"  check_passed  : false\n"
                f"  scraped_first : {sc.scraped_first or '(unknown)'}\n"
                f"  scraped_last  : {sc.scraped_last or '(unknown)'}\n"
                f"  master_first  : {sc.master_first or self.first_name}\n"
                f"  master_last   : {sc.master_last or self.last_name}\n"
                f"  fail_reason   : {sc.fail_reason or 'Scraped name did not match master name'}\n"
                f"  ACTION REQUIRED: Find a record where scraped first+last MATCH master first+last."
            )

        # Steps block
        if self.step_failures:
            steps_lines = [f.to_prompt_line() for f in self.step_failures]
            for f in self.step_failures:
                if f.candidates:
                    steps_lines.append(
                        f"\n  Candidates from {f.step_name}:\n"
                        + json.dumps(f.candidates, indent=4)
                    )
            steps_block = "\n".join(steps_lines)
        else:
            steps_block = "  (no rule-based steps recorded)"

        # Failure summary
        failure_summary = self._build_failure_summary()

        # Disambiguation candidates (Layer 2)
        candidates_block = ""
        if self.ai_layer == AILayer.LAYER_2_DISAMBIGUATOR:
            all_cands: List[Dict] = []
            for f in self.step_failures:
                if f.candidates:
                    all_cands.extend(f.candidates)
            if all_cands:
                candidates_block = (
                    "==============================================================================\n"
                    "DISAMBIGUATION CANDIDATES (work through D-1→D-5 on these)\n"
                    "==============================================================================\n"
                    + json.dumps(all_cands, indent=2) + "\n\n"
                )

        # Evidence block
        ev_lines: List[str] = []
        if self.screenshots:
            ev_lines.append("  Screenshots (use AnalyzeScreenshot for each):")
            for p in self.screenshots:
                ev_lines.append(f"    - {p}")
        if self.html_files:
            ev_lines.append("  HTML files (use ParseHTML for each):")
            for p in self.html_files:
                ev_lines.append(f"    - {p}")
        if not ev_lines:
            ev_lines.append("  None provided — rely on live URL searches only.")

        # Alerts block
        if self._alerts:
            alert_lines = [
                f"  [{a.alert_type.value}] {a.source_id} / {a.step_name}: {a.detail}\n"
                f"    -> {a.suggested_action}"
                for a in self._alerts
            ]
            alerts_block = "\n".join(alert_lines)
        else:
            alerts_block = "  None raised during rule-based steps."

        layer_instructions = (
            _LAYER1_INSTRUCTIONS if self.ai_layer == AILayer.LAYER_1_FETCHER
            else _LAYER2_INSTRUCTIONS
        )

        esc = self._esc
        return _USER_PROMPT_TEMPLATE.format(
            master_row_id        = self.master_row_id or "(not set)",
            evidence_dir         = self.evidence_dir  or "(not set)",
            source_id            = self.source_id     or "(not set)",
            board_name           = self.board_name    or "(not set)",
            state                = self.state         or "(not set)",
            board_url            = self.board_url     or "(not set)",
            ai_layer_value       = self.ai_layer.value,
            ai_layer_reason      = ai_layer_reason,
            search_criteria_json = esc(json.dumps(criteria, indent=4)),
            npi_block            = "\n".join(npi_lines),
            secondary_block      = secondary_block,
            steps_block          = esc(steps_block),
            failure_summary      = failure_summary,
            candidates_block     = esc(candidates_block),
            evidence_block       = "\n".join(ev_lines),
            alerts_block         = alerts_block,
            layer_instructions   = layer_instructions,
        )

    def _build_failure_summary(self) -> str:
        if not self.step_failures:
            return "  No structured failure data provided."
        counts: Dict[str, int] = {}
        portal_issues: List[str] = []
        for f in self.step_failures:
            counts[f.reason.value] = counts.get(f.reason.value, 0) + 1
            if f.reason in _PORTAL_ALERT_REASONS:
                portal_issues.append(f.step_name)
        lines = ["  Failure reason distribution:"]
        for rv, cnt in sorted(counts.items()):
            lines.append(f"    {rv}: {cnt} step(s)")
        if portal_issues:
            lines.append(f"  PORTAL ISSUES on: {', '.join(portal_issues)}")
            lines.append("  -> Prioritise screenshot / HTML evidence for these steps.")
        zero = [f.step_name for f in self.step_failures if f.reason == FailureReason.ZERO_RECORDS]
        multi = [f.step_name for f in self.step_failures if f.reason == FailureReason.MULTIPLE_UNRESOLVED]
        sec   = [f.step_name for f in self.step_failures if f.reason == FailureReason.SECONDARY_CHECK_FAIL]
        if zero:
            lines.append(
                f"  ZERO_RECORDS on: {', '.join(zero)} — "
                "criteria may be too narrow or name listed differently on the board."
            )
        if multi:
            lines.append(
                f"  MULTIPLE_UNRESOLVED on: {', '.join(multi)} — "
                "use NPI, profession, or visual evidence to narrow down."
            )
        if sec:
            lines.append(
                f"  SECONDARY_CHECK_FAIL on: {', '.join(sec)} — "
                "record was found but scraped name did not match master name."
            )
        return "\n".join(lines)


# =============================================================================
# SIMPLE HTML-ONLY PROMPT  (used when no structured search context available)
# =============================================================================

def _build_simple_prompt(html: str, field_map: Dict[str, str]) -> str:
    """Minimal extraction prompt for the basic extract_with_ai path."""
    canonical_fields = sorted(set(field_map.values()))
    fields_str = ", ".join(canonical_fields)
    return (
        "You are a professional license record extractor.\n"
        f"Extract the following fields from the HTML below: {fields_str}\n\n"
        "Return a JSON object with exactly these keys. Use null for missing values. "
        "Return JSON only — no explanation text.\n\n"
        f"HTML:\n{html[:_MAX_HTML_CHARS]}"
    )


# =============================================================================
# PUBLIC API
# =============================================================================

async def extract_with_ai(
    html: str,
    field_map: Dict[str, str],
    source_id: str,
    run_id: str,
    db: Any | None = None,
    # Optional ReAct context — when provided, uses the full structured prompt
    react_context: Optional[Dict] = None,
) -> Dict:
    """
    Call Claude Opus to extract license fields.

    Parameters
    ----------
    html          : Raw page HTML (truncated to _MAX_HTML_CHARS if needed)
    field_map     : {raw_col: canonical_field} mapping from config.yaml
    source_id     : Board identifier (e.g. "NV_MEDBOARD")
    run_id        : Current run identifier for telemetry
    db            : aiosqlite connection for telemetry (optional)
    react_context : If provided, builds a full ReAct prompt instead of the
                    simple extraction prompt. Dict keys:
                      board_name, state, board_url, master_row_id, license_id,
                      first_name, last_name, profession, npi, evidence_dir,
                      ai_layer (AILayer), npi_result (NPIEnrichmentResult),
                      secondary_check (SecondaryCheckResult),
                      step_failures (List[StepFailure]),
                      screenshots (List[str]), html_files (List[str])

    Returns
    -------
    dict with extracted fields + _used_ai flag.
    """
    global _consecutive_errors, _circuit_open

    if not _CLAUDE_AVAILABLE or _client is None:
        log.debug("[%s] AI fallback skipped — Anthropic API not configured", source_id)
        return {"_used_ai": False}

    if _circuit_open:
        log.debug("[%s] AI fallback skipped — circuit breaker open", source_id)
        return {"_used_ai": False}

    # Build prompt
    if react_context:
        builder = _ReActPromptBuilder(
            source_id       = source_id,
            board_name      = react_context.get("board_name", source_id),
            state           = react_context.get("state", ""),
            board_url       = react_context.get("board_url", ""),
            master_row_id   = react_context.get("master_row_id", ""),
            license_id      = react_context.get("license_id", ""),
            first_name      = react_context.get("first_name", ""),
            last_name       = react_context.get("last_name", ""),
            profession      = react_context.get("profession", ""),
            npi             = react_context.get("npi", ""),
            evidence_dir    = react_context.get("evidence_dir", ""),
            ai_layer        = react_context.get("ai_layer", AILayer.LAYER_1_FETCHER),
            npi_result      = react_context.get("npi_result"),
            secondary_check = react_context.get("secondary_check"),
            step_failures   = react_context.get("step_failures"),
            screenshots     = react_context.get("screenshots"),
            html_files      = react_context.get("html_files"),
            field_map       = field_map,
        )
        prompt_parts = builder.build()
        system_prompt = prompt_parts["system"]
        user_content  = prompt_parts["user"]
    else:
        system_prompt = (
            "You are a professional license record extractor. "
            "Extract structured data from HTML. Return JSON only."
        )
        user_content = _build_simple_prompt(html, field_map)

    try:
        response = await _client.messages.create(
            model      = _ANTHROPIC_MODEL,
            max_tokens = _MAX_TOKENS_RESPONSE,
            system     = system_prompt,
            messages   = [{"role": "user", "content": user_content}],
        )
        _consecutive_errors = 0
        raw_text = response.content[0].text if response.content else "{}"
        usage    = response.usage

        # Log telemetry
        if db is not None:
            from .telemetry import log_ai_touchpoint
            await log_ai_touchpoint(
                db=db,
                run_id=run_id,
                source_id=source_id,
                stage="detail_extraction",
                prompt_tokens=usage.input_tokens if usage else 0,
                completion_tokens=usage.output_tokens if usage else 0,
                model=_ANTHROPIC_MODEL,
            )

        # Parse JSON from response
        start = raw_text.find("{")
        end   = raw_text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                extracted = json.loads(raw_text[start:end])
            except json.JSONDecodeError as jde:
                log.warning("[%s] AI response JSON malformed: %s", source_id, jde)
                _notification_log.raise_alert(PortalAlert(
                    alert_type = AlertType.UNEXPECTED_RESPONSE,
                    source_id  = source_id,
                    step_name  = "extract_with_ai",
                    detail     = f"Claude response JSON was malformed (parse error: {jde}).",
                ))
                extracted = {}
        else:
            log.warning("[%s] AI response contained no parseable JSON", source_id)
            _notification_log.raise_alert(PortalAlert(
                alert_type = AlertType.UNEXPECTED_RESPONSE,
                source_id  = source_id,
                step_name  = "extract_with_ai",
                detail     = "Claude response contained no JSON object — output may have been truncated.",
            ))
            extracted = {}

        # Flatten the ReAct FinalAnswer record fields into the top-level dict.
        # Apply field aliases so AI schema names map to pipeline canonical names.
        _AI_FIELD_ALIASES = {
            "license_id":     "license_number",
            "license_status": "status",
            "npi":            "npi",          # same — listed for clarity
        }
        if react_context and "record" in extracted and isinstance(extracted["record"], dict):
            for k, v in extracted["record"].items():
                canonical = _AI_FIELD_ALIASES.get(k, k)
                if canonical not in extracted:
                    extracted[canonical] = v
                if k not in extracted:
                    extracted[k] = v
            # Preserve full ReAct output for downstream inspection
            extracted["_react_output"] = extracted.copy()

        # ── Persist portal notifications from Claude's FinalAnswer ────────────
        if react_context and extracted:
            _persist_ai_notifications(
                extracted.get("notifications") or [], source_id
            )
            _notification_log.write_site_drift(
                source_id, run_id, extracted.get("site_drift") or []
            )
            if extracted.get("status") == "NO_RECORD":
                _fa = extracted.get("failure_analysis") or {}
                _summary = (
                    _fa.get("summary", "") if isinstance(_fa, dict) else ""
                )
                _notification_log.raise_alert(PortalAlert(
                    alert_type = AlertType.MANUAL_REVIEW_REQUIRED,
                    source_id  = source_id,
                    step_name  = "extract_with_ai",
                    detail     = (
                        f"AI returned NO_RECORD for {source_id}. "
                        + (_summary or "No failure summary provided.")
                    ),
                ))

        log.info("[%s] AI fallback extracted %d fields", source_id, len(extracted))
        extracted["_used_ai"]    = True
        extracted["_ai_model"]   = _ANTHROPIC_MODEL
        return extracted

    except Exception as e:
        _consecutive_errors += 1
        log.error("[%s] AI fallback failed: %s", source_id, e)
        if _consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
            _circuit_open = True
            log.warning(
                "AI fallback circuit breaker OPEN after %d consecutive errors — "
                "skipping AI for remainder of run", _consecutive_errors
            )
            _notification_log.raise_alert(PortalAlert(
                alert_type = AlertType.AI_SERVICE_ERROR,
                source_id  = source_id,
                step_name  = "extract_with_ai",
                detail     = (
                    f"Anthropic API failed {_consecutive_errors} consecutive time(s) "
                    f"(last error: {str(e)[:200]}). AI fallback disabled for this run."
                ),
            ))
        return {"_used_ai": False}


def should_use_ai_fallback(raw: dict) -> bool:
    """Return True if rule-based extraction produced too few meaningful fields."""
    meaningful = {
        k: v for k, v in raw.items()
        if k and not k.startswith("_") and v and str(v).strip()
    }
    return len(meaningful) < _MIN_FIELDS_THRESHOLD


def build_react_prompt(
    source_id:       str                             = "",
    board_name:      str                             = "",
    state:           str                             = "",
    board_url:       str                             = "",
    master_row_id:   str                             = "",
    license_id:      str                             = "",
    first_name:      str                             = "",
    last_name:       str                             = "",
    profession:      str                             = "",
    npi:             str                             = "",
    evidence_dir:    str                             = "",
    ai_layer:        AILayer                         = AILayer.LAYER_1_FETCHER,
    npi_result:      Optional[NPIEnrichmentResult]   = None,
    secondary_check: Optional[SecondaryCheckResult]  = None,
    step_failures:   Optional[List[StepFailure]]     = None,
    screenshots:     Optional[List[str]]             = None,
    html_files:      Optional[List[str]]             = None,
    field_map:       Optional[Dict[str, str]]        = None,
) -> Dict[str, str]:
    """
    One-call wrapper that returns {'system': str, 'user': str}.

    Useful for inspection / testing without actually calling the API.
    """
    return _ReActPromptBuilder(
        source_id=source_id, board_name=board_name, state=state,
        board_url=board_url, master_row_id=master_row_id,
        license_id=license_id, first_name=first_name, last_name=last_name,
        profession=profession, npi=npi, evidence_dir=evidence_dir,
        ai_layer=ai_layer, npi_result=npi_result, secondary_check=secondary_check,
        step_failures=step_failures, screenshots=screenshots,
        html_files=html_files, field_map=field_map,
    ).build()


# =============================================================================
# SMOKE TEST  —  python -m engine.ai_fallback
# =============================================================================

if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    sep = "=" * 78

    # ── Layer 1 — generic board (NV_MEDBOARD) ────────────────────────────────
    prompt_l1 = build_react_prompt(
        source_id      = "NV_MEDBOARD",
        board_name     = "Nevada State Board of Medical Examiners",
        state          = "NV",
        board_url      = "https://medboard.nv.gov/public/Lookup",
        master_row_id  = "ROW-0042",
        license_id     = "OS00007",
        first_name     = "William",
        last_name      = "Smith",
        profession     = "DO",
        npi            = "1234567890",
        evidence_dir   = "Evidence/2026-06/NV/NV_MEDBOARD/20260622_1121_Smith",
        ai_layer       = AILayer.LAYER_1_FETCHER,
        npi_result     = NPIEnrichmentResult(
            npi               = "1234567890",
            nppes_first_name  = "WILLIAM",
            nppes_last_name   = "SMITH",
            nppes_credential  = "DO",
            nppes_state       = "NV",
            discrepancy_found = False,
        ),
        secondary_check = SecondaryCheckResult(
            check_passed  = False,
            scraped_first = "BILL",
            scraped_last  = "SMITH",
            master_first  = "WILLIAM",
            master_last   = "SMITH",
            fail_reason   = "Scraped first name BILL did not match master WILLIAM",
        ),
        step_failures = [
            StepFailure("STEP1_LICENSE",        FailureReason.ZERO_RECORDS),
            StepFailure("STEP2_NAME_PROFESSION", FailureReason.URL_ERROR,
                        detail="HTTP 404 — portal may have changed URL"),
            StepFailure("STEP3_NAME_ONLY",       FailureReason.SECONDARY_CHECK_FAIL,
                        records_found=1,
                        detail="Scraped 'BILL SMITH' vs master 'WILLIAM SMITH'"),
        ],
        screenshots = ["Evidence/2026-06/NV/NV_MEDBOARD/20260622_1121_Smith/search_results.png"],
        html_files  = ["Evidence/2026-06/NV/NV_MEDBOARD/20260622_1121_Smith/search_results.html"],
        field_map   = {"LicNum": "license_id", "Name": "full_name", "Status": "license_status"},
    )

    # ── Layer 2 — disambiguation (SD_PT) ─────────────────────────────────────
    prompt_l2 = build_react_prompt(
        source_id     = "SD_PT",
        board_name    = "South Dakota Board of Physical Therapy",
        state         = "SD",
        board_url     = "https://doh.sd.gov/boards/physical-therapy/",
        master_row_id = "ROW-0099",
        license_id    = "247",
        first_name    = "Maria",
        last_name     = "Garcia",
        profession    = "PT",
        ai_layer      = AILayer.LAYER_2_DISAMBIGUATOR,
        npi_result    = NPIEnrichmentResult(
            npi               = "9876543210",
            nppes_last_name   = "GARCIA-LOPEZ",
            discrepancy_found = True,
            discrepancy_detail= "NPPES last name GARCIA-LOPEZ differs from master GARCIA",
        ),
        step_failures = [
            StepFailure("STEP3_NAME_ONLY", FailureReason.MULTIPLE_UNRESOLVED,
                        records_found=2,
                        candidates=[
                            {"license_id": "247", "first_name": "MARIA",
                             "last_name": "GARCIA", "status": "Active"},
                            {"license_id": "312", "first_name": "MARIA",
                             "last_name": "GARCIA", "status": "Inactive"},
                        ]),
        ],
    )

    print(sep)
    print("LAYER 1 — SYSTEM PROMPT  (NV_MEDBOARD)")
    print(sep)
    print(prompt_l1["system"])

    print()
    print(sep)
    print("LAYER 1 — USER PROMPT")
    print(sep)
    print(prompt_l1["user"])

    print()
    print(sep)
    print("LAYER 2 — USER PROMPT (SD_PT, system omitted for brevity)")
    print(sep)
    print(prompt_l2["user"])

    print()
    print(sep)
    sys_len = len(prompt_l1["system"])
    u1_len  = len(prompt_l1["user"])
    u2_len  = len(prompt_l2["user"])
    print(f"Layer 1 system  : {sys_len:,} chars")
    print(f"Layer 1 user    : {u1_len:,} chars  |  total: {sys_len + u1_len:,}")
    print(f"Layer 2 user    : {u2_len:,} chars")
    print()
    print(f"Anthropic model : {_ANTHROPIC_MODEL}")
    print(f"Claude available: {_CLAUDE_AVAILABLE}")
    print()
    print(sep)
    print("PORTAL NOTIFICATIONS LOG (last 48h)")
    print(sep)
    recent = _notification_log.get_recent(hours=48)
    print(json.dumps(recent, indent=2) if recent else "  (none)")
