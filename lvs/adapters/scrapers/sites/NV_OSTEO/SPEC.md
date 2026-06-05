# NV_OSTEO — Nevada State Board of Osteopathic Medicine

**URL:** https://nsbom.portalus.thentiacloud.net/webs/portal/register/#/  
**State:** NV  
**Profession codes:** DO  
**Captured:** 2026-06-04

---

## Platform Analysis

| Property | Value |
|---|---|
| Framework | Thentia Cloud SPA (Angular) — `portalus.thentiacloud.net` |
| Subdomain | `nsbom` (Nevada State Board of Osteopathic Medicine) |
| Routing | Hash-based (`#/register/`) |
| Grid library | Bootstrap table via Angular data-binding |
| **Archetype** | `thentia_cloud` |

This board uses the same Thentia Cloud `portalus` platform as **NV_CHIRO** (`nvcpbn.portalus.thentiacloud.net`) and **NV_PT** (`nptb.portalus.thentiacloud.net`). The `nsbom` subdomain is unique to NSBOM. The `portalus` sub-platform differs from the older `*.us.thentiacloud.net` variant used by NV_MEDBOARD and NV_NVADGC — notably it **does not render a Search-By dropdown**, relying instead on a single keywords input.

---

## Search Interface

Single text input (`input#keywords`) — no "Search By" dropdown:

| Field | Selector | Notes |
|---|---|---|
| Keywords | `input[id='keywords']` | Accepts last name, first name, or license number |

Search button: `button.btn-brand`

The engine uses the same `input#keywords` for all three search modes; the caller passes the query value and the board returns all matching records regardless of match type. To search by license number only, enter the exact license number (e.g., `DO12345`).

---

## Results Table

After search, a Bootstrap `table.table` appears with results rendered by Angular. The table headers and row layout match the NV_CHIRO portalus pattern:

| Col # | Header | Field key | Notes |
|---|---|---|---|
| 0 | License # | `license_number` | Board-assigned DO number |
| 1 | First Name | `first_name` | |
| 2 | Last Name | `last_name` | |
| 3 | City | `city` | From licensee address |
| 4 | License Type | `license_type` | e.g., "Doctor of Osteopathic Medicine" |
| 5 | Status | `status` | See status values below |
| 6 | Expiration Date | `expiration_date` | MM/DD/YYYY |
| 7 | Action | *(View button)* | `a.btn-single` — triggers detail navigation |

Row selector: `table tbody tr`

---

## Detail Page (separate URL — URL change on click)

Clicking `a.btn-single` navigates to `#/register/profile/<licenseId>` — a **full URL change**, not an inline panel. The engine uses `browser_back` to return to results after extraction.

### Top-level fields (dt/dd and field-label/field-value patterns)

The detail page renders via dt/dd pairs and CSS class pairs (`field-label` + `field-value`):

| Label | Maps to | Notes |
|---|---|---|
| License Number / License No | `license_number` | |
| First Name / Legal First Name | `first_name` | |
| Last Name / Legal Last Name | `last_name` | |
| Middle Name | `middle_name` | May be absent |
| License Type / License Category | `license_type` | |
| License Status / Status | `status` | |
| Issue Date / Original Date of Licensure / Initial License Date | `issue_date` | |
| Effective Date | `effective_date` | |
| Expiration Date / Exp Date | `expiration_date` | |
| Address / Public Address / Business Address | `address` | |
| City / Public City / Business City | `city` | |
| State / Public State | `state_code` | |
| Zip / Zip Code / Public ZIP Code | `zip_code` | |

### Sub-tables (header_mapped_table sections)

| Section name | Engine field | Typical columns |
|---|---|---|
| Board Actions | `disciplinary_actions` | Action Date, Action Type, Description |
| Place of Practice | `practice_locations` | Practice Name, Address, City, State, Zip |
| Education History | `education_history` | School, Degree, Graduation Date |
| Malpractice Information | `malpractice_info` | Date, Settlement Amount, Description |
| Specialties | `specialties` | Specialty Name, Certification Body |

---

## Pagination

Standard Thentia Cloud next-button pagination:
- Next link: `a[title='Next Page']`
- Disabled on last page: link is absent or has class `disabled`

---

## Status Values

| Raw value | Normalized |
|---|---|
| Active | active |
| Clear/Active | active |
| Current | active |
| Inactive | inactive |
| Retired | inactive |
| Expired | expired |
| Lapsed | expired |
| Suspended | suspended |
| Suspended-Non Renewal | suspended |
| Suspended-Board Action | suspended |
| Restricted | suspended |
| Revoked | revoked |
| Revoked-Non Renewal | revoked |
| Revoked-Board Action | revoked |
| Voluntary Surrender | revoked |
| Voluntary Surrender-Board Action | revoked |
| Probation | probation |
| On Probation | probation |

---

## No-Results Behavior

When no records match the query, the `tbody` contains a single row with text "No Records Found" (or similar). Detected via `no_results_indicators` in config.

---

## Edge Cases

1. **Middle name** — may appear as "Legal Middle Name" or be absent entirely; always include in `field_map` and map to `middle_name`.
2. **Address visibility** — disciplinary cases may hide address fields; extract what's available and leave blanks as `null`.
3. **Thentia slow load** — `portalus.thentiacloud.net` can take 5–10 s on first load; `timeout_ms: 30000` is set for results_wait.
4. **Back-navigation staleness** — after `browser_back`, the Angular router re-renders results; wait `1500 ms` before re-querying DOM rows.
5. **Date formats** — detail page may use `%b-%d-%Y` (e.g., "Jan-15-2024") in addition to standard `%m/%d/%Y`.

---

## Test Commands

```bash
# Dry-run against fixture
python run.py --config sites/NV_OSTEO/config.yaml --query "Smith" --mode last_name --dry-run

# Live smoke test (use a known active DO license number from NV)
python run.py --config sites/NV_OSTEO/config.yaml --mode license_number --query "DO1234"

# Headed mode for debugging
python run.py --config sites/NV_OSTEO/config.yaml --query "Smith" --mode last_name --headed
```

---

## Closest Reference Config

`sites/NV_CHIRO/config.yaml` — same `portalus.thentiacloud.net` platform, same selectors, same extraction strategies. Any selector failures on NV_OSTEO should first be validated against NV_CHIRO to isolate platform-level vs board-specific issues.
