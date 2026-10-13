# PropertyIQ: verified current state (2026-10-11)

This document records what was **inspected on the running system**, as distinct from assumptions. It was written for the Institutional AI Roadmap (Phase 0), with no code changed.

## Platform: verified

| Item | Verified fact | Note vs roadmap |
|---|---|---|
| Host OS | Windows 11 Home (build 26300), Lenovo 82XV, i5-13420H, 16 GB RAM, RTX 2050 with 4 GB VRAM | The roadmap says "Ubuntu/Docker". **There is no Ubuntu host.** Docker Desktop runs on WSL2 and hosts only Postgres. |
| Database | Postgres 17.11 + pgvector 0.8.7 in Docker container `propertyiq-postgres`, published only on `127.0.0.1:5432`, volume `pgdata` | |
| Schema | Schema `piq`, 23 tables, migrations 0001–0006 applied by `scripts/migrate.py` | pgvector is enabled, but **no vector columns exist yet** (no RAG) |
| API | FastAPI on Python 3.14, run natively on Windows (`scripts/start.ps1`), `127.0.0.1:8000` | Not containerized |
| Web | Next.js 16.4 / React 19.3 / TypeScript 5.9, production build, `127.0.0.1:3000`, strict CSP (`'self'` only), telemetry off, system fonts | Not containerized |
| Inference | Ollama 0.40.3 on `127.0.0.1:11434`. Chat model **`qwen3:4b-instruct-2507-q4_K_M`** (4.0B parameters, Q4_K_M). Also installed but unused: `qwen3:4b` (Thinking-2507), and `nomic-embed-text` (embeddings, reserved for RAG) | Matches "Qwen3 local inference" |
| Charts | Custom SVG line charts (`apps/web/components/LineChart.tsx`); no chart library | |
| Startup | Desktop shortcut runs `scripts/start.ps1` (Docker check, Ollama check, API and web windows) | |
| Source control | Local git repo, branch `main`. **No remote configured.** | |
| Backups | `scripts/backup.py` (added 2026-10-11): database dump and uploads zip in `%USERPROFILE%\PropertyIQ-backups`, with a restore test into a scratch database. No off-machine copy yet. | |

## Data: verified

- **Portfolio analytics:**
  - 55 synthetic properties in 2 synthetic funds (`FUND-I` with 28, `FUND-II` with 27).
  - 36 months (Oct 2023 – Sep 2026) of monthly actuals and budgets.
  - The seed is deterministic, recorded with a SHA-256 in `seed_runs`.
- **Loan screening (Tier 1):** guideline sets.
- **Document intake and underwriting (Tier 2):**
  - deals, uploaded documents (files in `data/uploads/`), extracted items, approved rent roll units and T-12 lines,
  - flags, benchmarks, California rules, market cache, condition items, adjustments.
- **Not present anywhere:**
  - portfolio-level loans, covenants, leases or tenants,
  - valuations, cash flows or capital events,
  - documents for RAG, geocodes.

## Authentication and authorization: verified

- **Users:** demo users in `demo_users`: `analyst` (both funds), `restricted` (Fund II), `guest` (both funds, its own password), and `admin` (test admin for the security checklist and lockdown, added 2026-10-11; its own password). Passwords are scrypt hashes.
- **Sessions:** HMAC-signed bearer tokens lasting 8 hours, stored in the browser's `sessionStorage`.
- **Portfolio analytics and chat:** fund scope is enforced inside every SQL query (a `JOIN` on `user_portfolio_access`). A property in another fund returns the same 404 as a missing one, and tests cover this.
- **Loan screening (Tiers 1 and 2): not fund-scoped.** Deals, documents, guideline sets and settings are shared by every signed-in user, and any user can change them. The `restricted` user can see every screening deal. There are no roles beyond the user name, and no read-only guest. *(Fixed by Phase 0.5 on 2026-10-12: deals are fund-scoped and there are four roles. See [../access-control.md](../access-control.md).)*
- **Database roles:**

  | Role | Can do |
  |---|---|
  | `piq_readonly` | SELECT only, read-only transactions; used by the API |
  | `piq_screening_writer` | Writes `screening_*` tables only |
  | `piq_owner` | Migrations and seeding |

## Query and AI path: verified

- **Portfolio Q&A:**
  - **Routing:** a deterministic router sends each question to a vetted analytics function (fixed, parameterized SQL).
  - **What the model sees:** only the minimized facts, as JSON.
  - **Number check:** every $ and % figure in the answer must match those facts.
  - **Fallback:** if the check fails or the model is down, a deterministic summary is shown instead.
  - **Visible in the chat:** the facts sent to the model, the verification status and the source record IDs.
  - **Verified-calculations page:** the full payload with its SHA-256.
- **Loan screening:** a deterministic `Decimal` engine. Excel exports use live formulas, verified by OpenOffice recalculation.
- **Document intake:** code-first extraction, local OCR (RapidOCR). The local model only suggests categories for unknown T-12 labels: the labels are masked, amounts are never sent, and the answer must be from a fixed list.
- **Missing:**
  - no persisted request traces,
  - no model or tool registry,
  - no RAG,
  - no evaluation runs beyond pytest.

## Tests: verified

- 13 pytest files, **250 tests**, all passing on `main`. Coverage:
  - financial rules and boundaries,
  - seed determinism,
  - authorization (cross-fund 404s, the read-only role, forged tokens),
  - chat routing and number verification,
  - Tier 1 cases A–D,
  - Tier 2 fixtures (planted problems, clean set, privacy),
  - OpenOffice recalculation of the Excel exports,
  - live local-model round trips.
- **Frontend:** a TypeScript type check and production build only; there are no frontend unit tests.

## Network egress: what is and isn't proven

- **Proven by inspection:**
  - All four services listen on `127.0.0.1` only.
  - The CSP blocks external scripts, styles, fonts, images and connections.
  - The LLM adapter refuses non-local hosts and `*-cloud` models.
  - No hosted AI SDKs are installed.
- **Informal:** the owner reports the app works in airplane mode.
- **Not yet proven:** no firewall-blocked run with captured connection logs has been done. Known outbound-capable software on the host:
  - Ollama desktop app (update checks)
  - Docker Desktop
  - Windows itself
  - AnyViewer, which is remote-access software and is about to be used for a guest

## Documentation present

- `README.md` and `CLAUDE.md`
- `docs/`:
  - `architecture.md`, `security-boundary.md`
  - `financial-definitions.md`, `model-evaluation.md`, `demo-script.md`
  - `loan-screening.md` (Tier 1), `document-intake.md` (Tier 2)
