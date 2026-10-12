"""Loan screening API: evaluate, guideline sets, exports, sign-in, and the off switch."""
import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest

from propertyiq.config import get_settings

pytestmark = pytest.mark.db

try:
    with psycopg.connect(get_settings().dsn("piq_readonly")) as _c:
        _c.execute("SELECT 1 FROM screening_guideline_sets LIMIT 1")
except Exception as exc:  # pragma: no cover
    pytest.skip(f"screening tables not set up: {exc}", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from propertyiq.main import app  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CASE_A = {"property_name": "Test case A", "property_type": "multifamily", "value": 6000000, "pgi": 600000,
          "vacancy_pct": 5, "operating_expenses": 170000, "loan_amount": 4000000, "interest_rate_pct": 6.5,
          "amortization_years": 30}
GUIDE = {"name": "", "min_dscr": {"enabled": True, "threshold": 1.25}, "max_ltv": {"enabled": True, "threshold": 70},
         "min_debt_yield": {"enabled": True, "threshold": 8}, "max_break_even": {"enabled": True, "threshold": 85},
         "min_loan": {"enabled": False, "threshold": None}, "max_loan": {"enabled": False, "threshold": None},
         "allowed_types_enabled": False, "dscr_basis": "amortizing"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def h(client):
    r = client.post("/api/auth/login", json={"username": "analyst", "password": os.environ["PIQ_DEMO_PASSWORD"]})
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def temp_set(client, h):
    """A guideline set created for one test and always removed afterwards."""
    created = []

    def make(**over):
        body = {**GUIDE, "name": f"pytest {uuid.uuid4().hex[:8]}", **over}
        r = client.post("/api/screening/guidelines", headers=h, json=body)
        assert r.status_code == 200, r.text
        created.append(r.json()["set_id"])
        return r.json()

    yield make
    for set_id in created:
        client.delete(f"/api/screening/guidelines/{set_id}", headers=h)


def evaluate(client, h, deal, **extra):
    r = client.post("/api/screening/evaluate", headers=h, json={"deal": deal, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_case_a_through_the_api(client, h):
    body = evaluate(client, h, CASE_A)
    m = body["metrics"]
    assert body["valid"] and body["overall"] == "PASS"
    assert round(m["monthly_payment"]) == 25283 and round(m["annual_debt_service"]) == 303393
    assert round(m["dscr"], 2) == 1.32 and round(m["break_even_occupancy"], 3) == 0.789
    assert body["guideline_set"]["name"] == "Example (set your own)"
    assert [s["key"] for s in body["stress"]][:3] == ["base", "rate_1", "rate_2"]
    assert body["stress"][1]["overall"] == "FAIL"
    assert len(body["grid"]["dscr"]) == len(body["grid"]["rates"]) == 7
    assert {"label": "Interest rate", "value": "6.5%"} in body["assumptions"]
    assert "Not an appraisal" in body["disclaimer"]


def test_case_c_fails_through_the_api(client, h):
    body = evaluate(client, h, {**CASE_A, "interest_rate_pct": 7.5})
    dscr_rule = next(r for r in body["rules"] if r["rule_id"] == "min_dscr")
    assert body["overall"] == "FAIL" and dscr_rule["message"] == "DSCR 1.19 vs. minimum 1.25: FAIL"


def test_invalid_inputs_return_friendly_errors(client, h):
    body = evaluate(client, h, {**CASE_A, "pgi": -5, "vacancy_pct": 120, "value": 0})
    assert not body["valid"] and "metrics" not in body
    assert "Potential gross income can't be negative." in body["errors"]
    assert "Vacancy and credit loss must be between 0% and 100%." in body["errors"]
    assert "Property value must be more than $0." in body["errors"]


def test_missing_loan_is_not_an_error(client, h):
    body = evaluate(client, h, {**CASE_A, "loan_amount": None})
    assert body["valid"] and body["metrics"]["dscr"] is None and body["overall"] == "NOT CHECKED"


def test_custom_scenarios(client, h):
    body = evaluate(client, h, CASE_A, scenarios=[{"key": "x", "label": "Rate +3%", "rate_add_pct": 3}])
    assert [s["label"] for s in body["stress"]] == ["Rate +3%"]
    assert body["stress"][0]["interest_rate"] == pytest.approx(0.095)


def test_guideline_set_round_trip(client, h, temp_set):
    s = temp_set(min_dscr={"enabled": True, "threshold": 1.4}, max_ltv={"enabled": True, "threshold": 65})
    assert s["min_dscr"]["threshold"] == 1.4 and s["max_ltv"]["threshold"] == 65
    body = evaluate(client, h, CASE_A, guideline_set_id=s["set_id"])
    assert body["guideline_set"]["name"] == s["name"] and body["overall"] == "FAIL"   # 1.32 < 1.40, 66.7% > 65%
    updated = client.put(f"/api/screening/guidelines/{s['set_id']}", headers=h,
                         json={**GUIDE, "name": s["name"], "min_dscr": {"enabled": False, "threshold": 1.4},
                               "max_ltv": {"enabled": True, "threshold": 70}})
    assert updated.status_code == 200
    assert evaluate(client, h, CASE_A, guideline_set_id=s["set_id"])["overall"] == "PASS"


def test_duplicate_guideline_name_rejected(client, h, temp_set):
    s = temp_set()
    r = client.post("/api/screening/guidelines", headers=h, json={**GUIDE, "name": s["name"]})
    assert r.status_code == 400 and "already exists" in r.json()["detail"]


def test_unknown_guideline_set_is_404(client, h):
    r = client.post("/api/screening/evaluate", headers=h, json={"deal": CASE_A, "guideline_set_id": 999999})
    assert r.status_code == 404


def test_cannot_delete_last_guideline_set(client, h):
    sets = client.get("/api/screening/guidelines", headers=h).json()
    if len(sets) == 1:
        r = client.delete(f"/api/screening/guidelines/{sets[0]['set_id']}", headers=h)
        assert r.status_code == 400 and "at least one" in r.json()["detail"]


def test_exports(client, h):
    pdf = client.post("/api/screening/export/pdf", headers=h, json={"deal": CASE_A})
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content[:4] == b"%PDF"
    assert "PropertyIQ-screening-Test-case-A-" in pdf.headers["content-disposition"]
    xlsx = client.post("/api/screening/export/xlsx", headers=h, json={"deal": CASE_A})
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"
    bad = client.post("/api/screening/export/pdf", headers=h, json={"deal": {**CASE_A, "value": 0}})
    assert bad.status_code == 422


@pytest.mark.parametrize("method,path", [
    ("get", "/api/screening/status"), ("post", "/api/screening/evaluate"), ("get", "/api/screening/guidelines"),
    ("post", "/api/screening/export/pdf"),
])
def test_sign_in_required(client, method, path):
    assert getattr(client, method)(path).status_code == 401


def test_readonly_role_still_cannot_write_guidelines():
    with psycopg.connect(get_settings().dsn("piq_readonly"), autocommit=True) as conn:
        conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM screening_guideline_sets")


def test_screening_writer_cannot_touch_portfolio_tables():
    from propertyiq.screening.store import _writer
    with _writer() as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE properties SET name = 'x' WHERE property_id = 'PIQ-001'")


def test_off_switch_removes_module_and_keeps_everything_else():
    code = ("import propertyiq.main as m; paths = {r.path for r in m.app.routes}; "
            "print(any(p.startswith('/api/screening') for p in paths), "
            "'/api/properties/underperforming' in paths, '/api/chat' in paths)")
    env = {**os.environ, "PIQ_SCREENING_ENABLED": "false", "PYTHONPATH": str(ROOT / "services" / "api")}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert out.stdout.split() == ["False", "True", "True"], out.stderr
