"""Create the PropertyIQ database roles and schema. Safe to run more than once.

Connects once as the postgres superuser (POSTGRES_PASSWORD from .env) and creates:
  piq_owner     owns schema piq; used only for migrations and seeding
  piq_readonly  SELECT-only, read-only transactions, short statement timeout; used by the API
Passwords come from .env and are never printed.
"""
import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "api"))
from propertyiq.config import load_dotenv  # noqa: E402


def main() -> None:
    load_dotenv()
    for key in ("POSTGRES_PASSWORD", "PIQ_OWNER_PASSWORD", "PIQ_READONLY_PASSWORD"):
        if not os.environ.get(key) or os.environ[key] == "change-me":
            sys.exit(f"{key} is missing from .env. Run: python scripts/init_env.py")
    dbname = os.environ.get("PIQ_DB_NAME", "propertyiq_db")

    with psycopg.connect(
        host=os.environ.get("PIQ_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("PIQ_DB_PORT", "5432")),
        dbname=dbname,
        user="postgres",
        password=os.environ["POSTGRES_PASSWORD"],
        connect_timeout=5,
        autocommit=True,
    ) as conn:
        db = sql.Identifier(dbname)
        for role, env_key in (("piq_owner", "PIQ_OWNER_PASSWORD"), ("piq_readonly", "PIQ_READONLY_PASSWORD")):
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
            verb = "ALTER" if exists else "CREATE"
            conn.execute(
                sql.SQL("{} ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(
                    sql.SQL(verb), sql.Identifier(role), sql.Literal(os.environ[env_key])
                )
            )
            print(f"{verb.lower()}d role {role}")

        conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(db))
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO piq_owner, piq_readonly").format(db))
        conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute("CREATE SCHEMA IF NOT EXISTS piq AUTHORIZATION piq_owner")
        conn.execute("GRANT USAGE ON SCHEMA piq TO piq_readonly")
        conn.execute("ALTER DEFAULT PRIVILEGES FOR ROLE piq_owner IN SCHEMA piq GRANT SELECT ON TABLES TO piq_readonly")
        conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA piq TO piq_readonly")
        conn.execute("ALTER ROLE piq_owner SET search_path = piq, public")
        conn.execute("ALTER ROLE piq_readonly SET search_path = piq, public")
        conn.execute("ALTER ROLE piq_readonly SET default_transaction_read_only = on")
        conn.execute("ALTER ROLE piq_readonly SET statement_timeout = '5s'")
        print("schema piq ready; piq_readonly is SELECT-only with read-only transactions")


if __name__ == "__main__":
    main()
