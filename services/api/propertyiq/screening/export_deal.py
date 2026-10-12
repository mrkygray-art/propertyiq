"""Tier 2 deal exports (Milestone 9): PDF memo and Excel workbook with live formulas.

PDF: summary, borrower vs underwritten, red-flag summary, flags with sources, market support,
data-source list. Disclaimer on every page.
Excel: the Tier 1 sheets (driven by the Underwritten sheet) plus Borrower, Adjustments,
Underwritten, Flags and Comps. Underwritten values are formulas over the Adjustments sheet: change
an adjustment's value or status and everything downstream recalculates.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from io import BytesIO

from openpyxl.styles import PatternFill
from openpyxl.utils import get_column_letter
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

from propertyiq.screening import compare
from propertyiq.screening.export_pdf import BODY, H1, H2, MUTED, SMALL, _esc, _p, _status, _table
from propertyiq.screening.export_xlsx import BOLD, INPUT_FILL, NOTE, PCT2, TITLE, USD, _status_formatting, build_workbook
from propertyiq.screening.textfmt import fmt_pct, fmt_ratio, fmt_usd

DISCLAIMER = ("Screening and underwriting support only. Not an appraisal, legal advice, or a credit approval. "
              "Results depend on the accuracy of the inputs provided.")
SEV_COLORS = {"high": "#c03030", "medium": "#8a5a00", "low": "#1c5cab"}


def _fmt_line(key: str, v) -> str:
    if v is None:
        return "n/a"
    v = Decimal(str(v))
    return fmt_pct(v, 1) if compare.LINE_KIND[key] == "pct" else fmt_usd(v)


def _footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica-Oblique", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(0.5 * inch, 0.38 * inch, DISCLAIMER)
    canvas.drawRightString(letter[0] - 0.5 * inch, 0.38 * inch, f"Page {doc.page}")
    canvas.restoreState()


# ---------------------------------------------------------------- PDF

def render_deal_pdf(c: dict, flags: list[dict], market: dict, documents: list[dict], rules: list[dict]) -> bytes:
    """c: workspace.comparison() output."""
    deal = c["deal"]
    rb, ru = c["borrower_report"], c["underwritten_report"]
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.5 * inch, rightMargin=0.5 * inch,
                            topMargin=0.5 * inch, bottomMargin=0.6 * inch, title=f"Underwriting memo - {deal['name']}",
                            author="PropertyIQ")
    w = letter[0] - inch
    open_flags = [f for f in flags if f["status"] == "open"]
    story: list = [
        _p(f"Underwriting memo: {deal['name']}", H1),
        _p(f"Generated {datetime.now().astimezone():%Y-%m-%d %H:%M %Z} · Guideline set: {ru.guidelines.name} · "
           f"Borrower figures from: {c['source']}", SMALL),
        _p("PropertyIQ prototype. Synthetic/demo use. " + DISCLAIMER, SMALL),
        Spacer(0, 6),
        Paragraph(f"<b>{_esc(c['summary'])}</b>", BODY),
        Spacer(0, 4),
    ]
    counts = {s: sum(1 for f in open_flags if f["severity"] == s) for s in ("high", "medium", "low")}
    story.append(Paragraph(
        f"Open flags: <font color='{SEV_COLORS['high']}'><b>{counts['high']} high</b></font> · "
        f"<font color='{SEV_COLORS['medium']}'><b>{counts['medium']} medium</b></font> · "
        f"<font color='{SEV_COLORS['low']}'><b>{counts['low']} low</b></font> "
        f"({', '.join(f'{p} {sum(1 for f in open_flags if f['rule_id'].startswith(p))}' for p in ('A', 'M', 'R'))}: "
        "A = document checks, M = market, R = California)", BODY))

    # Side-by-side results
    def m(rep, key, kind):
        if not rep.valid or rep.metrics is None:
            return "n/a"
        v = getattr(rep.metrics, key)
        return fmt_ratio(v) if kind == "ratio" else fmt_pct(v) if kind == "pct" else fmt_usd(v)
    side = [["", "Borrower", "Underwritten"]]
    for label, key, kind in (("Effective gross income", "egi", "usd"), ("Net operating income", "noi", "usd"),
                             ("Annual debt service", "annual_debt_service", "usd"), ("DSCR", "dscr", "ratio"),
                             ("LTV", "ltv", "pct"), ("Debt yield", "debt_yield", "pct"),
                             ("Break-even occupancy", "break_even_occupancy", "pct")):
        side.append([label, m(rb, key, kind), m(ru, key, kind)])
    side.append(["Guidelines", _status(rb.overall), _status(ru.overall)])
    story += [_p("Results: borrower vs underwritten", H2), _table(side, [2.4 * inch, 1.6 * inch, 1.6 * inch], right_cols=(1, 2))]

    # Comparison table
    approved = {a["line_item"]: a for a in c["adjustments"] if a["status"] == "approved"}
    rows = [["Line item", "Borrower", "Underwritten", "Change", "Reason", "Note / source"]]
    for key, label, kind, _section in compare.LINES:
        b, u = c["borrower"][key], c["underwritten"][key]
        a = approved.get(key)
        delta = "" if b == u else (("+" if u > b else "") + fmt_pct(u - b, 1).replace("%", " pts") if kind == "pct" else
                                   ("+" if u > b else "") + fmt_usd(u - b))
        rows.append([_p(label), _fmt_line(key, b), _fmt_line(key, u), delta, _p(a["reason_code"] if a else "", SMALL),
                     _p((a["note"] + (f" [flag {a['flag_fingerprint'].split(':')[0]}]" if a.get("flag_fingerprint") else ""))
                        if a else "", SMALL)])
    tb, tu = c["borrower_totals"], c["underwritten_totals"]
    for label, attr in (("Effective gross income", "egi"), ("Operating expenses", "expenses"), ("Reserves", "reserves"),
                        ("Net operating income", "noi")):
        b, u = getattr(tb, attr), getattr(tu, attr)
        rows.append([Paragraph(f"<b>{label}</b>", BODY), fmt_usd(b), fmt_usd(u), ("+" if u > b else "") + fmt_usd(u - b) if u != b else "", "", ""])
    story += [_p("Borrower vs underwritten (annual)", H2),
              _table(rows, [1.7 * inch, 0.95 * inch, 0.95 * inch, 0.85 * inch, 1.2 * inch, w - 5.65 * inch], right_cols=(1, 2, 3))]

    # Underwritten stress
    if ru.valid and ru.stress:
        srows = [["Scenario (underwritten)", "NOI", "DSCR", "LTV", "Result"]]
        for s in ru.stress:
            srows.append([_p(s.scenario.label), fmt_usd(s.metrics.noi), fmt_ratio(s.metrics.dscr), fmt_pct(s.metrics.ltv),
                          _status(s.overall)])
        story += [_p("Stress tests on the underwritten case", H2),
                  _table(srows, [3.6 * inch, 1.0 * inch, 0.7 * inch, 0.7 * inch, 1.0 * inch], right_cols=(1, 2, 3))]

    # Flags
    story += [PageBreak(), _p("Flags", H1)]
    for f in sorted(flags, key=lambda x: (x["status"] != "open", {"high": 0, "medium": 1, "low": 2}[x["severity"]], x["rule_id"])):
        color = SEV_COLORS[f["severity"]]
        status = "" if f["status"] == "open" else f" (dismissed: {_esc(f['dismissal_note'] or '')})"
        story.append(Paragraph(f"<font color='{color}'><b>{f['severity'].upper()}</b></font> <b>{f['rule_id']}</b> "
                               f"{_esc(f['title'])}{status}", BODY))
        story.append(_p(f["explanation"], SMALL))
        ev = "; ".join(f"{e['label']}: {e['value']}" for e in f["evidence"])
        if ev:
            story.append(_p("Evidence: " + ev, SMALL))
        story.append(_p("Suggested action: " + f["suggested_action"], SMALL))
        story.append(_p("Source: " + " · ".join(f["sources"]), SMALL))
        story.append(Spacer(0, 5))

    # Market
    story.append(_p("Market support", H1))
    if not market:
        story.append(_p("No comps were pulled for this deal. Not available.", BODY))
    for kind, res in market.items():
        story.append(_p(f"{'Rent' if kind == 'rent' else 'Sale'} comps: {res['provider_label']}", H2))
        story.append(_p(f"Retrieved {res['retrieved_at'][:19]} · {res['area']} · {len(res['comps'])} comps"
                        + (" · SYNTHETIC DEMO DATA" if res.get("synthetic") else ""), SMALL))
        if not res["comps"]:
            story.append(_p("No comps available.", BODY))
            continue
        if kind == "sale":
            s = res["summary"]
            story.append(_p(f"Median price per unit {fmt_usd(Decimal(str(s['price_per_unit']['median'])))} "
                            f"(range {fmt_usd(Decimal(str(s['price_per_unit']['low'])))} to "
                            f"{fmt_usd(Decimal(str(s['price_per_unit']['high'])))}); cap rate "
                            + (f"median {s['cap_rate']['median'] * 100:.2f}%, {s['cap_rate']['note']}"
                               if s['cap_rate']['median'] is not None else "not available"), SMALL))
            crow = [["Source ID", "Address", "Sold", "Price/unit", "Cap rate"]] + [
                [x["source_id"], _p(x["address"], SMALL), x["sale_date"], fmt_usd(Decimal(str(x["price_per_unit"]))),
                 f"{x['cap_rate'] * 100:.2f}% (reported)" if x.get("cap_rate") is not None else "not available"]
                for x in res["comps"]]
        else:
            crow = [["Source ID", "Address", "Type", "Rent", "Miles"]] + [
                [x["source_id"], _p(x["address"], SMALL), x["unit_type"], fmt_usd(Decimal(str(x["rent"]))), str(x["distance_miles"])]
                for x in res["comps"]]
        story.append(_table(crow, [1.0 * inch, 2.8 * inch, 1.0 * inch, 1.1 * inch, 1.4 * inch], right_cols=(3,)))
    story.append(_p("Comps support the lender's judgment. They are not an appraisal.", SMALL))

    # Sources
    story += [PageBreak(), _p("Data sources", H1)]
    story.append(_table([["Document", "Read with", "Status"]] + [
        [_p(d["filename"]), d.get("extraction_method") or "", d["status"]] for d in documents],
        [4.5 * inch, 1.2 * inch, 1.3 * inch]))
    story.append(_p("Jurisdiction rules", H2))
    story.append(_table([["Rule", "Status", "Source"]] + [
        [_p(r["name"], SMALL), "verified " + str(r["last_verified"]) if r["verified"] else "UNVERIFIED", _p(r["source_url"] or "", SMALL)]
        for r in rules], [2.6 * inch, 1.1 * inch, w - 3.7 * inch]))
    story.append(_p("Expense benchmarks and the component typical-life table are example placeholders unless "
                    "replaced in Screening settings.", SMALL))
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


# ---------------------------------------------------------------- Excel

def render_deal_xlsx(c: dict, flags: list[dict], market: dict) -> bytes:
    wb, ref = build_workbook(c["underwritten_report"])
    keys = [k for k, *_ in compare.LINES]

    # Borrower sheet: borrower figures (values) and their totals (formulas)
    wbs = wb.create_sheet("Borrower", 0)
    wbs["A1"] = f"Borrower case: {c['deal']['name']}"
    wbs["A1"].font = TITLE
    wbs["A2"] = f"From {c['source']}. " + DISCLAIMER
    wbs["A2"].font = NOTE
    for col, h in enumerate(("Key", "Line item", "Borrower"), start=1):
        wbs.cell(4, col, h).font = BOLD
    brow = {}
    for i, k in enumerate(keys, start=5):
        wbs.cell(i, 1, k)
        wbs.cell(i, 2, compare.LINE_LABEL[k])
        cell = wbs.cell(i, 3, float(c["borrower"][k]))
        cell.number_format = PCT2 if compare.LINE_KIND[k] == "pct" else USD
        brow[k] = i

    # Adjustments sheet: one row per adjustment; Status and Underwritten value are editable
    wa = wb.create_sheet("Adjustments", 1)
    wa["A1"] = "Adjustments (only rows with Status = approved change the underwritten case)"
    wa["A1"].font = TITLE
    heads = ("ID", "Key", "Line item", "Borrower", "Underwritten", "Status", "Reason", "Note", "Flag")
    for col, h in enumerate(heads, start=1):
        wa.cell(3, col, h).font = BOLD
    for i, a in enumerate(c["adjustments"], start=4):
        k = a["line_item"]
        vals = (a["adjustment_id"], k, compare.LINE_LABEL.get(k, k),
                float(a["borrower_value"]) if a["borrower_value"] is not None else None,
                float(a["underwritten_value"]) if a["underwritten_value"] is not None else None,
                a["status"], a["reason_code"], a["note"], a.get("flag_fingerprint") or "")
        for col, v in enumerate(vals, start=1):
            cell = wa.cell(i, col, v)
            if col in (4, 5):
                cell.number_format = PCT2 if compare.LINE_KIND.get(k) == "pct" else USD
            if col in (5, 6):
                cell.fill = INPUT_FILL
    last = max(4, 3 + len(c["adjustments"]))
    _status_formatting(wa, f"F4:F{last}")

    # Underwritten sheet: formulas pick the approved adjustment, otherwise the borrower figure
    wu = wb.create_sheet("Underwritten", 2)
    wu["A1"] = "Underwritten case (live formulas)"
    wu["A1"].font = TITLE
    wu["A2"] = "Each line = the approved adjustment's value if there is one, otherwise the borrower's figure."
    wu["A2"].font = NOTE
    for col, h in enumerate(("Key", "Line item", "Underwritten", "Borrower", "Change"), start=1):
        wu.cell(4, col, h).font = BOLD
    urow = {}
    for i, k in enumerate(keys, start=5):
        wu.cell(i, 1, k)
        wu.cell(i, 2, compare.LINE_LABEL[k])
        crit = f'Adjustments!$B$4:$B${last},A{i},Adjustments!$F$4:$F${last},"approved"'
        wu.cell(i, 3, f"=IF(COUNTIFS({crit})>0,SUMIFS(Adjustments!$E$4:$E${last},{crit}),Borrower!C{brow[k]})")
        wu.cell(i, 4, f"=Borrower!C{brow[k]}")
        wu.cell(i, 5, f"=C{i}-D{i}")
        fmt = PCT2 if compare.LINE_KIND[k] == "pct" else USD
        for col in (3, 4, 5):
            wu.cell(i, col).number_format = fmt
        urow[k] = i
    t0 = 5 + len(keys) + 1
    U = lambda k: f"C{urow[k]}"  # noqa: E731
    B = lambda k: f"D{urow[k]}"  # noqa: E731
    exp_u = "+".join(U(k) for k in compare.EXPENSE_KEYS)
    exp_b = "+".join(B(k) for k in compare.EXPENSE_KEYS)
    total_rows = [
        ("Recurring gross income", f"={U('gross_potential_rent')}+{U('other_income')}", f"={B('gross_potential_rent')}+{B('other_income')}"),
        ("Vacancy and credit loss", f"=C{t0}*{U('vacancy_pct')}", f"=D{t0}*{B('vacancy_pct')}"),
        ("Effective gross income", f"=C{t0}+{U('non_recurring_income')}-C{t0 + 1}", f"=D{t0}+{B('non_recurring_income')}-D{t0 + 1}"),
        ("Operating expenses", f"={exp_u}", f"={exp_b}"),
        ("Reserves", f"={U('reserves')}", f"={B('reserves')}"),
        ("Net operating income", f"=C{t0 + 2}-C{t0 + 3}-C{t0 + 4}", f"=D{t0 + 2}-D{t0 + 3}-D{t0 + 4}"),
        ("Vacancy rate on all income (engine basis)",
         f"=IF(C{t0}+{U('non_recurring_income')}=0,0,C{t0 + 1}/(C{t0}+{U('non_recurring_income')}))", None),
        ("DSCR (NOI / annual debt service on the Calculations sheet)",
         f'=IF(ISNUMBER(Calculations!$B$8),C{t0 + 5}/Calculations!$B$8,"n/a")',
         f'=IF(ISNUMBER(Calculations!$B$8),D{t0 + 5}/Calculations!$B$8,"n/a")'),
    ]
    for j, (label, fu, fb) in enumerate(total_rows):
        r = t0 + j
        wu.cell(r, 2, label).font = BOLD
        wu.cell(r, 3, fu)
        if fb:
            wu.cell(r, 4, fb)
            wu.cell(r, 5, f'=IF(AND(ISNUMBER(C{r}),ISNUMBER(D{r})),C{r}-D{r},"")')
        fmt = PCT2 if "rate" in label else ("0.00" if label.startswith("DSCR") else USD)
        for col in (3, 4, 5):
            wu.cell(r, col).number_format = fmt
    for col, wdt in zip("ABCDE", (24, 44, 16, 16, 16)):
        wu.column_dimensions[col].width = wdt

    # Drive the Tier 1 Inputs from the Underwritten sheet
    wi = wb["Inputs"]
    def setf(key: str, formula: str):
        cell = wi[ref[key].split("!")[1].replace("$", "")]
        cell.value = formula
        cell.fill = PatternFill(fill_type=None)   # no longer a yellow input cell
    setf("pgi", f"=Underwritten!{U('gross_potential_rent')}")
    setf("other", f"=Underwritten!{U('other_income')}+Underwritten!{U('non_recurring_income')}")
    setf("vac", f"=Underwritten!C{t0 + 6}")
    setf("opex", f"=Underwritten!C{t0 + 3}")
    setf("res", f"=Underwritten!C{t0 + 4}")
    setf("value", f"=Underwritten!{U('value')}")
    wi["A3"] = ("Income, expenses, reserves and value come from the Underwritten sheet (driven by Adjustments). "
                "Loan terms in yellow are still editable here.")

    # Flags sheet
    wf = wb.create_sheet("Flags")
    wf["A1"] = "Flags"
    wf["A1"].font = TITLE
    for col, h in enumerate(("Rule", "Severity", "Status", "Title", "Explanation", "Evidence", "Suggested action", "Source",
                             "Dismissal note"), start=1):
        wf.cell(3, col, h).font = BOLD
    for i, f in enumerate(flags, start=4):
        for col, v in enumerate((f["rule_id"], f["severity"], f["status"], f["title"], f["explanation"],
                                 "; ".join(f"{e['label']}: {e['value']}" for e in f["evidence"]), f["suggested_action"],
                                 " | ".join(f["sources"]), f.get("dismissal_note") or ""), start=1):
            wf.cell(i, col, v)
    for col, wdt in zip("ABCDEFGHI", (7, 9, 10, 40, 60, 60, 40, 60, 30)):
        wf.column_dimensions[col].width = wdt

    # Comps sheet
    wc = wb.create_sheet("Comps")
    wc["A1"] = "Market comps (support for judgment, not an appraisal)"
    wc["A1"].font = TITLE
    r = 3
    if not market:
        wc.cell(r, 1, "No comps were pulled for this deal. Not available.")
    for kind, res in market.items():
        wc.cell(r, 1, f"{'Rent' if kind == 'rent' else 'Sale'} comps: {res['provider_label']}").font = BOLD
        wc.cell(r + 1, 1, f"Retrieved {res['retrieved_at'][:19]}; {res['area']}; {len(res['comps'])} comps"
                + ("; SYNTHETIC DEMO DATA" if res.get("synthetic") else "")).font = NOTE
        r += 2
        if not res["comps"]:
            wc.cell(r, 1, "No comps available.")
            r += 2
            continue
        cols = [k for k in res["comps"][0] if k != "url"]
        for j, k in enumerate(cols, start=1):
            wc.cell(r, j, k.replace("_", " ")).font = BOLD
        for comp in res["comps"]:
            r += 1
            for j, k in enumerate(cols, start=1):
                v = comp.get(k)
                wc.cell(r, j, "not available" if (k == "cap_rate" and v is None) else v)
        r += 3
    for j in range(1, 14):
        wc.column_dimensions[get_column_letter(j)].width = 16

    for sheet in wb.worksheets:
        sheet.oddFooter.center.text = DISCLAIMER
        sheet.oddFooter.center.size = 8
    wb.properties.title = f"Underwriting - {c['deal']['name']}"
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
