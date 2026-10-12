"""Deterministic financial calculations and underperformance rules.

Everything here is pure Python on Decimal values so it can be unit tested without a
database. The LLM never computes these numbers; it only explains them.

Definitions (see docs/financial-definitions.md):
  Reporting period   a month end. "Annual" means the trailing 12 months (TTM) ending there.
  NOI                operating revenue - operating expenses (no financing costs, no capex).
  Budget variance    actual TTM NOI - budget TTM NOI (negative = under budget).
  Budget shortfall   budget - actual when positive, else 0.
  NOI YoY %          (current TTM NOI - prior TTM NOI) / |prior TTM NOI| * 100.
                     Not computed when the prior TTM NOI is zero or negative.
  Occupancy          physical: occupied size / rentable size at the period's month end.
"""
from __future__ import annotations

import calendar
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

HUNDRED = Decimal(100)
MONTHS_IN_TTM = 12


# ---------------------------------------------------------------- periods

def month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def add_months(d: date, months: int) -> date:
    """Month-end arithmetic: add_months(2026-09-30, -11) == 2025-10-31."""
    index = d.year * 12 + (d.month - 1) + months
    return month_end(index // 12, index % 12 + 1)


def is_month_end(d: date) -> bool:
    return d == month_end(d.year, d.month)


def current_window(period: date) -> tuple[date, date]:
    return add_months(period, -(MONTHS_IN_TTM - 1)), period


def prior_window(period: date) -> tuple[date, date]:
    return add_months(period, -(2 * MONTHS_IN_TTM - 1)), add_months(period, -MONTHS_IN_TTM)


# ---------------------------------------------------------------- rules

@dataclass(frozen=True)
class RuleSet:
    version: str
    occupancy_min_pct: Decimal
    noi_yoy_max_pct: Decimal
    noi_to_budget_max_pct: Decimal

    @classmethod
    def load(cls, path: Path) -> "RuleSet":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            version=raw["version"],
            occupancy_min_pct=Decimal(raw["occupancy_min_pct"]),
            noi_yoy_max_pct=Decimal(raw["noi_yoy_max_pct"]),
            noi_to_budget_max_pct=Decimal(raw["noi_to_budget_max_pct"]),
        )

    def describe(self) -> list[dict]:
        return [
            {"rule_id": "occupancy_below_min", "condition": f"physical occupancy < {self.occupancy_min_pct}%"},
            {"rule_id": "noi_yoy_decline", "condition": f"TTM NOI YoY change <= {self.noi_yoy_max_pct}%"},
            {
                "rule_id": "noi_below_budget",
                "condition": f"TTM actual NOI <= {self.noi_to_budget_max_pct}% of a positive TTM NOI budget",
            },
        ]


@dataclass(frozen=True)
class PeriodInputs:
    """Aggregated facts for one property and reporting period, as returned by the database."""
    property_id: str
    period: date
    current_months: int
    current_revenue: Decimal | None
    current_expenses: Decimal | None
    current_noi: Decimal | None
    prior_months: int
    prior_noi: Decimal | None
    budget_months: int
    budget_noi: Decimal | None
    occupied_size: int | None
    rentable_size: int | None


@dataclass(frozen=True)
class Metrics:
    occupancy_pct: Decimal | None
    noi_actual: Decimal | None
    noi_budget: Decimal | None
    budget_variance: Decimal | None
    budget_shortfall: Decimal | None
    noi_to_budget_pct: Decimal | None
    noi_prior: Decimal | None
    noi_yoy_pct: Decimal | None
    data_issues: tuple[str, ...]


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    status: str  # "triggered" | "not_triggered" | "not_evaluated"
    value: Decimal | None
    comparator: str
    threshold: Decimal
    explanation: str


@dataclass(frozen=True)
class Evaluation:
    inputs: PeriodInputs
    metrics: Metrics
    rules: tuple[RuleResult, ...]
    rule_set_version: str
    rules_triggered: int = field(init=False)
    flagged: bool = field(init=False)
    data_complete: bool = field(init=False)

    def __post_init__(self) -> None:
        triggered = sum(r.status == "triggered" for r in self.rules)
        object.__setattr__(self, "rules_triggered", triggered)
        object.__setattr__(self, "flagged", triggered > 0)
        object.__setattr__(
            self, "data_complete",
            not self.metrics.data_issues and all(r.status != "not_evaluated" for r in self.rules),
        )


def compute_metrics(inp: PeriodInputs) -> Metrics:
    issues: list[str] = []

    noi_actual = inp.current_noi if inp.current_months == MONTHS_IN_TTM else None
    if inp.current_months != MONTHS_IN_TTM:
        issues.append(f"current TTM has {inp.current_months} of 12 months of actuals")

    noi_budget = inp.budget_noi if inp.budget_months == MONTHS_IN_TTM else None
    if inp.budget_months != MONTHS_IN_TTM:
        issues.append(f"current TTM has {inp.budget_months} of 12 months of budget")

    noi_prior = inp.prior_noi if inp.prior_months == MONTHS_IN_TTM else None
    if inp.prior_months != MONTHS_IN_TTM:
        issues.append(f"prior TTM has {inp.prior_months} of 12 months of actuals")

    occupancy = None
    if inp.occupied_size is None or not inp.rentable_size:
        issues.append("no occupancy record at the period month end")
    else:
        occupancy = Decimal(inp.occupied_size) / Decimal(inp.rentable_size) * HUNDRED

    variance = shortfall = to_budget = None
    if noi_actual is not None and noi_budget is not None:
        variance = noi_actual - noi_budget
        shortfall = max(noi_budget - noi_actual, Decimal(0))
        if noi_budget > 0:
            to_budget = noi_actual / noi_budget * HUNDRED
        else:
            issues.append("TTM NOI budget is zero or negative; actual-to-budget % not meaningful")

    yoy = None
    if noi_actual is not None and noi_prior is not None:
        if noi_prior > 0:
            yoy = (noi_actual - noi_prior) / abs(noi_prior) * HUNDRED
        else:
            issues.append("prior TTM NOI is zero or negative; YoY % not meaningful")

    return Metrics(
        occupancy_pct=occupancy,
        noi_actual=noi_actual,
        noi_budget=noi_budget,
        budget_variance=variance,
        budget_shortfall=shortfall,
        noi_to_budget_pct=to_budget,
        noi_prior=noi_prior,
        noi_yoy_pct=yoy,
        data_issues=tuple(issues),
    )


def _rule(rule_id: str, value: Decimal | None, comparator: str, threshold: Decimal,
          hit: bool | None, label: str, missing: str) -> RuleResult:
    if value is None or hit is None:
        return RuleResult(rule_id, "not_evaluated", None, comparator, threshold, missing)
    status = "triggered" if hit else "not_triggered"
    word = "meets" if hit else "does not meet"
    return RuleResult(
        rule_id, status, value, comparator, threshold,
        f"{label} {value:.2f}% {word} the condition {comparator} {threshold}%",
    )


def evaluate(inp: PeriodInputs, rules: RuleSet) -> Evaluation:
    m = compute_metrics(inp)
    occ = m.occupancy_pct
    yoy = m.noi_yoy_pct
    ratio = m.noi_to_budget_pct
    results = (
        _rule("occupancy_below_min", occ, "<", rules.occupancy_min_pct,
              None if occ is None else occ < rules.occupancy_min_pct,
              "Occupancy", "Occupancy is missing for this period"),
        _rule("noi_yoy_decline", yoy, "<=", rules.noi_yoy_max_pct,
              None if yoy is None else yoy <= rules.noi_yoy_max_pct,
              "NOI YoY change", "NOI YoY could not be computed (missing or non-positive prior TTM NOI)"),
        _rule("noi_below_budget", ratio, "<=", rules.noi_to_budget_max_pct,
              None if ratio is None else ratio <= rules.noi_to_budget_max_pct,
              "Actual NOI as % of budget", "No positive, complete TTM NOI budget to compare against"),
    )
    return Evaluation(inputs=inp, metrics=m, rules=results, rule_set_version=rules.version)


def prioritize(evaluations: list[Evaluation]) -> list[tuple[int, Evaluation]]:
    """Rank flagged properties: most rules triggered first, then largest budget shortfall,
    then property ID for a stable tie-break. Returns (rank, evaluation) pairs."""
    flagged = [e for e in evaluations if e.flagged]
    flagged.sort(key=lambda e: (
        -e.rules_triggered,
        -(e.metrics.budget_shortfall or Decimal(0)),
        e.inputs.property_id,
    ))
    return list(enumerate(flagged, start=1))
