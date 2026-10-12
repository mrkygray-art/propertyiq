"""Request traces: stages, IDs and timings for each chat answer. Never the question or prompt text.

Written with piq_trace_writer (request_traces only). Older than the retention period (default 30 days,
PIQ_TRACE_RETENTION_DAYS) is deleted on every write. A failed write never fails the chat answer.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid

import psycopg
from psycopg.types.json import Jsonb

from propertyiq.config import get_settings, load_dotenv

RETENTION_DAYS = int(os.environ.get("PIQ_TRACE_RETENTION_DAYS", "30"))
# Response times count answers built from data, not fixed replies (which take a few milliseconds).
DATA_ANSWER = "intent NOT IN ('unsupported', 'property_not_found', 'out_of_scope')"
LIST_COLUMNS = ("trace_id, created_at, username, endpoint, period, intent, answer_source, model, "
                "verification_status, numbers_checked, unsupported_numbers, cited_property_ids, total_ms, model_ms")


def _writer() -> psycopg.Connection:
    load_dotenv()
    s = get_settings()
    return psycopg.connect(host=s.db_host, port=s.db_port, dbname=s.db_name, user="piq_trace_writer",
                           password=os.environ.get("PIQ_TRACE_PASSWORD", ""), connect_timeout=3)


def facts_hash(facts: dict) -> str | None:
    if not facts:
        return None
    return hashlib.sha256(json.dumps(facts, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def record(user, question: str, result, endpoint: str = "chat") -> str | None:
    """Store one trace; returns its ID, or None if tracing is unavailable."""
    trace_id = str(uuid.uuid4())
    record_ids = sorted({i for c in result.citations for ids in c.get("source_records", {}).values() for i in ids})
    try:
        with _writer() as conn:
            conn.execute(
                "INSERT INTO request_traces (trace_id, username, funds, endpoint, period, intent, answer_source, "
                "model, verification_status, numbers_checked, unsupported_numbers, cited_property_ids, "
                "source_record_ids, facts_sha256, question_chars, prompt_tokens, output_tokens, model_ms, "
                "total_ms, stages, notes) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "%s, %s, %s, %s, %s, %s, %s)",
                (trace_id, user.username, list(user.portfolios), endpoint, result.period, result.intent, result.answer_source,
                 result.llm.get("model"), result.verification.get("status", "not_applicable"),
                 result.verification.get("numbers_checked", 0), len(result.verification.get("unsupported_numbers", [])),
                 [c["property_id"] for c in result.citations], record_ids, facts_hash(result.facts),
                 len(question), result.llm.get("prompt_tokens"), result.llm.get("output_tokens"),
                 result.llm.get("model_ms"), result.latency_ms, Jsonb(result.stages), result.notes))
            conn.execute("DELETE FROM request_traces WHERE created_at < now() - make_interval(days => %s)",
                         (RETENTION_DAYS,))
        return trace_id
    except psycopg.Error:
        return None


def visible_clause(user) -> tuple[str, dict]:
    """Admins see every trace; everyone else sees only their own."""
    if user.role == "admin":
        return "TRUE", {}
    return "username = %(username)s", {"username": user.username}


def list_traces(conn: psycopg.Connection, user, limit: int = 100) -> list[dict]:
    where, args = visible_clause(user)
    rows = conn.execute(f"SELECT {LIST_COLUMNS} FROM request_traces WHERE {where} "
                        f"ORDER BY created_at DESC LIMIT %(limit)s", {**args, "limit": limit}).fetchall()
    return [_jsonable(r) for r in rows]


def get_trace(conn: psycopg.Connection, user, trace_id: str) -> dict | None:
    where, args = visible_clause(user)
    row = conn.execute(f"SELECT * FROM request_traces WHERE trace_id = %(id)s AND {where}",
                       {**args, "id": trace_id}).fetchone()
    return _jsonable(row) if row else None


def stats(conn: psycopg.Connection, user) -> dict:
    where, args = visible_clause(user)
    row = conn.execute(
        f"SELECT count(*) AS total, count(*) FILTER (WHERE answer_source = 'local_model') AS model_answers, "
        f"count(*) FILTER (WHERE verification_status = 'rejected') AS rejected, "
        f"count(*) FILTER (WHERE answer_source = 'verified_template') AS template_answers, "
        f"percentile_cont(0.5) WITHIN GROUP (ORDER BY total_ms) FILTER (WHERE {DATA_ANSWER}) AS p50_ms, "
        f"percentile_cont(0.95) WITHIN GROUP (ORDER BY total_ms) FILTER (WHERE {DATA_ANSWER}) AS p95_ms, "
        f"min(created_at) AS oldest "
        f"FROM request_traces WHERE {where}", args).fetchone()
    return {**_jsonable(row), "retention_days": RETENTION_DAYS}


def _jsonable(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            v = v.isoformat()
        elif isinstance(v, uuid.UUID):
            v = str(v)
        elif hasattr(v, "is_finite"):   # Decimal
            v = float(v)
        out[k] = v
    return out


