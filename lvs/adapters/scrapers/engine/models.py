"""Pydantic v2 models — canonical contract for all boards."""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class BoardUnavailableError(Exception):
    """Raised when a board site is unreachable or erroring at the source —
    connection timeout, TLS handshake drop, or an HTTP 5xx / server-error page.

    This is distinct from "no records found": the board never gave a usable
    response, so the row should be skipped (and retried later) rather than
    failed as a data mismatch. Propagated up from navigation and re-raised
    through the browser search wrapper so the ladder can classify it.
    """


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class LicenseStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    EXPIRED = "expired"
    SUSPENDED = "suspended"
    REVOKED = "revoked"
    PROBATION = "probation"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Canonical output record
# ---------------------------------------------------------------------------

class LicenseRecord(BaseModel):
    source_id: str
    license_number: str

    licensee_first_name: Optional[str] = None
    licensee_last_name: Optional[str] = None
    licensee_full_name: Optional[str] = None
    licensee_middle_name: Optional[str] = None
    licensee_suffix: Optional[str] = None

    license_type: Optional[str] = None
    profession_code: Optional[str] = None
    status: LicenseStatus = LicenseStatus.UNKNOWN
    effective_date: Optional[date] = None
    expiration_date: Optional[date] = None
    issue_date: Optional[date] = None
    last_renewal_date: Optional[date] = None

    address: Optional[str] = None
    city: Optional[str] = None
    state_code: Optional[str] = None
    zip_code: Optional[str] = None

    disciplinary_actions: list[dict] = Field(default_factory=list)

    source_url: str = ""
    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    evidence_html_path: Optional[str] = None
    evidence_screenshot_path: Optional[str] = None
    raw_fields: dict = Field(default_factory=dict)
    used_ai: bool = False
    partial_result: bool = False

    # ----- Orchestrator provenance fields (populated by output_emitter) -----
    match_method: Optional[str] = None       # exact_license | exact_name |
                                             # npi_substituted_exact | ai_fuzzy |
                                             # tiebreak_provider_type | none
    npi_discrepancy_used: bool = False
    npi_other_name_match: bool = False
    npi_source_flag: bool = False
    ai_fallback_used: bool = False
    ai_layer: Optional[int] = None
    manual_flag: bool = False
    secondary_check_passed: bool = False
    out_of_state_state: Optional[str] = None   # FL T-license: state name from Out of State tab
    provider_type_matched: bool = False
    fuzzy_score: Optional[float] = None
    fuzzy_breakdown: Optional[dict] = None
    tiebreaker_used: bool = False
    weight_profile_used: Optional[str] = None
    evidence_dir: Optional[str] = None
    trace_path: Optional[str] = None
    attempts_used: int = 0
    failure_reason: Optional[str] = None     # one of trace.REASON_* codes when not Pass


# ---------------------------------------------------------------------------
# Search query
# ---------------------------------------------------------------------------

class SearchQuery(BaseModel):
    mode: str
    query: str = ""
    # Structured fields — when set, they take precedence over `query` token splitting.
    license_number: Optional[str] = None
    first_name: Optional[str] = None
    middle_name: Optional[str] = None   # optional; boards with a dedicated middle-name field use {middle} in extra_inputs
    last_name: Optional[str] = None
    # Orthogonal filter modifiers — applied alongside the chosen combo mode.
    license_type: Optional[str] = None    # sub-category of license (Active, Permanent, ...)
    provider_type: Optional[str] = None   # kind of provider (MD, DO, RN, LPN, PA, NP, ...)

    @model_validator(mode="after")
    def _auto_join_query(self) -> "SearchQuery":
        # If `query` is empty but explicit fields are set, build a sensible joined string
        # so legacy code reading `query.query` still gets a usable value. license_type,
        # provider_type are filter modifiers — excluded from join. middle_name sits between
        # first and last in the canonical order.
        if not self.query:
            parts = [v for v in (self.license_number, self.first_name, self.middle_name, self.last_name) if v]
            if parts:
                object.__setattr__(self, "query", " ".join(parts))
        return self


# Canonical combination mode names. The engine synthesises these at runtime by
# merging the constituent single-field modes (license_number / first_name / last_name)
# from the same config — no per-board YAML needed when the single-field modes exist.
# middle_name is always optional in *_mid_* modes: synthesis falls back gracefully when
# the board config has no dedicated middle-name field.
COMBO_MODES = frozenset({
    "first_and_last",
    "license_and_first",
    "license_and_last",
    "license_first_last",
    "first_mid_last",          # first + middle + last (middle silently skipped if board has no mid field)
    "license_first_mid_last",  # license + first + middle + last
})


# ---------------------------------------------------------------------------
# Telemetry event
# ---------------------------------------------------------------------------

class TelemetryEvent(BaseModel):
    run_id: str
    source_id: str
    stage: str
    status: str
    duration_ms: int = 0
    record_count: int = 0
    used_ai: bool = False
    error_msg: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    partial_result: bool = False
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Site config — nested models
# ---------------------------------------------------------------------------

class SiteIdentity(BaseModel):
    source_id: str
    board_name: str
    state: str
    country: str = "US"
    profession_codes: list[str] = Field(default_factory=list)
    profession_code_map: dict[str, str] = Field(default_factory=dict)
    base_url: str
    archetype: Literal[
        "thentia_cloud", "ag_grid_spa", "classic_html_form", "state_portal",
        "socrata_api", "socrata_bulk_csv", "pdf_bulk", "csv_bulk", "certemy",
        "json_api", "datatables_jsapi", "filemaker_webdirect", "pega_constellation",
        "psypact", "ny_credentials",
    ]
    # Optional explicit capability list — overrides auto-derivation in check_board_capability.
    # Use when the auto-derivation is wrong (e.g. dropdown-switched single-field boards).
    capabilities: Optional[list[str]] = None
    # Optional CSS selectors for orthogonal filter dropdowns. When set and the corresponding
    # SearchQuery field is populated, the engine sets the dropdown via the extra_selects
    # pattern (label-first, value-fallback) alongside the primary input fill.
    license_type_selector: Optional[str] = None
    provider_type_selector: Optional[str] = None
    # Maps psv_prov_type code → dropdown option value (or label) for the provider_type_selector.
    # Used by first_and_last_typed ladder rung to pre-set the board/profession dropdown.
    # Store the <option value="..."> string so Playwright's select_option(value=...) matches exactly.
    prov_type_values: dict[str, str] = Field(default_factory=dict)
    skip: bool = False
    skip_reason: str = ""
    # When True, the name-search ladder will retry with first and last names swapped if the
    # standard order returns no results. Useful when input data stores names in "Last First"
    # order without a comma separator (e.g. "Tucker Robin" could mean Last=Tucker, First=Robin
    # OR Last=Robin, First=Tucker depending on the source system).
    try_name_swap: bool = False


class SearchMode(BaseModel):
    mode: str
    dropdown_value: Optional[str] = None
    input_selector: Optional[str] = None   # per-mode override for the search text input
    button_selector: Optional[str] = None  # per-mode override for the search submit button
    # Additional text inputs to fill alongside the primary one (e.g. first name + last name).
    # Key = CSS selector, value = template: {q} = full query, {first} = tokens before last
    # space, {last} = last space-separated token.
    extra_inputs: dict[str, str] = Field(default_factory=dict)
    # Additional <select> dropdowns to set alongside the primary input.
    # Key = CSS selector, value = option label or value to select (exact text).
    extra_selects: dict[str, str] = Field(default_factory=dict)
    # Angular Ivy reactive-form support: key in FormGroup.controls to set via JS evaluate.
    # When set, fill_search_input() traverses __ngContext__ LView to find the FormGroup
    # (identified by _hasOwnPendingAsyncValidator) and calls controls[key].setValue(query).
    # Needed for boards where Playwright fill()/keyboard.type() don't update the FormControl.
    angular_formgroup_key: Optional[str] = None
    # Click this selector before filling inputs for this mode (e.g. switch to a different tab).
    pre_click: Optional[str] = None
    # Evaluate this JS expression to submit instead of clicking the submit button.
    # Use when the submit button's click event is intercepted by JS (Spring Web Flow, etc.)
    # and a direct form.submit() is needed. Example: "document.querySelector('#myForm form').submit()"
    submit_js: Optional[str] = None
    # When set, apply this template to produce the value typed into the primary search input.
    # Supports the same tokens as extra_inputs: {first}, {last}, {license}, {q}, etc.
    # Example: "{last}, {first}" for boards that require "LastName, FirstName" format.
    query_template: Optional[str] = None


class ElementSelector(BaseModel):
    selector: str
    fallback_selectors: list[str] = Field(default_factory=list)


class SearchByDropdown(BaseModel):
    strategy: Literal["select", "custom_dropdown", "radio", "slds_combobox", "none"] = "none"
    selector: Optional[str] = None


class ResultsWait(BaseModel):
    strategy: Literal["element_visible", "url_change", "network_idle", "delay", "ajax_row_count"] = "element_visible"
    selector: Optional[str] = None
    timeout_ms: int = 20000
    no_results_indicators: list[str] = Field(default_factory=list)
    # ajax_row_count: poll `selector` until row count > min_rows or timeout. Stabilises
    # the count across `stable_ticks` consecutive ticks before returning.
    min_rows: int = 1
    stable_ticks: int = 2
    poll_interval_ms: int = 400


class SearchForm(BaseModel):
    search_by_dropdown: SearchByDropdown = Field(default_factory=SearchByDropdown)
    search_input: ElementSelector = Field(default_factory=lambda: ElementSelector(selector="input[type='text']"))
    search_button: ElementSelector = Field(default_factory=lambda: ElementSelector(selector="button[type='submit']"))
    # Angular reactive forms don't pick up Playwright fill() — use keyboard.type() to fire real key events
    use_keyboard_type: bool = False
    # Extra wait (ms) after navigation before filling the form. Used for boards where a JS
    # challenge (e.g. Cloudflare managed challenge) must complete before form submission.
    post_navigate_wait_ms: int = 0
    # Wait for networkidle after navigation before filling the form. Needed for ASP.NET
    # UpdatePanel boards (e.g. CT eLicense) where form inputs render after page load fires.
    wait_for_networkidle: bool = False


class SearchConfig(BaseModel):
    modes: list[SearchMode]
    form: SearchForm = Field(default_factory=SearchForm)
    results_wait: ResultsWait = Field(default_factory=ResultsWait)
    pre_search_click: Optional[str] = None   # selector to click before filling (e.g. expand collapse form)
    pre_search_click_timeout_ms: int = 8000  # how long to wait for pre_search_click element to become visible
    post_pre_search_click_wait_ms: int = 15000  # networkidle timeout after pre_search_click; raise for CF-protected sites
    post_search_click: Optional[str] = None  # selector to click after results load (e.g. grid toggle)
    # Like post_search_click but clicks EVERY visible match — used for accordion-grouped
    # results where each profession panel must be expanded to load its rows (e.g. LA_DIETETICS,
    # LA_SPEECH ColdFusion accordions).
    post_search_click_all: Optional[str] = None
    submit_via_enter: bool = False           # press Enter after fill instead of clicking submit button
    # If set, the engine navigates directly to this URL (with {q} URL-encoded and {offset}=0)
    # instead of filling the form. Used for boards with hash-route search (e.g. Thentia Cloud).
    # Skips set_search_by + fill_search_input + click_search_button.
    direct_search_url: Optional[str] = None
    # Dash-format spec for boards that require a hyphenated license number in the search field.
    # Format: "N1-N2-N3" where N1/N2/N3 are digit-group lengths (sum must equal total digits).
    # Example: "2-5-3" reformats "5383371052" (10 digits) → "53-83371-052".
    # The ladder will add a synthetic `license_formatted` attempt after license_numeric_only.
    license_dash_format: Optional[str] = None
    # When true, licenses matching ^([A-Za-z]+)(\d+)$ get a synthetic `license_formatted`
    # attempt with a dash inserted after the prefix (e.g. "L301745" → "L-301745").
    # Used for boards like IBCLC_COMMISSION that require hyphenated credential numbers.
    license_prefix_dash: bool = False
    # When true, licenses matching ^([A-Za-z]+)(\d+)$ get a synthetic attempt with a space
    # inserted between the alpha prefix and the digits (e.g. "LCPC03720" → "LCPC 03720").
    # Used for boards like KS_BSRB that require a space between the type prefix and number.
    license_alpha_space_insert: bool = False
    # List of alpha prefixes to try prepending (with a space) when the input license is
    # pure-digit. E.g. ["LCPC", "LPC"] → tries "LCPC 03192", "LPC 03192" for input "03192".
    # Used for boards like KS_BSRB where callers may omit the required license-type prefix.
    license_digit_prefixes: Optional[list[str]] = None
    # When set, zero-pads the digit portion of the license to exactly N digits.
    # For inputs already containing a letter prefix (e.g. "D63352"), the prefix is kept and
    # only the digit segment is padded: "D63352" → "D0063352" with license_digit_pad=7.
    # When combined with license_digit_prefixes for pure-digit inputs, generates
    # prefix+padded combos with no space (e.g. prefix "D" + "63352".zfill(7) → "D0063352").
    # For pure-digit inputs with no prefix list, simply zero-pads to digit_pad digits
    # (e.g. "12345".zfill(10) → "0000012345"). Used for VA_DHP (license_digit_pad=10).
    license_digit_pad: Optional[int] = None
    # Maps psv_prov_type → prefix to prepend when the input license number is a bare digit
    # string (no leading alpha characters). When the license already starts with any letter,
    # it is assumed to carry its own designation prefix and is left unchanged.
    # Used for NC_PT where the search form requires 'P' (PT) or 'A' (PTA) before the number,
    # e.g. bare "1234" → "P1234" for PT, "A1234" for PTA. Compact/Military/Temp licenses
    # (CP024411T, T1234, M1234, O1234) start with letters and are passed through unchanged.
    license_prov_type_prefix_map: Optional[dict[str, str]] = None
    # CSS selector for an <iframe> that contains the search form. When set, the engine
    # switches to the iframe's content frame for set_search_by / fill_search_input /
    # fill_extra_inputs / click_search_button, then reverts to the main page for
    # wait_for_results. Used for Clarus-embedded boards (e.g. IBCLC_COMMISSION).
    iframe_search_selector: Optional[str] = None
    # When set, scan ALL page.frames (including deeply nested ones) for the first frame
    # that contains an element matching this selector, and use it for form fill/submit.
    # Unlike iframe_search_selector (which calls content_frame() on a DOM element and
    # fails for cross-origin multi-level nesting), this probe scans the live frame list
    # directly. Use for boards where the form is buried in a nested about:blank iframe
    # that cannot be reached via CSS-based content_frame() traversal (e.g. NC_OPTOMETRY).
    search_frame_probe_selector: Optional[str] = None


class DetailTrigger(BaseModel):
    type: Literal["view_button", "row_click", "link_in_cell"] = "view_button"
    selector: str = "a:has-text('View'), button:has-text('View')"
    force_pdf: bool = False  # treat all linked hrefs as PDFs regardless of URL pattern
    # When the trigger opens an in-page modal/dialog (no navigation, URL stays the same),
    # set this so the engine skips the "wait for URL change" step (which would otherwise
    # burn the full detail timeout every row) and instead fires the row's own click
    # handler via JS — immune to overlays (e.g. a cookie-consent banner) intercepting the
    # pointer — before waiting for the modal body to populate.
    opens_modal: bool = False


class PaginationConfig(BaseModel):
    enabled: bool = False
    strategy: Literal["next_button", "page_numbers", "infinite_scroll", "none"] = "none"
    next_selector: Optional[str] = None
    disabled_class: str = "disabled"
    # Max pages to traverse. 0 => use the engine safety cap (50). Set higher for
    # boards whose result sets are large and must be searched exhaustively.
    max_pages: int = 0
    # Two-phase harvest: page through ALL result pages collecting summary rows
    # (staying on the grid — no per-row detail navigation), THEN fetch the detail
    # page only for rows matching the search target via their captured link.
    # Fixes postback-paginated grids (e.g. ASP.NET GridView) where clicking a row's
    # detail link and navigating back resets the grid to page 1, capping results at
    # one page. Requires results.type == "table" and a link_in_cell detail_trigger.
    harvest_all: bool = False


class ResultsTableConfig(BaseModel):
    row_selector: str = "table tbody tr"
    cell_selector: str = "td"
    columns: dict[int, str] = Field(default_factory=dict)
    skip_first_row: bool = False  # skip header row when table has no <thead>
    required_fields: list[str] = Field(default_factory=list)  # row dropped if any of these fields is empty
    deduplicate_by: list[str] = Field(default_factory=list)  # keep first row per unique combo of these fields
    table_selector: Optional[str] = None  # if set, select this element then use row_selector within it
    table_index: Optional[int] = None     # use .nth(table_index) of table_selector matches
    iframe_selector: Optional[str] = None # if set, look for the table inside this iframe
    # When the form button uses _doPostBack into an iframe, the engine drops into
    # this iframe BEFORE filling/submitting (so the postback target stays valid).
    fill_inside_iframe: bool = False
    # vertical_kv extractor: rows are <strong>Label:</strong>Value blocks separated by a
    # known recurring marker label (e.g. "Name"). Set to use vertical_kv extraction.
    vertical_kv: Optional["VerticalKvConfig"] = None
    # custom_js: JS expression (arrow function string) returning [{field: value, ...}].
    # When set, page.evaluate(custom_js) is called and results are returned directly,
    # bypassing all other table/vertical_kv extraction. Use for non-standard layouts.
    custom_js: Optional[str] = None
    # When set, scan ALL page.frames for the first frame containing an element matching
    # this selector and use it for table extraction. Complements search_frame_probe_selector
    # on SearchConfig — use the same selector value for both so that the same frame
    # handles both form fill and results reading.
    iframe_probe_selector: Optional[str] = None
    # column_patterns: per-column regex extractions. Maps column_index → {field_name: pattern}.
    # Each pattern is applied to the raw cell text; the first capture group is assigned to
    # field_name. Multiple fields can be extracted from the same cell (e.g. a "Licensed Dates"
    # cell that contains both "Issued: MM/DD/YYYY" and "Expires: MM/DD/YYYY").
    # If column_patterns is set for a column that also appears in `columns`, the `columns`
    # mapping still runs first (providing a raw fallback), then patterns overlay any matched fields.
    column_patterns: dict[int, dict[str, str]] = Field(default_factory=dict)


class VerticalKvConfig(BaseModel):
    """Layout where each record is rendered as a vertical list of label:value pairs
    (no <table>). Records start at each occurrence of `record_marker_label` and end
    at the next marker (or document end)."""
    container_selector: str = "body"      # CSS selector for the area to scan
    label_selector: str = "strong, b, label, .label, dt"   # selectors for label nodes
    record_marker_label: str = "Name"     # the label whose presence marks a new record
    field_map: dict[str, str] = Field(default_factory=dict)  # raw label → canonical field
    max_records: int = 200


class SelectListConfig(BaseModel):
    """Config for results that appear as a <select> listbox (e.g. Maryland Board of Physicians)."""
    selector: str = ""           # CSS selector for the <select> element
    submit_selector: str = ""    # CSS selector for the button that navigates to the detail page
    option_separator: str = "-"  # character that separates license number from name in option text
    # When navigation_strategy == "license_number_search", the engine re-runs the
    # license_number search mode for each parsed license number instead of using submit_selector.
    navigation_strategy: Literal["submit_button", "license_number_search"] = "submit_button"
    license_number_mode: str = "license_number"  # mode name to use for re-search


class ThTdMultiConfig(BaseModel):
    """Config for th_td_multi results type: one record per matching container element."""
    container_selector: str = "table"  # CSS selector — each matched element is one record


class ResultsConfig(BaseModel):
    type: Literal["table", "card_list", "single_record", "ag_grid", "select_list", "th_td_multi"] = "table"
    table: Optional[ResultsTableConfig] = None
    ag_grid_columns: list[str] = Field(default_factory=list)
    has_detail_page: bool = True
    detail_trigger: Optional[DetailTrigger] = None
    # If set, and the URL after search submission contains this substring, the portal
    # has auto-redirected to the detail page (single-result shortcut). Extract directly
    # from that page instead of hunting for trigger links (which would find wrong links).
    single_result_url_pattern: Optional[str] = None
    select_list: Optional[SelectListConfig] = None
    # Config for th_td_multi type (th=key, td=value per row, one container = one record).
    th_td_multi: Optional[ThTdMultiConfig] = None
    pagination: PaginationConfig = Field(default_factory=PaginationConfig)
    # Opt-in: for name-mode searches (last_name/first_name/…), collect summary rows
    # across ALL result pages before matching, and match on the summary table directly
    # instead of detail-clicking each row. Use for boards whose name search returns many
    # paginated rows and whose summary row already carries name + license_number (e.g.
    # AR_MEDBOARD's ASP.NET GridView). Detail-clicking each row on such boards both breaks
    # the pager (so only page 1 is ever seen) and is O(N) slow. Expiry for the matched row
    # is fetched on demand afterwards. Default off — no effect on other boards.
    paginate_summary_rows: bool = False


class DetailWait(BaseModel):
    strategy: Literal["url_change", "element_visible", "delay"] = "url_change"
    selector: Optional[str] = None
    timeout_ms: int = 15000
    fallback_selectors: list[str] = Field(default_factory=list)
    # CSS selectors that indicate an error/session-expired page rather than a real
    # detail page.  When any matches, _scrape_one_detail raises DetailPageError so
    # _scrape_with_detail_clicks falls through to the summary-row merge fallback.
    error_page_selectors: list[str] = Field(default_factory=list)


class DetailSection(BaseModel):
    name: str
    type: str = "header_mapped_table"
    field: str
    selector: Optional[str] = None          # CSS selector to locate the table directly
    columns: dict[str, str] = Field(default_factory=dict)  # header text → field name mapping


class BackNavigation(BaseModel):
    strategy: Literal["browser_back", "breadcrumb_click", "url_navigate", "escape_key"] = "browser_back"
    selector: Optional[str] = None   # for breadcrumb_click: CSS selector to click
    url_fragment: Optional[str] = None
    wait_after_ms: int = 1500


class OutOfStateTabConfig(BaseModel):
    """Config for fetching expiry from an 'Out of State' secondary tab (FL_MQA T-licenses)."""
    enabled: bool = False
    trigger_license_prefix: str = "T"
    tab_selector: str = "a:has-text('Out of State'), a:text-is('Out of State')"
    expiration_label: str = "Expiration Date"
    state_label: str = "State"
    content_wait_selector: str = "table tr td, dl dt, h2"


# ---------------------------------------------------------------------------
# DetailApiConfig — direct JSON API strategy for detail fetching
# ---------------------------------------------------------------------------

class DetailApiConfig(BaseModel):
    """Config for boards whose 'detail page' is actually a direct JSON API call.

    WHY THIS EXISTS — PA_PALS root cause:
    --------------------------------------
    PA_PALS's Angular controller (getAssetDetail) does NOT navigate the current
    tab. Instead it stores PersonId/LicenseId in localStorage and then opens
    #!/page/searchresult in a brand-new _blank tab via:
        link.target = "_blank"; link.click()
    Playwright's standard click+wait_for_url flow watches the CURRENT tab, so
    the URL never changes here → detail page is never loaded → ExpiryDate is
    never scraped.

    HOW IT WORKS:
    -------------
    Rather than following the _blank tab, we skip the click entirely and call
    the backing JSON API that the new-tab's SearchResultController would call.
    Specifically:
      1. The engine evaluates scope_selector to find an Angular scope element.
      2. It walks DOM ancestors until it finds the scope holding scope_params data.
      3. It builds the POST body dict from scope_params path expressions.
      4. It calls fetch(endpoint, POST body) inside the page context so session
         cookies are sent automatically.
      5. The JSON response is mapped via field_map to canonical LicenseRecord fields.

    GENERALISATION:
    ---------------
    Any AngularJS SPA that opens a _blank detail tab can use this strategy.
    Just configure the endpoint, scope_selector, scope_params, and field_map.
    """

    # Relative URL of the JSON API endpoint (resolved against the page origin).
    # Example: "api/Search/GetPersonOrFacilityDetails"
    endpoint: str

    # HTTP method for the API call.
    method: Literal["GET", "POST"] = "POST"

    # CSS selector for the DOM element used to start Angular scope traversal.
    # Pick an element INSIDE the results table so the walk reaches the controller
    # scope that holds the search result data.
    # Example: "#DataTables_Table_3"
    scope_selector: str = "body"

    # Maps POST body key names → dot-path expressions in the Angular scope.
    # Array indexing is supported via [N] literal or {idx} placeholder (replaced
    # at runtime with the row's zero-based index in the results table).
    # Example:
    #   PersonId:      "search.PersonDetails[{idx}].PersonId"
    #   LicenseNumber: "search.PersonDetails[{idx}].LicenseNumber"
    scope_params: dict[str, str] = Field(default_factory=dict)

    # Maps JSON response key names → canonical LicenseRecord field names.
    # Example: {"ExpiryDate": "expiration_date", "IssueDate": "issue_date"}
    field_map: dict[str, str] = Field(default_factory=dict)


class DetailConfig(BaseModel):
    wait: DetailWait = Field(default_factory=DetailWait)
    strategies: list[dict] = Field(default_factory=list)
    field_map: dict[str, str] = Field(default_factory=dict)
    sections: list[DetailSection] = Field(default_factory=list)
    back_navigation: BackNavigation = Field(default_factory=BackNavigation)
    out_of_state_tab: OutOfStateTabConfig = Field(default_factory=OutOfStateTabConfig)
    # When set, all extraction strategies are scoped to this CSS selector rather than
    # the full page. Use for inline popup/modal boards (e.g. Kendo UI Window) where the
    # search form's labels contaminate page-wide extractions.
    scope_selector: Optional[str] = None

    # When set, the engine skips the detail link click and instead calls this JSON
    # API directly (in the current tab's page context).  See DetailApiConfig above.
    # Use for boards where the detail link opens a new _blank tab that Playwright
    # cannot follow without explicit popup handling (e.g. PA_PALS).
    api: Optional[DetailApiConfig] = None


class OutputConfig(BaseModel):
    license_record: dict[str, str] = Field(default_factory=dict)
    status_map: dict[str, str] = Field(default_factory=dict)
    date_formats: list[str] = Field(default_factory=lambda: [
        "%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%B %d, %Y", "%b %d, %Y",
    ])
    # When set (e.g. "03-01"), any record missing expiration_date gets the next
    # annual occurrence of that MM-DD as its expiration. Use for boards like KY_OD
    # where all licenses expire on the same calendar date each year.
    fixed_annual_expiration_mmdd: Optional[str] = None


class RateLimitConfig(BaseModel):
    delay_between_requests_ms: int = 2000
    max_concurrent: int = 1


class RetryConfig(BaseModel):
    max_attempts: int = 3
    backoff_ms: list[int] = Field(default_factory=lambda: [1000, 2000, 4000])
    retry_on: list[str] = Field(default_factory=lambda: ["timeout", "navigation_error", "network_error"])


class ProxyConfig(BaseModel):
    enabled: Optional[bool] = None  # None=follow env var, False=force off, True=same as None


class TransportConfig(BaseModel):
    browser: str = "chromium"
    channel: Optional[str] = None  # e.g. "chrome" to use system Google Chrome
    headless: bool = True
    viewport: dict[str, int] = Field(default_factory=lambda: {"width": 1920, "height": 1080})
    timeout_ms: int = 60000
    navigation_timeout_ms: int = 30000
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    user_agent: str = "LVS-LicenseVerifier/1.0"
    ladder_timeout_s: Optional[int] = None
    ignore_https_errors: bool = False


class EvidenceConfig(BaseModel):
    capture_html: bool = True
    capture_screenshot: bool = True
    capture_on: list[str] = Field(default_factory=lambda: ["search_results", "detail_page", "error"])
    storage: Literal["local", "gcs"] = "local"
    local_path: str = "./evidence/{month}/{source_id}/{run_id}/"


class ComplianceConfig(BaseModel):
    tos_review_date: Optional[str] = None
    tos_review_ticket: Optional[str] = None
    requires_captcha: bool = False
    requires_login: bool = False
    robots_txt_compliant: bool = True


class PdfEntry(BaseModel):
    url: Optional[str] = None     # direct URL; may be None when download_strategy="page_link"
    format: str = "default"       # "prof", "estab", or custom label
    license_prefix: Optional[str] = None  # route to this PDF when license_number starts with this prefix
    link_selector: Optional[str] = None   # per-PDF anchor selector for page_link strategy; falls back to PdfBulkConfig.link_selector


class PdfBulkConfig(BaseModel):
    pdfs: list[PdfEntry] = Field(default_factory=list)
    download_strategy: Literal["direct_url", "page_link"] = "direct_url"
    # page_link: navigate to base_url (or pdf_bulk.base_url), find anchor matching link_selector,
    # download its href. With multiple PdfEntry items each having link_selector, run page_link
    # discovery once per entry — useful for boards that publish 2+ PDFs (e.g. prof + estab).
    base_url: Optional[str] = None    # override identity.base_url for page_link discovery
    link_selector: str = "a[href*='.pdf']"
    cache_days: int = 7
    cache_dir: str = "./pdfs"


class MergeSourceEntry(BaseModel):
    """One source board in a local_merge csv_bulk download."""
    source_id: str
    header_row: int = 0
    encoding: str = "utf-8-sig"
    separator: str = ","
    cache_days: int = 7   # max age in days to accept a cached CSV for this source
    # Maps canonical field name → CSV column name (license_number, last_name, first_name,
    # status, issue_date, expiration_date).  Only listed columns are kept in the merged output.
    columns: dict[str, str] = Field(default_factory=dict)


class CheckboxSectionConfig(BaseModel):
    checkbox_section: str = ""
    practitioner_types: list[str] = Field(default_factory=list)


class CsvBulkConfig(BaseModel):
    download_strategy: Literal[
        "link_text", "link_text_xlsx", "direct_url", "multi_direct_url", "post_form",
        "multi_step_checkbox", "google_sheet_link", "aithent_portal_xls",
        "nvbop_angular_xlsx", "onedrive_excel", "ohio_data_portal_csv",
        "mopro_zip", "local_merge",
    ] = "link_text"
    multi_urls: list[str] = Field(default_factory=list)  # for multi_direct_url: list of CSV URLs to download and concatenate
    link_text: Optional[str] = None        # for link_text: visible anchor text to find
    link_selector: Optional[str] = None   # for google_sheet_link: CSS/text selector for the Google Sheets link
    link_selector_nth: int = 0            # for google_sheet_link: 0-based index when multiple links match
    header_row: int = 0                   # row index of the CSV header (0-based); use 3 for Wyoming Google Sheets
    xlsx_header_row: int = 0              # row index of the XLSX header (0-based) for link_text_xlsx; the converted CSV always has header at row 0 so csv read uses header_row above
    checkbox_section: Optional[str] = None        # for multi_step_checkbox: section header text to click
    practitioner_types: list[str] = Field(default_factory=list)  # for multi_step_checkbox: types to download
    sections: list[CheckboxSectionConfig] = Field(default_factory=list)  # for multi_step_checkbox: multiple sections
    business_unit: Optional[str] = None   # for aithent_portal_xls: Business Unit dropdown text to match
    license_type_filter: Optional[str] = None  # for nvbop_angular_xlsx: license type to select before export
    # mopro_zip: Missouri MOPRO Salesforce LWC portal (mopro.mo.gov/license/s/license-downloads).
    # Selects board_label from the portal combobox → Submit → download each ZIP → extract TXT.
    board_label: Optional[str] = None
    # File format and column separator for non-CSV roster files (e.g. tab-delimited TXT from mopro_zip).
    file_format: str = "csv"   # "csv" or "txt"
    separator: str = ","       # column separator; use "\t" for mopro_zip TXT files
    download_timeout_ms: int = 120000     # max ms to wait for file download (google_sheet_link, xls/xlsx strategies)
    cache_days: int = 7
    cache_dir: str = "./csvs"
    encoding: str = "utf-8-sig"
    # Maps search mode → CSV column name to search against.
    # Scalar str = single-column search (existing behavior).
    # list[str] = multi-column AND-filter (combo modes like first_and_last).
    # e.g. {"license_number": "LicenseNum", "last_name": "Owners",
    #       "first_and_last": ["FirstName", "LastName"]}
    search_columns: dict[str, "str | list[str]"] = Field(default_factory=dict)
    # Optional CSV columns to AND-filter on when SearchQuery has license_type/provider_type set.
    license_type_column: Optional[str] = None
    provider_type_column: Optional[str] = None
    # google_sheet_link: download additional sheets (e.g. expired roster) and concatenate with primary.
    # Each selector is matched on the same base_url page.  Failures are logged and skipped.
    additional_link_selectors: list[str] = Field(default_factory=list)
    # local_merge: read + normalize already-cached CSVs from other source boards and merge them.
    # Used by combined boards like WY_ALL.  Does not download from the web.
    merge_sources: list["MergeSourceEntry"] = Field(default_factory=list)
    # OH-style combined name column: "LAST , FIRST MIDDLE" → _parsed_last/_parsed_first/_parsed_middle.
    parse_combined_name_column: Optional[str] = None


class SmokeTestExpect(BaseModel):
    license_number: Optional[str] = None       # exact match on first record
    status: Optional[str] = None               # exact match after status_map normalisation
    full_name_contains: Optional[str] = None   # case-insensitive substring on full_name
    min_records: int = 1                        # minimum records expected


class SmokeTestConfig(BaseModel):
    mode: str
    query: str = ""
    # Optional structured fields — when set, smoke runner builds a SearchQuery
    # with these instead of (or in addition to) the legacy `query` string.
    license_number: Optional[str] = None
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    last_name: Optional[str] = None
    license_type: Optional[str] = None
    provider_type: Optional[str] = None
    expect: SmokeTestExpect = Field(default_factory=SmokeTestExpect)
    skip: bool = False
    skip_reason: str = ""


class JsonApiInterceptForm(BaseModel):
    """For json_api.mode='intercept' — selectors used to drive the form so the SPA
    fires its own XHR; the engine then captures the matching response."""
    # Per-mode input fill: each entry is {selector: value-template-with-{q}}.
    fills: dict[str, dict[str, str]] = Field(default_factory=dict)
    # Per-mode click selectors run after fills (e.g. radio buttons).
    pre_clicks: dict[str, list[str]] = Field(default_factory=dict)
    # Submit button selector. If None or empty, submit_via_enter is implied.
    submit_selector: Optional[str] = None
    submit_via_enter: bool = True


class JsonApiConfig(BaseModel):
    """For archetype: json_api. Two modes:

    - mode='direct' (default): POST a JSON body to endpoint_url and parse the response.
      Works only when the endpoint is reachable without portal auth/CORS.
    - mode='intercept': drive the public form (via intercept_form) so the SPA fires
      its own XHR; the engine intercepts the matching response and parses records_path.
      Use this when corporate proxy or CORS blocks direct API calls.
    """
    mode: Literal["direct", "intercept"] = "direct"
    endpoint_url: str
    method: Literal["GET", "POST"] = "POST"
    bodies: dict[str, dict] = Field(default_factory=dict)
    params: dict[str, dict] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    records_path: str = ""
    timeout_ms: int = 30000
    intercept_form: Optional[JsonApiInterceptForm] = None
    # Substring matched against response URLs in intercept mode (default = endpoint_url).
    intercept_url_pattern: Optional[str] = None


class DataTablesConfig(BaseModel):
    """For archetype: datatables_jsapi. Drives the DataTables JS API to set the
    column-search filter (or global filter) then read rows from the rendered table."""
    # Per-mode column index for column-search; -1 means use the global filter (`search(q).draw()`).
    # Scalar int = single-column. list[int] = multi-column drive (combo modes); the engine
    # applies each column's search in order [license_number, first_name, last_name].
    column_index: dict[str, "int | list[int]"] = Field(default_factory=dict)
    table_selector: str = "table.dataTable"
    # When the site has multiple license-type pages (e.g. oklahoma.gov/dentistry),
    # iterate each URL and merge rows.
    sub_page_urls: list[str] = Field(default_factory=list)
    settle_ms: int = 1500   # additional settle after .draw() before reading rows


class FileMakerConfig(BaseModel):
    """For archetype: filemaker_webdirect (Vaadin 8). Fields are div.fm-textarea readonly
    containers — must .click() then keyboard.type() (fill() is incompatible)."""
    boot_wait_ms: int = 30000     # Vaadin needs 10-60s for initial widget render
    # Per-mode container selector ordering (1st input box = license_number, 2nd = last_name, …)
    # Scalar int = single-field. list[int] = multi-field drive (combo modes); engine fills
    # each container in order [license_number, first_name, last_name].
    field_index: dict[str, "int | list[int]"] = Field(default_factory=dict)
    container_selector: str = ".fm-textarea"
    submit_selector: str = "button.fm-widget:has-text('Search')"
    row_selector: str = "tr.v-grid-row-has-data"
    cell_value_selector: str = "td.v-grid-cell div.text"


class MultiIterationConfig(BaseModel):
    """For boards where one search-result page only covers a slice of the providers
    (e.g. AZ Speech/Hearing — must iterate over 7 provider type codes). The engine
    re-runs the form once per item, merging rows.

    Each iteration sets `field_selector` to `value` before filling/submitting.
    """
    field_selector: str               # CSS selector for the field to set
    field_kind: Literal["select", "input", "url_replace"] = "select"
    values: list[str] = Field(default_factory=list)
    # url_replace only: a {value} placeholder is substituted into base_url per iteration.
    url_template: Optional[str] = None
    stop_after_first_hit: bool = False  # when True, stop iterating once any row matches


class SiteConfig(BaseModel):
    identity: SiteIdentity
    search: SearchConfig
    results: ResultsConfig = Field(default_factory=ResultsConfig)
    detail: DetailConfig = Field(default_factory=DetailConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    transport: TransportConfig = Field(default_factory=TransportConfig)
    evidence: EvidenceConfig = Field(default_factory=EvidenceConfig)
    compliance: ComplianceConfig = Field(default_factory=ComplianceConfig)
    smoke_test: Optional[SmokeTestConfig] = None
    pdf_bulk: Optional[PdfBulkConfig] = None
    csv_bulk: Optional[CsvBulkConfig] = None
    json_api: Optional[JsonApiConfig] = None
    datatables: Optional[DataTablesConfig] = None
    filemaker: Optional[FileMakerConfig] = None
    multi_iteration: Optional[MultiIterationConfig] = None
