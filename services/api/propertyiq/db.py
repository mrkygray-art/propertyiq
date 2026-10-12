"""Read-only connection pool. The API only ever connects as piq_readonly."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from propertyiq.config import get_settings

_pool: ConnectionPool | None = None


def open_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            get_settings().dsn("piq_readonly"),
            min_size=1, max_size=5, open=False,
            kwargs={"row_factory": dict_row},
        )
        _pool.open(wait=False)
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    with open_pool().connection(timeout=5) as conn:
        yield conn


def database_ready() -> bool:
    try:
        with connection() as conn:
            return conn.execute("SELECT 1 AS ok").fetchone()["ok"] == 1
    except Exception:
        return False
