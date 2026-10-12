"""Run one uploaded file through extraction: detect what it is, read it, map labels.

Never drops rows silently: anything skipped becomes a warning. If the file can't be recognized,
the result says so and the UI offers manual entry.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from propertyiq.llm import LLMProvider
from propertyiq.screening.intake import mapping, rentroll, t12
from propertyiq.screening.intake.tables import ExtractionError, RawTable, read_any


@dataclass
class ExtractionResult:
    kind: str | None                      # rent_roll | t12 | None (not recognized)
    file_type: str | None                 # xlsx | xls | csv | pdf
    method: str | None                    # excel | csv | pdf_text | pdf_ocr
    rent_roll: rentroll.RentRollExtraction | None = None
    t12: t12.T12Extraction | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


def _either_header(texts: list[str]) -> bool:
    return rentroll.is_header(texts) or t12.is_header(texts)


def extract(filename: str, data: bytes, expected: str | None = None,
            provider: LLMProvider | None = None) -> ExtractionResult:
    """expected: 'rent_roll' or 't12' if the user said what they're uploading; None to detect."""
    try:
        file_type, tables = read_any(filename, data, _either_header)
    except ExtractionError as exc:
        return ExtractionResult(None, None, None, error=str(exc))

    rr = rentroll.parse(tables, filename) if expected in (None, "rent_roll") else None
    tw = t12.parse(tables, filename) if expected in (None, "t12") else None
    if rr and rr.units and (expected == "rent_roll" or not (tw and tw.lines)):
        return ExtractionResult("rent_roll", file_type, rr.method, rent_roll=rr, warnings=rr.warnings)
    if tw and tw.lines:
        _map_unknown_labels(tw, provider)
        t12.reconcile(tw)
        return ExtractionResult("t12", file_type, tw.method, t12=tw, warnings=tw.warnings)
    what = {"rent_roll": "a rent roll", "t12": "a T-12"}.get(expected or "", "a rent roll or a T-12")
    hint = _describe(tables)
    return ExtractionResult(None, file_type, tables[0].method if tables else None,
                            error=f"Couldn't find {what} in this file. {hint} You can enter the figures manually instead.")


def _map_unknown_labels(x: t12.T12Extraction, provider: LLMProvider | None) -> None:
    unknown = [ln for ln in x.lines if ln.category == "unmapped"]
    suggestions = mapping.suggest([(ln.original_label, ln.section_hint) for ln in unknown], provider) if (
        unknown and provider is not None) else {}
    for ln in unknown:
        if ln.original_label in suggestions:
            ln.category, ln.mapped_by, ln.confidence = suggestions[ln.original_label], "ai", "low"
            ln.issues.append("Category suggested by the local AI model; check it.")
        else:
            default = t12.section_default(ln.section_hint)
            if default != "unmapped":
                ln.category, ln.mapped_by = default, "section_default"
                ln.issues.append(f"Label not recognized; placed in '{default}' because it's listed under "
                                 f"'{ln.section_hint}'. Check it.")
            else:
                ln.issues.append("Label not recognized; choose a category.")
            ln.confidence = "low"


def _describe(tables: list[RawTable]) -> str:
    rows = sum(len(t.rows) for t in tables)
    if rows == 0:
        return "No readable text or cells were found."
    return (f"Read {rows} rows but didn't find a header row with unit/rent columns (rent roll) "
            "or at least six month columns (T-12).")
