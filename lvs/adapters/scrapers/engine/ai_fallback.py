"""Azure OpenAI GPT-4 fallback — invoked when rule-based extraction yields < 3 fields."""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

_MIN_FIELDS_THRESHOLD = 3
_MAX_HTML_CHARS = 20000

_AZURE_API_KEY = os.environ.get("AZURE_OPENAI_API_KEY", "")
_AZURE_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
_OPENAI_CONFIGURED = bool(_AZURE_API_KEY) and bool(_AZURE_ENDPOINT)

try:
    from openai import AzureOpenAI as _AzureOpenAI
    _client = _AzureOpenAI(
        api_key=_AZURE_API_KEY,
        azure_endpoint=_AZURE_ENDPOINT,
        api_version="2024-02-01",
    ) if _OPENAI_CONFIGURED else None
    _OPENAI_AVAILABLE = _OPENAI_CONFIGURED
except Exception:
    _OPENAI_AVAILABLE = False
    log.warning("Azure OpenAI not available — AI fallback disabled")


def _build_prompt(html: str, field_map: dict[str, str]) -> str:
    canonical_fields = list(set(field_map.values()))
    fields_str = ", ".join(canonical_fields)
    return (
        f"You are a license record extractor. Extract the following fields from the HTML below:\n"
        f"Fields: {fields_str}\n\n"
        f"Return a JSON object with exactly these keys (use null for missing values).\n\n"
        f"HTML:\n{html[:_MAX_HTML_CHARS]}"
    )


async def extract_with_ai(
    html: str,
    field_map: dict[str, str],
    source_id: str,
    run_id: str,
    db: Any | None = None,
) -> dict:
    """Call GPT-4 to extract license fields. Returns extracted dict with _used_ai=True."""
    if not _OPENAI_AVAILABLE or _client is None:
        log.debug("[%s] AI fallback skipped — Azure OpenAI not configured", source_id)
        return {"_used_ai": False}

    prompt = _build_prompt(html, field_map)

    try:
        response = _client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Extract structured license data from HTML. Return JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=1000,
        )
        content = response.choices[0].message.content or "{}"
        usage = response.usage

        # Log AI touchpoint to DB
        if db is not None:
            from .telemetry import log_ai_touchpoint
            await log_ai_touchpoint(
                db=db,
                run_id=run_id,
                source_id=source_id,
                stage="detail_extraction",
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                model="gpt-4",
            )

        # Parse JSON response
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            extracted = json.loads(content[start:end])
        else:
            extracted = {}

        log.info("[%s] AI fallback extracted %d fields", source_id, len(extracted))
        extracted["_used_ai"] = True
        return extracted

    except Exception as e:
        log.error("[%s] AI fallback failed: %s", source_id, e)
        return {"_used_ai": False}


def should_use_ai_fallback(raw: dict) -> bool:
    """Return True if rule-based extraction produced too few meaningful fields."""
    meaningful = {
        k: v for k, v in raw.items()
        if k and not k.startswith("_") and v and str(v).strip()
    }
    return len(meaningful) < _MIN_FIELDS_THRESHOLD
