"""Tier 2 API: deals, document upload and review, analysis and flags (/api/screening/deals...).

Mounted inside the Tier 1 screening router, so the same sign-in and the same PIQ_SCREENING_ENABLED
switch apply.

Access (Phase 0.5, see propertyiq/access.py): every deal belongs to one fund. A deal, document, flag or
adjustment in a fund the user isn't granted gets the same 404 as one that doesn't exist. Then the role
decides what the user may change (403 otherwise). Refusals and successful changes go to the access audit.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from propertyiq import access, audit
from propertyiq.screening import settings_store, workspace
from propertyiq.screening.intake.schema import CATEGORY_MEANING, CATEGORY_SECTION, T12_CATEGORIES
from propertyiq.screening.intake.tables import MAX_BYTES

Id = Annotated[int, Path(ge=1)]
NOT_FOUND = {"deal": "Deal not found", "document": "Document not found", "flag": "Flag not found",
             "adjustment": "Adjustment not found"}


class DealIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    property_facts: dict[str, Any] = Field(default_factory=dict)
    borrower_inputs: dict[str, Any] = Field(default_factory=dict)
    guideline_set_id: int | None = None
    # Required when the user can see more than one fund; otherwise their one fund is used.
    fund_id: str | None = Field(None, pattern=r"^FUND-[A-Z0-9]{1,10}$")


class DealPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    property_facts: dict[str, Any] | None = None
    borrower_inputs: dict[str, Any] | None = None
    guideline_set_id: int | None = None


class ItemPatch(BaseModel):
    category: str | None = None
    data: dict[str, Any] | None = None


class LoanIn(BaseModel):
    value: Decimal | None = Field(None, ge=0, le=10_000_000_000)
    loan_amount: Decimal | None = Field(None, ge=0, le=10_000_000_000)
    interest_rate_pct: Decimal | None = Field(None, ge=0, le=100)
    amortization_years: int | None = Field(None, ge=1, le=50)
    interest_only_years: Decimal | None = Field(None, ge=0, le=50)
    term_years: Decimal | None = Field(None, ge=0, le=50)
    guideline_set_id: int | None = None


class AdjustmentIn(BaseModel):
    line_item: str = Field(pattern=r"^[a-z_]{1,40}$")
    underwritten_value: Decimal = Field(ge=-10_000_000_000, le=10_000_000_000)
    reason_code: str = Field(max_length=40)
    note: str = Field(min_length=1, max_length=1000)


class AdjustmentPatch(BaseModel):
    underwritten_value: Decimal | None = Field(None, ge=-10_000_000_000, le=10_000_000_000)
    reason_code: str | None = Field(None, max_length=40)
    note: str | None = Field(None, max_length=1000)
    status: Literal["proposed", "approved", "rejected"] | None = None


class MarketIn(BaseModel):
    kind: Literal["rent", "sale"]
    address: str = Field(min_length=1, max_length=200)
    city: str = Field(min_length=1, max_length=80)
    radius_miles: float = Field(1.0, gt=0, le=25)
    max_age_months: int = Field(12, ge=1, le=60)
    provider: str = Field("synthetic", max_length=30)
    refresh: bool = False


class ConditionIn(BaseModel):
    item_key: str = Field(pattern=r"^[a-z0-9_]{1,60}$")
    details: dict[str, str | int | float | None] = Field(default_factory=dict)
    estimated_cost: Decimal | None = Field(None, ge=0, le=100_000_000)


class CaliforniaIn(BaseModel):
    facts: dict[str, Any] = Field(default_factory=dict)
    condition: list[ConditionIn] = Field(default_factory=list, max_length=50)


class BenchmarkIn(BaseModel):
    property_type: Literal["multifamily", "office", "retail", "industrial", "mixed_use", "other"]
    category: str = Field(min_length=1, max_length=40, pattern=r"^[a-z_]+$")
    basis: Literal["per_unit", "per_sf", "pct_egi"]
    low: Decimal | None = None
    typical: Decimal | None = None
    high: Decimal | None = None
    source: str = Field(min_length=1, max_length=300)
    last_reviewed: date
    is_example: bool = False


class RuleIn(BaseModel):
    description: str = Field(min_length=1, max_length=2000)
    source_url: str | None = Field(None, max_length=500)
    last_verified: date | None = None
    verified: bool = False
    notes: str = Field("", max_length=1000)
    trigger: dict[str, Any] | str = Field(default_factory=dict)


class LifeIn(BaseModel):
    typical_life: int | None = Field(None, ge=1, le=200)
    flag_if: str | None = Field(None, max_length=200)
    source: str = Field(min_length=1, max_length=300)
    last_reviewed: date
    is_example: bool = False


class SettingIn(BaseModel):
    value: str = Field(min_length=1, max_length=40)


class FlagStatus(BaseModel):
    status: Literal["open", "dismissed"]
    note: str | None = Field(None, max_length=1000)


def jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if hasattr(v, "__dataclass_fields__"):
        return jsonable(asdict(v))
    return v


def build_tier2_router(provider_getter, user_dependency) -> APIRouter:
    """provider_getter() returns the local LLM provider (looked up per call so tests can swap it)."""
    r = APIRouter()

    def guard(fn, *args, **kw):
        try:
            return fn(*args, **kw)
        except LookupError as e:
            raise HTTPException(404, str(e)) from None
        except workspace.WorkspaceError as e:
            raise HTTPException(400, str(e)) from None

    def scoped(user, kind: str, record_id: int, permission: str, action: str) -> dict:
        """Fund check first (another fund's record looks exactly like a missing one), then the role check."""
        o = workspace.owner(kind, record_id)
        access.require_fund(user, o["fund_id"] if o else None, action, kind, record_id, NOT_FOUND[kind])
        access.require(user, permission, action, kind, record_id, o["fund_id"])
        return o

    def done(user, action: str, kind: str, record_id, o: dict) -> None:
        audit.allowed(user, action, kind, record_id, o["fund_id"])

    def policy(user, action: str) -> None:
        access.require(user, "policy", action, "policy")

    @r.get("/categories")
    def categories() -> list[dict]:
        return [{"key": k, "section": s, "meaning": m} for k, s, m in T12_CATEGORIES]

    @r.get("/deals")
    def deals(user=Depends(user_dependency)) -> list[dict]:
        return jsonable(workspace.list_deals(user.portfolios))

    @r.post("/deals")
    def create(body: DealIn, user=Depends(user_dependency)) -> dict:
        access.require(user, "work_deals", "deal.create", "deal")
        fund = body.fund_id or (user.portfolios[0] if len(user.portfolios) == 1 else None)
        if fund is None:
            raise HTTPException(400, "Choose the fund this deal belongs to.")
        if fund not in user.portfolios:
            audit.refused(user, "deal.create", "fund", "deal", None, fund)
            raise HTTPException(403, f"You can't add deals to {fund}.")
        deal_id = workspace.create_deal(body.name.strip(), user.username, body.property_facts, body.borrower_inputs,
                                        body.guideline_set_id, fund)
        audit.allowed(user, "deal.create", "deal", deal_id, fund)
        return jsonable(workspace.get_deal(deal_id))

    @r.get("/deals/{deal_id}")
    def deal(deal_id: Id, user=Depends(user_dependency)) -> dict:
        scoped(user, "deal", deal_id, "view", "deal.view")
        return jsonable(guard(workspace.get_deal, deal_id))

    @r.patch("/deals/{deal_id}")
    def patch(deal_id: Id, body: DealPatch, user=Depends(user_dependency)) -> dict:
        o = scoped(user, "deal", deal_id, "work_deals", "deal.edit")
        guard(workspace.update_deal, deal_id, **body.model_dump(exclude_none=True))
        done(user, "deal.edit", "deal", deal_id, o)
        return jsonable(workspace.get_deal(deal_id))

    @r.delete("/deals/{deal_id}")
    def delete(deal_id: Id, user=Depends(user_dependency)) -> dict:
        o = scoped(user, "deal", deal_id, "work_deals", "deal.delete")
        guard(workspace.delete_deal, deal_id)
        done(user, "deal.delete", "deal", deal_id, o)
        return {"deleted": deal_id}

    @r.post("/deals/{deal_id}/documents")
    async def upload(deal_id: Id, file: UploadFile = File(...),
                     kind: Literal["auto", "rent_roll", "t12"] = Form("auto"), user=Depends(user_dependency)) -> dict:
        o = scoped(user, "deal", deal_id, "work_deals", "document.upload")
        data = await file.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise HTTPException(413, f"The file is larger than the {MAX_BYTES // 1_048_576} MB limit.")
        doc_id = guard(workspace.upload, deal_id, file.filename or "upload", data,
                       None if kind == "auto" else kind, provider_getter())
        done(user, "document.upload", "document", doc_id, o)
        return jsonable(workspace.get_document(doc_id))

    @r.get("/documents/{doc_id}")
    def document(doc_id: Id, user=Depends(user_dependency)) -> dict:
        scoped(user, "document", doc_id, "view", "document.view")
        return jsonable(guard(workspace.get_document, doc_id))

    @r.patch("/documents/{doc_id}/items/{item_id}")
    def edit(doc_id: Id, item_id: Id, body: ItemPatch, user=Depends(user_dependency)) -> dict:
        o = scoped(user, "document", doc_id, "work_deals", "document.edit_item")
        guard(workspace.edit_item, doc_id, item_id, body.category, body.data)
        done(user, "document.edit_item", "document", doc_id, o)
        return jsonable(workspace.get_document(doc_id))

    @r.post("/documents/{doc_id}/approve")
    def approve(doc_id: Id, user=Depends(user_dependency)) -> dict:
        o = scoped(user, "document", doc_id, "work_deals", "document.approve")
        guard(workspace.approve, doc_id)
        done(user, "document.approve", "document", doc_id, o)
        return jsonable(workspace.get_document(doc_id))

    @r.post("/documents/{doc_id}/unapprove")
    def unapprove(doc_id: Id, user=Depends(user_dependency)) -> dict:
        o = scoped(user, "document", doc_id, "work_deals", "document.unapprove")
        guard(workspace.unapprove, doc_id)
        done(user, "document.unapprove", "document", doc_id, o)
        return jsonable(workspace.get_document(doc_id))

    @r.delete("/documents/{doc_id}")
    def delete_document(doc_id: Id, user=Depends(user_dependency)) -> dict:
        o = scoped(user, "document", doc_id, "work_deals", "document.delete")
        guard(workspace.delete_document, doc_id)
        done(user, "document.delete", "document", doc_id, o)
        return {"deleted": doc_id}

    @r.get("/deals/{deal_id}/analysis")
    def analysis(deal_id: Id, as_of: date | None = None, user=Depends(user_dependency)) -> dict:
        # Viewers may open this too. It refreshes the deal's computed flags (derived data, not a user change).
        scoped(user, "deal", deal_id, "view", "deal.view")
        a = guard(workspace.analyze, deal_id, as_of)
        return jsonable({
            "rent_roll": a["rent_roll"], "t12": a["t12"], "t12_info": a["t12_info"], "flags": a["flags"],
            "category_meaning": CATEGORY_MEANING, "category_section": CATEGORY_SECTION,
        })

    @r.post("/flags/{flag_id}/status")
    def flag_status(flag_id: Id, body: FlagStatus, user=Depends(user_dependency)) -> dict:
        o = scoped(user, "flag", flag_id, "decide", "flag.decide")
        guard(workspace.set_flag_status, flag_id, body.status, body.note)
        done(user, "flag.decide", "flag", flag_id, o)
        return {"ok": True}

    # ------------------------------------------------------------ market (Milestone 6)

    @r.get("/market/providers")
    def market_providers() -> list[dict]:
        from propertyiq.screening import market as mk
        return mk.providers()

    @r.post("/deals/{deal_id}/market")
    def market(deal_id: Id, body: MarketIn, user=Depends(user_dependency)) -> dict:
        o = scoped(user, "deal", deal_id, "work_deals", "deal.market")
        out = jsonable(guard(workspace.market, deal_id, body.kind, body.address, body.city, body.radius_miles,
                             body.max_age_months, body.provider, body.refresh))
        done(user, "deal.market", "deal", deal_id, o)
        return out

    # ------------------------------------------------------------ California (Milestone 7)

    @r.get("/deals/{deal_id}/california")
    def california(deal_id: Id, user=Depends(user_dependency)) -> dict:
        scoped(user, "deal", deal_id, "view", "deal.view")
        deal = guard(workspace.get_deal, deal_id)
        return jsonable({"facts": deal["property_facts"], "condition": workspace.condition_items(deal_id),
                         "life_table": settings_store.list_condition_life(), "rules": settings_store.list_rules(),
                         "flags": [f for f in workspace.load_flags(deal_id) if f["rule_id"].startswith("R")]})

    @r.put("/deals/{deal_id}/california")
    def put_california(deal_id: Id, body: CaliforniaIn, user=Depends(user_dependency)) -> dict:
        o = scoped(user, "deal", deal_id, "work_deals", "deal.california")
        flags = guard(workspace.california, deal_id, body.facts, [c.model_dump() for c in body.condition])
        done(user, "deal.california", "deal", deal_id, o)
        return jsonable({"flags": [f for f in flags if f["rule_id"].startswith("R")]})

    # ------------------------------------------------------------ borrower vs underwritten (Milestone 8)

    def comparison_json(deal_id: int) -> dict:
        from propertyiq.screening import compare
        from propertyiq.screening.routes import report_json
        c = guard(workspace.comparison, deal_id)
        approved = {a["line_item"]: a for a in c["adjustments"] if a["status"] == "approved"}
        lines = [{"key": k, "label": label, "kind": kind, "section": section,
                  "borrower": c["borrower"][k], "underwritten": c["underwritten"][k],
                  "delta": c["underwritten"][k] - c["borrower"][k], "adjustment": approved.get(k)}
                 for k, label, kind, section in compare.LINES]
        tb, tu = c["borrower_totals"], c["underwritten_totals"]
        return jsonable({
            "source": c["source"], "lines": lines, "summary": c["summary"],
            "totals": {"borrower": tb, "underwritten": tu,
                       "difference": {k: getattr(tu, k) - getattr(tb, k) for k in vars(tb)}},
            "borrower_case": report_json(c["borrower_report"], c["guideline_set_id"]),
            "underwritten_case": report_json(c["underwritten_report"], c["guideline_set_id"]),
            "adjustments": c["adjustments"], "guideline_set_id": c["guideline_set_id"],
            "loan": {k: c["deal"]["borrower_inputs"].get(k) for k in workspace.LOAN_FIELDS + ("value",)},
            "reason_codes": list(compare.REASON_CODES), "line_labels": compare.LINE_LABEL,
        })

    @r.get("/deals/{deal_id}/compare")
    def compare_view(deal_id: Id, user=Depends(user_dependency)) -> dict:
        scoped(user, "deal", deal_id, "view", "deal.view")
        return comparison_json(deal_id)

    @r.put("/deals/{deal_id}/loan")
    def put_loan(deal_id: Id, body: LoanIn, user=Depends(user_dependency)) -> dict:
        o = scoped(user, "deal", deal_id, "work_deals", "deal.loan_terms")
        guard(workspace.save_loan, deal_id, body.model_dump(exclude={"guideline_set_id"}), body.guideline_set_id)
        done(user, "deal.loan_terms", "deal", deal_id, o)
        return comparison_json(deal_id)

    @r.post("/deals/{deal_id}/adjustments/suggest")
    def suggest(deal_id: Id, user=Depends(user_dependency)) -> dict:
        o = scoped(user, "deal", deal_id, "work_deals", "adjustment.suggest")
        added = guard(workspace.suggest_adjustments, deal_id)
        done(user, "adjustment.suggest", "deal", deal_id, o)
        return {"added": added, **comparison_json(deal_id)}

    @r.post("/deals/{deal_id}/adjustments")
    def add_adjustment(deal_id: Id, body: AdjustmentIn, user=Depends(user_dependency)) -> dict:
        o = scoped(user, "deal", deal_id, "work_deals", "adjustment.propose")
        guard(workspace.add_adjustment, deal_id, body.line_item, body.underwritten_value, body.reason_code, body.note)
        done(user, "adjustment.propose", "deal", deal_id, o)
        return comparison_json(deal_id)

    def adjustment_permission(current: str, new_status: str | None) -> tuple[str, str]:
        """Analysts edit proposals; changing a status, or touching a decided adjustment, needs a credit officer."""
        if (new_status is not None and new_status != current) or current != "proposed":
            return "decide", "adjustment.decide"
        return "work_deals", "adjustment.edit"

    @r.patch("/adjustments/{adjustment_id}")
    def patch_adjustment(adjustment_id: Id, body: AdjustmentPatch, user=Depends(user_dependency)) -> dict:
        o = workspace.owner("adjustment", adjustment_id)
        access.require_fund(user, o["fund_id"] if o else None, "adjustment.edit", "adjustment", adjustment_id,
                            NOT_FOUND["adjustment"])
        perm, action = adjustment_permission(o["status"], body.status)
        access.require(user, perm, action, "adjustment", adjustment_id, o["fund_id"])
        guard(workspace.update_adjustment, adjustment_id, body.model_dump(exclude_none=True))
        done(user, action, "adjustment", adjustment_id, o)
        return {"ok": True}

    @r.delete("/adjustments/{adjustment_id}")
    def remove_adjustment(adjustment_id: Id, user=Depends(user_dependency)) -> dict:
        o = workspace.owner("adjustment", adjustment_id)
        access.require_fund(user, o["fund_id"] if o else None, "adjustment.delete", "adjustment", adjustment_id,
                            NOT_FOUND["adjustment"])
        access.require(user, "work_deals" if o["status"] == "proposed" else "decide", "adjustment.delete",
                       "adjustment", adjustment_id, o["fund_id"])
        guard(workspace.delete_adjustment, adjustment_id)
        done(user, "adjustment.delete", "adjustment", adjustment_id, o)
        return {"ok": True}

    # ------------------------------------------------------------ exports (Milestone 9)

    def _export_inputs(deal_id: int):
        c = guard(workspace.comparison, deal_id)
        if not c["underwritten_report"].valid:
            raise HTTPException(422, "Fix the deal before exporting: " + " ".join(c["underwritten_report"].errors))
        return c, workspace.load_flags(deal_id), workspace.last_market(deal_id)

    def _name(c: dict, ext: str) -> str:
        import re
        stem = re.sub(r"[^A-Za-z0-9]+", "-", c["deal"]["name"]).strip("-")[:50] or "deal"
        return f"PropertyIQ-underwriting-{stem}-{date.today():%Y%m%d}.{ext}"

    @r.get("/deals/{deal_id}/export/pdf")
    def export_pdf(deal_id: Id, user=Depends(user_dependency)) -> Response:
        from propertyiq.screening.export_deal import render_deal_pdf
        o = scoped(user, "deal", deal_id, "view", "deal.export_pdf")
        c, flags, market = _export_inputs(deal_id)
        pdf = render_deal_pdf(c, flags, market, c["deal"]["documents"], settings_store.list_rules())
        done(user, "deal.export_pdf", "deal", deal_id, o)
        return Response(pdf, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{_name(c, "pdf")}"'})

    @r.get("/deals/{deal_id}/export/xlsx")
    def export_xlsx(deal_id: Id, user=Depends(user_dependency)) -> Response:
        from propertyiq.screening.export_deal import render_deal_xlsx
        o = scoped(user, "deal", deal_id, "view", "deal.export_xlsx")
        c, flags, market = _export_inputs(deal_id)
        body = render_deal_xlsx(c, flags, market)
        done(user, "deal.export_xlsx", "deal", deal_id, o)
        return Response(body,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{_name(c, "xlsx")}"'})

    # ------------------------------------------------------------ settings (Milestone 5): lender policy

    @r.get("/benchmarks")
    def benchmarks() -> list[dict]:
        return jsonable(settings_store.list_benchmarks())

    @r.post("/benchmarks")
    def add_benchmark(body: BenchmarkIn, user=Depends(user_dependency)) -> list[dict]:
        policy(user, "policy.benchmark")
        guard(settings_store.save_benchmark, body.model_dump())
        audit.allowed(user, "policy.benchmark", "policy")
        return jsonable(settings_store.list_benchmarks())

    @r.put("/benchmarks/{benchmark_id}")
    def edit_benchmark(benchmark_id: Id, body: BenchmarkIn, user=Depends(user_dependency)) -> list[dict]:
        policy(user, "policy.benchmark")
        guard(settings_store.save_benchmark, body.model_dump(), benchmark_id)
        audit.allowed(user, "policy.benchmark", "benchmark", benchmark_id)
        return jsonable(settings_store.list_benchmarks())

    @r.delete("/benchmarks/{benchmark_id}")
    def remove_benchmark(benchmark_id: Id, user=Depends(user_dependency)) -> list[dict]:
        policy(user, "policy.benchmark")
        guard(settings_store.delete_benchmark, benchmark_id)
        audit.allowed(user, "policy.benchmark", "benchmark", benchmark_id)
        return jsonable(settings_store.list_benchmarks())

    @r.get("/rules")
    def rules() -> list[dict]:
        return jsonable(settings_store.list_rules())

    @r.put("/rules/{rule_key}")
    def edit_rule(rule_key: Annotated[str, Path(pattern=r"^[a-z0-9_]{1,60}$")], body: RuleIn, user=Depends(user_dependency)) -> list[dict]:
        policy(user, "policy.rule")
        guard(settings_store.save_rule, rule_key, body.model_dump())
        audit.allowed(user, "policy.rule", "rule", rule_key)
        return jsonable(settings_store.list_rules())

    @r.get("/condition-life")
    def condition_life() -> list[dict]:
        return jsonable(settings_store.list_condition_life())

    @r.put("/condition-life/{item_key}")
    def edit_condition_life(item_key: Annotated[str, Path(pattern=r"^[a-z0-9_]{1,60}$")], body: LifeIn, user=Depends(user_dependency)) -> list[dict]:
        policy(user, "policy.condition_life")
        guard(settings_store.save_condition_life, item_key, body.model_dump())
        audit.allowed(user, "policy.condition_life", "condition_life", item_key)
        return jsonable(settings_store.list_condition_life())

    @r.get("/settings")
    def get_settings_() -> dict:
        return jsonable(settings_store.settings())

    @r.put("/settings/{key}")
    def put_setting(key: Annotated[str, Path(pattern=r"^[a-z0-9_]{1,60}$")], body: SettingIn, user=Depends(user_dependency)) -> dict:
        policy(user, "policy.setting")
        guard(settings_store.save_setting, key, body.value)
        audit.allowed(user, "policy.setting", "setting", key)
        return jsonable(settings_store.settings())

    return r
