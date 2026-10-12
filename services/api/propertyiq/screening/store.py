"""Saved lender guideline sets (table screening_guideline_sets).

Reads use the API's read-only connection. Writes use piq_screening_writer, which can
change this one table and nothing else. Sets are shared by every signed-in user.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row

from propertyiq import db
from propertyiq.config import get_settings, load_dotenv
from propertyiq.screening.calc import PROPERTY_TYPES, GuidelineSet, Rule


class GuidelineError(ValueError):
    """A guideline set request that can't be carried out (shown to the user)."""


def _writer() -> psycopg.Connection:
    load_dotenv()
    s = get_settings()
    return psycopg.connect(host=s.db_host, port=s.db_port, dbname=s.db_name, user="piq_screening_writer",
                           password=os.environ.get("PIQ_SCREENING_PASSWORD", ""), connect_timeout=5,
                           row_factory=dict_row)


def _rule(raw: dict | None) -> Rule:
    raw = raw or {}
    t = raw.get("threshold")
    return Rule(bool(raw.get("enabled")), Decimal(str(t)) if t is not None else None)


def to_guideline_set(name: str, rules: dict) -> GuidelineSet:
    types = rules.get("allowed_types") or {}
    return GuidelineSet(
        name=name,
        min_dscr=_rule(rules.get("min_dscr")),
        max_ltv=_rule(rules.get("max_ltv")),
        min_debt_yield=_rule(rules.get("min_debt_yield")),
        max_break_even=_rule(rules.get("max_break_even")),
        min_loan=_rule(rules.get("min_loan")),
        max_loan=_rule(rules.get("max_loan")),
        allowed_types=tuple(t for t in types.get("types", PROPERTY_TYPES) if t in PROPERTY_TYPES),
        allowed_types_enabled=bool(types.get("enabled")),
        dscr_basis=rules.get("dscr_basis", "amortizing"),
    )


def list_sets() -> list[dict]:
    with db.connection() as conn:
        return conn.execute(
            "SELECT set_id, name, rules, updated_at FROM screening_guideline_sets ORDER BY set_id"
        ).fetchall()


def get_set(set_id: int) -> dict | None:
    with db.connection() as conn:
        return conn.execute(
            "SELECT set_id, name, rules, updated_at FROM screening_guideline_sets WHERE set_id = %s", (set_id,)
        ).fetchone()


def save_set(name: str, rules: dict, set_id: int | None = None) -> dict:
    try:
        with _writer() as conn:
            if set_id is None:
                row = conn.execute(
                    "INSERT INTO screening_guideline_sets (name, rules) VALUES (%s, %s) "
                    "RETURNING set_id, name, rules, updated_at", (name, json.dumps(rules))).fetchone()
            else:
                row = conn.execute(
                    "UPDATE screening_guideline_sets SET name = %s, rules = %s, updated_at = now() "
                    "WHERE set_id = %s RETURNING set_id, name, rules, updated_at",
                    (name, json.dumps(rules), set_id)).fetchone()
                if row is None:
                    raise GuidelineError("That guideline set no longer exists.")
            return row
    except psycopg.errors.UniqueViolation:
        raise GuidelineError(f"A guideline set named \"{name}\" already exists.") from None


def delete_set(set_id: int) -> None:
    with _writer() as conn:
        count = conn.execute("SELECT count(*) AS n FROM screening_guideline_sets").fetchone()["n"]
        if count <= 1:
            raise GuidelineError("Keep at least one guideline set.")
        if conn.execute("DELETE FROM screening_guideline_sets WHERE set_id = %s", (set_id,)).rowcount == 0:
            raise GuidelineError("That guideline set no longer exists.")
