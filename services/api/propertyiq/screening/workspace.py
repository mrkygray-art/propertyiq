"""Tier 2 deal workspace: deals, uploaded documents, review, approval, analysis and flags.

Reads use the API's read-only connection; writes use piq_screening_writer, which can change only
screening_ tables. Uploaded files live in data/uploads/ (outside the web app, ignored by git).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg

from propertyiq import db
from propertyiq.config import REPO_ROOT
from propertyiq.llm import LLMProvider
from propertyiq.screening.intake import anomalies, calcs, t12 as t12mod
from propertyiq.screening.intake.extract import extract
from propertyiq.screening.intake.rentroll import RentRollExtraction
from propertyiq.screening.intake.schema import CATEGORY_SECTION, RentRollUnit, SourceRef, T12Line
from propertyiq.screening.store import _writer

UPLOAD_DIR = REPO_ROOT / "data" / "uploads"
ZERO = Decimal(0)


class WorkspaceError(ValueError):
    """Shown to the user as-is."""


def _j(v):
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, date):
        return v.isoformat()
    raise TypeError(type(v))


def dumps(obj) -> str:
    return json.dumps(obj, default=_j)


def _dec(v) -> Decimal | None:
    return None if v in (None, "") else Decimal(str(v))


# ---------------------------------------------------------------- deals

def list_deals() -> list[dict]:
    with db.connection() as conn:
        return conn.execute(
            "SELECT d.deal_id, d.name, d.updated_at, d.property_facts, "
            "(SELECT count(*) FROM screening_documents x WHERE x.deal_id = d.deal_id) AS documents, "
            "(SELECT count(*) FROM screening_flags f WHERE f.deal_id = d.deal_id AND f.status = 'open') AS open_flags "
            "FROM screening_deals d ORDER BY d.updated_at DESC").fetchall()


def get_deal(deal_id: int) -> dict:
    with db.connection() as conn:
        deal = conn.execute("SELECT * FROM screening_deals WHERE deal_id = %s", (deal_id,)).fetchone()
        if deal is None:
            raise LookupError("Deal not found")
        deal["documents"] = conn.execute(
            "SELECT document_id, kind, filename, sha256, size_bytes, status, extraction_method, error, summary, "
            "uploaded_at, approved_at FROM screening_documents WHERE deal_id = %s ORDER BY uploaded_at",
            (deal_id,)).fetchall()
    return deal


def create_deal(name: str, username: str, property_facts: dict, borrower_inputs: dict,
                guideline_set_id: int | None) -> int:
    with _writer() as conn:
        return conn.execute(
            "INSERT INTO screening_deals (name, created_by, property_facts, borrower_inputs, guideline_set_id) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING deal_id",
            (name, username, dumps(property_facts), dumps(borrower_inputs), guideline_set_id)).fetchone()["deal_id"]


def update_deal(deal_id: int, **fields) -> None:
    allowed = {"name", "property_facts", "borrower_inputs", "guideline_set_id"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed and v is not None:
            sets.append(f"{k} = %s")
            vals.append(dumps(v) if isinstance(v, (dict, list)) else v)
    if not sets:
        return
    with _writer() as conn:
        if conn.execute(f"UPDATE screening_deals SET {', '.join(sets)}, updated_at = now() WHERE deal_id = %s",
                        (*vals, deal_id)).rowcount == 0:
            raise LookupError("Deal not found")


def delete_deal(deal_id: int) -> None:
    get_deal(deal_id)
    stored = _stored_names(deal_id)
    with _writer() as conn:
        conn.execute("DELETE FROM screening_deals WHERE deal_id = %s", (deal_id,))
    for name in stored:
        _remove_file(name)


def _stored_names(deal_id: int) -> list[str]:
    with db.connection() as conn:
        return [r["stored_name"] for r in conn.execute(
            "SELECT stored_name FROM screening_documents WHERE deal_id = %s AND stored_name IS NOT NULL", (deal_id,))]


def _remove_file(stored_name: str | None) -> None:
    if stored_name:
        p = (UPLOAD_DIR / stored_name).resolve()
        if p.parent == UPLOAD_DIR.resolve() and p.exists():
            p.unlink()


# ---------------------------------------------------------------- documents

def _unit_item(u: RentRollUnit) -> tuple[dict, dict]:
    data = {k: v for k, v in asdict(u).items() if k not in ("source", "confidence", "issues")}
    return data, {"issues": u.issues}


def _line_item(ln: T12Line) -> dict:
    return {"months": ln.months, "reported_total": ln.reported_total, "section_hint": ln.section_hint,
            "issues": ln.issues}


def upload(deal_id: int, filename: str, data: bytes, expected_kind: str | None,
           provider: LLMProvider | None) -> int:
    get_deal(deal_id)
    result = extract(filename, data, expected_kind, provider)
    sha = hashlib.sha256(data).hexdigest()
    stored_name = None
    if result.error is None:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        stored_name = f"{uuid.uuid4().hex}{Path(filename).suffix.lower()[:6]}"
        (UPLOAD_DIR / stored_name).write_bytes(data)

    summary: dict = {"warnings": result.warnings}
    items: list[tuple] = []
    if result.rent_roll:
        rr: RentRollExtraction = result.rent_roll
        summary.update(table=rr.table_name, columns_found=rr.columns_found,
                       reported_totals={k: str(v) for k, v in rr.reported_totals.items()},
                       totals_check=anomalies.rent_roll_totals_check(rr.units, rr.reported_totals))
        for i, u in enumerate(rr.units, start=1):
            data_, extra = _unit_item(u)
            items.append((i, "rent_roll_unit", u.source.location, u.unit_id, None, data_, extra, u.confidence,
                          "; ".join(u.issues), "parsed"))
    if result.t12:
        x = result.t12
        summary.update(table=x.table_name, months=x.months, missing_months=x.missing_months,
                       duplicate_months=x.duplicate_months, reconciliation=x.reconciliation)
        for i, ln in enumerate(x.lines + x.subtotals, start=1):
            items.append((i, "t12_line", ln.source.location, ln.original_label, ln.category, _line_item(ln),
                          {}, ln.confidence, "; ".join(ln.issues), ln.mapped_by))

    kind = result.kind or expected_kind or "t12"
    with _writer() as conn, conn.transaction():
        doc_id = conn.execute(
            "INSERT INTO screening_documents (deal_id, kind, filename, stored_name, sha256, size_bytes, status, "
            "extraction_method, error, summary) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING document_id",
            (deal_id, kind, filename[:200], stored_name, sha, len(data), "failed" if result.error else "extracted",
             result.method, result.error, dumps(summary))).fetchone()["document_id"]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO screening_extracted_items (document_id, seq, item_type, source_ref, original_label, "
                "category, data, raw, confidence, confidence_note, mapped_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [(doc_id, seq, typ, ref, label, cat, dumps(d), dumps(extra), conf, note, mb)
                 for seq, typ, ref, label, cat, d, extra, conf, note, mb in items])
        conn.execute("UPDATE screening_deals SET updated_at = now() WHERE deal_id = %s", (deal_id,))
    return doc_id


def get_document(doc_id: int) -> dict:
    with db.connection() as conn:
        doc = conn.execute("SELECT document_id, deal_id, kind, filename, status, extraction_method, error, summary, "
                           "uploaded_at, approved_at FROM screening_documents WHERE document_id = %s",
                           (doc_id,)).fetchone()
        if doc is None:
            raise LookupError("Document not found")
        doc["items"] = conn.execute(
            "SELECT item_id, seq, item_type, source_ref, original_label, category, data, raw, confidence, "
            "confidence_note, mapped_by, edited FROM screening_extracted_items WHERE document_id = %s ORDER BY seq",
            (doc_id,)).fetchall()
    return doc


UNIT_FIELDS = {"unit_id", "unit_type", "beds", "baths", "sqft", "status", "current_rent", "market_rent",
               "lease_start", "lease_end", "month_to_month", "concessions", "deposit"}


def edit_item(doc_id: int, item_id: int, category: str | None, data: dict | None) -> None:
    doc = get_document(doc_id)
    if doc["status"] == "approved":
        raise WorkspaceError("This document is already approved. Un-approve it to make changes.")
    item = next((i for i in doc["items"] if i["item_id"] == item_id), None)
    if item is None:
        raise LookupError("Item not found")
    new = dict(item["data"])
    if item["item_type"] == "rent_roll_unit" and data:
        for k, v in data.items():
            if k in UNIT_FIELDS:
                new[k] = v
    if item["item_type"] == "t12_line" and data and "months" in data:
        new["months"] = {m: (None if v in (None, "") else str(Decimal(str(v)))) for m, v in data["months"].items()
                         if m in item["data"]["months"]}
    if category is not None and category not in CATEGORY_SECTION:
        raise WorkspaceError("Unknown category.")
    with _writer() as conn:
        conn.execute("UPDATE screening_extracted_items SET data = %s, category = COALESCE(%s, category), edited = true, "
                     "mapped_by = CASE WHEN %s::text IS NULL THEN mapped_by ELSE 'reviewer' END "
                     "WHERE item_id = %s AND document_id = %s",
                     (dumps(new), category, category, item_id, doc_id))


def approve(doc_id: int) -> None:
    """Copy reviewed items into the normalized tables. Replaces the deal's earlier approved document of this kind."""
    doc = get_document(doc_id)
    if doc["status"] == "failed":
        raise WorkspaceError("This upload couldn't be read, so there's nothing to approve.")
    unmapped = [i["original_label"] for i in doc["items"] if i["category"] == "unmapped"]
    if unmapped:
        raise WorkspaceError(f"Choose a category for: {', '.join(unmapped)}.")
    deal_id = doc["deal_id"]
    with _writer() as conn, conn.transaction():
        conn.execute("UPDATE screening_documents SET status = 'extracted', approved_at = NULL "
                     "WHERE deal_id = %s AND kind = %s AND status = 'approved'", (deal_id, doc["kind"]))
        table = "screening_rent_roll_units" if doc["kind"] == "rent_roll" else "screening_t12_lines"
        conn.execute(f"DELETE FROM {table} WHERE deal_id = %s", (deal_id,))
        for it in doc["items"]:
            d = it["data"]
            if it["item_type"] == "rent_roll_unit":
                conn.execute(
                    "INSERT INTO screening_rent_roll_units (deal_id, document_id, unit_id, unit_type, beds, baths, sqft, "
                    "status, current_rent, market_rent, lease_start, lease_end, month_to_month, concessions, deposit, "
                    "source_ref) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (deal_id, doc_id, d.get("unit_id"), d.get("unit_type"), d.get("beds"), d.get("baths"), d.get("sqft"),
                     d.get("status"), d.get("current_rent"), d.get("market_rent"), d.get("lease_start"),
                     d.get("lease_end"), bool(d.get("month_to_month")), d.get("concessions"), d.get("deposit"),
                     f"{doc['filename']}, {it['source_ref']}"))
            else:
                conn.execute(
                    "INSERT INTO screening_t12_lines (deal_id, document_id, seq, original_label, category, months, "
                    "reported_total, source_ref) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (deal_id, doc_id, it["seq"], it["original_label"], it["category"], dumps(d["months"]),
                     d.get("reported_total"), f"{doc['filename']}, {it['source_ref']}"))
        conn.execute("UPDATE screening_documents SET status = 'approved', approved_at = now() WHERE document_id = %s",
                     (doc_id,))
        conn.execute("UPDATE screening_deals SET updated_at = now() WHERE deal_id = %s", (deal_id,))


def unapprove(doc_id: int) -> None:
    doc = get_document(doc_id)
    table = "screening_rent_roll_units" if doc["kind"] == "rent_roll" else "screening_t12_lines"
    with _writer() as conn, conn.transaction():
        conn.execute(f"DELETE FROM {table} WHERE document_id = %s", (doc_id,))
        conn.execute("UPDATE screening_documents SET status = 'extracted', approved_at = NULL WHERE document_id = %s",
                     (doc_id,))


def delete_document(doc_id: int) -> None:
    """Remove the uploaded file and everything extracted from it."""
    with db.connection() as conn:
        row = conn.execute("SELECT stored_name FROM screening_documents WHERE document_id = %s", (doc_id,)).fetchone()
    if row is None:
        raise LookupError("Document not found")
    with _writer() as conn:
        conn.execute("DELETE FROM screening_documents WHERE document_id = %s", (doc_id,))
    _remove_file(row["stored_name"])


# ---------------------------------------------------------------- analysis

def approved_data(deal_id: int) -> tuple[list[RentRollUnit], list[T12Line], list[T12Line], dict]:
    with db.connection() as conn:
        units = conn.execute("SELECT * FROM screening_rent_roll_units WHERE deal_id = %s ORDER BY unit_id",
                             (deal_id,)).fetchall()
        lines = conn.execute("SELECT * FROM screening_t12_lines WHERE deal_id = %s ORDER BY seq", (deal_id,)).fetchall()
        docs = conn.execute("SELECT document_id, kind, filename, summary FROM screening_documents "
                            "WHERE deal_id = %s AND status = 'approved'", (deal_id,)).fetchall()
    rr_units = [RentRollUnit(
        unit_id=u["unit_id"], unit_type=u["unit_type"], beds=u["beds"], baths=u["baths"], sqft=u["sqft"],
        status=u["status"], current_rent=u["current_rent"], market_rent=u["market_rent"],
        lease_start=u["lease_start"], lease_end=u["lease_end"], month_to_month=u["month_to_month"],
        concessions=u["concessions"], deposit=u["deposit"], source=SourceRef(*_split_ref(u["source_ref"]), "approved"),
    ) for u in units]
    all_lines = [T12Line(ln["original_label"], ln["category"], {k: _dec(v) for k, v in ln["months"].items()},
                         ln["reported_total"], SourceRef(*_split_ref(ln["source_ref"]), "approved", ln["original_label"]))
                 for ln in lines]
    subs = [ln for ln in all_lines if ln.category in t12mod.SUBTOTALS]
    return rr_units, [ln for ln in all_lines if ln.category not in t12mod.SUBTOTALS], subs, {d["kind"]: d for d in docs}


def _split_ref(ref: str) -> tuple[str, str]:
    file, _, loc = ref.partition(", ")
    return file, loc


def benchmarks() -> list[dict]:
    with db.connection() as conn:
        return conn.execute("SELECT property_type, category, basis, low, typical, high, source, last_reviewed "
                            "FROM screening_benchmarks").fetchall()


def analyze(deal_id: int, as_of: date | None = None) -> dict:
    """Metrics and flags from approved data. Flags are saved; a dismissed flag stays dismissed."""
    deal = get_deal(deal_id)
    facts = {**deal["borrower_inputs"], **deal["property_facts"]}
    units, lines, subs, docs = approved_data(deal_id)
    rr = calcs.rent_roll_metrics(units, as_of or date.today()) if units else None
    t12 = None
    t12_info = None
    if lines:
        months = sorted({m for ln in lines for m in ln.months})
        sqft = Decimal(str(facts["sqft"])) if facts.get("sqft") else None
        t12 = calcs.t12_metrics(lines, months, units=facts.get("units") or (rr.unit_count if rr else None), sqft=sqft)
        x = t12mod.T12Extraction(lines, subs, months, "", "")
        last = max(months)
        x.missing_months = [m for m in (t12mod._add_month(last, -i) for i in range(11, -1, -1)) if m not in months]
        t12mod.reconcile(x)
        t12_info = {"missing_months": x.missing_months,
                    "duplicate_months": docs.get("t12", {}).get("summary", {}).get("duplicate_months", []),
                    "reconciliation": x.reconciliation, "source": docs.get("t12", {}).get("filename", "T-12")}
    if units and docs.get("rent_roll", {}).get("summary", {}).get("totals_check"):
        t12_info = t12_info or {}
        t12_info["rent_roll_totals_mismatch"] = docs["rent_roll"]["summary"]["totals_check"]
    facts.setdefault("property_type", deal["borrower_inputs"].get("property_type", "multifamily"))
    flags = anomalies.check(units or None, rr, docs.get("rent_roll", {}).get("filename"), lines or None, t12, t12_info,
                            facts, benchmarks())
    save_flags(deal_id, flags, prefix="A")
    return {"rent_roll": rr, "t12": t12, "t12_info": t12_info, "flags": load_flags(deal_id)}


def save_flags(deal_id: int, flags: list[anomalies.Flag], prefix: str) -> None:
    """Upsert this family of flags (A, M or R); remove ones that no longer fire; keep dismissals."""
    with _writer() as conn, conn.transaction():
        keep = [f.fingerprint for f in flags]
        conn.execute("DELETE FROM screening_flags WHERE deal_id = %s AND rule_id LIKE %s AND NOT (fingerprint = ANY(%s))",
                     (deal_id, f"{prefix}%", keep))
        for f in flags:
            conn.execute(
                "INSERT INTO screening_flags (deal_id, rule_id, fingerprint, severity, title, explanation, evidence, "
                "sources, suggested_action) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (deal_id, fingerprint) DO UPDATE SET severity = EXCLUDED.severity, title = EXCLUDED.title, "
                "explanation = EXCLUDED.explanation, evidence = EXCLUDED.evidence, sources = EXCLUDED.sources, "
                "suggested_action = EXCLUDED.suggested_action, updated_at = now()",
                (deal_id, f.rule_id, f.fingerprint, f.severity, f.title, f.explanation, dumps(f.evidence),
                 dumps(f.sources), f.suggested_action))


def load_flags(deal_id: int) -> list[dict]:
    order = {"high": 0, "medium": 1, "low": 2}
    with db.connection() as conn:
        rows = conn.execute("SELECT flag_id, rule_id, fingerprint, severity, title, explanation, evidence, sources, "
                            "suggested_action, status, dismissal_note FROM screening_flags WHERE deal_id = %s",
                            (deal_id,)).fetchall()
    return sorted(rows, key=lambda r: (r["status"] != "open", order[r["severity"]], r["rule_id"], r["fingerprint"]))


def set_flag_status(flag_id: int, status: str, note: str | None) -> None:
    if status == "dismissed" and not (note and note.strip()):
        raise WorkspaceError("Add a note saying why the flag is being dismissed.")
    with _writer() as conn:
        if conn.execute("UPDATE screening_flags SET status = %s, dismissal_note = %s, updated_at = now() "
                        "WHERE flag_id = %s", (status, note if status == "dismissed" else None, flag_id)).rowcount == 0:
            raise LookupError("Flag not found")


def is_db_error(exc: Exception) -> bool:
    return isinstance(exc, psycopg.Error)


# ---------------------------------------------------------------- market (Milestone 6)

def _deal_noi_and_units(deal_id: int, deal: dict) -> tuple[Decimal | None, int | None, dict | None]:
    """Recurring NOI from the approved T-12 (annualized), units, and rent-roll unit-type summary."""
    units, lines, _subs, _docs = approved_data(deal_id)
    facts = {**deal["borrower_inputs"], **deal["property_facts"]}
    n_units = facts.get("units") or (len(units) if units else None)
    noi = None
    if lines:
        months = sorted({m for ln in lines for m in ln.months})
        noi = calcs.t12_metrics(lines, months, units=n_units).noi
    by_type = None
    if units:
        rr = calcs.rent_roll_metrics(units, date.today())
        by_type = {t.unit_type: {"avg_rent": t.avg_rent, "avg_market": t.avg_market} for t in rr.by_type}
    return noi, (int(n_units) if n_units else None), by_type


def market(deal_id: int, kind: str, address: str, city: str, radius: float, max_age: int,
           provider: str = "synthetic", refresh: bool = False) -> dict:
    from propertyiq.screening import market as mk, settings_store
    deal = get_deal(deal_id)
    noi, n_units, by_type = _deal_noi_and_units(deal_id, deal)
    q = mk.CompQuery(kind, address, city, radius, max_age, sorted(by_type) if by_type and kind == "rent" else [],
                     deal["property_facts"].get("property_type", "multifamily"))
    stale_days = int(settings_store.setting("market_stale_days", "30"))
    res = mk.get_comps(q, provider, refresh, stale_days)
    if res.get("error"):
        raise WorkspaceError(res["error"])
    out: dict = {"result": res, "summary": None}
    if kind == "rent":
        subject = {k: {kk: (float(vv) if vv is not None else None) for kk, vv in v.items()} for k, v in (by_type or {}).items()}
        out["summary"] = mk.rent_summary(res["comps"], subject)
    else:
        summary = mk.sale_summary(res["comps"])
        out["summary"] = summary
        facts = deal["property_facts"]
        price = _dec(facts.get("purchase_price")) or _dec(deal["borrower_inputs"].get("value"))
        flags = mk.m1_flags(summary, price, n_units, noi, Decimal(settings_store.setting("m1_price_gap_pct", "0.20")),
                            Decimal(settings_store.setting("m1_cap_gap_points", "0.01")), res["provider_label"],
                            res["retrieved_at"]) if res["comps"] else []
        save_flags(deal_id, flags, prefix="M")
        out["implied"] = {"price": price, "units": n_units, "noi": noi,
                          "price_per_unit": price / n_units if price and n_units else None,
                          "cap_rate": noi / price if noi and price else None}
    market_last = {**deal["property_facts"].get("market_last", {}), kind: {"provider": provider, "query_key": q.key()}}
    with _writer() as conn:
        conn.execute("UPDATE screening_deals SET property_facts = property_facts || %s, updated_at = now() "
                     "WHERE deal_id = %s",
                     (dumps({"address": address, "city": city, "market_last": market_last}), deal_id))
    return out


def last_market(deal_id: int) -> dict[str, dict]:
    """The most recent cached rent and sale comp results for this deal (for exports)."""
    from propertyiq.screening import market as mk
    last = get_deal(deal_id)["property_facts"].get("market_last", {})
    out = {}
    with db.connection() as conn:
        for kind, k in last.items():
            row = conn.execute("SELECT results, retrieved_at FROM screening_market_cache WHERE provider = %s AND "
                               "query_key = %s", (k["provider"], k["query_key"])).fetchone()
            if row:
                res = row["results"]
                res["summary"] = mk.sale_summary(res["comps"]) if kind == "sale" else None
                out[kind] = res
    return out


# ---------------------------------------------------------------- California red flags (Milestone 7)

def condition_items(deal_id: int) -> list[dict]:
    with db.connection() as conn:
        return conn.execute("SELECT item_key, details, estimated_cost FROM screening_condition_items WHERE deal_id = %s "
                            "ORDER BY item_key", (deal_id,)).fetchall()


def california(deal_id: int, facts: dict | None = None, condition: list[dict] | None = None,
               as_of: date | None = None) -> list[dict]:
    from propertyiq.screening import ca_flags, settings_store
    get_deal(deal_id)
    with _writer() as conn, conn.transaction():
        if facts is not None:
            conn.execute("UPDATE screening_deals SET property_facts = property_facts || %s, updated_at = now() "
                         "WHERE deal_id = %s", (dumps(facts), deal_id))
        if condition is not None:
            conn.execute("DELETE FROM screening_condition_items WHERE deal_id = %s", (deal_id,))
            for c in condition:
                details = {k: v for k, v in (c.get("details") or {}).items() if v not in (None, "")}
                if details or c.get("estimated_cost") not in (None, ""):
                    conn.execute("INSERT INTO screening_condition_items (deal_id, item_key, details, estimated_cost) "
                                 "VALUES (%s, %s, %s, %s)",
                                 (deal_id, c["item_key"], dumps(details), c.get("estimated_cost") or None))
    deal = get_deal(deal_id)
    all_facts = {**deal["borrower_inputs"], **deal["property_facts"]}
    _units, lines, _s, _d = approved_data(deal_id)
    t12_annual = noi = None
    if lines:
        months = sorted({m for ln in lines for m in ln.months})
        m = calcs.t12_metrics(lines, months, units=all_facts.get("units"))
        t12_annual, noi = m.annual_by_category, m.noi
    bench = next((b for b in benchmarks() if b["property_type"] == all_facts.get("property_type", "multifamily")
                  and b["category"] == "repairs_maintenance" and b["basis"] == "per_unit"), None)
    flags = ca_flags.evaluate(
        all_facts, settings_store.list_rules(), t12_annual, noi, condition_items(deal_id),
        settings_store.list_condition_life(), bench["low"] if bench else None,
        Decimal(settings_store.setting("example_tax_rate", "0.012")), as_of)
    save_flags(deal_id, flags, prefix="R")
    return load_flags(deal_id)


# ---------------------------------------------------------------- borrower vs underwritten (Milestone 8)

LOAN_FIELDS = ("loan_amount", "interest_rate_pct", "amortization_years", "interest_only_years", "term_years")


def _case_inputs(deal_id: int) -> dict:
    """Everything the comparison needs, from approved data only."""
    deal = get_deal(deal_id)
    facts = {**deal["borrower_inputs"], **deal["property_facts"]}
    units, lines, _subs, _docs = approved_data(deal_id)
    n_units = facts.get("units") or (len(units) if units else None)
    t12 = None
    if lines:
        months = sorted({m for ln in lines for m in ln.months})
        t12 = calcs.t12_metrics(lines, months, units=n_units)
    rr = calcs.rent_roll_metrics(units, date.today()) if units else None
    value = _dec(facts.get("value")) or _dec(facts.get("purchase_price"))
    return {"deal": deal, "facts": facts, "units": int(n_units) if n_units else None, "t12": t12, "rr": rr,
            "value": value}


def list_adjustments(deal_id: int) -> list[dict]:
    with db.connection() as conn:
        return conn.execute("SELECT * FROM screening_adjustments WHERE deal_id = %s ORDER BY adjustment_id",
                            (deal_id,)).fetchall()


def suggest_adjustments(deal_id: int) -> int:
    """Turn open flags into PROPOSED adjustments (computed from the data, not parsed from flag text).
    Existing adjustments for the same flag and line are left alone. Returns how many were added."""
    from propertyiq.screening import compare, settings_store
    ci = _case_inputs(deal_id)
    t12, rr, units, facts = ci["t12"], ci["rr"], ci["units"], ci["facts"]
    borrower, _src = compare.borrower_lines(t12, facts, ci["value"])
    open_flags = {f["fingerprint"]: f for f in load_flags(deal_id) if f["status"] == "open"}
    existing = {(a["flag_fingerprint"], a["line_item"]) for a in list_adjustments(deal_id)}
    bench = {(b["category"], b["basis"]): b for b in benchmarks()
             if b["property_type"] == facts.get("property_type", "multifamily")}
    proposals: list[tuple] = []

    def propose(fp: str, line: str, value: Decimal, reason: str, note: str) -> None:
        if fp in open_flags and (fp, line) not in existing and value is not None:
            proposals.append((line, borrower.get(line), value, reason, note, fp))

    if t12 is not None:
        a = t12.annual_by_category
        gross = borrower["gross_potential_rent"] + borrower["other_income"]
        if rr is not None and rr.physical_occupancy is not None and gross:
            credit = abs(a.get("concessions", ZERO)) + abs(a.get("bad_debt", ZERO))
            vac = (1 - rr.physical_occupancy) * borrower["gross_potential_rent"]
            proposed = max(borrower["vacancy_pct"], (vac + credit) / gross)
            propose("A1:vacancy", "vacancy_pct", proposed, "VACANCY",
                    f"Rent roll shows {(1 - rr.physical_occupancy) * 100:.1f}% of units empty; vacancy and credit loss "
                    f"set to {proposed * 100:.1f}% of recurring gross income (was {borrower['vacancy_pct'] * 100:.1f}%).")
        if rr is not None:
            propose("A2:rent", "gross_potential_rent", rr.gross_potential_annual, "MARKET_RENT",
                    "Gross potential rent from the current rent roll (in-place rent on occupied units plus market rent "
                    "on vacant units, x 12) instead of the T-12.")
        for fp in [f for f in open_flags if f.startswith("A3:label:")]:
            propose(fp, "non_recurring_income", ZERO, "ONE_TIME_ITEM_REMOVED",
                    f"Removed one-time income ({fp.split(':', 2)[2]}) that won't repeat.")
        units_d = Decimal(units) if units else None
        for fp, line in (("A4:insurance", "insurance"), ("A4:repairs_maintenance", "repairs_maintenance"),
                         ("A4:utilities", "utilities"), ("A4:administrative", "administrative")):
            b = bench.get((line, "per_unit"))
            if b and units_d and b.get("typical") is not None:
                propose(fp, line, Decimal(str(b["typical"])) * units_d, "EXPENSE_BELOW_BENCHMARK",
                        f"Underwritten at the typical ${b['typical']:,.0f} per unit (benchmark: {b['source']}).")
        b = bench.get(("management_fee", "pct_egi"))
        if b and b.get("typical") is not None:
            egi = compare.totals({**borrower, "non_recurring_income": ZERO}).egi
            rate = Decimal(str(b["typical"]))
            propose("A5:management_fee", "management_fee", egi * rate, "MANAGEMENT_FEE",
                    f"Market management fee at {rate * 100:.1f}% of recurring effective gross income (${egi:,.0f}).")
    price = _dec(facts.get("purchase_price"))
    if price and facts.get("transaction") == "purchase":
        rate = Decimal(settings_store.setting("example_tax_rate", "0.012"))
        propose("R1:prop13", "real_estate_taxes", price * rate, "TAX_REASSESSMENT",
                f"Prop 13 reassessment on purchase: ${price:,.0f} x {rate * 100:.2f}% example rate. Confirm the county rate.")
    if units:
        per_unit = Decimal(settings_store.setting("example_reserve_per_unit", "300"))
        total = next((e["value"] for e in open_flags.get("R5:capex_total", {}).get("evidence", [])
                      if e["label"] == "Total of your estimates"), None)
        propose("R5:capex_total", "reserves", per_unit * units, "DEFERRED_MAINTENANCE",
                f"Replacement reserve of ${per_unit:,.0f} per unit (example; set your own). Deferred maintenance "
                f"estimate {total or 'n/a'}: consider a holdback at closing, which isn't part of NOI.")
    m1 = open_flags.get("M1:price_per_unit")
    if m1 and units:
        med = next((e["value"] for e in m1["evidence"] if e["label"] == "Comp median price per unit"), None)
        if med:
            comp_value = Decimal(med.replace("$", "").replace(",", "")) * units
            if ci["value"] is None or comp_value < ci["value"]:
                propose("M1:price_per_unit", "value", comp_value, "OTHER",
                        f"Value at the sale-comp median {med} per unit x {units} units for LTV, below the price. "
                        "Comps are support, not an appraisal.")
    if proposals:
        with _writer() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO screening_adjustments (deal_id, line_item, borrower_value, underwritten_value, reason_code, "
                    "note, flag_fingerprint, status) VALUES (%s,%s,%s,%s,%s,%s,%s,'proposed')",
                    [(deal_id, line, bv, uv, reason, note, fp) for line, bv, uv, reason, note, fp in proposals])
    return len(proposals)


def add_adjustment(deal_id: int, line_item: str, value: Decimal, reason: str, note: str) -> None:
    from propertyiq.screening import compare
    if line_item not in compare.LINE_KEYS:
        raise WorkspaceError("Unknown line item.")
    if reason not in compare.REASON_CODES:
        raise WorkspaceError("Choose a reason code.")
    if not note.strip():
        raise WorkspaceError("Every adjustment needs a note explaining it.")
    ci = _case_inputs(deal_id)
    borrower, _ = compare.borrower_lines(ci["t12"], ci["facts"], ci["value"])
    with _writer() as conn:
        conn.execute("INSERT INTO screening_adjustments (deal_id, line_item, borrower_value, underwritten_value, "
                     "reason_code, note, status) VALUES (%s,%s,%s,%s,%s,%s,'proposed')",
                     (deal_id, line_item, borrower.get(line_item), value, reason, note.strip()))


def update_adjustment(adjustment_id: int, fields: dict) -> None:
    from propertyiq.screening import compare
    with db.connection() as conn:
        adj = conn.execute("SELECT * FROM screening_adjustments WHERE adjustment_id = %s", (adjustment_id,)).fetchone()
    if adj is None:
        raise LookupError("Adjustment not found")
    new = {**adj, **{k: v for k, v in fields.items() if v is not None}}
    if new["reason_code"] not in compare.REASON_CODES:
        raise WorkspaceError("Choose a reason code.")
    if not str(new["note"]).strip():
        raise WorkspaceError("Every adjustment needs a note explaining it.")
    with _writer() as conn, conn.transaction():
        if new["status"] == "approved":
            # One approved adjustment per line: approving this one sets any other approved one back to proposed.
            conn.execute("UPDATE screening_adjustments SET status = 'proposed', updated_at = now() WHERE deal_id = %s "
                         "AND line_item = %s AND status = 'approved' AND adjustment_id <> %s",
                         (adj["deal_id"], adj["line_item"], adjustment_id))
        conn.execute("UPDATE screening_adjustments SET underwritten_value = %s, reason_code = %s, note = %s, status = %s, "
                     "updated_at = now() WHERE adjustment_id = %s",
                     (new["underwritten_value"], new["reason_code"], str(new["note"]).strip(), new["status"], adjustment_id))


def delete_adjustment(adjustment_id: int) -> None:
    with _writer() as conn:
        if conn.execute("DELETE FROM screening_adjustments WHERE adjustment_id = %s", (adjustment_id,)).rowcount == 0:
            raise LookupError("Adjustment not found")


def save_loan(deal_id: int, loan: dict, guideline_set_id: int | None) -> None:
    clean = {k: loan.get(k) for k in LOAN_FIELDS + ("value",) if k in loan}
    update_deal(deal_id, borrower_inputs={**get_deal(deal_id)["borrower_inputs"], **clean},
                guideline_set_id=guideline_set_id)


def comparison(deal_id: int) -> dict:
    from propertyiq.screening import compare, store
    ci = _case_inputs(deal_id)
    deal, facts = ci["deal"], ci["facts"]
    borrower, source = compare.borrower_lines(ci["t12"], facts, ci["value"])
    adjustments = list_adjustments(deal_id)
    underwritten = compare.apply(borrower, adjustments)
    row = store.get_set(deal["guideline_set_id"]) if deal.get("guideline_set_id") else (store.list_sets() or [None])[0]
    g = store.to_guideline_set(row["name"], row["rules"])
    result = compare.run(borrower, underwritten, deal["borrower_inputs"], g, deal["name"],
                         facts.get("property_type", "multifamily"))
    return {"deal": deal, "source": source, "borrower": borrower, "underwritten": underwritten,
            "adjustments": adjustments, "guideline_set_id": row["set_id"], **result}
