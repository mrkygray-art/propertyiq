"""Request and response models. Money is in dollars rounded to cents; percentages to 2 places.
Unrounded Decimal values are used for every rule comparison before rounding for display."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from propertyiq.analytics import PropertyEvaluation
from propertyiq.finance import Evaluation, RuleResult, current_window, prior_window


def money(d: Decimal | None) -> float | None:
    return None if d is None else float(d.quantize(Decimal("0.01")))


def pct(d: Decimal | None) -> float | None:
    return None if d is None else float(d.quantize(Decimal("0.01")))


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    username: str
    display_name: str
    role: str
    portfolios: list[str]
    permissions: list[str] = []   # see propertyiq/access.py; the server enforces them on every request


class LoginResponse(BaseModel):
    token: str
    expires_in_seconds: int
    user: UserOut


class Window(BaseModel):
    start: date
    end: date


class RuleOut(BaseModel):
    rule_id: str
    status: Literal["triggered", "not_triggered", "not_evaluated"]
    value: float | None
    comparator: str
    threshold: float
    explanation: str

    @classmethod
    def of(cls, r: RuleResult) -> "RuleOut":
        return cls(rule_id=r.rule_id, status=r.status, value=pct(r.value), comparator=r.comparator,
                   threshold=float(r.threshold), explanation=r.explanation)


class MetricsOut(BaseModel):
    occupancy_pct: float | None = Field(description="Physical occupancy at the period month end")
    noi_actual: float | None = Field(description="TTM actual NOI")
    noi_budget: float | None = Field(description="TTM budget NOI")
    budget_variance: float | None = Field(description="Actual - budget; negative is under budget")
    budget_shortfall: float | None = Field(description="Budget - actual when positive, else 0")
    noi_to_budget_pct: float | None
    noi_prior: float | None = Field(description="Prior TTM actual NOI")
    noi_yoy_pct: float | None
    data_issues: list[str]

    @classmethod
    def of(cls, e: Evaluation) -> "MetricsOut":
        m = e.metrics
        return cls(
            occupancy_pct=pct(m.occupancy_pct), noi_actual=money(m.noi_actual), noi_budget=money(m.noi_budget),
            budget_variance=money(m.budget_variance), budget_shortfall=money(m.budget_shortfall),
            noi_to_budget_pct=pct(m.noi_to_budget_pct), noi_prior=money(m.noi_prior),
            noi_yoy_pct=pct(m.noi_yoy_pct), data_issues=list(m.data_issues),
        )


FORMULAS = {
    "noi": "sum(operating_revenue) - sum(operating_expenses) over the 12 months ending the period",
    "budget_variance": "noi_actual - noi_budget",
    "budget_shortfall": "max(noi_budget - noi_actual, 0)",
    "noi_to_budget_pct": "noi_actual / noi_budget * 100 (only when noi_budget > 0)",
    "noi_yoy_pct": "(noi_actual - noi_prior) / abs(noi_prior) * 100 (only when noi_prior > 0)",
    "occupancy_pct": "occupied_size / rentable_size * 100 at the period month end (physical occupancy)",
}


class CalculationOut(BaseModel):
    period: date
    current_ttm: Window
    prior_ttm: Window
    inputs: dict[str, float | int | str | None]
    formulas: dict[str, str]
    source_records: dict[str, list[int]]
    rule_set_version: str

    @classmethod
    def of(cls, pe: PropertyEvaluation) -> "CalculationOut":
        i = pe.evaluation.inputs
        cs, ce = current_window(i.period)
        ps, pe_ = prior_window(i.period)
        return cls(
            period=i.period, current_ttm=Window(start=cs, end=ce), prior_ttm=Window(start=ps, end=pe_),
            inputs={
                "current_months_of_actuals": i.current_months,
                "current_operating_revenue": money(i.current_revenue),
                "current_operating_expenses": money(i.current_expenses),
                "current_noi": money(i.current_noi),
                "prior_months_of_actuals": i.prior_months,
                "prior_noi": money(i.prior_noi),
                "budget_months": i.budget_months,
                "budget_noi": money(i.budget_noi),
                "occupied_size": i.occupied_size,
                "rentable_size": i.rentable_size,
                "size_unit": pe.property["size_unit"],
            },
            formulas=FORMULAS,
            source_records=pe.source_records,
            rule_set_version=pe.evaluation.rule_set_version,
        )


class PropertyRef(BaseModel):
    property_id: str
    name: str
    asset_class: str
    portfolio_id: str
    portfolio_name: str
    market: str
    size_unit: str
    rentable_size: int


class PropertyAssessment(PropertyRef):
    flagged: bool
    priority_rank: int | None
    rules_triggered: list[str]
    data_complete: bool
    metrics: MetricsOut
    rules: list[RuleOut]
    calculation: CalculationOut

    @classmethod
    def of(cls, pe: PropertyEvaluation, rank: int | None) -> "PropertyAssessment":
        e = pe.evaluation
        return cls(
            **pe.property, flagged=e.flagged, priority_rank=rank,
            rules_triggered=[r.rule_id for r in e.rules if r.status == "triggered"],
            data_complete=e.data_complete, metrics=MetricsOut.of(e),
            rules=[RuleOut.of(r) for r in e.rules], calculation=CalculationOut.of(pe),
        )


class RuleSetOut(BaseModel):
    version: str
    logic: str = "A property is flagged if any rule is triggered."
    rules: list[dict[str, str]]
    priority_rule: str = (
        "Flagged properties are ranked by number of rules triggered (most first), then by "
        "budget shortfall (largest first), then by property ID."
    )


class Scope(BaseModel):
    username: str
    portfolios: list[str]
    properties_evaluated: int


class UnderperformingResponse(BaseModel):
    synthetic_data: Literal[True] = True
    period: date
    rule_set: RuleSetOut
    scope: Scope
    flagged_count: int
    total_budget_shortfall_flagged: float
    flagged: list[PropertyAssessment]
    incomplete_data: list[str] = Field(description="Property IDs where at least one rule could not be evaluated")
    note: str = "Budget shortfall is actual NOI below the approved budget. It is not a forecast loss."
    payload_sha256: str = ""


class AssetClassSummary(BaseModel):
    asset_class: str
    property_count: int
    size_unit: str
    occupancy_pct: float | None = Field(description="Size-weighted physical occupancy within the class")
    noi_actual: float
    noi_budget: float
    budget_variance: float
    flagged_count: int


class PortfolioSummaryResponse(BaseModel):
    synthetic_data: Literal[True] = True
    period: date
    scope: Scope
    property_count: int
    flagged_count: int
    total_budget_shortfall_flagged: float
    noi_actual: float
    noi_budget: float
    budget_variance: float
    noi_prior: float
    noi_yoy_pct: float | None
    average_property_occupancy_pct: float | None = Field(
        description="Simple average of property occupancy. Square feet and units are not summed together."
    )
    by_asset_class: list[AssetClassSummary]
    rule_set_version: str
    payload_sha256: str = ""


class MonthOut(BaseModel):
    month_end: date
    record_id: int
    budget_record_id: int | None
    operating_revenue: float
    operating_expenses: float
    noi: float
    budget_noi: float | None
    occupancy_pct: float


class PropertyDetailResponse(BaseModel):
    synthetic_data: Literal[True] = True
    period: date
    property: PropertyAssessment
    rule_set: RuleSetOut
    monthly: list[MonthOut]
    payload_sha256: str = ""


class PeriodsResponse(BaseModel):
    default_period: date | None
    periods: list[date]
    definition: str = "Each period is a quarter end. Annual figures are the trailing 12 months ending that date."


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    period: date | None = None
    context_property_id: str | None = Field(default=None, pattern=r"^PIQ-\d{3}$",
                                            description="Property the conversation is currently about, for follow-ups")


class Verification(BaseModel):
    status: Literal["verified", "rejected", "not_applicable"]
    numbers_checked: int
    unsupported_numbers: list[str]
    missing_properties: list[str] = Field(default_factory=list,
                                          description="Flagged properties the model's draft failed to mention")


class Citation(BaseModel):
    property_id: str
    name: str
    period: date
    source_records: dict[str, list[int]]
    link: str


class ChatResponse(BaseModel):
    synthetic_data: Literal[True] = True
    period: date
    intent: str
    answer: str
    answer_source: Literal["local_model", "verified_template"]
    model: str | None
    verification: Verification
    facts: dict
    citations: list[Citation]
    calculation_link: str
    focus_property_id: str | None
    notes: list[str]
    latency_ms: int
    trace_id: str | None = None  # AI Engineering Lab trace (stages and timings; no question text)


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: bool
    local_model: bool
    synthetic_data: Literal[True] = True
