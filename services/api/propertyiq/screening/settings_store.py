"""Editable reference data: expense benchmarks, jurisdiction rules, component typical life, settings.

Every benchmark and life entry needs a source and a last-reviewed date; a rule can only be marked
verified if it has a source URL and a last-verified date. Shipped examples stay labeled as examples
until the lender replaces them.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from propertyiq import db
from propertyiq.screening.store import _writer
from propertyiq.screening.workspace import WorkspaceError


def list_benchmarks() -> list[dict]:
    with db.connection() as conn:
        return conn.execute("SELECT * FROM screening_benchmarks ORDER BY property_type, category, basis").fetchall()


def save_benchmark(b: dict, benchmark_id: int | None = None) -> None:
    if not str(b.get("source", "")).strip():
        raise WorkspaceError("Every benchmark needs a source.")
    if not b.get("last_reviewed"):
        raise WorkspaceError("Every benchmark needs a last-reviewed date.")
    vals = [b.get(k) for k in ("low", "typical", "high")]
    nums = [Decimal(str(v)) for v in vals if v not in (None, "")]
    if any(n < 0 for n in nums) or nums != sorted(nums):
        raise WorkspaceError("Benchmark values must be zero or more, with low ≤ typical ≤ high.")
    params = (b["property_type"], b["category"], b["basis"], *[None if v in (None, "") else v for v in vals],
              b["source"].strip(), b["last_reviewed"], bool(b.get("is_example", False)))
    with _writer() as conn:
        if benchmark_id is None:
            conn.execute("INSERT INTO screening_benchmarks (property_type, category, basis, low, typical, high, source, "
                         "last_reviewed, is_example) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", params)
        elif conn.execute("UPDATE screening_benchmarks SET property_type=%s, category=%s, basis=%s, low=%s, typical=%s, "
                          "high=%s, source=%s, last_reviewed=%s, is_example=%s WHERE benchmark_id=%s",
                          (*params, benchmark_id)).rowcount == 0:
            raise LookupError("Benchmark not found")


def delete_benchmark(benchmark_id: int) -> None:
    with _writer() as conn:
        if conn.execute("DELETE FROM screening_benchmarks WHERE benchmark_id = %s", (benchmark_id,)).rowcount == 0:
            raise LookupError("Benchmark not found")


def list_rules() -> list[dict]:
    with db.connection() as conn:
        return conn.execute("SELECT * FROM screening_rules ORDER BY flag_id, rule_key").fetchall()


def save_rule(rule_key: str, r: dict) -> None:
    verified = bool(r.get("verified"))
    if verified and not (str(r.get("source_url") or "").startswith("http") and r.get("last_verified")):
        raise WorkspaceError("A rule can be marked verified only with a source URL and a last-verified date.")
    trigger = r.get("trigger")
    if isinstance(trigger, str):
        try:
            trigger = json.loads(trigger)
        except json.JSONDecodeError:
            raise WorkspaceError("Trigger must be valid JSON.") from None
    with _writer() as conn:
        if conn.execute(
                "UPDATE screening_rules SET description=%s, source_url=%s, last_verified=%s, verified=%s, notes=%s, "
                "trigger=%s WHERE rule_key=%s",
                (r["description"], r.get("source_url") or None, r.get("last_verified") or None, verified,
                 r.get("notes") or "", json.dumps(trigger or {}), rule_key)).rowcount == 0:
            raise LookupError("Rule not found")


def list_condition_life() -> list[dict]:
    with db.connection() as conn:
        return conn.execute("SELECT * FROM screening_condition_life ORDER BY label").fetchall()


def save_condition_life(item_key: str, c: dict) -> None:
    if not str(c.get("source", "")).strip() or not c.get("last_reviewed"):
        raise WorkspaceError("Each entry needs a source and a last-reviewed date.")
    life = c.get("typical_life")
    if life not in (None, "") and int(life) <= 0:
        raise WorkspaceError("Typical life must be more than 0 years.")
    with _writer() as conn:
        if conn.execute("UPDATE screening_condition_life SET typical_life=%s, flag_if=%s, source=%s, last_reviewed=%s, "
                        "is_example=%s WHERE item_key=%s",
                        (None if life in (None, "") else int(life), c.get("flag_if") or None, c["source"].strip(),
                         c["last_reviewed"], bool(c.get("is_example", False)), item_key)).rowcount == 0:
            raise LookupError("Item not found")


def settings() -> dict[str, dict]:
    with db.connection() as conn:
        return {r["key"]: r for r in conn.execute("SELECT key, value, description FROM screening_settings ORDER BY key")}


def setting(key: str, default: str) -> str:
    return settings().get(key, {}).get("value", default)


def save_setting(key: str, value: str) -> None:
    try:
        if Decimal(value) < 0:
            raise ValueError
    except Exception:
        raise WorkspaceError("Settings must be zero or a positive number.") from None
    with _writer() as conn:
        if conn.execute("UPDATE screening_settings SET value = %s WHERE key = %s", (value, key)).rowcount == 0:
            raise LookupError("Setting not found")


def today() -> str:
    return date.today().isoformat()
