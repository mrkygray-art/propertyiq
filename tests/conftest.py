"""Shared test setup: chat answers made during tests write AI Lab traces; remove them when the run ends, so
the Lab only shows real use."""
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
    except Exception:
        pass   # no database, or traces not set up: nothing to clean
