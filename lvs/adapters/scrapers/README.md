# PSV License Verification Scraper Engine

Spec-driven Playwright scraper engine for professional license verification.  
243 qualifying boards from the source Excel — each board is a `sites/XX_BOARD/config.yaml` file;
no engine code changes are needed to add a new board (including new `csv_bulk`, `pdf_bulk`, and `certemy` boards).
**Current smoke baseline: 178 PASS / 0 FAIL / 10 SKIP** (2026-06-22; 188 board configs).

---

## Directory layout

```
lvs/adapters/scrapers/
├── engine/                  # shared engine (15 modules)
│   ├── models.py            # Pydantic v2 models — all config + output contracts
│   │                        #   LicenseRecord.partial_result flags degraded results
│   │                        #   TelemetryEvent.partial_result + .warnings for telemetry
│   ├── browser.py           # Playwright launch helper (proxy-aware)
│   ├── navigator.py         # form fill, dropdown, search button interactions
│   │                        #   fill_search_form(partial_failures=) propagates silent errors
│   ├── extractor.py         # results table + detail page extraction strategies
│   │                        #   extract_results_table() → tuple[list, str | None]
│   ├── pdf_extractor.py     # PDF bulk-roster download, table extract, search
│   ├── csv_extractor.py     # CSV bulk-roster download (link_text / post_form), search
│   ├── pagination.py        # next-button / page-numbers pagination
│   ├── output.py            # field normalization → LicenseRecord
│   ├── post_processors.py   # apply_field_map, status_map, date parsing
│   ├── ai_fallback.py       # Azure OpenAI GPT-4 fallback (< 3 fields extracted)
│   ├── telemetry.py         # SQLite scrape_events / ai_touchpoints logging
│   │                        #   scrape_events has partial_result + warnings columns
│   ├── evidence.py          # HTML + screenshot capture per run
│   │                        #   Path: Evidence/{YYYY-MM}/{state}/{source_id}/{YYYYMMDD_HHMM}_{query}/
│   │                        #   capture_evidence(page, config, stage, run_id, source_id, state, query)
│   ├── proxy.py             # corporate proxy config from env vars
│   ├── retry.py             # exponential back-off wrapper
│   └── validate.py          # load_config: YAML → SiteConfig (Pydantic)
│
├── archetypes/              # per-archetype scrape implementations (split from run.py)
│   ├── __init__.py          # re-exports verify_license for backward compat
│   ├── dispatcher.py        # verify_license: run_id generation, capability check, routing
│   ├── _shared.py           # _emit_event, _scrape_one_detail, _navigate_back,
│   │                        #   _wait_for_detail_content, _set_iteration_value
│   ├── socrata.py           # socrata_api + socrata_bulk_csv
│   ├── csv_bulk.py          # csv_bulk
│   ├── pdf_bulk.py          # pdf_bulk
│   ├── certemy.py           # certemy
│   ├── json_api.py          # json_api
│   ├── datatables.py        # datatables_jsapi (partial_failures wired)
│   ├── filemaker.py         # filemaker_webdirect
│   └── browser_form.py      # classic_html_form, state_portal, thentia_cloud,
│                            #   ag_grid_spa, pega_constellation (partial_failures wired)
│
├── sites/                   # per-board YAML configs (188 boards)
│   ├── AK_CBP, AL_ALBME
│   ├── AL_ABESPA, AL_MFT, AL_OPTOMETRY                     # Alabama boards (session 42)
│   ├── FL_MQA
│   ├── NV_MEDBOARD, NV_CHIRO, NV_NVADGC, NV_PT, NV_BOP, NV_DENTAL, NV_OSTEO
│   ├── NV_MASSAGE, NV_SPEECH, NV_OPTOMETRY
│   ├── NV_PODIATRY, NV_MFTPC, NV_ORIENTAL, NV_ABA         # certemy archetype
│   ├── NV_DIETITIAN, NV_PHARMACY                          # aithent_portal_xls / nvbop_angular_xlsx
│   ├── MA_HEALTH, MA_MDDO                                  # MA_MDDO: json_api archetype (session 26 PASS)
│   ├── MA_BSAS                                             # session 42 classic_html_form (MA Substance Abuse)
│   ├── MD_PHYSICIANS, MD_CHIROPRACTIC, MD_MASSAGE, MD_OPTOMETRY, MD_AUDIOLOGY, MD_PT
│   ├── MD_SOCIALWORK, MD_PSYCH, MD_DIETETICS, MD_COUNSELORS
│   ├── MD_ACUPUNCTURE                                      # session 29 csv_bulk direct_url (PASS)
│   ├── MN_COSMETOLOGY, MN_DENTISTRY                        # MN GLSuite PASS
│   ├── MN_EMS, MN_MEDPRACTICE                              # MN_EMS: PASS (session 29); MN_MEDPRACTICE: SKIP (Angular)
│   ├── MI_LARA                                             # Michigan LARA Accela Citizen Access (session 31)
│   ├── MO_HEALING_ARTS, MO_DENTAL, MO_OPTOMETRY, MO_PHARMACY  # Missouri mopro_zip csv_bulk (session 36, PASS)
│   ├── MO_CHIROPRACTIC, MO_PSYCHOLOGISTS                   # Missouri mopro_zip csv_bulk (session 42)
│   ├── MO_NURSING                                          # Missouri Nursing — SKIP (portal redirects to Nursys.com)
│   ├── MS_CHIRO, MS_OPTOMETRY, MS_PT                       # Mississippi: classic_html_form + datatables_jsapi (session 31)
│   ├── MS_DHPL, MS_PSYCH, MS_SWMFT                        # Mississippi: classic_html_form (session 39)
│   ├── MS_LPC                                             # session 40 classic_html_form PASS (MS LPC Board; lpc.ms.gov)
│   ├── MS_ABA                                             # session 41 classic_html_form PASS (MS Autism Board; msautismboard.ms.gov)
│   ├── NJ_DCA
│   ├── KS_DENTAL, KS_OPTOMETRY, KS_PHARMACY, KS_GLSUITE, KS_KSBHADA, KS_BSRB
│   ├── DE_LICENSING
│   ├── WA_HEALTH
│   ├── CO_DORA
│   ├── KY_MEDBOARD, KY_AP, KY_GC, KY_OD, KY_PA, KY_SA, KY_MULTIBOARD
│   ├── LA_MASSAGETHERAPY, LA_ADRA                          # LA_ADRA: certemy; LA_MASSAGETHERAPY: SKIP (PDFs 404)
│   ├── LA_DENTAL, LA_OPTOMETRY, LA_PT, LA_SOCIALWORK
│   ├── LA_DIETETICS, LA_SPEECH                             # session 28 PASS (accordion-expand)
│   ├── LA_MEDBOARD                                         # session 29 PASS (ag_grid_spa)
│   ├── AR_PODIATRY                                         # SKIP (PDF 403)
│   ├── AR_MEDBOARD                                         # session 23 PASS
│   ├── OR_OMB, OR_HLO                                      # OR_HLO: session 29 PASS
│   ├── OR_OT, OR_PSYCH                                     # Thentia PASS
│   ├── OR_COUNSELORS, OR_DENTAL, OR_NATUROPATH, OR_PT, OR_SLP   # session 23-26 PASS
│   ├── OR_OPTOMETRY                                        # session 29 PASS (OGovCore)
│   ├── WY_CHIRO, WY_DIETETICS, WY_PSYCH, WY_MENTAL_HEALTH
│   ├── WY_OT, WY_PODIATRY, WY_RESP, WY_SPEECH
│   ├── WY_PHYSICIAN, WY_PA                                 # WY GLSuite PASS
│   ├── WY_DENTAL, WY_OPTOMETRY, WY_PT                      # WY CSV PASS
│   ├── ND_AP, ND_DENTISTRY, ND_PT, ND_PODIATRY
│   ├── ME_OPLR                                             # session 23 PASS
│   ├── NC_CHIRO, NC_PODIATRY                               # session 28-29 PASS
│   ├── NC_DENTAL, NC_DAC                                   # NC_DENTAL: session 28 PASS
│   ├── NC_DIETETICS, NC_MASSAGE, NC_MENTAL_HEALTH, NC_OT   # session 24-26 PASS
│   ├── NC_SLP_AUD                                          # session 29/30 PASS
│   ├── NC_OPTOMETRY, NC_PT                                 # SKIP (cross-origin iframe / Cloudflare)
│   ├── NY_CREDENTIALS, NY_APPEARANCE                       # NY_CREDENTIALS: SKIP; NY_APPEARANCE: PASS
│   ├── OK_DENTAL, OK_MEDBOARD, OK_OPTOMETRY, OK_OSTEO       # session 23-26 PASS
│   ├── TX_CHIRO, TX_DENTAL                                  # session 26 PASS (FileMaker + DataTables archetypes)
│   ├── TX_MEDBOARD, TX_TDLR, TX_OPTOMETRY                  # TX_OPTOMETRY: session 30 PASS (tob.texas.gov jqGrid)
│   ├── TX_CHEMICAL                                         # session 29 PASS (csv_bulk link_text_xlsx)
│   ├── WV_PT, WV_SOCIALWORK                                # session 25-26 PASS
│   ├── WV_MEDBOARD_MD, WV_MEDBOARD_PA, WV_MEDBOARD_DPM    # WV Board of Medicine (session 32)
│   ├── WV_DENTAL                                          # session 37 PASS (GLSuite ASP.NET form)
│   ├── WV_PSYCH                                           # session 44 PASS (custom_js extraction)
│   ├── ID_DOPL                                             # session 30 PASS (use_keyboard_type + td.TDS)
│   ├── WI_DSPS, NY_APPEARANCE
│   ├── IL_LICENSING, VA_DHP
│   ├── SD_CHIRO, SD_OPT                                    # csv_bulk JS-rendered download
│   ├── SD_AUDIOLOGY, SD_PT, SD_PODIATRY, SD_PSYCH, SD_SPEECH  # pdf_bulk page_link (sdboards.org)
│   ├── HI_DIETITIANS                                          # Hawaii Licensed Dietitians (session 34, datatables_jsapi)
│   ├── PA_PALS                                                # Pennsylvania umbrella PALS portal (session 34, classic_html_form)
│   ├── WV_OPTOMETRY                                        # certemy archetype
│   ├── WV_CHIRO                                            # SKIP (SPO-migrated OneDrive share requires auth)
│   ├── AZ_ACUPUNCTURE, AZ_BEHAVIORAL_HEALTH, AZ_NATUROPATHIC  # session 25 Thentia PASS
│   ├── AZ_OSTEO, AZ_PSYCH, AZ_PT                           # session 25 Thentia PASS
│   ├── AZ_DENTAL, AZ_OPTOMETRY                             # session 24 classic_html_form PASS
│   ├── AZ_SPEECH_HEAR                                      # session 26 multi_iteration PASS
│   ├── AZ_PODIATRY                                         # session 38 Thentia portalus PASS
│   ├── AZ_OT                                               # session 38 onGovCore PASS
│   ├── AZ_CHIRO                                            # session 43 onGovCore PASS (AZSBCE)
│   ├── AZ_MEDBOARD                                         # session 38 GLSuite SKIP (azbomv7prod proxy block)
│   ├── NM_MEDBOARD                                         # session 38 SKIP (Salesforce LWC no archetype)
│   ├── NM_RLD, NM_MIDWIVES                                 # session 38 SKIP/PASS (NM_MIDWIVES PASS; NM_RLD SKIP)
│   ├── NH_OPLC                                             # session 42 classic_html_form (NH OPLC; no proxy — Akamai WAF)
│   ├── OK_ADAC                                             # session 44 PASS (custom_js extraction; PROXY)
│   ├── OK_ODOHCS                                           # session 43 SKIP (thentiacloud_api_blocked_corporate; proxy: false)
│   ├── OH_PROVIDERS_BUSINESS, OH_PROVIDERS_INDIVIDUAL       # session 29 csv_bulk ohio_data_portal_csv PASS
│   ├── SC_SCLLR_LPCMFT, SC_SCLLR_SW                       # session 39 datatables_jsapi PASS (SC LLR Telehealth)
│   └── CT_ELICENSE, IN_PLA, VT_MEDBOARD, VT_OPR           # VT_OPR: session 43 SKIP (Pega Constellation)
│
├── run.py                   # CLI entry point (167 lines) — thin dispatcher; re-exports
│                            #   verify_license from archetypes.dispatcher for backward compat
├── smoke_all.py             # regression gate — runs all boards' smoke_test blocks
├── psv_test.py              # PSV batch verifier — reads Input.xlsx, writes Pass/Fail + expiry + reason
├── psv_config.yaml          # project-level settings: proxy.server default (auto-loaded by proxy.py)
├── board_routing_master.csv # routing: (state, psv_prov_type) → [source_id, ...]  (795 entries, 41 states)
├── board_routing.py         # same routing as hardcoded Python dict — fallback when CSV absent
├── board_inventory.py       # reads Excel, emits filtered board list
├── board_inventory.xlsx     # 186 boards — smoke test status, 6-sprint priority, proxy flags
└── requirements.txt
```

---

## Quick start

```bash
cd lvs/adapters/scrapers

# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Single board — license number lookup (proxy auto-configured via psv_config.yaml)
python run.py --config sites/NV_MEDBOARD/config.yaml --mode license_number --query "17371"

# Single board — last name, headed browser for debugging
python run.py --config sites/KY_MEDBOARD/config.yaml --mode last_name --query "Smith" --headed

# Structured field combo (preferred over --mode/--query)
python run.py --config sites/OH_PROVIDERS_INDIVIDUAL/config.yaml \
  --license-number 35076302 --last-name SMITH

# Validate config without running
python run.py --config sites/KS_DENTAL/config.yaml --dry-run

# Regression gate — all boards (proxy auto-configured; no PROXY= prefix needed)
python smoke_all.py

# Regression gate — specific boards
python smoke_all.py --filter KY_OD KY_MULTIBOARD NV_OPTOMETRY

# Show what would run without launching browsers
python smoke_all.py --dry-run
```

---

## Proxy configuration

Proxy is **automatic** — no `PROXY=proxy:9119` prefix is required.

`engine/proxy.py` resolves the proxy server in this order:
1. `LVS_PROXY_SERVER` env var (highest priority)
2. `PROXY` env var
3. `psv_config.yaml` → `proxy.server` (project-level default, already set to `proxy:9119`)

Each board's `config.yaml` declares:
- `transport.proxy.enabled: true`  → board requires proxy (auto-resolved from above)
- `transport.proxy.enabled: false` → proxy force-disabled (e.g. NH_OPLC — Akamai WAF)
- *(absent)*                       → use proxy if configured, skip if not

### Boards requiring proxy (`proxy: enabled: true`)

| Board | Notes |
|-------|-------|
| AL_MFT, AL_OPTOMETRY | Alabama boards |
| LA_ADRA, LA_MASSAGETHERAPY | Louisiana boards |
| MS_ABA, MS_LPC | Mississippi boards |
| OH_PROVIDERS_INDIVIDUAL | Ohio CSV download via proxy |
| OK_ADAC | Oklahoma DAC board |
| WV_DENTAL, WV_OPTOMETRY, WV_PT, WV_PSYCH, WV_SOCIALWORK | West Virginia boards |

### Boards that block proxy (`proxy: enabled: false`)

| Board | Reason |
|-------|--------|
| NH_OPLC | Akamai WAF returns 403 when request comes through corporate proxy |
| NV_MEDBOARD | Direct connection required |

### Overriding from the shell

```bash
# Disable proxy for a run
PROXY="" python smoke_all.py

# Force a specific proxy
PROXY=proxy:8080 python smoke_all.py --filter WV_PT

# Check what proxy will be used
python -c "from engine.proxy import get_proxy_config; print(get_proxy_config())"
```

---

## PSV batch output channels

`psv_test.py` / `run_psv.py` produce up to six output files per run under
`PSV_DEV/Output/{YYYYMM}/`:

| Folder | File | Contents | When written |
|--------|------|----------|--------------|
| `Standard/` | `{run_id}.xlsx` + `.csv` | Every input row — Pass/Fail, expiry, match method, fuzzy score, board name | Always |
| `NPPES/` | `{run_id}.csv` | NPI registry lookup result + diff vs master row | Always |
| `AIFallback/` | `{run_id}.csv` | Every row where the AI agent ran — turns, tools, outcome | When AI ran |
| `Manual/` | `{run_id}.csv` | Every unresolved row with structured `failure_reason` | When any row needs review |
| `AddLicense/` | `{run_id}_AddLicense.xlsx` | Clean Pass rows ready to upload — EPDB, State, MaintBy, LicenseNumber, LicenseTermDate, LicenseType, OverrideExistingLicense | When Pass rows with board-returned expiry exist |
| `AIAddLicense/` | `{run_id}_AIAddLicense.xlsx` | "Almost sure" rows — same columns as AddLicense + match context (**see below**) | When any qualifying match exists |
| `RunSummary/` | `{run_id}_summary.csv` | Per-state counters (total, pass, fail, manual, add_license, ai_add_license, …) | Always |

### AddLicense column rules

| Column | Source |
|--------|--------|
| EPDB | Input EPDB PIN |
| State | Input license state |
| MaintBy | Input maintained-by field |
| LicenseNumber | Board-verified license number |
| LicenseEffDate | *(blank)* |
| LicenseTermDate | Board-returned expiry date (MM/DD/YYYY) |
| LicenseType | Input lic_type |
| OriginalLicenseDate | *(blank)* |
| OverrideExistingLicense | `Yes` |
| EPDBDone | *(blank — filled post-upload)* |

### AI AddLicense — extra context columns

The `AIAddLicense` sheet has the same 10 columns as `AddLicense`, plus:

| Column | Description |
|--------|-------------|
| VerificationReason | Short label explaining why this row is in AI_ADD_LICENSE |
| InputName | First + last name from input row |
| BoardMatchedName | Name returned by the board |
| InputLicense | License number from input row |
| BoardMatchedLicense | License number returned by the board |
| MatchScore | Disambiguator fuzzy score (0–1), blank for NPI/cross-row matches |

> **Upload rule:** columns A–J (EPDB → EPDBDone) are the upload payload.
> Columns K–P are review context only — do **not** include them in the upload template.

### Which rows qualify for AI AddLicense

A row appears in `AI_ADD_LICENSE` when the engine is "almost sure" but wants a human
to confirm before upload.  The row **also** appears in `Manual` (mutual exclusivity
with `AddLicense` still holds; `AI_ADD_LICENSE` is additive):

| VerificationReason | Root cause |
|--------------------|------------|
| `AI agent resolved match — verify before upload` | AI agent or disambiguator identified the correct record |
| `Numeric license match — format differs (input: X → board: Y)` | Digit-for-digit match; only prefix/punctuation differs (e.g. `12345` vs `LC-12345`) |
| `License exact match — name on board differs (input: X → board: Y)` | Board license number matches exactly; board name differs from input |
| `Name exact match — license on board differs (input: X → board: Y)` | Input name matches board name; license numbers don't align |
| `NPI-verified match — confirm license before upload` | NPI registry lookup found the record; license confirmed via NPI substitution |
| `Cross-row name match — same provider found via another row in this batch` | Post-run reconciliation matched a Fail row to an already-passing row for the same provider |

### Manual-only rows (not in AI AddLicense)

| failure_reason prefix | Meaning |
|-----------------------|---------|
| `Captcha Based Board: …` | Board is CAPTCHA/WAF-blocked — manual board lookup required |
| `AI fallback failed — …` | AI agent ran but could not resolve |
| `License Expired after fetch` | Board confirmed the license exists but expiry is already in the past |
| `Expired and same as input` | Board expiry matches input and is in the past |
| `Check again later for updates, same as input` | Board expiry matches input and is still in the future |
| `no_expiry_date: …` | Pass but board returned no expiry date |
| `no_records` / `name_mismatch` / etc. | Rule-based Fail — no match found |

---

## Running smoke tests with known good values

Every `config.yaml` contains a `smoke_test` block with a stable query and expected result.
These values are the canonical "known good" inputs for each board and are the same ones
`smoke_all.py` uses automatically. You can also run them manually with `run.py`.

### Run a single board's smoke test manually

```bash
# Use the smoke_test.query value and smoke_test.mode directly in run.py:

# CO_DORA — license_number mode, query "9944947"
python run.py --config sites/CO_DORA/config.yaml --mode license_number --query "9944947"

# WA_HEALTH — license_number mode, query "RN.RN.61663091"
python run.py --config sites/WA_HEALTH/config.yaml --mode license_number --query "RN.RN.61663091"

# NV_MEDBOARD — license_number mode, query "17371"
python run.py --config sites/NV_MEDBOARD/config.yaml --mode license_number --query "17371"

# KS_DENTAL — license_number mode, query "13578"
python run.py --config sites/KS_DENTAL/config.yaml --mode license_number --query "13578"

# DE_LICENSING — last_name mode, query "Smith"
python run.py --config sites/DE_LICENSING/config.yaml --mode last_name --query "Smith"

# NY_APPEARANCE — last_name mode, query "Smith"
python run.py --config sites/NY_APPEARANCE/config.yaml --mode last_name --query "Smith"

# AR_PODIATRY — license_number mode, query "247"
python run.py --config sites/AR_PODIATRY/config.yaml --mode license_number --query "247"

# IL_LICENSING — license_number mode, query "198000043" (David Smith, Acupuncturist)
python run.py --config sites/IL_LICENSING/config.yaml --mode license_number --query "198000043"

# IL_LICENSING — last_name search
python run.py --config sites/IL_LICENSING/config.yaml --mode last_name --query "Smith"

# VA_DHP — proxy auto-configured via psv_config.yaml
python run.py --config sites/VA_DHP/config.yaml --mode license_number --query "0024166737"

# NV_CHIRO — proxy auto-configured
python run.py --config sites/NV_CHIRO/config.yaml --mode license_number --query "B02060"
```

### Run the full smoke suite with all known good values

```bash
# All boards — proxy is auto-configured via psv_config.yaml (no PROXY= prefix needed)
python smoke_all.py

# Save results to a timestamped file
python smoke_all.py > smoke_$(date +%Y%m%d_%H%M).txt 2>&1

# Parallel (faster — 3 browsers at once)
python smoke_all.py --concurrency 3
```

### Run a subset of boards by name

```bash
# Run only the boards you recently added or changed
python smoke_all.py --filter CO_DORA NY_APPEARANCE

# Debug a single board with headed browser (visible Chromium window)
python smoke_all.py --filter NV_DENTAL --headed

# Run all KY and KS boards together
python smoke_all.py --filter KY_MEDBOARD KY_AP KY_GC KY_OD KY_PA KY_SA KY_MULTIBOARD KS_DENTAL KS_OPTOMETRY KS_PHARMACY KS_GLSUITE KS_KSBHADA KS_BSRB
```

### Reading smoke test values from config

Each `config.yaml` ends with a `smoke_test` block. To see what query + expected result
a board uses without running it:

```bash
# Print the smoke_test block for any board
python -c "
import yaml, sys
cfg = yaml.safe_load(open('sites/' + sys.argv[1] + '/config.yaml'))
import json; print(json.dumps(cfg.get('smoke_test', {}), indent=2))
" CO_DORA
```

### Per-board smoke test quick reference

| Board | Mode | Query | Expected first result |
|-------|------|-------|-----------------------|
| AK_CBP | last_name | Smith | **SKIP** (DataDome CAPTCHA from corp IP) |
| AL_ABESPA | last_name | Smith | [3171] Smith, Abby Lauren — active (+16 more) (session 44) |
| AL_ALBME | last_name | Smith | Smith — min 1 record (auto-proxy) |
| AL_MFT | last_name | Smith | [259] Smith, Charles Manuel — unknown (+7 more) (auto-proxy) |
| AL_OPTOMETRY | last_name | Smith | [S-275] Howard Smith — unknown (+10 more) (auto-proxy) |
| AR_PODIATRY | license_number | 247 | [247] Jason Smith — min 1 record |
| CO_DORA | license_number | 9944947 | [9944947] Kevin Smith — active |
| CT_ELICENSE | license_number | 82619 | [082619] Alif Ahmed — active |
| DE_LICENSING | last_name | Smith | min 1 record |
| FL_MQA | last_name | Smith | Smith — min 1 record |
| HI_DIETITIANS | last_name | Smith | [75-LD] Daryl Smith-Oswald (+2 more) |
| IL_LICENSING | license_number | 198000043 | [198000043] David Smith — active |
| IN_PLA | last_name | Smith | Smith IV, Robert H. — unknown (min 1 record) |
| KS_BSRB | license_number | LSCSW 4719 | MeLinda Smith-Moore — active |
| KS_DENTAL | license_number | 13578 | Abbigail Smith — active |
| KS_GLSUITE | license_number | 2720 | min 1 record |
| KS_KSBHADA | last_name | Burroughs | min 1 record |
| KS_OPTOMETRY | last_name | Smith | min 1 record |
| KS_PHARMACY | last_name | Baker | min 1 record |
| KY_AP | last_name | Smith | min 1 record |
| KY_GC | last_name | Smith | min 1 record |
| KY_MEDBOARD | last_name | Smith | min 1 record |
| KY_MULTIBOARD | last_name | Smith | min 1 record |
| KY_OD | last_name | Smith | min 1 record |
| KY_PA | last_name | Smith | min 1 record |
| KY_SA | last_name | Smith | min 1 record |
| LA_ADRA | last_name | Smith | [641] Charles R. Smith — active (+19) (auto-proxy) |
| LA_DENTAL | license_number | 3842 | [3842] SHANA SMITHWICK — active |
| LA_DIETETICS | last_name | Smith | **SKIP** (lazy_loaded_accordion) |
| LA_MASSAGETHERAPY | last_name | Smith | [LA9826] ALEXIS SMITH (+30 more) (auto-proxy) |
| LA_OPTOMETRY | last_name | Buisson | Laura Buisson — min 1 record |
| LA_PT | last_name | Smith | Smith — min 1 record |
| LA_SOCIALWORK | last_name | Smith | [?] Addie Smith — min 1 record |
| LA_SPEECH | last_name | Smith | **SKIP** (lazy_loaded_accordion) |
| MA_BSAS | last_name | Smith | [11100] Krista Sand — unknown (+21 more Smith records) |
| MA_HEALTH | last_name | Smith | min 1 record |
| MI_LARA | last_name | Smith | Smith — min 1 record (auto-proxy) |
| MN_COSMETOLOGY | last_name | Smith | Smith — min 1 record (191 records) |
| MN_DENTISTRY | last_name | Smith | Smith — min 1 record (169 records) |
| MD_AUDIOLOGY | last_name | Smith | Smith — min 1 record |
| MD_COUNSELORS | last_name | Goldstein | [LC0557] HAYA GOLDSTEIN — expired |
| MD_DIETETICS | last_name | Williams | [DX4288] Agata Williams — active (no Smiths in registry) |
| MD_CHIROPRACTIC | last_name | Smith | Smith — min 1 record |
| MD_MASSAGE | last_name | Smith | Smith — min 1 record |
| MD_OPTOMETRY | last_name | Smith | Smith — min 1 record |
| MD_PHYSICIANS | license_number | D0091066 | min 1 record |
| MD_PSYCH | last_name | Smith | [01103] A ROY SMITH — revoked |
| MD_PT | last_name | Smith | Smith — min 1 record |
| MD_SOCIALWORK | last_name | Smith | Smith — min 1 record (509 records) |
| ND_AP | last_name | Smith | [L20] Smith, Allison — unknown (+2) |
| ND_DENTISTRY | last_name | Smith | [2550] Smith, Joshua — expired (+2) |
| ND_PODIATRY | last_name | Anderson | [46] Brad Anderson — unknown (no Smiths in registry) |
| ND_PT | last_name | Smith | [PT] NAISMITH BERG, LAURIE — unknown (+15) |
| NH_OPLC | last_name | Smith | [002720-21] Smith, A. Jean — inactive (+40 more) |
| NJ_DCA | last_name | Smith | Smith — min 1 record |
| NM_MIDWIVES | last_name | Smith | [928] Smith, Szodyraa — active |
| NV_ABA | last_name | Smith | [RBT2632] Smith Cheregosha — expired |
| NV_BOP | last_name | Highsmith | Jennifer Highsmith — active |
| NV_DIETITIAN | last_name | Smith | Smith — min 1 record (27 records; PROXY) |
| NV_PHARMACY | last_name | Smith | Smith — min 1 record (89 records; PROXY) |
| NV_CHIRO | license_number | B02060 | Francisco Cruz — active (auto-proxy) |
| NV_DENTAL | license_number | LL-251-11 | min 1 record |
| NV_MASSAGE | last_name | Smith | Smith — min 1 record (auto-proxy) |
| NV_MEDBOARD | license_number | 17371 | Eli Azzi — inactive |
| NV_MFTPC | last_name | Smith | Hernoria Childress-Smith — active |
| NV_NVADGC | last_name | Smith | [183-C] Anita Smith — expired (auto-proxy) |
| NV_OPTOMETRY | last_name | Smith | Smith — min 1 record |
| NV_ORIENTAL | last_name | Abare | [2031] Rachel Abare — unknown |
| NV_OSTEO | last_name | Hatch | Preston Hatch — active (auto-proxy) |
| NV_PODIATRY | last_name | Smith | [9203] Lary Smith — active |
| NV_PT | license_number | 3485 | Sarah Distad — active (auto-proxy) |
| NV_SPEECH | last_name | Smith | Smith — min 1 record (auto-proxy) |
| NY_APPEARANCE | last_name | Smith | SMITH — min 1 record |
| OR_HLO | last_name | Smith | **SKIP** (network_blocked: UpdatePanel AJAX issue) |
| PA_PALS | last_name | Smith | [AA002213L] LEE A SMITH — active (+9 more) |
| OR_OMB | last_name | Smith | Ayre-Smith, Geoffrey — min 1 record |
| TX_MEDBOARD | last_name | Smith | Smith — min 1 record (50+ records) |
| TX_OPTOMETRY | last_name | Smith | **SKIP** (reCAPTCHA on every search) |
| TX_TDLR | last_name | Smithwick | SMITHWICK — min 1 record (4 records) |
| SD_AUDIOLOGY | last_name | Smith | Smith — min 1 record |
| SD_CHIRO | last_name | Smith | [952] Tracy J Smith — active |
| SD_OPT | last_name | Anderson | [738] Eva Anderson — active |
| SD_PODIATRY | last_name | Johnson | [?] Rylan Johnson — min 1 record |
| SD_PSYCH | last_name | Smith | Smith — min 1 record |
| SD_PT | last_name | Smith | Smith — min 1 record |
| SD_SPEECH | last_name | Smith | Smith — min 1 record |
| VA_DHP | license_number | 0024166737 | [0024166737] John R Smith — expired (auto-proxy) |
| VT_MEDBOARD | last_name | Smith | Smith, Delaney — unknown (min 1 record) |
| WA_HEALTH | license_number | RN.RN.61663091 | Madeline Smith — active |
| WI_DSPS | last_name | Smith | Smith — min 1 record |
| WV_CHIRO | license_number | 3842 | **SKIP** (pdf_url_required) |
| WV_DENTAL | last_name | Smith | [1918] Smith, Terri Lynn — unknown (+32 more) (auto-proxy) |
| WV_OPTOMETRY | last_name | Smith | [873-OD] Gary Smith — active (auto-proxy) |
| WY_CHIRO | last_name | Smith | [520] Brian Smith — active |
| WY_DENTAL | last_name | Smith | Smith — min 1 record (2 records) |
| WY_DIETETICS | last_name | Smith | [266] Katherine Smith — active |
| WY_MENTAL_HEALTH | last_name | Smith | [LPC-1832] Mitchell-Smith — active |
| WY_OT | last_name | Smith | [OT-1832] Smith, Bonnie A. — active |
| WY_OPTOMETRY | last_name | Smith | Smith — min 1 record (2 records) |
| WY_PODIATRY | last_name | Smith | [160] Stanton M. Smith — active |
| WY_PSYCH | last_name | Smith | Smith — min 1 record |
| WY_PT | license_number | PT-1338 | [PT-1338] Wright — active |
| WY_RESP | last_name | Smith | [252] Smith, Staci L. — active |
| WY_SPEECH | last_name | Smith | [SP-563] Colette M. Smith — active |
| ID_DOPL | last_name | Smith | **SKIP** (partial: Angular SPA needs live DOM inspection) |
| AR_MEDBOARD | last_name | Smith | **SKIP** (asp_net_radio_postback_required) |
| LA_MEDBOARD | last_name | Smith | **SKIP** (angular_spa_api_intercept_required) |
| ME_OPLR | last_name | Smith | **SKIP** (cascading_dropdown_required) |
| NC_CHIRO | last_name | Smith | **SKIP** (wrong_table_extracted) |
| NC_PODIATRY | last_name | Smith | **SKIP** (card_list_results_unsupported) |
| NY_CREDENTIALS | license_number | 0 | **SKIP** (csv_dataset_url_required) |
| OK_DENTAL | last_name | Smith | **SKIP** (datatables_jsapi_search_unsupported) |
| OK_MEDBOARD | last_name | Smith | **SKIP** (search_button_selector_mismatch) |
| OK_OPTOMETRY | last_name | Smith | **SKIP** (thentia_portalus_dom_differs) |
| OK_OSTEO | last_name | Smith | **SKIP** (thentia_portalus_dom_differs) |
| OR_COUNSELORS | last_name | Smith | **SKIP** (thentia_search_by_dropdown_not_set) |
| OR_DENTAL | last_name | Smith | **SKIP** (angular_spa_one_field_only) |
| OR_NATUROPATH | last_name | Smith | **SKIP** (thentia_search_by_dropdown_not_set) |
| OR_OPTOMETRY | last_name | Smith | **SKIP** (ogovcore_platform_not_validated) |
| OR_OT | last_name | Smith | [287581] KENNETH SMITH — unknown (+9 more) |
| OR_PSYCH | last_name | Smith | [1221] Adeyinka Akinsulure-Smith — expired (+9 more) |
| OR_PT | last_name | Smith | **SKIP** (thentia_search_by_dropdown_not_set) |
| OR_SLP | last_name | Smith | **SKIP** (thentia_search_by_dropdown_not_set) |
| TX_CHIRO | last_name | Smith | **SKIP** (filemaker_webdirect_unsupported) |
| TX_DENTAL | last_name | Smith | **SKIP** (datatables_column_search_unsupported) |
| WV_MEDBOARD_DPM | last_name | Smith | [373] Stacey Smith — unknown (min 1 record) |
| WV_MEDBOARD_MD | last_name | Smith | [31652] Rhonda Burch-Smith — unknown (+63 more) |
| WV_MEDBOARD_PA | last_name | Smith | [1955] Justin Smith — unknown (+20 more) |
| WV_PT | last_name | Smith | Smith — min 1 record (auto-proxy) |
| WV_SOCIALWORK | last_name | Smith | [BP00941134] Michelle Abruzzino-Smith — unknown (+25 more) (auto-proxy) |
| MA_MDDO | last_name | Smith | **SKIP** (json_api_archetype_required) |
| MO_CHIROPRACTIC | last_name | Smith | [2023046993] Delia Smith — active (+29 more) |
| MO_PSYCHOLOGISTS | last_name | Smith | [01487] Lizette Smith — active (+15 more) |
| MO_HEALING_ARTS | last_name | Smith | [2012030851] Stephen Smith — active (+809 more) |
| MO_NURSING | last_name | Smith | **SKIP** (portal_disabled_redirects_to_nursys) |
| MO_DENTAL | last_name | Smith | [003366] Kathleen Lucente-Smith — active (+173 more) |
| MO_OPTOMETRY | last_name | Smith | [T03249] Jill Smith — active (+15 more) |
| MO_PHARMACY | last_name | Smith | [2024026846] Tinley Smith — active (+334 more) |
| MS_CHIRO | last_name | Smith | Smith — min 1 record (auto-proxy) |
| MS_DHPL | last_name | Smith | [TA-4248] ABAGAIL GRAYCE SMITH — active (+49 more) (auto-proxy) |
| MS_OPTOMETRY | last_name | Smith | Smith — min 1 record (auto-proxy) |
| MS_PSYCH | last_name | Smith | [34 559] Smith — unknown (+7 more) (auto-proxy) |
| MS_PT | last_name | Smith | Smith — min 1 record (auto-proxy) |
| MS_SWMFT | last_name | Smith | [Licensed Social Worker] Amelita R Smith — unknown (+49 more) (auto-proxy) |
| MS_LPC | last_name | Smith | [3244] Allison Kenna Smith — active (+52 more) (auto-proxy) |
| MS_ABA | last_name | Smith | [200017] Chelsea Lynn Smith — unknown (+2 more) (auto-proxy) |
| WV_PSYCH | last_name | Smith | [785] Tracy P. Smith — active (+17 more) (session 44, PROXY) |
| SC_SCLLR_LPCMFT | last_name | Smith | [TLC 491 MFT] MICKENS-SMITH, KENYAH MONET — unknown (+9 more) |
| SC_SCLLR_SW | last_name | Smith | [TLS 152 CP] BOYD, KARA ILENE SMITH — unknown (+9 more) |
| AZ_CHIRO | last_name | Smith | [006086] Damian Smith — unknown (+19 more) (auto-proxy) |
| OK_ADAC | last_name | Smith | [288] Flucard-Smith, Vicki — inactive (+9 more) (session 44, PROXY) |
| OK_BEHAVIORAL_HEALTH | last_name | Johnson | **SKIP** (thentiacloud_api_blocked_corporate: obbhl.us.thentiacloud.net; proxy: false) |
| OK_SOCIALWORK | last_name | Smith | **SKIP** (thentiacloud_api_blocked_corporate: osblsw.portalus.thentiacloud.net; proxy: false) |
| OK_ODOHCS | last_name | Smith | **SKIP** (thentiacloud_api_blocked_corporate: odohcs.portalus.thentiacloud.net; proxy: false) |
| VT_OPR | last_name | Smith | **SKIP** (pega_constellation_unsupported) |

---

## Proxy configuration

The engine reads proxy settings from environment variables via `engine/proxy.py`.

### Running with the corporate proxy (required for some NV boards)

```bash
# Set for a single command
PROXY=proxy:9119 python run.py --config sites/NV_CHIRO/config.yaml --mode license_number --query "B02060"

# Set for the full smoke suite
PROXY=proxy:9119 python smoke_all.py

# Windows CMD
set PROXY=proxy:9119 && python smoke_all.py

# Windows PowerShell
$env:PROXY="proxy:9119"; python smoke_all.py
```

The `PROXY` value can be a bare `host:port` (gets `http://` prepended automatically)
or a full URL like `http://proxy:9119`.

### Boards that require `PROXY=proxy:9119`

These sites are blocked by Zscaler without the proxy:

| Board | Why |
|-------|-----|
| NV_CHIRO | `portalus.thentiacloud.net` blocked by Zscaler |
| NV_PT | `portalus.thentiacloud.net` blocked by Zscaler |
| NV_OSTEO | `portalus.thentiacloud.net` blocked by Zscaler |
| NV_NVADGC | `nvadgc.us.thentiacloud.net` blocked by Zscaler |
| NV_MASSAGE | `online.nvmassagebd.com` blocked by Zscaler |
| NV_SPEECH | `nvspeechhearing.org` blocked by Zscaler |
| AK_CBP | `commerce.alaska.gov` blocked by Zscaler |
| AL_ALBME | `dashboard.albme.gov` blocked by Zscaler |
| VA_DHP | `dhp.virginiainteractive.org` blocked by Zscaler |
| MI_LARA | `aca-prod.accela.com` blocked by Zscaler |
| MS_CHIRO | `msbce.ms.gov` blocked by Zscaler |
| MS_DHPL | `msdhpl.webapps.ms.gov` blocked by Zscaler |
| MS_OPTOMETRY | `ms.gov` (msbo subdomain) blocked by Zscaler |
| MS_PSYCH | `msbop.ms.gov` blocked by Zscaler |
| MS_PT | `msbpt.ms.gov` blocked by Zscaler |
| MS_SWMFT | `swmft.webapps.ms.gov` blocked by Zscaler |
| MS_LPC | `lpc.ms.gov` blocked by Zscaler |
| MS_ABA | `msautismboard.ms.gov` blocked by Zscaler |
| AL_MFT | `mft.alabama.gov` blocked by Zscaler |
| AL_OPTOMETRY | `optometry.alabama.gov` blocked by Zscaler |
| OK_ADAC | `okdrugcounselors.org` blocked by Zscaler |
| AZ_CHIRO | `azus-sbce.ongovcore.com` blocked by Zscaler |
| WV_OPTOMETRY | `wvbo.certemy.com` blocked by Zscaler |
| WV_PT | `wvbopt.portalus.thentiacloud.net` blocked by Zscaler |
| WV_SOCIALWORK | `wvsocialworkboard.org` blocked by Zscaler |
| LA_ADRA | `app.certemy.com` blocked by Zscaler |

All other boards (including SD, NJ, MD, FL, IL, CO, WA, NY, DE, WI, KY, KS, MA, WV_MEDBOARD_* boards)
work without proxy — accessible on the corporate network directly or via public Socrata/CSV APIs.
Some Certemy boards (nvba.certemy.com, wvbo.certemy.com) differ: nvba is accessible without proxy;
wvbo.certemy.com requires proxy. Always run the full suite with `PROXY=proxy:9119` to be safe.

> **Note:** FL_MQA (`mqa-internet.doh.state.fl.us`) does NOT require the unauthenticated proxy — it
> is reachable on the corporate network directly. The original `florida_all_providers_web_scraping.py`
> script supports an optional authenticated proxy (`PROXY_NID` + `PROXY_PASSWORD`) but the engine
> does not currently use authenticated proxy for FL_MQA.

### Running without proxy

Simply don't set the `PROXY` env var. Boards that need it will still run but return 0 records:

```bash
python run.py --config sites/KY_MEDBOARD/config.yaml --mode last_name --query "Smith"  # no proxy needed
python smoke_all.py  # proxy-required boards will return 0 records (not a hard failure)
```

### Optional: proxy credentials

If the proxy requires authentication:

```bash
PROXY=proxy:9119 PROXY_NID=your_nid PROXY_PASS=your_password python smoke_all.py
```

The current corporate proxy (`proxy:9119`) works unauthenticated for browser-based requests.

---

## CLI flags reference

### `run.py`

| Flag | Description |
|------|-------------|
| `--config` | Path to `sites/XX_BOARD/config.yaml` (required) |
| `--mode` | `license_number` \| `last_name` \| `first_name` \| `name` |
| `--query` | Search string |
| `--headed` | Run browser in visible mode (great for debugging) |
| `--dry-run` | Load config and print plan; no browser launched |
| `--evidence-dir` | Override evidence output base path |

### `smoke_all.py`

| Flag | Description |
|------|-------------|
| `--filter SOURCE_ID ...` | Run only the named boards (space-separated) |
| `--headed` | Visible browser — combine with `--filter` to debug one board |
| `--concurrency N` | Max parallel browsers (default: 1) |
| `--dry-run` | Print what would run, nothing executed |

Exit codes: `0` = all PASS/SKIP, `1` = any FAIL, `2` = config/arg error.

---

## Board inventory

`board_inventory.py` reads the source Excel (`board_inventory.xlsx`) and filters to the
243 qualifying boards:

- **Remove** if `Captcha` starts with `"Yes"` (103 boards)
- **Remove** if `ingestion` is Manual / CSV-only / PDF-only / API-only (47 boards)
- **Keep** if `ingestion` contains `"Web"` or `"scraping"`

---

## Archetypes

Each board config declares `identity.archetype`. The engine dispatches accordingly.

| Archetype | Description | Example boards |
|-----------|-------------|----------------|
| `thentia_cloud` | Thentia Cloud SPA portals (Angular, Kendo UI) | NV_MEDBOARD, NV_CHIRO, NV_PT, NV_DENTAL, NV_NVADGC, NV_OSTEO |
| `ag_grid_spa` | AG Grid / Salesforce LWC single-page apps | MA_HEALTH, NV_DENTAL, WI_DSPS |
| `classic_html_form` | Traditional form → results table → optional detail page | MD_PHYSICIANS, KY_MEDBOARD, KS_DENTAL, NV_MASSAGE, NV_SPEECH, FL_MQA, VA_DHP, NJ_DCA, MD_AUDIOLOGY, MD_PT |
| `state_portal` | Generic web portal (form search, table, optional detail) | KS_OPTOMETRY, KS_BSRB, NV_BOP |
| `socrata_bulk_csv` | Socrata JSON API via Playwright browser (Zscaler-safe) | DE_LICENSING, WA_HEALTH, CO_DORA, NY_APPEARANCE, IL_LICENSING |
| `pdf_bulk` | Static PDF roster — download, extract tables, search in-memory | NV_OPTOMETRY, LA_MASSAGETHERAPY, AR_PODIATRY |
| `csv_bulk` | CSV roster — download via browser (link or POST form), search in-memory | AK_CBP, AL_ALBME, SD_CHIRO, SD_OPT, CT_ELICENSE |
| `certemy` | Certemy public registry Angular SPA — live-filter search, Material paginator | LA_ADRA, NV_PODIATRY, NV_MFTPC, NV_ORIENTAL, NV_ABA, WV_OPTOMETRY |

### `csv_bulk` specifics

CSV boards do not use a live search form — they download a full roster CSV once (cached locally),
then search in-memory for every query.

```yaml
csv_bulk:
  download_strategy: link_text   # "link_text" | "post_form" | "multi_step_checkbox"
  link_text: "Professional License Download"   # for link_text: visible anchor text to find
  checkbox_section: "Healthcare Practitioners"   # for multi_step_checkbox: section header to click
  practitioner_types:                            # for multi_step_checkbox: practitioner type labels to check
    - "Physician/Surgeon - MD/DO"
  cache_days: 7
  cache_dir: "./csvs"
  encoding: "utf-8-sig"
  search_columns:
    license_number: "LicenseNum"   # CSV column name for each mode
    last_name: "Owners"
    full_name: "Owners"
```

**`link_text` strategy** — navigates to `base_url`, finds an `<a>` tag whose text contains
`link_text`, and fetches that URL via JS `fetch()` inside the browser. Used by **AK_CBP**:
the Alaska CBP main page has a "Professional License Download" hyperlink pointing to the dated CSV.

**`google_sheet_link` strategy** — navigates to `base_url`, finds a link by `link_selector` that points
to (or opens) a public Google Sheet, extracts the sheet ID from the URL, constructs the CSV export URL
(`/export?format=csv`), then downloads via httpx directly (not via browser `expect_download`, which
times out through the corporate proxy). Used by **all 11 Wyoming boards**.
The Google Sheets CSVs have 3–6 rows of board info/description before the actual column headers;
set `header_row` to the 0-based row index of the header row (confirmed per-board):

| Board | header_row | Key columns |
|-------|-----------|-------------|
| WY_CHIRO | 3 | LASTNAME, LicNo, License Status |
| WY_DIETETICS | 3 | LASTNAME, LICNO, License Status |
| WY_PSYCH | 3 | Last Name, License no., Status |
| WY_MENTAL_HEALTH | 3 | Last Name, License Number, License Status |
| WY_OT | 4 | Name (Last,First M.), License # |
| WY_PODIATRY | 6 | Licensee Name, License #, Status (A/I) |
| WY_RESP | 3 | Name (Last,First M.), License no. |
| WY_SPEECH | 3 | Name, Unnamed:3=License#, Unnamed:6=Status |
| WY_DENTAL | 4 | LASTNAME, LICNO, License Status |
| WY_OPTOMETRY | 3 | LASTNAME, LicNo, License Status |
| WY_PT | 3 | NAME (trailing space), LICENSE #; `link_selector_nth: 1` (page has two GSheets links) |

```yaml
csv_bulk:
  download_strategy: google_sheet_link
  link_selector: "a[aria-label='List of Active Licenses']"
  header_row: 3
  cache_days: 7
  cache_dir: "./csvs"
  encoding: "utf-8-sig"
  search_columns:
    license_number: "LicNo"
    last_name: "LASTNAME"
```

**`post_form` strategy** — navigates to `base_url`, reads hidden ASP.NET `__VIEWSTATE` /
`__EVENTVALIDATION` tokens from the DOM, then POSTs them back via JS `fetch()`. Used by
**AL_ALBME**: `dashboard.albme.gov/Verification/roster.aspx` returns the full roster CSV on POST.

**`aithent_portal_xls` strategy** — navigates to `base_url` (an Aithent portal), selects the
`business_unit` from the Business Unit dropdown (ASP.NET postback), waits for the page to reload,
then finds the "Generate Excel" `<a>` tag and clicks it to trigger a `.xls` file download.
The XLS is converted in-memory to CSV using `xlrd` (must be installed: `pip install xlrd`).
Used by **NV_DIETITIAN** (nvdpbh.aithent.com). Requires PROXY=proxy:9119.

**`nvbop_angular_xlsx` strategy** — navigates to `base_url` (NV BOP AngularJS portal), selects
"Personal License Search" radio, chooses `license_type_filter` from the `ng-model` dropdown,
performs a blank search, then clicks "Export To Excel" to download a `.xlsx` file.
The XLSX is converted in-memory to CSV using openpyxl/pandas.
Used by **NV_PHARMACY** (online.nvbop.org). Requires PROXY=proxy:9119.

**`multi_step_checkbox` strategy** — multi-step CT eLicense flow: navigate to GenerateRoster.aspx,
click a Bootstrap collapse panel header (`checkbox_section`) to expand it, wait for checkboxes to
become visible via `offsetParent !== null` (CSS animation), check each `practitioner_types` label
by matching the sibling text node after the `<span><input></span>` wrapper, submit, then override
`window.open` on DownloadRoster.aspx to capture the CSV URL and fetch it with `credentials: include`.
Each requested practitioner type produces a separate CSV (with a `_practitioner_type` column added);
all are concatenated and returned as a single merged CSV. Used by **CT_ELICENSE**.

**`mopro_zip` strategy** — Missouri MOPRO Salesforce LWC portal (`mopro.mo.gov/license/s/license-downloads`).
Selects `board_label` from the Lightning combobox (tries native `<select>` first, falls back to clicking
the Lightning combobox and selecting by role), clicks Submit, waits for Download button(s), downloads
each ZIP, extracts the tab-delimited TXT member (skipping `filedesc*.txt` and 0-byte members), merges
all DataFrames, and returns UTF-8 tab-delimited text. Multi-ZIP boards (e.g. Healing Arts = 36 ZIPs)
are supported — all ZIPs are downloaded sequentially and merged. Cache is saved as UTF-8 regardless of
`encoding` config (portal TXT files can contain Unicode characters).

```yaml
csv_bulk:
  download_strategy: mopro_zip
  board_label: "Pharmacy"       # label to select from the portal combobox
  file_format: txt              # "csv" or "txt"
  separator: "\t"               # column separator (tab for mopro_zip TXT files)
  cache_days: 7
  cache_dir: "./csvs/mo_pharmacy"
  encoding: "latin-1"          # used for load_csv; mopro_zip saves as utf-8 regardless
  search_columns:
    license_number: "lic_number"
    last_name: "prc_last_name"
    first_name: "prc_first_name"
```

Both strategies use a real Chromium browser (proxy-aware) so they work behind Zscaler SSL inspection.
The `search_columns` mapping tells the engine which CSV column to search for each mode.

**Cache naming:** `{source_id}_{YYYYMMDD}.csv` — e.g. `AK_CBP_20260608.csv`.
Freshness is checked by parsing the `YYYYMMDD` suffix from the filename; files dated within
`cache_days` of today are reused without re-downloading. Old-format files (e.g. `DDMMYYYY` suffix)
are silently skipped → one-time re-download on first run after upgrading.

### Why `socrata_bulk_csv` instead of a direct HTTP request

Zscaler SSL interception breaks `urllib.request` and `playwright.request.new_context()`
with `WinError 10054` / ECONNRESET on external Socrata hosts. The `socrata_bulk_csv`
archetype fetches the JSON API endpoint via `page.goto()` in a real Chromium browser,
which uses the Windows OS certificate store and bypasses the TLS interception issue.
The JSON response is rendered as text in the browser and parsed from `page.content()`.

### `certemy` specifics

Certemy boards host public registries at `*.certemy.com/public-registry/{uuid}`.
The engine navigates to the URL, types the search query into `input.search-input`
(Angular reactive live-filter — no submit button), waits for the table to settle,
then extracts `<thead th>` column headers and `<tbody tr td>` rows.
Pagination via Material Design paginator: `[aria-label='Next page']` / `.mat-paginator-navigation-next`.

```yaml
identity:
  archetype: certemy
  base_url: "https://app.certemy.com/public-registry/8ca73d3d-b51c-42b9-9e44-892b2411d264"

detail:
  field_map:
    "Last name": last_name
    "First name": first_name
    "Credential Type": license_type
    "Number": license_number
    "Expiration Status": status
    "Expiration date": expiration_date
    "Date of Registration": issue_date
```

Column headers are discovered at runtime from `<thead th>` text — the `field_map` under `detail:`
maps those live headers to canonical field names. The engine discovers real headers on first run;
use `discover_certemy_headers.py` to probe headers without running a full scrape.

**Certemy boards (all PASS, no proxy required):**

| Board | URL | Key columns |
|-------|-----|-------------|
| LA_ADRA | app.certemy.com | Last name, First name, Credential Type, Number, Expiration Status, Expiration date, Date of Registration |
| NV_PODIATRY | app.certemy.com | First name, Last name, License, Credential ID, Expiration Status, Expiration date, Original issue date |
| NV_MFTPC | nvboe.certemy.com | First name, Last name, License Type, License Number, Expiration Status, Expiration date, Original issue date |
| NV_ORIENTAL | nvbom.certemy.com | First name, Last name, License #, Original issue date, Expiration date, Business Address/City/State/Zip |
| NV_ABA | nvba.certemy.com | Last name, First name, Profession, Credential, Status, Expiration, Original issue date |
| WV_OPTOMETRY | wvbo.certemy.com | First name, Last name, License Number, Expiration Status, Initial License Date, Expiration date + address fields |

---

### `pdf_bulk` specifics

PDF boards do not use a browser for search — they download a roster PDF once (cached),
extract all rows with PyMuPDF, and search in-memory.

```yaml
pdf_bulk:
  pdfs:
    - url: "https://example.gov/licensees.pdf"
      format: default          # "prof" | "estab" | "default"
      license_prefix: "L"      # optional: route by license number prefix
  cache_days: 7
  cache_dir: "./pdfs"
```

- `format: estab` — establishments with `OWNER:` pattern in Name column
- `format: prof` — individual licensees with First/Last name columns
- `license_prefix` — when mode=`license_number`, routes to the PDF whose prefix matches

Download uses Playwright Chromium (`accept_downloads=True` + `expect_download()`)
to handle both Zscaler SSL interception and `Content-Disposition: attachment` PDFs.
An inline fallback reads `response.body()` directly when `expect_download` times out.

**Cache naming:** `{url_stem}_{YYYYMMDD}.pdf` — e.g. `Podiatry_LicenseVerification_20260522_20260608.pdf`.
Freshness is checked by parsing the `YYYYMMDD` suffix from the filename (not file mtime); files
dated within `cache_days` of today are reused. URL-change detection: if the board publishes a new
PDF at a different URL, the stem changes, the old cached file is not matched, and a fresh download
triggers automatically. Old-format files (no date suffix) are silently skipped → one-time
re-download on first run after upgrading.

---

## Adding a new board

### Step 0 (required): Run the archetype triage script first

```bash
python new_board_check.py --url <board-url> --state XX --source-id XX_BOARD
```

This script walks through a 10-question decision tree and tells you:
- **Which archetype to use** (csv_bulk / pdf_bulk / certemy / classic_html_form / etc.)
- **Whether Python code changes are required first** — ~15-20% of real boards need a new
  archetype or download strategy that does not yet exist in the engine
- **A starter `config.yaml` skeleton** tailored to the archetype (pass `--output sites/XX_BOARD/config.yaml` to save it)

**Do not create a `config.yaml` before completing Step 0.**
If the triage verdict is `NEEDS_PYTHON`, file a GitHub issue / Jira ticket before
proceeding. A developer must add the archetype to `archetypes/` first.

```bash
# List all archetypes and their decision signals (no triage, just reference)
python new_board_check.py --non-interactive
```

### Steps 1–7 (after triage confirms an existing archetype)

1. Create `sites/XX_BOARD/config.yaml` — use the skeleton from the triage script.
2. Validate schema: `python -m engine.validate sites/XX_BOARD/config.yaml`
3. Dry-run: `python run.py --config sites/XX_BOARD/config.yaml --mode license_number --query "TBD" --dry-run`
4. Live test (headed): `python run.py --config sites/XX_BOARD/config.yaml --mode license_number --query "TBD" --headed`
5. Add a `smoke_test` block with a real stable query and run `python smoke_all.py --filter XX_BOARD`.
6. Run full regression: `python smoke_all.py` — all prior PASS boards must still PASS.
7. Update `board_inventory.xlsx` — set `Smoke Test Status` to READY, assign Sprint column, add to `board_routing_master.csv`.

### Minimal config skeleton

```yaml
identity:
  source_id: XX_BOARD
  board_name: "Full Board Name"
  state: XX
  country: US
  profession_codes: [MD]
  base_url: "https://example.gov/verify"
  archetype: classic_html_form   # choose from archetypes table above

search:
  modes:
    - mode: license_number
    - mode: last_name
  form:
    search_by_dropdown:
      strategy: none
    search_input:
      selector: "input[name='license']"
    search_button:
      selector: "button[type='submit']"
  results_wait:
    strategy: element_visible
    selector: "table tbody tr"
    timeout_ms: 20000
    no_results_indicators:
      - "no results found"

results:
  type: table
  table:
    row_selector: "table tbody tr"
    cell_selector: "td"
    columns:
      0: license_number
      1: full_name
      2: status
      3: expiration_date
    skip_first_row: false
  has_detail_page: false
  pagination:
    enabled: false
    strategy: none

detail:
  wait:
    strategy: delay
    timeout_ms: 1000
  strategies: []
  field_map:
    "License #": license_number
    "Name": full_name
    "Status": status
    "Expiration": expiration_date

output:
  status_map:
    "Active": active
    "Inactive": inactive
    "Expired": expired
  date_formats:
    - "%m/%d/%Y"
    - "%Y-%m-%d"

transport:
  browser: chromium
  headless: true
  viewport:
    width: 1280
    height: 900
  timeout_ms: 60000
  navigation_timeout_ms: 30000
  rate_limit:
    delay_between_requests_ms: 1000
    max_concurrent: 1
  retry:
    max_attempts: 2
    backoff_ms: [2000, 5000]
    retry_on: ["timeout", "network_error"]

evidence:
  capture_html: true
  capture_screenshot: true
  # Use ["search_results", "detail_page", "error"] when has_detail_page: true
  # Use ["search_results", "error"] when has_detail_page: false
  capture_on: ["search_results", "error"]
  storage: local
  local_path: "Evidence/{source_id}/{run_id}/"  # kept for reference; actual path computed by engine

compliance:
  requires_captcha: false
  requires_login: false
  robots_txt_compliant: true

smoke_test:
  mode: license_number
  query: "12345"
  expect:
    license_number: "12345"
    status: "active"
    full_name_contains: "Smith"
```

### `smoke_test` schema

```yaml
smoke_test:
  mode: license_number          # or last_name / first_name
  query: "12345"
  expect:
    license_number: "12345"     # exact match on first returned record
    status: "active"            # optional — normalized value (active/inactive/expired/suspended/revoked)
    full_name_contains: "Smith" # case-insensitive substring check, optional
    min_records: 1              # minimum records returned (default 1)
  skip: false                   # set true + skip_reason if untestable right now
  skip_reason: ""               # network_blocked | proxy_required | pdf_url_required | captcha | partial
```

**Best practices:**
- Prefer `mode: license_number` — single deterministic result, no first-record ambiguity
- Avoid `mode: last_name` with common names (Smith, Jones) unless the board returns only
  exact single-record results; multi-result pages make `license_number` assertions unstable
- If the site stores names with spaces around hyphens (e.g. `Smith - Moore`), match on
  the non-hyphenated fragment (`full_name_contains: "Smith"`)

---

## Evidence capture

Every browser-based run produces HTML and screenshot evidence.

### Folder structure

```
PSV_DEV/
  Evidence/
    2026-06/
      TX/
        TX_CHIRO/
          20260622_1121_Smith/
            search_results.html    ← full page DOM after results load
            search_results.png     ← full-page screenshot
      NV/
        NV_MEDBOARD/
          20260622_1122_17371/     ← license number as query label
            search_results.html
            search_results.png
          20260622_1122/           ← detail page (no query label)
            detail_page.html
            detail_page.png
```

**Path template:** `Evidence/{YYYY-MM}/{state}/{source_id}/{YYYYMMDD_HHMM}_{query_label}/`

- `{YYYY-MM}` — month folder extracted from `run_id` (e.g. `2026-06`)
- `{state}` — from `config.identity.state` (e.g. `TX`)
- `{source_id}` — board ID (e.g. `TX_CHIRO`)
- `{YYYYMMDD_HHMM}_{query_label}` — timestamp + query identifier (license_number → first_name → last_name → raw query)
- Detail page captures use `{YYYYMMDD_HHMM}/` only (no query label in the detail scraper)

### Files saved per run

| File | Stage | When saved |
|------|-------|------------|
| `search_results.html` | `search_results` | Full page DOM after results load |
| `search_results.png` | `search_results` | Full-page screenshot after results load |
| `detail_page.html` | `detail_page` | DOM when visiting a detail page (`has_detail_page: true`) |
| `detail_page.png` | `detail_page` | Screenshot when visiting a detail page |
| `error.html` | `error` | Page DOM at the point of any exception or failure |
| `error.png` | `error` | Screenshot at the point of any exception or failure |
| `summary.json` | *(non-browser)* | JSON summary for csv_bulk / pdf_bulk / json_api / socrata_api (no Playwright page) |

### Archetype coverage

| Archetype | HTML | Screenshot | Notes |
|-----------|------|------------|-------|
| `classic_html_form`, `state_portal`, `thentia_cloud`, `ag_grid_spa`, `pega_constellation` | ✓ | ✓ | Via `browser_form.py` |
| `certemy` | ✓ | ✓ | Via `certemy.py` |
| `datatables_jsapi` | ✓ | ✓ | Via `datatables.py` |
| `filemaker_webdirect` | ✓ | ✓ | Via `filemaker.py` |
| `socrata_bulk_csv` | ✓ | ✓ | Via `socrata.py` — captures raw JSON body as HTML |
| `csv_bulk`, `pdf_bulk`, `json_api`, `socrata_api` | — | — | No Playwright page; `summary.json` written instead |

All 147 browser-based boards have `capture_html: true`, `capture_screenshot: true`, and `capture_on: ["search_results", "error"]`. Boards with `has_detail_page: true` also have `"detail_page"` in `capture_on`.

### Evidence config options

```yaml
evidence:
  capture_html: true              # Save page HTML to {stage}.html
  capture_screenshot: true        # Save full-page screenshot to {stage}.png
  capture_on:
    - search_results              # Capture after search results load
    - detail_page                 # Capture on detail page (only if has_detail_page: true)
    - error                       # Capture whenever an error or exception occurs
  storage: local
  local_path: "Evidence/{source_id}/{run_id}/"  # NOTE: local_path is ignored by the engine;
                                                 # actual path is always computed from project root
                                                 # as Evidence/{YYYY-MM}/{state}/{source_id}/{ts}_{query}/
```

### Finding evidence for a specific run

```bash
# List all evidence for a board this month
ls Evidence/2026-06/TX/TX_CHIRO/

# Find the latest search_results screenshot for a board
ls -t Evidence/2026-06/NV/NV_MEDBOARD/*/search_results.png | head -1

# Find all error screenshots across all boards (AI debug triage)
find Evidence/ -name "error.png" | sort

# Find HTML for a specific query
find Evidence/2026-06/ -path "*/NV_MEDBOARD/*17371*/search_results.html"
```

**Notes on non-standard archetypes:**
- `socrata_bulk_csv` (CO_DORA, DE_LICENSING, IL_LICENSING, NY_APPEARANCE, WA_HEALTH) — the screenshot and HTML show the raw JSON API response rendered in Chromium. Useful for debugging malformed API responses or blocked requests, not for visual form review.
- `pdf_bulk` (AR_PODIATRY, NV_OPTOMETRY, LA_MASSAGETHERAPY, SD_*) — no browser page after download; evidence captures the download-page state only.
- `csv_bulk`, `json_api`, `socrata_api` — no browser page; a `summary.json` is written with file path, record count, and timestamp.

---

## Key config options

### `results.table`

| Field | Description |
|-------|-------------|
| `row_selector` | CSS selector for data rows |
| `cell_selector` | CSS selector for cells within a row |
| `columns` | `{column_index: field_name}` mapping |
| `skip_first_row` | Skip row 0 when header lives in `<tr>` not `<thead>` |
| `table_selector` | Parent CSS selector when multiple matching tables exist |
| `table_index` | Which match to use (0-based) — requires `table_selector` |

`table_selector` + `table_index` enable nth-table selection without CSS sibling combinators:

```yaml
table:
  table_selector: "#content table.data"
  table_index: 1          # second matching table
  row_selector: "tr"
  cell_selector: "td"
  skip_first_row: true
```

### `search.post_search_click`

Selector to click after the results load — used by web1.ky.gov GenSearch portals
to toggle the grid view:

```yaml
search:
  post_search_click: "#usLicenseList_rdbtGrid"
```

### Per-mode form overrides

When a board has separate form fields for different search modes:

```yaml
search:
  modes:
    - mode: license_number
      input_selector: "#txtLicense"
      button_selector: "#btnLICENSE"
    - mode: last_name
      input_selector: "#LastName"
      button_selector: "#btnLastName"
```

---

## Extraction strategies

The engine tries all declared strategies in order and merges their output.

| Strategy | When to use |
|----------|-------------|
| `dt_dd` | `<dl><dt>Label</dt><dd>Value</dd></dl>` |
| `label_sibling` | `<label>Field</label><span>Value</span>` |
| `field_label_value` | CSS classes like `infoTitle`/`rlabel` adjacent to value spans |
| `two_column_table` | Table where both label and value are `<td>` cells (2-cell rows) |
| `th_td_table` | Table where label is `<th>` and value is `<td>` (e.g. VA_DHP) |
| `four_column_table` | Table with alternating label/value pairs across 4 columns |
| `br_column_table` | GLSuite-style: single row, labels in col 0 separated by `<br>` |
| `strong_label` | `<li><strong>Label:</strong> value</li>` |
| `element_ids` | Direct DOM element ID map (e.g. `#Lic_Status`) |
| `heading_name` | `<h2 class="ng-binding">Name</h2>` for SPA name extraction |

---

## Telemetry

Every run writes to `lvs_scrape.db` (SQLite, auto-created):

| Table | Contents |
|-------|----------|
| `scrape_events` | Per-run: board, mode, query, status, duration, record count, `partial_result` flag, `warnings` JSON array |
| `ai_touchpoints` | AI fallback invocations: board, run_id, tokens used |
| `license_records` | Canonical `LicenseRecord` output per record; `partial_result` column flags records from degraded runs |

A run has `status="partial"` (instead of `"success"`) when any step silently failed but still returned records — e.g. search-by dropdown didn't apply the right mode, extra filters failed to set, or table extraction fell back. The `warnings` column carries the failure details. `partial_result=1` on `license_records` rows flags which records came from those runs.

Query the DB to audit runs:

```bash
# Recent runs and their results
sqlite3 lvs_scrape.db "SELECT source_id, mode, query, status, record_count, duration_ms FROM scrape_events ORDER BY created_at DESC LIMIT 20;"

# AI fallback usage
sqlite3 lvs_scrape.db "SELECT source_id, run_id, tokens_used FROM ai_touchpoints ORDER BY created_at DESC LIMIT 10;"
```

---

## AI fallback

When rule-based extraction yields fewer than 3 fields, the engine sends the page HTML
to Azure OpenAI GPT-4 and merges the result.

**Required env var:** `AZURE_OPENAI_API_KEY`  
The fallback is silently skipped when the key is the placeholder value `"PRASHANT API KEY"`.

---

## Environment variables

| Variable | Used by | Description |
|----------|---------|-------------|
| `AZURE_OPENAI_API_KEY` | `ai_fallback.py` | GPT-4 fallback key |
| `PROXY` | `engine/proxy.py` | Corporate proxy — bare `host:port` or full URL. Required for NV boards. Example: `PROXY=proxy:9119` |
| `LVS_PROXY_SERVER` | `engine/proxy.py` | Full proxy URL override (takes priority over `PROXY`) |
| `PROXY_NID` | `engine/proxy.py` | Proxy username (optional — current proxy works unauthenticated) |
| `PROXY_PASS` | `engine/proxy.py` | Proxy password (optional) |

---

## Network notes (corporate Zscaler)

Zscaler SSL interception breaks `urllib.request` and `playwright.request.new_context()`
with `WinError 10054` / ECONNRESET on some external hosts.

**Workaround:** `playwright.chromium.launch().new_page().goto()` uses the Windows
OS certificate store and works correctly. This is why:

- `socrata_bulk_csv` fetches JSON via `page.goto()` rather than `APIRequestContext`
- `pdf_bulk` downloads PDFs via `page.expect_download()` in a `ThreadPoolExecutor` thread
  (needed because the main Playwright loop is already running an async event loop)

`socrata_api` (original archetype for DE_LICENSING) used `APIRequestContext` + explicit
proxy credentials — it returned HTTP 407 without credentials, so DE_LICENSING was switched
to `socrata_bulk_csv` (browser) which works without proxy credentials.

---

## Board status (as of 2026-06-16)

| Board | State | Archetype | Smoke Status | Notes |
|-------|-------|-----------|--------------|-------|
| AK_CBP | AK | csv_bulk | PASS | 751+ Smith records; covers all AK professions; requires PROXY=proxy:9119 |
| AL_ALBME | AL | csv_bulk | PASS | 487+ Smith records; MD/DO/PA; requires PROXY=proxy:9119 |
| AR_PODIATRY | AR | pdf_bulk | **SKIP** | pdf_url_stale_403: `Podiatry_LicenseVerification_20260522.pdf` returns HTTP 403 (verified 2026-06-16). Update url at https://healthy.arkansas.gov/boards-commissions/boards/podiatric-medicine-board/ |
| CO_DORA | CO | socrata_bulk_csv | PASS | [9944947] Kevin Smith — active; 1.5M+ records on data.colorado.gov |
| CT_ELICENSE | CT | csv_bulk | PASS | `multi_step_checkbox` strategy; 24K+ Physician/Surgeon–MD/DO roster; smoke query license 82619 (Alif Ahmed — [082619]) |
| DE_LICENSING | DE | socrata_bulk_csv | PASS | 500 records; switched from socrata_api (HTTP 407) |
| FL_MQA | FL | classic_html_form | PASS | 20 Smith records; single portal covers all FL health boards; no proxy required |
| IL_LICENSING | IL | socrata_bulk_csv | PASS | [198000043] David Smith — active; 63 IDFPR license types; no proxy needed |
| IN_PLA | IN | classic_html_form | PASS | `t_web_lookup__` input prefix; row_selector `table#datagrid_results > tbody > tr`; Smith query returns 41 records |
| KS_BSRB | KS | classic_html_form | PASS | [LSCSW 4719] MeLinda Smith-Moore — active |
| KS_DENTAL | KS | classic_html_form | PASS | [13578] Abbigail Smith — active |
| KS_GLSUITE | KS | classic_html_form | PASS | br_column_table strategy |
| KS_KSBHADA | KS | state_portal | PASS | strong_label strategy; query Burroughs |
| KS_OPTOMETRY | KS | classic_html_form | PASS | 16 Smiths |
| KS_PHARMACY | KS | classic_html_form | PASS | session 35 SKIP→PASS — public form at ksbop.elicensesoftware.com/portal.aspx is reachable (was wrongly diagnosed as login-only); ASP.NET GridView `#gvResults`, 7 cols (Name/AKA/L/P/R #/City/State/Class/Status); query Baker → [1-126156] Abu Baker, Abdalla — active (+129 more) |
| KY_AP | KY | classic_html_form | PASS | web1.ky.gov AGY=26 |
| KY_GC | KY | classic_html_form | PASS | web1.ky.gov AGY=27 |
| KY_MEDBOARD | KY | classic_html_form | PASS | web1.ky.gov AGY=5; 309 Smiths |
| KY_MULTIBOARD | KY | classic_html_form | PASS | oop.ky.gov; table_index=1 |
| KY_OD | KY | classic_html_form | PASS | web1.ky.gov AGY=8; 6-col grid (includes middle name) |
| KY_PA | KY | classic_html_form | PASS | web1.ky.gov AGY=20; was intermittent FAIL 2026-06-08 |
| KY_SA | KY | classic_html_form | PASS | web1.ky.gov AGY=23; was intermittent FAIL 2026-06-08 |
| LA_ADRA | LA | certemy | PASS | [641] Charles R. Smith — active (+19); app.certemy.com |
| LA_DENTAL | LA | classic_html_form | PASS | [3842] SHANA SMITHWICK — active; member-base.net portal; table table tr; col order H/D, Lic#, Last, First, Status |
| LA_DIETETICS | LA | classic_html_form | PASS | session 28 — post_search_click_all accordion-expand; 40 Smith records |
| LA_MASSAGETHERAPY | LA | pdf_bulk | PASS | session 35 — switched to `page_link` strategy; labmt.org/search/ has anchors "PROFESSIONAL LICENSE SEARCH" + "ESTABLISHMENT LICENSE SEARCH"; engine extended to support per-PDF link_selector; [LA9826] ALEXIS SMITH (+30 more) |
| LA_OPTOMETRY | LA | classic_html_form | PASS | Laura Buisson; MemberLeap portal; element_visible div.search-result; name only (no detail) |
| LA_PT | LA | classic_html_form | PASS | Smith (9 records); laptboard.org; pre_search_click + submit_via_enter; div.licensee cards; h3+dd cell extraction |
| LA_SOCIALWORK | LA | classic_html_form | PASS | [?] Addie Smith — unknown (+9); labswe.org ColdFusion; a#search_button; li.row + h3 name extraction |
| LA_SPEECH | LA | classic_html_form | PASS | session 28 — post_search_click_all accordion-expand; 113 Smith records |
| MA_HEALTH | MA | ag_grid_spa | PASS | AG Grid; 100+ Smiths |
| MD_AUDIOLOGY | MD | classic_html_form | PASS | 5+ Smiths; AUD/SLP board |
| MD_COUNSELORS | MD | classic_html_form | PASS | [LC0557] HAYA GOLDSTEIN — expired (+7); mdbnc.health.maryland.gov/pctverification/; bodyContentPlaceHolder_ prefix |
| MD_DIETETICS | MD | classic_html_form | PASS | [DX4288] Agata Williams — active (+41); mdbnc.health.maryland.gov/dietVerification/; bodyContentPlaceHolder_ prefix; smoke query Williams (no Smiths in registry) |
| MD_CHIROPRACTIC | MD | classic_html_form | PASS | [S04006] ADAM P. SMITH — active; ChiroPortal element_ids detail |
| MD_MASSAGE | MD | classic_html_form | PASS | [M04621] STACY A. ALLGOOD-SMITH — active |
| MD_OPTOMETRY | MD | classic_html_form | PASS | [DA2109] ALESHA SPELLMAN SMITH — expired; two_column_table |
| MD_PHYSICIANS | MD | classic_html_form | PASS | [D0091066]; intermittent Azure OpenAI via Zscaler |
| MD_PSYCH | MD | classic_html_form | PASS | [01103] A ROY SMITH — revoked; MainContent_ prefix; Open Details Page link (not View Details) |
| MD_PT | MD | classic_html_form | PASS | 10+ Smiths; PT/PTA board |
| MD_SOCIALWORK | MD | classic_html_form | PASS | 509 Smith records; bodyContentPlaceHolder_ prefix; col[4]=license_number (not col[2]) |
| ND_AP | ND | classic_html_form | PASS | [L20] Smith, Allison — unknown (+2); ndbihc.org/verify/; table.table tbody tr; col order Name/Type/License#/City/State |
| ND_DENTISTRY | ND | classic_html_form | PASS | [2550] Smith, Joshua — expired (+2); nddentalboard.org/verify/; same bootstrap template as ND_AP |
| ND_PODIATRY | ND | classic_html_form | PASS | [46] Brad Anderson — unknown; ndpodiatryboard.org; WordPress participants-database plugin; select#pdb-search_field-2 + #participant_search_term; smoke query Anderson (no Smiths) |
| ND_PT | ND | classic_html_form | PASS | [PT] NAISMITH BERG, LAURIE — unknown (+15); ndbpt.org/verify/; #inputLastName + button[title*='Search']; table.table tbody tr |
| NJ_DCA | NJ | classic_html_form | PASS | 20+ Smiths; single portal covers all NJ health boards |
| NV_ABA | NV | certemy | PASS | [RBT2632] Smith Cheregosha — expired (+19); nvba.certemy.com |
| NV_BOP | NV | state_portal | PASS | query Highsmith; Jennifer Highsmith — active |
| NV_CHIRO | NV | thentia_cloud | PASS | [B02060] Francisco Cruz — active; requires PROXY=proxy:9119 |
| NV_DENTAL | NV | ag_grid_spa | PASS | Angular detail panel; 2s sleep after expand |
| NV_MASSAGE | NV | classic_html_form | PASS | [NVMT.045] VICKIE L. SMITH — active; requires PROXY=proxy:9119 |
| NV_MEDBOARD | NV | thentia_cloud | PASS | [17371] Eli Azzi — inactive |
| NV_MFTPC | NV | certemy | PASS | [4407] Hernoria Childress-Smith — active (+17); nvboe.certemy.com |
| NV_NVADGC | NV | thentia_cloud | PASS | [183-C] Anita Smith — expired; smoke query changed from stale 01952-I; requires PROXY=proxy:9119 |
| NV_OPTOMETRY | NV | pdf_bulk | PASS | Monthly PDF — update URL monthly |
| NV_ORIENTAL | NV | certemy | PASS | [2031] Rachel Abare — unknown; small board; query Abare; nvbom.certemy.com |
| NV_OSTEO | NV | thentia_cloud | PASS | query Hatch; Preston Hatch — active; requires PROXY=proxy:9119; has_detail_page=false |
| NV_PODIATRY | NV | certemy | PASS | [9203] Lary Smith — active; app.certemy.com |
| NV_PT | NV | thentia_cloud | PASS | [3485] Sarah Distad — active; requires PROXY=proxy:9119 |
| NV_SPEECH | NV | classic_html_form | PASS | [A-3328] Bari L Goldsmith — expired (+20); requires PROXY=proxy:9119 |
| NY_APPEARANCE | NY | socrata_bulk_csv | PASS | 500+ Smiths on data.ny.gov; active-only dataset |
| OR_HLO | OR | classic_html_form | PASS | session 29 — UpdatePanel AJAX resolved via post_search_click + networkidle wait |
| OR_OMB | OR | classic_html_form | PASS | 10 Smith records; AngularJS alx-list-item cards; name only; ng-hide always in DOM |
| SD_CHIRO | SD | csv_bulk | PASS | [952] Tracy J Smith — active; JS-rendered Download (25s wait) |
| SD_OPT | SD | csv_bulk | PASS | [738] Eva Anderson — active; 251 ODs; no Smiths; query Anderson |
| VA_DHP | VA | classic_html_form | PASS | [0024166737] John R Smith — expired; 157 occupation types; requires PROXY=proxy:9119 |
| VT_MEDBOARD | VT | classic_html_form | PASS | MUI Cards (`div[class*='MuiCard-root']`); only full_name extractable (no detail page); input selector `input[id*='r']` |
| WA_HEALTH | WA | socrata_bulk_csv | PASS | [RN.RN.61663091] Madeline Smith — active |
| WI_DSPS | WI | ag_grid_spa | PASS | [10045-142] Chalon Lashae Smith — active; Salesforce slds-table |
| WV_CHIRO | WV | pdf_bulk | **SKIP** | pdf_url_required: navigate to boc.wv.gov/roster.html, find current PDF link (a[href$='.pdf'] or iframe[src*='.pdf']), update pdf_bulk.pdfs[0].url |
| WV_OPTOMETRY | WV | certemy | PASS | [873-OD] Gary Smith — active; 19 columns; wvbo.certemy.com |
| WY_CHIRO | WY | csv_bulk | PASS | [520] Brian Smith — active; google_sheet_link strategy; LASTNAME/LicNo; header_row=3 |
| WY_DIETETICS | WY | csv_bulk | PASS | [266] Katherine Smith — active; LASTNAME/LICNO; header_row=3 |
| WY_MENTAL_HEALTH | WY | csv_bulk | PASS | 21 Smith records; Last Name/License Number; header_row=3 (multi-line quotes merge desc rows) |
| WY_OT | WY | csv_bulk | PASS | [OT-1832] Smith, Bonnie A. — active; Name col="Last, First M."; header_row=4 |
| WY_PODIATRY | WY | csv_bulk | PASS | [160] Stanton M. Smith — active; Licensee Name/License #; header_row=6; A=Active |
| WY_PSYCH | WY | csv_bulk | PASS | Smith (6+ records); Last Name/License no.; Status: Active License/Expired License; header_row=3 |
| WY_RESP | WY | csv_bulk | PASS | [252] Smith, Staci L. — active; Name/License no.; header_row=3 |
| WY_SPEECH | WY | csv_bulk | PASS | [SP-563] Colette M. Smith — active; Name/Unnamed:3(lic#); header_row=3 |
| ID_DOPL | ID | thentia_cloud | PASS | session 30 — pre_search_click tile + use_keyboard_type + td.TDS wait (#3 route ~12s); [055228] JONATHAN LOCK-SMITH — expired (+24 more) |
| AR_MEDBOARD | AR | classic_html_form | PASS | session 23 — pre_search_click 'Verify a License'; #ctl00_MainContentPlaceHolder_ucVerifyLicense_txtVerifyLicNumLastName |
| LA_MEDBOARD | LA | ag_grid_spa | PASS | session 29 — ajax_row_count wait + config tightened against live DOM |
| ME_OPLR | ME | classic_html_form | PASS | session 23 — cascading dropdowns resolved |
| NC_CHIRO | NC | classic_html_form | PASS | session 29 — wpDataTables container row_selector tightened |
| NC_PODIATRY | NC | state_portal | PASS | session 28 — iMIS WebPartManager UUIDs → attribute-contains selectors; [?] 4 Smith records |
| NY_CREDENTIALS | NY | classic_html_form | **SKIP** | wrong_url_no_public_search_form: op.nysed.gov/verification-search redirects to eservices.nysed.gov renewal portal (login required). Unblock: find correct public license lookup URL (per-profession Socrata dataset likely). |
| OK_DENTAL | OK | ag_grid_spa | PASS | session 26 — datatables_jsapi archetype; 7 sub-pages, 55 records |
| OK_MEDBOARD | OK | classic_html_form | PASS | session 23 — search_button resolved |
| OK_OPTOMETRY | OK | thentia_cloud | PASS | session 25 — portalus breakthrough; thentia_cloud PASS |
| OK_OSTEO | OK | thentia_cloud | PASS | session 25 — portalus breakthrough; has_detail_page: false |
| OR_COUNSELORS | OR | thentia_cloud | PASS | session 25 — portalus breakthrough |
| OR_DENTAL | OR | ag_grid_spa | PASS | session 26 — datatables_column_search resolved |
| OR_NATUROPATH | OR | thentia_cloud | PASS | session 25 — portalus breakthrough |
| OR_OPTOMETRY | OR | state_portal | PASS | session 29 — OGovCore config validated |
| OR_OT | OR | thentia_cloud | PASS | session 21 — [287581] KENNETH SMITH — unknown (+9 more) |
| OR_PSYCH | OR | thentia_cloud | PASS | session 21 — [1221] Adeyinka Akinsulure-Smith — expired (+9 more) |
| OR_PT | OR | thentia_cloud | PASS | session 23/25 — portalus breakthrough |
| OR_SLP | OR | thentia_cloud | PASS | session 23/25 — portalus breakthrough |
| TX_CHIRO | TX | classic_html_form | PASS | session 26 — filemaker_webdirect archetype; 23 records |
| TX_DENTAL | TX | ag_grid_spa | PASS | session 26 — datatables_jsapi archetype; 15 records |
| WV_MEDBOARD_DPM | WV | csv_bulk | PASS | session 32 — link_text_xlsx; wvbom.wv.gov "Roster of Podiatric Physicians" XLSX (1 sheet) |
| WV_MEDBOARD_MD | WV | csv_bulk | PASS | session 32 — link_text_xlsx; wvbom.wv.gov "Roster of Medical Doctors" XLSX (6 sheets, engine reads Licenses sheet) |
| WV_MEDBOARD_PA | WV | csv_bulk | PASS | session 32 — link_text_xlsx; wvbom.wv.gov "Roster of Physician Assistants" XLSX (3 sheets, engine reads Licenses sheet) |
| WV_PT | WV | thentia_cloud | PASS | session 25 — portalus breakthrough; has_detail_page: false |
| WV_DENTAL | WV | classic_html_form | PASS | session 37 — GLSuite ASP.NET form; wvbodv7prod.glsuite.us; 33 Smith records; no detail page; expiration_date used for status |
| WV_SOCIALWORK | WV | classic_html_form | PASS | session 26 — ajax_row_count wait; DNN SQLViewPro; 26 records |
| MA_MDDO | MA | classic_html_form | PASS | session 26 — json_api archetype; api.medboard.mass.gov; 504 records |
| MN_COSMETOLOGY | MN | classic_html_form | PASS | 191 Smiths; GLSuite bcegl.hlb.state.mn.us; has_detail_page: false (detail links are javascript:__doPostBack) |
| MN_DENTISTRY | MN | classic_html_form | PASS | 169 Smiths; row_selector "#DataTable table tr" (nested table); GLSuite mnbodv7prod.glsuite.us |
| MN_EMS | MN | classic_html_form | PASS | session 29 — direct URL navigation + selector refinement |
| MN_MEDPRACTICE | MN | classic_html_form | **SKIP** | Angular SPA at bmp.hlb.state.mn.us; accordion-based inline detail expansion not supported |
| NV_DIETITIAN | NV | csv_bulk | PASS | 3535 total; 27 Smiths; aithent_portal_xls: Generate Excel <a> click → .xls download; xlrd required; requires PROXY=proxy:9119 |
| NV_PHARMACY | NV | csv_bulk | PASS | 89 Smiths; nvbop_angular_xlsx: AngularJS dropdown → Export Excel → .xlsx download; requires PROXY=proxy:9119 |
| TX_MEDBOARD | TX | classic_html_form | PASS | 50+ Smiths; pre_search_click waits networkidle after btnAccept; has_detail_page: false (JS __doPostBack links); col[4]=city |
| TX_OPTOMETRY | TX | classic_html_form | PASS | session 30 — tob.texas.gov jqGrid form (no reCAPTCHA); selectors last_nme/lic_nbr; tr.jqgrow rows; [2259] SMITH, ALLISON K. — expired (+19 more) |
| TX_TDLR | TX | classic_html_form | PASS | 4 Smithwicks; row_selector "tr:has(> td[width='90'])"; has_detail_page: false; smoke query "Smithwick" |
| WY_DENTAL | WY | csv_bulk | PASS | 2 Smiths; google_sheet_link; httpx direct download (bypasses proxy timeout); download_timeout_ms: 180000 |
| WY_OPTOMETRY | WY | csv_bulk | PASS | 2 Smiths; google_sheet_link; httpx direct download (bypasses proxy timeout); download_timeout_ms: 180000 |
| WY_PA | WY | classic_html_form | PASS | [?] Smith, Carol Louise — 46 PA records; GLSuite wybomv7prod stacked label-value format parses correctly |
| WY_PHYSICIAN | WY | classic_html_form | PASS | [4407A] SMITH, JOSEPH T, MD — expired (+127); GLSuite wybomv7prod; skip_first_row=true for td header row |
| WY_PT | WY | csv_bulk | PASS | [PT-1338] Wright — active; google_sheet_link; httpx download; link_selector_nth: 1 (PT roster is second GSheets link) |
| AZ_ACUPUNCTURE | AZ | thentia_cloud | PASS | session 25 — portalus breakthrough; Arizona Acupuncture Board |
| AZ_BEHAVIORAL_HEALTH | AZ | thentia_cloud | PASS | session 25 — portalus breakthrough; Arizona BBHE |
| AZ_DENTAL | AZ | classic_html_form | PASS | session 24 — Arizona State Board of Dental Examiners |
| AZ_NATUROPATHIC | AZ | thentia_cloud | PASS | session 25 — portalus breakthrough; Arizona Naturopathic |
| AZ_OPTOMETRY | AZ | classic_html_form | PASS | session 24 — Arizona State Board of Optometry |
| AZ_OSTEO | AZ | thentia_cloud | PASS | session 25 — portalus breakthrough; has_detail_page: false |
| AZ_PSYCH | AZ | thentia_cloud | PASS | session 25 — portalus breakthrough; Arizona Board of Psychologist Examiners |
| AZ_PT | AZ | thentia_cloud | PASS | session 25 — portalus breakthrough; Arizona Board of Physical Therapy |
| AZ_SPEECH_HEAR | AZ | classic_html_form | PASS | session 26 — multi_iteration (9 provider-type codes); 43 records |
| MD_ACUPUNCTURE | MD | csv_bulk | PASS | session 29 — direct_url strategy; Maryland Board of Acupuncture |
| NC_DAC | NC | classic_html_form | PASS | session 24 — NC Substance Abuse Professional Certification Board |
| NC_DENTAL | NC | classic_html_form | PASS | session 28 — Bootstrap div.search-results cards; 250 Smith records |
| NC_DIETETICS | NC | classic_html_form | PASS | session 26 — vertical_kv extractor; 108 records; no table |
| NC_MASSAGE | NC | classic_html_form | PASS | session 24 — NC Board of Massage and Bodywork Therapy |
| NC_MENTAL_HEALTH | NC | classic_html_form | PASS | session 26 — ajax_row_count wait; #MultiResultsList; 101 records |
| NC_OPTOMETRY | NC | classic_html_form | **SKIP** | cross-origin Google Drive iframe; content_frame() blocked by same-origin policy |
| NC_OT | NC | classic_html_form | PASS | session 24 — NC Board of Occupational Therapy |
| NC_PT | NC | classic_html_form | **SKIP** | cloudflare_intermittent: Cloudflare traffic split blocks corporate proxy automation |
| NC_SLP_AUD | NC | classic_html_form | PASS | session 29/30 — empty no_results_indicators (avoid "0 Records Found" in "90 Records Found" false-positive); [30004433] Abigail Joy Smith — inactive (+89 more) |
| OH_PROVIDERS_BUSINESS | OH | csv_bulk | PASS | session 29 — ohio_data_portal_csv strategy; State of Ohio Licensure |
| OH_PROVIDERS_INDIVIDUAL | OH | csv_bulk | PASS | session 29 — ohio_data_portal_csv strategy; State of Ohio Licensure |
| TX_CHEMICAL | TX | csv_bulk | PASS | session 29 — link_text_xlsx strategy; Texas HHS Licensed Chemical Dependency Counselors |
| HI_DIETITIANS | HI | datatables_jsapi | PASS | session 34 — TablePress 7 DataTables 2.x global search; #tablepress-7; [75-LD] Daryl Smith-Oswald (+2 more) |
| PA_PALS | PA | classic_html_form | PASS | session 34 — Pennsylvania umbrella PALS portal (29 boards/commissions); AngularJS SPA at pals.pa.gov; ajax_row_count wait on #DataTables_Table_3; [AA002213L] LEE A SMITH — active (+9 more) |
| MO_HEALING_ARTS | MO | csv_bulk | PASS | session 36 — mopro_zip strategy; 36 ZIPs, 134K records; [2012030851] Stephen Smith — active (+809 more) |
| MO_DENTAL | MO | csv_bulk | PASS | session 36 — mopro_zip strategy; 19 ZIPs, 18K records; [003366] Kathleen Lucente-Smith — active (+173 more) |
| MO_OPTOMETRY | MO | csv_bulk | PASS | session 36 — mopro_zip strategy; 1 ZIP, 1.5K records; [T03249] Jill Smith — active (+15 more) |
| MO_PHARMACY | MO | csv_bulk | PASS | session 36 — mopro_zip strategy; 10 ZIPs, 39K records; [2024026846] Tinley Smith — active (+334 more) |
| MS_DHPL | MS | classic_html_form | PASS | session 39 — ASP.NET WebForms GridView; msdhpl.webapps.ms.gov; [TA-4248] ABAGAIL GRAYCE SMITH — active (+49 more); requires PROXY=proxy:9119 |
| MS_PSYCH | MS | classic_html_form | PASS | session 39 — Classic ASP table; msbop.ms.gov; [34 559] Smith (+7 more); requires PROXY=proxy:9119 |
| MS_SWMFT | MS | classic_html_form | PASS | session 39 — ASP.NET WebForms GridView; swmft.webapps.ms.gov; [Licensed Social Worker] Amelita R Smith (+49 more); requires PROXY=proxy:9119 |
| MS_LPC | MS | classic_html_form | PASS | session 40 — Classic ASP; lpc.ms.gov/secure/licensesearch.asp; POST to licensesearchresults.asp; row_selector tr:has(a[href*='licensesearchdetails']); no_results_indicator '0 matching documents'; [3244] Allison Kenna Smith (+52 more); requires PROXY=proxy:9119 |
| MS_ABA | MS | classic_html_form | PASS | session 41 — Classic ASP; msautismboard.ms.gov/secure/licenseverification.asp; nested table structure; row_selector tr:has(a[href*='licenseverificationdetails']):not(:has(tr)); no status column (all active); [200017] Chelsea Lynn Smith (+2 more); requires PROXY=proxy:9119 |
| WV_PSYCH | WV | classic_html_form | PASS | session 44 — custom_js extraction; psychbd.wv.gov; all results flat in div.psychbdSearchContainer; bare `<b>Name</b>` + `<b>Label:</b>` text nodes; [785] Tracy P. Smith — active (+17 more); requires PROXY=proxy:9119 |
| AL_ABESPA | AL | classic_html_form | PASS | session 44 — proxy: false (site works directly, not Zscaler-blocked); ASP.NET form; #ContentPlaceHolder1_gv1; [3171] Smith, Abby Lauren — active (+16 more) |
| AL_MFT | AL | classic_html_form | PASS | session 42 — ASP.NET form at mft.alabama.gov; results in #ContentPlaceHolder1_GridView1; [259] Smith, Charles Manuel — unknown (+7 more); requires PROXY=proxy:9119 |
| AL_OPTOMETRY | AL | classic_html_form | PASS | session 42 — ASP.NET GET form at optometry.alabama.gov; #GridView1 cols: Last Name/First Name/City/State/License#; [S-275] Howard Smith — unknown (+10 more); requires PROXY=proxy:9119 |
| MA_BSAS | MA | classic_html_form | PASS | session 42 — MA Bureau of Substance Addiction Services eLicensing at hhsvgapps03.hhs.state.ma.us; input#lastName + input[value='Search']; 22 Smith records; [11100] Krista Sand — unknown (+21 more) |
| MO_CHIROPRACTIC | MO | csv_bulk | PASS | session 42 — mopro_zip strategy; board_label 'Chiropractic Examiners'; 1 ZIP, 2883 records; [2023046993] Delia Smith — active (+29 more) |
| MO_PSYCHOLOGISTS | MO | csv_bulk | PASS | session 42 — mopro_zip strategy; board_label 'Psychologists'; 2 ZIPs, 1813 records; [01487] Lizette Smith — active (+15 more) |
| NH_OPLC | NH | classic_html_form | PASS | session 42 — ASP.NET form at forms.nh.gov/licenseverification; #ctl00_Main_txtName + #ctl00_Main_btnSearch; #ctl00_Main_gvLicensees results; 41 Smith records; [002720-21] Smith, A. Jean — inactive (+40 more); NO proxy (Akamai WAF blocks proxy IP) |
| NM_MIDWIVES | NM | classic_html_form | PASS | session 42 — NM Midwife Registry at license.nmmidwife.doh.nm.gov; input#searchByName + Enter; table rows; [928] Smith, Szodyraa — active |
| OK_ADAC | OK | classic_html_form | PASS | session 44 — custom_js extraction; okdrugcounselors.org; each provider in `<table border="1" width="398">`; row 0 = name `<b>`, rows 1+ = `<b>Label:</b>` value pairs; [288] Flucard-Smith, Vicki — inactive (+9 more); requires PROXY=proxy:9119 |
| AZ_CHIRO | AZ | classic_html_form | PASS | session 43 — onGovCore React SPA (azus-sbce.ongovcore.com); same platform as AZ_OT/AZ_OPTOMETRY/AZ_DENTAL; [006086] Damian Smith — unknown (+19 more); requires PROXY=proxy:9119 |
| OK_BEHAVIORAL_HEALTH | OK | thentia_cloud | **SKIP** | thentiacloud_api_blocked_corporate: obbhl.us.thentiacloud.net page loads without proxy but Thentia search XHR API times out from corporate network (both via proxy and direct). Column mapping corrected (0=last_name/1=first_name/2=city/3=state/4=zip/5=license_type/6=status/7=supervisor/8=disciplinary). proxy: false; strategy: select; timeouts: 90s/60s. Confirmed 2026-06-19. |
| OK_SOCIALWORK | OK | thentia_cloud | **SKIP** | thentiacloud_api_blocked_corporate: osblsw.portalus.thentiacloud.net page loads without proxy but Thentia search XHR API times out from corporate network. Column mapping corrected (0=license_number/1=first_name/2=last_name/3=city/4=license_type/5=status/6=expiration_date/7=disciplinary). proxy: false; strategy: none. Confirmed 2026-06-19. |
| OK_ODOHCS | OK | thentia_cloud | **SKIP** | thentiacloud_api_blocked_corporate: odohcs.portalus.thentiacloud.net page loads without proxy but Thentia search XHR API times out from corporate network. Column mapping corrected (0=license_number/1=first_name/2=last_name/3=city/4=license_type/5=status/6=expiration_date/7=disciplinary). proxy: false; results_wait timeout 60s. Confirmed 2026-06-19. |
| VT_OPR | VT | classic_html_form | **SKIP** | session 43 — Pega Constellation React SPA at secure.professionals.vermont.gov; requires press_sequentially, React event dispatch, 90s boot wait, CSS force-enable for submit button; no engine archetype supports Pega |

**Summary: 178 PASS / 0 FAIL / 10 SKIP** *(current as of 2026-06-22; 188 total configs)*

SKIP(10): WV_CHIRO / NC_OPTOMETRY / NC_PT / NY_CREDENTIALS / MO_NURSING / AZ_MEDBOARD / OK_BEHAVIORAL_HEALTH / OK_SOCIALWORK / AK_CBP / OK_ODOHCS

---

Session 45–46 (2026-06-22) — **Evidence capture overhaul; RI_HEALTH FAIL fix; screenshot/HTML audit; PSV_DEV cleanup; 178 PASS / 0 FAIL / 10 SKIP**:

**RI_HEALTH FAIL fix:**
- `results_wait.strategy: element_visible, timeout_ms: 30000` → `strategy: network_idle, timeout_ms: 60000`. Under full-run load, `healthri.mylicense.com` (myLicense.com platform) takes >30s to respond — the prior fix that made `wait_for_results` correctly return `False` on timeout triggered this as a FAIL. Changed to `network_idle` with 60s (same as IN_PLA, same platform). PASS in 13.8s.

**Evidence path restructured:**
- Old: `Evidence/{source_id}/{run_id}/search_results.png`
- New: `Evidence/{YYYY-MM}/{state}/{source_id}/{YYYYMMDD_HHMM}_{query_label}/{stage}.{ext}`
- `{query_label}` = `license_number` → `first_name` → `last_name` → raw query (filesystem-safe, 40-char max)
- Month and timestamp extracted from `run_id` (format `20260622_091856_001`), falls back to `datetime.now()`
- Updated `engine/evidence.py`: `resolve_evidence_path(source_id, run_id, state, query_label)` + `_query_label(query)` helper
- Updated all archetype callers to pass `state=config.identity.state, query=query`

**Screenshot + HTML audit — all 147 browser boards now fully configured:**
- Added `capture_evidence` calls to `certemy.py`, `datatables.py`, `filemaker.py` (previously never called it)
- Added `capture_evidence` calls to `socrata.py` (`socrata_bulk_csv` uses Playwright but was missing evidence calls)
- Fixed 8 browser board configs: `capture_html: false → true` (AZ_SPEECH_HEAR, KS_NURSING, NC_DIETETICS, NC_MENTAL_HEALTH, OK_DENTAL, TX_CHIRO, TX_DENTAL, WV_SOCIALWORK)
- Fixed KS_NURSING `capture_on: [] → ["search_results", "error"]`
- All 147 browser boards now have: `capture_html: true`, `capture_screenshot: true`, `capture_on: ["search_results", "error"]` (or `["search_results", "detail_page", "error"]` for boards with detail pages)
- 43 non-browser boards (csv_bulk / pdf_bulk / json_api / socrata_api) correctly have no screenshot config (no Playwright page)

**PSV_DEV folder cleanup (~1.07 GB freed):**
- Removed `csvs/` root directory (822 MB stale CSV cache — now maintained in `PSV/CSVS/`)
- Removed `Edgedriver/` (20 MB Selenium Edge driver — replaced by Playwright)
- Removed stale standalone scripts, historical run outputs, and intermediate files from project root and subdirectories

Session 44 (2026-06-19) — **3 SKIP→PASS + engine custom_js feature + csv cache_dir fix; 175 PASS / 0 FAIL / 13 SKIP**:

**SKIP→PASS (3):**
- **AL_ABESPA** — `abespa.alabama.gov` is NOT Zscaler-blocked — was a proxy misconfiguration. Switched `proxy: enabled: false`; ASP.NET form works directly. [3171] Smith, Abby Lauren — active (+16 more).
- **WV_PSYCH** — West Virginia Board of Examiners of Psychologists. All results rendered flat inside `div.psychbdSearchContainer > div` with bare `<b>Name</b>` nodes + `<b>Label:</b> Value` text-node siblings. Added `custom_js` engine feature to walk `childNodes`, detect colon presence to distinguish name vs. field, and build records. `results_wait.selector` changed to `div.psychbdSearchContainer b` (the `#psychbdSearchResultsPane` div was always present before search — not a valid sentinel). [785] Tracy P. Smith — active (+17 more).
- **OK_ADAC** — `okdrugcounselors.org` PHP POST form. Each provider rendered in a `<table border="1" width="398">`; row 0 = name `<b>`, subsequent rows = `<b>Label:</b> value` pairs. `vertical_kv` with `record_marker_label` couldn't capture the pre-colon bare-bold name. Fixed via `custom_js` iterating each provider table. [288] Flucard-Smith, Vicki — inactive (+9 more). Requires PROXY=proxy:9119.

**Engine change — `custom_js` in `ResultsTableConfig`** (`engine/models.py` + `engine/extractor.py`):
- New `custom_js: Optional[str]` field on `ResultsTableConfig`. When set, `page.evaluate(custom_js)` is called and results are returned directly, bypassing all other table/vertical_kv extraction. JS must return `[{field: value, ...}]`. Used by WV_PSYCH and OK_ADAC.

**Skip reason updates (3 Thentia boards — still SKIP, improved configs):**
- **OK_BEHAVIORAL_HEALTH** — `obbhl.us.thentiacloud.net` page loads without proxy (proxy: false now); Thentia search XHR API calls time out from corporate network regardless. Column mapping corrected; strategy: select; timeouts increased to 90s/60s.
- **OK_SOCIALWORK** — `osblsw.portalus.thentiacloud.net` same XHR-blocked pattern. Proxy: false; strategy: none (custom_dropdown broke Angular page state). Column mapping corrected.
- **OK_ODOHCS** — `odohcs.portalus.thentiacloud.net` same pattern. Proxy: false; results_wait timeout increased to 60s. Column mapping corrected.

**New SKIP(13):** WV_CHIRO / NC_OPTOMETRY / NC_PT / NY_CREDENTIALS / MO_NURSING / AZ_MEDBOARD / NM_MEDBOARD / NM_RLD / OK_BEHAVIORAL_HEALTH / OK_SOCIALWORK / AK_CBP / OK_ODOHCS / VT_OPR.

**Engine fix — `csv_extractor.py` cache_dir resolution** (`engine/csv_extractor.py`):
- `get_csv()` now resolves relative `cache_dir` values (e.g. `./csvs`) against `Path(__file__).parent.parent` (the scrapers root), not the process CWD. Prevents WY google_sheet_link boards from failing when `smoke_all.py` is run from the project root instead of the scrapers directory.
- WY boards' `google_sheet_link` CSV downloads are also blocked by McAfee Web Gateway (`URLBlockedStorage`) on the corporate network. The 7-day cache in `lvs/adapters/scrapers/csvs/` is used automatically; cached files expire 2026-06-23 (next refresh needs non-corporate network or McAfee allowlist update for `docs.google.com/spreadsheets/export`).

Session 43 (2026-06-18) — **3 new boards added from PSV_DEV scripts; 172 PASS / 0 FAIL / 16 SKIP**:

**New PASS boards (1):** AZ_CHIRO.

**New SKIP boards (2):**
- **OK_ODOHCS** — network_timeout: `odohcs.portalus.thentiacloud.net` hung >180s via corporate proxy — same pattern as OK_SOCIALWORK/OK_BEHAVIORAL_HEALTH. Thentia portalus board. Test outside corporate proxy to confirm.
- **VT_OPR** — Pega Constellation React SPA at `secure.professionals.vermont.gov`. Requires `press_sequentially`, React native-setter event dispatch, 90s boot wait, and CSS force-enable for DISPLAY RESULTS button. No engine archetype currently supports Pega Constellation.

**New boards detail:**
- **AZ_CHIRO** — Arizona State Board of Chiropractic Examiners. onGovCore React SPA at `azus-sbce.ongovcore.com`. Identical platform to AZ_OT/AZ_OPTOMETRY/AZ_DENTAL. Columns: NAME/LICENSE NUMBER/LICENSE TYPE/STATUS/EXPIRATION DATE/BOARD ACTIONS. Requires PROXY=proxy:9119.
- **OK_ODOHCS** — Oklahoma State Dept. of Health Consumer Health Service. Thentia portalus at `odohcs.portalus.thentiacloud.net`. Keyword-only search (no Search By dropdown, unlike OK_SOCIALWORK). Detail page via `a.btn-single`. Requires PROXY=proxy:9119.
- **VT_OPR** — Vermont Office of Professional Regulation. Multi-profession portal (LPC, SW, PSYCH, MFT, DC, OD, PT, OT, RN, RPh, DENT, and more). Distinct from VT_MEDBOARD (`search.medicallicensing.vermont.gov`).

Session 42 (2026-06-18) — **11 new boards added; 171 PASS / 0 FAIL / 14 SKIP**:

**New PASS boards (9):** AL_MFT, AL_OPTOMETRY, AZ_OT (session 38, carried), AZ_PODIATRY (session 38, carried), HI_DIETITIANS (session 34, carried), MA_BSAS, MO_CHIROPRACTIC, MO_PSYCHOLOGISTS, NH_OPLC, NM_MIDWIVES. Plus MS_ABA/MS_DHPL/MS_LPC/MS_PSYCH/MS_SWMFT already counted in sessions 39–41.

**New SKIP boards (2):**
- **AL_ABESPA** — network_timeout: `abespa.alabama.gov` times out (30s) via corporate proxy. Site is Zscaler-blocked or intermittently slow. ASP.NET form; results in `#ContentPlaceHolder1_gv1`; cols: Name/License#/Status. Unblock: test directly or add to allowlist.
- **OK_ADAC** — vertical_kv nested-table layout at `okdrugcounselors.org`. PHP POST form confirmed working (member results appear). Requires custom parser for nested-table record extraction. Requires PROXY=proxy:9119.

**New boards detail:**
- **AL_MFT** — ASP.NET ImageButton form at `mft.alabama.gov`; `#ContentPlaceHolder1_GridView1`; cols: Name/License#/Status. Requires PROXY=proxy:9119.
- **AL_OPTOMETRY** — ASP.NET GET form at `optometry.alabama.gov`; `#GridView1`; cols: Last/First/City/State/License#. Requires PROXY=proxy:9119.
- **MA_BSAS** — MA Bureau of Substance Addiction Services eLicensing at `hhsvgapps03.hhs.state.ma.us`; `input#lastName` + `input[value='Search']`; 22 Smith records. No proxy.
- **MO_CHIROPRACTIC** — mopro_zip; `board_label: 'Chiropractic Examiners'`; 1 ZIP, 2883 records.
- **MO_PSYCHOLOGISTS** — mopro_zip; `board_label: 'Psychologists'`; 2 ZIPs, 1813 records.
- **NH_OPLC** — ASP.NET form at `forms.nh.gov/licenseverification`; `#ctl00_Main_gvLicensees`; 41 Smith records. NO proxy (Akamai WAF blocks proxy IP).
- **NM_MIDWIVES** — NM Midwife Registry at `license.nmmidwife.doh.nm.gov`; `input#searchByName` + Enter; small board. No proxy.

Session 37 (2026-06-16) — **WV_DENTAL added; 153 PASS / 0 FAIL / 6 SKIP**:

**New board — WV_DENTAL** (West Virginia Board of Dental Examiners):
- Platform: GLSuite ASP.NET WebForms at `wvbodv7prod.glsuite.us` (same platform as MN_COSMETOLOGY, MN_DENTISTRY, WY_PA, WY_PHYSICIAN).
- URL: `https://wvbodv7prod.glsuite.us/GLSuiteWeb/Clients/WVBOD/Public/Verification/Search.aspx`
- Selectors: `#ContentPlaceHolder1_tbLast` / `#ContentPlaceHolder1_tbFirst` / `#ContentPlaceHolder1_tbNumber` + `#ContentPlaceHolder1_btnSearch`.
- Results: flat table (no detail page). Columns: Licensee Name (Last, First) | Degree | Graduation Year | School | License Number | License Date | Specialty | Anesthesia | Expiration Date | Action Indicator.
- No explicit Status column — `expiration_date` used to infer active/expired. `disciplinary_actions` from Action Indicator (Yes/No).
- Smoke: `last_name/Smith → [1918] Smith, Terri Lynn — unknown (+32 more)` PASS (13s, proxy).
- Note: the Excel inventory listed this board as "Manual" ingestion; overridden in `_EXTRA_BOARDS` since the public form is scrapable.

Session 36 (2026-06-16) — **`mopro_zip` strategy implemented; 4 MO boards SKIP→PASS; MO_NURSING permanently SKIP**:

**Engine change — `mopro_zip` csv_bulk download strategy** in `csv_extractor.py`:
- New `_download_mopro_zip(board_label, ...)` function: navigates `mopro.mo.gov/license/s/license-downloads` (Salesforce LWC portal), selects the board from the Lightning combobox via a two-strategy selector (`select_option` → Lightning combobox click), clicks Submit, downloads each ZIP, extracts the tab-delimited TXT, skips empty 0-byte members, merges all DataFrames, and returns UTF-8 tab-delimited text.
- New fields in `CsvBulkConfig`: `board_label`, `file_format`, `separator` — enable per-board portal name and separator config.
- `load_csv` extended with `sep` parameter; `_scrape_csv_bulk` in `run.py` passes `csv_cfg.separator`.
- Cache saved as UTF-8 for `mopro_zip` (portals TXT files can contain Unicode e.g. right single-quote `’`).
- Screenshot-on-timeout diagnostic in the Download button wait — saves `_mopro_debug_{board}.png` to scrapers root.

**SKIP→PASS (4):**
- **MO_HEALING_ARTS** — 36 ZIPs, 134K records (38 license types including osteopathy, acupuncture, PT, OT, massage, etc.). [2012030851] Stephen Smith — active (+809 more).
- **MO_DENTAL** — 19 ZIPs, 18K records (DDS, DMD, hygienists, assistants). [003366] Kathleen Lucente-Smith — active (+173 more).
- **MO_OPTOMETRY** — 1 ZIP, 1.5K ODs. [T03249] Jill Smith — active (+15 more).
- **MO_PHARMACY** — 10 ZIPs, 39K records (pharmacists, technicians, interns). [2024026846] Tinley Smith — active (+334 more).

**Permanently SKIP (1):**
- **MO_NURSING** — Screenshot confirmed: MOPRO portal displays "Downloadable files for nursing are not available." Portal redirects to Nursys.com (NCSBN QuickConfirm). No bulk file exists. Cannot unblock without a separate Nursys-based source.

**Still SKIP (6):** WV_CHIRO, MN_MEDPRACTICE, NC_OPTOMETRY, NC_PT, NY_CREDENTIALS, MO_NURSING.

Session 35 (2026-06-16) — **SKIP triage: 2 SKIP→PASS, NY_CREDENTIALS skip-reason updated; engine multi-PDF page_link**:

**SKIP→PASS recoveries (2):**
- **KS_PHARMACY** — public form was always reachable; the prior "login_required" diagnosis was incorrect (the page renders chrome that LOOKS like a portal but the public form is right there). ASP.NET GridView `#gvResults` with 7 cols (Name/AKA/L/P/R #/City/State/Class/Status). Tightened `row_selector: "#gvResults tr"` + `skip_first_row: true`, `results_wait.strategy: element_visible` on `#gvResults tr`, `has_detail_page: false` (501 records on Smith would time out). Smoke: `last_name/Baker → [1-126156] Abu Baker, Abdalla — active (+129 more)` PASS.
- **LA_MASSAGETHERAPY** — labmt.org/search/ now exposes anchors `"PROFESSIONAL LICENSE SEARCH"` (prof PDF) and `"ESTABLISHMENT LICENSE SEARCH"` (estab PDF). Switched from hard-coded `direct_url` URLs (which went stale weekly) to new `page_link` strategy with per-PDF `link_selector`. URLs auto-discover at runtime — config no longer goes stale. Smoke: `last_name/Smith → [LA9826] ALEXIS SMITH — active (+30 more)` PASS.

**Engine enhancement: multi-PDF `page_link` strategy** in `run.py` `_scrape_pdf_bulk`:
- `PdfEntry.link_selector: Optional[str]` added to `models.py` — per-PDF anchor selector for `page_link` discovery.
- `PdfBulkConfig.base_url: Optional[str]` added — overrides `identity.base_url` for discovery (LA_MASSAGETHERAPY uses `labmt.org/search/` for discovery while `identity.base_url` stays at `labmt.org`).
- When `download_strategy: page_link` AND `pdfs` list is non-empty, run.py loops each entry, calls `discover_pdf_url(discovery_url, entry.link_selector or pdf_cfg.link_selector)`, preserving `format` + `license_prefix` routing for multi-PDF boards. Single-PDF boards (SD_*) continue to work via the empty-list fallback path.

**NY_CREDENTIALS** — skip reason updated. eservices.nysed.gov/professions/verification-search IS reachable with browser user-agent (was 403 in session 30); page has 2 unlabeled `input[placeholder='Select option']` typeahead widgets (Profession + Search Type) + `#goButton`. Custom widgets have no id/name; classic_html_form selectors can't fill them. Future unblock requires investigating the typeahead widget framework (likely Drupal Views + ng-select or react-select).

**Confirmed still SKIP:**
- **NC_PT** — Cloudflare WAF firmly blocks the proxy IP range (`Attention Required! | Cloudflare`, "Sorry, you have been blocked"). No bypass available.
- **MN_MEDPRACTICE** — site reachable, returns 200 OK, but Angular FormControl ignores Playwright fill (search returns unfiltered top-200 records — `Lia Filice Alvarez` instead of `Smith`). Engine gap: needs zone-aware FormControl.setValue() + ShieldSquare bypass.
- **NC_OPTOMETRY**, **WV_CHIRO** — same architectural blockers (cross-origin iframe, SPO auth).
- **MO_HEALING_ARTS / MO_NURSING / MO_DENTAL / MO_OPTOMETRY / MO_PHARMACY** — `mopro_zip` strategy not yet implemented in csv_extractor.

**Still SKIP (10):** WV_CHIRO, MN_MEDPRACTICE, NC_OPTOMETRY, NC_PT, NY_CREDENTIALS, MO_HEALING_ARTS, MO_NURSING, MO_DENTAL, MO_OPTOMETRY, MO_PHARMACY. *(Note: 4 of these were resolved in session 36.)*

Session 34 (2026-06-16) — **2 new boards added (HI_DIETITIANS, PA_PALS) — first HI and PA boards in inventory**:

**New boards (2):**
- **HI_DIETITIANS** — Hawaii Department of Health Licensed Dietitians (LD, RD). TablePress 7 with DataTables 2.x global search at health.hawaii.gov; full roster client-side; jQuery DataTables `api.search(q).draw()` filters rows. `datatables_jsapi` archetype, `column_index: -1` for global filter, `settle_ms: 1500`. Columns: License Number (e.g. `2-LD`) | Name | Effective Date | Expiration Date. No proxy required. Smoke: `last_name/Smith → [75-LD] Daryl Smith-Oswald (+2 more)` PASS in 8.0s.
- **PA_PALS** — Pennsylvania Licensing System umbrella portal (covers ALL 29 PA boards/commissions: Medicine, Osteopathic, Nursing, Dental, Pharmacy, Optometry, Chiropractic, PT, OT, Psych, Social Work, SLP/AUD, Counselors, Massage, Veterinary, Funeral, Cosmetology, Auctioneers, etc.). AngularJS SPA at pals.pa.gov; form fields `#LicenseNo / #lName / #fName / #mName`; submit `button.btn-primary:has-text('Search')`. Results in `#DataTables_Table_3` (7 cols: full_name/license_number/board/license_type/status/address/action — last skipped). Pagination `#DataTables_Table_3_next`. `classic_html_form` archetype with `ajax_row_count` results_wait (form chrome contains generic "no results" text — rely on row-count signal). No proxy required. Smoke: `last_name/Smith → [AA002213L] LEE A SMITH — active (+9 more)` PASS in 12.7s.

Source: standalone scripts `HawaiDietitians.py`, `PensylvaniaAllProvider.py`. Both files preserved in repo root for reference. No engine changes required — both boards fit existing archetypes (`datatables_jsapi` from session 26, `classic_html_form` with `ajax_row_count` from session 26).

Session 33 (2026-06-16) — **5 new SD boards added (SD_AUDIOLOGY, SD_PT, SD_PODIATRY, SD_PSYCH, SD_SPEECH); new pdf_bulk page_link strategy; pdf_bulk capability fix**:

**New boards (5):** All use new `pdf_bulk / page_link` strategy. `discover_pdf_url()` navigates to `base_url`, finds `a[href*='pdf']`, downloads at runtime. SD_PODIATRY uses query `Johnson` (only 66 DPMs in SD; no Smith). SD_PSYCH uses `/dss/` subdomain (intermittently slow; retry on 60s timeout).

**Engine changes:** (a) `pdf_bulk.download_strategy: page_link` added to `PdfBulkConfig` (and `PdfEntry.url` made Optional). (b) `discover_pdf_url()` added to `pdf_extractor.py`. (c) `_scrape_pdf_bulk` in `run.py` dispatches to `discover_pdf_url` when strategy is `page_link`. (d) **pdf_bulk capability fix in `_auto_derive_capabilities`** — same pattern as certemy fix from session 32: pdf_bulk modes have no `input_selector`/`dropdown_value`, so caps was `{}`. (e) PDF filename fix: URLs like `roster.pdf?tim=123` were extracting `document` as stem (query string broke `.endswith('.pdf')` check). Now strips `?` and `#` fragments before filename extraction. (f) Per-board cache dirs `cache_dir: "./pdfs/{source_id}"` prevent cross-board cache collisions when all boards serve `roster.pdf`.

Session 32 (2026-06-16) — **3 new WV boards added (WV_MEDBOARD_MD, WV_MEDBOARD_PA, WV_MEDBOARD_DPM)**:

**New boards (3):**
- **WV_MEDBOARD_MD** — csv_bulk `link_text_xlsx`; wvbom.wv.gov "Roster of Medical Doctors" XLSX (6 sheets).
- **WV_MEDBOARD_PA** — csv_bulk `link_text_xlsx`; wvbom.wv.gov "Roster of Physician Assistants" XLSX (3 sheets).
- **WV_MEDBOARD_DPM** — csv_bulk `link_text_xlsx`; wvbom.wv.gov "Roster of Podiatric Physicians" XLSX (1 sheet).

All three use the same base_url; engine differentiates by `csv_bulk.link_text`. XLSX structure: row 0 = section title,
row 1 = column headers, `xlsx_header_row: 1`. Evidence paths updated to `{month}/{source_id}/{run_id}/` on all 4
existing WV configs (WV_CHIRO, WV_PT, WV_SOCIALWORK, WV_OPTOMETRY). Source: standalone scripts
`westvirginia_MD_csv.py`, `westvirginia_PA_csv.py`, `westvirginia_DPM_csv.py`.

Session 30 (2026-06-16) — **7 FAIL → resolved; ID_DOPL SKIP lifted; 4 FAIL fixed, 3 FAIL→SKIP**:

**Fixed (4 FAIL → PASS):**
1. **AK_CBP** — cache stale eviction (8 days > 7-day limit) caused fresh-download failure (JS-heavy page returned empty body). Fix: `cache_days: 7 → 30`; the weekly CSV in `csvs/AK_CBP_20260608.csv` survives eviction windows.
2. **TX_OPTOMETRY** — selectors were wrong (old HPC datamart field names `last_name`/`lic_nbr_old` etc.); actual fields are `last_nme`/`lic_nbr`/`first_nme`, submit is `input#btnSubmit`, results are `tr.jqgrow` rows. Config completely rewritten against live DOM. **[2259] SMITH, ALLISON K. — expired (+19 more).**
3. **NC_SLP_AUD** — `no_results_indicators: ["0 Records Found"]` was a substring of "**0 Records Found**" inside "9**0 Records Found**" → false-positive. Fix: empty the list, rely on row count. **[30004433] Abigail Joy Smith — inactive (+89 more).**
4. **ID_DOPL** — Angular SPA: (a) must navigate home `_/` first, then `pre_search_click` the "Search for Individual Licenses" tile → Angular router loads form at `#2`; (b) `fill()` doesn't update Angular FormControl — added `use_keyboard_type: true` to `SearchForm` so engine uses `keyboard.type()` instead; (c) results appear after Angular router navigates to `#3` (~12s) — wait for `td.TDS` sentinel; (d) result rows = `tr:has(td.TDS)`, cols 0/1/2/4/5 = license_number/type/full_name/status/date. **[055228] JONATHAN LOCK-SMITH — expired (+24 more).**

**New SKIP (3 FAIL → SKIP):**
- **AR_PODIATRY** — `pdf_url_stale_403`: `Podiatry_LicenseVerification_20260522.pdf` returns HTTP 403. Unblock: find current PDF at https://healthy.arkansas.gov/boards-commissions/boards/podiatric-medicine-board/
- **LA_MASSAGETHERAPY** — `pdf_urls_stale_404`: both May 2026 labmt.org PDFs return 404; `online.labmt.org` requires login; `labmt.org/search/` is WordPress site-search not license lookup. Unblock: obtain new static PDF URLs from LABMT.
- **NY_CREDENTIALS** — `wrong_url_no_public_search_form`: `op.nysed.gov/verification-search` redirects to `eservices.nysed.gov/professions/verification-search` (Online Registration Renewal portal, login required). Unblock: identify correct public free-text lookup URL; Socrata `data.ny.gov` per-profession datasets are a likely path.

**Engine changes:**
- `SearchForm.use_keyboard_type: bool = False` — when `true`, engine uses `keyboard.type()` with per-character key events instead of `fill()`, unblocking Angular reactive forms that ignore Playwright's programmatic fill. Config: `form.use_keyboard_type: true`.

**Still SKIP (8):** WV_CHIRO (SPO auth), KS_PHARMACY (login), MN_MEDPRACTICE (Angular fill+zone), NC_PT (Cloudflare), NC_OPTOMETRY (cross-origin iframe), AR_PODIATRY (PDF 403), LA_MASSAGETHERAPY (PDF 404), NY_CREDENTIALS (wrong URL/renewal portal).

Session 29 (2026-06-16) — **5 SKIP→PASS, 4 new boards, smoke v2 yielded 123 PASS / 7 FAIL / 5 SKIP**:

**SKIP lifts (5):**
1. **LA_MEDBOARD** — ag_grid_spa; `ajax_row_count` wait strategy and config tightened against live DOM.
2. **MN_EMS** — Angular SPA (emslm.mn.gov); direct URL navigation to `#/lookup` + selector refinement.
3. **OR_HLO** — UpdatePanel AJAX issue resolved via `post_search_click` + networkidle wait.
4. **OR_OPTOMETRY** — OGovCore platform config validated against live DOM.
5. **NC_CHIRO** — wpDataTables container row_selector resolved; was matching wrong table.

**New boards (4):**
- **MD_ACUPUNCTURE** — csv_bulk `direct_url` strategy; PASS.
- **TX_CHEMICAL** — csv_bulk link_text_xlsx; PASS.
- **OH_PROVIDERS_BUSINESS** — csv_bulk `ohio_data_portal_csv`; PASS.
- **OH_PROVIDERS_INDIVIDUAL** — csv_bulk `ohio_data_portal_csv`; PASS.

**Re-skipped (2):** MN_MEDPRACTICE (Angular reactive-form zone issue), NC_PT (Cloudflare intermittent — traffic split too high for reliable automation).

Session 28 (2026-06-15) — **Engine archetype gaps audit**: 4 SKIPs lifted (smoke-verified) by closing two genuine engine gaps and tightening configs against actual live DOM evidence. WV_CHIRO confirmed unfixable engine-side without auth.

**Engine changes (additive, low-risk):**
1. **`search.post_search_click_all`** — new SearchConfig field. Like `post_search_click` but iterates every visible match and clicks each, waiting for networkidle after the sweep. Used for accordion-grouped result panels where each section must be expanded to populate its rows.
2. **`csv_bulk.download_strategy: onedrive_excel`** — new download strategy in `engine/csv_extractor.py`. Locates a OneDrive iframe (1drv.ms / onedrive.live.com / office.com), strips query overrides to get the canonical share URL, base64-encodes it as `u!<b64>` and tries the Microsoft Graph public-shares API (`/v1.0/shares/u!<b64>/driveItem/content`) plus `?download=1` fallback via Chromium-`fetch` (works through corporate SSL inspection that ECONNRESETs `APIRequestContext`).

**Smoke-verified PASS (4):**
1. **NC_DENTAL** — root cause of the original "ASP.NET postback didn't transition" skip was a DOM-shape mismatch, not a postback failure: results render as Bootstrap `<div class="search-results">` cards with `<dl class="dl-horizontal">` (zero `<table>` elements). Replaced `table tbody tr` selectors with `div.search-results div.row` + `dd` cells; result_wait now keys off `div.search-results`. **250 Smith records returned.**
2. **NC_PODIATRY** — iMIS WebPartManager UUIDs replaced with attribute-contains selectors (`input[id*='Sheet0_Input0_TextBox']` etc.) that survive the dynamic ID drift. Result extraction uses `table.result` row_selector with `h1` cell extracting the licensee name + DPM suffix. **4 Smith records returned.**
3. **LA_DIETETICS** — accordion-expand engine gap closed via `post_search_click_all`. Config clicks all `.panel-heading a` triggers after the form submit, waits for networkidle, then extracts `li.row` entries from every populated profession panel. **40 Smith records returned.**
4. **LA_SPEECH** — same accordion fix as LA_DIETETICS. **113 Smith records returned.**

**Confirmed unfixable engine-side (1):**
- **WV_CHIRO** — `boc.wv.gov/roster.html` embeds a OneDrive iframe that has been migrated to SharePoint Online (URL contains `migratedtospo=true`). Engine's new `onedrive_excel` strategy reaches the iframe and constructs the Graph API URL correctly, but Graph returns HTTP 401 (auth required for SPO-tenanted shares) and `1drv.ms?download=1` redirects to the same gated SPO page. Unblock requires either a static .xlsx/.csv URL from the board, an auth token, or substantial new engine work to UI-scrape the rendered Excel Online viewer.

**Still SKIP (10):** URL-drift / login-portal needing live discovery (5): **LA_MEDBOARD, MN_EMS, MN_MEDPRACTICE, OR_HLO, OR_OPTOMETRY**. Anti-bot (2): **NC_PT** (Cloudflare), **KS_PHARMACY** (staff-only login). External data-access blockers (2): **NC_OPTOMETRY** (cross-origin Google Drive atari iframe — content_frame() blocked by same-origin policy), **WV_CHIRO** (SPO-migrated share requires auth — see above). Non-public (1): **NC_CHIRO** (paid request form only).

Session 27 attempt (2026-06-15) — **Network + URL re-audit**: 4 SKIPs lifted after DNS re-check + URL hint review. Configs validated through Pydantic loader; live PASS confirmation pending corporate-proxy smoke run.

1. **TX_OPTOMETRY** — switched from `vo.licensing.hpc.texas.gov` (reCAPTCHA-blocked HPC datamart) to `https://tob.texas.gov/licensesearch/index.php` (first-party TOB PHP search, no captcha). Config rewritten blind with fanned-out OR-selectors for `last_name|ln|last`, `first_name|fn|first`, `license_number|lic_no|license` field-name conventions; tighten after first headed-mode run.
2. **ID_DOPL** — DNS for `dopl.idaho.gov` now resolves (164.165.66.150); previous McAfee Web Gateway block has been lifted. Skip lifted; existing thentia_cloud config retained.
3. **LA_MASSAGETHERAPY** — Stale skip_reason cited `lmtb.la.gov` (DNS-blocked), but the actual config uses `labmt.org` (resolves) via `pdf_bulk` archetype. Skip lifted; switched smoke query from placeholder "TBD" to "Smith".
4. **NY_CREDENTIALS** — switched from non-existent multi-profession Socrata dataset to `https://www.op.nysed.gov/verification-search` as `classic_html_form` (NYSED Office of the Professions canonical multi-profession lookup). Selectors fanned out as ORs; needs first-run verification.

Session 26 (2026-06-15) — **Engine archetype gaps + result-rendering gaps closed**: 8 boards SKIP→PASS by extending the engine with three new archetypes plus two new result-extraction strategies and a multi-iteration form loop:

1. **`json_api` archetype** (intercept mode) — drives the public form so the SPA fires its own XHR; engine intercepts and parses the matching response. Fixes corporate-proxy CORS by avoiding direct API calls. Used for **MA_MDDO** (`api.medboard.mass.gov/api-public/search` → `results.data` → 504 records).
2. **`datatables_jsapi` archetype** — drives DataTables JS API (`jQuery(sel).DataTable().column(N).search(q).draw()`) and supports multi-URL iteration. Waits for the unfiltered table to populate before applying search. Fixes **OK_DENTAL** (7 sub-pages, 55 records) and **TX_DENTAL** (DataTables 2.x, 15 records).
3. **`filemaker_webdirect` archetype** — Vaadin 8 form driver (.fm-textarea click→type, 30s boot wait, lazy-cell wait + JS-based row read). Fixes **TX_CHIRO** (23 records).
4. **`multi_iteration` config** — loops over a list of values for any field (select option / input / URL placeholder) and merges results across iterations. Fixes **AZ_SPEECH_HEAR** (9 provider-type codes, 43 records).
5. **`vertical_kv` extractor** — walks `<dt>`/`<label>` nodes inside a container and starts a new record at every `record_marker_label`. Fixes **NC_DIETETICS** (108 records, no `<table>`).
6. **`ajax_row_count` results-wait strategy** — polls a row selector until count >= min_rows and stable for N ticks. Fixes **NC_MENTAL_HEALTH** (`#btnAJAX` + `#MultiResultsList` AJAX, 101 records) and **WV_SOCIALWORK** (DNN SQLViewPro, 26 records). Same wait can replace the LA_MEDBOARD `api_response_wait` gap.
7. **`results.table.iframe_selector` + `vertical_kv.iframe`** support — extractor can drop into an iframe for table or vertical-kv extraction.

**Recovered to PASS (8):** MA_MDDO, OK_DENTAL, TX_DENTAL, TX_CHIRO, AZ_SPEECH_HEAR, NC_DIETETICS, NC_MENTAL_HEALTH, WV_SOCIALWORK.

**Remaining SKIPs (18):** Login portals (KS_PHARMACY, LA_MEDBOARD, MN_EMS, MN_MEDPRACTICE, OR_OPTOMETRY), DNS-blocked (ID_DOPL, LA_MASSAGETHERAPY), slow+lazy (LA_DIETETICS, LA_SPEECH), non-public (NC_CHIRO), iMIS sessionful postback (NC_PODIATRY), Cloudflare challenge (NC_PT), captcha (TX_OPTOMETRY), URL drift (OR_HLO), opaque embeds (NC_OPTOMETRY Google Drive, WV_CHIRO OneDrive), unknown dataset (NY_CREDENTIALS), ASP.NET postback (NC_DENTAL).

Session 25 (2026-06-15) — **Thentia portalus breakthrough**: 10 boards SKIP→PASS by combining three changes:
1. **Use the generic `/webs/portal/register/` path** instead of the per-board `/webs/{slug}/register/` path. The portalus.thentiacloud.net subdomain returns 403 at the root and on per-board paths, but accepts requests to `/webs/portal/register/` and dispatches to the right board based on host.
2. **Use `results_wait.strategy: element_visible`** with selector `table tbody tr` (not `url_change`). The Angular SPA changes URL via hash routing immediately, but the actual table rows take 4-6s to appear via XHR — `element_visible` waits for at least one row.
3. **Set `has_detail_page: false`** for boards with large result sets — the View-Details click loop times out at 240s for OK_OSTEO/WV_PT when iterating 20+ records.

**Recovered to PASS (10):** AZ_ACUPUNCTURE, AZ_BEHAVIORAL_HEALTH, AZ_NATUROPATHIC, AZ_OSTEO, AZ_PT, AZ_PSYCH, OK_OPTOMETRY, OK_OSTEO, WV_PT, OR_COUNSELORS. **All 20 Thentia boards (NV/OR/AZ/OK/WV) now PASS.**

Column mapping for `*.portalus.thentiacloud.net` boards: 0=license_number, 1=first_name, 2=last_name, 3=city, 4=license_type, 5=status, 6=expiration_date.

**Earlier session 24 summary (still valid):** Added 18 new board configs from PSV_DEV root scrapers (Arizona + North Carolina) — 6 PASS, 12 SKIP at the time.

Session 24 (2026-06-15) added **18 new board configs** from PSV_DEV root scrapers — 6 PASS, 12 SKIP, 0 FAIL.

**New PASS boards (6):**
- **AZ_OPTOMETRY** (azus-sboe.ongovcore.com) — OnGovCore single-input keyword search; `network_idle` wait; col 0/1/3/4/5/6 = full_name/license_number/license_type/status/expiration_date/state_code
- **AZ_DENTAL** (azus-sbde.ongovcore.com) — same OnGovCore pattern as AZ_OPTOMETRY
- **NC_DAC** (ncsappb.learningbuilder.com) — LearningBuilder MVC; `input[name='LastName']` + `input[type='submit'][value='Verify']`
- **NC_OT** (ncbot-online.org) — Telerik RadGrid pagination; `#ctl00_MainContentPlaceHolder_LastRadTextBox` + `#SearchButton`
- **NC_SLP_AUD** (portal.ncboeslpa.org) — ASP.NET Verification/search.aspx → results.aspx; col 0/1/2/3/4 = license_type/full_name/city/license_number/status (note column reorder vs assumed)
- **NC_MASSAGE** (theconjuredsolution.com/aspsearch) — classic ASP search; `table#masterDataTable > tbody > tr`

**New SKIP boards (12) with documented unblock paths:**
- **AZ_ACUPUNCTURE / AZ_BEHAVIORAL_HEALTH / AZ_NATUROPATHIC / AZ_OSTEO / AZ_PT / AZ_PSYCH** (6) — `*.portalus.thentiacloud.net` returns 403 Forbidden via corporate proxy. Same block as OK_OPTOMETRY/OK_OSTEO/WV_PT.
- **AZ_SPEECH_HEAR** — hsapps.azdhs.gov requires `DropDownListPvType` (provider type) to be selected before search yields results; standalone scraper iterates over 7 provider type codes.
- **NC_DENTAL** — portal.ncdentalboard.org search form does not navigate after click; needs explicit `__doPostBack` target with `__EVENTTARGET`.
- **NC_DIETETICS** — gateway.ncbdn.org renders results as vertical Name/Status/License# label-value list, not table; needs `vertical_kv` extractor.
- **NC_MENTAL_HEALTH** — portal.ncblcmhc.org `#btnAJAX` populates `#MultiResultsList` via AJAX; engine `network_idle` fires before AJAX completes; needs `api_response_wait` (same gap as LA_MEDBOARD).
- **NC_OPTOMETRY** — ncoptometry.org Squarespace JS-only form loading; needs longer wait or alternate URL.
- **NC_PT** — www2.ncptboard.org returns Cloudflare JS challenge; needs cf-clearance cookie or bypass strategy.

**Earlier session 23 summary (still valid):**
Session 23 (2026-06-15) — full SKIP/FAIL diligence pass — 9 boards SKIP/FAIL → PASS via config-only fixes (IN_PLA, NJ_DCA, OK_MEDBOARD, AR_MEDBOARD, ME_OPLR, OR_DENTAL, OR_NATUROPATH, OR_PT, OR_SLP).

Session 23 (2026-06-15) — actively re-investigated every SKIP+FAIL by visiting the live URL through proxy, fixed everything that was a config/URL drift, and tightened skip reasons for the rest.

**Recovered SKIP→PASS (7):**
- **OK_MEDBOARD** — `medical_search.php` was 404; correct URL is `okmedicalboard.org/search`
- **AR_MEDBOARD** — landing flow now starts at `armedicalboard.org/`; `pre_search_click: a:has-text('Verify a License')` triggers the postback that exposes the form. Plus row_selector tightened to `#gvVerifyLicenseResultsLookup tr.gridrows`
- **ME_OPLR** — URL drift to `pfr.maine.gov/ALMSOnline/...`; cascading dropdowns side-stepped by `dropdown_value: ALL` for `#scRegulator`. Table is `#gvLicensees`
- **OR_DENTAL** — Verification is at `online.oregondentistry.org/#/verifylicense`; classic form with `name='LastName'` etc. Search/Reset buttons distinguished with `value='Search'`. `has_detail_page: false`
- **OR_NATUROPATH / OR_PT / OR_SLP** — all 3 are keyword-only Thentia boards (no Search By dropdown) — `search_by_dropdown: none`, `search_input: input#keywords`, `submit_via_enter: true`, `results_wait.strategy: url_change`. Old `"0 result"` indicator was false-matching `"1-10 of 20 results"` — replaced with `"showing results 1-0 of 0"`

**Recovered FAIL→PASS (2):**
- **IN_PLA** — Name cell contained a nested `<table>`; `cell_selector: "td"` was capturing nested tds. Fix: `cell_selector: "xpath=./td"` (direct children only). Also re-mapped col[2]=profession_code, col[3]=license_type
- **NJ_DCA** — Same nested-table column-shift problem; same `xpath=./td` fix. Plus `has_detail_page: false` to avoid the 60s `Details.aspx` click timeout

**Updated skip reasons after re-probe (network/URL truth):**
- `network_blocked_dns` (5): OK_OPTOMETRY, OK_OSTEO, WV_PT, ID_DOPL, LA_MASSAGETHERAPY all return `Host Not Resolvable` via corporate McAfee Web Gateway
- `login_portal_landing` (5): KS_PHARMACY, MN_EMS, MN_MEDPRACTICE, LA_MEDBOARD, OR_DENTAL-old-route — landing pages are now staff/applicant logins, not public lookups
- `url_not_found` (1): OR_HLO Elite portal returns 404; board may have migrated
- `not_a_public_lookup` (1): NC_CHIRO only offers a paid License Verification *Request* form (Gravity Forms), no public search
- `imis_aspnet_postback` (1): NC_PODIATRY runs on YourMembership/iMIS WebForms with sessionful __VIEWSTATE / __WPPS / WebPartManager UUIDs that drift between sessions
- `onedrive_iframe_unsupported` (1): WV_CHIRO roster is now a Microsoft OneDrive Excel embedded via iframe (not a PDF)
- `dataset_id_unknown` (1): NY_CREDENTIALS — no canonical Socrata multi-profession dataset; suggest replacing with op.nysed.gov verification-search archetype
- `intermittent`/`site_unreachable_or_slow` (3): OR_COUNSELORS (intermittent 403 via proxy), LA_DIETETICS / LA_SPEECH (35s+ timeout via proxy)

**Slow boards (PASS at 240s, FAIL at 90s):** FL_MQA, MD_DIETETICS, MD_SOCIALWORK — recommend default `--board-timeout=240` for full regression runs.

Session 21 added 23 new boards (20 from PSV_DEV root + 3 from Desktop\Codes) — 2 PASS (OR_OT, OR_PSYCH), 21 SKIP. SKIPs document specific engine gaps (Thentia custom_dropdown, ASP.NET radio postback, Angular reactive forms, FileMaker WebDirect, DataTables JS API, OGovCore tabs, Socrata signed URLs, cascading dropdowns, h2/h3 card lists, wpDataTables container, DNN SQLViewPro iframe, JSON API archetype) — see SKIP table for unblock paths.

---

## SKIP boards — status and unblock path

| Board | State | Archetype | Skip Reason | Unblock Path |
|-------|-------|-----------|-------------|--------------|
| LA_DIETETICS | LA | classic_html_form | `lazy_loaded_accordion` — lbedn.org groups results by profession; panel-body divs are empty until group header is clicked, triggering AJAX load of `li.row` items | Implement accordion-expand strategy: click each profession group header, wait for `li.row` to appear in that group's panel-body, extract; repeat for all groups |
| LA_SPEECH | LA | classic_html_form | `lazy_loaded_accordion` — lbespa.org groups results by profession; same accordion AJAX pattern as LA_DIETETICS | Same fix as LA_DIETETICS: implement accordion-expand strategy |
| OR_HLO | OR | classic_html_form | `network_blocked` — ASP.NET UpdatePanel AJAX POST to elite.hlo.state.or.us returns empty results; `tblRecordFinder` form rows are extracted instead of search results | Investigate UpdatePanel AJAX postback mechanism; try offline with direct browser; button value is "Start Search" (not "Search"); license field is `CPH1_txtsrcLicenseNo` |
| ID_DOPL | ID | thentia_cloud | `partial` — Angular SPA uses dynamic form field discovery (no hardcoded selectors in standalone script); engine config uses best-guess selectors | Run `python run.py --config sites/ID_DOPL/config.yaml --mode last_name --query Smith --headed` to see live DOM; update `search.modes[*].input_selector` and `results.table.row_selector` from actual page structure |
| MN_EMS | MN | classic_html_form | Angular SPA with div-based results and `#/lookup/user/detail/{uuid}` hash routing | Requires new engine archetype or custom SPA support; not solvable with classic_html_form config alone |
| MN_MEDPRACTICE | MN | classic_html_form | Angular SPA with accordion inline expansion | Requires new engine archetype or custom Angular interaction support |
| TX_OPTOMETRY | TX | classic_html_form | `captcha` — Texas HPC datamart at tob.texas.gov requires reCAPTCHA on every search page load | No unblock path without CAPTCHA bypass service; mark permanently SKIP |
| WV_CHIRO | WV | pdf_bulk | `pdf_url_required` — boc.wv.gov/roster.html hosts roster PDF but URL changes; placeholder URL in config | Navigate to boc.wv.gov/roster.html, find current PDF link (`a[href$='.pdf']` or `iframe[src*='.pdf']`), update `pdf_bulk.pdfs[0].url` in [config.yaml](sites/WV_CHIRO/config.yaml); remove `skip: true` |
| AR_MEDBOARD | AR | classic_html_form | `asp_net_radio_postback_required` — radio button click triggers `__doPostBack` which switches form action between `lookup.aspx` (name) and `results.aspx` (license); engine fills input before postback completes | Add `postback_wait` support in `navigator.py` after `pre_input_click`; OR navigate directly to `lookup.aspx` for name search and `results.aspx` for license search |
| LA_MEDBOARD | LA | ag_grid_spa | `angular_spa_api_intercept_required` — Angular SPA loads results via XHR to `/IndividualVerifyLicenseLAMED`; engine's `network_idle` wait fires before API completes, `div.detail-container` never appears | Implement `api_response_wait` strategy that intercepts JSON response (page.expect_response with URL pattern), then click all `input[value='Detail']` buttons concurrently |
| ME_OPLR | ME | classic_html_form | `cascading_dropdown_required` — ALMS portal needs `scDepartment` → `scAgency` → `scRegulator` (mandatory) chain; `#scRegulator` options change after `#scDepartment` changes | Add `pre_form_dropdowns` sequence in `navigator.py` to fill cascading dropdowns; OR pin `scRegulator='MEDICINE'` etc. via `page.evaluate()` before submit |
| NC_CHIRO | NC | classic_html_form | `wrong_table_extracted` — `row_selector: 'table tbody tr'` matches `'Disciplinary Decision History'` header table before search-result table loads | Identify result-table-specific selector via `--headed` (likely `.wpdt-c table` or wpDataTables container); update `results.table.row_selector` |
| NC_PODIATRY | NC | state_portal | `card_list_results_unsupported` — ncbpe.org renders provider results as `h2`/`h3` cards (not a table); engine `results.type='table'` cannot extract from h2/h3 sibling-walk pattern | Implement `card_list` extractor in `extractor.py` that walks h2/h3 headers and groups subsequent label/value siblings until next header |
| NY_CREDENTIALS | NY | csv_bulk | `csv_dataset_url_required` — Socrata uses UI-driven CSV export (`[data-testid='export-download-button']`); signed URLs change per session | Identify canonical SODA endpoint (`health.data.ny.gov/resource/{dataset_id}.csv`) and populate `csv_bulk.csvs[0].url` |
| OK_DENTAL | OK | ag_grid_spa | `datatables_jsapi_search_unsupported` — 8 separate sub-pages (Dentist, Hygienist, Assistant, Permit, Lab, Residents, Faculty, Student); each requires `$.fn.dataTable.tables({api:true}).search(q).draw()` JS API call | Implement `datatables_jsapi_search` strategy in `navigator.py` + multi-page archetype that iterates over 8 sub-page URLs |
| OK_MEDBOARD | OK | classic_html_form | `search_button_selector_mismatch` — engine could not click submit button on `medical_search.php`; possible anti-bot protection | Run `--headed` to inspect actual search button and any pre-search modal; update `form.search_button.selector` |
| OK_OPTOMETRY | OK | thentia_cloud | `thentia_portalus_dom_differs` — `obeo.portalus.thentiacloud.net` template differs from standard `*.us.thentiacloud.net`; engine could not find `input#keywords` | Run `--headed` on portalus subdomain to identify actual selector (likely `input[ng-model*='keyword']` or `input[placeholder*='earch']`); update mode `input_selector`s |
| OK_OSTEO | OK | thentia_cloud | `thentia_portalus_dom_differs` — same as OK_OPTOMETRY | Same fix as OK_OPTOMETRY |
| OR_COUNSELORS | OR | thentia_cloud | `thentia_search_by_dropdown_not_set` — engine warns "Could not set Search By to Last Name"; falls back to keyword search and returns first record alphabetically | Inspect Search By widget (likely a custom dropdown with `ng-click`, not native `<select>`); update `form.search_by_dropdown.strategy` to `'custom_dropdown'` with item selector |
| OR_DENTAL | OR | ag_grid_spa | `angular_spa_one_field_only` — Angular SPA enforces one search field at a time; engine fills `formcontrolname` input but the framework's reactive-form binding doesn't update | Implement Angular reactive-form fill that dispatches `input`/`change` events explicitly; OR use `page.evaluate()` to call ng-model setter directly |
| OR_NATUROPATH | OR | thentia_cloud | `thentia_search_by_dropdown_not_set` — same as OR_COUNSELORS; small board so search returned 0 records | Same fix as OR_COUNSELORS; consider direct hash-URL search pattern (`#search/{q}/{offset}/10`) |
| OR_OPTOMETRY | OR | state_portal | `ogovcore_platform_not_validated` — OGovCore platform; selectors copied from standalone scraper but not validated against engine | Run `--headed` to validate `input[type='search']` + `button:has-text('Search')` flow; OGovCore detail page has tabs (Board Actions, Places of Practice, License History) that need explicit click steps |
| OR_PT | OR | thentia_cloud | `thentia_search_by_dropdown_not_set` — same as OR_COUNSELORS | Same fix as OR_COUNSELORS; OR use `#search/{q}/{offset}/10` hash URL pattern |
| OR_SLP | OR | thentia_cloud | `thentia_search_by_dropdown_not_set` — same as OR_COUNSELORS | Same fix as OR_COUNSELORS |
| TX_CHIRO | TX | classic_html_form | `filemaker_webdirect_unsupported` — `db.tbce.texas.gov` uses FileMaker WebDirect on Vaadin 8; no native `<input>` elements; fields are `div.fm-textarea` readonly divs requiring `.click()` then `keyboard.type()` (not `fill()`) | Add `archetype: 'filemaker_webdirect'` with click→type fallback in `navigator.py`; plus 60s Vaadin boot wait |
| TX_DENTAL | TX | ag_grid_spa | `datatables_column_search_unsupported` — DataTables 2.x with per-column search inputs (col 7 = Last Name) requires `.DataTable().column(7).search(val).draw()` JS API call; detail page is Bootstrap modal where field values come from `disabled <input>.value` attributes | Implement `datatables_jsapi_search` strategy in `navigator.py` + `label_input_disabled` extractor that reads `.value` attribute |
| OK_BEHAVIORAL_HEALTH | OK | thentia_cloud | `thentiacloud_api_blocked_corporate` — `obbhl.us.thentiacloud.net` page loads OK but Thentia search XHR API returns no data from corporate network (both via proxy and direct). proxy: false; strategy: select; column mapping corrected. | Test from non-corporate network; if XHR works, remove `skip: true` and confirm results table populates |
| OK_SOCIALWORK | OK | thentia_cloud | `thentiacloud_api_blocked_corporate` — `osblsw.portalus.thentiacloud.net` same pattern. proxy: false; strategy: none (custom_dropdown broke Angular state); column mapping corrected. | Test from non-corporate network; if XHR works, remove `skip: true` |
| OK_ODOHCS | OK | thentia_cloud | `thentiacloud_api_blocked_corporate` — `odohcs.portalus.thentiacloud.net` page loads OK but Thentia search XHR API times out from corporate network. proxy: false; results_wait timeout 60s; column mapping corrected. | Test from non-corporate network; if XHR works, remove `skip: true` and confirm `button.btn-brand` selector works |
| VT_OPR | VT | classic_html_form | `pega_constellation_unsupported` — `secure.professionals.vermont.gov` uses Pega Constellation React SPA. Requires `press_sequentially` + React native-setter event dispatch + 90s boot wait + CSS force-enable for DISPLAY RESULTS button. | Implement `pega_constellation` archetype: `get_by_label()` fill + `press_sequentially(value, delay=40)` + `page.evaluate` native-setter + poll for button un-disable + force-click |

---

## Smoke test gate

Every `config.yaml` must have a `smoke_test` block before shipping.  
`smoke_all.py` is the only regression gate — run it before and after every engine change.

```
PASS     all assertions in expect{} matched
FAIL     assertion failed or exception raised  ← blocks merge
SKIP     skip: true in smoke_test block        ← acceptable (known blocker)
MISSING  no smoke_test block                   ← add one before shipping
```

Validation also warns on missing blocks:

```bash
python -m engine.validate sites/XX_BOARD/config.yaml
```

---

## Adding a `certemy` board

1. Find the public registry UUID from the Certemy URL (format: `*.certemy.com/public-registry/{uuid}`).
2. Run `discover_certemy_headers.py` (or a targeted smoke run with `--headed`) to extract live `<thead th>` column headers.
3. Create `sites/XX_BOARD/config.yaml` with `archetype: certemy`, `base_url`, and a `detail.field_map` mapping those headers.
4. Choose a `smoke_test.query` — use `last_name: "Smith"` for large boards; use a known practitioner's last name for small boards (< 100 licensees, e.g. NV_ORIENTAL uses "Abare").
5. Run targeted smoke test: `PROXY=proxy:9119 python smoke_all.py --filter XX_BOARD`
6. Run full regression: `PROXY=proxy:9119 python smoke_all.py`

---

## Maintenance reminders

- **NV_OPTOMETRY** — PDF URL contains a date stamp (`Verification-YYYYMMDD.pdf`).
  Update `pdf_bulk.pdfs[0].url` in [sites/NV_OPTOMETRY/config.yaml](sites/NV_OPTOMETRY/config.yaml) monthly.

- **LA_MASSAGETHERAPY** — Correct domain is `labmt.org`. URL pattern:
  `wp-content/uploads/YYYY/MM/estab-active-list-M-DD-YYYY.pdf` and `prof-active-M-DD-YYYYb.pdf`.
  Known publish date: 5-15-2026, but those files return HTTP 404 — board may not have published
  the current roster at that path. When new files appear, update both URLs in
  [sites/LA_MASSAGETHERAPY/config.yaml](sites/LA_MASSAGETHERAPY/config.yaml) and remove `skip: true`.

- **AR_PODIATRY** — PDF URL contains a date stamp (`Podiatry_LicenseVerification_YYYYMMDD.pdf`).
  Update `pdf_bulk.pdfs[0].url` in [sites/AR_PODIATRY/config.yaml](sites/AR_PODIATRY/config.yaml)
  when a new PDF is published. Check `https://healthy.arkansas.gov/boards-commissions/boards/podiatric-medicine-board/`.
  Current URL: `20260522`. Smoke query: license 247 (Jason Smith).

- **NY_APPEARANCE** — Dataset `ucu3-8265` on data.ny.gov contains active-only licenses;
  records return `status: unknown` (no status field in dataset). License number searches
  with hyphenated NY format (e.g. `AEC-15-05266`) return 0 results because the engine
  strips non-digits for LIKE matching — use `last_name` mode for NY searches.

- **SD_CHIRO / SD_OPT** — Both use `csv_bulk` with `download_strategy: link_text`. The `/Licensees`
  page renders the "Download" link via JavaScript (Blazor/React SPA) — takes 15-25s headless.
  The csv_extractor waits up to 25s with `wait_for_selector` before evaluating the JS fetch.
  Date format is `"%m/%d/%Y %I:%M:%S %p"` (e.g. `1/4/2016 12:00:00 AM`).
  SD_OPT has only 251 optometrists; no Smiths in dataset — smoke query uses "Anderson".

- **CT_ELICENSE** — `multi_step_checkbox` strategy. Bootstrap collapse panel: checkboxes only
  become visible after panel animation (`offsetParent !== null`). Label text is a TEXT NODE
  after the `<span><input></span>` wrapper — accessed via `cb.parentElement.nextSibling.textContent`.
  CSV stores zero-padded license numbers (e.g. `082619`); `search_by_license_number` strips
  leading zeros from both sides for normalization. Currently downloads only `Physician/Surgeon - MD/DO`
  (24K+ rows); extend `practitioner_types` in config to add more types.

- **IN_PLA** — mylicense.in.gov. Input selectors use `t_web_lookup__` prefix (double underscore):
  `t_web_lookup__last_name`, `t_web_lookup__first_name`, `t_web_lookup__license_no`.
  `row_selector: "table#datagrid_results > tbody > tr"` required — using `"table tr"` matches
  nested sub-rows inside the Name column's inner table, producing empty `full_name`.

- **VT_MEDBOARD** — Next.js MUI (Material UI) SPA. Results render as MUI Cards, not a table.
  `row_selector: "div[class*='MuiCard-root']"`, `cell_selector: "span[class*='MuiCardHeader-title']"`,
  `columns: {0: full_name}`. Only name is extractable from the card summary (no license number or
  status in the card; detail pages not visited). React input ID pattern `input[id*='r']` matches
  the live search box regardless of which `:ra:` / `:rb:` ID Next.js assigns.

- **NV_NVADGC** — Smoke test changed from `license_number 01952-I` (removed from portal) to
  `last_name Smith`. Board name corrected: was "Nevada State Board of Dental Examiners",
  is "Nevada Division of Alcohol and Drug Counselors". Requires PROXY=proxy:9119.

- **NV_MASSAGE** — requires proxy (`PROXY=proxy:9119`). ASP.NET UpdatePanel portal at
  online.nvmassagebd.com. `element_visible` results_wait waits for `table[id$='gvLicensee'] tr td`.

- **NV_SPEECH** — requires proxy (`PROXY=proxy:9119`). Results navigate to `/verify/results/`;
  `url_change` strategy + `table#verify-results` tbody extraction. 21 Smith records confirmed.

- **NV_OSTEO** — requires `PROXY=proxy:9119`. `has_detail_page: false` — detail page visits
  caused 3+ minute timeouts with empty field overwrite. Table extraction gives: License#,
  First/Last Name, City, License Type, Status, Expiry Date.

- **WI_DSPS** — Salesforce Experience Cloud SPA. Results render as `tr.slds-hint-parent` rows;
  first column is `<th data-label="Credential/License Number">` (not `<td>`), extracted via
  `cell_selector: "td[data-label], th[data-label]"`.

- **MD_PHYSICIANS** — Intermittently failing due to Azure OpenAI Connection error via Zscaler.
  Pre-existing environment issue; not related to the scraper config.

- **AK_CBP** — Covers all professions licensed by Alaska Division of CBP (medical, legal,
  construction, all trades). CSV is refreshed weekly. Requires `PROXY=proxy:9119`.
  `Owners` column contains both individual names and business entity names; `split_full_name`
  will parse individuals correctly but may produce odd first/last splits for businesses.

- **AL_ALBME** — Covers MD, DO, PA licensed by Alabama Board of Medical Examiners.
  CSV is a daily snapshot of all active licenses. Requires `PROXY=proxy:9119`.
  License numbers are simple integers (e.g. `893`). `PUBLIC ACTIONS?` column is not
  currently mapped — check `raw_fields` if you need disciplinary action data.

- **IL_LICENSING** — Covers all 63 IDFPR professional license types on data.illinois.gov (dataset `pzzh-kp68`).
  No proxy required — accessible via Playwright browser `page.goto()`. Healthcare-relevant types include
  MEDICAL BOARD, NURSING BOARD, DENTAL, PHARMACY, PHYSICAL THERAPY, OCCUPATIONAL THERAPY, PHYSICIAN ASSISTANT,
  ADV PRACTICE NURSE, ACUPUNCTURE, CLIN PSYCHOLOGIST, MAR AND FAM THERAPIST, SOCIAL WORKER, SPEECH-LANGUAGE PATH,
  OPTOMETRY, PODIATRY, and more. Date format `%m/%d/%Y`. Status stored as uppercase (ACTIVE, INACTIVE, EXPIRED, etc.)

- **VA_DHP** — Virginia Department of Health Professions covers 157 occupation types including Medicine, Nursing (RN/LPN/APRN),
  Dentistry, Pharmacy, Physical Therapy, Occupational Therapy, Physician Assistant, Chiropractor, Optometrist,
  Social Worker, Licensed Professional Counselor, Marriage and Family Therapist, Massage Therapist, Audiologist,
  Speech-Language Pathologist, Psychologist, Nurse Aide, Medication Aide, and more. Requires `PROXY=proxy:9119`.
  Detail page at `/Lookup/Detail/{id}` uses `<th>` labels + `<td>` values (th_td_table strategy). Name search
  returns "too many records" for common surnames — use `mode: license_number` or combine with first name / occupation.
  Annual subscription (`$95/user`) required for batch database downloads, but individual license lookups are free.

- **LA_DENTAL** — member-base.net portal. Outer page is a 2-column layout table; results table is
  nested inside (`row_selector: "table table tr"`). Column order: [0]=H/D code, [1]=License#,
  [2]=LastName, [3]=FirstName, [4]=Status (ACT=Active), [5]=DiscAction, [6]=IssueDate, [7]=ExpDate.
  Status "ACT" maps to active. Form inputs: `txtlicno`, `txtfname`, `txtlname`; button: `input[name='search']`.

- **LA_OPTOMETRY** — MemberLeap platform (`memberleap.com`). Name search uses `input[name='business_name']`
  (not a standard last_name field). Results are AJAX-loaded div.search-result cards — use
  `strategy: element_visible, selector: "div.search-result"` (NOT network_idle which fires before AJAX).
  License/status not available without detail page navigation; name is in `div.name-plate`.

- **LA_PT** — laptboard.org WordPress site with Bootstrap collapse form. REQUIRED steps:
  (1) `pre_search_click: "button.clps-trigger"` — expands the hidden search form.
  (2) `submit_via_enter: true` — pressing Enter submits the form (clicking the "Refine Results"
      button via button selector fails because site requires JS keystroke events via `.type()`,
      not Playwright's `.fill()`; the button-by-text fallback takes 60+ seconds).
  Results are `div.licensee` cards; `cell_selector: "h3, dd"` extracts [0]=name, [1]=license_type,
  [2]=license_number, [3]=status, [4]=issue_date, [5]=expiration_date.

- **MD_SOCIALWORK** — Same mdbnc.health.maryland.gov portal family as MD_AUDIOLOGY but with different
  column layout. License number is at column index 4 (NOT 2 like MD_AUDIOLOGY and MD_PSYCH).
  Uses `bodyContentPlaceHolder_` prefix for input IDs. Detail page `DetailsView1` uses two_column_table.

- **MD_PSYCH** — Same mdbnc.health.maryland.gov portal family but uses `MainContent_` prefix (NOT
  `bodyContentPlaceHolder_`). Detail page "View Details" link text is "Open Details Page" — different
  from all other MD boards which use "View Details". Column [2]=license_number (unlike MD_SOCIALWORK).
  Detail uses th_td_table strategy (label in `<th>`, value in `<td>`).

- **OR_OMB** — AngularJS SPA at omb.oregon.gov/search. The "No results were found." message is ALWAYS
  in the DOM inside a hidden `<div ng-hide>` (Angular template) — never use "no results" as a
  `no_results_indicators` string or it will always trigger false positive. Results are
  `div.alx-list-item` cards (ng-repeat); each card has `<h4>` = "Last, First Middle" name.
  License# and status require detail page visit (`VerificationDetails.aspx?EntityID=XXXXXX`).

- **OR_HLO** — Oregon Health Licensing Office at elite.hlo.state.or.us. Status: SKIP.
  Form button value is "Start Search" (not "Search"); license field is `CPH1_txtsrcLicenseNo`
  (note: `LicenseNo` not `LicNo`). The ASP.NET UpdatePanel AJAX POST returns empty results on
  corporate network. Run offline or investigate `Sys.WebForms.PageRequestManager` postback.

- **FL_MQA** — Single portal (`mqa-internet.doh.state.fl.us`) covers all Florida DOH health boards
  (Medicine, Dentistry, Nursing, Pharmacy, Chiropractic, Optometry, Podiatry, PT, OT, Psychology,
  Social Work, MFT, MHC, SLP/AUD, PA Council, and more). Board and Profession dropdowns are left
  at "-- Any --" so all professions are searched simultaneously. Detail pages have 3 tabs
  (License Information, Secondary Locations, Discipline/Admin Action) — only the default License
  Information tab is extracted. To narrow to a specific board, use the `--board` and `--profession`
  flags in the original `florida_all_providers_web_scraping.py` script instead.
  Names are stored in MQA format: "LAST, FIRST MIDDLE". Status values vary by profession —
  extend `output.status_map` if new status values appear in `raw_fields.status`.

- **NV_DIETITIAN** — nvdpbh.aithent.com portal. `aithent_portal_xls` strategy: selects the
  "Dietitians and Music Therapist" business unit from the dropdown (ASP.NET postback), then clicks
  the "Generate Excel" `<a>` tag to download the full roster as `.xls`. Requires `xlrd` package
  (`pip install xlrd==2.0.2`). Names stored as "LAST, FIRST" in the `Name` column.
  Requires PROXY=proxy:9119. Cache expires after 7 days.

- **NV_PHARMACY** — online.nvbop.org AngularJS portal. `nvbop_angular_xlsx` strategy: selects
  "Personal License Search" radio, chooses "Pharmacist" from the `ng-model` type dropdown,
  blank search, then "Export To Excel" → downloads `.xlsx`. Columns: `Last Name`, `First Name`,
  `LicenseId`, `License Status`, `License Expiration Date`. Requires PROXY=proxy:9119.

- **TX_MEDBOARD** — profile.tmb.state.tx.us. First page load shows a terms-of-use page with
  `input[id*='btnAccept']`; `pre_search_click` handles the click and waits for `networkidle`
  (not `asyncio.sleep`) so the search form loads before typing begins.
  Detail links are `javascript:__doPostBack(...)` — not URL navigation; `has_detail_page: false`.
  Columns: [0]=Name, [1]=License#, [2]=Type, [3]=Address (NOT city), [4]=City, [5]=BoardActions.

- **TX_TDLR** — tdlr.texas.gov. Results page has nested table structure; outer `<tr>` wraps
  data rows and header rows. Selector `tr:has(> td[width='90'])` targets only data rows (they
  contain a 90px-wide first cell). `has_detail_page: false` — detail page AI extraction returns
  empty `full_name`. Smoke query "Smithwick" (4 results) avoids "Smith" pagination slowdown.

- **MN_COSMETOLOGY** — bcegl.hlb.state.mn.us GLSuite. Results table uses standard
  `#ContentPlaceHolder1_dtgResults tr` row selector. Detail links are `javascript:__doPostBack`
  so `has_detail_page: false`; only `full_name` extracted from main table (col[0]).

- **MN_DENTISTRY** — mnbodv7prod.glsuite.us. Uses a nested table structure: outer `#DataTable`
  contains an inner `<table>` with data rows. Selector `#DataTable table tr` targets only
  the inner table rows. Columns: [0]=Name, [1]=City, [2]=LicType, [3]=LicNum, [4]=Status.
  `has_detail_page: false`.

- **WY_DENTAL / WY_OPTOMETRY / WY_PT** — Google Sheets roster. The `google_sheet_link`
  strategy now downloads via httpx directly rather than `page.expect_download()` — the
  browser-based download consistently timed out (>3 min) through the corporate proxy because
  Google Sheets returns a 302 redirect chain that `expect_download` cannot follow through the
  proxy. httpx handles the redirects correctly. `download_timeout_ms: 180000` still applies
  to the httpx request timeout. WY_PT uses `link_selector_nth: 1` because the page has two
  Google Sheets links and the PT roster is the second one.

- **MD_DIETETICS / MD_COUNSELORS** — Same mdbnc.health.maryland.gov ASP.NET portal family as MD_AUDIOLOGY. Both use `bodyContentPlaceHolder_` ID prefix, `DetailsView1` detail page, and `two_column_table` strategy. MD_DIETETICS smoke query is "Williams" (no Smiths registered as MD dietitians). MD_COUNSELORS smoke query is "Goldstein".

- **ND_AP / ND_DENTISTRY / ND_PT** — All use the same Bootstrap verify-portal template (same state IT vendor). Input IDs: `#inputLastName`, `#inputLicenseNo`, `#inputcity`. Results table has class `table table-striped table-hover` — use `row_selector: "table.table tbody tr"` (not `"table tr"` which matches Google CSE tables on the page). Column order: [0]=Name, [1]=Type/Specialty, [2]=License#, [3]=City, [4]=State.

- **ND_PODIATRY** — WordPress participants-database plugin at ndpodiatryboard.org. Search: `select#pdb-search_field-2` picks the search field (option values: `last_name`, `license_number`, etc.); `input#participant_search_term` (`name="value"`) is the search value input. AJAX-driven — use `results_wait.strategy: delay` (5s) rather than `network_idle`. No Smiths in ND podiatry registry — smoke query uses "Anderson" (Brad Anderson, confirmed in dataset).

- **LA_SOCIALWORK** — labswe.org ColdFusion portal. Search form is visible on page load (do NOT add `pre_search_click` — clicking "Refine Search" toggles the form CLOSED). Button is `a#search_button`. Results are flat `<li class="row">` cards (unlike lbedn.org and lbespa.org which use lazy-loaded accordions). Engine extracts `h3` from `li.row` for name only.

- **LA_DIETETICS / LA_SPEECH** — lbedn.org and lbespa.org ColdFusion portals. Results grouped by profession in Bootstrap collapse panels; each `<div class="panel-body">` is empty on page load and populated via AJAX only when user expands the group by clicking the profession header. `li.row` items never appear in the initial DOM. Marked SKIP until engine adds accordion-expand strategy.

- **WV_CHIRO** — boc.wv.gov PDF roster. PDF URL changes when board publishes a new roster. To unblock: navigate to boc.wv.gov/roster.html, right-click the current PDF link → copy URL, update `pdf_bulk.pdfs[0].url` in config.yaml, remove `skip: true`. Column order confirmed: Last Name | First Name | Middle Initial | Title | License# | Initial WV License Date | License Expiration Date | Disciplinary Action?.

- **AI fallback circuit breaker** — `ai_fallback.py` opens after 2 consecutive Azure OpenAI
  connection errors and skips all further AI calls for the process lifetime. Prevents infinite
  retry loops on TX_TDLR / TX_MEDBOARD which had 4000+ result rows with no Azure connectivity.

- **MS_CHIRO** — Mississippi Board of Chiropractic Examiners at msbce.ms.gov. Classic ASP page.
  Last-name search only (`input[name='lName']`), image-submit button. Results in `table[bgcolor='#FFFFFF']`
  with a header row (skip_first_row: true). Columns: [0]=Name, [1]=Address, [3]=License#, [4]=IssueDate,
  [6]=ExpireDate, [7]=Status. No detail page.

- **MS_OPTOMETRY** — Mississippi State Board of Optometry at ms.gov. DataTables client-side filtered
  table (`table#LicenseTable`). The full roster is loaded on page load; jQuery DataTables global search
  (`input[type='search']`) filters rows. Each row has a single `td.lic` cell containing multi-line text
  (name, address, License#, TPA#, dates, status, disciplinary). `datatables_jsapi` archetype.

- **MS_PT** — Mississippi Board of Physical Therapy at msbpt.ms.gov. Classic ASP page.
  License number (`input[name='LICENSENO']`) or last name (`input[name='LNAME']`) search.
  Results render as `fieldset.frameset2` cards; name is in `.dryneedlingname`. No detail page.

- **MI_LARA** — Michigan LARA on Accela Citizen Access (`aca-prod.accela.com/MILARA`). Uses
  `PropertyLookUp.aspx?isLicensee=Y` for licensee search. Covers all Michigan professional licenses.
  Last-name input: `input[id*='txtLastName']`. Search button: `a[id*='btnNewSearch']` (Accela uses
  `btnNewSearch`, not `btnSearch`). Results in `gdvRefLicenseeList` grid; data rows have class
  `ACA_TabRow_Odd` / `ACA_TabRow_Even`. Wait strategy: `element_visible` on data rows (not `network_idle`
  — click fires async so networkidle races). 9 columns: License Type, License Number, First Name, Middle
  Initial, Last Name, Org Name, DBA, Status, Expiration Date. `has_detail_page: false` — list view
  provides all needed fields; detail links are `lnkLicenseRefNumber`. Requires `PROXY=proxy:9119`.

- **WV_MEDBOARD_MD / WV_MEDBOARD_PA / WV_MEDBOARD_DPM** — West Virginia Board of Medicine at wvbom.wv.gov.
  All three boards share the same Rosters page (`wvbom.wv.gov/Rosters.asp`). Each downloads a separate
  XLSX by clicking the matching anchor text: "Roster of Medical Doctors" / "Roster of Physician Assistants" /
  "Roster of Podiatric Physicians". XLSX structure: row 0 = section title, row 1 = column headers, rows 2+ = data.
  Engine reads first sheet ("Licenses") via `link_text_xlsx` with `xlsx_header_row=1`.
  MD roster has 6 sheets (Licenses, Special Licenses, Temporary Licenses, Educational Permits,
  Reciprocal Educational Permits, Interstate Telehealth Registration); PA has 3; DPM has 1.
  Columns: First Name, Last Name, Middle Name, Suffix, License Number, License Expiration Date,
  License Type (MD only). Source scripts: `westvirginia_MD_csv.py`, `westvirginia_PA_csv.py`,
  `westvirginia_DPM_csv.py`. No proxy required (`wvbom.wv.gov` accessible on corporate network).

- **MO_HEALING_ARTS / MO_DENTAL / MO_OPTOMETRY / MO_PHARMACY** — Missouri Division of Professional
  Registration at mopro.mo.gov (Salesforce LWC portal). `mopro_zip` csv_bulk strategy: selects board
  label from Lightning combobox → Submit → downloads each ZIP → extracts tab-delimited TXT → merges.
  Cache: 7-day TTL under `csvs/mo_<board>/`, saved as UTF-8. Column names in TXT files are lowercase:
  `lic_number`, `prc_first_name`, `prc_last_name`, `lst_description`, `lic_exp_date`, `ba_address`, etc.
  Multi-ZIP boards: Healing Arts (36 ZIPs, 38 profession types), Dental (19), Pharmacy (10), Optometry (1).

- **MO_NURSING** — MOPRO portal explicitly states "Downloadable files for nursing are not available."
  Portal redirects to Nursys.com (NCSBN QuickConfirm + e-Notify). Permanently SKIP; would need a
  separate Nursys-based source implementation. Screenshot confirming portal message saved as
  `_mopro_debug_Nursing.png` in scrapers root.
