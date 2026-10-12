# Tier 2: document intake, market support, California red flags, underwriting comparison

Tier 2 takes over the slow, manual part of underwriting. You upload a borrower's rent roll and T-12, PropertyIQ reads and checks them, and you compare the borrower's numbers with your own underwritten version, with a reason for every change.

**This is screening and underwriting support only.** It isn't an appraisal, legal advice, or a credit approval, and every export says so. All data in this prototype is synthetic, and everything runs on this computer: OCR, the AI model and the database. Market comps come from an offline synthetic provider.

## The flow

Open **Loan screening → Deal workspace**, create a deal (or open the demo deal **Juniper Court Apartments (synthetic demo)**), then work through the steps:

1. **Upload.** A rent roll (multifamily only) and/or a T-12 (any property type), as Excel (.xlsx, .xls), CSV, or PDF (text or scanned), up to 10 MB each.
2. **Review.** Every extracted row shows its source (sheet and row, or page and line) and a confidence level. Rows to check are highlighted with the reason. Correct statuses, rents, amounts and categories in place.
3. **Approve.** Nothing from a document is used until you approve it.
4. **Flags.** Anomaly checks A1–A13 run on the approved data. Each flag has a severity, a plain explanation, the evidence numbers, where they came from, and a suggested action. You can dismiss a flag, but only with a note.
5. **Market.** Rent and sale comps with the provider, retrieval date, search area and each comp's source ID. Market data gets a "stale" label after a set number of days, and cities with no data show "No comps available".
6. **California.** Enter the property facts and a condition checklist to run red flags R1–R5. Every rule shows its official source and when it was verified, and the wording is always "may apply — verify".
7. **Compare.** The borrower's figures next to your underwritten ones. Flags can be turned into **suggested** adjustments; you approve, edit or reject each one, and every adjustment needs a reason code and a note. Both cases run through the Tier 1 engine, and a one-line summary is built from the numbers (not by the AI), for example: *"Underwriting adjustments reduce NOI by $178,195 (37.1%), moving DSCR from 1.58 to 1.00, which fails the 1.25 minimum."*
8. **Export.** A PDF memo and an Excel workbook whose underwritten case is driven by live formulas.

**Settings** (link at the top of the deal pages):
- **Expense benchmarks:** used by A4 and A5.
- **California rules:** each with a source link and the date it was verified.
- **Component typical life:** used by R5.
- **Other settings:** the example tax rate, the stale-data threshold, the M1 tolerances, and the example reserve per unit.

Shipped benchmarks and life values are labeled **example only — set your own**.

## Plain-language glossary

| Term | Meaning |
|---|---|
| Rent roll | Every unit with its status, rent and lease dates |
| T-12 | The last 12 months of income and expenses, month by month |
| Physical occupancy | Occupied units ÷ total units |
| Loss to lease | How far below market current tenants' rents are |
| Concessions | Discounts such as a free month |
| Non-recurring income | Money that won't repeat, such as an insurance payout |
| Owner-managed | No management fee on the T-12; a new owner would pay one |
| Capex | Big replacements (roof, HVAC), as opposed to routine repairs |
| Reserves | Money set aside each year for capex |
| Comps | Similar nearby properties used to judge rent or price |
| Prop 13 | California rule: a sale generally resets the taxable value to about the price |
| Rent control / RSO | Local laws limiting rent increases |
| Soft-story | A weak ground floor (often open parking) that can collapse in an earthquake |
| Phase I ESA | A standard environmental report on a site's history |
| Underwritten | The lender's own adjusted version of the borrower's numbers |

## The checks

**A: documents**

| ID | Fires when |
|---|---|
| A1 | T-12 vacancy differs from rent roll vacancy by more than 3 points |
| A2 | T-12 rent differs from the rent roll's in-place rent by more than 5% |
| A3 | One-time income (by label), or a month far above the line's usual month |
| A4 | An expense category per unit (or per sq ft) is below the benchmark low |
| A5 | Management fee is $0 or below the benchmark % of income |
| A6 | Taxes, insurance, repairs or utilities are missing or $0 |
| A7 | A repair line that looks like a capital item, or a large one-off repair month |
| A8 | Recent (T-3 or T-6 annualized) expenses differ from the T-12 by more than 10% |
| A9 | Recent income differs from the T-12 by more than 5% |
| A10 | Concessions or bad debt above 2% of gross potential rent, or rising |
| A11 | A unit's rent is more than 25% from its unit-type average |
| A12 | Duplicate units, a unit count that differs from the deal, missing or duplicated months, totals that don't add up |
| A13 | An occupied unit with no rent |

**M1, market:** the deal's price per unit is more than 20% above the sale-comp median, or its implied cap rate is more than 1 point below the comp median. The cap-rate check runs only when the source reports cap rates.

**R: California**

| ID | Checks |
|---|---|
| R1 | Prop 13 tax reset on a purchase. Estimate = price × example rate, compared with the T-12 tax line. |
| R2 | LA RSO (certificate of occupancy on or before Oct 1, 1978), Santa Monica (built before Apr 10, 1979), statewide AB 1482 (5% + CPI, at most 10%; under 15 years exempt; until 2030), or "check local rules" |
| R3 | LA soft-story retrofit, LA non-ductile concrete (UNVERIFIED), unreinforced masonry (UNVERIFIED), and the earthquake-zone map lookup |
| R4 | Lead paint (pre-1978), asbestos (1980 or earlier), past use of the site, and lookups for cleanup sites, fire zones, flood zones (UNVERIFIED link) and LA methane zones |
| R5 | Condition checklist against typical life, problem materials and brands, low repairs on an older building, and a total of your cost estimates |

**Rule sources:** all of these were looked up on official sites on 2026-10-11. The LA non-ductile concrete cutoff (confirmed only by trade press), the unreinforced masonry rule (the newest official report is from 2006), and the FEMA link (not checked) are marked UNVERIFIED and can be updated in Settings.

## Safety rules built in

- **Tenant privacy:** tenant names and contact details are dropped while the file is read. They're never stored, shown, exported or sent to the AI. Tests search the database and exports for every fake tenant name.
- **What the AI does:** the local model only suggests a category for T-12 labels the synonym list doesn't recognize. It's sent the label text only (no amounts), its answer must be a valid category, and every AI suggestion is marked low confidence for review. All arithmetic and flag decisions are plain, tested code.
- **Nothing is invented:** missing data shows "not available". Uncovered market areas show "No comps available". Hazard databases are links, because PropertyIQ doesn't query them.
- **Deleting an upload:** "Delete" removes the stored file and everything read from it.
- **Database access:** the screening database login can change only `screening_` tables.

## How correctness is proven

**Synthetic fixtures** live in `tests/fixtures/screening/` and are generated by `scripts/make_screening_fixtures.py`. There are two sets:

- **Problem set:** a 20-unit 1962 Los Angeles building with eight planted problems, as Excel, text PDF and scanned PDF.
- **Clean set:** the same building with nothing wrong, as Excel and CSV.

| Planted problem | Caught by |
|---|---|
| 10% vacancy on the rent roll vs 3% on the T-12 | A1 |
| $18,000 insurance settlement | A3 |
| $0 management fee | A5 |
| Insurance $200/unit and repairs $300/unit | A4 ×2 |
| $21,500 tax on a $6M purchase | R1: estimate $72,000, gap $50,500 |
| 1962 wood frame with tuck-under parking in LA | R2 and R3 |
| Missing June | A12 |
| Total Operating Expenses $1,000 off | Reconciliation warning with both numbers |

**The clean set raises no flags at all.**

**Other checks:**
- **Extraction:** Excel, CSV and text PDF give identical results. Scanned (OCR) gets at least 18 of 20 units exactly right and flags the rest as low confidence.
- **Comparison:** the underwritten NOI and DSCR are checked against a hand calculation from the fixture data. Proposed and rejected adjustments have no effect.
- **Excel:** the workbook is recalculated in OpenOffice and must match the app. Rejecting an adjustment inside the workbook changes NOI by exactly that adjustment.

## Turning it off

Tier 2 sits inside the loan screening module, so `PIQ_SCREENING_ENABLED=false` in `.env` (then restart the API window) turns off Tier 1 and Tier 2 together. The portfolio, property pages and chat keep working.

## Files

**New:**
- `services/api/propertyiq/screening/intake/`:
  - `schema.py`, `tables.py`, `rentroll.py`, `t12.py`, `mapping.py`, `extract.py`.
  - `calcs.py`, `anomalies.py`.
- `services/api/propertyiq/screening/`:
  - `workspace.py`, `tier2_routes.py`, `settings_store.py`.
  - `market.py`, `ca_flags.py`, `compare.py`, `export_deal.py`.
- `db/migrations/0003`–`0006` (Tier 2 tables, rules seed, comparison).
- `scripts/make_screening_fixtures.py`, `tests/fixtures/screening/`.
- `apps/web/app/screening/deals/` (list and workspace), `apps/web/app/screening/settings/`, `apps/web/lib/workspace.ts`.
- Tests: `tests/test_intake.py`, `test_tier2_api.py`, `test_tier2_m5_m7.py`, `test_tier2_compare.py`, `test_tier2_exports.py`.

**Changed (Tier 1 files):**

| File | Change |
|---|---|
| `screening/routes.py` | About 8 lines that mount the Tier 2 routes inside the screening router |
| `screening/export_xlsx.py` | The workbook build is split out (`build_workbook`) so Tier 2 can add sheets; the Tier 1 output is unchanged |
| `scripts/screening_setup.py` | Grants the screening login access to every `screening_` table |
| `app/screening/page.tsx` | One "Deal workspace" link |
| `app/screening/screening.css` | Tier 2 styles appended |
| `requirements.txt` | Six libraries, all needed for reading uploads |

| Library | Why |
|---|---|
| pdfplumber | PDF text with positions |
| pypdfium2 | Renders scanned PDF pages for OCR; detects password-protected PDFs |
| rapidocr-onnxruntime | Offline OCR |
| xlrd | Old `.xls` files |
| python-multipart | File uploads |
| Pillow | Images |

## Known limits

- **Rent rolls:** multifamily only.
- **Lease-level analysis:** no commercial lease abstraction or projections.
- **Rent control dates:** year built stands in for the certificate of occupancy date. Confirm on ZIMAS or with the city.
- **Market data:** comps are synthetic. A real provider (a Rentcast stub exists, off by default) would be the only feature that needs the internet.
- **Benchmarks:** benchmark and typical-life values are placeholders until you replace them.
- **OCR:** works on reasonably clean scans; handwriting, rotated pages or poor copies will need manual correction.
