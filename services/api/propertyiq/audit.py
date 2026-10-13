"""Access audit: who did what, when, and whether it was allowed (table access_audit).

Recorded: every sign-in (and failed sign-in), every refusal (by role or by fund), and every change, export
and admin action that succeeds. Never recorded: passwords, tokens, document contents, notes or any other
text the user typed. A sign-in attempt for a name that doesn't exist is stored as '(unknown)', because a
mistyped password is sometimes pasted into the username box.

Written with piq_audit_writer (this table only; scripts/access_setup.py). Rows older than the retention
period (default 90 days, PIQ_AUDIT_RETENTION_DAYS) are deleted on every write. A failed audit write never
fails the request; the checklist shows whether auditing works.
"""
from __future__ import annotations

import os

import psycopg

from propertyiq.config import get_settings, load_dotenv

RETENTION_DAYS = int(os.environ.get("PIQ_AUDIT_RETENTION_DAYS", "90"))
UNKNOWN_USER = "(unknown)"
COLUMNS = "event_id, created_at, username, role, action, outcome, reason, target_type, target_id, fund_id"


def _writer() -> psycopg.Connection:
    load_dotenv()
    s = get_settings()
    return psycopg.connect(host=s.db_host, port=s.db_port, dbname=s.db_name, user="piq_audit_writer",
                           password=os.environ.get("PIQ_AUDIT_PASSWORD", ""), connect_timeout=3)


def record(username: str, role: str | None, action: str, outcome: str, reason: str | None = None,
           target_type: str | None = None, target_id=None, fund_id: str | None = None) -> bool:
    """Write one event. Returns False (and never raises) if the audit log is unavailable."""
    try:
        with _writer() as conn:
            conn.execute(
                "INSERT INTO access_audit (username, role, action, outcome, reason, target_type, target_id, fund_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (username, role, action, outcome, reason, target_type,
                 None if target_id is None else str(target_id), fund_id))
            conn.execute("DELETE FROM access_audit WHERE created_at < now() - make_interval(days => %s)",
                         (RETENTION_DAYS,))
        return True
    except psycopg.Error:
        return False


def allowed(user, action: str, target_type: str | None = None, target_id=None, fund_id: str | None = None) -> bool:
    return record(user.username, user.role, action, "allowed", None, target_type, target_id, fund_id)


def refused(user, action: str, reason: str, target_type: str | None = None, target_id=None,
            fund_id: str | None = None) -> bool:
    return record(user.username, user.role, action, "refused", reason, target_type, target_id, fund_id)


def recent(conn: psycopg.Connection, limit: int = 200, outcome: str | None = None) -> dict:
    """For the Admin page: newest events first, plus counts for the retention window."""
    where, args = ("WHERE outcome = %(outcome)s", {"outcome": outcome}) if outcome else ("", {})
    rows = conn.execute(f"SELECT {COLUMNS} FROM access_audit {where} ORDER BY created_at DESC, event_id DESC "
                        f"LIMIT %(limit)s", {**args, "limit": limit}).fetchall()
    stats = conn.execute(
        "SELECT count(*) AS total, count(*) FILTER (WHERE outcome = 'refused') AS refused, "
        "count(*) FILTER (WHERE action = 'auth.sign_in' AND outcome = 'refused') AS failed_sign_ins, "
        "min(created_at) AS oldest FROM access_audit").fetchone()
    return {
        "retention_days": RETENTION_DAYS,
        "total": stats["total"], "refused": stats["refused"], "failed_sign_ins": stats["failed_sign_ins"],
        "oldest": stats["oldest"].isoformat() if stats["oldest"] else None,
        "events": [{**r, "created_at": r["created_at"].isoformat()} for r in rows],
    }
