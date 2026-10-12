"""API and authorization tests against the seeded local database (scripts/seed.py)."""
import hashlib
import json
import os

import psycopg
import pytest

from propertyiq.config import get_settings

pytestmark = pytest.mark.db

try:
    with psycopg.connect(get_settings().dsn("piq_readonly")) as _c:
        _c.execute("SELECT 1 FROM properties LIMIT 1")
except Exception as exc:  # pragma: no cover
    pytest.skip(f"seeded database not reachable: {exc}", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from propertyiq.main import app  # noqa: E402

PASSWORD = os.environ["PIQ_DEMO_PASSWORD"]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def token(client, username):
    r = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="module")
def analyst(client):
    return token(client, "analyst")


@pytest.fixture(scope="module")
def restricted(client):
    return token(client, "restricted")


def verify_hash(body: dict) -> bool:
    claimed = body.pop("payload_sha256")
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest() == claimed


# ---------------------------------------------------------------- the demo question

def test_underperforming_matches_brief(client, analyst):
    r = client.get("/api/properties/underperforming", headers=analyst)
    assert r.status_code == 200
    body = r.json()
    assert body["synthetic_data"] is True
    assert body["period"] == "2026-09-30"
    assert body["scope"]["properties_evaluated"] == 55
    assert body["flagged_count"] == 4
    assert body["total_budget_shortfall_flagged"] == 420000.00
    got = [(f["priority_rank"], f["name"], f["metrics"]["budget_shortfall"]) for f in body["flagged"]]
    assert got == [
        (1, "Westlake Office Center", 180000.0),
        (2, "Riverside Retail Plaza", 120000.0),
        (3, "Harbor Industrial Park", 70000.0),
        (4, "Sunset Multifamily", 50000.0),
    ]
    assert body["incomplete_data"] == []
    assert verify_hash(body)


def test_flag_details_westlake(client, analyst):
    body = client.get("/api/properties/underperforming", headers=analyst).json()
    w = body["flagged"][0]
    assert w["property_id"] == "PIQ-001"
    m = w["metrics"]
    assert (m["occupancy_pct"], m["noi_yoy_pct"], m["noi_actual"], m["noi_budget"]) == (72.0, -18.0, 720000.0, 900000.0)
    assert set(w["rules_triggered"]) == {"occupancy_below_min", "noi_yoy_decline", "noi_below_budget"}
    calc = w["calculation"]
    assert calc["current_ttm"] == {"start": "2025-10-31", "end": "2026-09-30"}
    assert {k: len(v) for k, v in calc["source_records"].items()} == {
        "monthly_financials_current_ttm": 12, "monthly_financials_prior_ttm": 12,
        "monthly_budgets_current_ttm": 12, "monthly_financials_occupancy": 1}


def test_harbor_and_sunset_flagged_by_budget_rule_only(client, analyst):
    body = client.get("/api/properties/underperforming", headers=analyst).json()
    by_name = {f["name"]: f for f in body["flagged"]}
    for name in ("Harbor Industrial Park", "Sunset Multifamily"):
        assert by_name[name]["rules_triggered"] == ["noi_below_budget"]


def test_portfolio_summary(client, analyst):
    body = client.get("/api/portfolio/summary", headers=analyst).json()
    assert body["property_count"] == 55 and body["flagged_count"] == 4
    assert body["total_budget_shortfall_flagged"] == 420000.0
    assert {c["asset_class"]: c["property_count"] for c in body["by_asset_class"]} == {
        "office": 14, "retail": 14, "industrial": 14, "multifamily": 13}
    assert verify_hash(body)


def test_property_detail(client, analyst):
    body = client.get("/api/properties/PIQ-001", headers=analyst).json()
    prop = body["property"]
    assert prop["priority_rank"] == 1 and prop["metrics"]["budget_shortfall"] == 180000.0
    assert len(body["monthly"]) == 36
    current_ids = prop["calculation"]["source_records"]["monthly_financials_current_ttm"]
    assert current_ids == [m["record_id"] for m in body["monthly"][-12:]]
    assert sum(m["noi"] for m in body["monthly"][-12:]) == pytest.approx(720000.0, abs=0.001)


@pytest.mark.parametrize("period", ["2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"])
def test_other_periods_never_flag_the_other_51(client, analyst, period):
    body = client.get(f"/api/properties/underperforming?period={period}", headers=analyst).json()
    assert {f["property_id"] for f in body["flagged"]} <= {"PIQ-001", "PIQ-002", "PIQ-003", "PIQ-004"}


def test_bad_period_rejected(client, analyst):
    assert client.get("/api/properties/underperforming?period=2026-09-15", headers=analyst).status_code == 400
    assert client.get("/api/properties/underperforming?period=2019-12-31", headers=analyst).status_code == 400
    assert client.get("/api/properties/underperforming?period=nope", headers=analyst).status_code == 422


# ---------------------------------------------------------------- authorization

def test_restricted_sees_only_fund_ii(client, restricted):
    body = client.get("/api/properties/underperforming", headers=restricted).json()
    assert body["scope"]["portfolios"] == ["FUND-II"]
    assert body["scope"]["properties_evaluated"] == 27
    assert [f["name"] for f in body["flagged"]] == ["Harbor Industrial Park", "Sunset Multifamily"]
    assert body["total_budget_shortfall_flagged"] == 120000.0
    assert all(f["portfolio_id"] == "FUND-II" for f in body["flagged"])


def test_restricted_cannot_open_other_portfolio(client, restricted):
    blocked = client.get("/api/properties/PIQ-001", headers=restricted)
    missing = client.get("/api/properties/PIQ-999", headers=restricted)
    assert blocked.status_code == missing.status_code == 404
    assert blocked.json() == missing.json()


def test_restricted_summary_excludes_fund_i(client, restricted):
    body = client.get("/api/portfolio/summary", headers=restricted).json()
    assert body["property_count"] == 27 and body["flagged_count"] == 2


@pytest.mark.parametrize("path", [
    "/api/properties/underperforming", "/api/portfolio/summary", "/api/properties/PIQ-001",
    "/api/periods", "/api/rules", "/api/me",
])
def test_auth_required(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer forged.token"}).status_code == 401


def test_tampered_token_rejected(client, restricted):
    body, sig = restricted["Authorization"].removeprefix("Bearer ").split(".")
    forged = {"Authorization": f"Bearer {body[:-2]}xx.{sig}"}
    assert client.get("/api/me", headers=forged).status_code == 401


def test_login_failures_are_indistinguishable(client):
    wrong_pw = client.post("/api/auth/login", json={"username": "analyst", "password": "wrong"})
    no_user = client.post("/api/auth/login", json={"username": "nobody", "password": "wrong"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json() == no_user.json()


def test_property_id_validated(client, analyst):
    assert client.get("/api/properties/PIQ-001;DROP", headers=analyst).status_code in (404, 422)
    assert client.get("/api/properties/x' OR '1'='1", headers=analyst).status_code in (404, 422)


def test_health_reveals_no_detail(client):
    body = client.get("/health").json()
    assert set(body) == {"status", "database", "local_model", "synthetic_data"}
    assert body["database"] is True


def test_no_cdn_docs_pages(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404


# ---------------------------------------------------------------- database least privilege

def test_readonly_role_cannot_write():
    with psycopg.connect(get_settings().dsn("piq_readonly"), autocommit=True) as conn:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("UPDATE properties SET name = 'x' WHERE property_id = 'PIQ-001'")
        conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE properties SET name = 'x' WHERE property_id = 'PIQ-001'")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("CREATE TABLE piq.evil (id int)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("CREATE TABLE public.evil (id int)")
