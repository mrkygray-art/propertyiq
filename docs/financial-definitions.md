# Financial definitions

The SQL and Python code in `services/api/propertyiq/finance.py` is the only source of these numbers. The language model explains them but never calculates them.

## Periods

- **Reporting period:** a quarter end (2025-09-30 through 2026-09-30 in the seed data). 2026-09-30 is the default.
- **Annual (TTM):** the trailing 12 months ending on the reporting period. For 2026-09-30 that is October 2025 through September 2026. The budget uses the same 12 months.
- **Prior TTM:** the 12 months before that (October 2024 through September 2025).
- A metric needs all 12 months present. If any month is missing, the metric is left empty and the reason is listed under `data_issues`.

## Metrics

| Metric | Definition | Policy for edge cases |
|---|---|---|
| NOI | Operating revenue − operating expenses, summed over the TTM. Excludes financing costs and capital expenditures, which aren't stored. | — |
| Budget variance | Actual TTM NOI − budget TTM NOI. Negative means under budget. | — |
| Budget shortfall | Budget − actual when positive, otherwise 0. **This is not a forecast loss.** | — |
| Actual-to-budget % | Actual ÷ budget × 100 | Not computed when the budget is zero or negative |
| NOI YoY % | (current TTM NOI − prior TTM NOI) ÷ \|prior TTM NOI\| × 100 | Not computed when prior TTM NOI is zero or negative |
| Occupancy | **Physical** occupancy: occupied size ÷ rentable size at the period's month end. Size is square feet for office, retail and industrial, and units for multifamily. | Not computed when the month-end record is missing |
| Cap rate, DSCR | **Not calculated.** They need property values, debt service and approved definitions, which don't exist yet. | — |

Portfolio-level occupancy is shown per asset class, weighted by size. Square feet and units are never added together. The portfolio summary also shows a simple average of property occupancy, labeled as such.

## Underperformance rules (version `2026-10-11.1`, in `rules.json`)

A property is flagged if **any** rule is triggered:

| Rule ID | Triggers when |
|---|---|
| `occupancy_below_min` | Occupancy < 85% (strict, so exactly 85% is not flagged) |
| `noi_yoy_decline` | NOI YoY ≤ −10% (inclusive) |
| `noi_below_budget` | Actual NOI ≤ 90% of a **positive** budget (inclusive) |

- **When a rule can't be evaluated:** each rule reports `triggered`, `not_triggered` or `not_evaluated` with a reason. A property with a missing input can still be flagged by its other rules, and it is listed under `incomplete_data`.
- **Rounding:** comparisons use exact decimal values. Rounding to 2 places happens only for display.

**Priority:** flagged properties are ranked by number of rules triggered (most first), then budget shortfall (largest first), then property ID.

## Required demo facts (2026-09-30)

| Property | Occupancy | Budget | Actual | YoY | Shortfall | Rules triggered |
|---|---:|---:|---:|---:|---:|---|
| PIQ-001 Westlake Office Center | 72% | $900,000 | $720,000 | −18% | $180,000 | all three |
| PIQ-002 Riverside Retail Plaza | 79% | $800,000 | $680,000 | −12% | $120,000 | all three |
| PIQ-003 Harbor Industrial Park | 91% | $600,000 | $530,000 | −9% | $70,000 | budget only |
| PIQ-004 Sunset Multifamily | 88% | $400,000 | $350,000 | −6% | $50,000 | budget only |
| **Total** | | | | | **$420,000** | |

**How YoY is seeded:** YoY is a fixed fact from the brief, so the generator works backward to the prior-year NOI that reproduces it (for example, $720,000 ÷ 0.82 = $878,048.78). The computed YoY then matches the brief to within 0.000001%. Monthly actuals follow a smooth trend through each year's total, so there's no artificial step at the October fiscal-year boundary. Budgets are set per year. The 51 other properties are generated with margins: occupancy ≥ 89%, YoY ≥ −6%, and actual ≥ 95.5% of budget. Tests check that none of them is flagged at any quarter end.
