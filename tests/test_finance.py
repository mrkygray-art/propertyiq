"""Unit tests for the deterministic calculations and rules. No database needed."""
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from propertyiq.finance import (
    PeriodInputs, RuleSet, add_months, current_window, evaluate, is_month_end, prior_window, prioritize,
)

RULES = RuleSet.load(Path(__file__).resolve().parents[1] / "services/api/propertyiq/rules.json")
P = date(2026, 9, 30)


def inputs(pid="PIQ-900", *, noi="1000000", budget="1000000", prior="1000000", occ=95, rent=100,
           cur_months=12, prior_months=12, budget_months=12) -> PeriodInputs:
    d = lambda v: None if v is None else Decimal(v)  # noqa: E731
    return PeriodInputs(
        property_id=pid, period=P, current_months=cur_months, current_revenue=None, current_expenses=None,
        current_noi=d(noi), prior_months=prior_months, prior_noi=d(prior), budget_months=budget_months,
        budget_noi=d(budget), occupied_size=occ, rentable_size=rent,
    )


def status(ev, rule_id):
    return next(r.status for r in ev.rules if r.rule_id == rule_id)


# ---------------------------------------------------------------- periods

def test_month_arithmetic():
    assert add_months(P, -11) == date(2025, 10, 31)
    assert add_months(date(2024, 3, 31), -1) == date(2024, 2, 29)
    assert current_window(P) == (date(2025, 10, 31), P)
    assert prior_window(P) == (date(2024, 10, 31), date(2025, 9, 30))
    assert is_month_end(P) and not is_month_end(date(2026, 9, 29))


# ---------------------------------------------------------------- the brief's four properties

@pytest.mark.parametrize("pid,occ,budget,actual,yoy,shortfall,triggered", [
    ("Westlake", 72, "900000", "720000", "-18", "180000", {"occupancy_below_min", "noi_yoy_decline", "noi_below_budget"}),
    ("Riverside", 79, "800000", "680000", "-12", "120000", {"occupancy_below_min", "noi_yoy_decline", "noi_below_budget"}),
    ("Harbor", 91, "600000", "530000", "-9", "70000", {"noi_below_budget"}),
    ("Sunset", 88, "400000", "350000", "-6", "50000", {"noi_below_budget"}),
])
def test_brief_examples(pid, occ, budget, actual, yoy, shortfall, triggered):
    prior = Decimal(actual) / (1 + Decimal(yoy) / 100)
    ev = evaluate(inputs(pid, noi=actual, budget=budget, prior=str(prior), occ=occ), RULES)
    assert ev.flagged
    assert {r.rule_id for r in ev.rules if r.status == "triggered"} == triggered
    assert ev.metrics.budget_shortfall == Decimal(shortfall)
    assert ev.metrics.budget_variance == -Decimal(shortfall)
    assert round(ev.metrics.noi_yoy_pct, 6) == Decimal(yoy)


# ---------------------------------------------------------------- boundaries

def test_occupancy_boundary_is_strict():
    assert status(evaluate(inputs(occ=85), RULES), "occupancy_below_min") == "not_triggered"
    assert status(evaluate(inputs(occ=8499, rent=10000), RULES), "occupancy_below_min") == "triggered"


def test_yoy_boundary_is_inclusive():
    assert status(evaluate(inputs(noi="900000", prior="1000000"), RULES), "noi_yoy_decline") == "triggered"
    assert status(evaluate(inputs(noi="900001", prior="1000000"), RULES), "noi_yoy_decline") == "not_triggered"


def test_budget_boundary_is_inclusive():
    assert status(evaluate(inputs(noi="900000", budget="1000000"), RULES), "noi_below_budget") == "triggered"
    assert status(evaluate(inputs(noi="900000.01", budget="1000000"), RULES), "noi_below_budget") == "not_triggered"


# ---------------------------------------------------------------- edge cases

@pytest.mark.parametrize("budget", ["0", "-50000"])
def test_non_positive_budget_is_not_evaluated(budget):
    ev = evaluate(inputs(noi="10", budget=budget), RULES)
    assert status(ev, "noi_below_budget") == "not_evaluated"
    assert ev.metrics.noi_to_budget_pct is None
    assert not ev.data_complete
    assert any("budget is zero or negative" in i for i in ev.metrics.data_issues)


@pytest.mark.parametrize("prior", ["0", "-25000"])
def test_non_positive_prior_noi_means_no_yoy(prior):
    ev = evaluate(inputs(prior=prior), RULES)
    assert ev.metrics.noi_yoy_pct is None
    assert status(ev, "noi_yoy_decline") == "not_evaluated"


def test_missing_occupancy_is_not_evaluated():
    ev = evaluate(inputs(occ=None, rent=None), RULES)
    assert status(ev, "occupancy_below_min") == "not_evaluated"
    assert not ev.flagged


def test_incomplete_months_block_metrics():
    ev = evaluate(inputs(cur_months=11), RULES)
    assert ev.metrics.noi_actual is None
    assert status(ev, "noi_below_budget") == "not_evaluated"
    assert status(ev, "noi_yoy_decline") == "not_evaluated"
    assert any("11 of 12" in i for i in ev.metrics.data_issues)


def test_negative_actual_noi_against_positive_budget_is_flagged():
    ev = evaluate(inputs(noi="-20000", budget="500000"), RULES)
    assert status(ev, "noi_below_budget") == "triggered"
    assert ev.metrics.budget_shortfall == Decimal("520000")


def test_over_budget_has_zero_shortfall():
    ev = evaluate(inputs(noi="1100000", budget="1000000"), RULES)
    assert ev.metrics.budget_shortfall == 0
    assert ev.metrics.budget_variance == Decimal("100000")
    assert not ev.flagged


def test_partial_data_can_still_flag():
    ev = evaluate(inputs(occ=70, budget="0"), RULES)
    assert ev.flagged and not ev.data_complete


# ---------------------------------------------------------------- priority

def test_priority_rule_count_then_shortfall_then_id():
    evs = [
        evaluate(inputs("PIQ-010", noi="850000", budget="1000000"), RULES),            # 1 rule, 150k
        evaluate(inputs("PIQ-011", noi="800000", budget="1000000"), RULES),            # 1 rule, 200k
        evaluate(inputs("PIQ-012", noi="950000", budget="1000000", occ=80), RULES),    # 1 rule, 50k
        evaluate(inputs("PIQ-013", noi="850000", budget="1000000", occ=80), RULES),    # 2 rules, 150k
        evaluate(inputs("PIQ-009", noi="850000", budget="1000000"), RULES),            # tie with 010
        evaluate(inputs("PIQ-014"), RULES),                                            # not flagged
    ]
    order = [e.inputs.property_id for _, e in prioritize(evs)]
    assert order == ["PIQ-013", "PIQ-011", "PIQ-009", "PIQ-010", "PIQ-012"]


def test_rule_set_is_configurable(tmp_path):
    f = tmp_path / "rules.json"
    f.write_text('{"version":"t","occupancy_min_pct":"95","noi_yoy_max_pct":"-50","noi_to_budget_max_pct":"10"}')
    strict = RuleSet.load(f)
    ev = evaluate(inputs(occ=90), strict)
    assert ev.flagged and ev.rule_set_version == "t"
