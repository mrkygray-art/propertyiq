"""Loan screening engine: spec test cases A-D, guidelines and stress tests.

Expected values come from two sources that don't share code with the engine:
  1. the numbers written in the Tier 1 spec, and
  2. ref_* below: an independent plain-float implementation of the textbook formulas.
"""
from decimal import Decimal as D

import pytest

from propertyiq.screening.calc import (
    DEFAULT_SCENARIOS, EXAMPLE_GUIDELINES, DealInputs, GuidelineSet, Rule, compute, dscr_grid,
    evaluate_guidelines, monthly_payment, stress, validate,
)

DOLLAR = 1.0      # spec tolerance on dollars
RATIO = 0.005     # spec tolerance on ratios


# ---------------------------------------------------------------- independent reference

def ref_payment(P, annual_rate, years):
    r, n = annual_rate / 12, years * 12
    return P / n if r == 0 else P * r / (1 - (1 + r) ** -n)


def ref_metrics(pgi, other, vac, opex, reserves, value, loan, rate, years):
    egi = (pgi + other) * (1 - vac)
    noi = egi - opex - reserves
    ads = 12 * ref_payment(loan, rate, years)
    return {
        "egi": egi, "noi": noi, "ads": ads, "dscr": noi / ads, "ltv": loan / value,
        "dy": noi / loan, "cap": noi / value, "be": (opex + reserves + ads) / (pgi + other),
        "const": ads / loan,
    }


def deal(**over) -> DealInputs:
    """Spec test case A, with overrides."""
    base = dict(
        property_name="Test case A", property_type="multifamily", value=D("6000000"), pgi=D("600000"),
        vacancy=D("0.05"), operating_expenses=D("170000"), loan_amount=D("4000000"),
        interest_rate=D("0.065"), amortization_years=30,
    )
    base.update(over)
    return DealInputs(**base)


def close(a, b, tol):
    return a is not None and abs(float(a) - float(b)) <= tol


# ---------------------------------------------------------------- Test case A: standard amortizing loan

def test_case_a_matches_spec():
    m = compute(deal())
    assert close(m.egi, 570_000, DOLLAR)
    assert close(m.noi, 400_000, DOLLAR)
    assert close(m.monthly_payment, 25_283, DOLLAR)
    assert close(m.annual_debt_service, 303_393, DOLLAR)
    assert close(m.dscr, 1.32, RATIO)
    assert close(m.ltv, 0.667, 0.0005)
    assert close(m.debt_yield, 0.100, 0.0005)
    assert close(m.cap_rate, 0.0667, 0.00005)
    assert close(m.break_even_occupancy, 0.789, 0.0005)
    assert m.interest_only_debt_service is None and m.dscr_interest_only is None
    assert m.warnings == ()


def test_case_a_matches_independent_reference_exactly():
    m = compute(deal())
    ref = ref_metrics(600_000, 0, 0.05, 170_000, 0, 6_000_000, 4_000_000, 0.065, 30)
    for got, key in ((m.egi, "egi"), (m.noi, "noi"), (m.annual_debt_service, "ads")):
        assert close(got, ref[key], 0.01)  # to the cent
    for got, key in ((m.dscr, "dscr"), (m.ltv, "ltv"), (m.debt_yield, "dy"), (m.cap_rate, "cap"),
                     (m.break_even_occupancy, "be"), (m.debt_constant, "const")):
        assert close(got, ref[key], 1e-9)


def test_case_a_passes_example_guidelines():
    d = deal()
    rules, overall = evaluate_guidelines(d, compute(d), EXAMPLE_GUIDELINES)
    assert overall == "PASS"
    by_id = {r.rule_id: r for r in rules}
    assert by_id["min_dscr"].message == "DSCR 1.32 vs. minimum 1.25: PASS"
    assert by_id["max_ltv"].message == "LTV 66.7% vs. maximum 70.0%: PASS"
    assert by_id["min_debt_yield"].status == "PASS"
    assert by_id["max_break_even"].status == "PASS"


# ---------------------------------------------------------------- Test case B: interest-only

def test_case_b_interest_only():
    m = compute(deal(interest_only_years=D("2")))
    assert close(m.interest_only_debt_service, 260_000, DOLLAR)
    assert close(m.dscr_interest_only, 1.54, RATIO)
    assert close(m.dscr, 1.32, RATIO)  # both are shown; amortizing stays the default for pass/fail


def test_case_b_pass_fail_basis_is_selectable():
    d = deal(interest_only_years=D("2"))
    m = compute(d)
    strict = GuidelineSet(name="t", min_dscr=Rule(True, D("1.40")))
    amortizing = {r.rule_id: r for r in evaluate_guidelines(d, m, strict)[0]}
    io = {r.rule_id: r for r in evaluate_guidelines(d, m, GuidelineSet(name="t", min_dscr=Rule(True, D("1.40")),
                                                                      dscr_basis="interest_only"))[0]}
    assert amortizing["min_dscr"].status == "FAIL"   # 1.32 < 1.40
    assert io["min_dscr"].status == "PASS"           # 1.54 >= 1.40
    assert io["min_dscr"].label == "DSCR (interest-only)"


# ---------------------------------------------------------------- Test case C: rate stress

def test_case_c_rate_75_fails_example_minimum():
    d = deal(interest_rate=D("0.075"))
    m = compute(d)
    assert close(m.annual_debt_service, 335_600, 25)    # spec says "approx. $335,600"
    assert close(m.annual_debt_service, 12 * ref_payment(4_000_000, 0.075, 30), 0.01)
    assert close(m.dscr, 1.19, RATIO)
    rules, overall = evaluate_guidelines(d, m, EXAMPLE_GUIDELINES)
    assert overall == "FAIL"
    dscr_rule = next(r for r in rules if r.rule_id == "min_dscr")
    assert dscr_rule.message == "DSCR 1.19 vs. minimum 1.25: FAIL"


# ---------------------------------------------------------------- Test case D: edge cases

@pytest.mark.parametrize("loan", [D("0"), None])
def test_zero_or_missing_loan_never_divides(loan):
    d = deal(loan_amount=loan)
    m = compute(d)
    assert m.monthly_payment is None and m.dscr is None and m.ltv is None and m.debt_yield is None
    assert any("Needs a loan amount" in w for w in m.warnings)
    rules, overall = evaluate_guidelines(d, m, EXAMPLE_GUIDELINES)
    assert next(r for r in rules if r.rule_id == "min_dscr").status == "NOT CHECKED"
    assert overall == "NOT CHECKED"


def test_zero_rate():
    m = compute(deal(interest_rate=D("0")))
    assert monthly_payment(D("4000000"), D("0"), 30) == D("4000000") / 360
    assert close(m.annual_debt_service, 4_000_000 / 30, 0.01)
    assert close(m.dscr, 400_000 / (4_000_000 / 30), 1e-9)


def test_zero_rate_interest_only_has_no_io_dscr():
    m = compute(deal(interest_rate=D("0"), interest_only_years=D("1")))
    assert m.interest_only_debt_service == 0 and m.dscr_interest_only is None


@pytest.mark.parametrize("opex", [D("570000"), D("700000")])
def test_noi_zero_or_negative_is_flagged_not_an_error(opex):
    d = deal(operating_expenses=opex)
    m = compute(d)
    assert m.noi <= 0 and m.dscr <= 0 and m.debt_yield <= 0
    assert any("NOI is zero or negative" in w for w in m.warnings)
    _, overall = evaluate_guidelines(d, m, EXAMPLE_GUIDELINES)
    assert overall == "FAIL"


def test_break_even_over_100_is_shown_and_flagged():
    m = compute(deal(operating_expenses=D("400000")))
    assert m.break_even_occupancy > 1
    assert close(m.break_even_occupancy, (400_000 + 12 * ref_payment(4_000_000, 0.065, 30)) / 600_000, 1e-9)
    assert any("above 100%" in w for w in m.warnings)


def test_zero_income_has_no_break_even():
    m = compute(deal(pgi=D("0"), operating_expenses=D("0")))
    assert m.break_even_occupancy is None and m.noi == 0


@pytest.mark.parametrize("field,value,message", [
    ("pgi", D("-1"), "Potential gross income can't be negative."),
    ("operating_expenses", D("-5"), "Operating expenses can't be negative."),
    ("vacancy", D("1.5"), "Vacancy and credit loss must be between 0% and 100%."),
    ("interest_rate", D("-0.01"), "Interest rate must be between 0% and 100%."),
    ("value", D("0"), "Property value must be more than $0."),
    ("value", None, "Property value must be more than $0."),
    ("amortization_years", 0, "Amortization must be at least 1 year."),
    ("loan_amount", D("-10"), "Loan amount can't be negative."),
    ("pgi", None, "Potential gross income is required."),
    ("property_type", "castle", "Choose a property type."),
])
def test_validation_messages(field, value, message):
    assert message in validate(deal(**{field: value}))


def test_valid_deal_has_no_errors():
    assert validate(deal()) == []


def test_no_nan_or_infinity_anywhere():
    cases = [deal(loan_amount=D("0")), deal(interest_rate=D("0")), deal(operating_expenses=D("900000")),
             deal(pgi=D("0"), operating_expenses=D("0")), deal(vacancy=D("1"))]
    for d in cases:
        m = compute(d)
        for v in vars(m).values():
            if isinstance(v, D):
                assert v.is_finite()


# ---------------------------------------------------------------- guidelines

def test_disabled_rule_is_not_checked():
    g = GuidelineSet(name="t", min_dscr=Rule(False, D("1.25")))
    d = deal(interest_rate=D("0.075"))
    rules, _ = evaluate_guidelines(d, compute(d), g)
    assert next(r for r in rules if r.rule_id == "min_dscr").status == "NOT CHECKED"


def test_loan_size_limits_and_property_types():
    g = GuidelineSet(name="t", min_loan=Rule(True, D("5000000")), max_loan=Rule(True, D("10000000")),
                     allowed_types=("office", "industrial"), allowed_types_enabled=True)
    d = deal()
    by_id = {r.rule_id: r for r in evaluate_guidelines(d, compute(d), g)[0]}
    assert by_id["min_loan"].status == "FAIL" and by_id["min_loan"].message == "Loan amount $4,000,000 vs. minimum $5,000,000: FAIL"
    assert by_id["max_loan"].status == "PASS"
    assert by_id["allowed_types"].status == "FAIL"


def test_rule_boundaries_are_inclusive():
    # DSCR exactly at the minimum passes; LTV exactly at the maximum passes.
    d = deal(loan_amount=D("4200000"))
    m = compute(d)
    g = GuidelineSet(name="t", min_dscr=Rule(True, m.dscr), max_ltv=Rule(True, D("0.70")))
    by_id = {r.rule_id: r for r in evaluate_guidelines(d, m, g)[0]}
    assert by_id["min_dscr"].status == "PASS" and by_id["max_ltv"].status == "PASS"


# ---------------------------------------------------------------- stress tests

def run(key, d=None):
    sc = next(s for s in DEFAULT_SCENARIOS if s.key == key)
    return stress(d or deal(), EXAMPLE_GUIDELINES, sc)


def test_stress_base_equals_unstressed():
    assert run("base").metrics == compute(deal())


def test_stress_rate_plus_1_is_case_c():
    r = run("rate_1")
    assert close(r.metrics.dscr, 1.19, RATIO) and r.overall == "FAIL"


def test_stress_vacancy_income_expenses_independently():
    v = run("vacancy_10").metrics
    assert close(v.noi, 600_000 * (1 - 0.15) - 170_000, 0.01)
    i = run("income_10").metrics
    assert close(i.egi, 570_000 * 0.9, 0.01) and close(i.noi, 570_000 * 0.9 - 170_000, 0.01)
    e = run("expenses_10").metrics
    assert close(e.noi, 570_000 - 170_000 * 1.1, 0.01)


def test_stress_exit_cap():
    r = run("exit_cap_1")
    # Implied cap 6.667%; +1 point = 7.667%; value = 400,000 / 0.07667 = ~$5,217,391; LTV = 4,000,000 / that.
    expected_value = 400_000 / (400_000 / 6_000_000 + 0.01)
    assert close(r.value_used, expected_value, 0.01)
    assert close(r.metrics.ltv, 4_000_000 / expected_value, 1e-9)
    assert r.overall == "FAIL"   # LTV ~76.7% > 70%


def test_stress_combined():
    m = run("combined").metrics
    noi = 600_000 * (1 - 0.10) - 170_000 * 1.05
    assert close(m.noi, noi, 0.01)
    assert close(m.dscr, noi / (12 * ref_payment(4_000_000, 0.075, 30)), 1e-9)


def test_stress_exit_cap_with_negative_noi_has_no_value():
    r = run("exit_cap_1", deal(operating_expenses=D("700000")))
    assert r.value_used is None and r.metrics.ltv is None


def test_dscr_grid_matches_reference():
    rates = [D("0.055"), D("0.065"), D("0.075")]
    vacs = [D("0.05"), D("0.10")]
    grid = dscr_grid(deal(), rates, vacs)
    for i, rate in enumerate(rates):
        for j, vac in enumerate(vacs):
            ref = ref_metrics(600_000, 0, float(vac), 170_000, 0, 6_000_000, 4_000_000, float(rate), 30)["dscr"]
            assert close(grid[i][j], ref, 1e-9)
    assert close(grid[1][0], 1.3184, 0.0001)
