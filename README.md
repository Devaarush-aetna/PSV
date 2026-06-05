# PSV_DEV — Professional State Verification (License Scraper Engine)

## Table of Contents

1. [How to Run the Codes](#1-how-to-run-the-codes)
2. [How Each Component Works](#2-how-each-component-works)
3. [Config Files, JSONs, and Engine Files](#3-config-files-jsons-and-engine-files)
4. [JSON Outputs and Run Patterns](#4-json-outputs-and-run-patterns)
5. [Code Details and Comments](#5-code-details-and-comments)
6. [How to Add a New State Board](#6-how-to-add-a-new-state-board)

---

## 1. How to Run the Codes

### Prerequisites

- Python 3.10 or higher
- Playwright browsers installed
- Edge WebDriver in Edgedriver/ (for legacy scrapers only)

### Step 1: Clone and Enter the Project

```bash
git clone <repo-url>
cd PSV_DEV
```

### Step 2: Create and Activate Virtual Environment

```bash
python -m venv .venv

# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

### Step 3: Install Dependencies

```bash
pip install -r lvs/adapters/scrapers/requirements.txt
playwright install
```

Dependencies:
- playwright (browser automation, replaces Selenium)
- pydantic (data models and config validation)
- pyyaml (config.yaml parsing)
- aiosqlite (async SQLite for telemetry and output DB)
- openai (AI fallback extraction)

### Step 4: Run a Scraper

All scraping is done through a single universal CLI entry point: `run.py`

```bash
cd lvs/adapters/scrapers

# Search by license number (Nevada Medical Board)
python run.py --config sites/NV_MEDBOARD/config.yaml --mode license_number --query "12345"

# Search by last name (Nevada Chiropractic)
python run.py --config sites/NV_CHIRO/config.yaml --mode last_name --query "Smith" --headed

# Search by name (Massachusetts)
python run.py --config sites/MA_HEALTH/config.yaml --mode name --query "Smith"

# Dry-run (validates config without launching browser)
python run.py --config sites/NV_PT/config.yaml --mode license_number --query "PT1234" --dry-run
```

### CLI Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| --config | Yes | Path to the board config.yaml file |
| --mode | Yes | Search mode (license_number, last_name, name, etc.) |
| --query | Yes | The search string |
| --headed | No | Show the browser window (default: headless) |
| --dry-run | No | Validate config only, do not scrape |
| --output | No | Custom output JSON path |
| --db | No | SQLite DB path (default: ./lvs_scrape.db) |
| --evidence-dir | No | Override evidence storage directory |

---

## 2. How Each Component Works

### Project Structure

```
PSV_DEV/
|-- .venv/                              # Python virtual environment
|-- Edgedriver/
|   +-- msedgedriver.exe                # Edge WebDriver binary (legacy)
|-- Resources_Aetna_01Jun2026.xlsx      # Input resource spreadsheet
|-- README.md                           # This file
|
+-- lvs/                                # Main package
    +-- __init__.py
    +-- adapters/
        +-- __init__.py
        +-- scrapers/                   # Core scraping system
            |-- __init__.py
            |-- run.py                  # Universal CLI entry point
            |-- board_inventory.py      # Registry of all supported boards
            |-- lvs_scrape.db           # SQLite telemetry/output database
            |-- requirements.txt        # Python dependencies
            |-- diagnose_*.py           # Diagnostic/debug scripts
            |-- diagnose_*.html/.png    # Diagnostic evidence snapshots
            |
            |-- engine/                 # Shared scraping engine
            |   |-- __init__.py
            |   |-- ai_fallback.py      # AI-based extraction fallback
            |   |-- browser.py          # Playwright browser lifecycle
            |   |-- evidence.py         # HTML/screenshot capture
            |   |-- extractor.py        # Data extraction from DOM
            |   |-- models.py           # Pydantic data models (contract)
            |   |-- navigator.py        # Page navigation and form filling
            |   |-- output.py           # JSON output and DB writing
            |   |-- pagination.py       # Multi-page result handling
            |   |-- post_processors.py  # Field mapping and normalization
            |   |-- retry.py            # Retry logic with backoff
            |   |-- telemetry.py        # Event logging to SQLite
            |   +-- validate.py         # Config loading and validation
            |
            |-- sites/                  # Per-board configuration (YAML)
            |   |-- MA_HEALTH/config.yaml
            |   |-- MD_PHYSICIANS/config.yaml
            |   |-- NV_CHIRO/config.yaml
            |   |-- NV_MEDBOARD/config.yaml
            |   |-- NV_NVADGC/config.yaml
            |   +-- NV_PT/config.yaml
            |
            |-- evidence/               # Saved HTML/screenshots per run
            |   |-- MA_HEALTH/{run_id}/
            |   |-- MD_PHYSICIANS/{run_id}/
            |   |-- NV_CHIRO/{run_id}/
            |   |-- NV_MEDBOARD/{run_id}/
            |   |-- NV_NVADGC/{run_id}/
            |   +-- NV_PT/{run_id}/
            |
            +-- output/                 # JSON result files
                |-- NV_CHIRO_smoke4.json
                |-- NV_PT_final2.json
                |-- MD_PHYSICIANS_20260602_191425.json
                +-- ...
```

### Architecture Diagram

```
+-------------------------------------------------------------------+
|                         USER / CALLER                              |
|   python run.py --config sites/X/config.yaml --mode M --query Q   |
+-----------------------------+-------------------------------------+
                              |
                              v
+-----------------------------+-------------------------------------+
|                          run.py (CLI)                              |
|   - Parses arguments                                              |
|   - Loads config.yaml via validate.py                             |
|   - Initializes telemetry DB                                      |
|   - Calls verify_license()                                        |
+-----------------------------+-------------------------------------+
                              |
                              v
+-------------------------------------------------------------------+
|                    verify_license() [Orchestrator]                 |
|                                                                   |
|   Manages the full scrape lifecycle for one search query:         |
|   1. Generate run_id (UUID)                                       |
|   2. Launch Playwright browser (browser.py)                       |
|   3. Navigate to search page (navigator.py)                       |
|   4. Fill form and submit (navigator.py)                          |
|   5. Capture evidence of results (evidence.py)                    |
|   6. Detect result type and extract (extractor.py)                |
|   7. Paginate if needed (pagination.py)                           |
|   8. Apply field mapping (post_processors.py)                     |
|   9. Map to LicenseRecord (output.py)                             |
|  10. Emit telemetry (telemetry.py)                                |
+----+----------+----------+----------+----------+---------+--------+
     |          |          |          |          |         |
     v          v          v          v          v         v
+---------+ +--------+ +--------+ +-------+ +------+ +---------+
| browser | | naviga | | extrac | | pagin | | evid | | ai_fall |
|  .py    | | tor.py | | tor.py | | ation | | ence | | back.py |
|         | |        | |        | | .py   | | .py  | |         |
| Launch  | | Go to  | | Parse  | | Next  | | Save | | OpenAI  |
| Play-   | | URL,   | | tables | | page  | | HTML | | extract |
| wright  | | fill   | | grids  | | click | | and  | | when    |
| browser | | forms  | | detail | | scroll| | PNG  | | rules   |
| context | | click  | | pages  | | pages | | snap | | fail    |
+---------+ +--------+ +--------+ +-------+ +------+ +---------+
                              |
                              v
+-------------------------------------------------------------------+
|                    post_processors.py                              |
|   apply_field_map(): raw page labels -> canonical field names     |
+-----------------------------+-------------------------------------+
                              |
                              v
+-------------------------------------------------------------------+
|                       output.py                                    |
|   map_to_license_record(): raw dict -> LicenseRecord (Pydantic)   |
|   write_output(): serialize list to JSON file                     |
|   upsert_to_db(): write/update records in SQLite                  |
+-----------------------------+-------------------------------------+
                              |
                              v
+-------------------------------------------------------------------+
|                      telemetry.py                                  |
|   init_db(): create SQLite tables                                 |
|   log_scrape_event(): persist timing, status, counts              |
+-------------------------------------------------------------------+
                              |
                              v
+-------------------------------------------------------------------+
|                        OUTPUTS                                     |
|   output/{SOURCE_ID}_{timestamp}.json    (JSON result file)       |
|   evidence/{SOURCE_ID}/{run_id}/         (HTML + screenshots)     |
|   lvs_scrape.db                          (telemetry + records)    |
+-------------------------------------------------------------------+
```

### Data Flow Summary

```
config.yaml  --->  validate.py  --->  SiteConfig (Pydantic model)
                                          |
User query   --->  SearchQuery            |
                        |                 |
                        +--------+--------+
                                 |
                                 v
                        verify_license()
                                 |
            +--------------------+--------------------+
            |                    |                    |
      [ag_grid]          [select_list]        [detail_clicks]
            |                    |                    |
            +--------------------+--------------------+
                                 |
                                 v
                       List[LicenseRecord]
                                 |
                 +---------------+---------------+
                 |               |               |
            JSON file       SQLite DB       Evidence files
```

---

## 3. Config Files, JSONs, and Engine Files

### 3.1 Site Config (config.yaml)

Each board has a config.yaml in sites/{BOARD_ID}/. This is the ONLY file needed to add a new board. No code changes required.

Structure:

```yaml
identity:
  source_id: NV_CHIRO                    # Unique board identifier
  board_name: Nevada Chiropractic Board  # Human-readable name
  state: Nevada
  country: US
  profession_codes: [chiro]
  base_url: https://nvcpbn.portalus...   # Starting URL
  archetype: thentia_cloud               # Site technology type

search:
  modes:                                 # What search types are available
    - mode: license_number
      dropdown_value: "License Number"
    - mode: last_name
      dropdown_value: "Last Name"
  form:
    search_by_dropdown:
      strategy: custom_dropdown
      selector: ".dropdown-toggle"
    search_input:
      selector: "input.form-control"
    search_button:
      selector: "button.btn-search"
  results_wait:
    strategy: element_visible
    selector: ".ag-row"
    timeout_ms: 20000
    no_results_indicators: ["No results found"]

results:
  type: ag_grid                          # How results are displayed
  ag_grid_columns: [name, license_number, status, expiration]
  has_detail_page: true
  detail_trigger:
    type: view_button
    selector: "a:has-text('View')"
  pagination:
    enabled: true
    strategy: next_button
    next_selector: ".ag-paging-button-next"

detail:
  wait:
    strategy: url_change
    timeout_ms: 15000
  field_map:                             # Maps raw page labels -> canonical fields
    "License No": license_number
    "First Name": licensee_first_name
    "Last Name": licensee_last_name
    "Status": status
    "Expiry Date": expiration_date
  back_navigation:
    strategy: browser_back
    wait_after_ms: 1500

output:
  status_map:                            # Normalize status values
    "Active": active
    "Inactive": inactive
    "Expired": expired
  date_formats: ["%m/%d/%Y", "%Y-%m-%d"]

transport:
  browser: chromium
  headless: true
  timeout_ms: 60000
  rate_limit:
    delay_between_requests_ms: 2000
  retry:
    max_attempts: 3
    backoff_ms: [1000, 2000, 4000]

evidence:
  capture_html: true
  capture_screenshot: true
  capture_on: [search_results, detail_page, error]
  storage: local
  local_path: "./evidence/{source_id}/{run_id}/"
```

### 3.2 Engine Files Summary

| File | Responsibility |
|------|----------------|
| models.py | All Pydantic models: LicenseRecord, SiteConfig, SearchQuery, TelemetryEvent, and 20+ nested config models. Defines the canonical data contract for the entire system. |
| validate.py | Loads config.yaml, parses into SiteConfig Pydantic model, validates all fields. Catches config errors before browser launches. |
| browser.py | Manages Playwright browser lifecycle. Provides get_page() async context manager. Configures headless/headed, viewport, user-agent, timeouts. |
| navigator.py | navigate_to_search(): loads base URL. fill_search_form(): selects dropdown mode, fills input, clicks search, waits for results. |
| extractor.py | extract_results_table(): reads HTML tables. extract_ag_grid(): reads AG Grid rows. extract_detail(): scrapes detail page using configured strategies. |
| pagination.py | paginate(): async generator yielding once per page. Handles next_button, page_numbers, infinite_scroll strategies. |
| evidence.py | capture_evidence(): saves full page HTML and PNG screenshot. resolve_evidence_path(): builds the storage directory path. |
| ai_fallback.py | should_use_ai_fallback(): checks if extraction is sparse. extract_with_ai(): sends HTML to OpenAI to extract structured data when rules fail. |
| post_processors.py | apply_field_map(): translates raw scraped keys to canonical field names using config field_map dictionary. |
| output.py | map_to_license_record(): raw dict to LicenseRecord. write_output(): serialize to JSON. upsert_to_db(): write to SQLite. |
| retry.py | with_retry(): wraps async operations with configurable retry count and exponential backoff. |
| telemetry.py | init_db(): creates SQLite tables. log_scrape_event(): persists TelemetryEvent (timing, status, counts, errors). |

### 3.3 Database (lvs_scrape.db)

SQLite database storing:
- Telemetry events: run_id, source_id, stage, status, duration_ms, record_count, error_msg, timestamp
- License records (optional upsert for deduplication across runs)

### 3.4 Supported Archetypes

| Archetype | Description | Example Boards |
|-----------|-------------|----------------|
| thentia_cloud | Angular SPA with AG Grid, async content rendering | NV_CHIRO, NV_MEDBOARD, NV_PT, NV_NVADGC |
| ag_grid_spa | Generic AG Grid single-page application | Newer state boards |
| classic_html_form | Server-rendered HTML with traditional form POST | MD_PHYSICIANS |
| state_portal | State government portal with custom UI framework | MA_HEALTH |

### 3.5 Result Types

| Type | Description | Extraction Method |
|------|-------------|-------------------|
| ag_grid | AG Grid JavaScript component | extract_ag_grid() reads .ag-row cells |
| table | Standard HTML table | extract_results_table() reads tr/td |
| select_list | Dropdown listbox of results | _scrape_select_list_results() iterates options |
| card_list | Card-style result items | CSS selector per card |
| single_record | Direct detail (no list page) | extract_detail() immediately |

---

## 4. JSON Outputs and Run Patterns

### 4.1 Output JSON Structure (LicenseRecord)

```json
[
  {
    "source_id": "NV_CHIRO",
    "license_number": "B00819",
    "licensee_first_name": "JOHN",
    "licensee_last_name": "SMITH",
    "licensee_full_name": "JOHN SMITH",
    "licensee_middle_name": null,
    "licensee_suffix": null,
    "license_type": "Chiropractic Physician",
    "profession_code": null,
    "status": "active",
    "effective_date": "2005-03-15",
    "expiration_date": "2026-09-30",
    "issue_date": null,
    "last_renewal_date": "2024-10-01",
    "address": "123 Main St",
    "city": "Las Vegas",
    "state_code": "NV",
    "zip_code": "89101",
    "disciplinary_actions": [],
    "source_url": "https://nvcpbn.portalus...",
    "scraped_at": "2026-06-03T14:30:22Z",
    "evidence_html_path": "./evidence/NV_CHIRO/a1b2c3d4/detail_page.html",
    "evidence_screenshot_path": "./evidence/NV_CHIRO/a1b2c3d4/detail_page.png",
    "raw_fields": {
      "License No": "B00819",
      "Name": "JOHN SMITH",
      "Status": "Active"
    },
    "used_ai": false
  }
]
```

### 4.2 Field Descriptions

| Field | Type | Description |
|-------|------|-------------|
| source_id | string | Board identifier matching config identity.source_id |
| license_number | string | License number as displayed on the board website |
| licensee_first_name | string or null | First name |
| licensee_last_name | string or null | Last name |
| licensee_full_name | string or null | Combined full name |
| license_type | string or null | Type of license (Physician, PT, Chiro, RN, etc.) |
| status | enum | Normalized value: active, inactive, expired, suspended, revoked, probation, unknown |
| effective_date | date or null | When license became effective |
| expiration_date | date or null | When license expires |
| issue_date | date or null | Original issue date |
| last_renewal_date | date or null | Most recent renewal |
| address, city, state_code, zip_code | string or null | Licensee address if available |
| disciplinary_actions | array | List of any disciplinary records |
| source_url | string | Base URL of the board website |
| scraped_at | datetime | UTC timestamp of scrape execution |
| evidence_html_path | string or null | Path to saved HTML file |
| evidence_screenshot_path | string or null | Path to saved PNG screenshot |
| raw_fields | dict | Original unprocessed key-value pairs from page |
| used_ai | bool | Whether AI fallback was needed for extraction |

### 4.3 Run Patterns

| Pattern | Command | When to Use |
|---------|---------|-------------|
| License lookup | --mode license_number --query "B00819" | You know the exact license number |
| Last name search | --mode last_name --query "Smith" | Find all licensees with that last name |
| Full name search | --mode name --query "John Smith" | Boards that support full name input |
| Headed debug | add --headed | Watch the browser to debug selectors |
| Dry run | add --dry-run | Validate config without launching browser |
| Custom output path | add --output ./my/path.json | Write to a specific location |

### 4.4 Step-by-Step Guide (Simple Version)

```
Step 1: Open your terminal.
        In VS Code press Ctrl+` (the backtick key, top-left of keyboard).

Step 2: Go to the project folder.
        Type: cd C:\Users\n661685\PSV_DEV
        Press Enter.

Step 3: Activate the virtual environment.
        Type: .\.venv\Scripts\Activate.ps1
        Press Enter.
        You should see (.venv) at the start of the line. That means it is active.

Step 4: Go to the scrapers folder.
        Type: cd lvs\adapters\scrapers
        Press Enter.

Step 5: Pick which board you want to search.
        The available boards are in the sites/ folder:
          - NV_CHIRO (Nevada Chiropractic)
          - NV_PT (Nevada Physical Therapy)
          - NV_MEDBOARD (Nevada Medical Board)
          - NV_NVADGC (Nevada Dentistry/Hygiene)
          - MD_PHYSICIANS (Maryland Board of Physicians)
          - MA_HEALTH (Massachusetts Health)

Step 6: Run the search.
        Type: python run.py --config sites/NV_CHIRO/config.yaml --mode license_number --query "B00819"
        Press Enter.

Step 7: Wait.
        The program opens an invisible browser, goes to the website, searches, and reads the data.
        Log messages appear showing progress. Do not interrupt.

Step 8: Check the result.
        When done, it prints something like:
          "Done. 1 record(s) written to output/NV_CHIRO_20260603_143022.json"

Step 9: View the output file.
        Type: cat output/NV_CHIRO_20260603_143022.json
        Press Enter to see the scraped data.

Step 10: View evidence (optional).
         The evidence/ folder has saved HTML and screenshots of every page visited.
         Open them in a browser to see exactly what the scraper saw.
```

---

## 5. Code Details and Comments

### 5.1 run.py — Universal CLI and Orchestrator

```
run.py is the single entry point for all board scraping.
It replaces individual per-state scraper scripts.
It is entirely config-driven: the same Python code handles every board.
The config.yaml for each board tells the engine what to do.

Key function: verify_license(config, query, db, headless_override) -> list

This orchestrator:
  1. Generates a unique run_id (8-char UUID prefix) for tracing this run
  2. Resolves the evidence storage path using source_id and run_id
  3. Opens a Playwright browser via get_page() async context manager
  4. Navigates to the board search page (navigator.navigate_to_search)
  5. Fills and submits the search form (navigator.fill_search_form)
  6. Captures evidence screenshot/HTML of the search results page
  7. Detects the result type at runtime:
     - ag_grid: calls extract_ag_grid() to read all grid rows directly
     - select_list: calls _scrape_select_list_results() to iterate options
     - detail_clicks: calls _scrape_with_detail_clicks() to click each View button
     - plain table: calls extract_results_table() to read HTML table rows
  8. For each extracted record, applies field_map normalization
  9. Converts to LicenseRecord Pydantic model via map_to_license_record()
 10. Emits a telemetry event (success/failure, timing, record count)
 11. Writes output to JSON file and optionally to SQLite
 12. Returns the list of LicenseRecord objects

Key internal functions:

  _scrape_one_detail(page, config, run_id, db):
    Handles one detail page. Captures evidence, runs rule-based extraction,
    invokes AI fallback if extraction is sparse, returns raw dict.

  _scrape_with_detail_clicks(page, config, run_id, db):
    For boards with View buttons. Iterates all buttons on each page,
    clicks each one, waits for detail to render, scrapes, navigates back.
    Uses paginate() to handle multiple pages of results.

  _scrape_select_list_results(page, config, run_id, db):
    For boards with <select> listbox results (e.g., MD_PHYSICIANS).
    Reads all options, parses license numbers from option text.
    Two strategies: submit_button (select+click) or license_number_search
    (re-runs search for each parsed license number).

  _wait_for_detail_content(page, config):
    Handles async rendering delay on detail pages.
    For SPAs (thentia_cloud): waits for JS predicate checking actual text content.
    For classic HTML: waits for networkidle state.

  _navigate_back(page, config):
    Returns to results page after scraping a detail page.
    Strategies: browser_back, breadcrumb_click, or url_navigate.
```

### 5.2 models.py — Data Contracts

```
models.py defines every data structure in the system using Pydantic v2.

LicenseRecord:
  The canonical output format. Every board's data is normalized into this
  single structure. Downstream consumers (APIs, reports, databases) always
  receive the same field names, types, and value formats regardless of
  which state board the data came from.

SiteConfig:
  The top-level configuration model parsed from config.yaml. Contains:
  - SiteIdentity: source_id, board_name, state, base_url, archetype
  - SearchConfig: modes list, form selectors, results_wait settings
  - ResultsConfig: result type, table/grid config, detail trigger, pagination
  - DetailConfig: wait strategy, extraction strategies, field_map, back nav
  - OutputConfig: status_map normalization, date format strings
  - TransportConfig: browser type, headless, viewport, timeouts, retry, proxy
  - EvidenceConfig: what to capture, where to store
  - ComplianceConfig: TOS review metadata, captcha/login requirements

SearchQuery:
  Simple two-field model: mode (string) and query (string).

TelemetryEvent:
  One event per scrape stage. Fields: run_id, source_id, stage, status,
  duration_ms, record_count, used_ai, error_msg, timestamp.

All nested config models use Pydantic Field defaults so that config.yaml
files only need to specify values that differ from defaults.
```

### 5.3 Engine Module Details

```
browser.py:
  Provides get_page(transport_config) as an async context manager.
  Uses Playwright (not Selenium) for browser automation.
  Playwright advantages: native async, built-in auto-wait, better SPA handling.
  The context manager guarantees browser cleanup on success or failure.

navigator.py:
  navigate_to_search(): loads base_url, waits for DOM ready.
  fill_search_form(): orchestrates the full form interaction:
    1. Select search mode (dropdown/radio/none per config)
    2. Per-mode input_selector override or default
    3. Clear field, type query text
    4. Click search button (per-mode override or default)
    5. Wait for results (element_visible, url_change, network_idle, or delay)
    6. Check no_results_indicators to detect empty results

extractor.py:
  extract_results_table(): reads standard HTML table rows using configured
    row_selector and cell_selector. Maps column indices to field names.
  extract_ag_grid(): reads AG Grid JavaScript component cells.
  extract_detail(): extracts key-value pairs from detail pages using
    strategies: header_mapped_table, dl_list, css_selector, label_value_pairs.

pagination.py:
  paginate(): async generator that yields once per results page.
  Strategies: next_button (clicks until disabled), page_numbers (clicks 1,2,3...),
  infinite_scroll (scrolls until no new content), none (single page).

evidence.py:
  capture_evidence(): saves page.content() as HTML and page.screenshot() as PNG.
  Captured at stages: search_results, detail_page, error.
  Storage path: evidence/{source_id}/{run_id}/{stage}.html|.png

ai_fallback.py:
  should_use_ai_fallback(): returns True if extracted dict has fewer fields
    than expected based on field_map size.
  extract_with_ai(): sends page HTML + field_map to OpenAI API.
    The AI returns structured JSON matching expected canonical fields.
    Provides resilience when page structure changes break CSS selectors.

post_processors.py:
  apply_field_map(): takes raw dict with page-specific keys and translates
    to canonical names. Example: {"License No": "B00819"} -> {"license_number": "B00819"}

output.py:
  map_to_license_record(): applies status_map normalization, parses dates
    using configured date_formats, sets source metadata, returns LicenseRecord.
  write_output(): JSON serialization with indent formatting.
  upsert_to_db(): inserts or updates record in SQLite by license_number+source_id.

retry.py:
  with_retry(): wraps an async function with retry logic.
  Retries on: timeout, navigation_error, network_error (configurable).
  Uses exponential backoff: backoff_ms list [1000, 2000, 4000].

telemetry.py:
  init_db(): creates SQLite tables (events, records) if not exist.
  log_scrape_event(): inserts TelemetryEvent row.
  Used to monitor scraper health: which runs succeeded, how long they took,
  how many records were found, which used AI fallback, what errors occurred.

validate.py:
  load_config(yaml_path): reads YAML, parses into SiteConfig.
  Pydantic validation catches missing/invalid fields immediately,
  providing clear error messages before the browser is launched.
```

---

## 6. How to Add a New State Board

### No Code Changes Required

The engine is generic. Adding a new board means writing ONLY a config.yaml file.

### Step 1: Identify the Board Website

Visit the state board website. Determine:
- The URL for the license search page
- How the search form works (inputs, buttons, dropdowns)
- How results are displayed (table, AG Grid, listbox, cards)
- Whether clicking a result leads to a detail page
- The technology (Angular SPA, classic HTML, custom framework)

### Step 2: Create the Config Folder

```bash
mkdir lvs/adapters/scrapers/sites/NEW_BOARD
```

### Step 3: Write config.yaml

Copy from a similar archetype board and modify. Use browser DevTools (F12) to find:
- CSS selectors for inputs, buttons, result containers
- Text labels on detail pages (these become field_map keys)
- How pagination works (if applicable)

Minimal config.yaml template:

```yaml
identity:
  source_id: NEW_BOARD
  board_name: "New State Board of XYZ"
  state: "New State"
  base_url: "https://newboard.state.gov/search"
  archetype: classic_html_form

search:
  modes:
    - mode: license_number
      input_selector: "#licenseInput"
      button_selector: "#searchBtn"
    - mode: last_name
      input_selector: "#lastNameInput"
      button_selector: "#nameSearchBtn"
  results_wait:
    strategy: element_visible
    selector: "#resultsTable"
    no_results_indicators: ["No records found"]

results:
  type: table
  table:
    row_selector: "#resultsTable tbody tr"
    cell_selector: "td"
  has_detail_page: true
  detail_trigger:
    type: link_in_cell
    selector: "a.detail-link"

detail:
  field_map:
    "License Number:": license_number
    "Name:": licensee_full_name
    "License Type:": license_type
    "Status:": status
    "Expiration Date:": expiration_date
  back_navigation:
    strategy: browser_back

output:
  status_map:
    "Active": active
    "Inactive": inactive
    "Expired": expired
```

### Step 4: Test with Dry Run

```bash
python run.py --config sites/NEW_BOARD/config.yaml --mode license_number --query "TEST" --dry-run
```

### Step 5: Test with Headed Browser

```bash
python run.py --config sites/NEW_BOARD/config.yaml --mode license_number --query "REAL123" --headed
```

Watch the browser. Verify each step completes correctly.

### Step 6: Run for Real

```bash
python run.py --config sites/NEW_BOARD/config.yaml --mode license_number --query "REAL123"
```

### Step 7: Verify Output

Check:
- output/NEW_BOARD_{timestamp}.json has correct data
- evidence/NEW_BOARD/{run_id}/ has HTML and screenshots
- lvs_scrape.db has a telemetry event with status=success

---

## Quick Reference

| Task | Command |
|------|---------|
| Activate venv | .\.venv\Scripts\Activate.ps1 |
| Install deps | pip install -r lvs/adapters/scrapers/requirements.txt && playwright install |
| List boards | ls lvs/adapters/scrapers/sites/ |
| Run scraper | python run.py --config sites/BOARD/config.yaml --mode MODE --query "Q" |
| Debug (visible) | add --headed |
| Validate only | add --dry-run |
| View output | cat output/BOARD_*.json |
| View evidence | open evidence/BOARD/{run_id}/ in browser |

---

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| Browser closed unexpectedly | Timeout or crash | Increase transport.timeout_ms in config |
| Timeout waiting for selector | CSS selector not found on page | Inspect with F12, update selector in config |
| AI fallback triggered | Rule-based extraction found too few fields | Verify detail.field_map keys match page labels |
| No results found | Query invalid or results_wait selector wrong | Test with --headed to see page state |
| ValidationError on config load | YAML structure incorrect | Read Pydantic error message for exact field |
| Network error / connection reset | Proxy or firewall blocking | Configure transport.proxy in config |
| playwright not installed | Missing browser binaries | Run: playwright install |
