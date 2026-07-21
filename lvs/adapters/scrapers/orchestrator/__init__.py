"""PSV orchestration layer.

Sits above the engine + archetypes. Drives per-row rule-based ladder + NPPES
retry + AI agent fallback, emits multi-channel output under:
    PSV_DEV/Output/{YYYYMM}/{run_id}/{Channel}/{ChannelName}_{YYYYMMDD_HHMM}.ext
"""
