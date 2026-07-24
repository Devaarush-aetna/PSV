"""JSON API archetype (direct POST/GET + intercept mode)."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from engine.models import SearchQuery, SiteConfig
from engine.output import map_to_license_record, upsert_to_db
from engine.post_processors import apply_field_map
from engine.proxy import get_proxy_config
from ._shared import _emit_event

log = logging.getLogger(__name__)


def _sub_str(s: str, sq: SearchQuery) -> str:
    if sq.first_name is not None or sq.last_name is not None:
        first = sq.first_name or ""
        last = sq.last_name or ""
    else:
        parts = sq.query.rsplit(" ", 1)
        first = parts[0] if len(parts) > 1 else ""
        last = parts[-1]
    license_val = sq.license_number if sq.license_number is not None else sq.query
    return (
        s.replace("{q}", sq.query)
        .replace("{first}", first)
        .replace("{last}", last)
        .replace("{license}", license_val or "")
        .replace("{type}", sq.license_type or "")
        .replace("{provider}", sq.provider_type or "")
    )


def _sub(obj, sq: SearchQuery):
    if isinstance(obj, dict):
        return {k: _sub(v, sq) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sub(v, sq) for v in obj]
    if isinstance(obj, str):
        return _sub_str(obj, sq)
    return obj


async def scrape_json_api(
    config: SiteConfig, query: SearchQuery, db, t0: float, run_id: str,
) -> list:
    """POST/GET a JSON request and parse a list of provider records."""
    from playwright.async_api import async_playwright

    source_id = config.identity.source_id
    api_cfg = config.json_api
    if not api_cfg:
        log.error("[%s] json_api archetype requires json_api section", source_id)
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, "no_json_api_config")
        return []

    log.info("[%s] JSON API run_id=%s  query=%s/%s", source_id, run_id, query.mode, query.query)

    if config.transport.proxy.enabled is False:
        proxy_cfg = None
        extra_launch_args = ["--no-proxy-server"]
    else:
        proxy_cfg = get_proxy_config()
        extra_launch_args = []

    base_origin = config.identity.base_url.rstrip("/")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": base_origin,
        "Referer": config.identity.base_url,
        "User-Agent": config.transport.user_agent,
    }
    headers.update(api_cfg.headers)

    body_template = api_cfg.bodies.get(query.mode) or {}
    param_template = api_cfg.params.get(query.mode) or {}

    payload = None
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, args=extra_launch_args)
            try:
                ctx = await browser.new_context(
                    proxy=proxy_cfg,
                    user_agent=config.transport.user_agent,
                    ignore_https_errors=True,
                )
                page = await ctx.new_page()

                if api_cfg.mode == "intercept":
                    pattern = api_cfg.intercept_url_pattern or api_cfg.endpoint_url
                    endpoint_path = api_cfg.endpoint_url.split("?", 1)[0].rstrip("/")
                    captured = []

                    def _on_resp(resp):
                        url = resp.url.split("?", 1)[0].rstrip("/")
                        if pattern in resp.url and (url == endpoint_path or url.endswith(endpoint_path)):
                            captured.append(resp)
                            log.info("[%s] captured response: %s", source_id, resp.url)

                    page.on("response", _on_resp)
                    await page.goto(config.identity.base_url, wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(3)

                    form = api_cfg.intercept_form
                    if form:
                        for sel in form.pre_clicks.get(query.mode, []):
                            try:
                                await page.locator(sel).first.click()
                                await asyncio.sleep(0.4)
                            except Exception as e:
                                log.warning("[%s] pre_click '%s' failed: %s", source_id, sel, e)
                        for sel, val_tpl in form.fills.get(query.mode, {}).items():
                            v = _sub_str(val_tpl, query)
                            try:
                                loc = page.locator(sel).first
                                await loc.clear()
                                await loc.fill(v)
                            except Exception as e:
                                log.warning("[%s] fill '%s' failed: %s", source_id, sel, e)
                        if form.submit_selector:
                            try:
                                await page.locator(form.submit_selector).first.click()
                            except Exception as e:
                                log.warning("[%s] submit '%s' failed: %s", source_id, form.submit_selector, e)
                        if form.submit_via_enter:
                            await page.keyboard.press("Enter")

                    deadline = time.time() + (api_cfg.timeout_ms / 1000.0)
                    while time.time() < deadline and not captured:
                        await asyncio.sleep(0.4)

                    if captured:
                        for resp in captured[::-1]:
                            try:
                                payload = await resp.json()
                                break
                            except Exception:
                                continue

                    if payload is None:
                        raise RuntimeError(f"no JSON response captured from pattern '{pattern}'")
                else:
                    # direct mode
                    try:
                        await page.goto(config.identity.base_url, wait_until="domcontentloaded", timeout=20000)
                        await asyncio.sleep(1.5)
                    except Exception:
                        pass
                    if api_cfg.method == "POST":
                        body = _sub(body_template, query)
                        log.info("[%s] POST %s body=%s", source_id, api_cfg.endpoint_url, body)
                        payload = await page.evaluate(
                            """async ({url, body, headers}) => {
                                const r = await fetch(url, {
                                    method: 'POST', headers, body: JSON.stringify(body),
                                    credentials: 'include',
                                });
                                if (!r.ok) throw new Error('HTTP ' + r.status);
                                return await r.json();
                            }""",
                            {"url": api_cfg.endpoint_url, "body": body, "headers": headers},
                        )
                    else:
                        params = _sub(param_template, query)
                        log.info("[%s] GET %s params=%s", source_id, api_cfg.endpoint_url, params)
                        payload = await page.evaluate(
                            """async ({url, params, headers}) => {
                                const u = new URL(url);
                                for (const [k, v] of Object.entries(params || {})) u.searchParams.set(k, v);
                                const r = await fetch(u.toString(), {method: 'GET', headers, credentials: 'include'});
                                if (!r.ok) throw new Error('HTTP ' + r.status);
                                return await r.json();
                            }""",
                            {"url": api_cfg.endpoint_url, "params": params, "headers": headers},
                        )
            finally:
                await browser.close()
    except Exception as exc:
        log.error("[%s] JSON API fetch failed: %s", source_id, exc)
        await _emit_event(db, run_id, source_id, "scrape", "error", t0, 0, str(exc))
        return []

    cursor = payload
    if api_cfg.records_path:
        for key in api_cfg.records_path.split("."):
            if isinstance(cursor, dict):
                cursor = cursor.get(key)
            else:
                cursor = None
                break
    raw_records = cursor if isinstance(cursor, list) else []

    log.info("[%s] JSON API returned %d record(s)", source_id, len(raw_records))
    records = []
    for raw in raw_records:
        if not isinstance(raw, dict):
            continue
        raw["_source_url"] = api_cfg.endpoint_url
        mapped = apply_field_map(raw, config.detail.field_map)
        rec = map_to_license_record(mapped, config, {})
        records.append(rec)

    await _emit_event(db, run_id, source_id, "complete", "success", t0, len(records))
    if db and records:
        await upsert_to_db(db, records)
    return records
