"""Apply db/migrations/*.sql in order as piq_owner. Each file runs once, in its own transaction."""
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "api"))
from propertyiq.config import get_settings  # noqa: E402

MIGRATIONS = ROOT / "db" / "migrations"


def main() -> None:
    settings = get_settings()
    with psycopg.connect(settings.dsn("piq_owner")) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS piq.schema_migrations "
            "(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        conn.commit()
        applied = {r[0] for r in conn.execute("SELECT version FROM piq.schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.stem in applied:
                continue
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO piq.schema_migrations (version) VALUES (%s)", (path.stem,))
            print(f"applied {path.name}")
        print("migrations up to date")


if __name__ == "__main__":
    main()
