# Access control: roles, fund scope and the access audit (Phase 0.5)

Built 2026-10-12 from the owner decisions in [roadmap/gap-analysis.md](roadmap/gap-analysis.md). Code: `services/api/propertyiq/access.py` (rules), `audit.py` (log), `screening/tier2_routes.py` and `screening/routes.py` (checks), `admin/routes.py` (audit view). Tests: `tests/test_access.py`.

Two separate questions decide every request:

1. **What can you see?** Your fund grants (`user_portfolio_access`). Portfolio analytics, chat, the debt dashboard and now loan screening deals all filter by them.
2. **What can you do?** Your role.

## Roles

| Permission | Viewer | Analyst | Credit officer | Admin | Covers |
|---|:-:|:-:|:-:|:-:|---|
| `view` | ✓ | ✓ | ✓ | ✓ | Dashboards, chat, debt, deals, flags, the comparison, PDF and Excel exports, Tier 1 screening calculations |
| `work_deals` | | ✓ | ✓ | ✓ | Create, edit and delete deals; upload documents; review and approve the extracted figures; market and California checks; loan terms; propose adjustments and edit proposals |
| `decide` | | | ✓ | ✓ | Approve, reject or undo adjustments; edit or delete a decided adjustment; dismiss or reopen flags |
| `policy` | | | ✓ | ✓ | Lender guideline sets, expense benchmarks, California rules, the useful-life table, screening settings |
| `admin` | | | | ✓ | Security checklist, internet lockdown, the access audit |

The analyst/credit officer split follows the usual lending practice: the analyst prepares the underwriting, and a credit officer signs off on the adjustments and on any red flag being cleared. "Approve these figures" on an uploaded document stays with the analyst, because it confirms what was read from the file, not a credit decision.

**Refusals.** A role refusal returns 403 with a plain reason, for example "Your role (analyst) can't approve or reject adjustments or clear flags; that needs a credit officer." The web app hides the controls a role can't use, but the API enforces every rule on its own.

## Accounts

| Username | Role | Funds | Password setting |
|---|---|---|---|
| `analyst` | Analyst | Fund I and Fund II | `PIQ_DEMO_PASSWORD` |
| `restricted` | Analyst | Fund II | `PIQ_DEMO_PASSWORD` |
| `guest` | Credit officer (full access, owner decision) | Fund I and Fund II | `PIQ_GUEST_PASSWORD` |
| `admin` | Admin | Fund I and Fund II | `PIQ_ADMIN_PASSWORD` |
| `viewer` | Viewer | Fund I and Fund II | `PIQ_VIEWER_PASSWORD` |
| `credit1` | Credit officer | Fund I and Fund II | `PIQ_CREDIT_PASSWORD` |
| `credit2` | Credit officer | Fund I | `PIQ_CREDIT_PASSWORD` |
| `credit3` | Credit officer | Fund II | `PIQ_CREDIT_PASSWORD` |
| `credit4` | Credit officer | Fund I and Fund II | `PIQ_CREDIT_PASSWORD` |

The old `restricted_analyst` role is gone: `restricted` is an analyst whose grants cover one fund. Set a password you'll remember with `scripts/set_demo_password.py --user viewer`, or `--user credit` for all four credit officers at once.

## Fund-scoped deals

- **Every deal belongs to one fund** (`screening_deals.fund_id`). The demo deal, Juniper Court Apartments, is in **Fund II**, so `credit2` (Fund I only) can't see it.
- **Creating a deal:** someone with one fund gets that fund automatically. Someone with both must choose; the API refuses a fund they aren't granted (403, audited).
- **Another fund's records look missing.** A deal, document, flag or adjustment in a fund you aren't granted returns the same 404 as one that doesn't exist, for reads and writes alike. A 403 would confirm the record exists. The fund check runs before the role check for the same reason.
- **Moving a deal** to another fund isn't supported.
- **Shared on purpose:** guideline sets, benchmarks, rules and settings are lender policy, the same for every fund. Everyone can read them; only credit officers and admins change them.
- **No foreign key** from `fund_id` to `portfolios`, because `scripts/seed.py` reloads portfolios with `TRUNCATE ... CASCADE`, which would delete every deal. The API validates the fund against the user's grants instead.

## Access audit

Table `access_audit`, written only by the `piq_audit_writer` database login, which can add rows and delete old ones but can't edit them or touch any other table. The Admin page shows it under **Access audit**, with a "Refused only" filter. The checklist reports whether the writer is limited to that table and whether events are arriving.

| Recorded | Example `action` |
|---|---|
| Every sign-in, successful or not | `auth.sign_in` |
| Every refusal, by role or by fund | `adjustment.decide` refused, reason `role` |
| Every change that succeeds | `deal.create`, `document.approve`, `flag.decide`, `policy.guidelines` |
| Every deal export | `deal.export_pdf`, `deal.export_xlsx` |
| Admin actions and refusals | `admin.lockdown`, `admin.checklist` |

- **Fields:** time, username, role, action, allowed or refused, reason (`role`, `fund` or `sign_in`), record type and ID, and fund.
- **Never stored:** passwords, tokens, document contents, notes, or anything else typed into the app. A sign-in for a username that doesn't exist is stored as `(unknown)`, because people sometimes type their password into the username box.
- **Not recorded:** ordinary page views, to keep the log readable. Reading another fund's deal is recorded, as a refusal.
- **Retention:** 90 days (`PIQ_AUDIT_RETENTION_DAYS`), pruned on every write. A failed audit write never blocks the request; the checklist shows whether auditing works.
- **Tests:** test runs delete the audit events they created when they finish (`tests/conftest.py`), so don't run the tests while someone is using the app.

## Setup

Run once (safe to repeat):

```powershell
.\.venv\Scripts\python.exe scripts\access_setup.py
```

It adds `PIQ_AUDIT_PASSWORD`, `PIQ_VIEWER_PASSWORD` and `PIQ_CREDIT_PASSWORD` to `.env` if missing (random values, never printed), creates `piq_audit_writer`, applies migration `0010_access_roles.sql`, grants the writer its one table, and adds the new accounts with their fund grants.

## What the tests prove

`tests/test_access.py` (19 tests), on top of the existing fund tests for portfolio analytics, chat and debt:

- Each role signs in with the right permissions, and the credit officers have the right funds.
- A Fund II user (`restricted`, `credit3`) can't list, open, analyze, compare, export or change a Fund I deal, its documents, flags or adjustments. Every attempt gets the same 404 as a missing record, and the deal is unchanged afterwards.
- A Fund I-only user (`credit2`) sees only Fund I in portfolio totals and debt, and can't open the Fund II demo deal.
- Creating a deal in an ungranted fund is refused and audited.
- The viewer can read deals and exports but every write is refused (403).
- An analyst can edit a proposed adjustment but can't approve, reject, undo or delete a decided one, dismiss a flag, or change settings. A credit officer can.
- Sign-in failures are audited without the typed text, and no password from `.env` appears anywhere in the audit table.
- The audit writer can add and prune audit rows only, and only the admin can read the audit.

## Not done yet

- Production sign-in: multi-factor, lockout after failed attempts, token revocation, single sign-on.
- Per-property grants (only per-fund).
- Separation of duties beyond roles: a credit officer can approve an adjustment they proposed themselves.
- Request traces and the audit are visible to admins only, but they aren't tamper-evident (no hash chain).
