"""Borrower vs underwritten comparison (Milestone 8).

The borrower's case comes from the approved T-12 (annualized), or from the deal form if no T-12
is approved. The underwritten case starts equal to it; only APPROVED adjustments change it. Both
cases run through the Tier 1 engine, so DSCR, LTV, debt yield and PASS/FAIL use the exact same
formulas as the loan screening page.

Vacancy here is "vacancy and credit loss as a % of recurring gross income" (gross potential rent +
other recurring income). One-time income is added on top and isn't annualized or reduced by
vacancy. When a case is passed to the Tier 1 engine the vacancy rate is converted to the engine's
basis, so EGI is identical either way.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from propertyiq.screening.calc import DealInputs, GuidelineSet
from propertyiq.screening.intake.calcs import T12Metrics, utilities_total
from propertyiq.screening.report import Report, build

ZERO = Decimal(0)
HUNDRED = Decimal(100)

# (key, label, kind, section). kind: usd | pct. Expenses are annual dollars.
LINES = [
    ("gross_potential_rent", "Gross potential rent", "usd", "income"),
    ("other_income", "Other recurring income", "usd", "income"),
    ("non_recurring_income", "One-time income", "usd", "income"),
    ("vacancy_pct", "Vacancy and credit loss (% of recurring gross income)", "pct", "income"),
    ("real_estate_taxes", "Property taxes", "usd", "expense"),
    ("insurance", "Insurance", "usd", "expense"),
    ("utilities", "Utilities", "usd", "expense"),
    ("repairs_maintenance", "Repairs and maintenance", "usd", "expense"),
    ("contract_services", "Contract services", "usd", "expense"),
    ("payroll", "Payroll", "usd", "expense"),
    ("management_fee", "Management fee", "usd", "expense"),
    ("administrative", "Administrative", "usd", "expense"),
    ("marketing", "Marketing", "usd", "expense"),
    ("other_expense", "Other expenses", "usd", "expense"),
    ("reserves", "Replacement reserves", "usd", "reserves"),
    ("value", "Value used for LTV", "usd", "value"),
]
LINE_KEYS = [k for k, *_ in LINES]
LINE_LABEL = {k: label for k, label, *_ in LINES}
LINE_KIND = {k: kind for k, _, kind, _ in LINES}
EXPENSE_KEYS = [k for k, _, _, s in LINES if s == "expense"]
OTHER_INCOME_CATS = ("laundry_income", "parking_income", "fee_income", "utility_reimbursement", "late_fees", "other_income")
LOSS_CATS = ("vacancy_loss", "concessions", "bad_debt")
REASON_CODES = ("MARKET_RENT", "VACANCY", "ONE_TIME_ITEM_REMOVED", "EXPENSE_BELOW_BENCHMARK", "TAX_REASSESSMENT",
                "MANAGEMENT_FEE", "RESERVES", "DEFERRED_MAINTENANCE", "RENT_CONTROL", "OTHER")


def borrower_lines(t12: T12Metrics | None, form: dict, value: Decimal | None) -> tuple[dict[str, Decimal], str]:
    """The borrower's figures, annual. Returns (values, where they came from)."""
    if t12 is not None:
        a = t12.annual_by_category
        gpr = a.get("rental_income", ZERO)
        other = sum((a.get(c, ZERO) for c in OTHER_INCOME_CATS), ZERO)
        nonrec = t12.non_recurring_income                              # actual total: one-time money isn't annualized
        losses = -sum((a.get(c, ZERO) for c in LOSS_CATS), ZERO)     # loss lines are negative on a T-12
        gross = gpr + other
        vals = {
            "gross_potential_rent": gpr, "other_income": other, "non_recurring_income": nonrec,
            "vacancy_pct": losses / gross if gross else ZERO,
            "utilities": utilities_total(a), "reserves": ZERO,
        }
        for k in EXPENSE_KEYS:
            if k != "utilities":
                vals[k] = a.get(k, ZERO)
        source = f"Approved T-12 ({t12.months_present} months" + (", annualized)" if t12.months_present != 12 else ")")
    else:
        d = lambda k: Decimal(str(form.get(k) or 0))  # noqa: E731
        vals = {k: ZERO for k in LINE_KEYS}
        vals.update(gross_potential_rent=d("pgi"), other_income=d("other_income"),
                    vacancy_pct=d("vacancy_pct") / HUNDRED, other_expense=d("operating_expenses"), reserves=d("reserves"))
        source = "Deal form (no T-12 approved)"
    vals["value"] = value or ZERO
    return vals, source


@dataclass
class CaseTotals:
    gross_income: Decimal
    losses: Decimal
    egi: Decimal
    expenses: Decimal
    reserves: Decimal
    noi: Decimal


def totals(v: dict[str, Decimal]) -> CaseTotals:
    recurring = v["gross_potential_rent"] + v["other_income"]
    gross = recurring + v["non_recurring_income"]
    losses = recurring * v["vacancy_pct"]
    egi = gross - losses
    expenses = sum((v[k] for k in EXPENSE_KEYS), ZERO)
    return CaseTotals(gross, losses, egi, expenses, v["reserves"], egi - expenses - v["reserves"])


def apply(borrower: dict[str, Decimal], adjustments: list[dict]) -> dict[str, Decimal]:
    """Underwritten = borrower, with each APPROVED adjustment's value. Proposed and rejected ones are ignored."""
    uw = dict(borrower)
    for a in adjustments:
        if a["status"] == "approved" and a["line_item"] in uw and a["underwritten_value"] is not None:
            uw[a["line_item"]] = Decimal(str(a["underwritten_value"]))
    return uw


def to_inputs(v: dict[str, Decimal], loan: dict, name: str, property_type: str) -> DealInputs:
    """Map a case onto the Tier 1 engine's inputs."""
    d = lambda k: None if loan.get(k) in (None, "") else Decimal(str(loan[k]))  # noqa: E731
    t = totals(v)
    engine_vacancy = t.losses / t.gross_income if t.gross_income else ZERO   # engine applies vacancy to all income
    return DealInputs(
        property_name=name, property_type=property_type if property_type in (
            "multifamily", "office", "retail", "industrial", "mixed_use", "other") else "other",
        value=v["value"] or None, pgi=v["gross_potential_rent"],
        other_income=v["other_income"] + v["non_recurring_income"], vacancy=engine_vacancy,
        operating_expenses=sum((v[k] for k in EXPENSE_KEYS), ZERO), reserves=v["reserves"],
        loan_amount=d("loan_amount"),
        interest_rate=d("interest_rate_pct") / HUNDRED if d("interest_rate_pct") is not None else None,
        amortization_years=int(loan["amortization_years"]) if loan.get("amortization_years") else None,
        interest_only_years=d("interest_only_years") or ZERO, term_years=d("term_years"),
    )


def summary_sentence(b: CaseTotals, u: CaseTotals, rb: Report, ru: Report, g: GuidelineSet) -> str:
    """One plain sentence built from the numbers (not written by the AI)."""
    diff = u.noi - b.noi
    if diff == 0:
        start = "Underwriting adjustments leave NOI unchanged"
    else:
        pct = (diff / b.noi * HUNDRED) if b.noi else None
        start = (f"Underwriting adjustments {'reduce' if diff < 0 else 'increase'} NOI by ${abs(diff):,.0f}"
                 + (f" ({abs(pct):.1f}%)" if pct is not None else ""))
    if not (rb.valid and ru.valid and rb.metrics.dscr is not None and ru.metrics.dscr is not None):
        return start + ". Enter the loan request to see the effect on DSCR."
    out = f"{start}, moving DSCR from {rb.metrics.dscr:.2f} to {ru.metrics.dscr:.2f}"
    if g.min_dscr.enabled and g.min_dscr.threshold is not None:
        ok = ru.metrics.dscr >= g.min_dscr.threshold
        out += f", which {'meets' if ok else 'fails'} the {g.min_dscr.threshold:.2f} minimum"
    return out + "."


def run(borrower: dict[str, Decimal], underwritten: dict[str, Decimal], loan: dict, g: GuidelineSet,
        name: str, property_type: str) -> dict:
    rb = build(to_inputs(borrower, loan, name, property_type), g)
    ru = build(to_inputs(underwritten, loan, name, property_type), g)
    tb, tu = totals(borrower), totals(underwritten)
    return {"borrower_report": rb, "underwritten_report": ru, "borrower_totals": tb, "underwritten_totals": tu,
            "summary": summary_sentence(tb, tu, rb, ru, g)}
