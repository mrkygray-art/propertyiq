# Debt dashboard (synthetic loans and covenants)

`/debt` gives a portfolio view of debt, and `/debt/LN-0001` (and the other loan IDs) shows one loan in detail. Every loan, lender and index fixing is synthetic.

## What's on the page

| Section | Shows |
|---|---|
| Headline figures | Outstanding debt, floating share, weighted rate, portfolio DSCR (total NOI ÷ total debt service), portfolio LTV, covenant breaches and watch count |
| Maturity wall | Principal due at maturity by year, fixed vs floating, with a table view |
| DSCR × LTV risk map | One bubble per loan (size = balance, colour and legend = worst covenant result), with guides at 1.25× and 70%; click to open the loan |
| Covenant watchlist | Breaches first, then loans within 5% of a threshold, with each test's actual vs required value |
| Portfolio DSCR by quarter | 9 quarter-ends, same loans |
| All loans | Filter by breach, watch, fixed or floating |
| Loan detail | Covenant tests, terms (rate, cap, repayment, maturity, balloon), DSCR and LTV by quarter against the covenant line, collateral, calculation trail with source record IDs and payload SHA-256 |

## Data (migration 0008, seeded by `scripts/seed_loans.py` and by `scripts/seed.py`)

- **Tables:**
  - `lenders` and `loans` (each loan belongs to one fund);
  - `loan_collateral` (many-to-many, with the share of each property pledged);
  - `loan_covenants` (per loan: minimum DSCR, maximum LTV, minimum debt yield);
  - `rate_caps`;
  - `index_rates` (`SYN-SOFR`, a made-up monthly series);
  - `property_valuations` (annual direct capitalization of trailing 12-month NOI).
- **Loans:** 46 loans: 31 fixed and 15 floating.
  - **Pool loans:** one per fund, each secured by 3 properties.
  - **Split property:** one Fund I property is financed by a 60% senior note and a 40% junior note.
  - **Unlevered properties:** a few properties have no debt.
- **Required properties:** the four required underperformers (Westlake, Riverside, Harbor, Sunset) carry floating loans with tighter covenants. All four breach at 2026-09-30, alongside three other loans.
- **Determinism:** the seed is deterministic (fixed random seed) and is built from the seeded properties' actual NOI.
- **What isn't stored:** balances, debt service and ratios. They're computed on request from the loan terms, so there is one source of truth.

## Calculations (`services/api/propertyiq/lending/calc.py`, Decimal)

- **Payments:**
  - **Timing:** monthly, in arrears.
  - **Fixed loans:** interest only for `io_months`, then a level payment over `amortization_months`.
  - **Floating loans:** interest only. The rate is `(min(index, cap strike while the cap is in force) + spread)`.
- **Debt service:** the 12 monthly payments ending at the period, the same window as NOI.
- **Ratios:** DSCR = NOI ÷ debt service; LTV = balance ÷ value; debt yield = NOI ÷ balance.
- **NOI and value:** NOI and value come from each collateral property times its pledged share. The value is the latest valuation on or before the period.
- **Covenants:**
  - Tested at 4 decimal places.
  - Exactly at the threshold passes; within 5% is "watch".
  - A missing or zero input gives "not calculable" with a reason.

## Security

- **Sign-in:** every route needs a signed-in user.
- **Fund scope in SQL:** every query joins `user_portfolio_access` on the loan's fund (and on the property's fund, for NOI and valuations).
- **Restricted user:** sees only Fund II loans.
- **Hidden loans:** a Fund I loan returns the same 404 as a loan that doesn't exist.
- **Totals:** computed only from the user's loans.

## Tests (`tests/test_debt.py`)

- **Payments and balances:** checked against a plain-float amortization reference.
- **Debt service:** IO then amortization; rate cap before and after expiry; 12-payment debt service.
- **Covenant edges:** exactly at threshold, and just inside or outside it.
- **Missing inputs and new loans:** not calculable.
- **Collateral:** allocation splits NOI and value.
- **Fund scope:** fund-scope leakage and identical 404s; sign-in required; bad IDs and periods.
- **Reconciliation:**
  - totals and the maturity wall reconcile with the loans;
  - the detail page matches the dashboard row;
  - one floating loan's DSCR is recomputed straight from the raw tables.
