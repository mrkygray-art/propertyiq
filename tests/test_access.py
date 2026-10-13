"""Phase 0.5: roles, fund-scoped deals and the access audit.

Exit test from docs/roadmap/gap-analysis.md: no cross-fund leakage on deals, documents, flags, adjustments,
exports, chat or aggregates; a viewer can't write; every refusal is audited, without secrets.

A FUND-I deal with real flags and adjustments is built once as credit1 (all funds). The users who mustn't
see it are restricted (analyst, Fund II) and credit3 (credit officer, Fund II).
"""
import os
from pathlib import Path

import psycopg
import pytest

import screening_fixture_data as F
from propertyiq.config import get_settings
from propertyiq.llm import LLMResult

pytestmark = pytest.mark.db

try:
    with psycopg.connect(get_settings().dsn("piq_readonly")) as _c:
        _c.execute("SELECT fund_id FROM screening_deals LIMIT 1")
        _c.execute("SELECT 1 FROM access_audit LIMIT 1")
        assert _c.execute("SELECT count(*) FROM demo_users WHERE username IN ('viewer', 'credit1', 'credit3')").fetchone()[0] == 3
except Exception as exc:  # pragma: no cover
    pytest.skip(f"Phase 0.5 not set up (run scripts/access_setup.py): {exc}", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from propertyiq import main  # noqa: E402

FX = Path(__file__).parent / "fixtures" / "screening"
PASSWORDS = {"analyst": "PIQ_DEMO_PASSWORD", "restricted": "PIQ_DEMO_PASSWORD", "viewer": "PIQ_VIEWER_PASSWORD",
             "credit1": "PIQ_CREDIT_PASSWORD", "credit2": "PIQ_CREDIT_PASSWORD", "credit3": "PIQ_CREDIT_PASSWORD",
             "admin": "PIQ_ADMIN_PASSWORD"}


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
def login(client):
    cache = {}

    def get(username):
        if username not in cache:
            r = client.post("/api/auth/login", json={"username": username, "password": os.environ[PASSWORDS[username]]})
            assert r.status_code == 200, r.text
            cache[username] = {"Authorization": f"Bearer {r.json()['token']}"}
        return cache[username]
    return get


def audit_rows(**where):
    cond = " AND ".join(f"{k} = %({k})s" for k in where)
    with psycopg.connect(get_settings().dsn("piq_readonly")) as c:
        c.row_factory = psycopg.rows.dict_row
        return c.execute(f"SELECT * FROM access_audit WHERE {cond} ORDER BY event_id DESC", where).fetchall()


@pytest.fixture(scope="module")
def fund1_deal(client, login):
    """A FUND-I deal with approved documents, open flags and proposed adjustments."""
    h = login("credit1")
    main.PROVIDER, saved = FakeAI(), main.PROVIDER
    try:
        d = client.post("/api/screening/deals", headers=h, json={
            "fund_id": "FUND-I", "name": "pytest access Fund I", "property_facts": {**F.DEAL_FACTS, "property_type": "multifamily"},
            "borrower_inputs": {"property_type": "multifamily"}}).json()
        did, docs = d["deal_id"], []
        for name in ("problem_rent_roll.xlsx", "problem_t12.xlsx"):
            doc = client.post(f"/api/screening/deals/{did}/documents", headers=h,
                              files={"file": (name, (FX / name).read_bytes())}).json()
            assert client.post(f"/api/screening/documents/{doc['document_id']}/approve", headers=h).status_code == 200
            docs.append(doc)
        flags = client.get(f"/api/screening/deals/{did}/analysis", headers=h).json()["flags"]
        client.put(f"/api/screening/deals/{did}/loan", headers=h, json={
            "value": 6000000, "loan_amount": 4000000, "interest_rate_pct": 6.5, "amortization_years": 30,
            "interest_only_years": 0, "term_years": 10})
        adjustments = client.post(f"/api/screening/deals/{did}/adjustments/suggest", headers=h).json()["adjustments"]
        assert flags and len(adjustments) >= 2
        yield {"deal_id": did, "doc_id": docs[0]["document_id"], "item_id": docs[0]["items"][0]["item_id"],
               "flag_id": flags[0]["flag_id"], "adjustments": [a["adjustment_id"] for a in adjustments]}
    finally:
        main.PROVIDER = saved
    client.delete(f"/api/screening/deals/{did}", headers=h)


# ---------------------------------------------------------------- roles

@pytest.mark.parametrize("username, role, perms", [
    ("viewer", "viewer", ["view"]),
    ("analyst", "analyst", ["view", "work_deals"]),
    ("credit1", "credit_officer", ["view", "work_deals", "decide", "policy"]),
    ("admin", "admin", ["view", "work_deals", "decide", "policy", "admin"]),
])
def test_sign_in_reports_role_and_permissions(client, username, role, perms):
    r = client.post("/api/auth/login", json={"username": username, "password": os.environ[PASSWORDS[username]]})
    assert r.status_code == 200
    assert (r.json()["user"]["role"], r.json()["user"]["permissions"]) == (role, perms)


def test_credit_officer_accounts_and_grants(client, login):
    funds = {u: client.get("/api/me", headers=login(u)).json()["portfolios"] for u in ("credit1", "credit2", "credit3")}
    assert funds == {"credit1": ["FUND-I", "FUND-II"], "credit2": ["FUND-I"], "credit3": ["FUND-II"]}


def test_demo_deal_is_in_fund_ii():
    with psycopg.connect(get_settings().dsn("piq_readonly")) as c:
        rows = c.execute("SELECT fund_id FROM screening_deals WHERE name ILIKE 'Juniper Court%'").fetchall()
    assert rows and {r[0] for r in rows} == {"FUND-II"}


# ---------------------------------------------------------------- no cross-fund leakage

@pytest.mark.parametrize("username", ["restricted", "credit3"])
def test_other_fund_deal_is_invisible(client, login, fund1_deal, username):
    h, d = login(username), fund1_deal
    did = d["deal_id"]
    assert did not in [x["deal_id"] for x in client.get("/api/screening/deals", headers=h).json()]
    missing = client.get("/api/screening/deals/999999", headers=h)
    reads = [f"/api/screening/deals/{did}", f"/api/screening/deals/{did}/analysis", f"/api/screening/deals/{did}/compare",
             f"/api/screening/deals/{did}/california", f"/api/screening/deals/{did}/export/pdf",
             f"/api/screening/deals/{did}/export/xlsx", f"/api/screening/documents/{d['doc_id']}"]
    for path in reads:
        r = client.get(path, headers=h)
        assert r.status_code == 404, path
        if "/deals/" in path and "export" not in path and "/documents/" not in path:
            assert r.json() == missing.json(), path       # same answer as a deal that doesn't exist


@pytest.mark.parametrize("username", ["restricted", "credit3"])
def test_other_fund_records_cant_be_changed(client, login, fund1_deal, username):
    """Writes on another fund's records get 404 (never 403, which would confirm the record exists)."""
    h, d = login(username), fund1_deal
    did, adj = d["deal_id"], d["adjustments"][0]
    calls = [
        ("patch", f"/api/screening/deals/{did}", {"json": {"name": "hijacked"}}),
        ("post", f"/api/screening/deals/{did}/documents", {"files": {"file": ("x.csv", b"a,b\n1,2\n")}}),
        ("patch", f"/api/screening/documents/{d['doc_id']}/items/{d['item_id']}", {"json": {"data": {}}}),
        ("post", f"/api/screening/documents/{d['doc_id']}/unapprove", {}),
        ("delete", f"/api/screening/documents/{d['doc_id']}", {}),
        ("post", f"/api/screening/flags/{d['flag_id']}/status", {"json": {"status": "dismissed", "note": "x"}}),
        ("put", f"/api/screening/deals/{did}/loan", {"json": {"loan_amount": 1}}),
        ("post", f"/api/screening/deals/{did}/adjustments/suggest", {}),
        ("patch", f"/api/screening/adjustments/{adj}", {"json": {"status": "approved"}}),
        ("delete", f"/api/screening/adjustments/{adj}", {}),
        ("delete", f"/api/screening/deals/{did}", {}),
    ]
    for method, path, kw in calls:
        r = getattr(client, method)(path, headers=h, **kw)
        assert r.status_code == 404, (method, path, r.status_code)
    # Nothing changed.
    deal = client.get(f"/api/screening/deals/{did}", headers=login("credit1")).json()
    assert deal["name"] == "pytest access Fund I" and len(deal["documents"]) == 2


def test_cant_create_a_deal_in_an_ungranted_fund(client, login):
    r = client.post("/api/screening/deals", headers=login("restricted"), json={"name": "x", "fund_id": "FUND-I"})
    assert r.status_code == 403
    assert audit_rows(username="restricted", action="deal.create", outcome="refused")[0]["reason"] == "fund"


def test_one_fund_user_gets_their_fund_by_default(client, login):
    r = client.post("/api/screening/deals", headers=login("restricted"), json={"name": "pytest default fund"})
    assert r.status_code == 200 and r.json()["fund_id"] == "FUND-II"
    client.delete(f"/api/screening/deals/{r.json()['deal_id']}", headers=login("restricted"))
    two = client.post("/api/screening/deals", headers=login("analyst"), json={"name": "x"})
    assert two.status_code == 400 and "fund" in two.json()["detail"]


def test_fund_i_only_user_sees_fund_i_everywhere(client, login):
    """credit2 (Fund I only) across the other modules: totals, debt and chat."""
    h = login("credit2")
    summary = client.get("/api/portfolio/summary", headers=h).json()
    assert summary["scope"]["portfolios"] == ["FUND-I"] and summary["property_count"] < 55
    debt = client.get("/api/debt/summary", headers=h).json()
    assert debt["scope"]["funds"] == ["FUND-I"] and {l["fund_id"] for l in debt["loans"]} == {"FUND-I"}
    with psycopg.connect(get_settings().dsn("piq_readonly")) as c:
        juniper = c.execute("SELECT deal_id FROM screening_deals WHERE name ILIKE 'Juniper Court%'").fetchone()
    if juniper:
        assert client.get(f"/api/screening/deals/{juniper[0]}", headers=h).status_code == 404


# ---------------------------------------------------------------- what each role can change

def test_viewer_can_read_but_not_write(client, login, fund1_deal):
    h, d = login("viewer"), fund1_deal
    did = d["deal_id"]
    for path in (f"/api/screening/deals/{did}", f"/api/screening/deals/{did}/compare", "/api/screening/deals",
                 f"/api/screening/deals/{did}/export/pdf", "/api/screening/guidelines", "/api/portfolio/summary"):
        assert client.get(path, headers=h).status_code == 200, path
    writes = [
        ("post", "/api/screening/deals", {"json": {"name": "x", "fund_id": "FUND-I"}}),
        ("patch", f"/api/screening/deals/{did}", {"json": {"name": "x"}}),
        ("post", f"/api/screening/deals/{did}/documents", {"files": {"file": ("x.csv", b"a,b\n")}}),
        ("post", f"/api/screening/documents/{d['doc_id']}/unapprove", {}),
        ("post", f"/api/screening/flags/{d['flag_id']}/status", {"json": {"status": "dismissed", "note": "x"}}),
        ("post", f"/api/screening/deals/{did}/adjustments", {"json": {"line_item": "insurance", "underwritten_value": 1,
                                                                    "reason_code": "OTHER", "note": "x"}}),
        ("patch", f"/api/screening/adjustments/{d['adjustments'][0]}", {"json": {"note": "x"}}),
        ("delete", f"/api/screening/deals/{did}", {}),
        ("delete", "/api/screening/guidelines/999999", {}),
        ("put", "/api/screening/settings/market_stale_days", {"json": {"value": "5"}}),
        ("get", "/api/admin/audit", {}),
    ]
    for method, path, kw in writes:
        r = getattr(client, method)(path, headers=h, **kw)
        assert r.status_code == 403 and "viewer" in r.json()["detail"], (method, path, r.status_code)
    assert audit_rows(username="viewer", action="deal.edit", outcome="refused")[0]["reason"] == "role"


def test_analyst_works_deals_but_cant_decide(client, login, fund1_deal):
    h, d = login("analyst"), fund1_deal
    adj = d["adjustments"][0]
    # Can edit a proposal...
    assert client.patch(f"/api/screening/adjustments/{adj}", headers=h, json={"note": "analyst note"}).status_code == 200
    # ...but approving, rejecting, dismissing a flag or changing lender policy needs a credit officer.
    for status in ("approved", "rejected"):
        r = client.patch(f"/api/screening/adjustments/{adj}", headers=h, json={"status": status})
        assert r.status_code == 403 and "credit officer" in r.json()["detail"]
    r = client.post(f"/api/screening/flags/{d['flag_id']}/status", headers=h, json={"status": "dismissed", "note": "x"})
    assert r.status_code == 403
    assert client.put("/api/screening/settings/market_stale_days", headers=h, json={"value": "5"}).status_code == 403


def test_credit_officer_decides_and_analyst_cant_undo(client, login, fund1_deal):
    adj = fund1_deal["adjustments"][1]
    co, an = login("credit1"), login("analyst")
    assert client.patch(f"/api/screening/adjustments/{adj}", headers=co,
                        json={"status": "approved", "note": "credit officer approved"}).status_code == 200
    # Once decided, an analyst can't edit, undo or delete it.
    assert client.patch(f"/api/screening/adjustments/{adj}", headers=an, json={"note": "changed"}).status_code == 403
    assert client.patch(f"/api/screening/adjustments/{adj}", headers=an, json={"status": "proposed"}).status_code == 403
    assert client.delete(f"/api/screening/adjustments/{adj}", headers=an).status_code == 403
    assert audit_rows(username="credit1", action="adjustment.decide", target_id=str(adj))[0]["outcome"] == "allowed"
    client.patch(f"/api/screening/adjustments/{adj}", headers=co, json={"status": "proposed"})


# ---------------------------------------------------------------- the audit itself

def test_sign_ins_are_audited_without_the_attempted_text(client):
    secret = "pytest-not-a-real-password-71f3"
    client.post("/api/auth/login", json={"username": "analyst", "password": secret})
    client.post("/api/auth/login", json={"username": secret, "password": "x"})   # a password typed in the wrong box
    assert audit_rows(username="analyst", action="auth.sign_in", outcome="refused")[0]["reason"] == "sign_in"
    assert audit_rows(username="(unknown)", action="auth.sign_in")[0]["outcome"] == "refused"
    with psycopg.connect(get_settings().dsn("piq_readonly")) as c:
        dump = str(c.execute("SELECT array_agg(a::text) FROM access_audit a").fetchone()[0])
    assert secret not in dump
    for key in ("PIQ_DEMO_PASSWORD", "PIQ_CREDIT_PASSWORD", "PIQ_VIEWER_PASSWORD", "PIQ_ADMIN_PASSWORD"):
        assert os.environ[key] not in dump


def test_audit_writer_can_only_append_and_prune():
    with psycopg.connect(get_settings().dsn("piq_readonly")) as c:
        row = c.execute(
            "SELECT has_table_privilege('piq_audit_writer', 'piq.access_audit', 'INSERT') AS ins, "
            "has_table_privilege('piq_audit_writer', 'piq.access_audit', 'UPDATE') AS upd, "
            "has_table_privilege('piq_audit_writer', 'piq.access_audit', 'TRUNCATE') AS trunc, "
            "(SELECT count(*) FROM pg_class t JOIN pg_namespace n ON n.oid = t.relnamespace WHERE n.nspname = 'piq' "
            " AND t.relkind = 'r' AND t.relname <> 'access_audit' "
            " AND has_table_privilege('piq_audit_writer', t.oid, 'SELECT, INSERT, UPDATE, DELETE')) AS other").fetchone()
    assert row == (True, False, False, 0)


def test_admin_reads_the_audit_and_others_cant(client, login):
    body = client.get("/api/admin/audit?outcome=refused&limit=50", headers=login("admin")).json()
    assert body["retention_days"] == 90 and body["refused"] >= 1
    assert all(e["outcome"] == "refused" for e in body["events"])
    assert set(body["events"][0]) == {"event_id", "created_at", "username", "role", "action", "outcome", "reason",
                                      "target_type", "target_id", "fund_id"}
    r = client.get("/api/admin/audit", headers=login("credit1"))
    assert r.status_code == 403
    assert audit_rows(username="credit1", action="admin.audit")[0]["reason"] == "role"
