# Admin: security checklist, internet lockdown and backups

The `admin` sign-in opens the **Admin** page (`/admin`). It has two parts: an internet lockdown switch and a live security checklist. No other user can open it; their API calls get `403`.

## Setting up the admin account

```powershell
.\.venv\Scripts\python.exe scripts\migrate.py                          # adds the 'admin' role (0007)
.\.venv\Scripts\python.exe scripts\set_demo_password.py --user admin   # asks for its password (hidden); saves PIQ_ADMIN_PASSWORD
```

## Internet lockdown

`scripts/lockdown.ps1` changes Windows Firewall. Every rule is in the firewall group **"PropertyIQ Lockdown"**.

| Mode | What it blocks | Still works |
|---|---|---|
| **PropertyIQ programs** (`App`) | Public internet addresses for Ollama (`ollama.exe`, `ollama app.exe`), the API's Python (the venv launcher and the real interpreter), Node.js (web app) and Docker Desktop (`com.docker.backend.exe`, `Docker Desktop.exe`) | Everything on this PC, the local network, and remote access (AnyViewer) |
| **Whole computer** (`Computer`) | Public internet addresses for every program, plus outbound traffic from the WSL virtual machine (Hyper-V firewall) | This PC's own services and the local network. **Remote-access sessions disconnect.** |
| **Off** | Removes every PropertyIQ rule and restores any setting it changed | |

**How a change is made:**
- **No admin rights in the API:** the API never runs as administrator. The Admin page asks the API to start the script with Windows' "Run as administrator", which shows the permission prompt on this PC's screen. Nothing changes until someone clicks **Yes**.
- **Fixed modes only:** the mode must be one of a fixed list; anything else is refused with `422`.
- **Restoring:** previous settings are saved in `C:\ProgramData\PropertyIQ\lockdown-state.json` and restored by **Off**.
- **Undo without the app:** the **"PropertyIQ - Restore internet"** desktop shortcut turns lockdown off (with the same permission prompt). Use it after "whole computer" mode, when remote access no longer works.
- **Survives restarts:** firewall rules stay in place after a restart until turned off.
- **Banner:** while lockdown is on, every page shows a green banner saying so.

**What happens when the script runs:**
- **Log:** every run is written to `logs/lockdown.log`, and each admin request to `logs/admin-actions.log` with the username.
- **Evidence:** before and after each change, the script saves its evidence to `logs/lockdown-evidence-*.json`. That evidence is:
  - the firewall status,
  - a test connection to 1.1.1.1:443 from PowerShell,
  - every established non-loopback connection, with its process name.

**A third-party firewall may override Windows Firewall.** On this PC, McAfee is registered as the active firewall, and it may take over from Windows Firewall rules. The checklist therefore doesn't trust the rules alone. It opens a real test connection to a public address from:
1. the API's own Python process;
2. inside the database container.

If lockdown is on and either connects, the item **fails** with "the block is not working".

### Live test (2026-10-12)

Ky ran each mode from the Admin page on this PC, with Wi-Fi on and McAfee still registered as the active firewall. Windows Firewall rules took effect.

| Mode | Time | Evidence |
|---|---|---|
| `App` | 17:06 and 17:10 | 7 rules added. Checklist: the API's Python and the database container both got **blocked** connecting to 1.1.1.1:443. PowerShell, which isn't blocked in this mode, still connected, so the rest of the PC kept working. A chat question was answered by the local model with every figure verified (14.6 s). |
| `Computer` | 17:07 | 8 rules plus the Hyper-V (WSL/Docker) outbound block. The PowerShell test connection changed from connected to **blocked**. Checklist: 34 pass, 2 warnings, 0 fail. |
| `Off` | 17:09 | All rules removed and the firewall defaults restored. The test connection went back to connected. |

The evidence files are `logs/lockdown-evidence-20261012-*.json` and `logs/lockdown.log`, kept on this PC only.

**Not covered by this test:**
- No separate connection probe for Ollama or Node.js. Their rules were added, but only the API and the database container were probed.
- The Windows Firewall log (`pfirewall.log`) wasn't captured.
- No chat question was asked during `Computer` mode.
- It was one run, on one PC.

## Security checklist

Every item is checked live each time the page loads (about 3 seconds). Each one is pass, warning, fail or info, and comes with the evidence that decided it. Secret values are never read out: secrets are checked by presence and length only.

| Area | Checks |
|---|---|
| Network | API (8000), web app (3000), Postgres (5432) and Ollama (11434) listen on 127.0.0.1 only (from `netstat`) |
| Database | The API connects as `piq_readonly` (not a superuser); sessions are read-only with a 5 s statement limit; `piq_readonly` can't write any table; `piq_screening_writer` can write only `screening_*` tables; CREATE revoked from everyone on schemas `public` and `piq`; `postgres` is the only superuser; no extensions that can connect out (`dblink`, `postgres_fdw`, untrusted languages); a wrong password from outside the container is refused; secret lengths |
| API | Every route except sign-in and health needs a signed-in user (walks all routes, including the screening and admin routers); built-in docs pages off; CORS allows only this PC's web app; signed, expiring tokens |
| Web app | Content Security Policy is `default-src 'self'` with no outside hosts; Next.js telemetry off |
| AI model | The model address is loopback; `OLLAMA_HOST` isn't opened up; installed models are listed and none are cloud models; allowlist; no hosted-AI libraries installed (`openai`, `anthropic`, `langchain` and others) |
| Secrets and files | `.env` is git-ignored and untracked; no `.env` password or secret appears in any tracked file; `data/uploads/` is ignored; no git remote |
| Firewall and internet | Lockdown mode and rule count; third-party firewall warning; real connection tests from the API and from the database container; remote-access software running |
| Backups | The newest backup's age; result of the latest restore test |
| Accounts | Each user's role, funds and which `.env` key holds its password (the key name only); only the test admin has the admin role |

## Backups

```powershell
.\.venv\Scripts\python.exe scripts\backup.py        # dump + uploads zip + restore test; keeps the newest 14
```

**What it does:**
1. **Dump:** `pg_dump` runs inside the container over its local connection, so no password appears on any command line.
2. **Uploads:** `data/uploads/` is zipped alongside the dump.
3. **Restore test:** each backup is restored into a scratch database (`piq_restore_test`), and every table's row count and the seed hash are compared with the live database. The scratch database is then dropped, and the result is saved as `last-restore-test.json`.

**What it doesn't cover:**
- **Location:** backups go to `%USERPROFILE%\PropertyIQ-backups`, outside the repo; `PIQ_BACKUP_DIR` overrides this. For an off-machine copy, copy that folder to an external drive.
- **Roles and passwords:** these aren't in the dump. `scripts/db_bootstrap.py` recreates them from `.env`.

**First run (2026-10-11):** 22 tables and 4,214 rows; the restore test passed.
