"""Rent roll extraction (multifamily): one record per unit, with tenant details removed.

Tenant name and contact columns are recognized only so they can be dropped. Their text is also
scrubbed from every other cell in the same row (OCR can spill a name into a neighbouring column),
so it never reaches the database, the screen, an export, or the AI.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from propertyiq.screening.intake.schema import (
    PRIVATE_FIELDS, RentRollUnit, SourceRef, match_column, match_status, normalize_label,
)
from propertyiq.screening.intake.tables import OCR_LOW_CONFIDENCE, RawRow, RawTable, cell_text, parse_date, parse_number


@dataclass
class RentRollExtraction:
    units: list[RentRollUnit]
    table_name: str
    method: str
    warnings: list[str] = field(default_factory=list)
    reported_totals: dict[str, Decimal] = field(default_factory=dict)   # from a "Totals" row, if any
    columns_found: list[str] = field(default_factory=list)


def is_header(texts: list[str]) -> bool:
    fields = {match_column(t) for t in texts if t}
    fields.discard(None)
    return len(fields) >= 4 and "unit_id" in fields


def _header_index(table: RawTable) -> int | None:
    for i, row in enumerate(table.rows[:40]):
        if is_header(row.texts()):
            return i
    return None


def find_table(tables: list[RawTable]) -> tuple[RawTable, int] | None:
    for t in tables:
        idx = _header_index(t)
        if idx is not None:
            return t, idx
    return None


def _scrub(text: str, private_bits: list[str]) -> str:
    for bit in private_bits:
        for token in re.findall(r"[A-Za-z][A-Za-z'\-]{1,}|\d[\d\-\s().]{5,}", bit):
            text = re.sub(re.escape(token), "[removed]", text, flags=re.I)
    return text


def parse(tables: list[RawTable], filename: str) -> RentRollExtraction | None:
    found = find_table(tables)
    if found is None:
        return None
    table, h = found
    header = table.rows[h].texts()
    colmap: dict[int, str] = {}
    for i, text in enumerate(header):
        f = match_column(text)
        if f and f not in colmap.values():
            colmap[i] = f
    result = RentRollExtraction([], table.name, table.method,
                                columns_found=sorted({f for f in colmap.values() if not f.startswith("_")}))

    for row in table.rows[h + 1:]:
        texts = row.texts()
        if not any(texts):
            continue
        private_bits = [texts[i] for i, f in colmap.items() if f in PRIVATE_FIELDS and i < len(texts) and texts[i]]
        values = {f: (row.cells[i] if i < len(row.cells) else None) for i, f in colmap.items() if f not in PRIVATE_FIELDS}
        unit_text = cell_text(values["unit_id"].value) if values.get("unit_id") else ""
        if re.match(r"^(grand\s*)?totals?\b", unit_text, re.I) or (not unit_text and re.match(r"^totals?", texts[0], re.I)):
            for f in ("current_rent", "market_rent", "sqft"):
                if values.get(f) and (n := parse_number(values[f].value)) is not None:
                    result.reported_totals[f] = n
            continue
        if not unit_text:
            result.warnings.append(f"{row.location}: a row with no unit number was skipped "
                                   f"({len([t for t in texts if t])} filled cells). Check it in the source.")
            continue
        result.units.append(_unit(row, values, private_bits, filename, table))
    if not result.units:
        result.warnings.append("A rent roll header was found, but no unit rows under it.")
    return result


def _unit(row: RawRow, v: dict, private_bits: list[str], filename: str, table: RawTable) -> RentRollUnit:
    issues: list[str] = []
    confs = [c.confidence for c in v.values() if c is not None and c.value not in (None, "")]
    ocr = table.method == "pdf_ocr"
    conf = "high" if not ocr else ("low" if confs and min(confs) < OCR_LOW_CONFIDENCE else "medium")

    def text(f):
        c = v.get(f)
        return _scrub(cell_text(c.value), private_bits) if c else ""

    def money(f, required=False):
        c = v.get(f)
        raw = cell_text(c.value) if c else ""
        n = parse_number(c.value) if c else None
        if raw and n is None:
            issues.append(f"{f.replace('_', ' ')} '{_scrub(raw, private_bits)}' isn't a number")
        elif required and n is None:
            issues.append(f"{f.replace('_', ' ')} is blank")
        return n

    beds = baths = None
    if v.get("beds_baths"):
        m = re.match(r"^\s*(\d+(?:\.\d)?)\s*[/x-]\s*(\d+(?:\.\d)?)\s*$", text("beds_baths"))
        if m:
            beds, baths = Decimal(m.group(1)), Decimal(m.group(2))
        elif text("beds_baths"):
            issues.append(f"beds/baths '{text('beds_baths')}' couldn't be read")
    beds = beds if beds is not None else money("beds")
    baths = baths if baths is not None else money("baths")
    if beds is None:
        m = re.search(r"(\d)\s*(?:br|bd|bed)", text("unit_type"), re.I)
        if m:
            beds = Decimal(m.group(1))
        elif re.search(r"studio", text("unit_type"), re.I):
            beds = Decimal(0)

    raw_status = text("status")
    status = match_status(raw_status) if raw_status else None
    if status is None:
        issues.append("status not recognized" if raw_status else "status is blank")
    current = money("current_rent", required=status in ("occupied", "employee"))
    market = money("market_rent")

    lease_end_raw = text("lease_end")
    mtm = bool(re.fullmatch(r"m\s*t\s*m|month[\s-]*to[\s-]*month", lease_end_raw, re.I))
    lease_end = None if mtm else parse_date(v["lease_end"].value) if v.get("lease_end") else None
    if lease_end_raw and not mtm and lease_end is None:
        issues.append(f"lease end '{lease_end_raw}' isn't a date")
    lease_start = parse_date(v["lease_start"].value) if v.get("lease_start") else None

    if conf == "low" and not issues:
        issues.append("OCR wasn't sure about some characters in this row; compare it with the scan")
    if issues:
        conf = "low"
    unit_id = re.sub(r"\s+", "", text("unit_id"))
    return RentRollUnit(
        unit_id=unit_id, unit_type=text("unit_type") or None, beds=beds, baths=baths, sqft=money("sqft"),
        status=status, current_rent=current, market_rent=market, lease_start=lease_start, lease_end=lease_end,
        month_to_month=mtm, concessions=money("concessions"), deposit=money("deposit"),
        source=SourceRef(filename, f"{table.name} {row.location}", table.method, normalize_label(unit_id)),
        confidence=conf, issues=issues,
    )
