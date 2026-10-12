# PropertyIQ

A private, self-hosted AI analyst for a **fictional** real estate portfolio. It is a proof of concept, not a financial advisory product.

- **Local only.** The language model runs locally through Ollama, the database is local Postgres, and nothing calls a hosted AI API.
- **Code does the math.** NOI, budget variance, YoY change and occupancy are calculated in SQL and Python. The model only explains results.
- **Access is enforced in the database queries,** not in prompts.
- **All data is synthetic:** 55 made-up properties, generated identically on every run.

## Status

| Milestone | Status |
|---|---|
| 0: Environment, model choice, architecture, threat model | ✅ Done |
| 1a: Schema, synthetic seed, verified analytics API, tests | ✅ Done |
| 1b: Next.js UI and local AI chat, with checked figures | ✅ Done. 99 tests pass. Chat answers in about 7–15 seconds with `qwen3:4b-instruct-2507`. |
| Tier 1: Loan screening module (lender guidelines, stress tests, PDF and Excel export) | ✅ Done, on branch `tier1-loan-screening`. See [loan-screening.md](docs/loan-screening.md). |
| Tier 2: Document intake (rent roll, T-12, OCR), anomaly flags, market comps, California red flags, borrower vs underwritten comparison, PDF and Excel memo | ✅ Done, on branch `tier2-intake-and-flags`. See [document-intake.md](docs/document-intake.md). |
| 2: Document Q&A over local embeddings, citations, prompt-injection test | Planned |
| 3: Audit log, rate limits, backups, offline proof with egress blocked, 50-question evaluation | Planned |

**Demo check:** `GET /api/properties/underperforming` returns **4 of 55** properties flagged, with a **$420,000** total budget shortfall for the period ending 2026-09-30.

## Setup (Windows 11)

Requires Docker Desktop (WSL2), Ollama, Python 3.12+ and Git.

```powershell
cd C:\Users\mrkyg\projects\propertyIQ

# 1. Secrets: create .env with POSTGRES_PASSWORD=..., then fill in the rest automatically
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r services\api\requirements.txt
.\.venv\Scripts\python.exe scripts\init_env.py

# 2. Database (pgvector image, bound to 127.0.0.1 only)
docker compose up -d
.\.venv\Scripts\python.exe scripts\db_bootstrap.py   # roles: piq_owner, piq_readonly
.\.venv\Scripts\python.exe scripts\migrate.py
.\.venv\Scripts\python.exe scripts\seed.py           # 55 synthetic properties
.\.venv\Scripts\python.exe scripts\screening_setup.py   # loan screening module (optional)

# 3. Local models (one-time download, about 2.8 GB)
ollama pull qwen3:4b-instruct-2507-q4_K_M
ollama pull nomic-embed-text

# 4. Web app (Node 20+)
cd apps\web
npx next telemetry disable
npm install
npm run build
cd ..\..
```

## Run and test

```powershell
# Start the API (127.0.0.1:8000) and web app (127.0.0.1:3000) and open the browser.
# Starts Docker Desktop and Ollama if needed; safe to run again (skips anything already running).
# The "PropertyIQ" desktop shortcut runs the same script.
powershell -ExecutionPolicy Bypass -File scripts\start.ps1

# All tests (unit, database, authorization, chat, live local model)
.\.venv\Scripts\python.exe -m pytest -q

# Web type check and production build
cd apps\web; npx tsc --noEmit; npm run build

# Against the running API
.\.venv\Scripts\python.exe scripts\smoke_test.py
```

## UI

| Page | What it shows |
|---|---|
| `/` | Portfolio overview: KPIs, flagged assets with filters, asset-class table, reporting-period selector, and chat |
| `/properties/{id}` | Rule checks, 36-month NOI-vs-budget and occupancy charts, calculation trail with record IDs, monthly table, and chat focused on the property |
| `/verified` | The exact verified payload and its SHA-256 |
| `/login` | Demo sign-in |
| `/screening` | Loan screening: deal form, underwriting numbers, guideline PASS/FAIL, stress tests, DSCR grid, PDF and Excel export |
| `/screening/guidelines` | Lender guideline sets |
| `/screening/deals` | Deal workspace: Upload → Review → Flags → Market → California → Compare → Export |
| `/screening/settings` | Benchmarks, California rules (with sources), component typical life, settings |

Each chat answer is labeled with its source:

- **"Local model · figures verified":** every dollar amount and percentage matched the verified facts.
- **"Verified summary":** the model was unavailable or its draft failed the number check, so the answer was built from the same facts in code.

Demo sign-ins are `analyst` (all 55 properties) and `restricted` (Synthetic Fund II only, 27 properties). The password is `PIQ_DEMO_PASSWORD` in `.env`.

## API

| Endpoint | Purpose |
|---|---|
| `GET /health` | Database and local-model readiness (true/false only) |
| `POST /api/auth/login` | Demo sign-in; returns a bearer token |
| `GET /api/periods` | Available quarter-end reporting periods |
| `GET /api/rules` | Active rule set and priority rule |
| `GET /api/properties/underperforming?period=` | Flagged properties with rules, inputs, formulas, source record IDs and `payload_sha256` |
| `GET /api/portfolio/summary?period=` | Counts, asset-class mix, occupancy, NOI actual vs budget |
| `GET /api/properties/{id}?period=` | One property: assessment plus 36-month history |
| `POST /api/chat` | Question → deterministic routing → verified facts → local model → number check. Returns answer, source label, facts, citations |

## Layout

```text
docker-compose.yml          Postgres 17 + pgvector, 127.0.0.1 only
apps/web/                   Next.js UI (system fonts, strict CSP, proxies /api to the API)
db/migrations/              SQL migrations (schema piq)
db/seeds/                   The four required demo properties from the brief
services/api/propertyiq/    FastAPI app: finance.py (rules), analytics.py (vetted SQL), chat.py (routing + number check), llm.py (local model adapter)
scripts/                    init_env, db_bootstrap, migrate, seed, smoke_test, start.ps1
tests/                      pytest: finance, seed, API/authorization, LLM guardrails
docs/                       architecture, security boundary, financial definitions, model evaluation, demo script
```

## Documents

- [Architecture](docs/architecture.md)
- [Security boundary and threat model](docs/security-boundary.md)
- [Financial definitions and rules](docs/financial-definitions.md)
- [Model evaluation](docs/model-evaluation.md)
- [Demo script](docs/demo-script.md)
- [Loan screening module](docs/loan-screening.md)
- [Tier 2: document intake and underwriting](docs/document-intake.md)
