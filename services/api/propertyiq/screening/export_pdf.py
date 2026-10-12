"""One-page PDF screening summary (reportlab). Numbers come from the same Report as the screen."""
from __future__ import annotations

from decimal import Decimal
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import KeepInFrame, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from propertyiq.screening import DISCLAIMER
from propertyiq.screening.report import Report, assumptions
from propertyiq.screening.textfmt import METRIC_ROWS, fmt_metric, fmt_pct, fmt_ratio, fmt_usd

INK = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#5b5a56")
RULE = colors.HexColor("#d9d8d3")
STATUS_COLORS = {"PASS": colors.HexColor("#0a7d0a"), "FAIL": colors.HexColor("#c03030"),
                 "NOT CHECKED": colors.HexColor("#6b6a66")}

BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=7.5, leading=9.5, textColor=INK, alignment=TA_LEFT)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=6.8, leading=8.4, textColor=MUTED)
H1 = ParagraphStyle("h1", parent=BODY, fontName="Helvetica-Bold", fontSize=13, leading=16)
H2 = ParagraphStyle("h2", parent=BODY, fontName="Helvetica-Bold", fontSize=8.5, leading=11, spaceBefore=6, spaceAfter=2)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _p(text: str, style=BODY) -> Paragraph:
    return Paragraph(_esc(text), style)


def _status(s: str) -> Paragraph:
    color = STATUS_COLORS.get(s, MUTED).hexval()[2:]
    return Paragraph(f'<font color="#{color}"><b>{s}</b></font>', BODY)


def _table(rows: list[list], widths: list[float], header: bool = True, right_cols: tuple[int, ...] = ()) -> Table:
    t = Table(rows, colWidths=widths, hAlign="LEFT")
    style = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 7.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, RULE),
    ] + [("ALIGN", (c, 0), (c, -1), "RIGHT") for c in right_cols]
    if header:
        style += [("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.2), ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
                  ("LINEBELOW", (0, 0), (-1, 0), 0.6, MUTED)]
    t.setStyle(TableStyle(style))
    return t


def _footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica-Oblique", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(0.5 * inch, 0.38 * inch, DISCLAIMER)
    canvas.drawRightString(letter[0] - 0.5 * inch, 0.38 * inch, f"Page {doc.page}")
    canvas.restoreState()


def render_pdf(r: Report) -> bytes:
    assert r.valid and r.metrics is not None
    m, d = r.metrics, r.deal
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.5 * inch, rightMargin=0.5 * inch,
                            topMargin=0.45 * inch, bottomMargin=0.6 * inch,
                            title=f"Loan screening summary - {d.property_name or 'deal'}", author="PropertyIQ")
    width = letter[0] - 1.0 * inch

    story: list = [
        _p(f"Loan screening summary: {d.property_name or '(unnamed property)'}", H1),
        Paragraph(
            f'Overall: <font color="#{STATUS_COLORS[r.overall].hexval()[2:]}"><b>{r.overall}</b></font>'
            f" &nbsp;·&nbsp; Guideline set: {_esc(r.guidelines.name)}"
            f" &nbsp;·&nbsp; Generated {r.generated_at:%Y-%m-%d %H:%M %Z}", BODY),
        _p("PropertyIQ prototype. Synthetic/demo use. " + DISCLAIMER, SMALL),
        Spacer(0, 4),
    ]

    # Metrics (left) and assumptions (right)
    metric_rows = [["Metric", "Value", "What it means"]]
    for key, label, meaning in METRIC_ROWS:
        v = getattr(m, key)
        if v is None and key in ("interest_only_debt_service", "dscr_interest_only"):
            continue
        metric_rows.append([_p(label), fmt_metric(key, v), _p(meaning, SMALL)])
    left = _table(metric_rows, [1.35 * inch, 0.8 * inch, 2.35 * inch], right_cols=(1,))

    assumption_rows = [["Input (every value used)", "Value"]] + [[_p(a), _p(b)] for a, b in assumptions(d)]
    right = _table(assumption_rows, [1.6 * inch, 1.3 * inch])
    story += [Table([[[_p("Key metrics", H2), left], [_p("Assumptions", H2), right]]],
                    colWidths=[4.6 * inch, width - 4.6 * inch],
                    style=[("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)])]

    if m.warnings:
        story += [_p("Flags", H2)] + [_p("• " + w) for w in m.warnings]

    rule_rows = [["Guideline", "Actual", "Limit", "Result"]]
    kinds = {"ratio": fmt_ratio, "pct": fmt_pct, "usd": fmt_usd}
    for x in r.rules:
        f = kinds.get(x.kind, lambda v: "n/a" if v is None else str(v))
        rule_rows.append([_p(x.label), _p(f(x.actual)), _p(f(x.threshold)), _status(x.status)])
    story += [_p(f"Guideline results ({r.guidelines.name})", H2),
              _table(rule_rows, [2.2 * inch, 1.6 * inch, 2.6 * inch, 1.0 * inch])]

    stress_rows = [["Scenario", "NOI", "Debt service", "DSCR", "LTV", "Debt yield", "Result"]]
    for s in r.stress:
        sm = s.metrics
        stress_rows.append([_p(s.scenario.label), fmt_usd(sm.noi), fmt_usd(sm.annual_debt_service),
                            fmt_ratio(sm.dscr), fmt_pct(sm.ltv), fmt_pct(sm.debt_yield), _status(s.overall)])
    story += [_p("Stress tests (what if things get worse)", H2),
              _table(stress_rows, [2.75 * inch, 0.85 * inch, 0.85 * inch, 0.55 * inch, 0.6 * inch, 0.7 * inch, 0.9 * inch],
                     right_cols=(1, 2, 3, 4, 5))]

    # Two-way grid: DSCR by interest rate (rows) and vacancy (columns)
    min_dscr = r.guidelines.min_dscr.threshold if r.guidelines.min_dscr.enabled else None
    grid_rows = [["Rate \ Vacancy"] + [fmt_pct(v) for v in r.grid_vacancies]]
    shade = []
    for i, (rate, row) in enumerate(zip(r.grid_rates, r.grid), start=1):
        grid_rows.append([fmt_pct(rate, 2)] + [fmt_ratio(v) for v in row])
        for j, v in enumerate(row, start=1):
            if min_dscr is not None and v is not None and v < min_dscr:
                shade.append(("BACKGROUND", (j, i), (j, i), colors.HexColor("#f8d7d7")))
    grid = _table(grid_rows, [1.0 * inch] + [0.75 * inch] * len(r.grid_vacancies), right_cols=tuple(range(1, 1 + len(r.grid_vacancies))))
    grid.setStyle(TableStyle(shade + [("FONT", (0, 1), (0, -1), "Helvetica-Bold", 7.2)]))
    note = (f"Shaded cells are below the minimum DSCR of {fmt_ratio(min_dscr)}." if min_dscr is not None
            else "Minimum DSCR rule is off, so no cells are shaded.")
    story += [_p("DSCR by interest rate (rows) and vacancy (columns)", H2), grid, _p(note, SMALL)]

    doc.build([KeepInFrame(width, letter[1] - 1.05 * inch, story, mode="shrink")],
              onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
