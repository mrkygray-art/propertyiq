# AI Engineering Lab

The **AI Lab** page (`/lab`) shows how PropertyIQ's AI answers are made, checked and kept on this PC. Everything on it is read from the running system: traces from the database, models from Ollama, and tools from the code.

| Tab | Shows |
|---|---|
| Overview | Trace counts, median and p95 response time, the six-step answer flow, answer types, the prompt layout and system instructions |
| Request traces | Recent answers (yours; admins see everyone's). For one answer: stage-by-stage timings, the vetted function used, number-check result, model and token counts, cited properties, facts SHA-256, source record IDs |
| Model on / off | Asks the same question with the local model off, then on. The verified facts must be identical (same SHA-256) |
| Models | Installed Ollama models: role, family, parameters, quantization, size, digest, allowlisted, loaded in memory |
| Tools | Every vetted function: what it returns, its fund scope, which features use it, file and line |
| Data schema | All tables in schema `piq` with descriptions, columns and foreign keys; row counts (only for users who can see every fund); what each database login can read or change |
| Offline proof | Lockdown status, what listens where, and the before/after evidence saved by the Admin page's lockdown |

Each chat answer links to its trace ("How this answer was made").

## Request traces (decided 2026-10-11)

- **What's stored:**
  - stages and timings;
  - the answer type;
  - the vetted function used;
  - the model and its token counts;
  - the number-check result;
  - cited property IDs and source record IDs;
  - the SHA-256 of the facts sent to the model.
- **Never stored:** the question or prompt text (only its length), the model's answer text, passwords and tokens.
- **Retention:** 30 days (`PIQ_TRACE_RETENTION_DAYS`). Older traces are deleted every time a trace is written.
- **Who sees them:** you see your own traces; admins see all of them. Any other trace ID returns the same 404 as a missing one.
- **Writer:** traces are written by the `piq_trace_writer` login, which can read, add and delete rows in `request_traces` and nothing else. The Admin checklist verifies this.
- **Failures:** a failed trace write never fails the chat answer.
- **Setup:** `.\.venv\Scripts\python.exe scripts\lab_setup.py`. This adds `PIQ_TRACE_PASSWORD` to `.env`, creates the login and applies migration 0009. Safe to run again.

## Tests (`tests/test_lab.py`)

- **Question text:** a marker word in a question never appears anywhere in the stored row.
- **Stages:** the stage order is correct.
- **Visibility:**
  - users see only their own traces, and admins see all;
  - an invalid trace ID gets 422, and a missing sign-in gets 401.
- **Retention:** a 31-day-old trace is deleted.
- **Trace login:** it can't touch other tables.
- **Model on/off:** gives identical facts.
- **Schema:** row counts are hidden from the restricted user; foreign keys are listed.
- **Tool registry:** shows fund scope.
- **Loan questions:** chat points them to the Debt page.

Traces created during a test run are deleted when the run ends (`tests/conftest.py`).
