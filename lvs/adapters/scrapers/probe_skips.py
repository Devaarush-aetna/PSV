"""
One-off probe to inspect SKIP'd board landing pages and identify the actual
selectors. Uses the engine's proxy + browser modules.

Usage:
    PROXY=proxy:9119 python probe_skips.py BOARD_NAME [BOARD_NAME ...]
"""
import asyncio
import sys
from pathlib import Path

from engine.browser import get_page
from engine.models import TransportConfig
from engine.proxy import get_proxy_config

# Board: (URL, things to inspect — a list of (label, JS expression returning string))
PROBES: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "OK_MEDBOARD": (
        "https://www.okmedicalboard.org/search",
        [
            ("forms", "JSON.stringify([...document.forms].map(f => ({action: f.action, method: f.method, inputs: [...f.elements].slice(0,30).map(e=>({tag:e.tagName, type:e.type, name:e.name, id:e.id, value: e.value && e.value.length<60 ? e.value : ''}))})))"),
            ("submits", "JSON.stringify([...document.querySelectorAll('input[type=submit],button[type=submit],button')].slice(0,20).map(b=>({tag:b.tagName, type:b.type||'', name:b.name||'', id:b.id||'', value:b.value||'', text:(b.textContent||'').trim().slice(0,40)})))"),
            ("results_table", "JSON.stringify([...document.querySelectorAll('table')].slice(0,10).map(t=>({id:t.id, cls:t.className, rows:t.rows.length, header_cells: [...t.querySelectorAll('th, thead td')].slice(0,10).map(h=>(h.textContent||'').trim().slice(0,30))})))"),
        ],
    ),
    "NC_CHIRO": (
        "https://ncchiroboard.com/license-verification-request/",
        [
            ("forms", "JSON.stringify([...document.forms].map(f => ({action:f.action, inputs:[...f.elements].slice(0,30).map(e=>({type:e.type||'', name:e.name||'', id:e.id||''}))})))"),
            ("tables", "JSON.stringify([...document.querySelectorAll('table')].slice(0,8).map(t=>({id:t.id, cls:(t.className||'').slice(0,80), rows:t.rows.length})))"),
            ("wpdt", "JSON.stringify([...document.querySelectorAll('[id*=wpdt],.wpDataTablesWrapper,.wpdt-c,[class*=wpDataTable]')].slice(0,5).map(t=>({tag:t.tagName, id:t.id, cls:(t.className||'').slice(0,60)})))"),
        ],
    ),
    "NC_PODIATRY_FORM": (
        "https://www.ncbpe.org/NCBPE/Content/Search_Podiatrist.aspx",
        [
            ("forms", "JSON.stringify([...document.forms].map(f => ({action:f.action, inputs:[...f.elements].slice(0,30).map(e=>({type:e.type||'', name:e.name||'', id:e.id||''}))})))"),
            ("h", "JSON.stringify([...document.querySelectorAll('h1,h2,h3')].slice(0,20).map(h=>({tag:h.tagName, text:(h.textContent||'').trim().slice(0,80)})))"),
        ],
    ),
    "NC_PODIATRY": (
        "https://www.ncbpe.org/",
        [
            ("links", "JSON.stringify([...document.querySelectorAll('a')].slice(0,80).map(a => ({href:a.href, text:(a.textContent||'').trim().slice(0,50)})).filter(l => /verif|search|lookup|find|directory|roster|listing/i.test(l.text+l.href)))"),
            ("title_text", "document.title + ' || ' + (document.body.innerText||'').slice(0,300)"),
        ],
    ),
    "OK_OPTOMETRY": (
        "https://obeo.thentiacloud.net/webs/obeo/register/",
        [
            ("inputs", "JSON.stringify([...document.querySelectorAll('input,select')].map(e=>({tag:e.tagName, type:e.type||'', name:e.name||'', id:e.id||'', placeholder:e.placeholder||'', cls:(e.className||'').slice(0,60)})))"),
            ("title_text", "document.title + ' || ' + (document.body.innerText||'').slice(0,200)"),
        ],
    ),
    "WV_PT": (
        "https://wvbopt.thentiacloud.net/webs/wvbopt/register/",
        [
            ("inputs", "JSON.stringify([...document.querySelectorAll('input,select')].map(e=>({tag:e.tagName, type:e.type||'', name:e.name||'', id:e.id||'', placeholder:e.placeholder||'', cls:(e.className||'').slice(0,60)})))"),
            ("title_text", "document.title + ' || ' + (document.body.innerText||'').slice(0,200)"),
        ],
    ),
    "OK_OSTEO": (
        "https://osboe.thentiacloud.net/webs/osboe/register/",
        [
            ("inputs", "JSON.stringify([...document.querySelectorAll('input,select')].map(e=>({tag:e.tagName, type:e.type||'', name:e.name||'', id:e.id||'', placeholder:e.placeholder||'', cls:(e.className||'').slice(0,60)})))"),
            ("title_text", "document.title + ' || ' + (document.body.innerText||'').slice(0,200)"),
        ],
    ),
    "AR_MEDBOARD": (
        "https://www.armedicalboard.org/public/verify/lookup.aspx",
        [
            ("forms", "JSON.stringify([...document.forms].map(f => ({action: f.action, inputs: [...f.elements].slice(0,30).map(e=>({type:e.type||'', name:e.name||'', id:e.id||'', value:(e.value||'').slice(0,50)}))})))"),
            ("title", "document.title"),
        ],
    ),
    "ID_DOPL": (
        "https://www.dopl.idaho.gov/license-search/",
        [
            ("inputs", "JSON.stringify([...document.querySelectorAll('input,select')].slice(0,20).map(e=>({tag:e.tagName, type:e.type||'', name:e.name||'', id:e.id||'', placeholder:e.placeholder||''})))"),
            ("title_text", "document.title + ' || ' + (document.body.innerText||'').slice(0,500)"),
        ],
    ),
    "OR_NATUROPATH": (
        "https://obnm.us.thentiacloud.net/webs/obnm/register/",
        [
            ("inputs", "JSON.stringify([...document.querySelectorAll('input,select')].map(e=>({tag:e.tagName, type:e.type||'', name:e.name||'', id:e.id||'', placeholder:e.placeholder||'', cls:(e.className||'').slice(0,60)})))"),
            ("dropdown_panels", "JSON.stringify([...document.querySelectorAll('select, [class*=dropdown], [class*=select-], ng-select, mat-select')].slice(0,30).map(d=>({tag:d.tagName, cls:(d.className||'').slice(0,60), id:d.id||''})))"),
            ("labels", "JSON.stringify([...document.querySelectorAll('label')].slice(0,20).map(l => ({for:l.getAttribute('for')||'', text:(l.textContent||'').trim().slice(0,50)})))"),
            ("any_search_by_text", "Array.from(document.querySelectorAll('*')).filter(el => (el.children.length===0) && /search by/i.test(el.textContent||'')).slice(0,10).map(el => ({tag:el.tagName, text:(el.textContent||'').trim().slice(0,80), cls:(el.className||'').slice(0,40), parent: el.parentElement && el.parentElement.tagName, parentCls: el.parentElement && (el.parentElement.className||'').slice(0,60)}))"),
        ],
    ),
    "OK_DENTAL": (
        "https://oklahoma.gov/dentistry/online-services/license-look-up.html",
        [
            ("forms", "JSON.stringify([...document.forms].map(f => ({action: f.action})))"),
            ("links", "JSON.stringify([...document.querySelectorAll('a')].slice(0,40).map(a => ({href:a.href, text:(a.textContent||'').trim().slice(0,50)})))"),
            ("tables", "JSON.stringify([...document.querySelectorAll('table')].slice(0,10).map(t=>({id:t.id, cls:(t.className||'').slice(0,60)})))"),
        ],
    ),
    "WV_SOCIALWORK": (
        "https://wvsocialworkboard.org/",
        [
            ("links", "JSON.stringify([...document.querySelectorAll('a')].slice(0,80).map(a => ({href:a.href, text:(a.textContent||'').trim().slice(0,50)})).filter(l => /verif|licens|search|lookup|directory|roster/i.test(l.text+l.href)))"),
        ],
    ),
    "LA_MASSAGETHERAPY": (
        "https://www.lmtb.la.gov/",
        [
            ("pdf_links", "JSON.stringify([...document.querySelectorAll('a[href*=\".pdf\"]')].slice(0,40).map(a => ({href:a.href, text:(a.textContent||'').trim().slice(0,80)})))"),
            ("all_links", "JSON.stringify([...document.querySelectorAll('a')].slice(0,40).map(a => ({href:a.href, text:(a.textContent||'').trim().slice(0,50)})).filter(l => /verif|roster|active|list/i.test(l.text)))"),
        ],
    ),
    "WV_CHIRO": (
        "https://boc.wv.gov/roster.html",
        [
            ("any_files", "JSON.stringify([...document.querySelectorAll('a')].filter(a=>a.href).map(a=>({href:a.href, text:(a.textContent||'').trim().slice(0,60)})).filter(l=>/(\\.pdf$|\\.xls|\\.csv|roster|active|licens)/i.test(l.href+l.text)))"),
            ("title_text", "document.title + ' || ' + (document.body.innerText||'').slice(0,1000)"),
        ],
    ),
    "ME_OPLR": (
        "https://www.pfr.maine.gov/ALMSOnline/ALMSQuery/SearchIndividual.aspx",
        [
            ("selects", "JSON.stringify([...document.querySelectorAll('select')].map(s=>({id:s.id, name:s.name, count:s.options.length, options:[...s.options].slice(0,5).map(o=>o.text)})))"),
            ("forms", "JSON.stringify([...document.forms].map(f => ({action: f.action, count: f.elements.length})))"),
            ("inputs", "JSON.stringify([...document.querySelectorAll('input')].slice(0,30).map(e=>({type:e.type||'', name:e.name||'', id:e.id||'', value:(e.value||'').slice(0,30)})))"),
        ],
    ),
}


async def probe(board: str, url: str, probes_list: list[tuple[str, str]]) -> dict:
    transport = TransportConfig(
        browser="chromium",
        headless=True,
        viewport={"width": 1280, "height": 900},
        timeout_ms=45000,
        navigation_timeout_ms=30000,
        rate_limit={"delay_between_requests_ms": 2000, "max_concurrent": 1},
        retry={"max_attempts": 1, "backoff_ms": [1000], "retry_on": ["timeout"]},
        proxy={"enabled": True},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    )
    out = {"board": board, "url": url, "probes": {}, "error": None}
    async with get_page(transport) as page:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as e:
            out["error"] = f"goto failed: {e}"
            return out
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        await asyncio.sleep(2.5)
        for label, expr in probes_list:
            try:
                result = await page.evaluate(expr)
                out["probes"][label] = result
            except Exception as e:
                out["probes"][label] = f"ERROR: {e}"
        try:
            title = await page.title()
            out["title"] = title
        except Exception:
            pass
    return out


async def main(boards: list[str]) -> int:
    results = await asyncio.gather(*[
        probe(b, *PROBES[b]) for b in boards if b in PROBES
    ], return_exceptions=True)
    for r in results:
        if isinstance(r, BaseException):
            print(f"[ERROR] {r}")
            continue
        print("=" * 80)
        print(f"BOARD: {r['board']}")
        print(f"URL: {r['url']}")
        print(f"TITLE: {r.get('title','?')}")
        if r.get("error"):
            print(f"ERROR: {r['error']}")
        for label, val in r.get("probes", {}).items():
            v = str(val)
            if len(v) > 3000:
                v = v[:3000] + "...(truncated)"
            print(f"--- {label} ---")
            print(v)
    return 0


if __name__ == "__main__":
    boards = sys.argv[1:] if len(sys.argv) > 1 else list(PROBES.keys())
    asyncio.run(main(boards))
