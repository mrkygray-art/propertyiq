"""Reload only the synthetic lending data (loans, covenants, caps, index, valuations). scripts/seed.py also
runs this after reloading properties, because that reload removes loans through foreign keys."""
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "api"))
from propertyiq.config import get_settings  # noqa: E402
from propertyiq.lending.seed import seed_lending  # noqa: E402

if __name__ == "__main__":
    with psycopg.connect(get_settings().dsn("piq_owner")) as conn, conn.transaction():
        print("lending data:", seed_lending(conn))
