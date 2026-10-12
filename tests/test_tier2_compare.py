"""Milestone 8: borrower vs underwritten comparison.

Expected values are worked out here from the raw fixture data with plain arithmetic and an
independent loan-payment formula, not with the module's code.
"""
import os
from decimal import Decimal as D
from pathlib import Path

import psycopg
import pytest

import screening_fixture_data as F
from propertyiq.config import get_settings
from propertyiq.llm import LLMResult

pytestmark = pytest.mark.db

try:
    with psycopg.connect(get_settings().dsn("piq_readonly")) as _c:
        _c.execute("SELECT 1 FROM screening_adjustments LIMIT 1")
except Exception as exc:  # pragma: no cover
    pytest.skip(f"Tier 2 tables not set up: {exc}", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from propertyiq import main  # noqa: E402

FX = Path(__file__).parent / "fixtures" / "screening"
LOAN = {"value": 6000000, "loan_amount": 4000000, "interest_rate_pct": 6.5, "amortization_years": 30,
        "interest_only_years": 0, "term_years": 10}


def ref_annual_debt_service(P, rate, years):
    r, n = rate / 12, years * 12
    return 12 * P * r / (1 - (1 + r) ** -n)


def fixture_annual(label):
    months = F.months_for(True)
    amount = next(a for lines in F.PROBLEM_T12.values() for lbl, a in lines if lbl == label)
    return sum(float(F.monthly(amount, m)) for m in months) * 12 / len(months)


# Borrower case from the problem T-12 (11 months, annualized), worked out by hand.
GPR = fixture_annual("Gross Potential Rent")                                  # 552,000
LOSSES = -(fixture_annual("Vacancy Loss") + fixture_annual("Concessions") + fixture_annual("Bad Debt"))  # 20,760
OTHER = fixture_annual("Laundry Income") + fixture_annual("Parking")          # 12,000
ONE_TIME = 18000.0                                                            # actual, not annualized
EXPENSE_LABELS = [lbl for lbl, _ in F.PROBLEM_T12["OPERATING EXPENSES"]]
OPEX = sum(fixture_annual(lbl) for lbl in EXPENSE_LABELS)                     # 80,820
B_NOI = GPR + OTHER + ONE_TIME - LOSSES - OPEX


class FakeAI:
    name = "fake"

    def available(self):
        return True

    def chat(self, system, user, **_):
        return LLMResult('{"1": "contract_services"}', "fake", None, None, None)


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(scope="module")
def h(client):
    r = client.post("/api/auth/login", json={"username": "analyst", "password": os.environ["PIQ_DEMO_PASSWORD"]})
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def deal(client, h, monkeypatch):
    monkeypatch.setattr(main, "PROVIDER", FakeAI())
    d = client.post("/api/screening/deals", headers=h, json={
        "name": "pytest compare", "property_facts": {**F.DEAL_FACTS, "property_type": "multifamily"},
        "borrower_inputs": {"property_type": "multifamily"}}).json()
    did = d["deal_id"]
    for name in ("problem_rent_roll.xlsx", "problem_t12.xlsx"):
        doc = client.post(f"/api/screening/deals/{did}/documents", headers=h, files={"file": (name, (FX / name).read_bytes())}).json()
        client.post(f"/api/screening/documents/{doc['document_id']}/approve", headers=h)
    client.get(f"/api/screening/deals/{did}/analysis", headers=h)
    client.put(f"/api/screening/deals/{did}/california", headers=h, json={
        "facts": {}, "condition": [{"item_key": "roof_flat", "details": {"age_years": "21"}, "estimated_cost": 70000}]})
    client.put(f"/api/screening/deals/{did}/loan", headers=h, json=LOAN)
    yield did
    client.delete(f"/api/screening/deals/{did}", headers=h)


def compare(client, h, did):
    return client.get(f"/api/screening/deals/{did}/compare", headers=h).json()


def line(c, key, which="underwritten"):
    return next(x[which] for x in c["lines"] if x["key"] == key)


def test_borrower_case_matches_the_t12(client, h, deal):
    c = compare(client, h, deal)
    assert c["source"].startswith("Approved T-12 (11 months, annualized)")
    assert line(c, "gross_potential_rent", "borrower") == pytest.approx(GPR)
    assert line(c, "non_recurring_income", "borrower") == pytest.approx(ONE_TIME)
    assert c["totals"]["borrower"]["noi"] == pytest.approx(B_NOI, abs=0.01)
    assert c["underwritten_case"]["metrics"]["noi"] == pytest.approx(B_NOI, abs=0.01)   # starts equal
    ads = ref_annual_debt_service(4_000_000, 0.065, 30)
    assert c["borrower_case"]["metrics"]["dscr"] == pytest.approx(B_NOI / ads, abs=1e-6)


def test_suggestions_have_no_effect_until_approved(client, h, deal):
    s = client.post(f"/api/screening/deals/{deal}/adjustments/suggest", headers=h).json()
    assert s["added"] >= 8
    reasons = {a["line_item"]: a["reason_code"] for a in s["adjustments"]}
    assert reasons == {"vacancy_pct": "VACANCY", "gross_potential_rent": "MARKET_RENT",
                       "non_recurring_income": "ONE_TIME_ITEM_REMOVED", "insurance": "EXPENSE_BELOW_BENCHMARK",
                       "repairs_maintenance": "EXPENSE_BELOW_BENCHMARK", "management_fee": "MANAGEMENT_FEE",
                       "real_estate_taxes": "TAX_REASSESSMENT", "reserves": "DEFERRED_MAINTENANCE"}
    assert all(a["status"] == "proposed" and a["note"] and a["flag_fingerprint"] for a in s["adjustments"])
    assert s["totals"]["underwritten"]["noi"] == pytest.approx(B_NOI, abs=0.01)
    # Suggesting again doesn't duplicate.
    assert client.post(f"/api/screening/deals/{deal}/adjustments/suggest", headers=h).json()["added"] == 0


def test_approved_adjustments_match_independent_math(client, h, deal):
    s = client.post(f"/api/screening/deals/{deal}/adjustments/suggest", headers=h).json()
    for a in s["adjustments"]:
        assert client.patch(f"/api/screening/adjustments/{a['adjustment_id']}", headers=h,
                            json={"status": "approved"}).status_code == 200
    c = compare(client, h, deal)

    # Independent underwritten case.
    rr_vacancy = 2 / 20
    credit = -(fixture_annual("Concessions") + fixture_annual("Bad Debt"))
    vac_pct = (rr_vacancy * GPR + credit) / (GPR + OTHER)
    in_place = sum(u["rent"] for u in F.PROBLEM_UNITS if u["rent"])
    gpr_uw = (in_place + sum(u["market"] for u in F.PROBLEM_UNITS if not u["rent"])) * 12      # 530,400
    egi_uw = (gpr_uw + OTHER) * (1 - vac_pct)                                                  # one-time removed
    mgmt = 0.04 * (GPR + OTHER - LOSSES)                                                       # 4% of borrower recurring EGI
    opex_uw = (OPEX - fixture_annual("RE Taxes") - fixture_annual("Insurance") - fixture_annual("R&M")
               - fixture_annual("Management Fee") + 72000 + 700 * 20 + 1000 * 20 + mgmt)
    reserves = 300 * 20
    noi_uw = egi_uw - opex_uw - reserves
    assert line(c, "real_estate_taxes") == pytest.approx(72000)
    assert line(c, "management_fee") == pytest.approx(mgmt, abs=0.01)
    assert c["totals"]["underwritten"]["noi"] == pytest.approx(noi_uw, abs=0.01)
    assert c["underwritten_case"]["metrics"]["noi"] == pytest.approx(noi_uw, abs=0.01)   # Tier 1 engine agrees

    ads = ref_annual_debt_service(4_000_000, 0.065, 30)
    dscr_b, dscr_u = B_NOI / ads, noi_uw / ads
    assert c["underwritten_case"]["metrics"]["dscr"] == pytest.approx(dscr_u, abs=1e-6)
    assert c["underwritten_case"]["overall"] == "FAIL" and c["borrower_case"]["overall"] == "PASS"
    drop = B_NOI - noi_uw
    assert c["summary"] == (f"Underwriting adjustments reduce NOI by ${drop:,.0f} ({drop / B_NOI * 100:.1f}%), moving DSCR "
                            f"from {dscr_b:.2f} to {dscr_u:.2f}, which fails the 1.25 minimum.")
    assert len(c["underwritten_case"]["stress"]) == 9


def test_rejected_and_proposed_have_no_effect(client, h, deal):
    s = client.post(f"/api/screening/deals/{deal}/adjustments/suggest", headers=h).json()
    tax = next(a for a in s["adjustments"] if a["line_item"] == "real_estate_taxes")
    client.patch(f"/api/screening/adjustments/{tax['adjustment_id']}", headers=h, json={"status": "rejected"})
    c = compare(client, h, deal)
    assert line(c, "real_estate_taxes") == pytest.approx(fixture_annual("RE Taxes"))
    assert c["totals"]["underwritten"]["noi"] == pytest.approx(B_NOI, abs=0.01)


def test_manual_adjustment_needs_reason_and_note(client, h, deal):
    url = f"/api/screening/deals/{deal}/adjustments"
    assert client.post(url, headers=h, json={"line_item": "payroll", "underwritten_value": 30000, "reason_code": "OTHER",
                                             "note": ""}).status_code == 422
    assert client.post(url, headers=h, json={"line_item": "payroll", "underwritten_value": 30000, "reason_code": "BECAUSE",
                                             "note": "x"}).status_code == 400
    c = client.post(url, headers=h, json={"line_item": "payroll", "underwritten_value": 30000, "reason_code": "OTHER",
                                          "note": "Market payroll for a 20-unit building"}).json()
    adj = next(a for a in c["adjustments"] if a["line_item"] == "payroll")
    assert adj["status"] == "proposed" and line(c, "payroll") == pytest.approx(fixture_annual("On-site Payroll"))
    client.patch(f"/api/screening/adjustments/{adj['adjustment_id']}", headers=h, json={"status": "approved"})
    c = compare(client, h, deal)
    assert line(c, "payroll") == 30000
    approved = next(x for x in c["lines"] if x["key"] == "payroll")["adjustment"]
    assert approved["reason_code"] == "OTHER" and approved["note"].startswith("Market payroll")


def test_only_one_approved_adjustment_per_line(client, h, deal):
    url = f"/api/screening/deals/{deal}/adjustments"
    for v in (25000, 27000):
        client.post(url, headers=h, json={"line_item": "payroll", "underwritten_value": v, "reason_code": "OTHER", "note": f"v{v}"})
    adjs = [a for a in compare(client, h, deal)["adjustments"] if a["line_item"] == "payroll"]
    for a in adjs:
        client.patch(f"/api/screening/adjustments/{a['adjustment_id']}", headers=h, json={"status": "approved"})
    adjs = [a for a in compare(client, h, deal)["adjustments"] if a["line_item"] == "payroll"]
    assert [a["status"] for a in adjs].count("approved") == 1
    assert line(compare(client, h, deal), "payroll") == 27000
