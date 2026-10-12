"""Tool registry: every vetted function the AI path and dashboards may call, read from code.

The model never calls these itself and never sees SQL. The deterministic router picks one, it runs with the
signed-in user's fund scope, and only its minimized output (the facts) reaches the model.
"""
from __future__ import annotations

import inspect

from propertyiq import analytics, chat, finance
from propertyiq.lending import calc as lending_calc
from propertyiq.lending import store as lending_store

SCOPED = "Joins user_portfolio_access: only the user's funds"

TOOLS = [
    {"id": "analytics.evaluate_properties", "fn": analytics.evaluate_properties, "kind": "SQL (vetted)",
     "used_by": ["chat: every answer", "portfolio dashboard"], "scope": SCOPED,
     "returns": "Each property's trailing-12-month NOI, prior year, budget and occupancy, with rule results and record IDs"},
    {"id": "finance.prioritize", "fn": finance.prioritize, "kind": "Deterministic rules",
     "used_by": ["chat: which assets are underperforming"], "scope": "Works only on the rows passed in",
     "returns": "Flagged properties in priority order (largest budget shortfall first)"},
    {"id": "analytics.monthly_series", "fn": analytics.monthly_series, "kind": "SQL (vetted)",
     "used_by": ["chat: why a property was flagged / NOI variance", "property page"], "scope": SCOPED,
     "returns": "One property's monthly actuals and budget up to the period"},
    {"id": "chat.route", "fn": chat.route, "kind": "Deterministic router",
     "used_by": ["chat: every answer"], "scope": "Matches only properties already in the user's scope",
     "returns": "Answer type and property; fixed replies for unsupported or out-of-scope questions"},
    {"id": "chat.verify_numbers", "fn": chat.verify_numbers, "kind": "Output check",
     "used_by": ["chat: every model answer"], "scope": "n/a",
     "returns": "Rejects the model's answer if any $ or % figure isn't in the verified facts"},
    {"id": "lending.store.load_loans", "fn": lending_store.load_loans, "kind": "SQL (vetted)",
     "used_by": ["debt dashboard"], "scope": SCOPED,
     "returns": "Loan terms, covenants, caps and collateral for the user's funds"},
    {"id": "lending.calc.loan_metrics", "fn": lending_calc.loan_metrics, "kind": "Deterministic math (Decimal)",
     "used_by": ["debt dashboard"], "scope": "Works only on the loan passed in",
     "returns": "Balance, trailing-12-month debt service, DSCR, LTV and debt yield, or a reason they can't be calculated"},
]


def tools() -> list[dict]:
    out = []
    for t in TOOLS:
        fn = t["fn"]
        sig = inspect.signature(fn)
        out.append({
            **{k: v for k, v in t.items() if k != "fn"},
            "module": fn.__module__, "function": fn.__name__,
            "parameters": [p for p in sig.parameters if p not in ("conn",)],
            "doc": (inspect.getdoc(fn) or "").split("\n")[0],
            "source_line": inspect.getsourcelines(fn)[1],
        })
    return out


def answer_types() -> list[dict]:
    return [{"intent": k, "facts_from": v} for k, v in chat.FACT_TOOLS.items()] + [
        {"intent": i, "facts_from": "none (fixed reply)"}
        for i in ("unsupported", "property_not_found", "out_of_scope")]
