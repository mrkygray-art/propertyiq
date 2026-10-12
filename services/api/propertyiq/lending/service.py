"""Builds the debt dashboard and loan detail payloads from vetted SQL (store.py) and Decimal math (calc.py)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import psycopg

from propertyiq.lending import calc, store
from propertyiq.lending.calc import HUNDRED, ZERO
from propertyiq.schemas import money, pct

STATUS_ORDER = {"breach": 0, "watch": 1, "not_calculable": 2, "pass": 3}
KIND_LABEL = {"min_dscr": "Minimum DSCR", "max_ltv": "Maximum LTV", "min_debt_yield": "Minimum debt yield"}
DEFINITIONS = {
    "noi": "Trailing 12 months actual NOI of each collateral property, times the share pledged to this loan",
    "debt_service": "Interest plus scheduled principal paid in the same 12 months",
    "dscr": "NOI / debt service",
    "ltv": "Outstanding principal / (latest valuation on or before the period × share pledged)",
    "debt_yield": "NOI / outstanding principal",
    "valuation": "Direct capitalization of trailing 12-month NOI at a synthetic cap rate, once a year",
    "floating_rate": "SYN-SOFR (a synthetic index) capped at the rate cap's strike while the cap is in force, plus the spread",
    "watch": "Within 5% of a covenant threshold. Exactly at the threshold passes. Covenants are tested at 4 decimal places.",
}


def ratio(d: Decimal | None) -> float | None:
    return None if d is None else float(d.quantize(Decimal("0.001")))


def covenant_results(loan: dict, m: calc.LoanMetrics) -> list[dict]:
    out = []
    for c in loan["covenants"]:
        threshold = Decimal(str(c["threshold"]))
        status, value = calc.covenant_status(c["kind"], threshold, m)
        out.append({"kind": c["kind"], "label": KIND_LABEL[c["kind"]], "threshold": float(threshold),
                    "value": ratio(value) if c["kind"] == "min_dscr" else pct(value), "status": status,
                    "covenant_id": c["covenant_id"]})
    return out


def worst(results: list[dict]) -> str:
    return min((r["status"] for r in results), key=STATUS_ORDER.__getitem__, default="pass")


def months_to(period: date, d: date) -> int:
    return calc.months_between(period, d)


def summary(conn: psycopg.Connection, username: str, period: date, periods: list[date]) -> dict:
    loans = store.load_loans(conn, username)
    index = store.IndexBook(conn)
    book = store.CollateralBook(conn, username, [c["property_id"] for l in loans for c in l["collateral"] or []])

    rows, wall = [], {}
    tot = {k: ZERO for k in ("balance", "fixed", "floating", "noi_dscr", "ds", "value", "noi_ltv", "rate_w", "m12", "m24")}
    breaches = watch = 0
    for loan in loans:
        terms = store.terms_of(loan)
        m = calc.loan_metrics(terms, period, book.inputs(loan, period), index)
        cov = covenant_results(loan, m)
        status = worst(cov)
        breaches += status == "breach"
        watch += status == "watch"
        bal = m.balance or ZERO
        tot["balance"] += bal
        tot[loan["rate_type"]] += bal
        if m.rate_pct is not None:
            tot["rate_w"] += bal * m.rate_pct
        if m.noi is not None and m.debt_service.total:
            tot["noi_dscr"] += m.noi
            tot["ds"] += m.debt_service.total
        if m.value:
            tot["value"] += m.value
            tot["noi_ltv"] += bal
        mt = months_to(period, loan["maturity_date"])
        if 0 < mt <= 12:
            tot["m12"] += bal
        if 0 < mt <= 24:
            tot["m24"] += bal
        due = calc.balloon(terms)
        y = wall.setdefault(loan["maturity_date"].year, {"year": loan["maturity_date"].year, "fixed": ZERO,
                                                          "floating": ZERO, "count": 0, "loans": []})
        y[loan["rate_type"]] += due
        y["count"] += 1
        y["loans"].append(loan["loan_id"])
        cap_active = loan["cap_strike_pct"] is not None and loan["cap_expiry"] >= period
        rows.append({
            "loan_id": loan["loan_id"], "name": loan["name"], "fund_id": loan["fund_id"], "fund_name": loan["fund_name"],
            "lender": loan["lender_name"], "rate_type": loan["rate_type"], "rate_pct": pct(m.rate_pct),
            "balance": money(m.balance), "original_principal": money(loan["original_principal"]),
            "maturity_date": loan["maturity_date"].isoformat(), "months_to_maturity": mt,
            "balloon": money(due), "noi": money(m.noi), "debt_service": money(m.debt_service.total),
            "value": money(m.value), "dscr": ratio(m.dscr), "ltv_pct": pct(m.ltv_pct),
            "debt_yield_pct": pct(m.debt_yield_pct), "covenants": cov, "status": status,
            "collateral": [{"property_id": c["property_id"], "name": c["name"], "allocation_pct": float(c["allocation_pct"])}
                           for c in loan["collateral"] or []],
            "cap": None if loan["cap_strike_pct"] is None else {
                "strike_pct": float(loan["cap_strike_pct"]), "expiry_date": loan["cap_expiry"].isoformat(),
                "active": cap_active},
            "notes": m.notes,
        })

    # Portfolio trend: aggregate DSCR and LTV for every period (same loans, same scope).
    trend = []
    for p in sorted(periods):
        noi = ds = bal = val = ZERO
        for loan in loans:
            terms = store.terms_of(loan)
            m = calc.loan_metrics(terms, p, book.inputs(loan, p), index)
            if m.noi is not None and m.debt_service.total:
                noi += m.noi
                ds += m.debt_service.total
            if m.value and m.balance is not None:
                bal += m.balance
                val += m.value
        trend.append({"period": p.isoformat(), "aggregate_dscr": ratio(noi / ds) if ds else None,
                      "aggregate_ltv_pct": pct(bal / val * HUNDRED) if val else None})

    rows.sort(key=lambda r: (STATUS_ORDER[r["status"]], r["dscr"] if r["dscr"] is not None else 99))
    return {
        "synthetic_data": True, "period": period.isoformat(), "periods": [p.isoformat() for p in periods],
        "scope": {"username": username, "funds": sorted({l["fund_id"] for l in loans}), "loan_count": len(loans)},
        "totals": {
            "balance": money(tot["balance"]), "fixed_balance": money(tot["fixed"]), "floating_balance": money(tot["floating"]),
            "floating_share_pct": pct(tot["floating"] / tot["balance"] * HUNDRED) if tot["balance"] else None,
            "weighted_rate_pct": pct(tot["rate_w"] / tot["balance"]) if tot["balance"] else None,
            "aggregate_dscr": ratio(tot["noi_dscr"] / tot["ds"]) if tot["ds"] else None,
            "aggregate_ltv_pct": pct(tot["noi_ltv"] / tot["value"] * HUNDRED) if tot["value"] else None,
            "breach_count": breaches, "watch_count": watch,
            "maturing_12m_balance": money(tot["m12"]), "maturing_24m_balance": money(tot["m24"]),
        },
        "maturity_wall": [{**w, "fixed": money(w["fixed"]), "floating": money(w["floating"])}
                          for _, w in sorted(wall.items())],
        "trend": trend, "loans": rows, "definitions": DEFINITIONS,
    }


def detail(conn: psycopg.Connection, username: str, loan_id: str, period: date, periods: list[date]) -> dict | None:
    found = store.load_loans(conn, username, loan_id)
    if not found:
        return None
    loan = found[0]
    terms = store.terms_of(loan)
    index = store.IndexBook(conn)
    book = store.CollateralBook(conn, username, [c["property_id"] for c in loan["collateral"] or []])

    history = []
    for p in sorted(periods):
        m = calc.loan_metrics(terms, p, book.inputs(loan, p), index)
        history.append({"period": p.isoformat(), "dscr": ratio(m.dscr), "ltv_pct": pct(m.ltv_pct),
                        "debt_yield_pct": pct(m.debt_yield_pct), "noi": money(m.noi),
                        "debt_service": money(m.debt_service.total), "balance": money(m.balance),
                        "rate_pct": pct(m.rate_pct), "covenants": covenant_results(loan, m)})

    index.used.clear()
    inputs = book.inputs(loan, period)
    m = calc.loan_metrics(terms, period, inputs, index)
    noi_rows, _ = book.at(period)
    collateral = []
    for c, ci in zip(loan["collateral"] or [], inputs):
        collateral.append({
            "property_id": c["property_id"], "name": c["name"], "asset_class": c["asset_class"], "market": c["market"],
            "allocation_pct": float(c["allocation_pct"]), "ttm_noi": money(ci.ttm_noi),
            "allocated_noi": money(ci.ttm_noi * ci.allocation_pct / HUNDRED) if ci.ttm_noi is not None else None,
            "value": money(ci.value), "allocated_value": money(ci.value * ci.allocation_pct / HUNDRED) if ci.value else None,
            "valuation_date": ci.valuation_date.isoformat() if ci.valuation_date else None,
            "valuation_id": ci.valuation_id,
        })
    sources = {
        "monthly_financials": sorted(i for r in noi_rows.values() for i in r["ids"]),
        "property_valuations": sorted(c["valuation_id"] for c in collateral if c["valuation_id"]),
        "index_rates": sorted(index.used),
        "loan_covenants": [c["covenant_id"] for c in loan["covenants"]],
    }
    return {
        "synthetic_data": True, "period": period.isoformat(), "periods": [p.isoformat() for p in periods],
        "loan": {
            "loan_id": loan["loan_id"], "name": loan["name"], "fund_id": loan["fund_id"], "fund_name": loan["fund_name"],
            "lender": loan["lender_name"], "rate_type": loan["rate_type"], "fixed_rate_pct": pct(loan["fixed_rate_pct"]),
            "index_name": loan["index_name"], "spread_bps": loan["spread_bps"],
            "original_principal": money(loan["original_principal"]), "amortization_months": loan["amortization_months"],
            "io_months": loan["io_months"], "origination_date": loan["origination_date"].isoformat(),
            "maturity_date": loan["maturity_date"].isoformat(), "balloon": money(calc.balloon(terms)),
            "cap": None if loan["cap_strike_pct"] is None else {
                "strike_pct": float(loan["cap_strike_pct"]), "expiry_date": loan["cap_expiry"].isoformat(),
                "active": loan["cap_expiry"] >= period},
        },
        "metrics": {"balance": money(m.balance), "noi": money(m.noi), "value": money(m.value),
                    "debt_service": money(m.debt_service.total), "interest": money(m.debt_service.interest),
                    "principal": money(m.debt_service.principal), "rate_pct": pct(m.rate_pct),
                    "dscr": ratio(m.dscr), "ltv_pct": pct(m.ltv_pct), "debt_yield_pct": pct(m.debt_yield_pct),
                    "notes": m.notes},
        "covenants": covenant_results(loan, m), "status": worst(covenant_results(loan, m)),
        "collateral": collateral, "history": history, "sources": sources, "definitions": DEFINITIONS,
    }
