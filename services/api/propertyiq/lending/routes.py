"""Debt dashboard API. Signed-in users only; every query is limited to the user's funds."""
import hashlib
import json
from datetime import date
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from propertyiq import db
from propertyiq.finance import is_month_end
from propertyiq.lending import service, store


def with_hash(body: dict) -> dict:
    """sha256 of the canonical JSON (sorted keys, no whitespace), same rule as the portfolio endpoints."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return {**body, "payload_sha256": hashlib.sha256(canonical.encode()).hexdigest()}


def build_router(user_dependency: Callable) -> APIRouter:
    router = APIRouter(prefix="/api/debt", dependencies=[Depends(user_dependency)], tags=["debt"])

    def resolve(conn, period: date | None) -> tuple[date, list[date]]:
        periods = store.debt_periods(conn)
        if not periods:
            raise HTTPException(status_code=503, detail="No financial data loaded")
        if period is None:
            return periods[0], periods
        if not is_month_end(period) or period not in periods:
            raise HTTPException(status_code=400, detail=f"Unsupported period. Available: "
                                                        f"{', '.join(p.isoformat() for p in periods)}")
        return period, periods

    @router.get("/summary")
    def debt_summary(user=Depends(user_dependency), period: date | None = Query(None)) -> dict:
        with db.connection() as conn:
            p, periods = resolve(conn, period)
            return with_hash(service.summary(conn, user.username, p, periods))

    @router.get("/loans/{loan_id}")
    def loan_detail(user=Depends(user_dependency), loan_id: str = Path(pattern=r"^LN-[0-9]{4}$"),
                    period: date | None = Query(None)) -> dict:
        with db.connection() as conn:
            p, periods = resolve(conn, period)
            body = service.detail(conn, user.username, loan_id, p, periods)
        if body is None:   # same answer for "doesn't exist" and "not in your funds"
            raise HTTPException(status_code=404, detail="Loan not found")
        return with_hash(body)

    return router
