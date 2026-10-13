"""One-time setup for Phase 0.5 (roles, fund-scoped deals, access audit). Safe to run more than once.

1. Adds to .env, if missing, random values for PIQ_AUDIT_PASSWORD, PIQ_VIEWER_PASSWORD and
   PIQ_CREDIT_PASSWORD (values are never printed). Set sign-in passwords you can remember afterwards with
   scripts/set_demo_password.py --user viewer, and --user credit for all four credit officers.
2. Creates the piq_audit_writer database role (as the postgres superuser).
3. Applies migrations (roles, deal funds, access_audit).
4. Lets piq_audit_writer add rows to access_audit and delete old ones (90-day retention), and nothing else.
5. Adds the test accounts that don't exist yet (viewer, credit1 to credit4) with their fund grants, and
   brings every demo account's role and grants in line with propertyiq/seed/generator.py.
"""
import os
import secrets
import subprocess
import sys
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
sys.path.insert(0, str(ROOT / "services" / "api"))
from propertyiq.config import get_settings, load_dotenv  # noqa: E402

NEW_KEYS = ("PIQ_AUDIT_PASSWORD", "PIQ_VIEWER_PASSWORD", "PIQ_CREDIT_PASSWORD")


def ensure_env() -> None:
    text = ENV.read_text(encoding="utf-8-sig")
    have = {l.split("=", 1)[0] for l in text.splitlines() if "=" in l}
    missing = [k for k in NEW_KEYS if k not in have]
    if missing:
        ENV.write_text(text.rstrip("\n") + "\n" + "".join(f"{k}={secrets.token_urlsafe(24)}\n" for k in missing),
                       encoding="utf-8")
        print("added to .env:", ", ".join(missing))


def superuser() -> psycopg.Connection:
    return psycopg.connect(
        host=os.environ.get("PIQ_DB_HOST", "127.0.0.1"), port=int(os.environ.get("PIQ_DB_PORT", "5432")),
        dbname=os.environ.get("PIQ_DB_NAME", "propertyiq_db"), user="postgres",
        password=os.environ["POSTGRES_PASSWORD"], connect_timeout=5, autocommit=True,
    )


def sync_users() -> None:
    from propertyiq.auth import hash_password
    from propertyiq.seed.generator import DEMO_USERS, USER_PASSWORD_ENV
    with psycopg.connect(get_settings().dsn("piq_owner")) as conn:
        existing = {r[0] for r in conn.execute("SELECT username FROM demo_users")}
        for username, display, role, funds in DEMO_USERS:
            if username in existing:
                # Keep the password; bring the role, name and fund grants in line with the generator.
                conn.execute("UPDATE demo_users SET role = %s, display_name = %s WHERE username = %s",
                             (role, display, username))
            else:
                password = os.environ.get(USER_PASSWORD_ENV.get(username, "PIQ_DEMO_PASSWORD"), "")
                if not password or password == "change-me":
                    print(f"skipped new user '{username}': no password in .env")
                    continue
                conn.execute("INSERT INTO demo_users (username, display_name, role, password_hash) VALUES (%s, %s, %s, %s)",
                             (username, display, role, hash_password(password)))
                print(f"added user {username} ({role}, {', '.join(funds)})")
            conn.execute("DELETE FROM user_portfolio_access WHERE username = %s AND NOT (portfolio_id = ANY(%s))",
                         (username, funds))
            for f in funds:
                conn.execute("INSERT INTO user_portfolio_access (username, portfolio_id) VALUES (%s, %s) "
                             "ON CONFLICT DO NOTHING", (username, f))


def main() -> None:
    ensure_env()
    load_dotenv()
    db = sql.Identifier(os.environ.get("PIQ_DB_NAME", "propertyiq_db"))
    with superuser() as conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'piq_audit_writer'").fetchone()
        conn.execute(sql.SQL("{} ROLE piq_audit_writer LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(
            sql.SQL("ALTER" if exists else "CREATE"), sql.Literal(os.environ["PIQ_AUDIT_PASSWORD"])))
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO piq_audit_writer").format(db))
        conn.execute("GRANT USAGE ON SCHEMA piq TO piq_audit_writer")
        conn.execute("ALTER ROLE piq_audit_writer SET search_path = piq, public")
        conn.execute("ALTER ROLE piq_audit_writer SET statement_timeout = '5s'")
        print("role piq_audit_writer ready")

    subprocess.run([sys.executable, str(ROOT / "scripts" / "migrate.py")], check=True)

    with superuser() as conn:
        # SELECT is needed only so the retention DELETE can find old rows.
        conn.execute("GRANT SELECT, INSERT, DELETE ON piq.access_audit TO piq_audit_writer")
        print("piq_audit_writer can add to and prune access_audit only")

    sync_users()
    print("Done. Set passwords you'll remember with scripts/set_demo_password.py --user viewer and --user credit.")


if __name__ == "__main__":
    main()
