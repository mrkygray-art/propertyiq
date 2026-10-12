"""Debt dashboard: loan math against an independent float reference, covenant edges, reconciliation, fund scope."""
import os
from datetime import date
from decimal import Decimal as D

import psycopg
import pytest

from propertyiq.lending import calc
from propertyiq.lending.calc import CollateralInput, LoanTerms


def fixed(**over):
    base = dict(loan_id="LN-9999", original_principal=D(1_000_000), rate_type="fixed", fixed_rate_pct=D(6),
                index_name=None, spread_bps=None, amortization_months=360, io_months=0,
                origination_date=date(2020, 1, 31), maturity_date=date(2030, 1, 31))
    return LoanTerms(**{**base, **over})


def floating(**over):
    base = dict(loan_id="LN-9998", original_principal=D(10_000_000), rate_type="floating", fixed_rate_pct=None,
                index_name="X", spread_bps=300, amortization_months=0, io_months=0,
                origination_date=date(2023, 1, 31), maturity_date=date(2028, 1, 31),
                cap_strike_pct=D(4), cap_expiry=date(2025, 1, 31))
    return LoanTerms(**{**base, **over})


def ref_payment(p, annual_pct, n):
    r = annual_pct / 100 / 12
    return p * r / (1 - (1 + r) ** -n)


def ref_balance(p, annual_pct, n, k):
    r = annual_pct / 100 / 12
    pmt = ref_payment(p, annual_pct, n)
    return p * (1 + r) ** k - pmt * ((1 + r) ** k - 1) / r


# ---------------------------------------------------------------- pure math

def test_fixed_payment_and_balance_match_float_reference():
    t = fixed()
    assert round(ref_payment(1_000_000, 6, 360), 2) == 5995.51
    interest, principal = calc.monthly_payment(t, date(2020, 2, 29), None)
    assert abs(float(interest + principal) - 5995.51) < 0.01
    for k in (1, 12, 60, 120):
        month = date(2020 + (1 + k) // 12, (1 + k) % 12 or 12, 1)
        assert abs(float(calc.fixed_balance_after(t, k)) - ref_balance(1_000_000, 6, 360, k)) < 0.01


def test_ttm_debt_service_is_twelve_payments():
    ds = calc.ttm_debt_service(fixed(), date(2024, 12, 31), None)
    assert ds.months == 12 and abs(float(ds.total) - 12 * ref_payment(1_000_000, 6, 360)) < 0.05


def test_interest_only_period_then_amortization():
    t = fixed(io_months=24)
    assert calc.balance_at(t, date(2022, 1, 31)) == D(1_000_000)               # payment 24: still IO
    i, p = calc.monthly_payment(t, date(2021, 6, 30), None)
    assert p == 0 and i == D(5000)
    assert calc.balance_at(t, date(2022, 2, 28)) < D(1_000_000)                # first amortizing payment
    assert abs(float(calc.fixed_balance_after(t, 36)) - ref_balance(1_000_000, 6, 360, 12)) < 0.01


def test_floating_rate_uses_cap_until_it_expires():
    idx = lambda name, m: D(5)
    t = floating()
    assert calc.annual_rate_pct(t, date(2024, 6, 30), idx) == D(7)             # min(5, 4) + 3
    assert calc.annual_rate_pct(t, date(2025, 6, 30), idx) == D(8)             # cap expired: 5 + 3
    ds = calc.ttm_debt_service(t, date(2025, 6, 30), idx)
    expected = 10_000_000 * (0.07 / 12) * 7 + 10_000_000 * (0.08 / 12) * 5    # Jul 2024–Jan 2025 capped
    assert abs(float(ds.total) - expected) < 0.01 and ds.principal == 0


def metrics(dscr_noi, value, terms=None):
    terms = terms or floating(cap_strike_pct=None, cap_expiry=None)
    idx = lambda name, m: D(2)                                                  # 5% all-in on 10m = 500k/yr
    return calc.loan_metrics(terms, date(2025, 12, 31),
                             [CollateralInput("PIQ-900", D(100), dscr_noi, value)], idx)


def test_covenants_exactly_at_threshold_do_not_breach():
    m = metrics(D(625_000), D(10_000_000) / D("0.7"))                           # DSCR 1.25, LTV 70%
    assert m.dscr.quantize(D("0.0001")) == D("1.25")   # 1.2499999… before rounding
    assert calc.covenant_status("min_dscr", D("1.25"), m)[0] == "watch"
    assert calc.covenant_status("max_ltv", D(70), m)[0] == "watch"
    assert calc.covenant_status("min_dscr", D("1.2501"), m)[0] == "breach"
    assert calc.covenant_status("max_ltv", D("69.99"), m)[0] == "breach"
    assert calc.covenant_status("min_dscr", D("1.10"), m)[0] == "pass"


def test_missing_inputs_are_not_calculable_with_a_reason():
    m = metrics(None, None)
    assert m.dscr is None and m.ltv_pct is None
    assert calc.covenant_status("min_dscr", D("1.25"), m) == ("not_calculable", None)
    assert any("NOI not calculable" in n for n in m.notes)
    new = metrics(D(600_000), D(15_000_000), floating(origination_date=date(2025, 6, 30), cap_strike_pct=None))
    assert new.dscr is None and any("of 12 monthly payments" in n for n in new.notes)


def test_allocation_splits_noi_and_value():
    idx = lambda name, m: D(2)
    m = calc.loan_metrics(floating(cap_strike_pct=None), date(2025, 12, 31),
                          [CollateralInput("A", D(60), D(1_000_000), D(20_000_000)),
                           CollateralInput("B", D(100), D(500_000), D(5_000_000))], idx)
    assert m.noi == D(1_100_000) and m.value == D(17_000_000)


# ---------------------------------------------------------------- API (database)

@pytest.fixture(scope="module")
def client():
    from propertyiq.config import get_settings
    try:
        with psycopg.connect(get_settings().dsn("piq_readonly")) as c:
            if c.execute("SELECT count(*) FROM loans").fetchone()[0] == 0:
                pytest.skip("no synthetic loans seeded (run scripts/seed_loans.py)")
    except Exception as exc:
        pytest.skip(f"lending tables not available: {exc}")
    from fastapi.testclient import TestClient
    from propertyiq.main import app
    with TestClient(app) as c:
        yield c


def headers(client, user):
    r = client.post("/api/auth/login", json={"username": user, "password": os.environ["PIQ_DEMO_PASSWORD"]})
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.mark.db
def test_debt_needs_sign_in(client):
    assert client.get("/api/debt/summary").status_code == 401
    assert client.get("/api/debt/loans/LN-0001").status_code == 401


@pytest.mark.db
def test_restricted_user_sees_only_fund_ii_loans(client):
    full = client.get("/api/debt/summary", headers=headers(client, "analyst")).json()
    h = headers(client, "restricted")
    mine = client.get("/api/debt/summary", headers=h).json()
    assert {l["fund_id"] for l in mine["loans"]} == {"FUND-II"}
    assert mine["scope"]["funds"] == ["FUND-II"] and len(mine["loans"]) < len(full["loans"])
    fund_i = next(l["loan_id"] for l in full["loans"] if l["fund_id"] == "FUND-I")
    hidden = client.get(f"/api/debt/loans/{fund_i}", headers=h)
    missing = client.get("/api/debt/loans/LN-9999", headers=h)
    assert hidden.status_code == missing.status_code == 404 and hidden.json() == missing.json()
    # Aggregates are computed from the user's loans only.
    assert mine["totals"]["balance"] == pytest.approx(sum(l["balance"] for l in mine["loans"]), abs=0.05)


@pytest.mark.db
def test_bad_inputs(client):
    h = headers(client, "analyst")
    assert client.get("/api/debt/loans/1;DROP", headers=h).status_code == 422
    assert client.get("/api/debt/summary?period=2026-09-15", headers=h).status_code == 400


@pytest.mark.db
def test_totals_reconcile_and_detail_matches_summary(client):
    h = headers(client, "analyst")
    s = client.get("/api/debt/summary", headers=h).json()
    loans = s["loans"]
    t = s["totals"]
    assert t["balance"] == pytest.approx(sum(l["balance"] for l in loans), abs=0.05)
    assert t["fixed_balance"] + t["floating_balance"] == pytest.approx(t["balance"], abs=0.05)
    assert sum(w["count"] for w in s["maturity_wall"]) == len(loans)
    assert sum(w["fixed"] + w["floating"] for w in s["maturity_wall"]) == pytest.approx(
        sum(l["balloon"] for l in loans), abs=0.5)
    assert t["breach_count"] == sum(l["status"] == "breach" for l in loans)
    for row in loans[:5]:
        d = client.get(f"/api/debt/loans/{row['loan_id']}", headers=h).json()
        assert d["metrics"]["dscr"] == row["dscr"] and d["metrics"]["balance"] == row["balance"]
        assert d["history"][-1]["dscr"] == row["dscr"]
        assert d["sources"]["monthly_financials"] and d["payload_sha256"]


@pytest.mark.db
def test_dscr_recomputed_independently_from_raw_rows(client):
    """Recompute one floating loan's DSCR straight from the tables with plain floats."""
    from propertyiq.config import get_settings
    h = headers(client, "analyst")
    s = client.get("/api/debt/summary", headers=h).json()
    row = next(l for l in s["loans"] if l["rate_type"] == "floating" and len(l["collateral"]) == 1)
    period = date.fromisoformat(s["period"])
    with psycopg.connect(get_settings().dsn("piq_readonly")) as c:
        loan = c.execute("SELECT original_principal, spread_bps, index_name FROM loans WHERE loan_id = %s",
                         (row["loan_id"],)).fetchone()
        cap = c.execute("SELECT strike_pct, expiry_date FROM rate_caps WHERE loan_id = %s", (row["loan_id"],)).fetchone()
        pid, alloc = c.execute("SELECT property_id, allocation_pct FROM loan_collateral WHERE loan_id = %s",
                               (row["loan_id"],)).fetchone()
        noi = c.execute("SELECT sum(noi) FROM monthly_financials WHERE property_id = %s AND month_end > %s "
                        "AND month_end <= %s", (pid, date(period.year - 1, period.month, period.day), period)).fetchone()[0]
        fixings = c.execute("SELECT month_end, rate_pct FROM index_rates WHERE index_name = %s AND month_end > %s "
                            "AND month_end <= %s", (loan[2], date(period.year - 1, period.month, period.day), period)).fetchall()
    ds = 0.0
    for month, rate in fixings:
        r = float(rate)
        if cap and month <= cap[1]:
            r = min(r, float(cap[0]))
        ds += float(loan[0]) * (r + loan[1] / 100) / 100 / 12
    assert len(fixings) == 12
    assert round(float(noi) * float(alloc) / 100 / ds, 3) == pytest.approx(row["dscr"], abs=0.001)
