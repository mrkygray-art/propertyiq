# Institutional AI Roadmap: gap analysis and plan (2026-10-11)

Compares the roadmap's features (F01–F15) with what already exists (see `docs/architecture/current-state.md`). It recommends extending existing work rather than duplicating it.

## Feature overlap and gap matrix

| ID | Feature | Status | What exists | Main gap |
|---|---|---|---|---|
| F01 | Debt and loan dashboard | **Built 2026-10-11** (`docs/debt-dashboard.md`) | Tier 1 engine: DSCR, LTV, debt yield, break-even, stress for one deal; Tier 2 underwritten case | No portfolio loan records, covenants, maturities, rate caps or charts |
| F02 | Asset performance cockpit | Partial | TTM NOI vs budget, occupancy, YoY, asset-class table, 36-month charts, calculation trail | NOI waterfall, variance heatmap, opex per unit/sq ft, collections, returns (no cash flows exist) |
| F03 | Risk and concentration | Minimal | Asset-class mix | Region, tenant, lender, maturity concentration; DSCR-LTV quadrants |
| F04 | Cash flow and stress testing | Partial | Tier 1 annual stress scenarios and rate × vacancy grid (one deal) | Monthly portfolio forecast, saved scenarios, tornado/fan charts, refinancing gaps |
| F05 | Lease rollover and tenants | Partial | Tier 2 rent roll intake (multifamily units, expirations, loss to lease) | Portfolio lease and tenant model, WALT, tenant concentration. Tenant names are deliberately dropped today, so synthetic tenants would need explicit permission controls. |
| F06 | AI Engineering Lab | Partial | Chat shows facts sent, number-check result, citations and model; verified-payload page with hash; architecture and security docs | Persisted request traces, trace UI, model and tool registries, schema explorer, offline-verification tab |
| F07 | AI evaluation and model comparison | Minimal | pytest live-model tests; measured latencies in `model-evaluation.md` | Versioned gold set, eval runs, p50/p95, model comparison charts |
| F08 | Data lineage and schema explorer | Partial | Per-property calculation trail with record IDs; seed manifest hash | ER view, data dictionary, metric definitions table, freshness |
| F09 | Committee report builder | Partial | Tier 1 one-page PDF; Tier 2 multi-page memo PDF with sources and disclaimer | Investment vs lender templates, immutable snapshots, "SYNTHETIC DATA / DEMONSTRATION" watermark |
| F10 | Watchlist and workflow | Partial | Deal flags with dismiss-with-note | Assignees, queue, covenant due dates, audit history |
| F11 | Underwriting vs actuals | Partial | Borrower vs underwritten at screening | Drift over time against actuals, vintages |
| F12 | Data quality and ingestion | Partial | Tier 2 intake: validation, reconciliation, review/approve, delete; spreadsheet formulas are never executed | Portfolio CSV import with dry run, quarantine, idempotency, rollback; quality dashboard |
| F13 | Multi-fund | Partial | Two funds with SQL-enforced grants | Ownership shares, consolidated vs fund views, no-double-count tests |
| F14 | Market comparison | Mostly done | Tier 2 offline synthetic comps with provider, dates, source IDs, stale labels | Portfolio-level benchmark snapshots |
| F15 | Geospatial | None | Market labels only | Synthetic geocodes and offline map assets |

## Prerequisites the roadmap requires first

1. **Backups (Phase 0).** Nothing is backed up today. Add a `pg_dump` script with a restore test and an off-machine copy of the repo. This takes minutes and protects everything else.
2. **Permissions (needed before Phase 1 shows anything restricted).** Loan screening data is shared by all users today, so the roadmap's rule ("authorization implemented and tested before restricted documents or fund data") isn't met yet for Tiers 1 and 2. Proposal:
   - **Roles:** add `viewer`, `analyst`, `credit_officer` and `admin` roles.
   - **Fund scoping:** give each screening deal a fund, and scope it like the portfolio.
   - **Guest:** keeps full access (owner's decision); it has its own password.
   - **Audit:** add an access audit table.
3. **Offline proof.** Add Windows Firewall outbound block rules for Ollama, Python, Node and Docker, then run the benchmark prompts and capture connection and firewall logs. This needs admin approval on this machine.

## Recommended synthetic lending schema (design only)

These are new tables. Existing tables stay; `portfolios` already plays the role of `funds`.

```text
loans                 loan_id (text PK, e.g. LN-0001), fund_id -> portfolios, lender (synthetic), status,
                      original_principal, current_principal, rate_type (fixed|floating), fixed_rate,
                      index_name, spread_bps, amortization_months, io_months, origination_date,
                      maturity_date, balloon_amount, as_of_date, is_synthetic CHECK true
loan_collateral       loan_id -> loans, property_id -> properties, allocation_pct  (many-to-many)
loan_terms_history    loan_id, effective_from, effective_to, rate/spread/amortization snapshot
loan_debt_service     loan_id, period_end, scheduled_interest, scheduled_principal, paid  (derived and stored)
loan_covenants        covenant_id, loan_id, kind (min_dscr|max_ltv|min_debt_yield|...), threshold,
                      test_frequency, noi_definition (labeled), effective_from/to
covenant_observations covenant_id, period_end, value, threshold, status, inputs jsonb, source record IDs
rate_caps             loan_id, strike, expiry_date, notional
property_valuations   property_id, as_of_date, value, method, assumptions jsonb, is_synthetic
tenants / leases / lease_rent_schedules   synthetic, with permission-controlled sensitive fields
scenarios / scenario_inputs / scenario_results / cashflow_forecasts
request_traces / model_registry / evaluation_cases / evaluation_runs / evaluation_results
access_audit / roles / fund_grants / property_grants
```

**Design rules:**
- every row carries `as_of_date` or `effective_from/to` dates, a source identifier and `is_synthetic`;
- loan ↔ property is many-to-many through `loan_collateral`;
- covenant thresholds belong to each loan, not one global setting;
- DSCR's NOI numerator is stored as a labeled definition;
- a zero or missing denominator gives "not calculable" with a reason, as the existing engine already does.

## Prioritized plan

| Phase | Scope | Exit test |
|---|---|---|
| 0 | Backups and a restore test; this report | Restore a dump into a scratch database and get the same row counts and seed hash |
| 0.5 | Roles (viewer/analyst/credit officer/admin), fund-scoped screening deals, access audit table | Cross-fund leakage tests on deals, documents, exports, chat and aggregates; viewer can't write |
| 1 | AI Engineering Lab: persisted sanitized request traces (no secrets, no hidden reasoning, retention setting), trace timeline UI, model registry (read from Ollama), tool registry (from code), schema explorer and data dictionary, offline-verification tab with captured evidence; same question with model off, on and offline | A trace for each demo question reproduces its SQL ID, formula, record IDs, model and validation; offline run with firewall logs |
| 2 | Synthetic loans and covenants, debt cockpit (maturity wall, DSCR trend with covenant line, DSCR × LTV bubble, debt yield distribution, fixed vs floating) | Every loan metric reconciles to fixture expected values; drill-down shows inputs, formula, source IDs and as-of date |
| 3 | NOI waterfall, variance heatmap, lease rollover ladder, concentration heatmaps, underwriting drift, data-quality dashboard | Waterfall bridges sum exactly; concentration denominators labeled |
| 4 | Deterministic monthly scenarios, assumption registry, committee report PDF with watermark | Scenario reruns reproduce identical outputs from saved inputs |
| 5 | Gold evaluation set (50+ cases), model comparison (installed local models only), regression dashboard, multi-fund ownership | Reported accuracy always shows sample size and grading method |

## Tests that will prove local/offline operation and financial correctness

- **Offline:**
  1. Block outbound traffic with firewall rules for `ollama.exe`, `ollama app.exe`, the venv `python.exe`, `node.exe` and `com.docker.backend.exe`.
  2. Run the 7 demo scripts.
  3. Capture `Get-NetTCPConnection` before and after, plus the firewall log (`pfirewall.log`).
  4. Expect only loopback traffic and recorded blocked attempts.
- **Model on vs off:** stop Ollama mid-run. Every answer must fall back to the verified summary with identical numbers.
- **Financial:** loan fixtures with independently computed DSCR, LTV, debt yield and balloon (plain-float reference, as in Tier 1), covenant boundary tests (exactly at threshold), zero/missing denominators, many-to-many collateral allocation, and maturity bucketing at period edges.
- **Permissions:**
  - restricted user against loans, deals, documents, traces, exports, cached results and aggregates;
  - viewer write attempts are refused;
  - every refusal is written to the audit table without secrets.
- **Traces:** contain no passwords, connection strings or reasoning text, and nothing outside the user's scope.
- **Evaluation:** a gold set with numeric assertions, prohibited claims and expected citations; results are reported with sample size.

## Expected new pages and charts

| Page | Charts and content |
|---|---|
| `/lab` | Tabs: Architecture, Request Trace, Model Registry, Tool Registry, Data Schema, RAG Evidence, Security Boundary, Evaluation, Offline Verification |
| `/debt` | Maturity wall, DSCR trend with covenant line, DSCR × LTV bubble, debt yield distribution, fixed vs floating, covenant exceptions timeline; loan detail drill-down |
| `/performance` | NOI waterfall, budget-vs-actual small multiples, variance heatmap, opex per unit/sq ft |
| `/risk` | Concentration heatmap (asset class × region), DSCR-LTV quadrant watchlist |
| `/scenarios` | Scenario editor, fan/tornado charts, cash flow bridge |
| `/leases` | Rollover ladder, tenant concentration Pareto |
| `/reports` | Committee report builder |

Charts stay as custom SVG: no new chart library, nothing loaded from the internet, and the same accessible patterns as today (legend, labels, keyboard and table views).

## Owner decisions (2026-10-11)

| Question | Decision |
|---|---|
| Guest | Full access, not read-only. It has its own password, so the owner's isn't shared. |
| Admin | A separate test `admin` account with a security checklist and internet lockdown. **Done:** see `docs/admin-lockdown.md`. |
| Firewall rules | Approved. Built as lockdown modes that need Windows' permission prompt for each change. |
| Backups | Approved, with a restore test. **Done:** `scripts/backup.py`; local folder outside the repo for now. |
| Roles, fund-scoped screening deals, access audit | Approved (Phase 0.5) |
| Traces | Stages, IDs and timings only, no prompt text; 30-day retention |
| Phases | Phase 1 (AI Engineering Lab), then Phases 2–5 |

## Original open questions

1. **Host OS.** The roadmap says Ubuntu, but the running system is Windows 11. Should a Linux deployment be planned, or should the docs describe Windows as it is?
2. **Backups.** Where should the off-machine copy go: an external drive, or a private GitHub repo? A GitHub repo would put the code online, but not the data or `.env`.
3. **Guest.** Should it become read-only now, before it's used?
4. **Screening deals.** Should they be fund-scoped? This changes how the Tier 1 and 2 pages behave for the `restricted` user.
5. **Firewall rules.** May I add Windows Firewall rules for the offline proof? This needs admin rights, and the rules are reversible.
6. **Trace storage.** Default retention, and should prompt text be stored? The proposal is no prompt text, only stages, IDs and timings, kept for 30 days.
