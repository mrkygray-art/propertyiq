"""Shared test setup: chat answers and sign-ins made during tests write AI Lab traces and access audit events;
remove them when the run ends, so the Lab and the Admin page only show real use. (Real use during a test run
is removed too; don't run the tests while someone is using the app.)"""
from datetime import datetime, timezone

import pytest


@pytest.fixture(scope="session", autouse=True)
def remove_test_traces():
    started = datetime.now(timezone.utc)
    yield
    try:
        import psycopg
        from propertyiq.config import get_settings
        with psycopg.connect(get_settings().dsn("piq_owner"), connect_timeout=3) as conn:
            conn.execute("DELETE FROM request_traces WHERE created_at >= %s", (started,))
            if conn.execute("SELECT to_regclass('piq.access_audit') IS NOT NULL").fetchone()[0]:
                conn.execute("DELETE FROM access_audit WHERE created_at >= %s", (started,))
    except Exception:
        pass   # no database, or traces not set up: nothing to clean
