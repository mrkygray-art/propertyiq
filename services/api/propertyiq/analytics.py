"""Vetted analytics functions. These are the only queries the API (and later the chat tools) can run.

Every function takes the signed-in username and filters through user_portfolio_access in
SQL, so a property outside the user's portfolios is indistinguishable from one that does
not exist. All SQL is fixed text with bound parameters; nothing is built from user input.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import psycopg

from propertyiq.finance import (
    Evaluation, PeriodInputs, RuleSet, add_months, current_window, evaluate, prior_window,
)


@dataclass(frozen=True)
class DemoUser:
    username: str
    display_name: str
    role: str
    portfolios: tuple[str, ...]


@dataclass(frozen=True)
class PropertyEvaluation:
    property: dict
    evaluation: Evaluation
    source_records: dict[str, list[int]]


_USER_SQL = """
SELECT u.username, u.display_name, u.role, u.password_hash,
       coalesce(array_agg(a.portfolio_id ORDER BY a.portfolio_id) FILTER (WHERE a.portfolio_id IS NOT NULL), '{}') AS portfolios
FROM demo_users u
LEFT JOIN user_portfolio_access a ON a.username = u.username
WHERE u.username = %(username)s
GROUP BY u.username
"""


def get_user(conn: psycopg.Connection, username: str) -> tuple[DemoUser, str] | None:
    """Return the user and their password hash, or None."""
    row = conn.execute(_USER_SQL, {"username": username}).fetchone()
    if row is None:
        return None
    return DemoUser(row["username"], row["display_name"], row["role"], tuple(row["portfolios"])), row["password_hash"]


def available_periods(conn: psycopg.Connection) -> list[date]:
    """Quarter-end periods that have full current and prior trailing-12-month windows."""
    row = conn.execute("SELECT min(month_end) AS first, max(month_end) AS last FROM monthly_financials").fetchone()
    if row["first"] is None:
        return []
    earliest = add_months(row["first"], 23)
    periods, p = [], row["last"]
    while p >= earliest:
        if p.month in (3, 6, 9, 12):
            periods.append(p)
        p = add_months(p, -1)
    return periods


_PERIOD_SQL = """
SELECT p.property_id, p.name, p.asset_class, p.portfolio_id, pf.name AS portfolio_name, p.market,
       p.size_unit, p.rentable_size,
       cur.months AS cur_months, cur.revenue AS cur_revenue, cur.expenses AS cur_expenses, cur.noi AS cur_noi,
       cur.ids AS cur_ids,
       pri.months AS prior_months, pri.noi AS prior_noi, pri.ids AS prior_ids,
       bud.months AS bud_months, bud.noi AS bud_noi, bud.ids AS bud_ids,
       occ.record_id AS occ_record_id, occ.occupied_size, occ.rentable_size AS occ_rentable_size
FROM properties p
JOIN portfolios pf ON pf.portfolio_id = p.portfolio_id
JOIN user_portfolio_access a ON a.portfolio_id = p.portfolio_id AND a.username = %(username)s
CROSS JOIN LATERAL (
    SELECT count(*) AS months, sum(operating_revenue) AS revenue, sum(operating_expenses) AS expenses,
           sum(noi) AS noi, coalesce(array_agg(record_id ORDER BY month_end), '{}') AS ids
    FROM monthly_financials f
    WHERE f.property_id = p.property_id AND f.month_end BETWEEN %(cur_start)s AND %(period)s
) cur
CROSS JOIN LATERAL (
    SELECT count(*) AS months, sum(noi) AS noi, coalesce(array_agg(record_id ORDER BY month_end), '{}') AS ids
    FROM monthly_financials f
    WHERE f.property_id = p.property_id AND f.month_end BETWEEN %(prior_start)s AND %(prior_end)s
) pri
CROSS JOIN LATERAL (
    SELECT count(*) AS months, sum(budget_noi) AS noi, coalesce(array_agg(record_id ORDER BY month_end), '{}') AS ids
    FROM monthly_budgets b
    WHERE b.property_id = p.property_id AND b.month_end BETWEEN %(cur_start)s AND %(period)s
) bud
LEFT JOIN monthly_financials occ ON occ.property_id = p.property_id AND occ.month_end = %(period)s
WHERE (%(property_id)s::text IS NULL OR p.property_id = %(property_id)s)
ORDER BY p.property_id
"""


def evaluate_properties(conn: psycopg.Connection, username: str, period: date, rules: RuleSet,
                        property_id: str | None = None) -> list[PropertyEvaluation]:
    cur_start, _ = current_window(period)
    prior_start, prior_end = prior_window(period)
    rows = conn.execute(_PERIOD_SQL, {
        "username": username, "period": period, "cur_start": cur_start,
        "prior_start": prior_start, "prior_end": prior_end, "property_id": property_id,
    }).fetchall()
    out = []
    for r in rows:
        inputs = PeriodInputs(
            property_id=r["property_id"], period=period,
            current_months=r["cur_months"], current_revenue=r["cur_revenue"],
            current_expenses=r["cur_expenses"], current_noi=r["cur_noi"],
            prior_months=r["prior_months"], prior_noi=r["prior_noi"],
            budget_months=r["bud_months"], budget_noi=r["bud_noi"],
            occupied_size=r["occupied_size"], rentable_size=r["occ_rentable_size"],
        )
        prop = {k: r[k] for k in ("property_id", "name", "asset_class", "portfolio_id", "portfolio_name",
                                   "market", "size_unit", "rentable_size")}
        sources = {
            "monthly_financials_current_ttm": list(r["cur_ids"]),
            "monthly_financials_prior_ttm": list(r["prior_ids"]),
            "monthly_budgets_current_ttm": list(r["bud_ids"]),
            "monthly_financials_occupancy": [r["occ_record_id"]] if r["occ_record_id"] else [],
        }
        out.append(PropertyEvaluation(prop, evaluate(inputs, rules), sources))
    return out


_SERIES_SQL = """
SELECT f.month_end, f.record_id, f.operating_revenue, f.operating_expenses, f.noi,
       f.occupied_size, f.rentable_size, b.record_id AS budget_record_id, b.budget_noi
FROM monthly_financials f
JOIN properties p ON p.property_id = f.property_id
JOIN user_portfolio_access a ON a.portfolio_id = p.portfolio_id AND a.username = %(username)s
LEFT JOIN monthly_budgets b ON b.property_id = f.property_id AND b.month_end = f.month_end
WHERE f.property_id = %(property_id)s AND f.month_end <= %(period)s
ORDER BY f.month_end
"""


def monthly_series(conn: psycopg.Connection, username: str, property_id: str, period: date) -> list[dict]:
    rows = conn.execute(_SERIES_SQL, {"username": username, "property_id": property_id, "period": period}).fetchall()
    return [
        {
            "month_end": r["month_end"],
            "record_id": r["record_id"],
            "budget_record_id": r["budget_record_id"],
            "operating_revenue": r["operating_revenue"],
            "operating_expenses": r["operating_expenses"],
            "noi": r["noi"],
            "budget_noi": r["budget_noi"],
            "occupancy_pct": Decimal(r["occupied_size"]) / Decimal(r["rentable_size"]) * 100,
        }
        for r in rows
    ]


def seed_provenance(conn: psycopg.Connection) -> dict | None:
    return conn.execute(
        "SELECT generator, random_seed, content_sha256, property_count, loaded_at "
        "FROM seed_runs ORDER BY seed_run_id DESC LIMIT 1"
    ).fetchone()
