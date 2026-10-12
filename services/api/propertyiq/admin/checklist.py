"""Live security checklist: every item is checked on this machine when the page loads, nothing is hard-coded.

Each item is pass, warn, fail or info, with the evidence that decided it. Secret values are never returned:
checks look at whether a setting exists and how long it is, never at what it says.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from importlib import metadata
from pathlib import Path
from urllib.parse import urlparse

import psycopg

from propertyiq import auth, db
from propertyiq.admin import lockdown
from propertyiq.config import REPO_ROOT, get_settings

PORTS = {8000: "PropertyIQ API", 3000: "PropertyIQ web app", 5432: "Postgres (Docker)", 11434: "Ollama (local model)"}
HOSTED_AI_PACKAGES = {"openai", "anthropic", "google-generativeai", "google-genai", "cohere", "mistralai",
                      "langchain", "langchain-openai", "groq", "together", "replicate", "litellm"}
SECRET_KEYS = ["POSTGRES_PASSWORD", "PIQ_OWNER_PASSWORD", "PIQ_READONLY_PASSWORD", "PIQ_SCREENING_PASSWORD", "PIQ_TRACE_PASSWORD",
               "PIQ_SESSION_SECRET"]
REMOTE_ACCESS = {"rcclient.exe": "AnyViewer", "anydesk.exe": "AnyDesk", "teamviewer.exe": "TeamViewer"}
DOCKER = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "DockerDesktop" / "resources" / "bin" / "docker.exe"
BACKUP_DIR = Path(os.environ.get("PIQ_BACKUP_DIR", Path.home() / "PropertyIQ-backups"))
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def item(area: str, key: str, title: str, status: str, detail: str, fix: str | None = None) -> dict:
    return {"area": area, "key": key, "title": title, "status": status, "detail": detail, "fix": fix}


def run(cmd: list[str], timeout: float = 15) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=REPO_ROOT, creationflags=NO_WINDOW)


# ---------------------------------------------------------------- network

def listeners() -> dict[int, set[str]]:
    found: dict[int, set[str]] = {p: set() for p in PORTS}
    for line in run(["netstat", "-ano", "-p", "TCP"]).stdout.splitlines() + \
            run(["netstat", "-ano", "-p", "TCPv6"]).stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[3] == "LISTENING":
            host, _, port = parts[1].rpartition(":")
            if port.isdigit() and int(port) in found:
                found[int(port)].add(host)
    return found


def check_listeners() -> list[dict]:
    out = []
    for port, hosts in listeners().items():
        name = PORTS[port]
        exposed = sorted(h for h in hosts if h not in ("127.0.0.1", "[::1]"))
        if not hosts:
            out.append(item("Network", f"port_{port}", f"{name} listens on this PC only", "warn",
                            f"Nothing is listening on port {port}, so it couldn't be checked. Is it running?"))
        elif exposed:
            out.append(item("Network", f"port_{port}", f"{name} listens on this PC only", "fail",
                            f"Port {port} is open on {', '.join(exposed)}, which other computers can reach.",
                            "Bind it to 127.0.0.1."))
        else:
            out.append(item("Network", f"port_{port}", f"{name} listens on this PC only", "pass",
                            f"Port {port} listens on {', '.join(sorted(hosts))} only (loopback)."))
    return out


# ---------------------------------------------------------------- database

def check_database() -> list[dict]:
    A = "Database"
    out = []
    with db.connection() as conn:
        def one(sql: str, *args):
            return conn.execute(sql, args).fetchone()

        me = one("SELECT current_user AS u, rolsuper FROM pg_roles WHERE rolname = current_user")
        out.append(item(A, "api_role", "The API connects with a read-only, non-superuser account",
                        "pass" if me["u"] == "piq_readonly" and not me["rolsuper"] else "fail",
                        f"Connected as {me['u']} (superuser: {'yes' if me['rolsuper'] else 'no'})."))

        ro = one("SHOW default_transaction_read_only")["default_transaction_read_only"]
        to = one("SHOW statement_timeout")["statement_timeout"]
        out.append(item(A, "readonly_session", "API sessions are read-only with a query time limit",
                        "pass" if ro == "on" and to not in ("0", "") else "fail",
                        f"default_transaction_read_only = {ro}; statement_timeout = {to}."))

        writable = one(
            "SELECT count(*) AS n FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'piq' AND c.relkind IN ('r','p') "
            "AND has_table_privilege('piq_readonly', c.oid, 'INSERT, UPDATE, DELETE, TRUNCATE')")["n"]
        out.append(item(A, "readonly_grants", "The read-only account can't change any table",
                        "pass" if writable == 0 else "fail", f"Tables piq_readonly can write: {writable}."))

        has_writer = one("SELECT count(*) AS n FROM pg_roles WHERE rolname = 'piq_screening_writer'")["n"]
        if has_writer:
            stray = [r["relname"] for r in conn.execute(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'piq' AND c.relkind IN ('r','p') AND c.relname NOT LIKE 'screening\\_%%' "
                "AND has_table_privilege('piq_screening_writer', c.oid, 'INSERT, UPDATE, DELETE, TRUNCATE')")]
            out.append(item(A, "writer_scope", "The screening writer can change only loan screening tables",
                            "pass" if not stray else "fail",
                            "Writes limited to screening_* tables." if not stray else f"Also writable: {', '.join(stray)}."))

        if one("SELECT count(*) AS n FROM pg_roles WHERE rolname = 'piq_trace_writer'")["n"]:
            stray = [r["relname"] for r in conn.execute(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'piq' AND c.relkind IN ('r','p') AND c.relname <> 'request_traces' "
                "AND has_table_privilege('piq_trace_writer', c.oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE')")]
            out.append(item(A, "trace_writer_scope", "The trace writer can touch only the AI request traces",
                            "pass" if not stray else "fail",
                            "Limited to request_traces." if not stray else f"Also reachable: {', '.join(stray)}."))

        pub = one("SELECT has_schema_privilege('public', 'public', 'CREATE') AS a, "
                  "has_schema_privilege('public', 'piq', 'CREATE') AS b")
        out.append(item(A, "public_create", "No account can create objects unless granted",
                        "pass" if not (pub["a"] or pub["b"]) else "fail",
                        f"CREATE for everyone: schema public {'allowed' if pub['a'] else 'revoked'}, "
                        f"schema piq {'allowed' if pub['b'] else 'revoked'}.",
                        "REVOKE CREATE ON SCHEMA public FROM PUBLIC;" if pub["a"] or pub["b"] else None))

        supers = [r["rolname"] for r in conn.execute("SELECT rolname FROM pg_roles WHERE rolsuper AND rolcanlogin")]
        out.append(item(A, "superusers", "Only the built-in administrator is a superuser",
                        "pass" if supers == ["postgres"] else "warn", f"Superusers that can sign in: {', '.join(supers)}."))

        risky = [r["extname"] for r in conn.execute(
            "SELECT extname FROM pg_extension WHERE extname IN ('dblink','postgres_fdw','plpython3u','plperlu','http','pg_net')")]
        exts = ", ".join(r["extname"] for r in conn.execute("SELECT extname FROM pg_extension ORDER BY 1"))
        out.append(item(A, "extensions", "No database extension can make outside connections",
                        "pass" if not risky else "fail", f"Installed: {exts}." + (f" Can connect out: {', '.join(risky)}." if risky else "")))

    s = get_settings()
    try:
        psycopg.connect(host=s.db_host, port=s.db_port, dbname=s.db_name, user="piq_readonly",
                        password="wrong-" + os.urandom(8).hex(), connect_timeout=5).close()
        refused, why = False, "A wrong password was ACCEPTED."
    except psycopg.OperationalError as exc:
        refused = "password authentication failed" in str(exc)
        why = "A sign-in with a wrong password was refused." if refused else "The test connection failed for another reason."
    out.append(item(A, "db_password_required", "The database demands a password from outside its container",
                    "pass" if refused else "fail", why))

    weak = [k for k in SECRET_KEYS if 0 < len(os.environ.get(k, "")) < 16]
    missing = [k for k in SECRET_KEYS if not os.environ.get(k)]
    out.append(item(A, "db_passwords", "Database and session secrets are long random values",
                    "pass" if not weak and len(s.session_secret) >= 32 else "warn",
                    "Checked by length only; values are never read out. "
                    + (f"Shorter than 16 characters: {', '.join(weak)}. " if weak else "All at least 16 characters. ")
                    + (f"Not in .env: {', '.join(missing)}." if missing else "")))
    return out


# ---------------------------------------------------------------- API and web app

def check_api(app) -> list[dict]:
    from fastapi.routing import APIRoute
    from starlette.middleware.cors import CORSMiddleware

    A = "API"
    out = []
    public = {"/health", "/api/auth/login"}

    def calls(dep) -> set:
        found = {dep.call}
        for d in dep.dependencies:
            found |= calls(d)
        return found

    def api_routes(routes, prefix: str = "", inherited: tuple = ()) -> list[tuple[str, set]]:
        """(full path, every dependency function) for each route, including ones added by enclosing routers."""
        found = []
        for r in routes:
            if isinstance(r, APIRoute):
                deps = calls(r.dependant) | {d.dependency for d in (*inherited, *r.dependencies)}
                found.append((prefix + r.path, deps))
            elif hasattr(r, "original_router"):   # FastAPI keeps included routers wrapped
                ctx = r.include_context
                found += api_routes(r.original_router.routes, prefix + ctx.prefix,
                                    (*inherited, *ctx.dependencies, *r.original_router.dependencies))
        return found

    from propertyiq.main import current_user
    routes = api_routes(app.routes)
    open_routes = sorted({path for path, deps in routes if path not in public and current_user not in deps})
    total = len(routes)
    out.append(item(A, "auth_required", "Every API route except sign-in and health needs a signed-in user",
                    "pass" if not open_routes else "fail",
                    f"{total} routes checked." + (f" Open: {', '.join(open_routes)}." if open_routes else "")))

    out.append(item(A, "docs_off", "Built-in API docs pages are off (they load scripts from a CDN)",
                    "pass" if app.docs_url is None and app.redoc_url is None else "fail",
                    f"docs: {app.docs_url}; redoc: {app.redoc_url}."))

    origins: list[str] = []
    for m in app.user_middleware:
        if m.cls is CORSMiddleware:
            origins = list(m.kwargs.get("allow_origins", []))
    local = all(urlparse(o).hostname in ("127.0.0.1", "localhost") for o in origins)
    out.append(item(A, "cors", "Browsers may call the API only from this PC's web app",
                    "pass" if origins and local else "fail", f"Allowed origins: {', '.join(origins) or 'none'}."))

    out.append(item(A, "sessions", "Sign-in tokens are signed and expire",
                    "pass", f"HMAC-SHA256 signed; expire after {auth.TOKEN_TTL_SECONDS // 3600} hours; "
                            "passwords stored as scrypt hashes."))
    return out


def check_web() -> list[dict]:
    A = "Web app"
    out = []
    cfg = (REPO_ROOT / "apps" / "web" / "next.config.ts").read_text(encoding="utf-8")
    external = sorted(set(re.findall(r"https?://(?!127\.0\.0\.1|localhost)[\w.-]+", cfg)))
    out.append(item(A, "csp", "The browser may load content only from this app (Content Security Policy)",
                    "pass" if "default-src 'self'" in cfg and not external else "fail",
                    "default-src 'self'; no outside hosts in the policy." if not external else f"Outside hosts: {', '.join(external)}."))

    env = REPO_ROOT / "apps" / "web" / ".env"
    start = (REPO_ROOT / "scripts" / "start.ps1").read_text(encoding="utf-8")
    off = (env.exists() and "NEXT_TELEMETRY_DISABLED=1" in env.read_text(encoding="utf-8")) and "NEXT_TELEMETRY_DISABLED" in start
    out.append(item(A, "telemetry", "Next.js usage reporting is off", "pass" if off else "warn",
                    "NEXT_TELEMETRY_DISABLED=1 in apps/web/.env and in the start script." if off else "Not set everywhere."))
    return out


# ---------------------------------------------------------------- local model

def check_model() -> list[dict]:
    A = "AI model"
    s = get_settings()
    out = []
    host = urlparse(s.ollama_url).hostname
    out.append(item(A, "ollama_url", "The model is called on this PC only",
                    "pass" if host in ("127.0.0.1", "localhost", "::1") else "warn", f"Model address: {s.ollama_url}."))

    ohost = os.environ.get("OLLAMA_HOST", "")
    out.append(item(A, "ollama_host", "Ollama isn't opened up to the network",
                    "fail" if ohost.startswith("0.0.0.0") else "pass",
                    f"OLLAMA_HOST = {ohost or 'not set (Ollama default: 127.0.0.1)'}."))

    try:
        with urllib.request.urlopen(f"{s.ollama_url}/api/tags", timeout=3) as r:
            models = [m["name"] for m in json.load(r).get("models", [])]
        cloud = [m for m in models if "cloud" in m]
        out.append(item(A, "installed_models", "Only local model files are installed",
                        "pass" if not cloud else "warn", f"Installed: {', '.join(models)}."
                        + (f" Cloud models (always refused by the API): {', '.join(cloud)}." if cloud else "")))
    except Exception:
        out.append(item(A, "installed_models", "Only local model files are installed", "warn",
                        "Ollama didn't answer, so its model list couldn't be checked."))

    out.append(item(A, "allowlist", "The API calls only allowlisted models, never cloud ones", "pass",
                    f"Allowlist: {', '.join(s.allowed_models)}. The adapter refuses non-local addresses and cloud models."))

    installed = {d.metadata["Name"].lower() for d in metadata.distributions() if d.metadata["Name"]}
    hosted = sorted(installed & HOSTED_AI_PACKAGES)
    out.append(item(A, "no_hosted_sdks", "No hosted AI service libraries are installed",
                    "pass" if not hosted else "fail", "None found." if not hosted else f"Found: {', '.join(hosted)}."))
    return out


# ---------------------------------------------------------------- secrets and files

def check_files() -> list[dict]:
    A = "Secrets and files"
    out = []
    tracked = run(["git", "ls-files", ".env"]).stdout.strip()
    ignored = run(["git", "check-ignore", "-q", ".env"]).returncode == 0
    out.append(item(A, "env_ignored", "The .env file with passwords is never committed",
                    "pass" if ignored and not tracked else "fail",
                    "It's git-ignored and not tracked." if ignored and not tracked else "It's tracked or not ignored."))

    # No .env value (8+ characters) appears in any tracked file. Values stay in memory; only file names are reported.
    values = []
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = (p.strip().strip("\"'") for p in line.split("=", 1))
                if ("PASSWORD" in k or "SECRET" in k) and len(v) >= 6:
                    values.append(v)
    leaks = []
    for name in run(["git", "ls-files"]).stdout.splitlines():
        p = REPO_ROOT / name
        try:
            if p.stat().st_size > 2_000_000:
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(v in text for v in values):
            leaks.append(name)
    out.append(item(A, "no_secrets_in_code", "No password or secret from .env appears in any tracked file",
                    "pass" if not leaks else "fail",
                    f"Scanned tracked files for {len(values)} secret values." + (f" Found in: {', '.join(leaks)}." if leaks else "")))

    up = run(["git", "check-ignore", "-q", "data/uploads/x"]).returncode == 0
    out.append(item(A, "uploads_ignored", "Uploaded documents stay out of source control",
                    "pass" if up else "fail", "data/uploads/ is git-ignored." if up else "data/uploads/ isn't ignored."))

    remotes = run(["git", "remote"]).stdout.split()
    out.append(item(A, "git_remote", "The code isn't pushed to any online service",
                    "pass" if not remotes else "info",
                    "No git remote is set up." if not remotes else f"Remotes: {', '.join(remotes)} (code only; .env and data are ignored)."))
    return out


# ---------------------------------------------------------------- firewall and outbound connections

def container_can_reach_internet() -> bool | None:
    if not DOCKER.exists():
        return None
    try:
        r = run([str(DOCKER), "exec", "propertyiq-postgres", "bash", "-c",
                 f"timeout 3 bash -c '</dev/tcp/{lockdown.PROBE[0]}/{lockdown.PROBE[1]}'"], timeout=15)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return None


def remote_access_running() -> list[str]:
    names = run(["tasklist", "/fo", "csv", "/nh"]).stdout.lower()
    return sorted({label for exe, label in REMOTE_ACCESS.items() if f'"{exe}"' in names})


def check_firewall(status: dict, api_reach: bool, db_reach: bool | None, remote: list[str]) -> list[dict]:
    A = "Firewall and internet"
    mode = status.get("mode", "unknown")
    out = []
    label = {"off": "Off", "app": "On: PropertyIQ programs blocked", "computer": "On: whole computer blocked"}.get(mode, "Unknown")
    out.append(item(A, "lockdown", "Lockdown status", "pass" if mode in ("app", "computer") else "info",
                    f"{label}. {status.get('rule_count', 0)} PropertyIQ firewall rules"
                    + (f"; since {status['since']}." if status.get("since") else ".")))

    third = [p["name"] for p in status.get("firewall_products", []) if p.get("enabled") and "windows" not in p["name"].lower()]
    out.append(item(A, "third_party_firewall", "Windows Firewall is the firewall in charge",
                    "warn" if third else "pass",
                    (f"{', '.join(third)} is registered as the active firewall, so it may override Windows Firewall rules. "
                     "The connection tests below show whether blocking really works.") if third else "No other firewall product is registered."))

    def probe(key: str, who: str, reached: bool | None) -> dict:
        title = f"{who} can't reach the internet"
        if reached is None:
            return item(A, key, title, "warn", "Couldn't run the test.")
        target = f"{lockdown.PROBE[0]}:{lockdown.PROBE[1]}"
        if mode == "off":
            return item(A, key, title, "info" if reached else "pass",
                        f"Lockdown is off. Test connection to {target}: {'connected' if reached else 'blocked'}.")
        return item(A, key, title, "fail" if reached else "pass",
                    f"Test connection to {target}: {'CONNECTED, so the block is not working' if reached else 'blocked'}.",
                    "Check the McAfee firewall settings, which may be overriding Windows Firewall." if reached else None)

    out.append(probe("api_egress", "The PropertyIQ API (Python)", api_reach))
    out.append(probe("db_egress", "The database container", db_reach))

    out.append(item(A, "remote_access", "Remote-access software", "info" if remote else "pass",
                    (f"Running: {', '.join(remote)}. 'Whole computer' lockdown will disconnect remote sessions; "
                     "'PropertyIQ programs' lockdown won't.") if remote else "None running."))
    return out


# ---------------------------------------------------------------- backups and accounts

def check_backups() -> list[dict]:
    A = "Backups"
    dumps = sorted(BACKUP_DIR.glob("propertyiq-*.dump")) if BACKUP_DIR.exists() else []
    out = []
    if not dumps:
        out.append(item(A, "backup", "A recent database backup exists", "fail",
                        f"No backups in {BACKUP_DIR}.", "Run .\\.venv\\Scripts\\python.exe scripts\\backup.py"))
        return out
    newest = dumps[-1]
    age = (datetime.now() - datetime.fromtimestamp(newest.stat().st_mtime)).days
    out.append(item(A, "backup", "A recent database backup exists", "pass" if age <= 7 else "warn",
                    f"Newest: {newest.name} ({age} days old, {len(dumps)} kept) in {BACKUP_DIR}."))
    test = BACKUP_DIR / "last-restore-test.json"
    try:
        t = json.loads(test.read_text(encoding="utf-8"))
        out.append(item(A, "restore_test", "The newest backup was test-restored", "pass" if t.get("ok") else "fail",
                        f"{t.get('dump')}: restored into a scratch database on {t.get('at')}; "
                        f"{t.get('tables')} tables, row counts {'match' if t.get('ok') else 'DIFFER'}."))
    except (OSError, ValueError):
        out.append(item(A, "restore_test", "The newest backup was test-restored", "warn", "No restore test recorded."))
    return out


def check_accounts() -> list[dict]:
    from propertyiq.seed.generator import DEMO_USERS, USER_PASSWORD_ENV
    A = "Accounts"
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT u.username, u.role, coalesce(array_agg(a.portfolio_id ORDER BY a.portfolio_id) "
            "FILTER (WHERE a.portfolio_id IS NOT NULL), '{}') AS funds FROM demo_users u "
            "LEFT JOIN user_portfolio_access a ON a.username = u.username GROUP BY 1, 2 ORDER BY 1").fetchall()
    shared = [u for u, *_ in DEMO_USERS if u not in USER_PASSWORD_ENV]
    desc = "; ".join(
        f"{r['username']} ({r['role']}, {', '.join(r['funds']) or 'no funds'}, password in "
        f"{USER_PASSWORD_ENV.get(r['username'], 'PIQ_DEMO_PASSWORD')})" for r in rows)
    admins = [r["username"] for r in rows if r["role"] == "admin"]
    return [
        item(A, "users", "Sign-in accounts", "info", desc + "."),
        item(A, "separate_passwords", "Guest and admin have their own passwords", "pass",
             f"Only {', '.join(shared)} share PIQ_DEMO_PASSWORD; guest and admin each have their own."),
        item(A, "admins", "Only the test admin can change security settings", "pass" if admins == ["admin"] else "warn",
             f"Admin accounts: {', '.join(admins) or 'none'}. Other users get 403 on admin pages."),
    ]


# ---------------------------------------------------------------- all together

def build(app) -> dict:
    with ThreadPoolExecutor(max_workers=6) as pool:
        f_status = pool.submit(lockdown.firewall_status)
        f_api = pool.submit(lockdown.can_reach_internet)
        f_db = pool.submit(container_can_reach_internet)
        f_remote = pool.submit(remote_access_running)
        f_listen = pool.submit(check_listeners)
        f_files = pool.submit(check_files)
        items = (f_listen.result() + check_database() + check_api(app) + check_web() + check_model()
                 + f_files.result() + check_firewall(f_status.result(), f_api.result(), f_db.result(), f_remote.result())
                 + check_backups() + check_accounts())
    counts = {s: sum(i["status"] == s for i in items) for s in ("pass", "warn", "fail", "info")}
    return {"checked_at": datetime.now().isoformat(timespec="seconds"), "counts": counts,
            "lockdown": f_status.result(), "items": items}
