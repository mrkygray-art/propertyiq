# Loan screening module (Tier 1)

A lender enters a property and a loan request. PropertyIQ calculates the standard underwriting numbers, checks them against the lender's own guidelines, stress-tests the deal, and exports a one-page PDF and an Excel workbook with live formulas.

**This is a screening aid only.** It isn't an appraisal, a valuation opinion, or a credit approval, and every export says so. The module is separate from the portfolio analytics and uses no AI.

## Using it

1. Sign in, then click **Loan screening** in the header (or go to http://127.0.0.1:3000/screening).
2. **Load example** fills in spec test case A. Results update as you type.
3. Choose a guideline set at the top right. **Edit guidelines** lets you create, change or delete sets.
4. **Export PDF** and **Export Excel** download the current deal.

## The numbers, in plain words

| Term | Meaning | Formula |
|---|---|---|
| EGI | Income actually collected | (PGI + other income) × (1 − vacancy) |
| NOI | Earnings after running costs, before loan payments | EGI − operating expenses − reserves |
| Monthly payment | Standard loan payment | Loan × r ÷ (1 − (1 + r)^−n), with r = rate ÷ 12 and n = years × 12. If the rate is 0, it's Loan ÷ n. |
| Annual debt service | A year of loan payments | 12 × monthly payment |
| Interest-only debt service | Yearly payments when only interest is paid | Loan × rate |
| DSCR | How many times NOI covers the loan payments | NOI ÷ annual debt service |
| LTV | Share of the value that is borrowed | Loan ÷ value |
| Debt yield | Lender's return if it took the property | NOI ÷ loan |
| Cap rate (implied) | Property's yield at the stated value | NOI ÷ value |
| Break-even occupancy | How full it must be to pay costs and the loan | (Operating expenses + reserves + annual debt service) ÷ (PGI + other income) |
| Debt constant | A year of payments as a % of the loan | Annual debt service ÷ loan |

**Interest-only loans:** the screen shows both DSCRs. Pass/fail uses the amortizing DSCR unless the guideline set says otherwise.

**Stress tests:**
- **Rate and vacancy:** changes are added to the inputs.
- **"Income −10%":** collected income (EGI) is cut by 10%.
- **"Expenses +10%":** operating expenses are raised by 10%. Reserves are unchanged.
- **"Exit cap +1%":** the value is re-estimated as NOI ÷ (implied cap rate + 1 point), and LTV is recalculated against that value.

**Rounding:** amounts are calculated exactly and rounded only for display. Dollars are shown as whole numbers, ratios to 2 decimals, and percentages to 1 decimal (2 for cap rate and debt constant). Hover a grid cell to see the exact DSCR.

## How correctness is proven

`pytest` runs 75 screening tests. Expected values never come from the module's own code. They come from:

1. **The spec's numbers** for test cases A–D.
2. **An independent plain-math version** of each formula, written separately in the test file.
3. **OpenOffice:** each exported workbook is opened in OpenOffice without a window and every formula is recalculated (`scripts/office_recalc.py`). The results must match the engine to 6 significant digits for every metric, guideline result, stress scenario and grid cell. This proves the Excel formulas are live and correct, not pasted values.

**Spec cases** (expected values calculated independently):

| Case | Check | Result |
|---|---|---|
| A | Monthly payment | $25,282.72 |
| A | Annual debt service | $303,392.65 |
| A | DSCR | 1.3184 |
| A | Break-even occupancy | 78.90% |
| A | Overall | PASS |
| B | Interest-only debt service | $260,000 |
| B | DSCR | 1.5385 |
| C | DSCR at 7.5% | 1.1918, reads "DSCR 1.19 vs. minimum 1.25: FAIL" |
| D | Zero loan, 0% rate, negative NOI, break-even over 100%, bad inputs | Friendly messages; never NaN or infinity |

## Turning it off

Set `PIQ_SCREENING_ENABLED=false` in `.env` and restart the API window. The screening pages, API routes and header link disappear, and the portfolio, property pages and chat keep working. A test checks exactly this: `test_off_switch_removes_module_and_keeps_everything_else`.

To remove it completely:
1. Delete the files listed below.
2. Delete the three lines at the bottom of `main.py` and the `ScreeningNavLink` line in `Shell.tsx`.
3. Optionally drop the `screening_guideline_sets` table.

## Files

**New:**
- `services/api/propertyiq/screening/`:
  - `calc.py`: the formulas, guideline checks and stress tests.
  - `report.py`, `routes.py`, `store.py`, `textfmt.py`.
  - `export_pdf.py` and `export_xlsx.py`.
- `db/migrations/0002_screening.sql`: the `screening_guideline_sets` table and the example set.
- `scripts/screening_setup.py` (one-time setup) and `scripts/office_recalc.py` (OpenOffice recalculation for the tests).
- `apps/web/app/screening/`: `page.tsx`, `guidelines/page.tsx`, `layout.tsx`, `screening.css`.
- `apps/web/lib/screening.ts`, `apps/web/components/Help.tsx`, `apps/web/components/ScreeningNavLink.tsx`.
- `tests/test_screening_calc.py`, `tests/test_screening_exports.py`, `tests/test_screening_api.py`.

**Changed:**

| File | Change |
|---|---|
| `services/api/propertyiq/main.py` | 3 lines at the end mount the module when it's switched on |
| `apps/web/components/Shell.tsx` | 2 lines add the header link |
| `services/api/requirements.txt` | `reportlab` (PDF, BSD license) and `openpyxl` (Excel with real formulas, MIT license) |
| `.gitignore` | Ignores `logs/` |

## Storage and security

- **Storage:** guideline sets live in one table, `screening_guideline_sets`, and are shared by every signed-in user. Deals aren't saved.
- **Writes:** the API writes through `piq_screening_writer`, a database login that can change that table and nothing else. Every other query still uses the read-only login.
- **Tests:** they confirm that the read-only login can't write guideline sets, and that the screening login can't touch portfolio tables.
- **Sign-in:** every screening route requires it.

## Setting it up on another machine

Run this after the main setup in the README:

```powershell
.\.venv\Scripts\python.exe scripts\screening_setup.py
```

It adds `PIQ_SCREENING_ENABLED` and `PIQ_SCREENING_PASSWORD` to `.env`, creates the database login, applies the migration, and grants access.
