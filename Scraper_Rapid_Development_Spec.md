# Scraper Rapid Development Spec
## Spec-Driven Development for 200+ License Board Scrapers

**Purpose.** Define a uniform, AI-codeable specification format and shared engine so that building a new license board scraper is a **configuration exercise** (30–60 minutes per board) rather than a bespoke coding effort (4–8 hours per board).

**Audience.** AI coding assistants (Claude, Copilot) and the human developer directing them.

**Key insight.** The 7 existing scrapers reveal **4 platform archetypes** that cover the vast majority of US license boards. A spec-driven approach defines the archetype engine once, then each new board is a YAML/JSON config + fixture bundle.

---

## 1. Analysis of Existing Code — Consistency Report

### 1.1 What works well

| Strength | Where observed |
|---|---|
| Multi-strategy detail extraction (dt/dd → table → label/sibling → raw text) | All Nevada scrapers, Kansas |
| Pagination support with View-click-and-back pattern | Nevada Chiro, NVADGC, PT |
| Structured JSON output with metadata (state, board, search_criteria, scraped_at) | Maryland, Nevada |
| Logging with Python `logging` module | All except Kansas |
| CLI with `argparse` | Maryland, MA, all Nevada |
| Dropdown + text + search-button flow reusable across Thentia Cloud sites | Nevada Chiro, NVADGC, PT, MedBoard |

### 1.2 Consistency problems (blocking scale)

| Problem | Impact | Files affected |
|---|---|---|
| **Two browser engines** — Kansas uses Playwright; all others use Selenium | Can't share utilities; dual dependency | All |
| **Copy-pasted `build_driver()`** — identical 15-line function in 5 files | Maintenance burden; no single point of change | MD, MA, NV×4 |
| **Copy-pasted `set_search_by()`** — identical 40-line function in 4 files | Same logic duplicated verbatim | NV×4 |
| **Copy-pasted `fill_search_text()`** — identical function in 4 files | Same | NV×4 |
| **Copy-pasted `click_search_button()`** — identical function in 4 files | Same | NV×4 |
| **Copy-pasted `scrape_detail_page()`** — ~100-line function duplicated | Same | NV Chiro, NVADGC, PT |
| **No shared output schema** — each file defines its own dict shape | Can't pipeline results into a common DB loader | All |
| **No shared config** — BASE_URL, WAIT_TIMEOUT, board metadata inline | Adding a board = writing a new file from scratch | All |
| **Hardcoded proxy credentials** (Kansas `nid`/`password` at module top) | Security risk; blocks portability | Kansas |
| **Mixed error reporting** — emoji prints vs logging vs silent failures | No unified observability | Kansas vs others |
| **No retry logic** on navigation steps | Fragile against transient failures | All |
| **No evidence capture** — no screenshots, no HTML archival | Can't diagnose failures post-hoc | All |
| **No test fixtures** — no committed HTML for offline testing | Can't validate without live network | All |

### 1.3 Platform archetypes identified

From analyzing the 7 scripts and ~20 board URLs referenced, four archetypes emerge:

| Archetype | Platform/Pattern | Boards using it | Transport | Example |
|---|---|---|---|---|
| **A: Thentia Cloud** | `*.thentiacloud.net/webs/*/register/` | NV MedBoard, NV Chiro, NV NVADGC, NV PT, ~30+ others nationally | Browser (JS SPA) | `nevada_medboard_scraper_v1.py` |
| **B: AG Grid / Angular SPA** | Custom Angular/React apps with AG Grid results | MA, several other states | Browser (JS SPA) | `MA_AllExceptMDDO_scraper_v1.py` |
| **C: Classic HTML Form** | Standard server-rendered HTML with form POST and table results | KS Dental, KS Optometry, KS KSBHADA, MD, many small-state boards | Browser or HTTP | `kansas_all_boards.py`, `Maryland.py` |
| **D: State Portal / SSO** | Centralized state portal with sub-board routing (e.g., Kansas `ssrv-*` pattern) | KS (8 boards via 1 portal), FL MQA (13 boards), others | HTTP or Browser | `kansas_all_boards.py` |

---

## 2. Architecture Decision: Playwright as Unified Transport

### 2.1 Rationale

The existing documentation (`Web_Scraping_License_Fetch_Architecture.md`) specifies `httpx` + `lxml` for the HTTP scrape pattern. This works for **server-rendered HTML** boards (archetype C/D). However, **the majority of modern boards** use JavaScript SPAs (archetypes A and B) that require a browser.

**Decision:** Use **Playwright** (async) as the unified transport layer for all archetypes.

| Factor | Playwright | Selenium | httpx + lxml |
|---|---|---|---|
| JS-rendered SPA support | Yes | Yes | No |
| Speed (non-SPA) | Fast (Chromium) | Slower (WebDriver protocol) | Fastest |
| Headless reliability | Excellent | Good | N/A |
| Auto-wait / smart selectors | Built-in | Manual waits | N/A |
| Evidence capture (screenshot + HTML) | Native `.screenshot()` + `.content()` | Requires extra setup | N/A for screenshot |
| Network interception (for API-like boards) | Yes (`route()`) | Limited | N/A |
| Maintainability | Auto-updating browsers | Manual driver management | N/A |
| Concurrency model | Native async | Thread-based | Native async |

For boards that are purely HTTP (archetype C/D without JS), Playwright still works and the marginal overhead (~200ms cold start) is acceptable given the 2-second politeness delay between requests.

### 2.2 Alignment with enterprise architecture

The layered engine (L0–L7) from `Web_Scraping_License_Fetch_Architecture.md` maps as follows:

| Layer | Enterprise doc specifies | This spec implements |
|---|---|---|
| L0 — SiteConfig | YAML per board | YAML per board (identical) |
| L1 — Transport | `httpx` + Cloud NAT | Playwright browser context + Cloud NAT proxy |
| L2 — Navigation | Template-driven NavSteps | Template-driven Playwright actions |
| L3 — Capture | HTML + screenshot to GCS | `page.content()` + `page.screenshot()` to local/GCS |
| L4 — Deterministic Parse | CSS/XPath selectors + post-processors | Playwright locators + post-processor chain |
| L5 — AI Fallback | GPT-4.1 agent on evidence | Same (operates on captured HTML/PNG) |
| L6 — Validate | Pydantic LicenseRecord | Pydantic LicenseRecord (identical) |
| L7 — Telemetry | scrape_event + ai_touchpoint | Structured logging + DB writes |

---

## 3. The Spec Format — Per-Board Configuration

### 3.1 File structure for a new board

```
lvs/adapters/scrapers/
├── engine/                         # Shared engine (written once)
│   ├── __init__.py
│   ├── browser.py                  # Playwright browser pool + context factory
│   ├── navigator.py                # Executes NavSteps from config
│   ├── extractor.py                # Runs SelectorPack against page
│   ├── detail_scraper.py           # Multi-strategy detail extraction
│   ├── pagination.py               # Generic pagination handler
│   ├── post_processors.py          # Named transform library
│   ├── evidence.py                 # Screenshot + HTML capture
│   ├── output.py                   # LicenseRecord serialization
│   ├── retry.py                    # Retry with backoff
│   └── models.py                   # Pydantic models (SiteConfig, LicenseRecord, etc.)
├── sites/
│   ├── NV_MEDBOARD/
│   │   ├── config.yaml             # THE SPEC — this is what AI generates
│   │   ├── SPEC.md                 # Human-readable notes
│   │   └── fixtures/
│   │       ├── search_results.html
│   │       ├── detail_page.html
│   │       └── no_results.html
│   ├── NV_CHIRO/
│   │   ├── config.yaml
│   │   └── ...
│   ├── MD_BOP/
│   │   ├── config.yaml
│   │   └── ...
│   └── ... (200+ boards)
└── run.py                          # CLI entrypoint
```

### 3.2 The `config.yaml` specification (THE key artifact for AI to generate)

```yaml
# ============================================================
# BOARD CONFIGURATION — This is the ONLY file needed per board
# ============================================================

# ─── A. Identity ───────────────────────────────────────────
identity:
  source_id: "NV_MEDBOARD"
  board_name: "Nevada State Board of Medical Examiners"
  state: "NV"
  country: "US"
  profession_codes: ["MD", "DO", "PA"]
  base_url: "https://nsbme.us.thentiacloud.net/webs/nsbme/register/#"
  archetype: "thentia_cloud"          # one of: thentia_cloud | ag_grid_spa | classic_html_form | state_portal

# ─── B. Search interface ───────────────────────────────────
search:
  # What search modes does this board support?
  modes:
    - mode: "license_number"
      dropdown_value: "License Number"    # exact text in the Search By dropdown
    - mode: "last_name"
      dropdown_value: "Last Name"
    - mode: "first_name"
      dropdown_value: "First Name"

  # How to interact with the search form
  form:
    # Search-By dropdown (if present)
    search_by_dropdown:
      strategy: "select"                  # select | custom_dropdown | radio | none
      selector: "select"                  # CSS selector for the <select> element
    
    # Search text input
    search_input:
      selector: "input[placeholder*='search text']"
      fallback_selectors:
        - "input[type='text']"
        - "input[type='search']"
    
    # Search button
    search_button:
      selector: "button[type='submit']"
      fallback_selectors:
        - "[class*='search-icon']"
        - "[aria-label='Search']"
        - "img[alt*='search']"

  # Wait conditions after search
  results_wait:
    strategy: "element_visible"           # element_visible | url_change | network_idle | delay
    selector: "table tbody tr"
    timeout_ms: 20000
    no_results_indicators:
      - "no results"
      - "no records found"
      - "0 result"

# ─── C. Results extraction ─────────────────────────────────
results:
  # How are results presented?
  type: "table"                           # table | card_list | single_record | ag_grid
  
  # Table-based results
  table:
    row_selector: "table tbody tr"
    cell_selector: "td"
    columns:                              # map column index → field name
      0: "name"
      1: "license_number"
      2: "license_type"
      3: "status"
      4: "expiration_date"
  
  # Does this board require clicking into detail pages?
  has_detail_page: true
  detail_trigger:
    type: "view_button"                   # view_button | row_click | link_in_cell
    selector: "a:has-text('View'), button:has-text('View')"
    
  # Pagination
  pagination:
    enabled: true
    strategy: "next_button"               # next_button | page_numbers | infinite_scroll | none
    next_selector: "a[title='Next Page'], a.next, a[aria-label='Next']"
    disabled_class: "disabled"

# ─── D. Detail page extraction ─────────────────────────────
detail:
  # Wait condition for detail page
  wait:
    strategy: "url_change"                # url_change | element_visible | delay
    timeout_ms: 15000
    fallback_selectors:
      - "div[class*='profile']"
      - "div[class*='detail']"
      - "dl"
      - "dt"

  # Extraction strategies (tried in order until one yields results)
  strategies:
    - type: "dt_dd"                       # <dt>Label</dt><dd>Value</dd>
    - type: "label_sibling"              # <label>Label</label><span>Value</span>
    - type: "field_label_value"          # class*='field-label' + class*='field-value'
    - type: "two_column_table"           # <tr><td>Label</td><td>Value</td></tr>
    - type: "four_column_table"          # <tr><td>L1</td><td>V1</td><td>L2</td><td>V2</td></tr>
    - type: "header_mapped_table"        # <thead> headers + <tbody> data rows

  # Field mapping — map raw scraped labels to canonical field names
  field_map:
    "License Number": "license_number"
    "License No": "license_number"
    "Lic No": "license_number"
    "License #": "license_number"
    "First Name": "first_name"
    "Last Name": "last_name"
    "Name": "full_name"
    "Licensee Name": "full_name"
    "License Type": "license_type"
    "License Status": "status"
    "Status": "status"
    "Issue Date": "issue_date"
    "Original License Date": "issue_date"
    "Effective Date": "effective_date"
    "Expiration Date": "expiration_date"
    "Exp Date": "expiration_date"
    "Year Expire": "expiration_date"
    "Address": "address"
    "City": "city"
    "State": "state_code"
    "Discipline": "disciplinary_actions"
    "Board Actions": "disciplinary_actions"

  # Multi-value sections (tables within the detail page)
  sections:
    - name: "Board Actions"
      type: "header_mapped_table"
      field: "disciplinary_actions"
    - name: "Place of Practice"
      type: "header_mapped_table"
      field: "practice_locations"

  # Navigation back to results after detail extraction
  back_navigation:
    strategy: "browser_back"              # browser_back | breadcrumb_click | url_navigate
    wait_after_ms: 1500

# ─── E. Output mapping ─────────────────────────────────────
output:
  # Map to canonical LicenseRecord fields
  license_record:
    license_number: "{license_number}"
    licensee_first_name: "{first_name}"
    licensee_last_name: "{last_name}"
    licensee_full_name: "{full_name}"
    license_type: "{license_type}"
    status: "{status}"
    effective_date: "{issue_date}"
    expiration_date: "{expiration_date}"
    disciplinary_actions: "{disciplinary_actions}"
    address: "{address}"
    source_url: "{_source_url}"
  
  # Status normalization
  status_map:
    "active": "active"
    "clear/active": "active"
    "current": "active"
    "valid": "active"
    "inactive": "inactive"
    "expired": "expired"
    "lapsed": "expired"
    "suspended": "suspended"
    "revoked": "revoked"
    "surrendered": "revoked"
    "probation": "probation"

  # Date parsing formats (tried in order)
  date_formats:
    - "%m/%d/%Y"
    - "%Y-%m-%d"
    - "%m-%d-%Y"
    - "%B %d, %Y"
    - "%b %d, %Y"

# ─── F. Transport policy ───────────────────────────────────
transport:
  browser: "chromium"                     # chromium | firefox | webkit
  headless: true
  viewport: { width: 1920, height: 1080 }
  timeout_ms: 60000
  navigation_timeout_ms: 30000
  rate_limit:
    delay_between_requests_ms: 2000       # politeness delay
    max_concurrent: 1
  retry:
    max_attempts: 3
    backoff_ms: [1000, 2000, 4000]
    retry_on: ["timeout", "navigation_error", "network_error"]
  proxy:
    enabled: false                        # set true if corporate proxy needed
    # server, username, password loaded from env vars, never in config
  user_agent: "LVS-LicenseVerifier/1.0"

# ─── G. Evidence capture ───────────────────────────────────
evidence:
  capture_html: true
  capture_screenshot: true
  capture_on: ["search_results", "detail_page", "error"]
  storage: "local"                        # local | gcs
  local_path: "./evidence/{source_id}/{run_id}/"

# ─── H. Compliance ────────────────────────────────────────
compliance:
  tos_review_date: "2026-05-15"
  tos_review_ticket: "LVS-101"
  requires_captcha: false
  requires_login: false
  robots_txt_compliant: true
```

---

## 4. Shared Engine — Key Components

### 4.1 `LicenseRecord` — Canonical output model

```python
from pydantic import BaseModel, Field
from datetime import date, datetime
from typing import Optional
from enum import Enum

class LicenseStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    EXPIRED = "expired"
    SUSPENDED = "suspended"
    REVOKED = "revoked"
    PROBATION = "probation"
    UNKNOWN = "unknown"

class LicenseRecord(BaseModel):
    """Canonical output from any scraper — the contract every board config maps to."""
    # Core identifiers
    source_id: str
    license_number: str
    
    # Licensee identity
    licensee_first_name: Optional[str] = None
    licensee_last_name: Optional[str] = None
    licensee_full_name: Optional[str] = None
    licensee_middle_name: Optional[str] = None
    licensee_suffix: Optional[str] = None
    
    # License details
    license_type: Optional[str] = None
    profession_code: Optional[str] = None
    status: LicenseStatus = LicenseStatus.UNKNOWN
    effective_date: Optional[date] = None
    expiration_date: Optional[date] = None
    issue_date: Optional[date] = None
    last_renewal_date: Optional[date] = None
    
    # Location
    address: Optional[str] = None
    city: Optional[str] = None
    state_code: Optional[str] = None
    zip_code: Optional[str] = None
    
    # Disciplinary
    disciplinary_actions: list[dict] = Field(default_factory=list)
    
    # Metadata
    source_url: str
    scraped_at: datetime
    evidence_html_path: Optional[str] = None
    evidence_screenshot_path: Optional[str] = None
    raw_fields: dict = Field(default_factory=dict)  # all raw key-value pairs before mapping
    used_ai: bool = False
```

### 4.2 `SiteConfig` — Pydantic model for config.yaml

```python
from pydantic import BaseModel
from typing import Literal

class SiteIdentity(BaseModel):
    source_id: str
    board_name: str
    state: str
    country: str = "US"
    profession_codes: list[str]
    base_url: str
    archetype: Literal["thentia_cloud", "ag_grid_spa", "classic_html_form", "state_portal"]

class SearchMode(BaseModel):
    mode: str
    dropdown_value: str | None = None

class SearchByDropdown(BaseModel):
    strategy: Literal["select", "custom_dropdown", "radio", "none"]
    selector: str | None = None

class ElementSelector(BaseModel):
    selector: str
    fallback_selectors: list[str] = []

class ResultsWait(BaseModel):
    strategy: Literal["element_visible", "url_change", "network_idle", "delay"]
    selector: str | None = None
    timeout_ms: int = 20000
    no_results_indicators: list[str] = []

class SearchConfig(BaseModel):
    modes: list[SearchMode]
    form: dict  # search_by_dropdown, search_input, search_button
    results_wait: ResultsWait

class DetailTrigger(BaseModel):
    type: Literal["view_button", "row_click", "link_in_cell"]
    selector: str

class PaginationConfig(BaseModel):
    enabled: bool = False
    strategy: Literal["next_button", "page_numbers", "infinite_scroll", "none"] = "none"
    next_selector: str | None = None
    disabled_class: str = "disabled"

class ResultsConfig(BaseModel):
    type: Literal["table", "card_list", "single_record", "ag_grid"]
    table: dict | None = None
    has_detail_page: bool = True
    detail_trigger: DetailTrigger | None = None
    pagination: PaginationConfig = PaginationConfig()

class DetailConfig(BaseModel):
    wait: dict
    strategies: list[dict]
    field_map: dict[str, str]
    sections: list[dict] = []
    back_navigation: dict

class TransportConfig(BaseModel):
    browser: str = "chromium"
    headless: bool = True
    timeout_ms: int = 60000
    rate_limit: dict
    retry: dict
    proxy: dict = {"enabled": False}

class SiteConfig(BaseModel):
    identity: SiteIdentity
    search: SearchConfig
    results: ResultsConfig
    detail: DetailConfig
    output: dict
    transport: TransportConfig
    evidence: dict
    compliance: dict
```

### 4.3 Engine execution flow

```python
async def verify_license(config: SiteConfig, query: SearchQuery) -> list[LicenseRecord]:
    """
    Universal entry point. Loads config, drives browser, returns records.
    This function is identical for ALL 200+ boards — behavior is config-driven.
    """
    async with BrowserPool.acquire(config.transport) as page:
        # L1 — Transport: navigate to board
        await navigate_to_search(page, config)
        
        # L2 — Navigation: fill form and search
        await fill_search_form(page, config.search, query)
        
        # L3 — Capture: screenshot search results
        await capture_evidence(page, config, stage="search_results")
        
        # Check for no results
        if await is_no_results(page, config.search.results_wait):
            return []
        
        # L4 — Extract: get results from page
        records = []
        async for result_page in paginate(page, config.results.pagination):
            if config.results.has_detail_page:
                # Click each detail → extract → back
                for row in await get_result_rows(page, config.results):
                    detail_data = await extract_detail(page, row, config)
                    records.append(detail_data)
            else:
                # Extract directly from results table
                records.extend(await extract_from_table(page, config.results))
        
        # L6 — Validate: map to LicenseRecord
        license_records = [
            map_to_license_record(raw, config)
            for raw in records
        ]
        
        return license_records
```

---

## 5. AI-Assisted Rapid Development Workflow

### 5.1 The prompt template for generating a new board config

When you need to add a new board, give the AI coding assistant this prompt:

```markdown
## Task: Generate scraper config for [BOARD NAME]

**Board URL:** [URL]
**State:** [XX]
**Profession codes:** [list]

**Instructions:**
1. Open the URL in a browser and identify:
   - The search interface (dropdown, text input, button)
   - The results format (table, cards, AG grid)
   - Whether there's a detail page (View button, row click, link)
   - The fields available on the detail page
2. Classify the archetype: thentia_cloud | ag_grid_spa | classic_html_form | state_portal
3. Generate a `config.yaml` following the schema in `docs/Scraper_Rapid_Development_Spec.md` §3.2
4. Generate fixture HTML files by capturing page source at each stage
5. Test the config against the engine using: `python run.py --config sites/XX_BOARD/config.yaml --query "Smith" --mode last_name --dry-run`

**Reference configs:**
- Thentia Cloud: `sites/NV_MEDBOARD/config.yaml`
- AG Grid SPA: `sites/MA_HEALTH/config.yaml`
- Classic HTML: `sites/MD_BOP/config.yaml`
- State Portal: `sites/KS_DENTAL/config.yaml`
```

### 5.2 Development velocity targets

| Activity | Time target | How |
|---|---|---|
| Identify board archetype | 2 min | AI visits URL, matches to known archetype |
| Generate config.yaml | 10 min | AI fills template from archetype + observed selectors |
| Capture fixtures | 10 min | Automated fixture capture script |
| Validate config | 5 min | `run.py --dry-run` against fixtures |
| Fix edge cases | 10–20 min | Iterate on selectors/field_map |
| **Total per board** | **30–50 min** | vs 4–8 hours today |

At 30–50 min per board × 200 boards = **100–170 hours** (vs 800–1600 hours without the spec).
With parallel AI-assisted development: **2–4 weeks** for full coverage.

### 5.3 Archetype-specific engine behaviors

The `archetype` field in config selects pre-built behavior that the engine applies automatically:

#### `thentia_cloud`
- Auto-handles: `<select>` dropdown for Search By, placeholder-based text input, search icon click
- Default result wait: table rows appear
- Default detail: View button click → dt/dd + label/sibling + field-label/field-value extraction
- Default back: `driver.back()` + wait for results to reappear
- **~40% of boards nationally use Thentia Cloud**

#### `ag_grid_spa`
- Auto-handles: Virtual-scrolling detection, header extraction from `div.ag-header-cell-text`
- Scroll-to-load pagination (scroll container detection)
- Cell extraction via `div[role='gridcell']`
- Deduplication by license number (handles re-rendered rows)

#### `classic_html_form`
- Auto-handles: `<form>` detection, `<input>` fill by name/id/placeholder
- Submit via `input[type='submit']` or `button[type='submit']`
- Table parsing with `<th>` header mapping
- Link-based detail navigation

#### `state_portal`
- Auto-handles: Multi-board routing (sub-boards within one portal)
- Sub-board selection via config
- Shared session across board switches

---

## 6. Quality Gates — What "Done" Means for a Board Config

### 6.1 Minimum acceptance criteria per board

- [ ] `config.yaml` passes schema validation (`python -m engine.validate sites/XX_BOARD/config.yaml`)
- [ ] Fixture HTML files committed for: search results, detail page, no-results page
- [ ] Dry-run against fixtures produces valid `LicenseRecord` with all required fields
- [ ] Live smoke test with 1 known license number returns correct data
- [ ] Evidence capture (HTML + screenshot) works in both headless and headed mode
- [ ] Status normalization maps at least the 3 most common statuses for this board
- [ ] Date parsing succeeds for the board's date format
- [ ] Field map covers all fields visible on the detail page
- [ ] `compliance.tos_review_date` is set and within 365 days
- [ ] No hardcoded credentials anywhere in config or code

### 6.2 Automated validation pipeline

```bash
# Validate config schema
python -m engine.validate sites/XX_BOARD/config.yaml

# Run against fixtures (offline, deterministic)
python -m engine.test_fixtures sites/XX_BOARD/

# Live smoke test (requires network)
python run.py --config sites/XX_BOARD/config.yaml \
    --mode license_number --query "KNOWN_LICENSE" \
    --evidence-dir ./evidence/smoke/

# Regression test (compare against baseline)
python -m engine.regression sites/XX_BOARD/ --baseline ./baselines/XX_BOARD.json
```

---

## 7. Migration Plan — From Current Scripts to Spec-Driven

### Phase 1: Build the shared engine (1 week)
1. Implement `engine/browser.py` — Playwright browser pool
2. Implement `engine/navigator.py` — config-driven navigation
3. Implement `engine/extractor.py` — multi-strategy detail extraction
4. Implement `engine/pagination.py` — generic paginator
5. Implement `engine/models.py` — SiteConfig + LicenseRecord Pydantic models
6. Implement `engine/post_processors.py` — date parsing, status normalization, name cleanup
7. Implement `engine/evidence.py` — HTML + screenshot capture
8. Implement `engine/output.py` — JSON + DB output

### Phase 2: Convert existing scrapers to configs (3 days)
1. `NV_MEDBOARD/config.yaml` — from `nevada_medboard_scraper_v1.py`
2. `NV_CHIRO/config.yaml` — from `nevada_chiropractic_scraper_v1.py`
3. `NV_NVADGC/config.yaml` — from `nevada_nvadgc_scraper_v1.py`
4. `NV_PT/config.yaml` — from `nevada_pt_scraper_v1.py`
5. `MA_HEALTH/config.yaml` — from `MA_AllExceptMDDO_scraper_v1.py`
6. `MD_BOP/config.yaml` — from `Maryland.py`
7. `KS_DENTAL/config.yaml` + 7 more — from `kansas_all_boards.py`

### Phase 3: AI-assisted bulk generation (2–4 weeks)
- Use the prompt template (§5.1) to generate configs for remaining 180+ boards
- Batch by archetype (do all Thentia Cloud boards together, etc.)
- Validate each batch against fixtures before merging

---

## 8. Key Design Decisions Summary

| Decision | Choice | Rationale |
|---|---|---|
| Browser engine | Playwright (unified) | Handles both SPA and classic; better API than Selenium; auto-wait |
| Config format | YAML | Human-readable, AI-friendly, diffable, validatable via JSON schema |
| One engine, N configs | Yes | Matches enterprise architecture; eliminates code duplication |
| Archetype system | 4 archetypes | Covers observed patterns; extensible without engine changes |
| Detail extraction | Multi-strategy cascade | Robust against layout changes; works across all archetypes |
| Output format | Pydantic `LicenseRecord` | Typed, validated, serializable; feeds into downstream pipeline |
| Evidence capture | Every run | Required for debugging, compliance, and AI fallback path |
| AI fallback | Optional per-board | Engine supports it; not required for initial config generation |
| Fixture testing | Mandatory per board | Enables offline validation; catches regressions |
| Rate limiting | Config-driven per board | Respects source; adjustable without code change |

---

## 9. Appendix: Quick Reference for AI Coding Assistants

### When generating a new board config, always:
1. Start from the archetype template closest to the target board
2. Visit the URL and identify all CSS selectors empirically
3. Map every visible field label to the canonical `field_map`
4. Include `no_results_indicators` specific to the board's language
5. Set `date_formats` matching the board's actual date rendering
6. Capture fixture HTML at every navigation stage
7. Never put secrets in config — proxy credentials come from env vars

### When fixing a broken config:
1. Run with `--headed` to watch the browser
2. Check evidence screenshots for what the page actually looks like
3. Update selectors in `config.yaml` — never modify engine code for one board
4. If the board changed its DOM: update fixtures, update selectors, validate
5. If a new extraction pattern is needed: propose an engine enhancement, not a per-board hack

### Common pitfalls:
- Thentia Cloud sites load slowly — set `timeout_ms >= 30000`
- AG Grid virtualizes rows — must scroll to load all data
- Some boards have maintenance windows — capture the maintenance page as a fixture
- Date formats vary between the results table and the detail page on the same board
- "View" buttons may be `<a>`, `<button>`, `<span>`, or `<input>` — use text-based selector
- After browser back, elements may be stale — re-query the DOM
