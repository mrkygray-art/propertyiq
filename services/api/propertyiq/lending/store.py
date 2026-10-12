"""Vetted, parameterized SQL for the debt dashboard. Every loan query joins user_portfolio_access, so a user
only ever sees loans (and their collateral) in funds they are granted. Read-only connection."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import psycopg

from propertyiq.finance import add_months
from propertyiq.lending.calc import CollateralInput, LoanTerms

_LOANS_SQL = """
SELECT l.loan_id, l.name, l.fund_id, pf.name AS fund_name, l.lender_id, ld.name AS lender_name,
       l.original_principal, l.rate_type, l.fixed_rate_pct, l.index_name, l.spread_bps,
       l.amortization_months, l.io_months, l.origination_date, l.maturity_date,
       c.strike_pct AS cap_strike_pct, c.expiry_date AS cap_expiry,
       coalesce((SELECT json_agg(json_build_object('kind', k.kind, 'threshold', k.threshold,
                 'covenant_id', k.covenant_id, 'noi_definition', k.noi_definition) ORDER BY k.kind)
                 FROM loan_covenants k WHERE k.loan_id = l.loan_id), '[]') AS covenants,
       (SELECT json_agg(json_build_object('property_id', p.property_id, 'name', p.name,
                 'asset_class', p.asset_class, 'market', p.market, 'allocation_pct', lc.allocation_pct)
                 ORDER BY p.property_id)
        FROM loan_collateral lc JOIN properties p ON p.property_id = lc.property_id
        WHERE lc.loan_id = l.loan_id) AS collateral
FROM loans l
JOIN portfolios pf ON pf.portfolio_id = l.fund_id
JOIN user_portfolio_access a ON a.portfolio_id = l.fund_id AND a.username = %(username)s
JOIN lenders ld ON ld.lender_id = l.lender_id
LEFT JOIN rate_caps c ON c.loan_id = l.loan_id
WHERE (%(loan_id)s::text IS NULL OR l.loan_id = %(loan_id)s)
ORDER BY l.loan_id
"""

# TTM NOI per property, only for properties in the user's funds.
_NOI_SQL = """
SELECT f.property_id, count(*) AS months, sum(f.noi) AS noi, array_agg(f.record_id ORDER BY f.month_end) AS ids
FROM monthly_financials f
JOIN properties p ON p.property_id = f.property_id
JOIN user_portfolio_access a ON a.portfolio_id = p.portfolio_id AND a.username = %(username)s
WHERE f.month_end BETWEEN %(start)s AND %(period)s AND f.property_id = ANY(%(ids)s)
GROUP BY f.property_id
"""

_VALUATION_SQL = """
SELECT DISTINCT ON (v.property_id) v.property_id, v.valuation_id, v.as_of_date, v.value, v.method, v.cap_rate_pct
FROM property_valuations v
JOIN properties p ON p.property_id = v.property_id
JOIN user_portfolio_access a ON a.portfolio_id = p.portfolio_id AND a.username = %(username)s
WHERE v.as_of_date <= %(period)s AND v.property_id = ANY(%(ids)s)
ORDER BY v.property_id, v.as_of_date DESC
"""


def debt_periods(conn: psycopg.Connection) -> list[date]:
    """Quarter-ends with a full trailing-12-month window of actuals, newest first."""
    row = conn.execute("SELECT min(month_end) AS first, max(month_end) AS last FROM monthly_financials").fetchone()
    if row["first"] is None:
        return []
    earliest, out, p = add_months(row["first"], 11), [], row["last"]
    while p >= earliest:
        if p.month in (3, 6, 9, 12):
            out.append(p)
        p = add_months(p, -1)
    return out


def load_loans(conn: psycopg.Connection, username: str, loan_id: str | None = None) -> list[dict]:
    return conn.execute(_LOANS_SQL, {"username": username, "loan_id": loan_id}).fetchall()


def terms_of(row: dict) -> LoanTerms:
    return LoanTerms(
        loan_id=row["loan_id"], original_principal=row["original_principal"], rate_type=row["rate_type"],
        fixed_rate_pct=row["fixed_rate_pct"], index_name=row["index_name"], spread_bps=row["spread_bps"],
        amortization_months=row["amortization_months"], io_months=row["io_months"],
        origination_date=row["origination_date"], maturity_date=row["maturity_date"],
        cap_strike_pct=row["cap_strike_pct"], cap_expiry=row["cap_expiry"],
    )


class IndexBook:
    """Monthly index fixings; remembers which records were used, for the calculation trail."""

    def __init__(self, conn: psycopg.Connection):
        self.rows = {(r["index_name"], r["month_end"]): (r["rate_pct"], r["record_id"])
                     for r in conn.execute("SELECT index_name, month_end, rate_pct, record_id FROM index_rates")}
        self.used: set[int] = set()

    def __call__(self, name: str, month_end: date) -> Decimal | None:
        hit = self.rows.get((name, month_end))
        if hit is None:
            return None
        self.used.add(hit[1])
        return hit[0]


class CollateralBook:
    """TTM NOI and latest valuation for every property, per period, loaded in bulk."""

    def __init__(self, conn: psycopg.Connection, username: str, property_ids: list[str]):
        self.conn, self.username, self.ids = conn, username, sorted(set(property_ids))
        self.cache: dict[date, tuple[dict, dict]] = {}

    def at(self, period: date) -> tuple[dict, dict]:
        if period not in self.cache:
            args = {"username": self.username, "ids": self.ids, "period": period, "start": add_months(period, -11)}
            noi = {r["property_id"]: r for r in self.conn.execute(_NOI_SQL, args)}
            val = {r["property_id"]: r for r in self.conn.execute(_VALUATION_SQL, args)}
            self.cache[period] = (noi, val)
        return self.cache[period]

    def inputs(self, loan: dict, period: date) -> list[CollateralInput]:
        noi, val = self.at(period)
        out = []
        for c in loan["collateral"] or []:
            n, v = noi.get(c["property_id"]), val.get(c["property_id"])
            out.append(CollateralInput(
                property_id=c["property_id"], allocation_pct=Decimal(str(c["allocation_pct"])),
                ttm_noi=n["noi"] if n and n["months"] == 12 else None,
                value=v["value"] if v else None,
                valuation_id=v["valuation_id"] if v else None,
                valuation_date=v["as_of_date"] if v else None,
            ))
        return out
