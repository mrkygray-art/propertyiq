"""Loan screening calculations.

Plain-language guide to the terms (also shown as help text in the UI):

  PGI   Potential gross income: the rent the property would collect if 100% full.
  EGI   Effective gross income: income actually collected after empty units and
        tenants who don't pay ("vacancy and credit loss").
  NOI   Net operating income: what the property earns after its running costs,
        before any loan payments.
  Debt service   The loan payments for a year.
  DSCR  Debt service coverage ratio: how many times NOI covers the loan payments.
        1.00 means the property earns exactly enough to pay the loan; lenders want
        a cushion above that (for example 1.25).
  LTV   Loan-to-value: how much of the property's value is borrowed.
  Debt yield   NOI as a percentage of the loan: the lender's return if it had to
        take the property back. It ignores interest rate and amortization.
  Cap rate     NOI as a percentage of the value: the property's yield at that price.
  Break-even occupancy   How full the property must be to pay all running costs and
        the loan. Above 100% means it can't break even even when completely full.
  Debt constant   The year's loan payments as a percentage of the loan amount.

Money is kept as exact Decimal values. Nothing is rounded here; rounding happens only
when values are displayed or exported.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal

ZERO = Decimal(0)
ONE = Decimal(1)
TWELVE = Decimal(12)

PROPERTY_TYPES = ("multifamily", "office", "retail", "industrial", "mixed_use", "other")


@dataclass(frozen=True)
class DealInputs:
    """One deal. Percentages are stored as fractions: 6.5% is Decimal('0.065')."""
    property_name: str
    property_type: str
    value: Decimal | None              # value used for screening (purchase price or appraised value)
    pgi: Decimal | None                # potential gross income, per year
    vacancy: Decimal | None            # vacancy and credit loss, fraction of PGI + other income
    operating_expenses: Decimal | None  # per year: taxes, insurance, utilities, repairs, management, payroll
    loan_amount: Decimal | None
    interest_rate: Decimal | None      # per year, fraction
    amortization_years: int | None     # years over which the loan is paid off
    other_income: Decimal = ZERO       # parking, laundry, fees, per year
    reserves: Decimal = ZERO           # replacement reserves: yearly set-aside for big repairs
    interest_only_years: Decimal = ZERO
    term_years: Decimal | None = None  # informational only in Tier 1
    size: Decimal | None = None        # units or square feet, optional
    size_unit: str | None = None       # "units" or "sf"


@dataclass(frozen=True)
class Metrics:
    """Results for one deal. None means "can't be calculated from these inputs"."""
    egi: Decimal | None
    noi: Decimal | None
    monthly_payment: Decimal | None           # amortizing payment
    annual_debt_service: Decimal | None       # amortizing, 12 x monthly payment
    interest_only_debt_service: Decimal | None  # only when there is an interest-only period
    dscr: Decimal | None                      # amortizing basis
    dscr_interest_only: Decimal | None        # during the interest-only period, if any
    ltv: Decimal | None
    debt_yield: Decimal | None
    cap_rate: Decimal | None
    break_even_occupancy: Decimal | None      # amortizing basis
    debt_constant: Decimal | None             # amortizing basis
    warnings: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------- input checks

def validate(d: DealInputs) -> list[str]:
    """Friendly messages for inputs that can't be screened. An empty list means OK."""
    errors: list[str] = []

    def nonneg(name: str, v: Decimal | None, required: bool = True) -> None:
        if v is None:
            if required:
                errors.append(f"{name} is required.")
        elif v < 0:
            errors.append(f"{name} can't be negative.")

    if d.property_type not in PROPERTY_TYPES:
        errors.append("Choose a property type.")
    if d.value is None or d.value <= 0:
        errors.append("Property value must be more than $0.")
    nonneg("Potential gross income", d.pgi)
    nonneg("Other income", d.other_income)
    nonneg("Operating expenses", d.operating_expenses)
    nonneg("Replacement reserves", d.reserves)
    for name, pct in (("Vacancy and credit loss", d.vacancy), ("Interest rate", d.interest_rate)):
        if pct is None:
            errors.append(f"{name} is required.")
        elif not ZERO <= pct <= ONE:
            errors.append(f"{name} must be between 0% and 100%.")
    if d.amortization_years is None or d.amortization_years <= 0:
        errors.append("Amortization must be at least 1 year.")
    if d.interest_only_years < 0:
        errors.append("Interest-only period can't be negative.")
    if d.loan_amount is not None and d.loan_amount < 0:
        errors.append("Loan amount can't be negative.")
    if d.size is not None and d.size < 0:
        errors.append("Units or square feet can't be negative.")
    return errors


# ---------------------------------------------------------------- formulas

def monthly_payment(principal: Decimal, annual_rate: Decimal, years: int) -> Decimal:
    """Standard amortizing payment: the same amount every month pays off the loan exactly.

    P x r / (1 - (1 + r)^-n), where r is the monthly rate and n the number of payments.
    With a 0% rate the loan is simply split into equal payments: P / n.
    """
    n = years * 12
    r = annual_rate / TWELVE
    if r == 0:
        return principal / n
    return principal * r / (ONE - (ONE + r) ** -n)


def _div(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    """a / b, or None when either is missing or b is zero (never divide by zero)."""
    if a is None or b is None or b == 0:
        return None
    return a / b


def compute(d: DealInputs) -> Metrics:
    """All Section 6 metrics. Call validate() first; this assumes inputs passed it."""
    warnings: list[str] = []

    gross = d.pgi + d.other_income                      # income if 100% full
    egi = gross * (ONE - d.vacancy)                     # income actually collected
    noi = egi - d.operating_expenses - d.reserves       # after running costs, before the loan
    if noi <= 0:
        warnings.append("NOI is zero or negative: the property doesn't cover its running costs.")

    has_loan = d.loan_amount is not None and d.loan_amount > 0
    pay = ads = io_ds = None
    if has_loan:
        pay = monthly_payment(d.loan_amount, d.interest_rate, d.amortization_years)
        ads = pay * TWELVE
        if d.interest_only_years > 0:
            io_ds = d.loan_amount * d.interest_rate     # interest only: no principal repaid
    else:
        warnings.append("Needs a loan amount to calculate loan payments, DSCR, LTV and debt yield.")

    break_even = None
    if ads is not None and gross > 0:
        break_even = (d.operating_expenses + d.reserves + ads) / gross
        if break_even > 1:
            warnings.append("Break-even occupancy is above 100%: the property can't cover its costs "
                            "and loan payments even when completely full.")

    dscr = _div(noi, ads)
    dscr_io = _div(noi, io_ds)
    if io_ds == 0:
        warnings.append("Interest-only payments are $0 at a 0% rate, so interest-only DSCR can't be calculated.")
    if has_loan and dscr is not None and dscr < 0:
        warnings.append("DSCR is negative because NOI is negative.")

    return Metrics(
        egi=egi,
        noi=noi,
        monthly_payment=pay,
        annual_debt_service=ads,
        interest_only_debt_service=io_ds,
        dscr=dscr,
        dscr_interest_only=dscr_io,
        ltv=_div(d.loan_amount, d.value) if has_loan else None,
        debt_yield=_div(noi, d.loan_amount) if has_loan else None,
        cap_rate=_div(noi, d.value),
        break_even_occupancy=break_even,
        debt_constant=_div(ads, d.loan_amount) if has_loan else None,
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------- guidelines

@dataclass(frozen=True)
class Rule:
    enabled: bool
    threshold: Decimal | None


@dataclass(frozen=True)
class GuidelineSet:
    """A lender's own rules. Percent thresholds are fractions (0.70 = 70%)."""
    name: str
    min_dscr: Rule = Rule(True, Decimal("1.25"))
    max_ltv: Rule = Rule(True, Decimal("0.70"))
    min_debt_yield: Rule = Rule(True, Decimal("0.08"))
    max_break_even: Rule = Rule(True, Decimal("0.85"))
    min_loan: Rule = Rule(False, None)
    max_loan: Rule = Rule(False, None)
    allowed_types: tuple[str, ...] = PROPERTY_TYPES
    allowed_types_enabled: bool = False
    dscr_basis: str = "amortizing"  # or "interest_only": which DSCR the min_dscr rule uses


EXAMPLE_GUIDELINES = GuidelineSet(name="Example (set your own)")


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    label: str
    status: str  # "PASS" | "FAIL" | "NOT CHECKED"
    actual: Decimal | str | None
    threshold: Decimal | str | None
    kind: str    # "ratio" | "pct" | "usd" | "text": how to display actual/threshold
    message: str


def _fmt(v: Decimal | str | None, kind: str) -> str:
    if v is None:
        return "n/a"
    if kind == "ratio":
        return f"{v:.2f}"
    if kind == "pct":
        return f"{v * 100:.1f}%"
    if kind == "usd":
        return f"${v:,.0f}"
    return str(v)


def evaluate_guidelines(d: DealInputs, m: Metrics, g: GuidelineSet) -> tuple[list[RuleResult], str]:
    """Check each enabled rule. Returns the per-rule results and the overall status:
    FAIL if any rule fails, PASS if every checked rule passes, NOT CHECKED if nothing could be checked."""
    results: list[RuleResult] = []

    def check(rule_id: str, label: str, rule: Rule, actual, kind: str, op: str, why_missing: str,
              measure: str | None = None) -> None:
        """label names the rule; measure names the number in the message (defaults to label)."""
        measure = measure or label
        if not rule.enabled:
            results.append(RuleResult(rule_id, label, "NOT CHECKED", actual, rule.threshold, kind, f"{label}: rule turned off"))
            return
        if rule.threshold is None:
            results.append(RuleResult(rule_id, label, "NOT CHECKED", actual, None, kind, f"{label}: no limit set"))
            return
        if actual is None:
            results.append(RuleResult(rule_id, label, "NOT CHECKED", None, rule.threshold, kind, f"{label}: {why_missing}"))
            return
        ok = actual >= rule.threshold if op == ">=" else actual <= rule.threshold
        word = "minimum" if op == ">=" else "maximum"
        status = "PASS" if ok else "FAIL"
        results.append(RuleResult(rule_id, label, status, actual, rule.threshold, kind,
                                  f"{measure} {_fmt(actual, kind)} vs. {word} {_fmt(rule.threshold, kind)}: {status}"))

    use_io = g.dscr_basis == "interest_only" and m.dscr_interest_only is not None
    dscr = m.dscr_interest_only if use_io else m.dscr
    dscr_label = "DSCR (interest-only)" if use_io else "DSCR"
    check("min_dscr", dscr_label, g.min_dscr, dscr, "ratio", ">=", "needs a loan amount")
    check("max_ltv", "LTV", g.max_ltv, m.ltv, "pct", "<=", "needs a loan amount")
    check("min_debt_yield", "Debt yield", g.min_debt_yield, m.debt_yield, "pct", ">=", "needs a loan amount")
    check("max_break_even", "Break-even occupancy", g.max_break_even, m.break_even_occupancy, "pct", "<=",
          "needs a loan amount and income")
    loan = d.loan_amount if d.loan_amount and d.loan_amount > 0 else None
    check("min_loan", "Minimum loan size", g.min_loan, loan, "usd", ">=", "needs a loan amount", "Loan amount")
    check("max_loan", "Maximum loan size", g.max_loan, loan, "usd", "<=", "needs a loan amount", "Loan amount")

    if g.allowed_types_enabled:
        ok = d.property_type in g.allowed_types
        allowed = ", ".join(t.replace("_", "-") for t in g.allowed_types)
        status = "PASS" if ok else "FAIL"
        results.append(RuleResult("allowed_types", "Property type", status, d.property_type.replace("_", "-"),
                                  allowed, "text", f"Property type {d.property_type.replace('_', '-')} vs. allowed "
                                  f"({allowed}): {status}"))
    else:
        results.append(RuleResult("allowed_types", "Property type", "NOT CHECKED", d.property_type, "all", "text",
                                  "Property type: all types allowed"))

    statuses = [r.status for r in results]
    overall = "FAIL" if "FAIL" in statuses else "PASS" if "PASS" in statuses else "NOT CHECKED"
    return results, overall


# ---------------------------------------------------------------- stress tests

@dataclass(frozen=True)
class Scenario:
    """A "what if things get worse" change to the deal. Percent changes are fractions."""
    key: str
    label: str
    rate_add: Decimal = ZERO          # +0.01 = interest rate 1 point higher
    vacancy_add: Decimal = ZERO       # +0.05 = 5 more points of vacancy
    income_change: Decimal = ZERO     # -0.10 = collected income (EGI) 10% lower
    expense_change: Decimal = ZERO    # +0.10 = operating expenses 10% higher
    exit_cap_add: Decimal = ZERO      # +0.01 = value re-estimated at a cap rate 1 point higher


DEFAULT_SCENARIOS = (
    Scenario("base", "Base (as entered)"),
    Scenario("rate_1", "Rate +1%", rate_add=Decimal("0.01")),
    Scenario("rate_2", "Rate +2%", rate_add=Decimal("0.02")),
    Scenario("vacancy_5", "Vacancy +5 points", vacancy_add=Decimal("0.05")),
    Scenario("vacancy_10", "Vacancy +10 points", vacancy_add=Decimal("0.10")),
    Scenario("income_10", "Income -10%", income_change=Decimal("-0.10")),
    Scenario("expenses_10", "Expenses +10%", expense_change=Decimal("0.10")),
    Scenario("exit_cap_1", "Exit cap +1%", exit_cap_add=Decimal("0.01")),
    Scenario("combined", "Combined downside (rate +1%, vacancy +5 pts, expenses +5%)",
             rate_add=Decimal("0.01"), vacancy_add=Decimal("0.05"), expense_change=Decimal("0.05")),
)


@dataclass(frozen=True)
class StressResult:
    scenario: Scenario
    inputs: DealInputs
    metrics: Metrics
    value_used: Decimal | None
    overall: str
    rules: list[RuleResult]


def stress(d: DealInputs, g: GuidelineSet, scenario: Scenario) -> StressResult:
    """Re-run the deal under one scenario.

    Rate and vacancy changes go straight into the inputs. "Income -10%" lowers collected
    income (EGI); it's applied by lowering PGI and other income by the same fraction, which
    lowers EGI by exactly that fraction. "Expenses +x%" raises operating expenses (reserves
    unchanged). "Exit cap +1%" re-estimates the value as NOI / (implied cap rate + 1 point)
    and recomputes LTV against that lower value.
    """
    s = replace(
        d,
        interest_rate=min(ONE, d.interest_rate + scenario.rate_add),
        vacancy=min(ONE, d.vacancy + scenario.vacancy_add),
        pgi=d.pgi * (ONE + scenario.income_change),
        other_income=d.other_income * (ONE + scenario.income_change),
        operating_expenses=d.operating_expenses * (ONE + scenario.expense_change),
    )
    value = s.value
    if scenario.exit_cap_add:
        base = compute(d)
        stressed_noi = compute(s).noi
        new_cap = (base.cap_rate or ZERO) + scenario.exit_cap_add
        value = stressed_noi / new_cap if new_cap > 0 and stressed_noi > 0 else None
        s = replace(s, value=value) if value is not None else s
    m = compute(s)
    if scenario.exit_cap_add and value is None:
        # NOI at or below zero: no meaningful value, so no LTV.
        m = replace(m, ltv=None, cap_rate=None,
                    warnings=m.warnings + ("Exit value can't be estimated because NOI is zero or negative.",))
    rules, overall = evaluate_guidelines(s, m, g)
    return StressResult(scenario, s, m, value, overall, rules)


def dscr_grid(d: DealInputs, rate_steps: list[Decimal], vacancy_steps: list[Decimal]) -> list[list[Decimal | None]]:
    """DSCR for every combination of interest rate (rows) and vacancy (columns), both absolute fractions."""
    grid = []
    for rate in rate_steps:
        row = []
        for vac in vacancy_steps:
            row.append(compute(replace(d, interest_rate=rate, vacancy=vac)).dscr)
        grid.append(row)
    return grid
