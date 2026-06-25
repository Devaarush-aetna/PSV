"""PSV orchestration layer.

Sits above the engine + archetypes. Drives per-row rule-based ladder + NPPES
retry + AI agent fallback, emits 4-channel output (standard, nppes, ai_fallback,
manual) under PSV_DEV/Output/{channel}/{YYYY-MM}/{run_id}.csv.
"""
