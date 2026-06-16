"""
Session 29: probe all 10 SKIP boards live to confirm/disprove skip_reason.
Run with: PROXY=proxy:9119 python probe_session29.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from engine.browser import get_page
from engine.models import TransportConfig


PROBES = {
    "LA_MEDBOARD": [
        "https://online.lasbme.org/#/verifylicense",
        "https://www.lsbme.la.gov/verification",
        "https://www.lsbme.la.gov/content/license-verification",
    ],
    "MN_EMS": [
        "https://emslm.mn.gov/lms/public/portal#/lookup/user",
        "https://emslm.mn.gov/lms/public/portal#/lookup",
        "https://emslm.mn.gov/lms/public/portal",
        "https://mn.gov/emsrb/licensing/",
    ],
    "MN_MEDPRACTICE": [
        "https://bmp.hlb.state.mn.us/#/onlineEntitySearch",
        "https://bmp.hlb.state.mn.us/onlineEntitySearch",
        "https://mn.gov/boards/medical-practice/public/licenselookup/",
        "https://bmp.hlb.state.mn.us/",
    ],
    "OR_HLO": [
        "https://elite.hlo.state.or.us/OHLOPublicR/LPRBrowser.aspx",
        "https://www.oregon.gov/oha/PH/HLO/Pages/Licensee-Search.aspx",
        "https://oregon.gov/oha/PH/HLO/Pages/index.aspx",
    ],
    "OR_OPTOMETRY": [
        "https://orus-obo.ongovcore.com/public/verify-professional-license",
        "https://orus-obo.ongovcore.com/Public/SearchPublicLicense.aspx",
        "https://orus-obo.ongovcore.com/",
        "https://www.oregon.gov/obo/Pages/license-verification.aspx",
    ],
    "NC_PT": [
        "https://www2.ncptboard.org/app/OnlineServices/VerifyTherapist/VerifyTherapist.php",
        "https://www.ncptboard.org/online-services/verify-therapist/",
        "https://www.ncptboard.org/",
    ],
    "KS_PHARMACY": [
        "https://ksbop.elicensesoftware.com/portal.aspx",
        "https://ksbop.elicensesoftware.com/Pages/Search/Lookup.aspx",
        "https://pharmacy.ks.gov/licensing/license-verification",
        "https://pharmacy.ks.gov/",
    ],
    "NC_OPTOMETRY": [
        "https://www.ncoptometry.org/verify-a-license",
        "https://www.ncoptometry.org/",
    ],
    "NC_CHIRO": [
        "https://ncchiroboard.com/non-certified-license-verification-request/",
        "https://ncchiroboard.com/",
    ],
    "WV_CHIRO": [
        "https://boc.wv.gov/roster.html",
        "https://boc.wv.gov/",
    ],
}


PROBE_JS = """
JSON.stringify({
    title: document.title,
    url: location.href,
    body_top: (document.body && document.body.innerText || '').slice(0, 500),
    forms_count: document.forms.length,
    inputs: [...document.querySelectorAll('input,select,textarea')].slice(0,15).map(e=>({
        tag: e.tagName, type: e.type||'', name: e.name||'', id: e.id||'',
        placeholder: e.placeholder||''
    })),
    iframes: [...document.querySelectorAll('iframe')].slice(0,10).map(f=>({
        src: f.src, name: f.name||'', id: f.id||''
    })),
    interesting_links: [...document.querySelectorAll('a')].slice(0,80).map(a=>({
        href: a.href, text: (a.textContent||'').trim().slice(0,80)
    })).filter(l => /verif|lookup|search|find|directory|roster|public|active|license/i.test(l.text+l.href)).slice(0,15),
    cloudflare_marker: !!document.querySelector('[id*=cf-]') || /cloudflare|attention required/i.test(document.title) || /just a moment/i.test(document.body.innerText||''),
    login_form: !!document.querySelector('input[type=password]') || /sign\\s*in|log\\s*in|username/i.test(document.body.innerText||''),
    has_table_results: document.querySelectorAll('table tbody tr').length,
})
"""


async def probe_url(board: str, url: str, transport: TransportConfig) -> dict:
    out = {"board": board, "url": url, "status": None, "data": None, "error": None}
    try:
        async with get_page(transport) as page:
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=25_000)
                out["status"] = resp.status if resp else "no_response"
            except Exception as e:
                out["error"] = f"goto: {e}"
                return out
            try:
                await page.wait_for_load_state("networkidle", timeout=12_000)
            except Exception:
                pass
            await asyncio.sleep(2.0)
            try:
                data = await page.evaluate(PROBE_JS)
                import json as _json
                out["data"] = _json.loads(data) if isinstance(data, str) else data
            except Exception as e:
                out["error"] = f"probe: {e}"
    except Exception as e:
        out["error"] = f"browser: {e}"
    return out


def _safe(s):
    try:
        return str(s).encode("ascii", "replace").decode("ascii")
    except Exception:
        return repr(s)


async def main(boards):
    transport = TransportConfig(
        browser="chromium",
        headless=True,
        viewport={"width": 1280, "height": 900},
        timeout_ms=45000,
        navigation_timeout_ms=25000,
        rate_limit={"delay_between_requests_ms": 1000, "max_concurrent": 1},
        retry={"max_attempts": 1, "backoff_ms": [1000], "retry_on": ["timeout"]},
        proxy={"enabled": True},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    )
    for board in boards:
        urls = PROBES.get(board, [])
        if not urls:
            print(f"[{board}] no urls configured")
            continue
        print("=" * 80)
        print(f"BOARD: {board}")
        for url in urls:
            result = await probe_url(board, url, transport)
            print(f"\n  URL: {url}")
            print(f"  status: {result['status']}")
            if result["error"]:
                print(f"  ERROR: {result['error']}")
                continue
            d = result["data"] or {}
            print(_safe(f"  title: {d.get('title','?')}"))
            print(_safe(f"  final_url: {d.get('url','?')}"))
            print(_safe(f"  cloudflare: {d.get('cloudflare_marker')}  login_form: {d.get('login_form')}  table_rows: {d.get('has_table_results')}"))
            print(_safe(f"  inputs ({len(d.get('inputs',[]))}): {d.get('inputs')[:6]}"))
            print(_safe(f"  iframes ({len(d.get('iframes',[]))}): {d.get('iframes')[:3]}"))
            print(_safe(f"  interesting_links ({len(d.get('interesting_links',[]))}):"))
            for ln in (d.get('interesting_links') or [])[:8]:
                print(_safe(f"    {ln.get('text','')[:50]:50s} -> {ln.get('href','')[:100]}"))
            body = d.get('body_top','')
            if body:
                print(_safe(f"  body_top: {body[:300]}"))


if __name__ == "__main__":
    boards = sys.argv[1:] if len(sys.argv) > 1 else list(PROBES.keys())
    asyncio.run(main(boards))
