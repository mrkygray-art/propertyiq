"""Milestones 5-7: settings (benchmarks, rules, life table), market comps, California red flags."""
import os
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import psycopg
import pytest

import screening_fixture_data as F
from propertyiq.config import get_settings

pytestmark = pytest.mark.db

try:
    with psycopg.connect(get_settings().dsn("piq_readonly")) as _c:
        _c.execute("SELECT 1 FROM screening_rules LIMIT 1")
except Exception as exc:  # pragma: no cover
    pytest.skip(f"Tier 2 rules not set up: {exc}", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from propertyiq import main  # noqa: E402
from propertyiq.screening import ca_flags, market as mk, settings_store  # noqa: E402

FX = Path(__file__).parent / "fixtures" / "screening"
AS_OF = date(2026, 10, 11)
RULES = settings_store.list_rules()
LIFE = settings_store.list_condition_life()
PROBLEM_FACTS = {**F.DEAL_FACTS, "property_type": "multifamily"}
T12_TAX = D("1791.67") * 12   # problem T-12 tax line, annualized ($21,500.04)


def flags(facts, t12=None, noi=D(450000), condition=(), repairs_low=D(600)):
    return {f.fingerprint: f for f in ca_flags.evaluate(facts, RULES, t12, noi, list(condition), LIFE, repairs_low,
                                                       D("0.012"), AS_OF)}


# ---------------------------------------------------------------- R1 Prop 13

def test_r1_fixture_gap_matches_spec():
    f = flags(PROBLEM_FACTS, {"real_estate_taxes": T12_TAX})["R1:prop13"]
    ev = {e["label"]: e["value"] for e in f.evidence}
    assert ev["Estimated tax after purchase"] == "$72,000"
    assert ev["T-12 property tax (annualized)"] == "$21,500"
    assert ev["Gap (tax increase)"] == "$50,500"
    assert f.severity == "high" and "May apply" in f.explanation and "TAX_REASSESSMENT" in f.suggested_action
    assert "boe.ca.gov" in f.sources[0] and "verified 2026-10-11" in f.sources[0]


def test_r1_not_for_refinance_and_quiet_when_taxes_already_reset():
    assert "R1:prop13" not in flags({**PROBLEM_FACTS, "transaction": "refinance"}, {"real_estate_taxes": T12_TAX})
    assert "R1:prop13" not in flags(PROBLEM_FACTS, {"real_estate_taxes": D(72000)})


# ---------------------------------------------------------------- R2 rent control

def test_r2_la_rso_for_the_fixture():
    f = flags(PROBLEM_FACTS)
    assert "R2:la_rso" in f and "R2:ca_ab1482" not in f   # local rent control takes precedence
    assert "not a legal determination" in f["R2:la_rso"].explanation
    assert "RENT_CONTROL" in f["R2:la_rso"].suggested_action


def test_r2_other_cities_and_ages():
    sm_new = flags({**PROBLEM_FACTS, "city": "Santa Monica", "year_built": 1985})
    assert "R2:santa_monica_rent_control" not in sm_new and "R2:ca_ab1482" in sm_new
    assert "R2:santa_monica_rent_control" in flags({**PROBLEM_FACTS, "city": "Santa Monica", "year_built": 1970})
    torrance = flags({**PROBLEM_FACTS, "city": "Torrance"})
    assert {"R2:local_rent_rules", "R2:ca_ab1482"} <= set(torrance)
    assert "R2:ca_ab1482" not in flags({**PROBLEM_FACTS, "city": "Torrance", "year_built": 2020})


# ---------------------------------------------------------------- R3 seismic

def test_r3_soft_story_for_the_fixture():
    f = flags(PROBLEM_FACTS)["R3:la_soft_story"]
    assert f.severity == "high" and "dbs.lacity.gov" in f.sources[0]


def test_r3_variants():
    assert "R3:soft_story_indicator" in flags({**PROBLEM_FACTS, "city": "Torrance"})
    small = flags({**PROBLEM_FACTS, "units": 3})
    assert "R3:la_soft_story" not in small and "R3:soft_story_indicator" in small
    concrete = flags({**PROBLEM_FACTS, "construction": "concrete", "parking": "surface"})["R3:la_non_ductile_concrete"]
    assert concrete.sources[0].startswith("UNVERIFIED")
    assert flags({**PROBLEM_FACTS, "construction": "masonry"})["R3:unreinforced_masonry"].sources[0].startswith("UNVERIFIED")
    assert "R3:earthquake_zone_lookup" in flags(PROBLEM_FACTS)


# ---------------------------------------------------------------- R4 environmental

def test_r4_age_based_and_site_history():
    f = flags(PROBLEM_FACTS)
    assert {"R4:lead_paint", "R4:asbestos", "R4:hazard_lookups"} <= set(f)
    newer = flags({**PROBLEM_FACTS, "year_built": 1985})
    assert "R4:lead_paint" not in newer and "R4:asbestos" not in newer
    hist = flags({**PROBLEM_FACTS, "site_history": {"dry_cleaner": True}})["R4:site_history"]
    assert hist.severity == "high" and "Phase I" in hist.suggested_action
    links = [e["value"] for e in f["R4:hazard_lookups"].evidence]
    assert any("geotracker" in v for v in links) and any("envirostor" in v for v in links)
    assert any("ladbs" in v or "dbs.lacity" in v for v in links)   # LA methane lookup


# ---------------------------------------------------------------- R5 deferred maintenance

def test_r5_checklist_and_cross_check():
    cond = [{"item_key": "roof_shingle", "details": {"age_years": 26}, "estimated_cost": 85000},
            {"item_key": "electrical_panel", "details": {"brand": "Zinsco", "age_years": 50}, "estimated_cost": 40000},
            {"item_key": "water_heaters", "details": {"age_years": 4}, "estimated_cost": None}]
    f = flags(PROBLEM_FACTS, {"real_estate_taxes": T12_TAX, "repairs_maintenance": D(6000)}, condition=cond)
    assert "R5:roof_shingle" in f and f["R5:electrical_panel"].severity == "high"
    assert "R5:water_heaters" not in f
    assert {"label": "Total of your estimates", "value": "$125,000"} in f["R5:capex_total"].evidence
    assert "DEFERRED_MAINTENANCE" in f["R5:capex_total"].suggested_action
    assert "R5:low_repairs_old_building" in f   # $300/unit on a 64-year-old building


def test_every_r_flag_has_a_source_and_cautious_language():
    for f in flags({**PROBLEM_FACTS, "site_history": {"gas_station": True}}, {"real_estate_taxes": T12_TAX}).values():
        assert f.sources and f.suggested_action
        assert not any(w in f.explanation.lower() for w in ("is covered", "must comply", "is subject to"))


# ---------------------------------------------------------------- market comps

def test_synthetic_comps_are_deterministic_and_labeled():
    q = mk.CompQuery("rent", "123 Juniper Ct", "Los Angeles", 1.0, 12, ["1BR/1BA", "2BR/1BA"])
    a, b = mk.SyntheticProvider().fetch(q), mk.SyntheticProvider().fetch(q)
    assert a.comps == b.comps and a.synthetic and "not real" in a.provider_label
    assert all(c["source_id"].startswith("SYN-R-") and c["distance_miles"] <= 1.0 for c in a.comps)


def test_no_comps_for_uncovered_area():
    r = mk.SyntheticProvider().fetch(mk.CompQuery("sale", "1 Main St", "Fresno"))
    assert r.comps == [] and "No comps available" in r.notes[0]


def test_rent_summary_matches_independent_median():
    comps = [{"unit_type": "1BR", "rent": r} for r in (2000, 2100, 2300, 1900)]
    s = mk.rent_summary(comps, {"1BR": {"avg_rent": 1980.0, "avg_market": 2100.0}})[0]
    assert (s["n"], s["median"], s["low"], s["high"]) == (4, 2050.0, 1900, 2300)
    assert s["subject_in_place_vs_median_pct"] == pytest.approx((1980 - 2050) / 2050)


def test_sale_summary_reports_cap_rates_only_from_source():
    comps = [{"price_per_unit": 300000, "price_per_sqft": 400, "sale_date": "2026-01-01", "cap_rate": 0.05},
             {"price_per_unit": 320000, "price_per_sqft": 420, "sale_date": "2026-03-01", "cap_rate": None}]
    s = mk.sale_summary(comps)
    assert s["cap_rate"]["n"] == 1 and s["cap_rate"]["note"] == "reported by source for 1 of 2 sales"
    assert s["date_range"] == ["2026-01-01", "2026-03-01"]


def test_m1_flags():
    s = mk.sale_summary([{"price_per_unit": 240000, "price_per_sqft": 300, "sale_date": "2026-01-01", "cap_rate": 0.055}] * 3)
    fl = {f.fingerprint for f in mk.m1_flags(s, D(6000000), 20, D(260000), D("0.20"), D("0.01"), "test", "2026-10-11")}
    assert fl == {"M1:price_per_unit", "M1:cap_rate"}   # $300k/unit is 25% over $240k; 4.33% cap vs 5.5%
    assert mk.m1_flags(s, D(4800000), 20, D(300000), D("0.20"), D("0.01"), "t", "2026-10-11") == []


def test_m1_boundaries_are_strict():
    # Exactly 20% over the median and exactly 1 point below the median cap: not flagged ("more than").
    s = mk.sale_summary([{"price_per_unit": 250000, "price_per_sqft": 300, "sale_date": "2026-01-01", "cap_rate": 0.055}] * 3)
    assert mk.m1_flags(s, D(6000000), 20, D(270000), D("0.20"), D("0.01"), "t", "2026-10-11") == []


# ---------------------------------------------------------------- API: settings, market, California

@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(scope="module")
def h(client):
    r = client.post("/api/auth/login", json={"username": "analyst", "password": os.environ["PIQ_DEMO_PASSWORD"]})
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def deal(client, h):
    d = client.post("/api/screening/deals", headers=h, json={
        "name": "pytest M5-M7", "property_facts": {**PROBLEM_FACTS}, "borrower_inputs": {"property_type": "multifamily"}}).json()
    for name in ("problem_rent_roll.xlsx", "problem_t12.xlsx"):
        doc = client.post(f"/api/screening/deals/{d['deal_id']}/documents", headers=h,
                          files={"file": (name, (FX / name).read_bytes())}).json()
        client.post(f"/api/screening/documents/{doc['document_id']}/approve", headers=h)
    yield d
    client.delete(f"/api/screening/deals/{d['deal_id']}", headers=h)


def test_rule_cannot_be_verified_without_source(client, h):
    rule = next(r for r in client.get("/api/screening/rules", headers=h).json() if r["rule_key"] == "flood_lookup")
    bad = client.put("/api/screening/rules/flood_lookup", headers=h,
                     json={**rule, "source_url": "", "verified": True, "last_verified": None})
    assert bad.status_code == 400 and "source URL" in bad.json()["detail"]


def test_benchmark_validation_and_effect_on_flags(client, h, deal):
    rows = client.get("/api/screening/benchmarks", headers=h).json()
    ins = next(b for b in rows if b["property_type"] == "multifamily" and b["category"] == "insurance")
    body = {k: ins[k] for k in ("property_type", "category", "basis", "low", "typical", "high", "source", "last_reviewed", "is_example")}
    assert client.put(f"/api/screening/benchmarks/{ins['benchmark_id']}", headers=h,
                      json={**body, "low": 900, "typical": 700}).status_code == 400
    assert client.put(f"/api/screening/benchmarks/{ins['benchmark_id']}", headers=h, json={**body, "source": ""}).status_code == 422
    try:
        client.put(f"/api/screening/benchmarks/{ins['benchmark_id']}", headers=h, json={**body, "low": 150})
        fps = {f["fingerprint"] for f in client.get(f"/api/screening/deals/{deal['deal_id']}/analysis", headers=h).json()["flags"]}
        assert "A4:insurance" not in fps   # $200/unit is now above the lender's $150 low end
    finally:
        client.put(f"/api/screening/benchmarks/{ins['benchmark_id']}", headers=h, json=body)


def test_market_endpoint_caches_and_flags(client, h, deal):
    url = f"/api/screening/deals/{deal['deal_id']}/market"
    q = {"kind": "sale", "address": "100 Juniper Ct", "city": "Los Angeles", "radius_miles": 1, "max_age_months": 12}
    first = client.post(url, headers=h, json={**q, "refresh": True}).json()
    second = client.post(url, headers=h, json=q).json()
    assert first["result"]["cached"] is False and second["result"]["cached"] is True
    assert first["result"]["comps"] == second["result"]["comps"] and first["result"]["synthetic"]
    assert first["implied"]["price_per_unit"] == 300000.0
    rent = client.post(url, headers=h, json={**q, "kind": "rent"}).json()
    types = {r["unit_type"] for r in rent["summary"]}
    assert types == {"1BR/1BA", "2BR/1BA"} and all(r["n"] >= 4 for r in rent["summary"])
    none = client.post(url, headers=h, json={**q, "city": "Fresno"}).json()
    assert none["result"]["comps"] == [] and "No comps available" in none["result"]["notes"][0]


def test_california_endpoint_fixture(client, h, deal):
    r = client.put(f"/api/screening/deals/{deal['deal_id']}/california", headers=h, json={
        "facts": {"transaction": "purchase", "purchase_price": 6000000},
        "condition": [{"item_key": "roof_shingle", "details": {"age_years": 26}, "estimated_cost": 85000}]})
    fps = {f["fingerprint"]: f for f in r.json()["flags"]}
    assert {"R1:prop13", "R2:la_rso", "R3:la_soft_story", "R4:lead_paint", "R5:roof_shingle", "R5:capex_total"} <= set(fps)
    gap = next(e["value"] for e in fps["R1:prop13"]["evidence"] if e["label"] == "Gap (tax increase)")
    assert gap == "$50,500"
    again = client.get(f"/api/screening/deals/{deal['deal_id']}/california", headers=h).json()
    assert again["condition"][0]["item_key"] == "roof_shingle" and again["facts"]["purchase_price"] == 6000000
