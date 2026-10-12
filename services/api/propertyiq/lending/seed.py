"""Deterministic synthetic lending data: lenders, loans, collateral, covenants, rate caps, index fixings and
property valuations. Built from the seeded properties' actual NOI, so the four required underperformers
carry the most stressed loans. Run by scripts/seed.py (and scripts/seed_loans.py on its own).

Everything here is synthetic. The index is "SYN-SOFR", a made-up series, not a published rate.
"""
from __future__ import annotations

import random
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import psycopg

from propertyiq.finance import add_months
from propertyiq.lending.calc import LoanTerms, balance_at

SEED = 20261011
INDEX = "SYN-SOFR"
VALUATION_DATES = [date(2024, 9, 30), date(2025, 9, 30), date(2026, 9, 30)]
REQUIRED = ["PIQ-001", "PIQ-002", "PIQ-003", "PIQ-004"]   # the four flagged properties: floating, tighter
CAP_RATES = {"multifamily": Decimal("5.25"), "industrial": Decimal("5.50"), "retail": Decimal("6.75"),
             "office": Decimal("7.25")}
LENDERS = [
    ("LDR-01", "Northgate Bank (synthetic)"), ("LDR-02", "Harborview Life Insurance (synthetic)"),
    ("LDR-03", "Summit Credit Partners (synthetic)"), ("LDR-04", "Bayline CMBS Trust (synthetic)"),
    ("LDR-05", "Ridgefield Debt Fund (synthetic)"), ("LDR-06", "Cobalt Regional Bank (synthetic)"),
]
FIXED_LENDERS = ["LDR-01", "LDR-02", "LDR-04", "LDR-06"]
FLOATING_LENDERS = ["LDR-03", "LDR-05", "LDR-01"]


def index_series() -> list[tuple[date, Decimal]]:
    """Monthly SYN-SOFR fixings, Oct 2021 to Sep 2026: rises through 2023, eases from late 2024."""
    path = {date(2021, 10, 31): "0.05", date(2022, 3, 31): "0.30", date(2022, 6, 30): "1.50",
            date(2022, 9, 30): "3.00", date(2022, 12, 31): "4.30", date(2023, 3, 31): "4.80",
            date(2023, 7, 31): "5.30", date(2024, 8, 31): "5.32", date(2024, 9, 30): "4.85",
            date(2024, 11, 30): "4.60", date(2024, 12, 31): "4.35", date(2025, 9, 30): "4.30",
            date(2025, 10, 31): "4.05", date(2025, 12, 31): "3.85", date(2026, 3, 31): "3.70",
            date(2026, 9, 30): "3.70"}
    knots = sorted(path)
    out, m = [], knots[0]
    while m <= knots[-1]:
        prev = max(k for k in knots if k <= m)
        nxt = min((k for k in knots if k >= m), default=prev)
        a, b = Decimal(path[prev]), Decimal(path[nxt])
        span = (nxt.year - prev.year) * 12 + nxt.month - prev.month
        step = (m.year - prev.year) * 12 + m.month - prev.month
        rate = a if span == 0 else a + (b - a) * step / span
        out.append((m, rate.quantize(Decimal("0.0001"))))
        m = add_months(m, 1)
    return out


def r50k(x: Decimal) -> Decimal:
    return (x / 50000).quantize(Decimal(1), ROUND_HALF_UP) * 50000


def seed_lending(conn: psycopg.Connection) -> dict:
    rng = random.Random(SEED)
    cur = conn.cursor()
    cur.execute("TRUNCATE loans, loan_collateral, loan_covenants, rate_caps, property_valuations, index_rates, "
                "lenders RESTART IDENTITY CASCADE")
    cur.executemany("INSERT INTO lenders (lender_id, name) VALUES (%s, %s)", LENDERS)
    cur.executemany("INSERT INTO index_rates (index_name, month_end, rate_pct) VALUES (%s, %s, %s)",
                    [(INDEX, m, r) for m, r in index_series()])

    props = cur.execute("SELECT property_id, portfolio_id, name, asset_class FROM properties ORDER BY property_id").fetchall()

    # Valuations: direct capitalization of trailing-12-month NOI at each valuation date.
    values: dict[str, dict[date, Decimal]] = {}
    for pid, _, _, cls in props:
        cap_base = CAP_RATES[cls] + Decimal(rng.randint(-25, 25)) / 100
        for i, d in enumerate(VALUATION_DATES):
            noi = cur.execute("SELECT sum(noi), count(*) FROM monthly_financials WHERE property_id = %s "
                              "AND month_end BETWEEN %s AND %s", (pid, add_months(d, -11), d)).fetchone()
            if noi[1] != 12 or noi[0] is None or noi[0] <= 0:
                continue
            cap = cap_base + (Decimal("0.25") * i if cls == "office" else Decimal("0.10") * i)
            value = (noi[0] / (cap / 100) / 10000).quantize(Decimal(1), ROUND_HALF_UP) * 10000
            values.setdefault(pid, {})[d] = value
            cur.execute("INSERT INTO property_valuations (property_id, as_of_date, value, method, cap_rate_pct) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (pid, d, value, "Direct capitalization of trailing 12-month NOI (synthetic)", cap))

    # Group properties into loans, per fund.
    loans: list[dict] = []
    by_fund: dict[str, list[tuple]] = {}
    for p in props:
        if p[0] in values and VALUATION_DATES[0] in values[p[0]]:
            by_fund.setdefault(p[1], []).append(p)
    for fund, plist in sorted(by_fund.items()):
        others = [p for p in plist if p[0] not in REQUIRED]
        rng.shuffle(others)
        unlevered, others = others[:3], others[3:]
        pool, others = others[:3], others[3:]
        loans.append({"fund": fund, "kind": "pool", "collateral": [(p, Decimal(100)) for p in pool],
                      "name": f"{fund} pool loan ({len(pool)} properties)"})
        if fund == "FUND-I":   # one property financed by two loans: a senior and a junior piece
            split, others = others[0], others[1:]
            loans.append({"fund": fund, "kind": "senior", "collateral": [(split, Decimal(60))],
                          "name": f"{split[2]} senior note (60%)"})
            loans.append({"fund": fund, "kind": "junior", "collateral": [(split, Decimal(40))],
                          "name": f"{split[2]} junior note (40%)"})
        for p in sorted([p for p in plist if p[0] in REQUIRED] + others):
            loans.append({"fund": fund, "kind": "required" if p[0] in REQUIRED else "single",
                          "collateral": [(p, Decimal(100))], "name": f"{p[2]} first mortgage"})

    loans.sort(key=lambda l: (l["fund"], l["collateral"][0][0][0], l["kind"]))
    index = dict(index_series())
    stats = {"loans": 0, "fixed": 0, "floating": 0}
    for n, ln in enumerate(loans, start=1):
        loan_id = f"LN-{n:04d}"
        floating = ln["kind"] in ("required", "junior") or (ln["kind"] == "single" and rng.random() < 0.2)
        if floating:
            orig = add_months(date(2022, 6, 30), rng.randint(0, 15))       # Jun 2022 – Sep 2023
            term = rng.choice([36, 48, 60])
            while add_months(orig, term) < date(2026, 12, 31):   # extension options exercised
                term += 12
            ltv = Decimal(rng.randint(64, 70) if ln["kind"] == "required" else rng.randint(56, 65)) / 100
            spread = rng.choice([275, 300, 325, 350, 375])
            if ln["kind"] == "junior":
                spread += 300
            row = dict(rate_type="floating", fixed_rate_pct=None, index_name=INDEX, spread_bps=spread,
                       amortization_months=0, io_months=0, lender=rng.choice(FLOATING_LENDERS))
        else:
            orig = add_months(date(2016, 3, 31), rng.randint(0, 90))       # Mar 2016 – Sep 2023
            year_rate = {2016: "4.10", 2017: "4.35", 2018: "4.70", 2019: "4.15", 2020: "3.30",
                         2021: "3.20", 2022: "5.10", 2023: "6.40"}[orig.year]
            term = rng.choice([84, 120, 120])
            while add_months(orig, term) < date(2026, 12, 31):
                term += 24
            ltv = Decimal(rng.randint(50, 64)) / 100
            row = dict(rate_type="fixed", fixed_rate_pct=Decimal(year_rate) + Decimal(rng.randint(-20, 30)) / 100,
                       index_name=None, spread_bps=None, amortization_months=360,
                       io_months=rng.choice([0, 0, 24, 36, 60]), lender=rng.choice(FIXED_LENDERS))
        maturity = add_months(orig, term)
        value0 = sum(values[p[0]][VALUATION_DATES[0]] * alloc / 100 for p, alloc in ln["collateral"])
        principal = r50k(value0 * ltv)
        cur.execute(
            "INSERT INTO loans (loan_id, fund_id, lender_id, name, original_principal, rate_type, fixed_rate_pct, "
            "index_name, spread_bps, amortization_months, io_months, origination_date, maturity_date) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (loan_id, ln["fund"], row["lender"], ln["name"], principal, row["rate_type"], row["fixed_rate_pct"],
             row["index_name"], row["spread_bps"], row["amortization_months"], row["io_months"], orig, maturity))
        for p, alloc in ln["collateral"]:
            cur.execute("INSERT INTO loan_collateral (loan_id, property_id, allocation_pct) VALUES (%s, %s, %s)",
                        (loan_id, p[0], alloc))
        tight = ln["kind"] == "required"
        if ln["kind"] == "junior":   # a junior lender tests only coverage, at a lower bar
            covenants = [("min_dscr", Decimal("1.10"))]
        else:
            covenants = [("min_dscr", Decimal("1.35") if tight else Decimal("1.25") if floating
                          else rng.choice([Decimal("1.20"), Decimal("1.25")]))]
            if floating or rng.random() < 0.5:
                covenants.append(("max_ltv", Decimal("70") if tight or not floating else Decimal("75")))
            if floating:
                covenants.append(("min_debt_yield", Decimal("8.5") if tight else Decimal("7.5")))
        for kind, threshold in covenants:
            cur.execute("INSERT INTO loan_covenants (loan_id, kind, threshold) VALUES (%s, %s, %s)",
                        (loan_id, kind, threshold))
        if floating:
            # Most caps run to maturity; some were bought for a shorter term and have already expired.
            expiry = maturity if rng.random() < 0.6 else add_months(orig, 24)
            cur.execute("INSERT INTO rate_caps (loan_id, strike_pct, expiry_date) VALUES (%s, %s, %s)",
                        (loan_id, Decimal(rng.choice(["3.00", "3.50", "4.00", "4.50"])), expiry))
        stats["loans"] += 1
        stats["floating" if floating else "fixed"] += 1
        # Sanity: the loan must have a balance at the latest period.
        assert balance_at(LoanTerms(loan_id, principal, row["rate_type"], row["fixed_rate_pct"], row["index_name"],
                                    row["spread_bps"], row["amortization_months"], row["io_months"], orig, maturity),
                          date(2026, 9, 30)) is not None or maturity < date(2026, 9, 30)
    stats["valuations"] = sum(len(v) for v in values.values())
    stats["index_months"] = len(index)
    return stats
