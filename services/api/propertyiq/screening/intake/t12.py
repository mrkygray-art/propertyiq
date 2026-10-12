"""T-12 extraction: monthly operating statement lines mapped to standard categories.

Also checks the document's own arithmetic ("reconciliation"): each line's months against its
Total column, and the reported subtotals (Total Income, Total Operating Expenses, NOI) against
the lines above them. Any difference is reported with both numbers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from propertyiq.screening.intake.schema import CATEGORY_SECTION, SourceRef, T12Line, match_category
from propertyiq.screening.intake.tables import (
    OCR_LOW_CONFIDENCE, RawTable, cell_text, parse_date, parse_number,
)

TOLERANCE = Decimal("1.00")   # dollars of rounding allowed in reconciliation
MONTH_NAMES = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
SUBTOTALS = ("total_income", "total_expenses", "net_operating_income")


@dataclass
class T12Extraction:
    lines: list[T12Line]
    subtotals: list[T12Line]
    months: list[str]                       # month keys in document order
    table_name: str
    method: str
    missing_months: list[str] = field(default_factory=list)
    duplicate_months: list[str] = field(default_factory=list)
    reconciliation: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_month(v) -> str | None:
    """'Oct-25', 'Oct 2025', 'October 2025', '10/2025', '2025-10', '10/31/2025' or a date -> '2025-10'."""
    if isinstance(v, (date, datetime)):
        return f"{v.year:04d}-{v.month:02d}"
    s = cell_text(v).strip().lower().replace(".", "")
    if not s:
        return None
    m = re.fullmatch(r"([a-z]{3})[a-z]*[\s\-/']*(\d{2}|\d{4})", s)
    if m and m.group(1) in MONTH_NAMES:
        y = int(m.group(2))
        return f"{y + 2000 if y < 100 else y:04d}-{MONTH_NAMES[m.group(1)]:02d}"
    m = re.fullmatch(r"(\d{1,2})[/\-](\d{4})", s) or None
    if m and 1 <= int(m.group(1)) <= 12:
        return f"{int(m.group(2)):04d}-{int(m.group(1)):02d}"
    m = re.fullmatch(r"(\d{4})[/\-](\d{1,2})", s)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    d = parse_date(s)
    return f"{d.year:04d}-{d.month:02d}" if d else None


def is_header(texts: list[str]) -> bool:
    return sum(1 for t in texts if parse_month(t)) >= 6


def find_table(tables: list[RawTable]) -> tuple[RawTable, int] | None:
    for t in tables:
        for i, row in enumerate(t.rows[:40]):
            if sum(1 for c in row.cells if parse_month(c.value)) >= 6:
                return t, i
    return None


def _add_month(key: str, n: int) -> str:
    y, m = int(key[:4]), int(key[5:])
    idx = y * 12 + m - 1 + n
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def parse(tables: list[RawTable], filename: str) -> T12Extraction | None:
    found = find_table(tables)
    if found is None:
        return None
    table, h = found
    header = table.rows[h].cells
    month_cols: list[tuple[int, str]] = [(i, m) for i, c in enumerate(header) if (m := parse_month(c.value))]
    first_month_col = month_cols[0][0]
    total_col = next((i for i, c in enumerate(header) if i > month_cols[-1][0]
                      and re.search(r"total|ttm|t-?12|annual|ytd", cell_text(c.value), re.I)), None)
    keys = [m for _, m in month_cols]
    result = T12Extraction([], [], [], table.name, table.method)
    seen: set[str] = set()
    for k in keys:
        if k in seen and k not in result.duplicate_months:
            result.duplicate_months.append(k)
        seen.add(k)
    result.months = sorted(seen)
    last = max(seen)
    expected = [_add_month(last, -i) for i in range(11, -1, -1)]
    result.missing_months = [m for m in expected if m not in seen]

    ocr = table.method == "pdf_ocr"
    section = ""
    for row in table.rows[h + 1:]:
        label = " ".join(cell_text(c.value) for c in row.cells[:first_month_col] if cell_text(c.value)).strip()
        values = {}
        confs = []
        for i, key in month_cols:
            c = row.cells[i] if i < len(row.cells) else None
            n = parse_number(c.value) if c else None
            if c and c.value not in (None, ""):
                confs.append(c.confidence)
            values[key] = (values.get(key) or Decimal(0)) + n if n is not None and key in values else n
        total = parse_number(row.cells[total_col].value) if total_col is not None and total_col < len(row.cells) else None
        if all(v is None for v in values.values()) and total is None:
            if label:
                section = label
            continue
        if not label:
            result.warnings.append(f"{table.name} {row.location}: numbers with no label were skipped. Check the source.")
            continue
        category, conf = match_category(label)
        mapped_by = "synonym" if conf == "high" else "fuzzy"
        if category is None:
            mapped_by, conf = "unmapped", "low"
            category = "unmapped"
        if ocr:
            conf = "low" if confs and min(confs) < OCR_LOW_CONFIDENCE else ("medium" if conf == "high" else conf)
        line = T12Line(label, category, values, total, SourceRef(filename, f"{table.name} {row.location}", table.method, label),
                       conf, mapped_by, section_hint=section)
        (result.subtotals if category in SUBTOTALS else result.lines).append(line)

    # reconcile() runs after unrecognized labels get a category (see extract.py).
    if not result.lines:
        result.warnings.append("A T-12 header was found, but no line items under it.")
    return result


def section_default(section_hint: str) -> str:
    s = section_hint.lower()
    if re.search(r"income|revenue", s):
        return "other_income"
    if re.search(r"operating\s+expense|expenses?$|opex", s):
        return "other_expense"
    if re.search(r"below|non.?operating|capital|debt", s):
        return "capital_expenditures"
    return "unmapped"


def reconcile(x: T12Extraction) -> None:
    """Check the document's own arithmetic. Replaces x.reconciliation with entries showing both numbers."""
    x.reconciliation = []
    def note(kind, label, ref, expected, reported, detail):
        x.reconciliation.append({"kind": kind, "label": label, "source": ref, "calculated": str(expected),
                                 "reported": str(reported), "difference": str(reported - expected), "detail": detail})

    for line in x.lines + x.subtotals:
        if line.reported_total is not None and abs(line.total() - line.reported_total) > TOLERANCE:
            note("row_total", line.original_label, line.source.text(), line.total(), line.reported_total,
                 f"The months add up to ${line.total():,.2f} but the Total column shows ${line.reported_total:,.2f}.")

    def section_of(ln: T12Line) -> str:
        s = CATEGORY_SECTION.get(ln.category, "unknown")
        return CATEGORY_SECTION.get(section_default(ln.section_hint), "unknown") if s == "unknown" else s

    def section_sum(section: str, month: str | None) -> Decimal:
        return sum(((ln.months.get(month) or 0) if month else ln.total()
                    for ln in x.lines if section_of(ln) == section), Decimal(0))

    for sub in x.subtotals:
        if sub.category == "total_income":
            calc = lambda m: section_sum("income", m)  # noqa: E731
            what = "income lines"
        elif sub.category == "total_expenses":
            calc = lambda m: section_sum("expense", m)  # noqa: E731
            what = "operating expense lines"
        else:
            calc = lambda m: section_sum("income", m) - section_sum("expense", m)  # noqa: E731
            what = "income minus operating expenses"
        reported = sub.reported_total if sub.reported_total is not None else sub.total()
        if abs(calc(None) - reported) > TOLERANCE:
            note("subtotal", sub.original_label, sub.source.text(), calc(None), reported,
                 f"'{sub.original_label}' shows ${reported:,.2f}, but the {what} add up to ${calc(None):,.2f}.")
        for month, val in sub.months.items():
            if val is not None and abs(calc(month) - val) > TOLERANCE:
                note("subtotal_month", f"{sub.original_label} ({month})", sub.source.text(), calc(month), val,
                     f"In {month}, '{sub.original_label}' shows ${val:,.2f}, but the {what} add up to ${calc(month):,.2f}.")
