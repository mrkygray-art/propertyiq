"""Display formatting shared by the PDF (the web app applies the same rules in TypeScript).
Dollars to whole numbers, ratios to 2 decimals, percents to 1 decimal (2 for cap rate and debt constant)."""
from __future__ import annotations

from decimal import Decimal


def fmt_usd(v: Decimal | None) -> str:
    if v is None:
        return "n/a"
    return f"-${-v:,.0f}" if v < 0 else f"${v:,.0f}"


def fmt_ratio(v: Decimal | None) -> str:
    return "n/a" if v is None else f"{v:.2f}"


def fmt_pct(v: Decimal | None, places: int = 1) -> str:
    return "n/a" if v is None else f"{v * 100:.{places}f}%"


# (metric key, label, plain-language meaning)
METRIC_ROWS = [
    ("egi", "Effective gross income (EGI)", "Income actually collected after vacancy and non-payment"),
    ("noi", "Net operating income (NOI)", "What the property earns after running costs, before loan payments"),
    ("monthly_payment", "Monthly loan payment", "Standard amortizing payment"),
    ("annual_debt_service", "Annual debt service", "Total loan payments in a year"),
    ("interest_only_debt_service", "Interest-only debt service", "Yearly payments during the interest-only period"),
    ("dscr", "DSCR", "How many times NOI covers the loan payments"),
    ("dscr_interest_only", "DSCR (interest-only)", "Coverage during the interest-only period"),
    ("ltv", "Loan-to-value (LTV)", "How much of the value is borrowed"),
    ("debt_yield", "Debt yield", "Lender's return if it had to take the property"),
    ("cap_rate", "Cap rate (implied)", "Property's yield at the stated value"),
    ("break_even_occupancy", "Break-even occupancy", "How full it must be to pay all bills and the loan"),
    ("debt_constant", "Debt constant", "Annual loan payments as a % of the loan"),
]

_KIND = {
    "egi": "usd", "noi": "usd", "monthly_payment": "usd", "annual_debt_service": "usd",
    "interest_only_debt_service": "usd", "dscr": "ratio", "dscr_interest_only": "ratio", "ltv": "pct",
    "debt_yield": "pct", "cap_rate": "pct2", "break_even_occupancy": "pct", "debt_constant": "pct2",
}


def fmt_metric(key: str, v: Decimal | None) -> str:
    kind = _KIND[key]
    if kind == "usd":
        return fmt_usd(v)
    if kind == "ratio":
        return fmt_ratio(v)
    return fmt_pct(v, 2 if kind == "pct2" else 1)
