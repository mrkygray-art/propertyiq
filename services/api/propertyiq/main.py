"""PropertyIQ API. Run with: uvicorn propertyiq.main:app --host 127.0.0.1 --port 8000"""
from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from typing import Annotated

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg_pool import PoolTimeout
from pydantic import BaseModel

from propertyiq import analytics, auth, chat, db
from propertyiq.config import get_settings
from propertyiq.finance import RuleSet, is_month_end, prioritize
from propertyiq.llm import OllamaProvider
from propertyiq.schemas import (
    AssetClassSummary, ChatRequest, ChatResponse, HealthResponse, LoginRequest, LoginResponse, MonthOut, PeriodsResponse,
    PortfolioSummaryResponse, PropertyAssessment, PropertyDetailResponse, RuleSetOut, Scope,
    UnderperformingResponse, UserOut, money, pct,
)

settings = get_settings()
RULES = RuleSet.load(settings.rules_path)
PROVIDER = OllamaProvider(settings.ollama_url, settings.chat_model, settings.embed_model, settings.allowed_models)
# Used when a username does not exist, so a failed login costs the same either way.
_DUMMY_HASH = auth.hash_password("not-a-real-password")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.open_pool()
    yield
    db.close_pool()


# docs_url/redoc_url are off: FastAPI's built-in docs pages load JavaScript from a CDN.
app = FastAPI(title="PropertyIQ API", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(psycopg.OperationalError)
@app.exception_handler(PoolTimeout)
async def database_unavailable(_: Request, __: Exception) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "Database unavailable"})


def with_hash(model: BaseModel) -> BaseModel:
    """Attach sha256 of the canonical JSON payload (sorted keys, no whitespace, hash field blank)."""
    body = model.model_dump(mode="json", exclude={"payload_sha256"})
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    model.payload_sha256 = hashlib.sha256(canonical.encode()).hexdigest()
    return model


# ---------------------------------------------------------------- auth

bearer = HTTPBearer(auto_error=False)


def current_user(creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> analytics.DemoUser:
    username = auth.read_token(creds.credentials, settings.session_secret) if creds else None
    if username is None:
        raise HTTPException(status_code=401, detail="Sign in required", headers={"WWW-Authenticate": "Bearer"})
    with db.connection() as conn:
        found = analytics.get_user(conn, username)
    if found is None:
        raise HTTPException(status_code=401, detail="Sign in required", headers={"WWW-Authenticate": "Bearer"})
    return found[0]


User = Annotated[analytics.DemoUser, Depends(current_user)]
PeriodParam = Annotated[date | None, Query(description="Quarter-end reporting period, e.g. 2026-09-30")]


@app.post("/api/auth/login", response_model=LoginResponse)
def login(body: LoginRequest) -> LoginResponse:
    with db.connection() as conn:
        found = analytics.get_user(conn, body.username)
    stored = found[1] if found else _DUMMY_HASH
    if not auth.verify_password(body.password, stored) or found is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    user = found[0]
    return LoginResponse(
        token=auth.issue_token(user.username, settings.session_secret),
        expires_in_seconds=auth.TOKEN_TTL_SECONDS,
        user=UserOut(username=user.username, display_name=user.display_name, role=user.role,
                     portfolios=list(user.portfolios)),
    )


@app.get("/api/me", response_model=UserOut)
def me(user: User) -> UserOut:
    return UserOut(username=user.username, display_name=user.display_name, role=user.role,
                   portfolios=list(user.portfolios))


# ---------------------------------------------------------------- analytics

def resolve_period(conn: psycopg.Connection, period: date | None) -> date:
    periods = analytics.available_periods(conn)
    if not periods:
        raise HTTPException(status_code=503, detail="No financial data loaded")
    if period is None:
        return periods[0]
    if not is_month_end(period) or period not in periods:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported period. Available: {', '.join(p.isoformat() for p in periods)}",
        )
    return period


def rule_set_out() -> RuleSetOut:
    return RuleSetOut(version=RULES.version, rules=RULES.describe())


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    database, model = db.database_ready(), PROVIDER.available()
    return HealthResponse(status="ok" if database and model else "degraded", database=database, local_model=model)


@app.get("/api/periods", response_model=PeriodsResponse)
def periods(_: User) -> PeriodsResponse:
    with db.connection() as conn:
        found = analytics.available_periods(conn)
    return PeriodsResponse(default_period=found[0] if found else None, periods=found)


@app.get("/api/rules", response_model=RuleSetOut)
def rules(_: User) -> RuleSetOut:
    return rule_set_out()


@app.get("/api/properties/underperforming", response_model=UnderperformingResponse)
def underperforming(user: User, period: PeriodParam = None) -> UnderperformingResponse:
    with db.connection() as conn:
        p = resolve_period(conn, period)
        evals = analytics.evaluate_properties(conn, user.username, p, RULES)
    by_id = {pe.property["property_id"]: pe for pe in evals}
    ranked = prioritize([pe.evaluation for pe in evals])
    flagged = [PropertyAssessment.of(by_id[e.inputs.property_id], rank) for rank, e in ranked]
    shortfall = sum((e.metrics.budget_shortfall or Decimal(0) for _, e in ranked), Decimal(0))
    return with_hash(UnderperformingResponse(
        period=p,
        rule_set=rule_set_out(),
        scope=Scope(username=user.username, portfolios=list(user.portfolios), properties_evaluated=len(evals)),
        flagged_count=len(flagged),
        total_budget_shortfall_flagged=money(shortfall),
        flagged=flagged,
        incomplete_data=[pe.property["property_id"] for pe in evals if not pe.evaluation.data_complete],
    ))


@app.get("/api/portfolio/summary", response_model=PortfolioSummaryResponse)
def portfolio_summary(user: User, period: PeriodParam = None) -> PortfolioSummaryResponse:
    with db.connection() as conn:
        p = resolve_period(conn, period)
        evals = analytics.evaluate_properties(conn, user.username, p, RULES)
    ranked = prioritize([pe.evaluation for pe in evals])
    zero = Decimal(0)

    def total(attr: str, items) -> Decimal:
        return sum((getattr(pe.evaluation.metrics, attr) or zero for pe in items), zero)

    classes = []
    for cls in ("office", "retail", "industrial", "multifamily"):
        items = [pe for pe in evals if pe.property["asset_class"] == cls]
        if not items:
            continue
        occupied = sum(pe.evaluation.inputs.occupied_size or 0 for pe in items)
        rentable = sum(pe.evaluation.inputs.rentable_size or 0 for pe in items)
        classes.append(AssetClassSummary(
            asset_class=cls, property_count=len(items), size_unit=items[0].property["size_unit"],
            occupancy_pct=pct(Decimal(occupied) / Decimal(rentable) * 100) if rentable else None,
            noi_actual=money(total("noi_actual", items)), noi_budget=money(total("noi_budget", items)),
            budget_variance=money(total("budget_variance", items)),
            flagged_count=sum(pe.evaluation.flagged for pe in items),
        ))
    occs = [pe.evaluation.metrics.occupancy_pct for pe in evals if pe.evaluation.metrics.occupancy_pct is not None]
    noi_actual, noi_prior = total("noi_actual", evals), total("noi_prior", evals)
    return with_hash(PortfolioSummaryResponse(
        period=p,
        scope=Scope(username=user.username, portfolios=list(user.portfolios), properties_evaluated=len(evals)),
        property_count=len(evals),
        flagged_count=len(ranked),
        total_budget_shortfall_flagged=money(sum((e.metrics.budget_shortfall or zero for _, e in ranked), zero)),
        noi_actual=money(noi_actual), noi_budget=money(total("noi_budget", evals)),
        budget_variance=money(total("budget_variance", evals)), noi_prior=money(noi_prior),
        noi_yoy_pct=pct((noi_actual - noi_prior) / abs(noi_prior) * 100) if noi_prior > 0 else None,
        average_property_occupancy_pct=pct(sum(occs, zero) / len(occs)) if occs else None,
        by_asset_class=classes,
        rule_set_version=RULES.version,
    ))


@app.get("/api/properties/{property_id}", response_model=PropertyDetailResponse)
def property_detail(
    user: User,
    property_id: Annotated[str, Path(pattern=r"^PIQ-\d{3}$")],
    period: PeriodParam = None,
) -> PropertyDetailResponse:
    with db.connection() as conn:
        p = resolve_period(conn, period)
        evals = analytics.evaluate_properties(conn, user.username, p, RULES)
        target = next((pe for pe in evals if pe.property["property_id"] == property_id), None)
        if target is None:
            # Same response whether the property is missing or belongs to another portfolio.
            raise HTTPException(status_code=404, detail="Property not found")
        series = analytics.monthly_series(conn, user.username, property_id, p)
    ranks = {e.inputs.property_id: rank for rank, e in prioritize([pe.evaluation for pe in evals])}
    return with_hash(PropertyDetailResponse(
        period=p,
        property=PropertyAssessment.of(target, ranks.get(property_id)),
        rule_set=rule_set_out(),
        monthly=[MonthOut(
            month_end=m["month_end"], record_id=m["record_id"], budget_record_id=m["budget_record_id"],
            operating_revenue=money(m["operating_revenue"]), operating_expenses=money(m["operating_expenses"]),
            noi=money(m["noi"]), budget_noi=money(m["budget_noi"]), occupancy_pct=pct(m["occupancy_pct"]),
        ) for m in series],
    ))


# ---------------------------------------------------------------- chat

@app.post("/api/chat", response_model=ChatResponse)
def chat_endpoint(user: User, body: ChatRequest) -> ChatResponse:
    with db.connection() as conn:
        p = resolve_period(conn, body.period)
        result = chat.answer(conn, user, body.question, p, RULES, PROVIDER, body.context_property_id)
    return ChatResponse(
        period=result.period, intent=result.intent, answer=result.answer, answer_source=result.answer_source,
        model=result.model, verification=result.verification, facts=result.facts, citations=result.citations,
        calculation_link=f"/verified?period={result.period.isoformat()}",
        focus_property_id=result.focus_property_id, notes=result.notes, latency_ms=result.latency_ms,
    )


# ---------------------------------------------------------------- optional modules

# Tier 1 loan screening. Turn off with PIQ_SCREENING_ENABLED=false; nothing above depends on it.
from propertyiq.screening.routes import build_router as build_screening_router, enabled as screening_enabled  # noqa: E402
if screening_enabled():
    app.include_router(build_screening_router(current_user))
