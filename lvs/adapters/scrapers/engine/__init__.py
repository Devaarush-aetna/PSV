"""Shared scraper engine — Playwright-based, config-driven, archetype-aware."""
from .models import LicenseRecord, LicenseStatus, SearchQuery, SiteConfig, TelemetryEvent
from .validate import load_config

__all__ = [
    "LicenseRecord",
    "LicenseStatus",
    "SearchQuery",
    "SiteConfig",
    "TelemetryEvent",
    "load_config",
]
