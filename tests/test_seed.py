"""The synthetic dataset is deterministic and matches the brief. No database needed."""
from collections import Counter, defaultdict
from decimal import Decimal

import pytest

from propertyiq.finance import add_months
from propertyiq.seed.generator import PREFIXES, generate, split_cents

DS = generate()


def ttm_noi(pid, end):
    start = add_months(end, -11)
    return sum(r - e for p, m, r, e, *_ in DS.financials if p == pid and start <= m <= end)


def test_deterministic():
    assert generate().content_sha256() == DS.content_sha256()
    assert generate(seed=1).content_sha256() != DS.content_sha256()


def test_shape():
    assert len(DS.properties) == 55
    assert Counter(p["asset_class"] for p in DS.properties) == {
        "office": 14, "retail": 14, "industrial": 14, "multifamily": 13}
    assert len({p["name"] for p in DS.properties}) == 55
    assert len(DS.financials) == len(DS.budgets) == 55 * 36
    per_property = Counter(row[0] for row in DS.financials)
    assert set(per_property.values()) == {36}


def test_no_real_or_reserved_names_reused():
    names = " ".join(p["name"] for p in DS.properties[4:])
    for reserved in ("Westlake", "Riverside", "Harbor", "Sunset"):
        assert reserved not in names
    assert len(PREFIXES) == len(set(PREFIXES))


@pytest.mark.parametrize("spec", DS.required, ids=lambda s: s["name"])
def test_required_property_facts(spec):
    pid, period = spec["property_id"], DS.period
    assert ttm_noi(pid, period) == Decimal(spec["noi_actual_ttm"])
    budget = sum(r - e for p, m, r, e in DS.budgets if p == pid and add_months(period, -11) <= m <= period)
    assert budget == Decimal(spec["noi_budget_ttm"])
    prior = ttm_noi(pid, add_months(period, -12))
    yoy = (ttm_noi(pid, period) - prior) / abs(prior) * 100
    assert abs(yoy - Decimal(spec["noi_yoy_pct"])) < Decimal("0.001")
    last = next(row for row in DS.financials if row[0] == pid and row[1] == period)
    assert Decimal(last[4]) / Decimal(last[5]) * 100 == Decimal(spec["occupancy_pct"])


def test_required_shortfalls_total_420k():
    assert sum(Decimal(s["expected_budget_shortfall"]) for s in DS.required) == Decimal("420000")


def test_noi_equals_revenue_minus_expenses_and_expenses_positive():
    for _, _, rev, exp, occ, rent in DS.financials:
        assert exp > 0 and rev > exp and 0 <= occ <= rent


def test_split_cents_is_exact():
    assert sum(split_cents(100_001, [3, 3, 3])) == 100_001
    assert split_cents(10, [1, 1, 1]) == [4, 3, 3]
    assert sum(split_cents(-7, [1, 2])) == -7


def test_month_coverage():
    months = sorted({row[1] for row in DS.financials})
    assert months[0].isoformat() == "2023-10-31" and months[-1].isoformat() == "2026-09-30"
    by_pid = defaultdict(set)
    for p, m, *_ in DS.financials:
        by_pid[p].add(m)
    assert all(len(v) == 36 for v in by_pid.values())
