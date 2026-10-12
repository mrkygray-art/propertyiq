"""AI Engineering Lab: request traces (no question text, per-user visibility, retention), registries, schema,
model on/off comparison."""
import json
import os
import secrets
import uuid

import psycopg
import pytest

from propertyiq.config import get_settings

pytestmark = pytest.mark.db

try:
    with psycopg.connect(get_settings().dsn("piq_readonly")) as _c:
        _c.execute("SELECT 1 FROM request_traces LIMIT 1")
except Exception as exc:  # pragma: no cover
    pytest.skip(f"request_traces not set up (run scripts/lab_setup.py): {exc}", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

import propertyiq.main as main  # noqa: E402
from propertyiq.auth import hash_password  # noqa: E402
from propertyiq.lab import traces  # noqa: E402
from propertyiq.llm import LLMError, LLMResult  # noqa: E402

MARKER = "zq7xmarker"   # unusual text that must never show up in stored traces


class FakeModel:
    """Fast stand-in for the local model: answers with no figures."""
    chat_model = "fake-model"

    def chat(self, *_, **__):
        return LLMResult("Here is the overview of the synthetic portfolio.", "fake-model", 100, 12, 5)


class DownModel:
    chat_model = "fake-model"

    def chat(self, *_, **__):
        raise LLMError("The local model did not respond. Verified figures are still shown.")


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


def login(client, user, pw=None):
    r = client.post("/api/auth/login", json={"username": user, "password": pw or os.environ["PIQ_DEMO_PASSWORD"]})
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def fast_model(monkeypatch):
    monkeypatch.setattr(main, "PROVIDER", DownModel())


def ask(client, h, question):
    r = client.post("/api/chat", headers=h, json={"question": question})
    assert r.status_code == 200, r.text
    return r.json()


def stored_row(trace_id):
    with psycopg.connect(get_settings().dsn("piq_owner")) as c:
        c.row_factory = psycopg.rows.dict_row
        return c.execute("SELECT * FROM request_traces WHERE trace_id = %s", (trace_id,)).fetchone()


def test_chat_answer_is_traced_without_question_text(client, fast_model):
    h = login(client, "analyst")
    body = ask(client, h, f"Which assets are underperforming {MARKER}?")
    assert body["trace_id"]
    row = stored_row(body["trace_id"])
    assert row is not None and MARKER not in json.dumps(row, default=str)
    assert row["intent"] == "underperforming_summary" and row["answer_source"] == "verified_template"
    assert row["question_chars"] == len(f"Which assets are underperforming {MARKER}?")
    assert [s["stage"] for s in row["stages"]] == ["scope", "route", "facts", "model", "answer"]
    assert row["stages"][-2]["outcome"] == "unavailable"
    assert set(row["cited_property_ids"]) >= {"PIQ-001", "PIQ-002", "PIQ-003", "PIQ-004"}
    assert row["source_record_ids"] and row["facts_sha256"]
    t = client.get(f"/api/lab/traces/{body['trace_id']}", headers=h).json()
    assert t["stages"][2]["source_records"] == len(t["source_record_ids"])


def test_users_see_only_their_own_traces(client, fast_model):
    analyst, restricted = login(client, "analyst"), login(client, "restricted")
    mine = ask(client, analyst, "Give me a portfolio overview")["trace_id"]
    assert client.get(f"/api/lab/traces/{mine}", headers=restricted).status_code == 404
    assert client.get(f"/api/lab/traces/{uuid.uuid4()}", headers=restricted).status_code == 404
    listed = client.get("/api/lab/traces", headers=restricted).json()["traces"]
    assert all(t["username"] == "restricted" for t in listed)
    assert client.get("/api/lab/traces/not-a-uuid", headers=analyst).status_code == 422
    assert client.get("/api/lab/traces").status_code == 401


def test_admin_sees_every_trace(client, fast_model):
    name, pw = f"pytest_admin_{uuid.uuid4().hex[:8]}", secrets.token_urlsafe(16)
    dsn = get_settings().dsn("piq_owner")
    with psycopg.connect(dsn) as c:
        c.execute("INSERT INTO demo_users VALUES (%s, 'pytest admin', 'admin', %s)", (name, hash_password(pw)))
    try:
        tid = ask(client, login(client, "restricted"), "Give me a portfolio overview")["trace_id"]
        assert client.get(f"/api/lab/traces/{tid}", headers=login(client, name, pw)).status_code == 200
    finally:
        with psycopg.connect(dsn) as c:
            c.execute("DELETE FROM demo_users WHERE username = %s", (name,))


def test_old_traces_are_deleted_after_retention(client, fast_model):
    old = str(uuid.uuid4())
    with traces._writer() as c:
        c.execute("INSERT INTO request_traces (trace_id, created_at, username, funds, endpoint, intent, answer_source, "
                  "verification_status, question_chars, total_ms, stages) VALUES (%s, now() - interval '31 days', "
                  "'analyst', '{}', 'chat', 'portfolio_summary', 'verified_template', 'not_applicable', 1, 1, '[]')", (old,))
    ask(client, login(client, "analyst"), "Give me a portfolio overview")
    assert stored_row(old) is None


def test_trace_writer_cannot_touch_other_tables():
    with traces._writer() as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("UPDATE properties SET name = 'x' WHERE property_id = 'PIQ-001'")


def test_model_on_off_comparison_gives_identical_facts(client, monkeypatch):
    monkeypatch.setattr(main.PROVIDER, "chat", FakeModel().chat)
    r = client.post("/api/lab/compare", headers=login(client, "analyst"), json={"question": "Give me a portfolio overview"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["same_facts"] and b["same_intent"]
    assert b["model_off"]["answer_source"] == "verified_template" and b["model_on"]["answer_source"] == "local_model"
    assert b["model_off"]["trace_id"] and stored_row(b["model_on"]["trace_id"])["endpoint"] == "lab_compare_model_on"


def test_schema_hides_row_counts_from_users_without_every_fund(client):
    full = client.get("/api/lab/schema", headers=login(client, "analyst")).json()
    part = client.get("/api/lab/schema", headers=login(client, "restricted")).json()
    assert full["row_counts_shown"] and not part["row_counts_shown"]
    assert all(t["rows"] is None for t in part["tables"])
    names = {t["name"] for t in full["tables"]}
    assert {"request_traces", "loans", "properties"} <= names
    loans = next(t for t in part["tables"] if t["name"] == "loans")
    assert {r["table"] for r in loans["references"]} == {"lenders", "portfolios"}
    roles = {r["rolname"]: r for r in full["roles"]}
    assert roles["piq_readonly"]["can_write"] == 0 and roles["piq_trace_writer"]["can_write"] == 1


def test_tool_registry_and_models(client):
    h = login(client, "analyst")
    tools = {t["id"]: t for t in client.get("/api/lab/tools", headers=h).json()["tools"]}
    assert "user_portfolio_access" in tools["analytics.evaluate_properties"]["scope"]
    assert tools["chat.verify_numbers"]["source_line"] > 0
    m = client.get("/api/lab/models", headers=h)
    assert m.status_code == 200 and "allowlist" in m.json()


def test_loan_questions_point_to_the_debt_page(client, fast_model):
    body = ask(client, login(client, "analyst"), "What is the DSCR on Westlake's loan?")
    assert body["intent"] == "unsupported" and "/debt" in body["answer"]
