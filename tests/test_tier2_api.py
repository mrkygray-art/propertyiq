"""Tier 2 workspace API: deals, upload, review, approve, analysis, flags, privacy, delete."""
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
        _c.execute("SELECT 1 FROM screening_deals LIMIT 1")
except Exception as exc:  # pragma: no cover
    pytest.skip(f"Tier 2 tables not set up: {exc}", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from propertyiq import main  # noqa: E402
from propertyiq.screening.workspace import UPLOAD_DIR  # noqa: E402

FX = Path(__file__).parent / "fixtures" / "screening"


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
    r = client.post("/api/auth/login", json={"username": "credit1", "password": os.environ["PIQ_CREDIT_PASSWORD"]})
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def deal(client, h, monkeypatch):
    monkeypatch.setattr(main, "PROVIDER", FakeAI())
    r = client.post("/api/screening/deals", headers=h, json={"fund_id": "FUND-I", 
        "name": "pytest Juniper Court", "property_facts": {**F.DEAL_FACTS, "property_type": "multifamily"},
        "borrower_inputs": {"property_type": "multifamily", "units": 20}})
    assert r.status_code == 200, r.text
    d = r.json()
    yield d
    client.delete(f"/api/screening/deals/{d['deal_id']}", headers=h)


def upload(client, h, deal_id, name, kind="auto"):
    with (FX / name).open("rb") as f:
        r = client.post(f"/api/screening/deals/{deal_id}/documents", headers=h,
                        files={"file": (name, f)}, data={"kind": kind})
    assert r.status_code == 200, r.text
    return r.json()


def test_full_flow_problem_fixture(client, h, deal):
    rr = upload(client, h, deal["deal_id"], "problem_rent_roll.xlsx")
    t12 = upload(client, h, deal["deal_id"], "problem_t12.xlsx")
    assert rr["kind"] == "rent_roll" and len(rr["items"]) == 20 and rr["status"] == "extracted"
    assert t12["kind"] == "t12" and t12["summary"]["missing_months"] == ["2026-06"]
    ai_line = next(i for i in t12["items"] if i["original_label"] == "Fire extinguisher inspection")
    assert (ai_line["category"], ai_line["mapped_by"], ai_line["confidence"]) == ("contract_services", "ai", "low")

    # Nothing counts until approved.
    before = client.get(f"/api/screening/deals/{deal['deal_id']}/analysis", headers=h).json()
    assert before["rent_roll"] is None and before["t12"] is None and before["flags"] == []

    for doc in (rr, t12):
        assert client.post(f"/api/screening/documents/{doc['document_id']}/approve", headers=h).status_code == 200
    a = client.get(f"/api/screening/deals/{deal['deal_id']}/analysis?as_of=2026-09-30", headers=h).json()
    assert a["rent_roll"]["unit_count"] == 20 and a["rent_roll"]["occupied_count"] == 18
    assert a["t12"]["months_present"] == 11
    fps = {f["fingerprint"] for f in a["flags"]}
    assert {"A1:vacancy", "A3:label:Insurance claim settlement", "A4:insurance", "A4:repairs_maintenance",
            "A5:management_fee", "A12:integrity"} <= fps


def test_clean_fixture_has_no_high_flags(client, h, deal):
    for name in ("clean_rent_roll.csv", "clean_t12.csv"):
        doc = upload(client, h, deal["deal_id"], name)
        client.post(f"/api/screening/documents/{doc['document_id']}/approve", headers=h)
    a = client.get(f"/api/screening/deals/{deal['deal_id']}/analysis?as_of=2026-09-30", headers=h).json()
    assert [f for f in a["flags"] if f["severity"] == "high"] == []


def test_review_edits_and_approval_rules(client, h, deal):
    doc = upload(client, h, deal["deal_id"], "problem_t12.xlsx")
    line = next(i for i in doc["items"] if i["original_label"] == "Management Fee")
    months = {m: 1800 for m in line["data"]["months"]}
    r = client.patch(f"/api/screening/documents/{doc['document_id']}/items/{line['item_id']}", headers=h,
                     json={"data": {"months": months}})
    edited = next(i for i in r.json()["items"] if i["item_id"] == line["item_id"])
    assert edited["edited"] and edited["data"]["months"]["2025-10"] == "1800"
    client.post(f"/api/screening/documents/{doc['document_id']}/approve", headers=h)
    a = client.get(f"/api/screening/deals/{deal['deal_id']}/analysis", headers=h).json()
    assert "A5:management_fee" not in {f["fingerprint"] for f in a["flags"]}   # the corrected fee is used
    locked = client.patch(f"/api/screening/documents/{doc['document_id']}/items/{line['item_id']}", headers=h,
                          json={"category": "other_expense"})
    assert locked.status_code == 400 and "already approved" in locked.json()["detail"]


def test_unmapped_line_blocks_approval(client, h, deal, monkeypatch):
    class Silent(FakeAI):
        def chat(self, *a, **k):
            return LLMResult("{}", "fake", None, None, None)
    monkeypatch.setattr(main, "PROVIDER", Silent())
    csv = b"Account,Oct-25,Nov-25,Dec-25,Jan-26,Feb-26,Mar-26,Total\nGross Potential Rent,1,1,1,1,1,1,6\nZorblax,5,5,5,5,5,5,30\n"
    r = client.post(f"/api/screening/deals/{deal['deal_id']}/documents", headers=h, files={"file": ("odd.csv", csv)})
    doc = r.json()
    assert next(i for i in doc["items"] if i["original_label"] == "Zorblax")["category"] == "unmapped"
    bad = client.post(f"/api/screening/documents/{doc['document_id']}/approve", headers=h)
    assert bad.status_code == 400 and "Zorblax" in bad.json()["detail"]


def test_dismiss_needs_a_note_and_survives_rerun(client, h, deal):
    for name in ("problem_rent_roll.xlsx", "problem_t12.xlsx"):
        client.post(f"/api/screening/documents/{upload(client, h, deal['deal_id'], name)['document_id']}/approve", headers=h)
    flags = client.get(f"/api/screening/deals/{deal['deal_id']}/analysis", headers=h).json()["flags"]
    a5 = next(f for f in flags if f["rule_id"] == "A5")
    no_note = client.post(f"/api/screening/flags/{a5['flag_id']}/status", headers=h, json={"status": "dismissed"})
    assert no_note.status_code == 400
    ok = client.post(f"/api/screening/flags/{a5['flag_id']}/status", headers=h,
                     json={"status": "dismissed", "note": "Seller provided a management agreement at 4%."})
    assert ok.status_code == 200
    again = client.get(f"/api/screening/deals/{deal['deal_id']}/analysis", headers=h).json()["flags"]
    a5b = next(f for f in again if f["rule_id"] == "A5")
    assert a5b["status"] == "dismissed" and "4%" in a5b["dismissal_note"]


def test_bad_upload_is_recorded_with_a_friendly_error(client, h, deal):
    doc = upload(client, h, deal["deal_id"], "password_protected.pdf")
    assert doc["status"] == "failed" and "password-protected" in doc["error"] and doc["items"] == []


def test_delete_upload_removes_file_and_data(client, h, deal):
    doc = upload(client, h, deal["deal_id"], "problem_rent_roll.pdf")
    with psycopg.connect(get_settings().dsn("piq_readonly")) as c:
        stored = c.execute("SELECT stored_name FROM screening_documents WHERE document_id = %s",
                           (doc["document_id"],)).fetchone()[0]
    assert (UPLOAD_DIR / stored).exists()
    assert client.delete(f"/api/screening/documents/{doc['document_id']}", headers=h).status_code == 200
    assert not (UPLOAD_DIR / stored).exists()
    with psycopg.connect(get_settings().dsn("piq_readonly")) as c:
        assert c.execute("SELECT count(*) FROM screening_extracted_items WHERE document_id = %s",
                         (doc["document_id"],)).fetchone()[0] == 0


def test_no_tenant_data_anywhere_in_the_database(client, h, deal):
    for name in ("problem_rent_roll.xlsx", "problem_rent_roll.pdf", "problem_rent_roll_scanned.pdf"):
        doc = upload(client, h, deal["deal_id"], name)
        client.post(f"/api/screening/documents/{doc['document_id']}/approve", headers=h)
    client.get(f"/api/screening/deals/{deal['deal_id']}/analysis", headers=h)
    with psycopg.connect(get_settings().dsn("piq_readonly")) as c:
        dump = " ".join(str(r) for t in ("screening_documents", "screening_extracted_items", "screening_rent_roll_units",
                                         "screening_flags")
                        for r in c.execute(f"SELECT * FROM {t} WHERE " + (
                            "deal_id = %s" if t != "screening_extracted_items" else
                            "document_id IN (SELECT document_id FROM screening_documents WHERE deal_id = %s)"),
                            (deal["deal_id"],)).fetchall())
    for who in F.TENANTS + F.PHONES:
        assert who not in dump
    for first in (t.split()[0] for t in F.TENANTS):
        assert first not in dump, first


def test_size_limit_and_sign_in(client, h, deal):
    big = client.post(f"/api/screening/deals/{deal['deal_id']}/documents", headers=h,
                      files={"file": ("big.csv", b"x," * (6 * 1024 * 1024))})
    assert big.status_code == 413
    assert client.get("/api/screening/deals").status_code == 401
    assert client.get("/api/screening/deals/999999", headers=h).status_code == 404
