"""One-time setup for the loan screening module. Safe to run more than once.

1. Adds PIQ_SCREENING_ENABLED and PIQ_SCREENING_PASSWORD to .env if missing (value never printed).
2. Creates the piq_screening_writer database role (as the postgres superuser).
3. Applies migrations (creates screening_guideline_sets).
4. Lets piq_screening_writer change screening_guideline_sets, and nothing else.
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
    keys = {l.split("=", 1)[0] for l in text.splitlines() if "=" in l}
    add = []
    if "PIQ_SCREENING_ENABLED" not in keys:
        add.append("PIQ_SCREENING_ENABLED=true")
    if "PIQ_SCREENING_PASSWORD" not in keys:
        add.append(f"PIQ_SCREENING_PASSWORD={secrets.token_urlsafe(24)}")
    if add:
        ENV.write_text(text.rstrip("\n") + "\n" + "\n".join(add) + "\n", encoding="utf-8")
        print("added to .env:", ", ".join(a.split("=")[0] for a in add))


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
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'piq_screening_writer'").fetchone()
        conn.execute(sql.SQL("{} ROLE piq_screening_writer LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(
            sql.SQL("ALTER" if exists else "CREATE"), sql.Literal(os.environ["PIQ_SCREENING_PASSWORD"])))
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO piq_screening_writer").format(db))
        conn.execute("GRANT USAGE ON SCHEMA piq TO piq_screening_writer")
        conn.execute("ALTER ROLE piq_screening_writer SET search_path = piq, public")
        conn.execute("ALTER ROLE piq_screening_writer SET statement_timeout = '5s'")
        print("role piq_screening_writer ready")

    subprocess.run([sys.executable, str(ROOT / "scripts" / "migrate.py")], check=True)

    with superuser() as conn:
        conn.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON piq.screening_guideline_sets TO piq_screening_writer")
        conn.execute("GRANT USAGE ON SEQUENCE piq.screening_guideline_sets_set_id_seq TO piq_screening_writer")
        print("piq_screening_writer can change screening_guideline_sets only")


if __name__ == "__main__":
    main()
