"""Read-only views of the running system for the AI Engineering Lab: models, schema, offline evidence."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import psycopg

from propertyiq.config import REPO_ROOT, get_settings

# Short descriptions for the data dictionary (tables not listed show their name only).
TABLE_NOTES = {
    "portfolios": "Synthetic funds (FUND-I, FUND-II)",
    "properties": "55 synthetic properties; each belongs to one fund",
    "monthly_financials": "Monthly actual revenue, expenses, NOI (generated column) and occupancy",
    "monthly_budgets": "Monthly budget revenue, expenses and NOI",
    "demo_users": "Demo sign-ins: role and scrypt password hash",
    "user_portfolio_access": "Which funds each user may see; joined by every scoped query",
    "seed_runs": "Seed provenance: generator version, random seed and content SHA-256",
    "loans": "Synthetic loans; each belongs to one fund",
    "loan_collateral": "Loan to property links with the share pledged (many-to-many)",
    "loan_covenants": "Per-loan covenant thresholds",
    "rate_caps": "Interest-rate caps on floating loans",
    "index_rates": "SYN-SOFR monthly fixings (synthetic)",
    "property_valuations": "Annual synthetic valuations (direct capitalization)",
    "lenders": "Synthetic lenders",
    "request_traces": "AI request traces: stages, IDs, timings; no question text; 30-day retention",
    "access_audit": "Sign-ins, refusals and changes: who, what, when, allowed or not; no passwords or contents; 90-day retention",
    "schema_migrations": "Applied database migrations",
    "screening_guideline_sets": "Loan screening: saved lender guideline sets",
    "screening_deals": "Loan screening: deals under review; each belongs to one fund",
    "screening_documents": "Loan screening: uploaded rent rolls and T-12s (files stay in data/uploads)",
    "screening_extracted_items": "Loan screening: values read from documents, awaiting review",
    "screening_rent_roll_units": "Loan screening: approved rent roll units (tenant names never stored)",
    "screening_t12_lines": "Loan screening: approved T-12 income and expense lines",
    "screening_flags": "Loan screening: automatic flags and their review notes",
    "screening_benchmarks": "Loan screening: expense benchmarks",
    "screening_rules": "Loan screening: California rules with source links and verified dates",
    "screening_market_cache": "Loan screening: synthetic market comparisons",
    "screening_adjustments": "Loan screening: underwriting adjustments",
    "screening_condition_items": "Loan screening: property condition items",
    "screening_condition_life": "Loan screening: useful-life table for building components",
    "screening_settings": "Loan screening: settings",
}


def models() -> dict:
    s = get_settings()
    out = {"ollama_url": s.ollama_url, "chat_model": s.chat_model, "embed_model": s.embed_model,
           "allowlist": list(s.allowed_models), "models": [], "reachable": False}
    try:
        with httpx.Client(base_url=s.ollama_url, timeout=5, trust_env=False) as c:
            tags = c.get("/api/tags").json().get("models", [])
            version = c.get("/api/version").json().get("version")
            running = {m["name"] for m in c.get("/api/ps").json().get("models", [])}
            out["reachable"], out["ollama_version"] = True, version
            for m in tags:
                d = m.get("details", {})
                try:
                    caps = c.post("/api/show", json={"model": m["name"]}).json().get("capabilities", [])
                except Exception:
                    caps = []
                name = m["name"]
                role = ("chat (in use)" if name in (s.chat_model, f"{s.chat_model}:latest")
                        else "embeddings (reserved for document search)" if name.startswith(s.embed_model)
                        else "installed, not used")
                out["models"].append({
                    "name": name, "role": role, "family": d.get("family"), "parameters": d.get("parameter_size"),
                    "quantization": d.get("quantization_level"), "format": d.get("format"),
                    "size_gb": round(m.get("size", 0) / 1e9, 2), "digest": (m.get("digest") or "")[:12],
                    "modified": m.get("modified_at", "")[:10], "capabilities": caps,
                    "allowlisted": name in s.allowed_models or name.removesuffix(":latest") in s.allowed_models,
                    "loaded_in_memory": name in running, "cloud": "cloud" in name,
                })
    except (httpx.HTTPError, ValueError):
        pass
    return out


def schema(conn: psycopg.Connection, show_counts: bool) -> dict:
    cols = conn.execute(
        "SELECT table_name, column_name, data_type, is_nullable, column_default IS NOT NULL AS has_default "
        "FROM information_schema.columns WHERE table_schema = 'piq' ORDER BY table_name, ordinal_position").fetchall()
    # pg_catalog, not information_schema: the latter hides constraints from logins that don't own the tables.
    fks = conn.execute(
        "SELECT cl.relname AS table_name, a.attname AS column_name, rf.relname AS ref_table, af.attname AS ref_column "
        "FROM pg_constraint c JOIN pg_class cl ON cl.oid = c.conrelid "
        "JOIN pg_namespace n ON n.oid = cl.relnamespace JOIN pg_class rf ON rf.oid = c.confrelid "
        "CROSS JOIN LATERAL unnest(c.conkey, c.confkey) AS k(col, fcol) "
        "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.col "
        "JOIN pg_attribute af ON af.attrelid = c.confrelid AND af.attnum = k.fcol "
        "WHERE c.contype = 'f' AND n.nspname = 'piq' ORDER BY 1, 2").fetchall()
    tables: dict[str, dict] = {}
    for c in cols:
        t = tables.setdefault(c["table_name"], {"name": c["table_name"], "note": TABLE_NOTES.get(c["table_name"]),
                                                "columns": [], "references": [], "rows": None})
        t["columns"].append({"name": c["column_name"], "type": c["data_type"], "nullable": c["is_nullable"] == "YES"})
    for f in fks:
        if f["table_name"] in tables:
            ref = {"column": f["column_name"], "table": f["ref_table"], "ref_column": f["ref_column"]}
            if ref not in tables[f["table_name"]]["references"]:
                tables[f["table_name"]]["references"].append(ref)
    if show_counts:
        for name, t in tables.items():
            t["rows"] = conn.execute(f'SELECT count(*) AS n FROM piq."{name}"').fetchone()["n"]
    grants = conn.execute(
        "SELECT r.rolname, count(*) FILTER (WHERE has_table_privilege(r.rolname, c.oid, 'SELECT')) AS can_read, "
        "count(*) FILTER (WHERE has_table_privilege(r.rolname, c.oid, 'INSERT, UPDATE, DELETE')) AS can_write "
        "FROM pg_roles r CROSS JOIN pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'piq' AND c.relkind = 'r' AND r.rolname LIKE 'piq\\_%%' GROUP BY r.rolname ORDER BY 1").fetchall()
    return {"tables": sorted(tables.values(), key=lambda t: t["name"]), "row_counts_shown": show_counts,
            "roles": [dict(g) for g in grants]}


def offline_evidence(limit: int = 6) -> list[dict]:
    """Summaries of the lockdown script's before/after evidence files (newest first)."""
    out = []
    for f in sorted((REPO_ROOT / "logs").glob("lockdown-evidence-*.json"), reverse=True)[:limit]:
        try:
            e = json.loads(f.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        conns = e.get("established_non_loopback_connections") or []
        if isinstance(conns, dict):
            conns = [conns]
        piq = [c for c in conns if (c.get("process") or "").lower() in ("ollama", "ollama app", "python", "node",
                                                                             "com.docker.backend")]
        out.append({"file": f.name, "label": e.get("label"), "at": e.get("at"),
                    "mode": (e.get("status") or {}).get("mode"),
                    "test_connection_succeeded": e.get("powershell_can_reach_1_1_1_1_443"),
                    "outside_connections": len(conns), "propertyiq_outside_connections": len(piq),
                    "propertyiq_processes": sorted({c.get("process") for c in piq})})
    return out
