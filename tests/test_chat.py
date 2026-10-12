"""Chat routing, authorization, number verification and graceful degradation."""
import os

import psycopg
import pytest

from propertyiq.chat import verify_numbers
from propertyiq.config import get_settings
from propertyiq.llm import LLMError, LLMResult

pytestmark = pytest.mark.db

try:
    with psycopg.connect(get_settings().dsn("piq_readonly")) as _c:
        _c.execute("SELECT 1 FROM properties LIMIT 1")
except Exception as exc:  # pragma: no cover
    pytest.skip(f"seeded database not reachable: {exc}", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from propertyiq import main  # noqa: E402


class FakeProvider:
    """Records what the model would have been sent and returns a scripted reply."""
    name = "fake"

    def __init__(self, reply="OK", fail=False):
        self.reply, self.fail, self.prompts = reply, fail, []

    def available(self):
        return True

    def chat(self, system, user, **_):
        self.prompts.append((system, user))
        if self.fail:
            raise LLMError("The local model did not respond. Verified figures are still shown.")
        return LLMResult(self.reply, "fake-model", None, None, None)

    def embed(self, texts):
        return [[0.0] * 768 for _ in texts]


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


def headers(client, username):
    r = client.post("/api/auth/login", json={"username": username, "password": os.environ["PIQ_DEMO_PASSWORD"]})
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def fake(monkeypatch):
    def install(**kw):
        provider = FakeProvider(**kw)
        monkeypatch.setattr(main, "PROVIDER", provider)
        return provider
    return install


def ask(client, h, question, **extra):
    r = client.post("/api/chat", headers=h, json={"question": question, **extra})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- the demo questions

def test_summarize_underperforming(client, fake):
    provider = fake(reply="For 2026-09-30, 4 of 55 properties are flagged with a $420,000 shortfall. "
                          "Westlake Office Center (PIQ-001) is 72% occupied, NOI down 18%, $180K below budget. "
                          "Riverside (PIQ-002), Harbor (PIQ-003) and Sunset (PIQ-004) are also flagged.")
    body = ask(client, headers(client, "analyst"), "Summarize our underperforming assets.")
    assert body["intent"] == "underperforming_summary"
    assert body["facts"]["flagged_count"] == 4 and body["facts"]["properties_evaluated"] == 55
    assert body["facts"]["total_budget_shortfall"] == 420000.0
    assert body["answer_source"] == "local_model" and body["verification"]["status"] == "verified"
    assert [c["property_id"] for c in body["citations"]] == ["PIQ-001", "PIQ-002", "PIQ-003", "PIQ-004"]
    assert body["calculation_link"] == "/verified?period=2026-09-30"
    _, prompt = provider.prompts[0]
    assert "Westlake" in prompt and "PIQ-005" not in prompt  # only flagged properties are sent


def test_why_westlake_then_followup_variance(client, fake):
    fake(reply="Hypothesis (not verified): unknown.")
    h = headers(client, "analyst")
    why = ask(client, h, "Why was Westlake flagged?")
    assert why["intent"] == "property_why" and why["focus_property_id"] == "PIQ-001"
    assert why["facts"]["trend"]["causal_evidence"] == "none"
    follow = ask(client, h, "Show the underlying NOI budget variance.", context_property_id="PIQ-001")
    assert follow["intent"] == "property_variance" and follow["focus_property_id"] == "PIQ-001"
    assert len(follow["facts"]["monthly_ttm"]) == 12
    assert follow["facts"]["property"]["noi_budget_variance"] == -180000.0


def test_template_states_missing_causal_evidence(client, fake):
    fake(fail=True)
    body = ask(client, headers(client, "analyst"), "Why is Westlake underperforming?")
    assert body["answer_source"] == "verified_template"
    assert "no causal evidence" in body["answer"]
    assert "72.0%" in body["answer"] and "-18.0%" in body["answer"]


# ---------------------------------------------------------------- verification and degradation

def test_invented_numbers_are_rejected(client, fake):
    fake(reply="Westlake lost $2.4 million and occupancy fell to 41%.")
    body = ask(client, headers(client, "analyst"), "Why was Westlake flagged?")
    assert body["answer_source"] == "verified_template"
    assert body["verification"]["status"] == "rejected"
    assert set(body["verification"]["unsupported_numbers"]) == {"$2.4 million", "41%"}
    assert "$2.4" not in body["answer"]


def test_summary_that_omits_flagged_properties_is_rejected(client, fake):
    # The real failure seen with qwen3:4b-instruct: correct numbers, but only two of the four named.
    fake(reply="Our underperforming assets are PIQ-001 (Westlake Office Center) and PIQ-002 (Riverside Retail Plaza). "
               "Total budget shortfall: $420K.")
    body = ask(client, headers(client, "analyst"), "Summarize our underperforming assets.")
    assert body["answer_source"] == "verified_template"
    assert body["verification"]["missing_properties"] == ["PIQ-003", "PIQ-004"]
    assert "Harbor Industrial Park" in body["answer"] and "Sunset Multifamily" in body["answer"]


def test_model_receives_verified_summary_to_rephrase(client, fake):
    provider = fake(reply="ok")
    ask(client, headers(client, "analyst"), "Summarize our underperforming assets.")
    _, prompt = provider.prompts[0]
    assert "VERIFIED_SUMMARY:" in prompt and "4 of 55" in prompt


def test_model_down_still_returns_verified_numbers(client, fake):
    fake(fail=True)
    body = ask(client, headers(client, "analyst"), "Summarize our underperforming assets.")
    assert body["answer_source"] == "verified_template"
    assert "4 of 55" in body["answer"] and "$420,000" in body["answer"]
    assert body["notes"]


@pytest.mark.parametrize("text,status", [
    ("Shortfall of $420,000 across 4 assets.", "verified"),
    ("Shortfall of $420K.", "verified"),
    ("Shortfall of $0.42 million.", "verified"),
    ("NOI fell 18% to $720,000.", "verified"),
    ("Shortfall of $500,000.", "rejected"),
    ("Occupancy is 64%.", "rejected"),
])
def test_verify_numbers(text, status):
    facts = {"total_budget_shortfall": 420000.0, "noi_yoy_pct": -18.0, "noi_actual_ttm": 720000.0, "occ": 72.0}
    assert verify_numbers(text, facts)["status"] == status


# ---------------------------------------------------------------- refusals

@pytest.mark.parametrize("question,phrase", [
    ("What is Westlake's cap rate?", "Cap rate"),
    ("Should we sell Riverside Retail Plaza?", "investment"),
    ("Forecast NOI for next year", "doesn't forecast"),
    ("Write me a poem about the beach", "I can answer questions"),
])
def test_refusals_skip_the_model(client, fake, question, phrase):
    provider = fake(reply="should never be used")
    body = ask(client, headers(client, "analyst"), question)
    assert phrase in body["answer"] and body["answer_source"] == "verified_template"
    assert provider.prompts == [] and body["facts"] == {}


# ---------------------------------------------------------------- authorization

def test_restricted_summary_only_fund_ii(client, fake):
    provider = fake(reply="2 properties are flagged.")
    body = ask(client, headers(client, "restricted"), "Summarize our underperforming assets.")
    assert body["facts"]["flagged_count"] == 2 and body["facts"]["properties_evaluated"] == 27
    assert body["facts"]["total_budget_shortfall"] == 120000.0
    _, prompt = provider.prompts[0]
    assert "Westlake" not in prompt and "Riverside" not in prompt and "FUND-I\"" not in prompt


@pytest.mark.parametrize("question", [
    "Why was Westlake flagged?",
    "Show the NOI budget variance for Westlake Office Center",
    "Tell me about PIQ-001",
])
def test_restricted_cannot_reach_fund_i_by_name(client, fake, question):
    provider = fake(reply="should never be used")
    body = ask(client, headers(client, "restricted"), question, context_property_id="PIQ-003")
    assert body["intent"] == "property_not_found" or body["focus_property_id"] == "PIQ-003"
    assert "720" not in str(body) and "Westlake Office Center" not in body["answer"]
    for _, prompt in provider.prompts:
        assert "PIQ-001" not in prompt and "Westlake" not in prompt


def test_restricted_context_property_from_other_portfolio_is_ignored(client, fake):
    fake(reply="ok")
    body = ask(client, headers(client, "restricted"), "Show the underlying NOI budget variance.",
               context_property_id="PIQ-001")
    assert body["focus_property_id"] != "PIQ-001"
    assert "PIQ-001" not in str(body["facts"])


def test_chat_requires_auth(client):
    assert client.post("/api/chat", json={"question": "hi"}).status_code == 401


def test_chat_input_validated(client):
    h = headers(client, "analyst")
    assert client.post("/api/chat", headers=h, json={"question": ""}).status_code == 422
    assert client.post("/api/chat", headers=h, json={"question": "x" * 501}).status_code == 422
    assert client.post("/api/chat", headers=h, json={"question": "hi", "context_property_id": "1;--"}).status_code == 422


# ---------------------------------------------------------------- live local model

@pytest.mark.llm
def test_live_model_summary(client):
    if not main.PROVIDER.available():
        pytest.skip("local model not available")
    body = ask(client, headers(client, "analyst"), "Summarize our underperforming assets.")
    assert body["facts"]["flagged_count"] == 4
    assert "</think>" not in body["answer"]
    # The verified numbers must be right whatever the model did: passed the number check,
    # failed it (replaced by the verified summary), or timed out (also the verified summary).
    if body["answer_source"] == "verified_template":
        assert "4 of 55" in body["answer"] and "$420,000" in body["answer"]
    print(f"\n[{body['answer_source']}, {body['latency_ms']} ms]\n{body['answer']}")
