# PropertyIQ: notes for Claude Code

A local-only proof of concept on synthetic data, built for a potential enterprise client. Read `docs/architecture.md` and `docs/security-boundary.md` before changing behavior.

## Hard rules

- **No hosted inference, embedding or analytics services.** Ollama on loopback only. Never add a hosted AI SDK or a CDN script or font.
- **Never print, log or commit `.env` values.** Check whether a key exists; don't show its value.
- **The model never does math and never gets SQL.** New questions get a new vetted function in `analytics.py` that joins `user_portfolio_access`.
- **Don't change the four required properties** in `db/seeds/required_properties.json`, or the requirement of 55 properties with 4 flagged and a $420,000 shortfall, without Ky's say-so.
- **Ask Ky before** pulling large models, installing big software, or deleting or replacing containers and volumes.

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
