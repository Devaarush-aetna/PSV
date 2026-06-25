"""Per-board capability lookup — wraps engine/navigator's auto-derivation.

Used by the ladder to ask each routed board "what search modes do you support?"
BEFORE any browser launches. Filters the canonical ladder down to (a) modes the
board can handle and (b) modes whose required fields are populated for this row.
"""
from __future__ import annotations

from typing import Iterable

from engine.models import COMBO_MODES, SiteConfig

# Canonical search-mode order. The ladder walks this list and keeps only modes
# the board supports AND for which the master row has non-empty inputs.
# `license_numeric_only` is synthetic — same as `license_number` but with
# non-digit chars stripped from the input. Built into ladder.py, not here.
CANONICAL_LADDER: tuple[str, ...] = (
    "license_number",
    "license_numeric_only",
    "first_and_last_typed",   # first+last WITH profession/board dropdown pre-set
    "first_and_last",
    "last_name",
    "first_name",
)


def supported_modes(config: SiteConfig) -> set[str]:
    """Return the set of search modes this board supports.

    Honors `identity.capabilities` override if explicitly set on the config;
    otherwise delegates to engine.navigator._auto_derive_capabilities.
    """
    if config.identity.capabilities is not None:
        return set(config.identity.capabilities)
    from engine.navigator import _auto_derive_capabilities  # noqa: PLC0415
    return _auto_derive_capabilities(config)


def required_fields_for(mode: str) -> tuple[str, ...]:
    """Which master_row keys must be non-empty for this mode to be runnable?

    Returns a tuple of master_row keys (e.g. ("license_id",), ("first_name",
    "last_name")). The ladder skips a mode if any required field is empty.
    """
    mapping = {
        "license_number": ("license_id",),
        "license_numeric_only": ("license_id",),
        "first_and_last_typed": ("first_name", "last_name"),
        "first_and_last": ("first_name", "last_name"),
        "last_name": ("last_name",),
        "first_name": ("first_name",),
    }
    return mapping.get(mode, ())


def applicable_modes(config: SiteConfig, master_row: dict) -> list[str]:
    """Return the ladder filtered to (a) modes the board supports AND
    (b) modes whose required fields are populated for this row.

    `license_numeric_only` is always paired with `license_number` capability.
    """
    caps = supported_modes(config)
    out: list[str] = []
    for mode in CANONICAL_LADDER:
        # Capability check
        if mode == "license_numeric_only":
            if "license_number" not in caps:
                continue
        elif mode == "first_and_last_typed":
            # Requires: first_and_last board support + provider_type_selector set +
            # this row's prov_type has an entry in prov_type_values.
            if "first_and_last" not in caps:
                continue
            ident = config.identity
            if not ident.provider_type_selector or not ident.prov_type_values:
                continue
            if not master_row.get("prov_type") or master_row.get("prov_type") not in ident.prov_type_values:
                continue
        elif mode not in caps:
            continue
        # Required-fields check
        if not all(master_row.get(f) for f in required_fields_for(mode)):
            continue
        out.append(mode)
    return out


def is_combo_mode(mode: str) -> bool:
    return mode in COMBO_MODES or mode == "license_numeric_only"


def license_modes() -> Iterable[str]:
    """Iterate over modes that are 'license-based' for the dual-profile
    disambiguator (used to decide whether name_only profile applies)."""
    return ("license_number", "license_numeric_only")
