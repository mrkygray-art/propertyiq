"""Loan screening API (/api/screening/*). Mounted by main.py only when PIQ_SCREENING_ENABLED is true.

Percentages arrive and leave the API as percents (6.5 means 6.5%) for the form, and are
converted to fractions for the engine. Ratios such as DSCR are plain numbers.
"""
from __future__ import annotations

import os
import re
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import Response
from pydantic import BaseModel, Field

from propertyiq.config import load_dotenv
from propertyiq.screening import DISCLAIMER, store
from propertyiq.screening.calc import PROPERTY_TYPES, DealInputs, Scenario
from propertyiq.screening.report import Report, assumptions, build

HUNDRED = Decimal(100)
Money = Annotated[Decimal, Field(ge=-1_000_000_000_000, le=1_000_000_000_000)]
Pct = Annotated[Decimal, Field(ge=-1000, le=1000)]


def enabled() -> bool:
    load_dotenv()
    return os.environ.get("PIQ_SCREENING_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


# ---------------------------------------------------------------- request models

class DealIn(BaseModel):
    property_name: str = Field("", max_length=120)
    property_type: str = "multifamily"
    size: Money | None = None
    size_unit: Literal["units", "sf"] | None = None
    value: Money | None = None
    pgi: Money | None = None
    other_income: Money = Decimal(0)
    vacancy_pct: Pct | None = None
    operating_expenses: Money | None = None
    reserves: Money = Decimal(0)
    loan_amount: Money | None = None
    interest_rate_pct: Pct | None = None
    amortization_years: int | None = Field(None, ge=-100, le=100)
    interest_only_years: Decimal = Field(Decimal(0), ge=-100, le=100)
    term_years: Decimal | None = Field(None, ge=0, le=100)

    def to_inputs(self) -> DealInputs:
        return DealInputs(
            property_name=self.property_name.strip(), property_type=self.property_type,
            value=self.value, pgi=self.pgi, other_income=self.other_income,
            vacancy=None if self.vacancy_pct is None else self.vacancy_pct / HUNDRED,
            operating_expenses=self.operating_expenses, reserves=self.reserves,
            loan_amount=self.loan_amount,
            interest_rate=None if self.interest_rate_pct is None else self.interest_rate_pct / HUNDRED,
            amortization_years=self.amortization_years, interest_only_years=self.interest_only_years,
            term_years=self.term_years, size=self.size, size_unit=self.size_unit,
        )


class ScenarioIn(BaseModel):
    key: str = Field(max_length=40)
    label: str = Field(max_length=120)
    rate_add_pct: Pct = Decimal(0)
    vacancy_add_pts: Pct = Decimal(0)
    income_change_pct: Pct = Decimal(0)
    expense_change_pct: Pct = Decimal(0)
    exit_cap_add_pct: Pct = Decimal(0)

    def to_scenario(self) -> Scenario:
        return Scenario(self.key, self.label, self.rate_add_pct / HUNDRED, self.vacancy_add_pts / HUNDRED,
                        self.income_change_pct / HUNDRED, self.expense_change_pct / HUNDRED,
                        self.exit_cap_add_pct / HUNDRED)


class ScreenRequest(BaseModel):
    deal: DealIn
    guideline_set_id: int | None = None
    scenarios: list[ScenarioIn] | None = Field(None, max_length=15)


class RuleIn(BaseModel):
    enabled: bool = False
    threshold: Decimal | None = Field(None, ge=0, le=1_000_000_000_000)


class GuidelineIn(BaseModel):
    """Thresholds as shown on the form: DSCR as a ratio, LTV / debt yield / break-even as percents, loans in dollars."""
    name: str = Field(min_length=1, max_length=80)
    min_dscr: RuleIn
    max_ltv: RuleIn
    min_debt_yield: RuleIn
    max_break_even: RuleIn
    min_loan: RuleIn
    max_loan: RuleIn
    allowed_types_enabled: bool = False
    allowed_types: list[str] = Field(default_factory=lambda: list(PROPERTY_TYPES))
    dscr_basis: Literal["amortizing", "interest_only"] = "amortizing"


PCT_RULES = ("max_ltv", "min_debt_yield", "max_break_even")


def rules_to_db(g: GuidelineIn) -> dict:
    def rule(key: str) -> dict:
        r: RuleIn = getattr(g, key)
        t = r.threshold
        if t is not None and key in PCT_RULES:
            t = t / HUNDRED
        return {"enabled": r.enabled, "threshold": None if t is None else str(t)}

    return {
        **{k: rule(k) for k in ("min_dscr", "max_ltv", "min_debt_yield", "max_break_even", "min_loan", "max_loan")},
        "allowed_types": {"enabled": g.allowed_types_enabled,
                          "types": [t for t in g.allowed_types if t in PROPERTY_TYPES]},
        "dscr_basis": g.dscr_basis,
    }


def set_out(row: dict) -> dict:
    """A saved set in form units (percents for the percentage rules)."""
    rules = row["rules"]

    def rule(key: str) -> dict:
        r = rules.get(key) or {}
        t = r.get("threshold")
        if t is not None:
            t = float(Decimal(t) * HUNDRED) if key in PCT_RULES else float(Decimal(t))
        return {"enabled": bool(r.get("enabled")), "threshold": t}

    types = rules.get("allowed_types") or {}
    return {
        "set_id": row["set_id"], "name": row["name"], "updated_at": row["updated_at"].isoformat(),
        **{k: rule(k) for k in ("min_dscr", "max_ltv", "min_debt_yield", "max_break_even", "min_loan", "max_loan")},
        "allowed_types_enabled": bool(types.get("enabled")),
        "allowed_types": types.get("types", list(PROPERTY_TYPES)),
        "dscr_basis": rules.get("dscr_basis", "amortizing"),
    }


# ---------------------------------------------------------------- response

def num(v: Any) -> Any:
    return float(v) if isinstance(v, Decimal) else v


def report_json(r: Report, set_id: int) -> dict:
    out: dict = {
        "disclaimer": DISCLAIMER,
        "generated_at": r.generated_at.isoformat(timespec="seconds"),
        "valid": r.valid,
        "errors": r.errors,
        "guideline_set": {"set_id": set_id, "name": r.guidelines.name, "dscr_basis": r.guidelines.dscr_basis},
        "assumptions": [{"label": a, "value": b} for a, b in assumptions(r.deal)],
    }
    if not r.valid:
        return out
    m = r.metrics
    out["metrics"] = {k: num(getattr(m, k)) for k in (
        "egi", "noi", "monthly_payment", "annual_debt_service", "interest_only_debt_service", "dscr",
        "dscr_interest_only", "ltv", "debt_yield", "cap_rate", "break_even_occupancy", "debt_constant")}
    out["warnings"] = list(m.warnings)
    out["rules"] = [{"rule_id": x.rule_id, "label": x.label, "status": x.status, "actual": num(x.actual),
                     "threshold": num(x.threshold), "kind": x.kind, "message": x.message} for x in r.rules]
    out["overall"] = r.overall
    out["stress"] = [{
        "key": s.scenario.key, "label": s.scenario.label,
        "noi": num(s.metrics.noi), "annual_debt_service": num(s.metrics.annual_debt_service),
        "dscr": num(s.metrics.dscr), "ltv": num(s.metrics.ltv), "debt_yield": num(s.metrics.debt_yield),
        "break_even_occupancy": num(s.metrics.break_even_occupancy), "value_used": num(s.value_used),
        "interest_rate": num(s.inputs.interest_rate), "vacancy": num(s.inputs.vacancy),
        "overall": s.overall, "failed": [x.label for x in s.rules if x.status == "FAIL"],
    } for s in r.stress]
    min_dscr = r.guidelines.min_dscr
    out["grid"] = {
        "rates": [num(x) for x in r.grid_rates], "vacancies": [num(x) for x in r.grid_vacancies],
        "dscr": [[num(c) for c in row] for row in r.grid],
        "min_dscr": num(min_dscr.threshold) if min_dscr.enabled else None,
        "base_rate": num(r.deal.interest_rate), "base_vacancy": num(r.deal.vacancy),
    }
    return out


def _filename(r: Report, ext: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9]+", "-", r.deal.property_name or "deal").strip("-")[:50] or "deal"
    return f"PropertyIQ-screening-{stem}-{r.generated_at:%Y%m%d}.{ext}"


# ---------------------------------------------------------------- router

def build_router(user_dependency) -> APIRouter:
    """user_dependency is the app's sign-in check; every route requires a signed-in user."""
    router = APIRouter(prefix="/api/screening", dependencies=[Depends(user_dependency)], tags=["screening"])

    def load_set(set_id: int | None) -> tuple[int, Any]:
        row = store.get_set(set_id) if set_id is not None else (store.list_sets() or [None])[0]
        if row is None:
            raise HTTPException(404, "Guideline set not found")
        return row["set_id"], store.to_guideline_set(row["name"], row["rules"])

    def run(body: ScreenRequest) -> tuple[Report, int]:
        set_id, g = load_set(body.guideline_set_id)
        scenarios = tuple(s.to_scenario() for s in body.scenarios) if body.scenarios else None
        report = build(body.deal.to_inputs(), g, scenarios) if scenarios else build(body.deal.to_inputs(), g)
        return report, set_id

    @router.get("/status")
    def status() -> dict:
        return {"enabled": True, "disclaimer": DISCLAIMER, "property_types": list(PROPERTY_TYPES)}

    @router.post("/evaluate")
    def evaluate(body: ScreenRequest) -> dict:
        report, set_id = run(body)
        return report_json(report, set_id)

    @router.post("/export/pdf")
    def export_pdf(body: ScreenRequest) -> Response:
        from propertyiq.screening.export_pdf import render_pdf
        report, _ = run(body)
        if not report.valid:
            raise HTTPException(422, "Fix the deal inputs before exporting: " + " ".join(report.errors))
        return Response(render_pdf(report), media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{_filename(report, "pdf")}"'})

    @router.post("/export/xlsx")
    def export_xlsx(body: ScreenRequest) -> Response:
        from propertyiq.screening.export_xlsx import render_xlsx
        report, _ = run(body)
        if not report.valid:
            raise HTTPException(422, "Fix the deal inputs before exporting: " + " ".join(report.errors))
        return Response(render_xlsx(report),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{_filename(report, "xlsx")}"'})

    @router.get("/guidelines")
    def list_guidelines() -> list[dict]:
        return [set_out(r) for r in store.list_sets()]

    @router.post("/guidelines")
    def create_guidelines(body: GuidelineIn) -> dict:
        try:
            return set_out(store.save_set(body.name.strip(), rules_to_db(body)))
        except store.GuidelineError as e:
            raise HTTPException(400, str(e)) from None

    @router.put("/guidelines/{set_id}")
    def update_guidelines(set_id: Annotated[int, Path(ge=1)], body: GuidelineIn) -> dict:
        try:
            return set_out(store.save_set(body.name.strip(), rules_to_db(body), set_id))
        except store.GuidelineError as e:
            raise HTTPException(400, str(e)) from None

    @router.delete("/guidelines/{set_id}")
    def delete_guidelines(set_id: Annotated[int, Path(ge=1)]) -> dict:
        try:
            store.delete_set(set_id)
        except store.GuidelineError as e:
            raise HTTPException(400, str(e)) from None
        return {"deleted": set_id}

    return router
