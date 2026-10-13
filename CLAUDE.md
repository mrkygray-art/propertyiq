# PropertyIQ: notes for Claude Code

A local-only proof of concept on synthetic data, built for a potential enterprise client. Read `docs/architecture.md` and `docs/security-boundary.md` before changing behavior.

## Hard rules

- **No hosted inference, embedding or analytics services.** Ollama on loopback only. Never add a hosted AI SDK or a CDN script or font.
- **Never print, log or commit `.env` values.** Check whether a key exists; don't show its value.
- **The model never does math and never gets SQL.** New questions get a new vetted function in `analytics.py` that joins `user_portfolio_access`.
- **Don't change the four required properties** in `db/seeds/required_properties.json`, or the requirement of 55 properties with 4 flagged and a $420,000 shortfall, without Ky's say-so.
- **Ask Ky before** pulling large models, installing big software, or deleting or replacing containers and volumes.

## Loan screening module

- **Where it lives:** `services/api/propertyiq/screening/` and `apps/web/app/screening/`. Read `docs/loan-screening.md` first.
- **Add only:** don't change portfolio features for it. It hooks in at the end of `main.py` and with one line in `Shell.tsx`.
- **Off switch:** `PIQ_SCREENING_ENABLED=false` removes it.
- **Excel export:** formulas are checked by recalculating in OpenOffice (`scripts/office_recalc.py` runs under OpenOffice's Python 2.7). Keep `export_xlsx.py` formulas in step with `calc.py`.

## Tier 2 (document intake and underwriting)

- **Where it lives:** `screening/intake/`, `workspace.py`, `tier2_routes.py`, `market.py`, `ca_flags.py`, `compare.py` and `export_deal.py`. Read `docs/document-intake.md` first.
- **Privacy:** tenant names are dropped at read time. Never add a code path that stores, shows or sends them.
- **Rules:** California rules live in `screening_rules` with source URLs and verified dates. Don't hard-code ordinance details; mark anything unconfirmed as unverified.
- **Fixtures:** regenerate with `scripts/make_screening_fixtures.py`. The planted problems are listed in `tests/screening_fixture_data.py`.
- **Commit messages with quotes:** use `git commit -F <file>`, because PowerShell 5.1 splits quoted arguments.

## Debt dashboard

- **Where it lives:** `services/api/propertyiq/lending/` (calc, store, service, seed, routes) and `apps/web/app/debt/`. Read `docs/debt-dashboard.md` first.
- **Ratios aren't stored.** Balances, debt service and ratios are computed in `calc.py` from the loan terms. Every loan query joins `user_portfolio_access`.
- **Reseeding:** `scripts/seed.py` reloads loans too (reloading properties cascades to them); `scripts/seed_loans.py` reloads only loans.

## AI Engineering Lab

- **Where it lives:** `services/api/propertyiq/lab/` and `apps/web/app/lab/`. Read `docs/ai-lab.md` first.
- **Traces store no question, prompt or answer text** (owner decision). Add trace fields as IDs, counts or timings only, and extend the marker test in `tests/test_lab.py`.
- **Writer login:** `piq_trace_writer` writes traces; setup is `scripts/lab_setup.py`.

## Roles, fund scope and the access audit (Phase 0.5)

- **Where it lives:** `services/api/propertyiq/access.py` (role table), `audit.py`, and the checks in `screening/tier2_routes.py` and `screening/routes.py`. Read `docs/access-control.md` first.
- **Every new route** takes the signed-in user. Deal-related routes call `scoped(...)` (fund check, then role check); lender-policy writes call `access.require(user, "policy", ...)`. Audit successful changes with `audit.allowed`.
- **Another fund's record answers 404**, the same as a missing one, never 403.
- **Audit fields are IDs and labels only.** Never store passwords, tokens, notes, document contents or other typed text. Extend `test_sign_ins_are_audited_without_the_attempted_text` if you add fields.
- **Writer login:** `piq_audit_writer` (append and prune only); setup is `scripts/access_setup.py`.

## Admin tools

- **Where it lives:** `services/api/propertyiq/admin/`, `apps/web/app/admin/`, `scripts/lockdown.ps1` and `scripts/backup.py`. Read `docs/admin-lockdown.md` first.
- **No admin rights in the API.** The API never runs elevated: lockdown changes go through Windows' permission prompt. Keep the mode list fixed; never build the PowerShell command from request text.
- **Firewall rules** stay in the group "PropertyIQ Lockdown". "Whole computer" mode cuts remote access, so don't run it while Ky is connected remotely.
- **Checklist items** return evidence, never secret values. Add a test in `tests/test_admin.py` for any new check that could leak.

## Commands (from the repo root)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1   # API + web, opens the browser
cd apps\web; npx tsc --noEmit; npm run build             # web type check and build
.\.venv\Scripts\python.exe -m pytest -q          # all tests; DB/LLM tests skip if unavailable
.\.venv\Scripts\python.exe scripts\seed.py       # reload synthetic data (deterministic)
.\.venv\Scripts\python.exe scripts\migrate.py    # apply new db/migrations/*.sql
```

## Machine quirks

- **Docker CLI location.** It's at `%LOCALAPPDATA%\Programs\DockerDesktop\resources\bin\docker.exe`, which older shells may not have on PATH.
- **Base image changes.** The pgvector image uses glibc 2.36. If you change the base image, check for a collation version mismatch.
- **Chat model.** `qwen3:4b-instruct-2507-q4_K_M`. Don't switch back to `qwen3:4b`: that's the Thinking-2507 build, which takes 1–4 minutes per answer and writes its reasoning into the answer when `think: false`. The adapter sends `think: true` to any model that lists the `thinking` capability.
- **Proxy timeout.** Next.js's proxy timeout is raised to 5 minutes in `next.config.ts` (`experimental.proxyTimeout`) for slow local inference.
