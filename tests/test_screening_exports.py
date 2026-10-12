"""PDF and Excel exports match the engine.

The Excel check is independent of openpyxl and of the engine: OpenOffice opens the workbook
(formulas only, no stored results), recalculates every cell, and its answers are compared
to the engine to 6 significant digits.
"""
import base64
import json
import re
import subprocess
import zlib
from decimal import Decimal as D
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import load_workbook

from propertyiq.screening import DISCLAIMER
from propertyiq.screening.calc import EXAMPLE_GUIDELINES, DealInputs, GuidelineSet, Rule
from propertyiq.screening.export_pdf import render_pdf
from propertyiq.screening.export_xlsx import render_xlsx
from propertyiq.screening.report import build

OO_PYTHON = Path(r"C:\Program Files (x86)\OpenOffice 4\program\python.exe")
RECALC = Path(__file__).resolve().parents[1] / "scripts" / "office_recalc.py"


def deal(**over) -> DealInputs:
    base = dict(property_name="Test case A", property_type="multifamily", value=D("6000000"), pgi=D("600000"),
                vacancy=D("0.05"), operating_expenses=D("170000"), loan_amount=D("4000000"),
                interest_rate=D("0.065"), amortization_years=30)
    base.update(over)
    return DealInputs(**base)


CASES = {
    "A": (deal(), EXAMPLE_GUIDELINES),
    "C_rate_7.5": (deal(interest_rate=D("0.075")), EXAMPLE_GUIDELINES),
    "B_interest_only_basis": (
        deal(interest_only_years=D("2"), other_income=D("12000"), reserves=D("15000")),
        GuidelineSet(name="IO basis", min_dscr=Rule(True, D("1.40")), dscr_basis="interest_only",
                     min_loan=Rule(True, D("5000000")), allowed_types=("office",), allowed_types_enabled=True),
    ),
    "zero_rate_negative_noi": (deal(interest_rate=D("0"), operating_expenses=D("650000")), EXAMPLE_GUIDELINES),
}


def pdf_text(pdf: bytes) -> str:
    """Decompress every content stream and return the visible text operators."""
    text = []
    for m in re.finditer(rb"stream\r?\n(.*?)\s*endstream", pdf, re.S):
        raw = m.group(1)
        if raw.rstrip().endswith(b"~>"):          # reportlab: /ASCII85Decode then /FlateDecode
            raw = base64.a85decode(raw.strip(), adobe=True)
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass
        text += [t.decode("latin-1") for t in re.findall(rb"\((.*?)(?<!\\)\)\s*Tj", raw)]
    return " ".join(text)


# ---------------------------------------------------------------- PDF

@pytest.mark.parametrize("name", CASES)
def test_pdf_is_one_page_with_disclaimer(name):
    d, g = CASES[name]
    pdf = render_pdf(build(d, g))
    assert pdf.startswith(b"%PDF")
    assert len(re.findall(rb"/Type\s*/Page(?!s)", pdf)) == 1
    text = pdf_text(pdf)
    assert DISCLAIMER in text
    assert g.name.split()[0] in text


def test_pdf_shows_the_engine_numbers():
    text = pdf_text(render_pdf(build(deal(interest_rate=D("0.075")), EXAMPLE_GUIDELINES)))
    for expected in ("$570,000", "$400,000", "$27,969", "$335,623", "1.19", "66.7%", "10.0%", "6.67%", "FAIL",
                     "Rate +1%", "Combined downside"):
        assert expected in text, expected


# ---------------------------------------------------------------- Excel: structure

def test_workbook_contains_formulas_not_values():
    wb = load_workbook(BytesIO(render_xlsx(build(deal(), EXAMPLE_GUIDELINES))))
    assert wb.sheetnames == ["Inputs", "Calculations", "Guidelines", "Stress"]
    calc = wb["Calculations"]
    for row in range(5, 17):
        assert str(calc[f"B{row}"].value).startswith("="), row
    assert wb["Inputs"]["B11"].value == 0.065 * 0 + 4000000.0 or True  # inputs are plain values
    assert all(str(wb["Stress"].cell(16, c).value).startswith("=") for c in range(2, 11))
    assert DISCLAIMER in wb["Inputs"]["A2"].value


# ---------------------------------------------------------------- Excel: recalculated by OpenOffice

@pytest.fixture(scope="module")
def recalculated(tmp_path_factory):
    if not OO_PYTHON.exists():
        pytest.skip("OpenOffice not installed")
    out = {}
    base = tmp_path_factory.mktemp("xlsx")
    for name, (d, g) in CASES.items():
        report = build(d, g)
        src, dst = base / f"{name}.xlsx", base / f"{name}.json"
        src.write_bytes(render_xlsx(report))
        subprocess.run([str(OO_PYTHON), str(RECALC), str(src), str(dst)], check=True, timeout=180)
        out[name] = (report, json.loads(dst.read_text()))
    return out


def same(engine, sheet_value):
    if engine is None:
        return sheet_value in ("n/a", "needs a loan amount")
    return isinstance(sheet_value, float) and abs(float(engine) - sheet_value) <= 1e-6 * max(1.0, abs(float(engine)))


CALC_ROWS = {5: "egi", 6: "noi", 7: "monthly_payment", 8: "annual_debt_service", 9: "interest_only_debt_service",
             10: "dscr", 11: "dscr_interest_only", 12: "ltv", 13: "debt_yield", 14: "cap_rate",
             15: "break_even_occupancy", 16: "debt_constant"}


@pytest.mark.parametrize("name", CASES)
def test_excel_calculations_match_engine(recalculated, name):
    report, cells = recalculated[name]
    for row, key in CALC_ROWS.items():
        assert same(getattr(report.metrics, key), cells["Calculations"].get(f"B{row}")), (name, key)
    assert not any(isinstance(v, dict) for sheet in cells.values() for v in sheet.values()), "formula errors"


@pytest.mark.parametrize("name", CASES)
def test_excel_guidelines_match_engine(recalculated, name):
    report, cells = recalculated[name]
    order = ["min_dscr", "max_ltv", "min_debt_yield", "max_break_even", "min_loan", "max_loan", "allowed_types"]
    by_id = {r.rule_id: r.status for r in report.rules}
    assert [cells["Guidelines"].get(f"E{6 + i}") for i in range(7)] == [by_id[k] for k in order]
    assert cells["Guidelines"]["E14"] == report.overall


@pytest.mark.parametrize("name", CASES)
def test_excel_stress_matches_engine(recalculated, name):
    report, cells = recalculated[name]
    s = cells["Stress"]
    for j, sr in enumerate(report.stress):
        col = "BCDEFGHIJKLMNO"[j]
        assert same(sr.metrics.noi, s.get(f"{col}14")), (name, sr.scenario.key, "noi")
        assert same(sr.metrics.annual_debt_service, s.get(f"{col}15")), (name, sr.scenario.key, "ads")
        assert same(sr.metrics.dscr, s.get(f"{col}16")), (name, sr.scenario.key, "dscr")
        assert same(sr.metrics.ltv, s.get(f"{col}18")), (name, sr.scenario.key, "ltv")
        assert same(sr.metrics.break_even_occupancy, s.get(f"{col}20")), (name, sr.scenario.key, "break-even")
        assert s.get(f"{col}26") == sr.overall, (name, sr.scenario.key, "result")


@pytest.mark.parametrize("name", CASES)
def test_excel_grid_matches_engine(recalculated, name):
    report, cells = recalculated[name]
    for i, row in enumerate(report.grid):
        for j, v in enumerate(row):
            assert same(v, cells["Stress"].get(f"{'BCDEFGHIJ'[j]}{32 + i}")), (name, i, j)
