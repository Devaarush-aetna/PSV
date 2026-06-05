# NV_DENTAL — Nevada State Board of Dental Examiners

**URL:** https://online.nvdental.org/#/VerifyLicense  
**State:** NV  
**Profession codes:** DMD, DDS  
**Captured:** 2026-06-03

---

## Platform Analysis

| Property | Value |
|---|---|
| Framework | AngularJS 1.x (`ng-app="natApp"`) |
| Hosting | Azure (20.141.96.100) |
| Routing | Hash-based (`#/VerifyLicense`) |
| Grid library | None — standard Bootstrap table via `ng-repeat` |
| **Archetype** | `ag_grid_spa` *(closest fit; not Thentia Cloud, no AG Grid)* |

This is **NOT Thentia Cloud** despite also being a Nevada board and using the same hash-based Angular routing. The app identifier is `natApp` (not Thentia's app). It uses AngularJS UI Bootstrap (uib-\*) components throughout.

---

## Search Interface

No "Search By" dropdown — three independent text inputs on the same form:

| Field | Selector | Mode key |
|---|---|---|
| Last Name | `input[name='LastName']` | `last_name` |
| First Name | `input[name='FirstName']` | `first_name` |
| License Number | `input[name='LicenseNumber']` | `license_number` |

Search button: `input[type='submit'][value='Search']`  
Reset button: `input[type='submit'][value='Reset']` (clears form and hides results)

---

## Results Table

After search, `div.searchoption` appears (ng-if toggles visibility). The results table is `table.verify-table`:

| Col # | Header | Notes |
|---|---|---|
| 0 | Credentials | "Dentist" or "Dental Hygienist" |
| 1 | Practitioner Name | "Last, First M , DDS" format |
| 2 | Speciality Details | Usually same as Credentials |
| 3 | Location | City, State — **blank for disciplinary cases** |
| 4 | Status | See status values below |
| 5 | Public Health | Usually blank |
| 6 | Action | "View Details" button |

Row selector: `table.verify-table tbody tr[ng-repeat-start]`  
Each result has two `<tr>`: the data row (`ng-repeat-start`) and a hidden detail row (`ng-repeat-end`).

---

## Detail Panel (INLINE — no page navigation)

Clicking "View Details" (`input[ng-click*='getLicenseDetails']`) toggles the sibling `tr[ng-repeat-end]` from `.ng-hide` → visible. **There is no URL change.** The detail content is loaded via `data-ng-include` from `/app/components/individual/license/verify/verify-license-detail.html`.

### Top-level fields (`table.view-details-table`)

Pattern: two-column table with `<label class="bold">Field :</label>` in left `<td>` and value `<td class="ng-binding">` on right.

| Label | Maps to | Notes |
|---|---|---|
| Full Name | `full_name` | "Last, First M, DDS" — parse with `split_name_with_suffix` |
| Office name | `office_name` | Blank for disciplinary |
| Primary Office Address | `address` | Blank for disciplinary |
| City, State Zip | `city_state_zip` | "City, ST 12345" — parse with `split_city_state_zip` |
| Office Phone | `phone` | |
| Graduated From | `school` | Full school name |
| Graduation Date | `graduation_date` | MM/DD/YYYY |

### Sub-tables within detail panel

**License Information** (primary):
- License # | Status | Original License Date | Expiration Date

**Permits** (secondary):
- Permit | Permit Number | Permit Status | Issue Date | Exp Date

**Board Action / Malpractice**:
- Action Type | Date | Document Link

### Closing the detail
Link: `a[ng-click*='isDetailsVisible=false']` (text "← Close detail")  
No browser_back needed — clicking this hides the panel.

---

## Pagination

Uses AngularJS UI Bootstrap `uib-pagination` component:
- Container: `div.mob-pages > ul.cus-pagination`
- Hidden when no results: `div[ng-hide="Pager.totalRecords==0"]` gets `.ng-hide`
- Page links: `li.pagination-page a`
- Prev/Next: `li.pagination-prev a` / `li.pagination-next a`
- First/Last: `li.pagination-first a` / `li.pagination-last a`
- Items per page: `select[ng-model='Pager.pageSize']` (options: 10, 20, 50, 100)

---

## Status Values (observed from license count table)

| Raw value | Normalized |
|---|---|
| Active | active |
| Inactive | inactive |
| Revoked-Non Renewal | revoked |
| Revoked-Board Action | revoked |
| Voluntary Surrender-Board Action | revoked |
| Suspended-Non Renewal | suspended |
| Suspended-Board Action | suspended |
| Retired | inactive |
| Disabled | inactive |

---

## No-Results Behavior

**No text message is displayed.** The results table appears with an empty `<tbody>` (only the `ng-repeat` comment, no `<tr>` elements). The pagination wrapper has `ng-hide` class when `Pager.totalRecords == 0`.

Detection selector: `table.verify-table tbody:empty` or checking `li.pagination-page` count = 0.

---

## Edge Cases

1. **Disciplinary cases** hide office address/location — the `ng-if` on those cells checks `LicenseStatusTypeName` for "Suspended-Board Action", "Revoked-Board Action", "Voluntary Surrender-Board Action". Location column in results is also blank for these.
2. **Name format** includes degree suffix after a comma: "Smith, Sydney M , DDS" — note the extra space before the comma and degree.
3. **Detail is pre-rendered** in DOM via `ng-include` even when collapsed. Selectors will match hidden rows — always filter by `.ng-hide` absence.
4. **Multiple pages** — search "Smith" returns multiple pages. Page 1 is active on load.

---

## Test Commands

```bash
# Dry-run against fixture
python run.py --config sites/NV_DENTAL/config.yaml --query "Smith" --mode last_name --dry-run

# Live smoke test
python run.py --config sites/NV_DENTAL/config.yaml --mode license_number --query "0591"

# Headed mode for debugging
python run.py --config sites/NV_DENTAL/config.yaml --query "Smith" --mode last_name --headed
```
