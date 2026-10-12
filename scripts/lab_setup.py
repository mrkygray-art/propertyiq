"""One-time setup for the AI Engineering Lab's request traces. Safe to run more than once.

1. Adds PIQ_TRACE_PASSWORD to .env if missing (random; value never printed).
2. Creates the piq_trace_writer database role (as the postgres superuser).
3. Applies migrations (creates request_traces).
4. Lets piq_trace_writer read, add and delete rows in request_traces (for 30-day retention), and nothing else.
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
from propertyiq.config import load_dotenv  # noqa: E402


def ensure_env() -> None:
    text = ENV.read_text(encoding="utf-8-sig")
    if not any(l.startswith("PIQ_TRACE_PASSWORD=") for l in text.splitlines()):
        ENV.write_text(text.rstrip("\n") + f"\nPIQ_TRACE_PASSWORD={secrets.token_urlsafe(24)}\n", encoding="utf-8")
        print("added to .env: PIQ_TRACE_PASSWORD")


def superuser() -> psycopg.Connection:
    return psycopg.connect(
        host=os.environ.get("PIQ_DB_HOST", "127.0.0.1"), port=int(os.environ.get("PIQ_DB_PORT", "5432")),
        dbname=os.environ.get("PIQ_DB_NAME", "propertyiq_db"), user="postgres",
        password=os.environ["POSTGRES_PASSWORD"], connect_timeout=5, autocommit=True,
    )


def main() -> None:
    ensure_env()
    load_dotenv()
    db = sql.Identifier(os.environ.get("PIQ_DB_NAME", "propertyiq_db"))
    with superuser() as conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'piq_trace_writer'").fetchone()
        conn.execute(sql.SQL("{} ROLE piq_trace_writer LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(
            sql.SQL("ALTER" if exists else "CREATE"), sql.Literal(os.environ["PIQ_TRACE_PASSWORD"])))
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO piq_trace_writer").format(db))
        conn.execute("GRANT USAGE ON SCHEMA piq TO piq_trace_writer")
        conn.execute("ALTER ROLE piq_trace_writer SET search_path = piq, public")
        conn.execute("ALTER ROLE piq_trace_writer SET statement_timeout = '5s'")
        print("role piq_trace_writer ready")

    subprocess.run([sys.executable, str(ROOT / "scripts" / "migrate.py")], check=True)

    with superuser() as conn:
        conn.execute("GRANT SELECT, INSERT, DELETE ON piq.request_traces TO piq_trace_writer")
        print("piq_trace_writer can read, add and delete request_traces only")


if __name__ == "__main__":
    main()
