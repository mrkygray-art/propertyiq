"""Read any supported upload into plain tables of cells, keeping where each cell came from.

Excel/CSV are read cell by cell. PDFs are read as positioned text: words are grouped into lines,
then split into columns using the header line's positions. Scanned PDFs (no text layer) go
through OCR first and then the same column logic, so every format ends up as the same RawTable.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

MAX_BYTES = 10 * 1024 * 1024
# OCR values are never "high" confidence; below this score they're "low" and highlighted for review.
OCR_LOW_CONFIDENCE = 0.6


class ExtractionError(Exception):
    """A file that can't be read. The message is shown to the user as-is."""


@dataclass
class Cell:
    value: str | Decimal | date | None
    confidence: float = 1.0          # OCR confidence (0-1); 1.0 for Excel/CSV/PDF text


@dataclass
class RawRow:
    cells: list[Cell]
    location: str                    # "row 14" or "line 9"

    def texts(self) -> list[str]:
        return [cell_text(c.value) for c in self.cells]


@dataclass
class RawTable:
    name: str                        # sheet name or "page 1"
    method: str                      # excel | csv | pdf_text | pdf_ocr
    rows: list[RawRow] = field(default_factory=list)


def cell_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (date, datetime)):
        return v.strftime("%m/%d/%Y")
    return str(v).strip()


# ---------------------------------------------------------------- value parsing (shared)

_MONEY = re.compile(r"^\(?-?\$?\s*-?[\d,]*\.?\d+\)?-?$")


def parse_number(v) -> Decimal | None:
    """'$1,234.50' -> 1234.50; '(1,380.00)' -> -1380.00; '' or '-' -> None."""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        return Decimal(str(v))
    # OCR slips: a semicolon for a comma ("1;990"), a unicode minus.
    s = str(v).strip().replace("−", "-").replace(";", ",")
    if s in ("", "-", "--", "—", "n/a", "N/A"):
        return None
    # OCR often reads a thousands comma as a period: "39.500" is $39,500 (money has 2 decimals, not 3).
    if re.fullmatch(r"\(?-?\$?\d{1,3}(\.\d{3})+\)?", s.replace(" ", "")):
        s = s.replace(".", ",")
    if not _MONEY.match(s.replace(" ", "")):
        return None
    neg = s.startswith("(") and s.endswith(")") or s.startswith("-") or s.endswith("-")
    digits = re.sub(r"[^\d.]", "", s)
    if not digits or digits == ".":
        return None
    try:
        d = Decimal(digits)
    except InvalidOperation:
        return None
    return -d if neg else d


_DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y", "%d-%b-%Y", "%b %d, %Y", "%B %d, %Y")


def parse_date(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = cell_text(v)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- file type checks

def sniff(filename: str, data: bytes) -> str:
    """Return xlsx | xls | csv | pdf, or raise ExtractionError with a friendly message."""
    if not data:
        raise ExtractionError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise ExtractionError(f"The file is {len(data) / 1_048_576:.1f} MB; the limit is {MAX_BYTES // 1_048_576} MB.")
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if data[:4] == b"%PDF":
        return "pdf"
    if data[:2] == b"PK" and ext in ("xlsx", "xlsm"):
        return "xlsx"
    if data[:8] == bytes.fromhex("D0CF11E0A1B11AE1") and ext == "xls":
        return "xls"
    if ext in ("csv", "txt"):
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                data.decode("latin-1")
            except UnicodeDecodeError:
                raise ExtractionError("The CSV file isn't readable text.") from None
        return "csv"
    if ext in ("xlsx", "xls", "xlsm", "pdf"):
        raise ExtractionError(f"The file is named .{ext} but its contents aren't a valid {ext.upper()} file. "
                              "It may be damaged or saved in another format.")
    raise ExtractionError("Unsupported file type. Upload Excel (.xlsx, .xls), CSV, or PDF.")


# ---------------------------------------------------------------- Excel / CSV

def read_xlsx(data: bytes) -> list[RawTable]:
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    except Exception as exc:  # zip/XML errors from damaged files
        raise ExtractionError("The Excel file couldn't be opened. It may be damaged or password-protected.") from exc
    tables = []
    for ws in wb.worksheets:
        t = RawTable(f"Sheet '{ws.title}'", "excel")
        for r_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
            cells = [Cell(_excel_value(v)) for v in row]
            if any(c.value not in (None, "") for c in cells):
                t.rows.append(RawRow(cells, f"row {r_idx}"))
        tables.append(t)
    return tables


def _excel_value(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return Decimal(str(v))
    if isinstance(v, str):
        return v.strip() or None
    return v


def read_xls(data: bytes) -> list[RawTable]:
    import xlrd
    try:
        book = xlrd.open_workbook(file_contents=data)
    except Exception as exc:
        raise ExtractionError("The .xls file couldn't be opened. It may be damaged or password-protected.") from exc
    tables = []
    for sh in book.sheets():
        t = RawTable(f"Sheet '{sh.name}'", "excel")
        for r in range(sh.nrows):
            cells = []
            for c in range(sh.ncols):
                cell = sh.cell(r, c)
                if cell.ctype == xlrd.XL_CELL_DATE:
                    v = xlrd.xldate.xldate_as_datetime(cell.value, book.datemode).date()
                elif cell.ctype == xlrd.XL_CELL_NUMBER:
                    v = Decimal(str(cell.value))
                else:
                    v = str(cell.value).strip() or None
                cells.append(Cell(v))
            if any(c.value not in (None, "") for c in cells):
                t.rows.append(RawRow(cells, f"row {r + 1}"))
        tables.append(t)
    return tables


def read_csv(data: bytes) -> list[RawTable]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    t = RawTable("CSV", "csv")
    for r_idx, row in enumerate(csv.reader(io.StringIO(text)), start=1):
        cells = [Cell(v.strip() or None) for v in row]
        if any(c.value for c in cells):
            t.rows.append(RawRow(cells, f"row {r_idx}"))
    return [t]


# ---------------------------------------------------------------- PDF (text and OCR)

@dataclass
class Phrase:
    """A run of words close together on one line: usually one table cell."""
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    confidence: float = 1.0

    @property
    def center(self) -> float:
        return (self.x0 + self.x1) / 2


def _group_lines(phrases: list[Phrase]) -> list[list[Phrase]]:
    phrases = sorted(phrases, key=lambda p: (p.top, p.x0))
    lines: list[list[Phrase]] = []
    for p in phrases:
        h = max(1.0, p.bottom - p.top)
        if lines and abs(lines[-1][0].top - p.top) <= h * 0.5:
            lines[-1].append(p)
        else:
            lines.append([p])
    return [sorted(line, key=lambda p: p.x0) for line in lines]


def _merge_words(words: list[Phrase], gap_factor: float) -> list[Phrase]:
    """Join words on the same line into phrases when the gap between them is small (a space)."""
    out: list[Phrase] = []
    for line in _group_lines(words):
        cur = None
        for w in line:
            h = max(1.0, w.bottom - w.top)
            if cur and w.x0 - cur.x1 <= h * gap_factor:
                cur = Phrase(f"{cur.text} {w.text}", cur.x0, w.x1, min(cur.top, w.top), max(cur.bottom, w.bottom),
                             min(cur.confidence, w.confidence))
            else:
                if cur:
                    out.append(cur)
                cur = w
        if cur:
            out.append(cur)
    return out


def phrases_to_table(phrases: list[Phrase], name: str, method: str, is_header) -> RawTable:
    """Split positioned phrases into rows and columns using the header line.

    is_header(texts) -> bool picks the header line. Column boundaries are the midpoints between
    neighbouring header cells; each phrase goes in the column its center falls in.
    """
    lines = _group_lines(phrases)
    table = RawTable(name, method)
    header_idx = next((i for i, line in enumerate(lines) if is_header([p.text for p in line])), None)
    if header_idx is None:
        for i, line in enumerate(lines, start=1):
            table.rows.append(RawRow([Cell(p.text, p.confidence) for p in line], f"line {i}"))
        return table
    header = lines[header_idx]
    centers = [p.center for p in header]
    bounds = [(centers[i] + centers[i + 1]) / 2 for i in range(len(centers) - 1)]
    # Right-aligned numbers can start left of their header; nudge boundaries toward the left cell's right edge.
    bounds = [max(b, header[i].x1 + 2) if i + 1 < len(header) and header[i + 1].x0 > header[i].x1 + 4 else b
              for i, b in enumerate(bounds)]
    for i, line in enumerate(lines, start=1):
        if i - 1 < header_idx:
            table.rows.append(RawRow([Cell(" ".join(p.text for p in line), min(p.confidence for p in line))],
                                     f"line {i}"))
            continue
        cols: list[list[Phrase]] = [[] for _ in header]
        for p in _split_across_columns(line, bounds):
            col = sum(1 for b in bounds if p.center >= b)
            cols[col].append(p)
        cells = [Cell(" ".join(p.text for p in c) or None, min((p.confidence for p in c), default=1.0)) for c in cols]
        table.rows.append(RawRow(cells, f"line {i}"))
    return table


def _split_across_columns(line: list[Phrase], bounds: list[float]) -> list[Phrase]:
    """OCR sometimes reads two neighbouring cells as one box ("OccupiedTatum Placeholder").
    If a box clearly straddles a column boundary, split its text at the word or capital-letter
    break closest to where the boundary falls (assuming roughly even character widths)."""
    out: list[Phrase] = []
    for p in line:
        width = p.x1 - p.x0
        crossing = [b for b in bounds if p.x0 + 0.25 * width < b < p.x1 - 0.25 * width]
        if not crossing or len(p.text) < 4:
            out.append(p)
            continue
        b = crossing[0]
        target = round((b - p.x0) / width * len(p.text))
        breaks = [i for i in range(1, len(p.text))
                  if p.text[i - 1] in " .,;:" or (p.text[i].isupper() and p.text[i - 1].islower())]
        if not breaks:
            out.append(p)
            continue
        cut = min(breaks, key=lambda i: abs(i - target))
        left, right = p.text[:cut].strip(" .,;:"), p.text[cut:].strip()
        split_x = p.x0 + width * cut / len(p.text)
        out.append(Phrase(left, p.x0, split_x - 1, p.top, p.bottom, p.confidence))
        out += _split_across_columns([Phrase(right, split_x + 1, p.x1, p.top, p.bottom, p.confidence)], bounds)
    return out


def pdf_is_encrypted(data: bytes) -> bool:
    import pypdfium2 as pdfium
    try:
        pdfium.PdfDocument(data).close()
        return False
    except pdfium.PdfiumError as exc:
        return "password" in str(exc).lower()


def read_pdf(data: bytes, is_header) -> list[RawTable]:
    """Text-layer PDFs via pdfplumber; pages with almost no text go to OCR."""
    import pdfplumber
    if pdf_is_encrypted(data):
        raise ExtractionError("This PDF is password-protected. Remove the password and upload it again.")
    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as exc:
        raise ExtractionError("The PDF couldn't be opened. It may be damaged.") from exc
    tables: list[RawTable] = []
    with pdf:
        for n, page in enumerate(pdf.pages, start=1):
            words = page.extract_words(keep_blank_chars=False, use_text_flow=False, x_tolerance=1.5)
            if len(words) >= 15:
                ws = [Phrase(w["text"], w["x0"], w["x1"], w["top"], w["bottom"]) for w in words]
                tables.append(phrases_to_table(_merge_words(ws, 0.45), f"page {n}", "pdf_text", is_header))
            else:
                tables.append(_ocr_page(data, n, is_header))
    return tables


_ocr_engine = None


def _ocr_page(data: bytes, page_number: int, is_header) -> RawTable:
    """Render one PDF page to an image and read it with RapidOCR (runs locally, offline)."""
    global _ocr_engine
    import numpy as np
    import pypdfium2 as pdfium
    from rapidocr_onnxruntime import RapidOCR
    if _ocr_engine is None:
        _ocr_engine = RapidOCR()
    doc = pdfium.PdfDocument(data)
    try:
        image = doc[page_number - 1].render(scale=200 / 72).to_pil().convert("RGB")
    finally:
        doc.close()
    result, _ = _ocr_engine(np.array(image))
    phrases = []
    for box, text, score in result or []:
        xs = [pt[0] for pt in box]
        ys = [pt[1] for pt in box]
        phrases.append(Phrase(text.strip(), min(xs), max(xs), min(ys), max(ys), float(score)))
    return phrases_to_table(_merge_words(phrases, 0.35), f"page {page_number}", "pdf_ocr", is_header)


def read_any(filename: str, data: bytes, is_header) -> tuple[str, list[RawTable]]:
    kind = sniff(filename, data)
    if kind == "xlsx":
        return kind, read_xlsx(data)
    if kind == "xls":
        return kind, read_xls(data)
    if kind == "csv":
        return kind, read_csv(data)
    return kind, read_pdf(data, is_header)
