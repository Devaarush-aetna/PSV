# PSV_DEV — Professional State Verification (License Verification Engine)

## Table of Contents

1. [Overview](#1-overview)
2. [Quick Start — Run PSV Verification](#2-quick-start--run-psv-verification)
3. [Input Format](#3-input-format)
4. [Output Format](#4-output-format)
5. [Architecture](#5-architecture)
6. [Orchestration Layer](#6-orchestration-layer)
7. [AI Fallback — Two Layers](#7-ai-fallback--two-layers)
8. [NPPES Enrichment](#8-nppes-enrichment)
9. [AddLicense Output Channel](#9-addlicense-output-channel)
10. [Routing Table](#10-routing-table)
11. [Rule-Based Search Strategy](#11-rule-based-search-strategy)
12. [Proxy Configuration](#12-proxy-configuration)
13. [State Coverage & Sprint Plan](#13-state-coverage--sprint-plan)
14. [Single-State Testing](#14-single-state-testing)
15. [Scraper Engine Reference](#15-scraper-engine-reference)
16. [Adding a New Board to Routing](#16-adding-a-new-board-to-routing)
17. [Troubleshooting](#17-troubleshooting)

---

## 1. Overview

PSV_DEV verifies professional licenses against state licensing board websites.
Given a spreadsheet of providers (name, license number, state, provider type, NPI), it
queries each applicable board, confirms the license exists and the name matches, and
writes a Pass/Fail result with the license expiry date, a specific failure reason, and
a structured AddLicense record for every Pass row.

```
Input.xlsx (PSV Tab sheet)
    │
    ▼
run_psv.py  ─── groups by state ──▶ run_state_orchestrated()
                                          │
                                          ├── board_routing_master.csv
                                          ├── NPPES enrichment (per row, before ladder)
                                          ├── Rule-based ladder (boards × rungs)
                                          ├── AI Agent fallback (Layer 1 or 2)
                                          └── 5-channel output
                                                  ├── Output/standard/   ← Pass + Fail (Excel + CSV)
                                                  ├── Output/nppes/      ← NPPES record per row
                                                  ├── Output/ai_fallback/← AI agent rows + reasons
                                                  ├── Output/manual/     ← Unresolved rows
                                                  └── Output/add_license/← EPDB upload file (Pass only)
```

**Key design decisions:**
- One input file (`Input.xlsx`) covers all states — no per-state commands needed
- NPPES is fetched universally for every row before any board search (30-day cache)
- The rule-based ladder tries boards × rungs with dedup guard; no duplicate queries
- AI fallback (Layer 1 / Layer 2) is invoked only when the ladder exhausts — uses Anthropic Claude
- Proxy is auto-configured from `psv_config.yaml`; each board config can override it per-board
- CAPTCHA-blocked states (CA, GA, TN, UT, IA, NE, MT) are written as Fail immediately

---

## 2. Quick Start — Run PSV Verification

### Prerequisites

```bash
pip install -r lvs/adapters/scrapers/requirements.txt
playwright install chromium
```

Set your Anthropic API key (required for AI fallback):

```bash
# In PSV_DEV/.env:
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-6    # optional override
```

### Prepare input

Copy your provider spreadsheet to `PSV_DEV/` and rename it `Input.xlsx`.
The sheet must be named **PSV Tab** and follow the column layout in [Section 3](#3-input-format).

```bash
cp "license_details_Jun.xlsx" Input.xlsx
```

### Run all states

```bash
cd C:\Users\n661685\PSV_DEV
python run_psv.py
```

Outputs are written to `PSV_DEV/Output/{channel}/{YYYY-MM}/{run_id}.{ext}`.

### Common options

```bash
# Run only specific states
python run_psv.py --states NV MD FL

# Limit rows (smoke test / sampling)
python run_psv.py --states NV --max-rows 20

# Skip the first N rows (resume after partial run)
python run_psv.py --states OH --skip-rows 50

# Custom input file
python run_psv.py --input path/to/license_details_Jun.xlsx

# Disable AI agent (rule-based + NPPES only)
python run_psv.py --no-ai

# Disable NPPES fetch (rule-based only)
python run_psv.py --no-nppes

# Force AI agent even when rule ladder succeeds (test/audit mode)
python run_psv.py --force-ai

# Mock AI responses for testing without API access
python run_psv.py --ai-mock orchestrator/test_data/ai_mock.json

# Legacy single-Excel output (backwards compatible)
python run_psv.py --legacy-output
```

### CLI flags

| Flag | Default | Description |
|------|---------|-------------|
| `--input` | `PSV_DEV/Input.xlsx` | Input Excel file |
| `--states ST [ST ...]` | all | Limit to specific state abbreviations |
| `--batch-size N` | 10 | Rows processed per batch |
| `--timeout N` | 45 | Per-board search timeout in seconds |
| `--sequential` | off | Process rows one at a time |
| `--skip-rows N` | 0 | Skip the first N rows of input |
| `--max-rows N` | 0 (all) | Stop after N rows total |
| `--sheet NAME` | PSV Tab | Sheet name to read |
| `--no-ai` | off | Skip the AI agent fallback entirely |
| `--no-nppes` | off | Skip the universal NPPES fetch |
| `--force-ai` | off | Run AI agent even when rule ladder resolves |
| `--ai-mock PATH` | none | JSON file of canned AI responses (CI/testing) |
| `--legacy-output` | off | Write legacy `PSV_Output_*.xlsx` instead of 5-channel layout |

---

## 3. Input Format

The input Excel must have a sheet named **PSV Tab** (or detected automatically).
The engine reads these columns by **position** (0-based index):

| Index | Column | Field | Example |
|-------|--------|-------|---------|
| 0 | First Name | `first_name` | `John` |
| 1 | Middle Name | `middle_name` | `R` |
| 2 | Last Name | `last_name` | `Smith` |
| 3 | EPDB PIN | `epdb_pin` | `E12345` |
| 4 | Provider Type | `prov_type` | `MD` |
| 7 | Maintained By | `maintained_by` | `CVS Health` |
| 9 | License State | `lic_state` | `NV` |
| 10 | License Type | `lic_type` | `Medical Doctor` |
| 11 | License ID | `license_id` | `17371` |

**NPI column** — looked up dynamically by header name (not fixed index).
The engine scans the header row for any cell matching: `NPI_NO`, `NPI NO`, `NPI`,
`NPI Number`, or `npi`. If found, that column becomes `npi_no`.

> Columns 5–6, 8 are not read by the engine. The header row is auto-detected.

### Baseline input file

`license_details_Jun.xlsx` is the current baseline input. Its full header row is:

```
First Name | Middle Name | Last Name | EPDB PIN | Provider Type | Source Code |
Status Eff Date | Maintained By | Netid's | License State | LIC_TYPE_NM |
License ID | LIC_EXPRTN_DT | LIC_PRDEXPN_DT | NPI_NO | Service Location State | provider type
```

NPI is read from column 14 (`NPI_NO`).

### Provider type codes (prov_type)

| Code | Meaning | Code | Meaning |
|------|---------|------|---------|
| MD | Medical Doctor | NP / NPB / NPS / NSA | Nurse Practitioner variants |
| DO | Doctor of Osteopathy | PN | Practical Nurse |
| PH | Pharmacist | PT | Physical Therapist |
| DDS / DMD | Dentist | OT | Occupational Therapist |
| DC | Chiropractor | SW | Social Worker |
| LPC / LC / PC | Licensed Counselor variants | PAS / PAB / PA / PM | Physician Assistant variants |
| CP | Psychologist | ABA | Applied Behavior Analyst |
| AP / LAC | Acupuncturist | MT / MST | Massage Therapist |
| DP | Podiatrist | AU / SH / ST | Audiologist / Speech |
| MW | Midwife | DT / NUT | Dietitian / Nutritionist |
| DAC | Drug/Alcohol Counselor | GNC | Genetic Counselor |
| OD / OP | Optometrist | RNA | Registered Nurse Anesthetist |

Full list: 43 prov_type codes across 795 routing entries.

---

## 4. Output Format

### 5-channel output layout

```
PSV_DEV/
└── Output/
    ├── standard/
    │   └── 2026-06/
    │       └── {run_id}.xlsx    ← Every input row — Pass (green) or Fail (red)
    │       └── {run_id}.csv     ← Same data as CSV
    ├── nppes/
    │   └── 2026-06/
    │       └── {run_id}.csv     ← NPPES record + discrepancy diff per row
    ├── ai_fallback/
    │   └── 2026-06/
    │       └── {run_id}.csv     ← Every row that hit the AI agent + structured reason
    ├── manual/
    │   └── 2026-06/
    │       └── {run_id}.csv     ← Every unresolved row + failure_reason code
    └── add_license/
        └── 2026-06/
            └── {run_id}_AddLicense.xlsx  ← EPDB upload file (Pass rows only)
```

Evidence and trace data land alongside:

```
PSV_DEV/
├── Evidence/
│   └── 2026-06/{state}/{board_id}/{ts}_{query}/
│       ├── search_results.html
│       └── search_results.png
└── Output/
    └── _traces/
        └── 2026-06/{run_id}/
            └── {row_id}.json    ← Full attempt log per row (internal)
```

### Standard channel columns

| Column | Description |
|--------|-------------|
| `status` | `Pass` or `Fail` |
| `license_expiry` | ISO date from board record (Pass rows; blank if board doesn't expose it) |
| `matched_license` | License number confirmed on the board |
| `matched_first` / `matched_last` | Name from board record |
| `match_method` | `exact_license` \| `exact_name` \| `npi_substituted_exact` \| `ai_fuzzy` \| `tiebreak_provider_type` \| `cross_row_name_match` \| `none` |
| `fuzzy_score` | Disambiguator score 0–1 (set for fuzzy matches) |
| `weight_profile` | `license_present` or `name_only` — which scorer profile was used |
| `ai_fallback_used` | `True` if the AI agent was invoked for this row |
| `ai_outcome` | `resolved` \| `gave_up` \| `errored` \| `skipped` |
| `tiebreaker_used` | `True` if provider_type tiebreaker decided between close candidates |
| `npi_substituted` | `True` if NPPES-derived values were used in the winning search |
| `attempts_used` | Number of board search attempts made |
| `reason` | Structured failure code (Fail rows only — see below) |

### Structured failure reason codes

| Code | Meaning |
|------|---------|
| `name_mismatch` | Records returned but no candidate name matched the gate |
| `license_mismatch` | Record found by name but license number disagreed |
| `provider_type_mismatch` | Name+license matched but candidate's license_type didn't align with prov_type |
| `ambiguous_after_narrowing` | Multiple candidates survived narrowing + tiebreaker |
| `no_records` | Every rung on every routed board returned zero records |
| `no_routing` | No board configured for (state, prov_type) |
| `state_captcha_blocked` | State is in the CAPTCHA exclusion list |
| `npi_no_missing` | NPPES skipped — input row had empty NPI_NO |
| `nppes_not_found` | NPPES returned 0 results for the supplied NPI |
| `ai_circuit_breaker_open` | AI skipped — endpoint hit 2 consecutive errors |
| `ai_max_turns_exceeded` | Agent ran 8 turns without committing |
| `ai_gave_up` | Agent called give_up — code appended after colon |

---

## 5. Architecture

### File layout

```
PSV_DEV/
├── run_psv.py                       ← Main entry point
├── Input.xlsx                       ← Input file
│
└── lvs/adapters/scrapers/
    ├── psv_test.py                  ← Core verification + state runner
    │   ├── load_input_rows()        ← Read Input.xlsx, parse PSV Tab
    │   ├── run_state_orchestrated() ← Orchestration-mode state runner
    │   └── run_state()              ← Legacy mode state runner
    │
    ├── orchestrator/                ← NEW — sits above engine + archetypes
    │   ├── ladder.py                ← Rule-based ladder (boards × rungs)
    │   ├── nppes_client.py          ← Universal NPPES fetch + 30-day cache
    │   ├── disambiguator.py         ← Gate + scorer (two weight profiles)
    │   ├── ai_agent.py              ← Multi-turn Anthropic Claude tool-calling agent
    │   ├── drift_detector.py        ← Site-drift report (no auto-apply)
    │   ├── output_emitter.py        ← 5-channel writer
    │   ├── trace.py                 ← Per-row attempt log (JSON + SQLite)
    │   ├── capability.py            ← supported_modes() per board config
    │   └── config.py                ← Paths, thresholds, env knobs
    │
    ├── engine/
    │   ├── ai_fallback.py           ← Claude AI for HTML extraction (Layer 1/2 ReAct)
    │   ├── models.py                ← Pydantic models (LicenseRecord, SiteConfig …)
    │   ├── navigator.py             ← Form fill, dropdown, search button
    │   ├── extractor.py             ← Results table + detail extraction
    │   ├── csv_extractor.py         ← CSV bulk download + in-memory search
    │   ├── pdf_extractor.py         ← PDF bulk download + table extract
    │   └── proxy.py                 ← Proxy resolution
    │
    ├── archetypes/                  ← Board archetype handlers
    │   ├── _shared.py               ← HTML extraction + AI fallback trigger
    │   └── ...
    │
    ├── board_routing_master.csv     ← Routing: (state, prov_type) → [board IDs]
    ├── board_inventory.xlsx         ← Board metadata, smoke test status, sprint priority
    ├── smoke_all.py                 ← Board regression test suite
    └── psv_config.yaml              ← Proxy + project defaults
```

### Request flow

```
run_psv.py
    │
    ├─ load_input_rows(Input.xlsx)          # Parse all rows from PSV Tab
    ├─ _load_routing()                      # board_routing_master.csv → _ROUTING dict
    ├─ _log_proxy_preflight(run_states)     # Log proxy plan before browser launch
    ├─ Export PROXY env var for NPPES client
    │
    └─ for each state:
           │
           ├─ [CAPTCHA state]  → emit Fail rows, continue
           │
           └─ run_state_orchestrated(rows, state, emitter, run_id)
                  │
                  ├─ For each row:
                  │   ├─ 1. NPPES fetch (npi_no → 30-day cached JSON)
                  │   ├─ 2. Rule ladder: boards × rungs (capability-driven)
                  │   │     ├─ license_number → license_numeric_only → license_first_last
                  │   │     ├─ license_and_last → license_and_first
                  │   │     ├─ first_and_last → last_name → first_name
                  │   │     └─ NPPES retry on differing fields (loop-guarded)
                  │   ├─ 3. Disambiguation gate + scorer (two weight profiles)
                  │   └─ 4. AI agent fallback if ladder exhausted
                  │
                  └─ emitter.flush() → writes all 5 channels
```

---

## 6. Orchestration Layer

The orchestration layer (`orchestrator/`) sits above the scraper engine and drives the
multi-step resolution process for each row.

### Rule-based ladder (`ladder.py`)

For each row, the ladder iterates over all routed boards and all rungs in this order
(rungs not supported by the board are skipped; already-tried signatures are deduped):

```
1. license_number          (e.g. "DO3940")
2. license_numeric_only    (e.g. "3940" — strips prefix/suffix)
3. license_first_last      (license + first + last)
4. license_and_last        (license + last)
5. license_and_first       (license + first)
6. first_and_last          (first + last)
7. last_name               (last name only)
8. first_name              (first name only)
```

After the master ladder exhausts, a targeted NPPES retry runs: for each field where
NPPES differs from the master (first name, last name, license number), only the rungs
that test that field are retried with the NPPES value.

### Disambiguation (`disambiguator.py`)

**Selection gate (applied before scoring):**
A candidate is selectable only when:
- (first_name matches AND license numerics match) **OR**
- (first_name matches AND last_name matches)

Middle name is never used.

**Two weight profiles:**

| Profile | When used | Weights |
|---------|-----------|---------|
| `license_present` | At least one license-based rung returned candidates | license 0.35, first 0.30, last 0.20, prov_type 0.10, state 0.05 — threshold 0.90 |
| `name_only` | All license rungs returned zero records | first 0.40, last 0.30, prov_type 0.25, state 0.05 — threshold 0.85 |

**Provider-type tiebreaker:** when the top two candidates are within 0.02 of each other,
the candidate whose license_type matches the master row's prov_type wins.

### Post-run name reconciliation

After all rows in a run complete, the emitter performs a cross-row reconciliation pass:
any Fail row that shares the same (first, last, state, prov_type) as a passing row is
promoted to Pass with `match_method = cross_row_name_match`. This handles providers who
appear twice with different license ID formats.

---

## 7. AI Fallback — Two Layers

AI is invoked at two distinct levels, both using **Anthropic Claude** via the same API key.

### Engine-level AI fallback (`engine/ai_fallback.py`) — called during board scraping

Invoked by `archetypes/_shared.py` when rule-based HTML extraction yields fewer than
3 meaningful fields from a detail page.

**Layer 1 — FETCHER**
Triggered when: zero records from rule ladder OR secondary identity check failed.
Claude works through all available search combinations from scratch:
```
AI-1  SearchBoard  — license_id only
AI-2  SearchBoard  — last_name + first_name + profession
AI-3  SearchBoard  — last_name + first_name
AI-4  SearchBoard  — last_name only
AI-5  SearchBoard  — first_name only
AI-4b SearchBoard  — nppes_last + nppes_first  (if NPPES name differs)
AI-1b SearchBoard  — NPPES license ID         (if NPPES has different ID)
AI-6..AI-10  SearchNPPES                       (if NPPES is available)
AI-11 AnalyzeScreenshot / AI-12 ParseHTML      (use evidence files)
```

**Layer 2 — DISAMBIGUATOR**
Triggered when: multiple candidates returned; auto-disambiguation could not isolate one.
Claude works the D-ladder on already-returned candidates — no full re-search:
```
D-1  Exact license ID match
D-2  Exact name match (first + last)
D-3  Exact NPI match
D-4  Fuzzy name score ≥ 90
D-5  AnalyzeScreenshot / ParseHTML — visual comparison
```

**JSON output structure (ReAct FinalAnswer):**
Claude returns a JSON object that is flattened back into the extraction pipeline:
```json
{
  "status": "AUTO_SELECT",
  "confidence": 95,
  "record": {
    "license_id": "17371",
    "first_name": "John",
    "last_name": "Smith",
    "expiration_date": "2027-01-31",
    "license_status": "Active"
  },
  "provenance": { "match_method": "exact_license", "ai_fallback_used": true, "ai_layer": 1 },
  "notifications": [],
  "site_drift": []
}
```
The `record` block fields are aliased into the pipeline (`license_id → license_number`,
`license_status → status`). The `_used_ai: true` flag is set on the returned dict.
When the API is unavailable or the circuit breaker is open, `_used_ai: false` is returned
and the row proceeds with whatever rule-based extraction produced.

**Circuit breaker:** after 2 consecutive Anthropic API errors, AI is disabled for
the remainder of the process run. A portal alert is logged.

### Orchestrator-level AI agent (`orchestrator/ai_agent.py`) — called after ladder exhaustion

Invoked by `run_state_orchestrated()` when the full rule ladder + NPPES retry fails.
Uses multi-turn Anthropic Claude tool-calling (up to 8 turns):

| Tool | Purpose |
|------|---------|
| `try_search(source_id, mode, fields)` | Issue a new rung (signature-deduped) |
| `inspect_evidence(attempt_seq)` | Read HTML excerpt from an attempt's evidence folder |
| `pick_candidate(source_id, candidate_index)` | Commit a candidate from a prior attempt |
| `report_site_drift(source_id, suspected_change, fix_hint)` | Record drift (no auto-apply) |
| `give_up(reason)` | Terminate with structured reason code |

The agent receives the full attempt log, master row, NPPES record, and escalation reason
as context. All tool results are JSON. The circuit breaker is independent of the engine-level one.

### Configuration

```
# PSV_DEV/.env
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-6   # optional; defaults to claude-opus-4-8
```

---

## 8. NPPES Enrichment

Every input row is enriched against NPPES (NPI Registry) before any board search runs.

- **Lookup key:** `npi_no` column from input (header-detected)
- **Cache:** 30-day JSON cache under `PSV_DEV/PSV/Cache/NPPES/{npi}.json`
- **Proxy:** inherits the configured proxy server (set via `PROXY` env var, which `run_psv.py`
  exports from `psv_config.yaml`)

**What is captured:**
- Full name (first, last, middle, credential)
- Primary taxonomy / profession code
- All license numbers with state codes
- Alternate / other names
- Addresses

**Discrepancy detection:** after each row's rule ladder runs, the NPPES record is diffed
against the master row. Differing fields (name, license number) drive the targeted
NPPES retry rungs described in Section 6.

**NPPES channel output** (`Output/nppes/{run_id}.csv`) contains one row per input row
regardless of Pass/Fail, with columns:

```
master_row_id, npi_no, nppes_first, nppes_last, nppes_middle, nppes_credential,
nppes_primary_taxonomy, nppes_primary_taxonomy_code,
nppes_primary_license_no, nppes_primary_license_state,
extra_license_count, has_other_names,
diff_first_name (master|nppes), diff_last_name (master|nppes),
diff_license_number (master|nppes), other_name_used,
fetch_status  (ok | not_found | http_error | empty_input)
```

---

## 9. AddLicense Output Channel

Every **Pass** row produces an entry in `Output/add_license/{YYYY-MM}/{run_id}_AddLicense.xlsx`.
This file is formatted for direct upload to the EPDB Auto Loader system.

### Column mapping

| Column | Source | Notes |
|--------|--------|-------|
| `EPDB` | Input col 3 (EPDB PIN) | |
| `State` | Input col 9 (License State) | |
| `MaintBy` | Input col 7 (Maintained By) | |
| `LicenseNumber` | Board record (verified license) | Falls back to input `license_id` if board record has no license number |
| `LicenseEffDate` | — | **Always blank** (per story requirement) |
| `LicenseTermDate` | Board record expiration date | ISO format |
| `LicenseType` | Input col 10 (LIC_TYPE_NM) | Sourced from Alteryx data |
| `OriginalLicenseDate` | — | **Always blank** (per story requirement) |
| `OverrideExistingLicense` | — | **Always "Yes"** (per story requirement) |
| `EPDBDone` | — | **Always blank** (filled manually post-upload) |

### Business rules

1. `LicenseEffDate` → blank
2. `OriginalLicenseDate` → blank
3. `LicenseTermDate` → populated with expiration date from board record
4. `LicenseType` → sourced from Alteryx input data (not board data)
5. `OverrideExistingLicense` → "Yes" for all records without exception

---

## 10. Routing Table

`board_routing_master.csv` maps each `(state, prov_type)` pair to one or more board source IDs.
The engine tries boards in order — first Pass wins.

### Format

```csv
state,psv_prov_type,source_id
NV,MD,NV_MEDBOARD
NV,PH,NV_PHARMACY
NV,PH,NV_OSTEO          ← second board tried only if NV_PHARMACY fails
OH,PT,OH_PROVIDERS_INDIVIDUAL
```

### Current coverage (as of 2026-06-22)

- **795 routing entries** covering **41 states** and **43 provider types**
- **14 multi-board pairs** (tried in order)
- 7 CAPTCHA-blocked states excluded: CA, GA, TN, UT, IA, NE, MT

### Python dictionary reference

```python
from lvs.adapters.scrapers.board_routing import get_boards, ROUTING

get_boards("NV", "PH")   # → ['NV_PHARMACY', 'NV_OSTEO']
get_boards("OH", "PT")   # → ['OH_PROVIDERS_INDIVIDUAL']
get_boards("XX", "MD")   # → []  (not routed)
```

### Rebuilding routing from inventory

```bash
python c:/tmp/build_routing.py
```

---

## 11. Rule-Based Search Strategy

For each row, the ladder iterates over all routed boards and all supported rungs.
Every attempt is tracked with a signature `(source_id, mode, normalized_query)` — no
duplicate attempt is ever issued.

```
for each board in routing:

  Rung 1 — license_number
    query = license_id as-is (e.g. "DO3940", "PT021941")

  Rung 1.5 — license_numeric_only
    query = digits only (e.g. "3940", "21941")
    triggered only when Pass 1 returned nothing AND license_id has non-digit chars

  Rung 2 — last_name (or other name combinations per board capability)
    triggered only when license rungs returned no records

  → on each rung: disambiguate candidates → Pass if selected, else next rung

→ after exhausting all boards + NPPES retry: escalate to AI or return Fail
```

### License number matching (in the disambiguator)

1. **Exact** — `"PT021941"` == `"PT021941"`
2. **Substring** — `"PT021941"` ⊂ `"STATE-PT021941"`
3. **Numeric-only** — digits stripped from both; `"3940"` will NOT match `"13940"`
   (pure-digit matches must be exact to prevent false positives)

---

## 12. Proxy Configuration

Proxy is **fully automatic** — no `PROXY=` environment variable prefix is required.

### How it works

Resolution order in `engine/proxy.py`:

| Priority | Source | Example |
|----------|--------|---------|
| 1 | `LVS_PROXY_SERVER` env var | `http://user:pass@proxy:9119` |
| 2 | `PROXY` env var | `proxy:9119` |
| 3 | `psv_config.yaml` (auto-loaded) | `proxy: server: "proxy:9119"` |

`run_psv.py` also exports the resolved proxy server to the `PROXY` environment variable
so the NPPES HTTP client picks it up automatically.

### Per-board proxy rules (`transport.proxy.enabled`)

| Value | Meaning |
|-------|---------|
| `true` | Board requires proxy |
| `false` | Proxy force-disabled (e.g. NH_OPLC — Akamai WAF) |
| *(absent)* | Use proxy if available |

### Boards that require proxy (`proxy: enabled: true`)

WV_DENTAL, WV_OPTOMETRY, WV_PT, WV_PSYCH, WV_SOCIALWORK, LA_ADRA, LA_MASSAGETHERAPY,
MS_ABA, MS_LPC, AL_MFT, AL_OPTOMETRY, OK_ADAC, AZ_CHIRO, OH_PROVIDERS_INDIVIDUAL

### Boards that block proxy (`proxy: enabled: false`)

NH_OPLC (Akamai WAF), NV_MEDBOARD

---

## 13. State Coverage & Sprint Plan

### 6-Sprint rollout

| Sprint | States | Boards |
|--------|--------|--------|
| **S1** | NV, WY, MD, KY, KS, FL | 54 |
| **S2** | OR, TX, OH, SD, NC, MO | 40 |
| **S3** | AZ, MN, ND, MA, AR, SC | 26 |
| **S4** | LA, MS, AL, OK, WV, NH, NJ | 37 |
| **S5** | CO, CT, DE, HI, ID, IL, IN, ME, MI, NM, NY, PA, RI, VA, VT, WA, WI | 17 |
| **S6** | AK, AZ, MO, NC, NM, NY, OK, WV *(SKIP)* | 12 |

### CAPTCHA-blocked states (always Fail)

CA, GA, TN, UT, IA, NE, MT

### SKIP boards

| Board | Skip reason |
|-------|-------------|
| AK_CBP | DataDome CAPTCHA from corporate IP |
| AZ_MEDBOARD | GLSuite network timeout |
| MO_NURSING | MOPRO portal disabled |
| NC_PT | Cloudflare intermittent |
| NC_OPTOMETRY | Google Drive cross-origin iframe |
| NY_CREDENTIALS | API response blocked |
| OK_BEHAVIORAL_HEALTH, OK_SOCIALWORK, OK_ODOHCS | thentiacloud XHR blocked on corp network |
| WV_CHIRO | SharePoint auth required |

**Current smoke baseline: 178 PASS / 0 FAIL / 10 SKIP** (188 boards)

---

## 14. Single-State Testing

```bash
cd C:\Users\n661685\PSV_DEV

# Test NV, first 10 rows — rule-based only (no AI)
python run_psv.py --states NV --max-rows 10 --no-ai

# Test with AI agent mock (no API key needed)
python run_psv.py --states NV --max-rows 5 \
  --ai-mock lvs/adapters/scrapers/orchestrator/test_data/ai_mock.json

# Force AI on every row (audit mode)
python run_psv.py --states MD --max-rows 3 --force-ai

# Legacy output for comparison
python run_psv.py --states NV --max-rows 10 --legacy-output

# Check what proxy will be used
python -c "
import sys; sys.path.insert(0,'lvs/adapters/scrapers')
from engine.proxy import get_proxy_config
print(get_proxy_config())
"
```

### Smoke tests (smoke_all.py)

```bash
cd C:\Users\n661685\PSV_DEV\lvs\adapters\scrapers

# All boards (baseline: 178 PASS / 0 FAIL / 10 SKIP)
python smoke_all.py

# Single board
python smoke_all.py --filter OH_PROVIDERS_INDIVIDUAL

# Parallel execution (3 browsers at once)
python smoke_all.py --concurrency 3

# Force-run SKIP boards (check if site recovered)
python smoke_all.py --filter AK_CBP --force-skip
```

### Direct board testing (run.py)

```bash
cd C:\Users\n661685\PSV_DEV\lvs\adapters\scrapers

# License number only
python run.py --config sites/OH_PROVIDERS_INDIVIDUAL/config.yaml \
  --license-number 35076302

# Last name only
python run.py --config sites/NV_MEDBOARD/config.yaml --last-name SMITH

# Headed browser (watch the scraper live)
python run.py --config sites/WV_OPTOMETRY/config.yaml --last-name JONES --headed
```

---

## 15. Scraper Engine Reference

Full documentation: [lvs/adapters/scrapers/README.md](lvs/adapters/scrapers/README.md)

---

## 16. Adding a New Board to Routing

### Step 1 — Verify board config passes smoke test

```bash
cd lvs/adapters/scrapers
python smoke_all.py --filter XX_BOARD
```

### Step 2 — Update board_inventory.xlsx

Set the board's **Profession Codes**, `Smoke Test Status = READY`, and `Sprint` column.

### Step 3 — Rebuild routing

```bash
python c:/tmp/build_routing.py
```

### Step 4 — Test

```bash
python run_psv.py --states XX --max-rows 10
```

---

## 17. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| All rows Fail with "No board configured" | No routing entry | Check `board_routing_master.csv` has entries for (state, prov_type) |
| NPPES fetch failed (empty error) | Proxy not exported | Verify `psv_config.yaml` has `proxy.server` — `run_psv.py` exports it to `PROXY` env var automatically |
| `AI fallback skipped — Anthropic API not configured` | `ANTHROPIC_API_KEY` not set | Add to `PSV_DEV/.env`: `ANTHROPIC_API_KEY=sk-ant-...` |
| `AI agent circuit breaker OPEN` | 2 consecutive Anthropic API errors | Check API key validity; verify network access to `api.anthropic.com` |
| State rows Fail with "CAPTCHA-blocked" | State in `CAPTCHA_STATES` | Expected — no fix |
| "Config not loaded for boards: ['XX_BOARD']" | `skip: true` or missing config | Check YAML, remove `skip: true` if board is functional |
| Pass rate 0% for a state that should work | Proxy issue | Check `psv_config.yaml` → `proxy.server` |
| OH CSV searches take 90–120s per row | Large bulk CSV scanned in-memory | Expected; run overnight for large datasets |
| WY boards fail / no data | Google Sheets CSV cache expired | Download fresh CSVs; cache to `PSV/CSVS/` |
| AddLicense file empty | No Pass rows in run | Expected when all rows Fail |
| `cross_row_name_match` in standard output | Post-run reconciliation promoted a Fail row | Normal — the match is based on another passing row's same name+state |
| NPPES returns nothing for all rows | Proxy not set for HTTP client | Check `run_psv.py` proxy export logic after `_log_proxy_preflight()` |

### Environment variables

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Claude API key — required for AI fallback (both engine and orchestrator) |
| `ANTHROPIC_MODEL` | Claude model override (default: `claude-opus-4-8`) |
| `PROXY` | Override proxy server — `proxy:9119` or full URL |
| `LVS_PROXY_SERVER` | Full proxy URL override (highest priority) |
| `PROXY_NID` / `PROXY_PASSWORD` | Proxy credentials (personal — not stored in files) |
| `PSV_AI_MOCK_PATH` | Path to AI mock JSON file (set by `--ai-mock` flag) |
| `PSV_AI_MAX_TURNS` | Max turns for AI agent loop (default: 8) |

---

## Step-by-Step Guide (Simple Version)

```
Step 1: Open your terminal.
        In VS Code press Ctrl+` (the backtick key, top-left of keyboard).

Step 2: Go to the project folder.
        Type: cd C:\Users\n661685\PSV_DEV
        Press Enter.

Step 3: Activate the virtual environment (if not already active).
        Type: .\.venv\Scripts\Activate.ps1
        Press Enter. You should see (.venv) at the start of the line.

Step 4: Copy your input file.
        Copy your spreadsheet to PSV_DEV/ and name it Input.xlsx.
        The sheet must be named "PSV Tab".
        Ensure columns are in the order: First Name (0), Middle Name (1),
        Last Name (2), EPDB PIN (3), Provider Type (4), [skip 5-6],
        Maintained By (7), [skip 8], License State (9), License Type (10),
        License ID (11). NPI_NO can appear anywhere — the engine finds it
        by header name.

Step 5: Set your API key (first time only).
        Edit PSV_DEV/.env and add:
          ANTHROPIC_API_KEY=sk-ant-...

Step 6: Run verification.
        To run all states:
          python run_psv.py

        To run only Nevada and Maryland as a test:
          python run_psv.py --states NV MD

        To skip AI (faster, rule-based only):
          python run_psv.py --no-ai

Step 7: Wait.
        Progress is logged to the console. Results are saved after each batch.

Step 8: Check the output.
        Output/standard/2026-06/{run_id}.xlsx  — Pass (green) / Fail (red)
        Output/add_license/2026-06/{run_id}_AddLicense.xlsx  — EPDB upload file
        Output/manual/2026-06/{run_id}.csv  — unresolved rows for manual review
        Output/nppes/2026-06/{run_id}.csv   — NPPES enrichment per row
```
