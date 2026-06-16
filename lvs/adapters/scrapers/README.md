# PSV License Verification Scraper Engine

Spec-driven Playwright scraper engine for professional license verification.  
253 qualifying boards across 41 states (133 engine configs) — each board is a `sites/XX_BOARD/config.yaml` file;
no engine code changes are needed to add a new board (including new `csv_bulk` and `certemy` boards).

---

## Directory layout

```
lvs/adapters/scrapers/
├── engine/                  # shared engine (15 modules)
│   ├── models.py            # Pydantic v2 models — all config + output contracts
│   ├── browser.py           # Playwright launch helper (proxy-aware)
│   ├── navigator.py         # form fill, dropdown, search button interactions
│   ├── extractor.py         # results table + detail page extraction strategies
│   ├── pdf_extractor.py     # PDF bulk-roster download, table extract, search
│   ├── csv_extractor.py     # CSV bulk-roster download (link_text / post_form), search
│   ├── pagination.py        # next-button / page-numbers pagination
│   ├── output.py            # field normalization → LicenseRecord
│   ├── post_processors.py   # apply_field_map, status_map, date parsing
│   ├── ai_fallback.py       # Azure OpenAI GPT-4 fallback (< 3 fields extracted)
│   ├── telemetry.py         # SQLite scrape_events / ai_touchpoints logging
│   ├── evidence.py          # HTML + screenshot capture per run
│   ├── proxy.py             # corporate proxy config from env vars
│   ├── retry.py             # exponential back-off wrapper
│   └── validate.py          # load_config: YAML → SiteConfig (Pydantic)
│
├── sites/                   # per-board YAML configs (127 boards)
│   ├── AK_CBP, AL_ALBME
│   ├── FL_MQA
│   ├── NV_MEDBOARD, NV_CHIRO, NV_NVADGC, NV_PT, NV_BOP, NV_DENTAL, NV_OSTEO
│   ├── NV_MASSAGE, NV_SPEECH, NV_OPTOMETRY
│   ├── NV_PODIATRY, NV_MFTPC, NV_ORIENTAL, NV_ABA         # certemy archetype
│   ├── NV_DIETITIAN, NV_PHARMACY                          # aithent_portal_xls / nvbop_angular_xlsx
│   ├── MA_HEALTH, MA_MDDO                                  # MA_MDDO: json_api archetype (session 26 PASS)
│   ├── MD_PHYSICIANS, MD_CHIROPRACTIC, MD_MASSAGE, MD_OPTOMETRY, MD_AUDIOLOGY, MD_PT
│   ├── MD_SOCIALWORK, MD_PSYCH, MD_DIETETICS, MD_COUNSELORS
│   ├── MD_ACUPUNCTURE                                      # session 29 csv_bulk direct_url (PASS)
│   ├── MN_COSMETOLOGY, MN_DENTISTRY                        # MN GLSuite PASS
│   ├── MN_EMS, MN_MEDPRACTICE                              # MN_EMS: PASS (session 29); MN_MEDPRACTICE: SKIP (Angular)
│   ├── MI_LARA                                             # Michigan LARA Accela Citizen Access (session 31)
│   ├── MO_HEALING_ARTS, MO_NURSING                         # Missouri mopro_zip csv_bulk (session 31, SKIP until engine support)
│   ├── MO_DENTAL, MO_OPTOMETRY, MO_PHARMACY                # Missouri mopro_zip csv_bulk (session 31, SKIP until engine support)
│   ├── MS_CHIRO, MS_OPTOMETRY, MS_PT                       # Mississippi: classic_html_form + datatables_jsapi (session 31)
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
│   ├── ID_DOPL                                             # session 30 PASS (use_keyboard_type + td.TDS)
│   ├── WI_DSPS, NY_APPEARANCE
│   ├── IL_LICENSING, VA_DHP
│   ├── SD_CHIRO, SD_OPT                                    # csv_bulk JS-rendered download
│   ├── WV_OPTOMETRY                                        # certemy archetype
│   ├── WV_CHIRO                                            # SKIP (SPO-migrated OneDrive share requires auth)
│   ├── AZ_ACUPUNCTURE, AZ_BEHAVIORAL_HEALTH, AZ_NATUROPATHIC  # session 25 Thentia PASS
│   ├── AZ_OSTEO, AZ_PSYCH, AZ_PT                           # session 25 Thentia PASS
│   ├── AZ_DENTAL, AZ_OPTOMETRY                             # session 24 classic_html_form PASS
│   ├── AZ_SPEECH_HEAR                                      # session 26 multi_iteration PASS
│   ├── OH_PROVIDERS_BUSINESS, OH_PROVIDERS_INDIVIDUAL       # session 29 csv_bulk ohio_data_portal_csv PASS
│   └── CT_ELICENSE, IN_PLA, VT_MEDBOARD
│
├── run.py                   # CLI entry point — single board, single query
├── smoke_all.py             # regression gate — runs all boards' smoke_test blocks
├── board_inventory.py       # reads Excel, emits filtered board list
├── board_inventory.xlsx     # 243 qualifying boards from source Excel
└── requirements.txt
```

---

## Quick start

```bash
cd lvs/adapters/scrapers

# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Single board — license number lookup
python run.py --config sites/NV_MEDBOARD/config.yaml --mode license_number --query "17371"

# Single board — last name search, headed browser for debugging
python run.py --config sites/KY_MEDBOARD/config.yaml --mode last_name --query "Smith" --headed

# Validate config without running
python run.py --config sites/KS_DENTAL/config.yaml --mode license_number --query "13578" --dry-run

# Regression gate — all boards
python smoke_all.py

# Regression gate — specific boards only
python smoke_all.py --filter KY_OD KY_MULTIBOARD NV_OPTOMETRY

# Show what would run without launching browsers
python smoke_all.py --dry-run
```

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

# VA_DHP — requires proxy, license_number mode, query "0024166737" (John R Smith, APRN)
PROXY=proxy:9119 python run.py --config sites/VA_DHP/config.yaml --mode license_number --query "0024166737"

# NV_CHIRO — requires proxy, license_number mode, query "B02060"
PROXY=proxy:9119 python run.py --config sites/NV_CHIRO/config.yaml --mode license_number --query "B02060"
```

### Run the full smoke suite with all known good values

```bash
# Linux/macOS — with proxy for NV boards
PROXY=proxy:9119 python smoke_all.py

# Windows CMD
set PROXY=proxy:9119 && python smoke_all.py

# Windows PowerShell
$env:PROXY="proxy:9119"; python smoke_all.py

# Save results to a timestamped file
PROXY=proxy:9119 python smoke_all.py 2>&1 | tee output/smoke_regression_$(date +%Y%m%d).txt
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
| AK_CBP | last_name | Smith | Smith — min 1 record (PROXY) |
| AL_ALBME | last_name | Smith | Smith — min 1 record (PROXY) |
| AR_PODIATRY | license_number | 247 | [247] Jason Smith — min 1 record |
| CO_DORA | license_number | 9944947 | [9944947] Kevin Smith — active |
| CT_ELICENSE | license_number | 82619 | [082619] Alif Ahmed — active |
| DE_LICENSING | last_name | Smith | min 1 record |
| FL_MQA | last_name | Smith | Smith — min 1 record |
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
| LA_ADRA | last_name | Smith | [641] Charles R. Smith — active |
| LA_DENTAL | license_number | 3842 | [3842] SHANA SMITHWICK — active |
| LA_DIETETICS | last_name | Smith | **SKIP** (lazy_loaded_accordion) |
| LA_MASSAGETHERAPY | license_number | TBD | **SKIP** (pdf_url_required) |
| LA_OPTOMETRY | last_name | Buisson | Laura Buisson — min 1 record |
| LA_PT | last_name | Smith | Smith — min 1 record |
| LA_SOCIALWORK | last_name | Smith | [?] Addie Smith — min 1 record |
| LA_SPEECH | last_name | Smith | **SKIP** (lazy_loaded_accordion) |
| MA_HEALTH | last_name | Smith | min 1 record |
| MI_LARA | last_name | Smith | Smith — min 1 record (PROXY) |
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
| NJ_DCA | last_name | Smith | Smith — min 1 record |
| NV_ABA | last_name | Smith | [RBT2632] Smith Cheregosha — expired |
| NV_BOP | last_name | Highsmith | Jennifer Highsmith — active |
| NV_DIETITIAN | last_name | Smith | Smith — min 1 record (27 records; PROXY) |
| NV_PHARMACY | last_name | Smith | Smith — min 1 record (89 records; PROXY) |
| NV_CHIRO | license_number | B02060 | Francisco Cruz — active (PROXY) |
| NV_DENTAL | license_number | LL-251-11 | min 1 record |
| NV_MASSAGE | last_name | Smith | Smith — min 1 record (PROXY) |
| NV_MEDBOARD | license_number | 17371 | Eli Azzi — inactive |
| NV_MFTPC | last_name | Smith | Hernoria Childress-Smith — active |
| NV_NVADGC | last_name | Smith | [183-C] Anita Smith — expired (PROXY) |
| NV_OPTOMETRY | last_name | Smith | Smith — min 1 record |
| NV_ORIENTAL | last_name | Abare | [2031] Rachel Abare — unknown |
| NV_OSTEO | last_name | Hatch | Preston Hatch — active (PROXY) |
| NV_PODIATRY | last_name | Smith | [9203] Lary Smith — active |
| NV_PT | license_number | 3485 | Sarah Distad — active (PROXY) |
| NV_SPEECH | last_name | Smith | Smith — min 1 record (PROXY) |
| NY_APPEARANCE | last_name | Smith | SMITH — min 1 record |
| OR_HLO | last_name | Smith | **SKIP** (network_blocked: UpdatePanel AJAX issue) |
| OR_OMB | last_name | Smith | Ayre-Smith, Geoffrey — min 1 record |
| TX_MEDBOARD | last_name | Smith | Smith — min 1 record (50+ records) |
| TX_OPTOMETRY | last_name | Smith | **SKIP** (reCAPTCHA on every search) |
| TX_TDLR | last_name | Smithwick | SMITHWICK — min 1 record (4 records) |
| SD_CHIRO | last_name | Smith | [952] Tracy J Smith — active |
| SD_OPT | last_name | Anderson | [738] Eva Anderson — active |
| VA_DHP | license_number | 0024166737 | [0024166737] John R Smith — expired (PROXY) |
| VT_MEDBOARD | last_name | Smith | Smith, Delaney — unknown (min 1 record) |
| WA_HEALTH | license_number | RN.RN.61663091 | Madeline Smith — active |
| WI_DSPS | last_name | Smith | Smith — min 1 record |
| WV_CHIRO | license_number | 3842 | **SKIP** (pdf_url_required) |
| WV_OPTOMETRY | last_name | Smith | [873-OD] Gary Smith — active |
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
| WV_PT | last_name | Smith | **SKIP** (thentia_portalus_dom_differs) |
| WV_SOCIALWORK | last_name | Smith | **SKIP** (dnn_sqlviewpro_postback_required) |
| MA_MDDO | last_name | Smith | **SKIP** (json_api_archetype_required) |
| MO_HEALING_ARTS | last_name | Smith | **SKIP** (mopro_zip_strategy_required) |
| MO_NURSING | last_name | Smith | **SKIP** (mopro_zip_strategy_required) |
| MO_DENTAL | last_name | Smith | **SKIP** (mopro_zip_strategy_required) |
| MO_OPTOMETRY | last_name | Smith | **SKIP** (mopro_zip_strategy_required) |
| MO_PHARMACY | last_name | Smith | **SKIP** (mopro_zip_strategy_required) |
| MS_CHIRO | last_name | Smith | Smith — min 1 record (PROXY) |
| MS_OPTOMETRY | last_name | Smith | Smith — min 1 record (PROXY) |
| MS_PT | last_name | Smith | Smith — min 1 record (PROXY) |

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
| MS_OPTOMETRY | `ms.gov` (msbo subdomain) blocked by Zscaler |
| MS_PT | `msbpt.ms.gov` blocked by Zscaler |

All other boards (including all Certemy, SD, NJ, MD, FL, IL, CO, WA, NY, DE, WI, KY, KS, MA boards)
work without proxy — accessible on the corporate network directly or via public Socrata/CSV APIs.

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

1. Create `sites/XX_BOARD/config.yaml` — see schema below.
2. Validate: `python -m engine.validate sites/XX_BOARD/config.yaml`
3. Dry-run: `python run.py --config sites/XX_BOARD/config.yaml --mode license_number --query "TBD" --dry-run`
4. Live test (headed): `python run.py --config sites/XX_BOARD/config.yaml --mode license_number --query "TBD" --headed`
5. Add a `smoke_test` block with a real stable query and run `python smoke_all.py --filter XX_BOARD`.
6. Run full regression: `python smoke_all.py` — all prior PASS boards must still PASS.
7. Update `board_inventory.xlsx` — set `Smoke Test Status` to PASS.

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
  local_path: "./evidence/{source_id}/{run_id}/"

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

Every run produces HTML and screenshot evidence in `./evidence/{source_id}/{run_id}/`.

Files saved per run (when `capture_on` includes the matching stage):

| File | Stage | When saved |
|------|-------|------------|
| `search_results.html` | `search_results` | After results load (all archetypes) |
| `search_results.png` | `search_results` | After results load (all archetypes) |
| `detail_page.html` | `detail_page` | When visiting detail page (`has_detail_page: true`) |
| `detail_page.png` | `detail_page` | When visiting detail page (`has_detail_page: true`) |
| `error.html` | `error` | When an exception or extraction failure occurs |
| `error.png` | `error` | When an exception or extraction failure occurs |

### Evidence config options

```yaml
evidence:
  capture_html: true              # Save page HTML to .html file
  capture_screenshot: true        # Save full-page screenshot to .png file
  capture_on:
    - search_results              # Capture after search results load
    - detail_page                 # Capture on detail page (only if has_detail_page: true)
    - error                       # Capture whenever an error occurs
  storage: local
  local_path: "./evidence/{source_id}/{run_id}/"   # {source_id} and {run_id} are expanded at runtime
```

### Finding evidence for a specific run

```bash
# List recent evidence directories for a board
ls evidence/CO_DORA/

# Open latest screenshot
ls -t evidence/NV_MEDBOARD/*/search_results.png | head -1
```

**Note:** For `pdf_bulk` boards (AR_PODIATRY, NV_OPTOMETRY, LA_MASSAGETHERAPY), the
`search_results` stage captures the PDF download page state, not search result rows —
this is less useful visually but still captures errors. For `socrata_bulk_csv` boards
(DE_LICENSING, WA_HEALTH, CO_DORA, NY_APPEARANCE), the screenshot shows the raw JSON
blob rendered in Chromium. For `csv_bulk` boards (AK_CBP, AL_ALBME), evidence capture
is disabled by default (`capture_html: false`) since no browser page renders the search results.

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
| `scrape_events` | Per-run: board, mode, query, status, duration, record count |
| `ai_touchpoints` | AI fallback invocations: board, run_id, tokens used |
| `license_records` | Canonical `LicenseRecord` output per record |

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

## Board status (as of 2026-06-15)

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
| KS_PHARMACY | KS | state_portal | PASS | query Baker; Abdalla Abu Baker — active |
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
| LA_MASSAGETHERAPY | LA | pdf_bulk | **SKIP** | pdf_urls_stale_404: labmt.org May 2026 PDFs return 404; online.labmt.org requires login; labmt.org/search/ is WordPress site-search |
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
| WV_PT | WV | thentia_cloud | PASS | session 25 — portalus breakthrough; has_detail_page: false |
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

**Summary: 127 PASS / 0 FAIL / 8 SKIP** *(post-session 30; smoke v3 2026-06-16: 7 FAIL boards all resolved — 4 PASS, 3 new SKIP. 22 new boards added. Board table to be fully refreshed from smoke v3 results.)*

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
| LA_MASSAGETHERAPY | LA | pdf_bulk | `pdf_url_required` — labmt.org PDFs dated 5-15-2026 return HTTP 404; board has not published current roster | Update `pdf_bulk.pdfs[].url` in [config.yaml](sites/LA_MASSAGETHERAPY/config.yaml) when new files appear at labmt.org; remove `skip: true` |
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
| WV_PT | WV | thentia_cloud | `thentia_portalus_dom_differs` — `wvbopt.portalus.thentiacloud.net` template differs from standard `*.us.thentiacloud.net` (same as OK_OPTOMETRY/OK_OSTEO) | Same fix as OK_OPTOMETRY: run `--headed` to identify actual selectors on portalus subdomain |
| WV_SOCIALWORK | WV | classic_html_form | `dnn_sqlviewpro_postback_required` — DNN SQLViewPro module uses `a.CommandButton` submit triggering iframe-based ASP.NET postback; engine's `table tbody tr` selector misses the iframe results panel | Identify result iframe selector via `--headed` (likely `iframe[src*='QueryResults']` or `div#dnn_ctr...`); point row_selector inside iframe context |
| MA_MDDO | MA | classic_html_form | `json_api_archetype_required` — `api.medboard.mass.gov/api-public/search` is a POST JSON endpoint; engine has no `json_api` archetype | Add `archetype: 'json_api'` that POSTs JSON to `base_url + endpoint` and parses `response.results[]`; OR verify if `MA_HEALTH` already covers MD/DO/AP and mark as duplicate |

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

- **MO_HEALING_ARTS / MO_NURSING / MO_DENTAL / MO_OPTOMETRY / MO_PHARMACY** — Missouri Division
  of Professional Registration at mopro.mo.gov (Salesforce Experience Cloud / LWC portal). Each board
  requires selecting from a Salesforce Lightning combobox → Submit → Download ZIP → extract tab-delimited
  TXT. A 7-day file cache under `csvs/mo_<board>/` avoids repeated portal downloads.
  Uses the `mopro_zip` csv_bulk strategy (not yet in csv_extractor; marked SKIP until implemented).
  Use `missouri_all_txt.py` standalone script in the interim. Field names in the TXT files follow the
  Missouri MOPRO schema: `LIC_NUM`, `PRC_FIRST`, `PRC_LAST`, `LST_DESC`, `EXP_DATE`, `BA_*` address fields.
  EST timestamps in HHMM format are embedded in cache filenames for traceability (session 31).
