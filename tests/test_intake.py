"""Tier 2 document intake: extraction parity across formats, failure paths, privacy, math, anomaly rules.

Expected values are computed from the raw fixture lists in screening_fixture_data.py with plain
arithmetic, not with the module's own functions.
"""
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

import screening_fixture_data as F
from propertyiq.llm import LLMError, LLMResult
from propertyiq.screening.intake import anomalies, calcs, mapping
from propertyiq.screening.intake.extract import extract
from propertyiq.screening.intake.schema import match_category

FX = Path(__file__).parent / "fixtures" / "screening"
AS_OF = date(2026, 9, 30)
FIELDS = ("unit_id", "unit_type", "beds", "baths", "sqft", "status", "current_rent", "market_rent",
          "lease_start", "lease_end", "month_to_month", "concessions", "deposit")


def load(name, provider=None, kind=None):
    return extract(name, (FX / name).read_bytes(), kind, provider)


def unit_tuples(result):
    return [tuple(getattr(u, f) for f in FIELDS) for u in result.rent_roll.units]


def line_tuples(result):
    return [(ln.original_label, ln.category, {k: v for k, v in ln.months.items()}, ln.reported_total)
            for ln in result.t12.lines + result.t12.subtotals]


class FakeAI:
    def __init__(self, reply="{}", fail=False):
        self.reply, self.fail, self.prompts = reply, fail, []

    def chat(self, system, user, **_):
        self.prompts.append(system + "\n" + user)
        if self.fail:
            raise LLMError("down")
        return LLMResult(self.reply, "fake", None, None, None)


# ---------------------------------------------------------------- extraction parity

def test_rent_roll_excel_and_text_pdf_match_exactly():
    assert unit_tuples(load("problem_rent_roll.xlsx")) == unit_tuples(load("problem_rent_roll.pdf"))


def test_rent_roll_excel_and_csv_match_exactly():
    assert unit_tuples(load("clean_rent_roll.xlsx")) == unit_tuples(load("clean_rent_roll.csv"))


def test_t12_excel_and_text_pdf_match_exactly():
    a, b = load("problem_t12.xlsx"), load("problem_t12.pdf")
    assert line_tuples(a) == line_tuples(b)
    assert a.t12.months == b.t12.months and a.t12.missing_months == b.t12.missing_months == ["2026-06"]


def test_t12_excel_and_csv_match_exactly():
    assert line_tuples(load("clean_t12.xlsx")) == line_tuples(load("clean_t12.csv"))


def test_rent_roll_matches_fixture_data():
    units = load("problem_rent_roll.xlsx").rent_roll.units
    assert len(units) == 20
    for u, src in zip(units, F.PROBLEM_UNITS):
        assert u.unit_id == src["unit"] and u.status == src["status"].lower()
        assert u.current_rent == (D(src["rent"]) if src["rent"] else None)
        assert u.market_rent == D(src["market"]) and u.sqft == D(src["sqft"])
        assert u.month_to_month == (src["status"] == "Occupied" and src["end"] is None)


def test_scanned_pdf_ocr_is_close_and_flags_what_it_isnt_sure_of():
    ocr = load("problem_rent_roll_scanned.pdf")
    assert ocr.method == "pdf_ocr"
    truth = {u[0]: u for u in unit_tuples(load("problem_rent_roll.xlsx"))}
    got = ocr.rent_roll.units
    assert [u.unit_id for u in got] == list(truth)
    exact = 0
    for u in got:
        key = (u.status, u.current_rent, u.market_rent, u.sqft)
        if key == (truth[u.unit_id][5], truth[u.unit_id][6], truth[u.unit_id][7], truth[u.unit_id][4]):
            exact += 1
        else:
            assert u.confidence == "low", f"unit {u.unit_id} differs but isn't flagged for review"
    assert exact >= 18
    assert all(u.confidence in ("medium", "low") for u in got)   # OCR is never "high"


# ---------------------------------------------------------------- failure paths

@pytest.mark.parametrize("name,message", [
    ("bad_not_really_excel.xlsx", "contents aren't a valid XLSX"),
    ("empty_workbook.xlsx", "Couldn't find a rent roll or a T-12"),
    ("password_protected.pdf", "password-protected"),
])
def test_bad_files_fail_with_friendly_messages(name, message):
    r = load(name)
    assert r.kind is None and message in r.error
    assert "enter the figures manually" in r.error or "password" in r.error or "valid" in r.error


def test_empty_huge_and_unsupported_files():
    assert "empty" in extract("x.xlsx", b"").error
    assert "limit is 10 MB" in extract("big.csv", b"a," * (6 * 1024 * 1024)).error
    assert "Unsupported file type" in extract("photo.jpg", b"\xff\xd8\xff\xe0" + b"0" * 100).error


def test_wrong_kind_requested_says_so():
    r = load("clean_t12.xlsx", kind="rent_roll")
    assert r.kind is None and "Couldn't find a rent roll" in r.error


# ---------------------------------------------------------------- privacy

@pytest.mark.parametrize("name", ["problem_rent_roll.xlsx", "problem_rent_roll.pdf", "problem_rent_roll_scanned.pdf",
                                  "clean_rent_roll.csv"])
def test_tenant_names_and_phones_never_extracted(name):
    text = repr(load(name).rent_roll)
    for who in F.TENANTS + F.PHONES:
        assert who not in text
    for first in (t.split()[0] for t in F.TENANTS):
        assert first not in text, first


def test_ai_prompt_has_no_personal_data_and_no_amounts():
    ai = FakeAI('{"1": "contract_services"}')
    load("problem_t12.xlsx", provider=ai)
    prompt = ai.prompts[0]
    assert "Fire extinguisher inspection" in prompt
    assert not any(t in prompt for t in F.TENANTS + F.PHONES)
    assert "1,791" not in prompt and "46000" not in prompt and "18,000" not in prompt


def test_mask_removes_contact_details():
    assert mapping.mask("Refund to tenant: Jordan Smith 310-555-0199 jordan@example.com") == \
        "Refund to tenant: [name] [phone] [email]"


# ---------------------------------------------------------------- label mapping

def test_known_labels_map_without_ai():
    assert match_category("R&M") == ("repairs_maintenance", "high")
    assert match_category("RE Taxes") == ("real_estate_taxes", "high")
    assert match_category("Insurance claim settlement") == ("non_recurring_income", "high")
    assert match_category("Pest control svc")[0] == "contract_services"


def test_ai_suggestion_is_used_but_marked_low_confidence():
    r = load("problem_t12.xlsx", provider=FakeAI('{"1": "contract_services"}'))
    line = next(ln for ln in r.t12.lines if ln.original_label == "Fire extinguisher inspection")
    assert (line.category, line.mapped_by, line.confidence) == ("contract_services", "ai", "low")


@pytest.mark.parametrize("reply,fail", [('{"1": "made_up_category"}', False), ("not json", False), ("", True)])
def test_bad_or_missing_ai_answer_falls_back_to_section_default(reply, fail):
    r = load("problem_t12.xlsx", provider=FakeAI(reply, fail))
    line = next(ln for ln in r.t12.lines if ln.original_label == "Fire extinguisher inspection")
    assert (line.category, line.mapped_by, line.confidence) == ("other_expense", "section_default", "low")


# ---------------------------------------------------------------- reconciliation

def test_planted_total_error_is_reported_with_both_numbers():
    rec = load("problem_t12.xlsx").t12.reconciliation
    row = next(r for r in rec if r["kind"] == "row_total")
    assert row["label"] == "Total Operating Expenses"
    assert D(row["calculated"]) == D("74085.00") and D(row["reported"]) == D("75085")
    assert not [r for r in rec if r["kind"] == "subtotal_month"]   # every month reconciles


def test_clean_t12_reconciles():
    assert load("clean_t12.xlsx").t12.reconciliation == []


# ---------------------------------------------------------------- rent roll math (independent)

def _expected_rr(units):
    occ = [u for u in units if u["status"] == "Occupied"]
    in_place = sum(u["rent"] for u in occ)
    gpr = in_place + sum(u["market"] for u in units if u["status"] != "Occupied")
    ltl = sum(u["market"] - u["rent"] for u in occ)
    return len(occ) / len(units), in_place * 12, gpr * 12, ltl * 12


@pytest.mark.parametrize("name,units", [("problem_rent_roll.xlsx", F.PROBLEM_UNITS), ("clean_rent_roll.xlsx", F.CLEAN_UNITS)])
def test_rent_roll_metrics(name, units):
    m = calcs.rent_roll_metrics(load(name).rent_roll.units, AS_OF)
    occ, in_place, gpr, ltl = _expected_rr(units)
    assert float(m.physical_occupancy) == pytest.approx(occ)
    assert (m.in_place_annual, m.gross_potential_annual, m.loss_to_lease_annual) == (in_place, gpr, ltl)


def test_rent_roll_by_type_and_expirations():
    m = calcs.rent_roll_metrics(load("problem_rent_roll.xlsx").rent_roll.units, AS_OF)
    one = next(t for t in m.by_type if t.unit_type == "1BR/1BA")
    rents = [u["rent"] for u in F.PROBLEM_UNITS if u["type"] == "1BR/1BA" and u["rent"]]
    assert one.units == 12 and one.occupied == 11 and float(one.avg_rent) == pytest.approx(sum(rents) / len(rents))
    assert float(one.rent_per_sqft) == pytest.approx(sum(r / 650 for r in rents) / len(rents))
    occupied = [u for u in F.PROBLEM_UNITS if u["status"] == "Occupied"]
    assert sum(m.expirations.values()) == len(occupied)
    assert m.expirations["month_to_month"] == sum(1 for u in occupied if u["end"] is None)
    assert m.expirations["expired"] == sum(1 for u in occupied if u["end"] and u["end"] < AS_OF)


# ---------------------------------------------------------------- T-12 math (independent)

def _fixture_annual(spec, label, problem):
    months = F.months_for(problem)
    amount = next(a for lines in spec.values() for lbl, a in lines if lbl == label)
    return sum(D(str(F.monthly(amount, m))) for m in months) * 12 / len(months)


def test_t12_metrics_problem():
    r = load("problem_t12.xlsx")
    m = calcs.t12_metrics(r.t12.lines, r.t12.months, units=20)
    assert m.months_present == 11 and m.annualize == D(12) / 11
    gpr = _fixture_annual(F.PROBLEM_T12, "Gross Potential Rent", True)
    vac = _fixture_annual(F.PROBLEM_T12, "Vacancy Loss", True)
    assert m.gross_potential_rent == gpr and m.reported_vacancy_pct == abs(vac) / gpr == D("0.03")
    assert m.non_recurring_income == 18000
    taxes = _fixture_annual(F.PROBLEM_T12, "RE Taxes", True)
    assert abs(m.annual_by_category["real_estate_taxes"] - D(21500)) < 1 and m.annual_by_category["real_estate_taxes"] == taxes
    opex = sum(_fixture_annual(F.PROBLEM_T12, lbl, True) for lbl, _ in F.PROBLEM_T12["OPERATING EXPENSES"])
    assert abs(m.operating_expenses - opex) < D("0.01")
    assert abs(m.expense_per_unit - opex / 20) < D("0.01")


def test_t12_trailing_annualization():
    r = load("clean_t12.xlsx")
    m = calcs.t12_metrics(r.t12.lines, r.t12.months, units=20)
    monthly_income = sum(D(str(a)) for lbl, a in F.CLEAN_T12["INCOME"])
    # T-3 = last 3 months x 4; T-6 = last 6 months x 2. Flat months, so both equal 12 months.
    assert m.t3_income == (monthly_income * 3) * 4 and m.t6_income == (monthly_income * 6) * 2
    assert m.recurring_egi == monthly_income * 12


# ---------------------------------------------------------------- anomaly rules on the fixtures

def run_checks(rr_name, t12_name, facts=None, units_override=None, lines_override=None):
    rr = load(rr_name).rent_roll
    tx = load(t12_name).t12
    units = units_override if units_override is not None else rr.units
    lines = lines_override if lines_override is not None else tx.lines
    rm = calcs.rent_roll_metrics(units, AS_OF)
    tm = calcs.t12_metrics(lines, tx.months, units=20)
    info = {"missing_months": tx.missing_months, "duplicate_months": tx.duplicate_months,
            "reconciliation": tx.reconciliation, "source": t12_name}
    return anomalies.check(units, rm, rr_name, lines, tm, info, {"units": 20, "property_type": "multifamily", **(facts or {})})


def test_problem_fixture_raises_each_planted_flag():
    flags = {f.fingerprint: f for f in run_checks("problem_rent_roll.xlsx", "problem_t12.xlsx")}
    a1 = flags["A1:vacancy"]
    assert a1.severity == "high" and {"value": "10.0%", "label": "Rent roll physical vacancy"} in a1.evidence
    assert {"label": "T-12 reported vacancy", "value": "3.0%"} in a1.evidence
    a3 = flags["A3:label:Insurance claim settlement"]
    assert {"label": "Insurance claim settlement", "value": "$18,000"} in a3.evidence
    assert "A5:management_fee" in flags
    assert {"label": "Per unit", "value": "$200 per unit"} in flags["A4:insurance"].evidence
    assert {"label": "Per unit", "value": "$300 per unit"} in flags["A4:repairs_maintenance"].evidence
    a12 = flags["A12:integrity"]
    assert {"label": "Missing T-12 months", "value": "2026-06"} in a12.evidence
    assert any("$74,085.00" in e["value"] and "$75,085.00" in e["value"] for e in a12.evidence)
    for f in flags.values():
        assert f.sources and f.suggested_action and f.explanation


def test_clean_fixture_raises_no_flags():
    flags = run_checks("clean_rent_roll.xlsx", "clean_t12.xlsx")
    assert [f for f in flags if f.severity == "high"] == []
    assert flags == []


def _clean_lines():
    return load("clean_t12.xlsx").t12.lines


def _with_line(lines, label, months_fn):
    return [replace(ln, months={m: months_fn(m, v) for m, v in ln.months.items()}) if ln.original_label == label else ln
            for ln in lines]


def test_a3_spike_in_an_ordinary_income_line():
    lines = _with_line(_clean_lines(), "Other Income", lambda m, v: v + 9000 if m == "2026-02" else v)
    f = {x.fingerprint: x for x in run_checks("clean_rent_roll.xlsx", "clean_t12.xlsx", lines_override=lines)}
    spike = f["A3:spike:Other Income:2026-02"]
    assert {"label": "Excess", "value": "$9,000"} in spike.evidence


def test_a6_missing_insurance():
    lines = [ln for ln in _clean_lines() if ln.category != "insurance"]
    assert "A6:insurance" in {x.fingerprint for x in run_checks("clean_rent_roll.xlsx", "clean_t12.xlsx", lines_override=lines)}


def test_a7_large_one_off_repair():
    lines = _with_line(_clean_lines(), "R&M", lambda m, v: v + 24000 if m == "2026-04" else v)
    assert "A7:spike:R&M:2026-04" in {x.fingerprint for x in run_checks("clean_rent_roll.xlsx", "clean_t12.xlsx",
                                                                       lines_override=lines)}


def test_a8_a9_recent_trends():
    lines = _with_line(_clean_lines(), "Gross Potential Rent", lambda m, v: v - 6000 if m >= "2026-07" else v)
    lines = _with_line(lines, "Water & Sewer", lambda m, v: v + 4000 if m >= "2026-07" else v)
    fps = {x.fingerprint for x in run_checks("clean_rent_roll.xlsx", "clean_t12.xlsx", lines_override=lines)}
    assert "A9:T-3" in fps and "A8:T-3" in fps


def test_a10_rising_bad_debt():
    lines = _with_line(_clean_lines(), "Bad Debt", lambda m, v: v - 1500 if m >= "2026-07" else v)
    assert "A10:bad_debt" in {x.fingerprint for x in run_checks("clean_rent_roll.xlsx", "clean_t12.xlsx", lines_override=lines)}


def test_a11_a12_a13_unit_level_checks():
    units = load("clean_rent_roll.xlsx").rent_roll.units
    units = [replace(u, current_rent=D(3400)) if u.unit_id == "102" else u for u in units]   # outlier
    units = [replace(u, current_rent=D(0)) if u.unit_id == "103" else u for u in units]      # occupied, no rent
    units = units + [replace(units[0])]                                                      # duplicate unit 101
    fps = {x.fingerprint: x for x in run_checks("clean_rent_roll.xlsx", "clean_t12.xlsx", units_override=units)}
    assert "A11:unit:102" in fps and "A13:unit:103" in fps
    ev = fps["A12:integrity"].evidence
    assert {"label": "Duplicate unit numbers", "value": "101"} in ev
    assert {"label": "Units on rent roll vs deal form", "value": "21 vs 20"} in ev


def test_rent_roll_totals_row_check():
    rr = load("problem_rent_roll.xlsx").rent_roll
    assert anomalies.rent_roll_totals_check(rr.units, rr.reported_totals) is None
    assert "Totals row shows 40,000" in anomalies.rent_roll_totals_check(rr.units, {"current_rent": D(40000)})
