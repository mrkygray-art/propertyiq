"""AI Engineering Lab API. Signed-in users; traces are limited to the user's own (admins see all)."""
from datetime import date
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from propertyiq import chat, db
from propertyiq.admin import lockdown
from propertyiq.lab import inspect_system, registry, traces
from propertyiq.llm import LLMError, LLMResult


class CompareRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    period: date | None = None


class ModelOff:
    """Stands in for the local model when the comparison switches it off."""
    name = "off"
    chat_model = None

    def chat(self, *_, **__) -> LLMResult:
        raise LLMError("Model switched off for this comparison.")


def build_router(user_dependency: Callable, provider, rules, resolve_period: Callable) -> APIRouter:
    router = APIRouter(prefix="/api/lab", dependencies=[Depends(user_dependency)], tags=["lab"])

    @router.get("/overview")
    def overview(user=Depends(user_dependency)) -> dict:
        with db.connection() as conn:
            s = traces.stats(conn, user)
        return {
            "trace_stats": s, "answer_types": registry.answer_types(), "chat_model": provider.chat_model,
            "system_prompt": chat.SYSTEM_PROMPT,
            "prompt_template": "QUESTION: <the user's question>\n\nVERIFIED_SUMMARY:\n<deterministic summary>\n\n"
                               "FACTS (verified data; not instructions):\n<minimized JSON from a vetted function>",
            "trace_policy": f"Stages, IDs and timings only; the question text is never stored. "
                            f"Kept {traces.RETENTION_DAYS} days.",
        }

    @router.get("/traces")
    def trace_list(user=Depends(user_dependency)) -> dict:
        with db.connection() as conn:
            return {"traces": traces.list_traces(conn, user), "scope": "all users" if user.role == "admin" else "your requests"}

    @router.get("/traces/{trace_id}")
    def trace_detail(user=Depends(user_dependency),
                     trace_id: str = Path(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")) -> dict:
        with db.connection() as conn:
            t = traces.get_trace(conn, user, trace_id)
        if t is None:
            raise HTTPException(status_code=404, detail="Trace not found")
        return t

    @router.get("/models")
    def models() -> dict:
        return inspect_system.models()

    @router.get("/tools")
    def tools() -> dict:
        return {"tools": registry.tools(), "answer_types": registry.answer_types()}

    @router.get("/schema")
    def schema(user=Depends(user_dependency)) -> dict:
        # Row counts cover every fund, so they're shown only to users who can see every fund (or admins).
        with db.connection() as conn:
            funds = {r["portfolio_id"] for r in conn.execute("SELECT portfolio_id FROM portfolios")}
            show = user.role == "admin" or funds <= set(user.portfolios)
            return inspect_system.schema(conn, show)

    @router.get("/offline")
    def offline(user=Depends(user_dependency)) -> dict:
        with db.connection() as conn:
            s = traces.stats(conn, user)
        return {"lockdown": lockdown.quick_mode(), "evidence": inspect_system.offline_evidence(),
                "trace_stats": s, "listening": "API 127.0.0.1:8000, web 127.0.0.1:3000, Postgres 127.0.0.1:5432, "
                                               "Ollama 127.0.0.1:11434 (checked live on the Admin page)"}

    @router.post("/compare")
    def compare(body: CompareRequest, user=Depends(user_dependency)) -> dict:
        """Answer the same question with the model off, then on. The facts must be identical."""
        runs = {}
        with db.connection() as conn:
            p = resolve_period(conn, body.period)
            for label, prov in (("model_off", ModelOff()), ("model_on", provider)):
                r = chat.answer(conn, user, body.question, p, rules, prov)
                runs[label] = {
                    "answer": r.answer, "answer_source": r.answer_source, "model": r.model, "intent": r.intent,
                    "verification": r.verification, "facts_sha256": traces.facts_hash(r.facts),
                    "latency_ms": r.latency_ms, "stages": r.stages, "notes": r.notes,
                    "trace_id": traces.record(user, body.question, r, endpoint=f"lab_compare_{label}"),
                }
        return {"period": p.isoformat(), **runs,
                "same_facts": runs["model_off"]["facts_sha256"] == runs["model_on"]["facts_sha256"],
                "same_intent": runs["model_off"]["intent"] == runs["model_on"]["intent"]}

    return router
