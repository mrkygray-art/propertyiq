"""Deterministic loan math (Decimal). No database access here; callers pass terms, index fixings and inputs.

Conventions (shown in the UI's calculation trail):
* Payments are monthly, in arrears; the first payment is at the end of the month after origination.
* Fixed loans: interest only for io_months, then a level payment amortizing over amortization_months
  (amortization_months = 0 means interest only to maturity). Rate / 12 per month.
* Floating loans are interest only. Monthly rate = (index + spread) / 12, with the index capped at the
  rate cap's strike while the cap is in force.
* Debt service for a period = the sum of the 12 monthly payments ending at the period (trailing 12 months),
  the same window as NOI.
* DSCR = NOI / debt service. LTV = balance / value. Debt yield = NOI / balance. A zero or missing
  denominator gives "not calculable" with a reason, never a number.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable

from propertyiq.finance import add_months

ZERO = Decimal(0)
HUNDRED = Decimal(100)
TWELVE = Decimal(12)
WATCH_MARGIN = Decimal("0.05")   # within 5% of a covenant threshold counts as "watch"
TEST_PRECISION = Decimal("0.0001")


@dataclass(frozen=True)
class LoanTerms:
    loan_id: str
    original_principal: Decimal
    rate_type: str                      # fixed | floating
    fixed_rate_pct: Decimal | None
    index_name: str | None
    spread_bps: int | None
    amortization_months: int
    io_months: int
    origination_date: date
    maturity_date: date
    cap_strike_pct: Decimal | None = None
    cap_expiry: date | None = None


def months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month)


def payment_number(terms: LoanTerms, month_end: date) -> int:
    """1 for the first payment month; 0 or less before it."""
    return months_between(terms.origination_date, month_end)


def annual_rate_pct(terms: LoanTerms, month_end: date, index: Callable[[str, date], Decimal | None]) -> Decimal | None:
    if terms.rate_type == "fixed":
        return terms.fixed_rate_pct
    fixing = index(terms.index_name, month_end)
    if fixing is None:
        return None
    if terms.cap_strike_pct is not None and terms.cap_expiry is not None and month_end <= terms.cap_expiry:
        fixing = min(fixing, terms.cap_strike_pct)
    return fixing + Decimal(terms.spread_bps) / HUNDRED


def level_payment(principal: Decimal, monthly_rate: Decimal, n: int) -> Decimal:
    if monthly_rate == 0:
        return principal / n
    return principal * monthly_rate / (1 - (1 + monthly_rate) ** -n)


def fixed_balance_after(terms: LoanTerms, k: int) -> Decimal:
    """Balance after payment k of a fixed loan (k = 0 is the original principal)."""
    p0 = terms.original_principal
    j = k - terms.io_months             # amortizing payments made
    if terms.amortization_months == 0 or j <= 0:
        return p0
    r = terms.fixed_rate_pct / HUNDRED / TWELVE
    j = min(j, terms.amortization_months)
    if r == 0:
        return p0 - p0 * j / terms.amortization_months
    pmt = level_payment(p0, r, terms.amortization_months)
    growth = (1 + r) ** j
    return max(ZERO, p0 * growth - pmt * (growth - 1) / r)


def balance_at(terms: LoanTerms, month_end: date) -> Decimal | None:
    """Outstanding principal at a month end; None before origination or after maturity."""
    k = payment_number(terms, month_end)
    if k < 0 or month_end > terms.maturity_date:
        return None
    if terms.rate_type == "floating":
        return terms.original_principal
    return fixed_balance_after(terms, k)


def monthly_payment(terms: LoanTerms, month_end: date, index) -> tuple[Decimal, Decimal] | None:
    """(interest, principal) paid at this month end, or None if no payment is due or the rate is unknown."""
    k = payment_number(terms, month_end)
    if k < 1 or month_end > terms.maturity_date:
        return None
    rate = annual_rate_pct(terms, month_end, index)
    if rate is None:
        return None
    r = rate / HUNDRED / TWELVE
    if terms.rate_type == "floating":
        return terms.original_principal * r, ZERO
    opening = fixed_balance_after(terms, k - 1)
    interest = opening * r
    principal = opening - fixed_balance_after(terms, k)
    return interest, principal


@dataclass
class DebtService:
    total: Decimal | None
    interest: Decimal
    principal: Decimal
    months: int
    reason: str | None = None


def ttm_debt_service(terms: LoanTerms, period: date, index) -> DebtService:
    interest = principal = ZERO
    months = 0
    for i in range(11, -1, -1):
        p = monthly_payment(terms, add_months(period, -i), index)
        if p is not None:
            interest += p[0]
            principal += p[1]
            months += 1
    if months < 12:
        return DebtService(None, interest, principal, months,
                           f"only {months} of 12 monthly payments fall in the window (new loan, matured, or index missing)")
    return DebtService(interest + principal, interest, principal, months)


@dataclass
class CollateralInput:
    property_id: str
    allocation_pct: Decimal
    ttm_noi: Decimal | None       # None if fewer than 12 months of actuals
    value: Decimal | None         # latest valuation on or before the period
    valuation_id: int | None = None
    valuation_date: date | None = None


@dataclass
class LoanMetrics:
    period: date
    balance: Decimal | None
    debt_service: DebtService
    noi: Decimal | None
    value: Decimal | None
    dscr: Decimal | None
    ltv_pct: Decimal | None
    debt_yield_pct: Decimal | None
    rate_pct: Decimal | None
    notes: list[str] = field(default_factory=list)


def loan_metrics(terms: LoanTerms, period: date, collateral: list[CollateralInput], index) -> LoanMetrics:
    notes: list[str] = []
    balance = balance_at(terms, period)
    ds = ttm_debt_service(terms, period, index)
    noi = value = None
    if collateral and all(c.ttm_noi is not None for c in collateral):
        noi = sum((c.ttm_noi * c.allocation_pct / HUNDRED for c in collateral), ZERO)
    else:
        notes.append("NOI not calculable: a collateral property lacks 12 months of actuals")
    if collateral and all(c.value is not None for c in collateral):
        value = sum((c.value * c.allocation_pct / HUNDRED for c in collateral), ZERO)
    else:
        notes.append("Value not calculable: a collateral property has no valuation on or before the period")

    def ratio(num, den, what, scale=Decimal(1)):
        if num is None or den is None:
            return None
        if den == 0:
            notes.append(f"{what} not calculable: denominator is zero")
            return None
        return num / den * scale

    if ds.total is None:
        notes.append(f"DSCR not calculable: {ds.reason}")
    return LoanMetrics(
        period=period, balance=balance, debt_service=ds, noi=noi, value=value,
        dscr=ratio(noi, ds.total, "DSCR"), ltv_pct=ratio(balance, value, "LTV", HUNDRED),
        debt_yield_pct=ratio(noi, balance, "Debt yield", HUNDRED),
        rate_pct=annual_rate_pct(terms, period, index), notes=notes,
    )


def covenant_status(kind: str, threshold: Decimal, m: LoanMetrics) -> tuple[str, Decimal | None]:
    """('pass' | 'watch' | 'breach' | 'not_calculable', tested value). Exactly at the threshold passes."""
    value = {"min_dscr": m.dscr, "max_ltv": m.ltv_pct, "min_debt_yield": m.debt_yield_pct}[kind]
    if value is None:
        return "not_calculable", None
    # Tested at 4 decimal places, so division remainders (1.2499999…) can't turn "at the threshold" into a breach.
    value = value.quantize(TEST_PRECISION, ROUND_HALF_UP)
    if kind == "max_ltv":
        if value > threshold:
            return "breach", value
        return ("watch" if value > threshold * (1 - WATCH_MARGIN) else "pass"), value
    if value < threshold:
        return "breach", value
    return ("watch" if value < threshold * (1 + WATCH_MARGIN) else "pass"), value


def balloon(terms: LoanTerms) -> Decimal:
    """Principal still owed at maturity."""
    return balance_at(terms, terms.maturity_date) or ZERO
