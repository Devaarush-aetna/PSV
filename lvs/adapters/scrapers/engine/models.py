"""Pydantic v2 models — canonical contract for all boards."""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


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


# ---------------------------------------------------------------------------
# Search query
# ---------------------------------------------------------------------------

class SearchQuery(BaseModel):
    mode: str
    query: str


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


# ---------------------------------------------------------------------------
# Site config — nested models
# ---------------------------------------------------------------------------

class SiteIdentity(BaseModel):
    source_id: str
    board_name: str
    state: str
    country: str = "US"
    profession_codes: list[str] = Field(default_factory=list)
    base_url: str
    archetype: Literal["thentia_cloud", "ag_grid_spa", "classic_html_form", "state_portal", "socrata_api", "socrata_bulk_csv"]


class SearchMode(BaseModel):
    mode: str
    dropdown_value: Optional[str] = None
    input_selector: Optional[str] = None   # per-mode override for the search text input
    button_selector: Optional[str] = None  # per-mode override for the search submit button


class ElementSelector(BaseModel):
    selector: str
    fallback_selectors: list[str] = Field(default_factory=list)


class SearchByDropdown(BaseModel):
    strategy: Literal["select", "custom_dropdown", "radio", "none"] = "none"
    selector: Optional[str] = None


class ResultsWait(BaseModel):
    strategy: Literal["element_visible", "url_change", "network_idle", "delay"] = "element_visible"
    selector: Optional[str] = None
    timeout_ms: int = 20000
    no_results_indicators: list[str] = Field(default_factory=list)


class SearchForm(BaseModel):
    search_by_dropdown: SearchByDropdown = Field(default_factory=SearchByDropdown)
    search_input: ElementSelector = Field(default_factory=lambda: ElementSelector(selector="input[type='text']"))
    search_button: ElementSelector = Field(default_factory=lambda: ElementSelector(selector="button[type='submit']"))


class SearchConfig(BaseModel):
    modes: list[SearchMode]
    form: SearchForm = Field(default_factory=SearchForm)
    results_wait: ResultsWait = Field(default_factory=ResultsWait)


class DetailTrigger(BaseModel):
    type: Literal["view_button", "row_click", "link_in_cell"] = "view_button"
    selector: str = "a:has-text('View'), button:has-text('View')"


class PaginationConfig(BaseModel):
    enabled: bool = False
    strategy: Literal["next_button", "page_numbers", "infinite_scroll", "none"] = "none"
    next_selector: Optional[str] = None
    disabled_class: str = "disabled"


class ResultsTableConfig(BaseModel):
    row_selector: str = "table tbody tr"
    cell_selector: str = "td"
    columns: dict[int, str] = Field(default_factory=dict)


class SelectListConfig(BaseModel):
    """Config for results that appear as a <select> listbox (e.g. Maryland Board of Physicians)."""
    selector: str = ""           # CSS selector for the <select> element
    submit_selector: str = ""    # CSS selector for the button that navigates to the detail page
    option_separator: str = "-"  # character that separates license number from name in option text
    # When navigation_strategy == "license_number_search", the engine re-runs the
    # license_number search mode for each parsed license number instead of using submit_selector.
    navigation_strategy: Literal["submit_button", "license_number_search"] = "submit_button"
    license_number_mode: str = "license_number"  # mode name to use for re-search


class ResultsConfig(BaseModel):
    type: Literal["table", "card_list", "single_record", "ag_grid", "select_list"] = "table"
    table: Optional[ResultsTableConfig] = None
    ag_grid_columns: list[str] = Field(default_factory=list)
    has_detail_page: bool = True
    detail_trigger: Optional[DetailTrigger] = None
    select_list: Optional[SelectListConfig] = None
    pagination: PaginationConfig = Field(default_factory=PaginationConfig)


class DetailWait(BaseModel):
    strategy: Literal["url_change", "element_visible", "delay"] = "url_change"
    selector: Optional[str] = None
    timeout_ms: int = 15000
    fallback_selectors: list[str] = Field(default_factory=list)


class DetailSection(BaseModel):
    name: str
    type: str = "header_mapped_table"
    field: str
    selector: Optional[str] = None          # CSS selector to locate the table directly
    columns: dict[str, str] = Field(default_factory=dict)  # header text → field name mapping


class BackNavigation(BaseModel):
    strategy: Literal["browser_back", "breadcrumb_click", "url_navigate"] = "browser_back"
    selector: Optional[str] = None   # for breadcrumb_click: CSS selector to click
    url_fragment: Optional[str] = None
    wait_after_ms: int = 1500


class DetailConfig(BaseModel):
    wait: DetailWait = Field(default_factory=DetailWait)
    strategies: list[dict] = Field(default_factory=list)
    field_map: dict[str, str] = Field(default_factory=dict)
    sections: list[DetailSection] = Field(default_factory=list)
    back_navigation: BackNavigation = Field(default_factory=BackNavigation)


class OutputConfig(BaseModel):
    license_record: dict[str, str] = Field(default_factory=dict)
    status_map: dict[str, str] = Field(default_factory=dict)
    date_formats: list[str] = Field(default_factory=lambda: [
        "%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%B %d, %Y", "%b %d, %Y",
    ])


class RateLimitConfig(BaseModel):
    delay_between_requests_ms: int = 2000
    max_concurrent: int = 1


class RetryConfig(BaseModel):
    max_attempts: int = 3
    backoff_ms: list[int] = Field(default_factory=lambda: [1000, 2000, 4000])
    retry_on: list[str] = Field(default_factory=lambda: ["timeout", "navigation_error", "network_error"])


class ProxyConfig(BaseModel):
    enabled: bool = False


class TransportConfig(BaseModel):
    browser: str = "chromium"
    headless: bool = True
    viewport: dict[str, int] = Field(default_factory=lambda: {"width": 1920, "height": 1080})
    timeout_ms: int = 60000
    navigation_timeout_ms: int = 30000
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    user_agent: str = "LVS-LicenseVerifier/1.0"


class EvidenceConfig(BaseModel):
    capture_html: bool = True
    capture_screenshot: bool = True
    capture_on: list[str] = Field(default_factory=lambda: ["search_results", "detail_page", "error"])
    storage: Literal["local", "gcs"] = "local"
    local_path: str = "./evidence/{source_id}/{run_id}/"


class ComplianceConfig(BaseModel):
    tos_review_date: Optional[str] = None
    tos_review_ticket: Optional[str] = None
    requires_captcha: bool = False
    requires_login: bool = False
    robots_txt_compliant: bool = True


class SmokeTestExpect(BaseModel):
    license_number: Optional[str] = None       # exact match on first record
    status: Optional[str] = None               # exact match after status_map normalisation
    full_name_contains: Optional[str] = None   # case-insensitive substring on full_name
    min_records: int = 1                        # minimum records expected


class SmokeTestConfig(BaseModel):
    mode: str
    query: str
    expect: SmokeTestExpect = Field(default_factory=SmokeTestExpect)
    skip: bool = False
    skip_reason: str = ""


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
