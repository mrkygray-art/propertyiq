"""One screening run, built once and used by the screen, the PDF and the Excel export,
so all three always show the same numbers."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from propertyiq.screening.calc import (
    DEFAULT_SCENARIOS, DealInputs, GuidelineSet, Metrics, RuleResult, Scenario, StressResult, compute,
    dscr_grid, evaluate_guidelines, stress, validate,
)

RATE_STEPS = [Decimal(x) for x in ("-0.01", "-0.005", "0", "0.005", "0.01", "0.015", "0.02")]
VACANCY_STEPS = [Decimal(x) for x in ("0", "0.025", "0.05", "0.075", "0.10", "0.15")]

TYPE_LABELS = {"multifamily": "Multifamily", "office": "Office", "retail": "Retail",
               "industrial": "Industrial", "mixed_use": "Mixed-use", "other": "Other"}


@dataclass
class Report:
    deal: DealInputs
    guidelines: GuidelineSet
    errors: list[str]
    generated_at: datetime
    metrics: Metrics | None = None
    rules: list[RuleResult] = field(default_factory=list)
    overall: str = "NOT CHECKED"
    stress: list[StressResult] = field(default_factory=list)
    grid_rates: list[Decimal] = field(default_factory=list)
    grid_vacancies: list[Decimal] = field(default_factory=list)
    grid: list[list[Decimal | None]] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors


def default_grid_axes(d: DealInputs) -> tuple[list[Decimal], list[Decimal]]:
    rates = [d.interest_rate + s for s in RATE_STEPS if d.interest_rate + s >= 0]
    vacs = [d.vacancy + s for s in VACANCY_STEPS if d.vacancy + s <= 1]
    return rates, vacs


def build(d: DealInputs, g: GuidelineSet, scenarios: tuple[Scenario, ...] = DEFAULT_SCENARIOS,
          grid_axes: tuple[list[Decimal], list[Decimal]] | None = None) -> Report:
    report = Report(deal=d, guidelines=g, errors=validate(d), generated_at=datetime.now().astimezone())
    if not report.valid:
        return report
    report.metrics = compute(d)
    report.rules, report.overall = evaluate_guidelines(d, report.metrics, g)
    report.stress = [stress(d, g, s) for s in scenarios]
    report.grid_rates, report.grid_vacancies = grid_axes or default_grid_axes(d)
    report.grid = dscr_grid(d, report.grid_rates, report.grid_vacancies)
    return report


def assumptions(d: DealInputs) -> list[tuple[str, str]]:
    """Every input used, as (label, display value)."""
    def usd(v):
        return "not entered" if v is None else f"${v:,.0f}"

    def pct(v):
        return "not entered" if v is None else f"{v * 100:.2f}".rstrip("0").rstrip(".") + "%"

    rows = [
        ("Property", d.property_name or "(unnamed)"),
        ("Property type", TYPE_LABELS.get(d.property_type, d.property_type)),
    ]
    if d.size is not None:
        rows.append(("Size", f"{d.size:,.0f} {'units' if d.size_unit == 'units' else 'sq ft'}"))
    rows += [
        ("Value used for screening", usd(d.value)),
        ("Potential gross income (PGI)", usd(d.pgi)),
        ("Other income", usd(d.other_income)),
        ("Vacancy and credit loss", pct(d.vacancy)),
        ("Operating expenses", usd(d.operating_expenses)),
        ("Replacement reserves", usd(d.reserves)),
        ("Loan amount", usd(d.loan_amount)),
        ("Interest rate", pct(d.interest_rate)),
        ("Amortization", "not entered" if d.amortization_years is None else f"{d.amortization_years} years"),
        ("Interest-only period", f"{d.interest_only_years:g} years"),
        ("Loan term", "not entered" if d.term_years is None else f"{d.term_years:g} years"),
    ]
    return rows
