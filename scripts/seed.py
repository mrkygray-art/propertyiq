"""Load the deterministic synthetic dataset into Postgres as piq_owner.

Replaces all seeded rows (portfolios, properties, monthly data, demo users). Only synthetic
data lives in these tables, so a reload loses nothing real. Record IDs restart at 1, so
the same seed always produces the same record IDs.
"""
import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "api"))
from propertyiq.auth import hash_password  # noqa: E402
from propertyiq.config import get_settings  # noqa: E402
from propertyiq.seed.generator import GENERATOR_VERSION, generate  # noqa: E402


def main() -> None:
    settings = get_settings()
    demo_password = os.environ.get("PIQ_DEMO_PASSWORD", "")
    if not demo_password or demo_password == "change-me":
        sys.exit("PIQ_DEMO_PASSWORD is missing from .env. Run: python scripts/init_env.py")
    ds = generate()

    with psycopg.connect(settings.dsn("piq_owner")) as conn, conn.transaction():
        conn.execute(
            "TRUNCATE user_portfolio_access, demo_users, monthly_budgets, monthly_financials, "
            "properties, portfolios, seed_runs RESTART IDENTITY CASCADE"
        )
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO portfolios (portfolio_id, name) VALUES (%s, %s)", ds.portfolios)
            cur.executemany(
                "INSERT INTO properties (property_id, portfolio_id, name, asset_class, market, size_unit, rentable_size) "
                "VALUES (%(property_id)s, %(portfolio_id)s, %(name)s, %(asset_class)s, %(market)s, %(size_unit)s, %(rentable_size)s)",
                ds.properties,
            )
            with cur.copy(
                "COPY monthly_financials (property_id, month_end, operating_revenue, operating_expenses, "
                "occupied_size, rentable_size) FROM STDIN"
            ) as copy:
                for row in ds.financials:
                    copy.write_row(row)
            with cur.copy(
                "COPY monthly_budgets (property_id, month_end, budget_revenue, budget_expenses) FROM STDIN"
            ) as copy:
                for row in ds.budgets:
                    copy.write_row(row)
            for username, display, role, portfolios in ds.users:
                cur.execute(
                    "INSERT INTO demo_users (username, display_name, role, password_hash) VALUES (%s, %s, %s, %s)",
                    (username, display, role, hash_password(demo_password)),
                )
                cur.executemany(
                    "INSERT INTO user_portfolio_access (username, portfolio_id) VALUES (%s, %s)",
                    [(username, p) for p in portfolios],
                )
            cur.execute(
                "INSERT INTO seed_runs (generator, random_seed, content_sha256, property_count) VALUES (%s, %s, %s, %s)",
                (GENERATOR_VERSION, ds.seed, ds.content_sha256(), len(ds.properties)),
            )
    print(
        f"seeded {len(ds.properties)} synthetic properties, {len(ds.financials)} monthly actuals, "
        f"{len(ds.budgets)} monthly budgets, {len(ds.users)} demo users; sha256 {ds.content_sha256()[:16]}"
    )


if __name__ == "__main__":
    main()
