"""Archetype package — re-exports verify_license for backward compatibility.

Importers that do `from archetypes import verify_license` or
`from run import verify_license` both work without changes.
"""
from .dispatcher import verify_license

__all__ = ["verify_license"]
