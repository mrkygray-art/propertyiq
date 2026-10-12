"""Admin tools: who can reach them, the lockdown request, and the live security checklist."""
import os
import secrets
import uuid

import psycopg
import pytest

from propertyiq.config import get_settings

pytestmark = pytest.mark.db

try:
    with psycopg.connect(get_settings().dsn("piq_owner")) as _c:
        _c.execute("SELECT 1 FROM demo_users LIMIT 1")
except Exception as exc:  # pragma: no cover
    pytest.skip(f"database not available: {exc}", allow_module_level=True)

from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from propertyiq.admin import checklist, lockdown  # noqa: E402
from propertyiq.auth import hash_password  # noqa: E402
from propertyiq.main import app, current_user  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def sign_in(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="module")
def analyst(client):
    return sign_in(client, "analyst", os.environ["PIQ_DEMO_PASSWORD"])


@pytest.fixture(scope="module")
def admin(client):
    """A throwaway admin account, removed afterwards; the real 'admin' user is never touched."""
    name, pw = f"pytest_admin_{uuid.uuid4().hex[:8]}", secrets.token_urlsafe(16)
    dsn = get_settings().dsn("piq_owner")
    with psycopg.connect(dsn) as conn:
        conn.execute("INSERT INTO demo_users (username, display_name, role, password_hash) VALUES (%s, %s, 'admin', %s)",
                     (name, "pytest admin", hash_password(pw)))
    yield sign_in(client, name, pw)
    with psycopg.connect(dsn) as conn:
        conn.execute("DELETE FROM demo_users WHERE username = %s", (name,))


@pytest.fixture
def launched(monkeypatch):
    """Capture the elevated launch instead of opening a Windows prompt."""
    calls = []
    monkeypatch.setattr(lockdown.subprocess, "Popen", lambda args, **kw: calls.append(args))
    return calls


ADMIN_ROUTES = [("get", "/api/admin/checklist"), ("get", "/api/admin/lockdown"), ("post", "/api/admin/lockdown")]


def test_admin_routes_need_sign_in(client):
    for method, path in ADMIN_ROUTES + [("get", "/api/lockdown/status")]:
        kw = {"json": {"mode": "off"}} if method == "post" else {}
        assert getattr(client, method)(path, **kw).status_code == 401


def test_admin_routes_refuse_non_admins(client, analyst, launched):
    for method, path in ADMIN_ROUTES:
        kw = {"json": {"mode": "app"}} if method == "post" else {}
        assert getattr(client, method)(path, headers=analyst, **kw).status_code == 403
    assert launched == []


def test_banner_status_for_any_signed_in_user(client, analyst):
    r = client.get("/api/lockdown/status", headers=analyst)
    assert r.status_code == 200 and r.json()["mode"] in ("off", "app", "computer")


def test_lockdown_request_only_accepts_fixed_modes(client, admin, launched):
    for bad in ["App; Remove-Item C:\\", "everything", "", None]:
        assert client.post("/api/admin/lockdown", headers=admin, json={"mode": bad}).status_code == 422
    assert launched == []
    r = client.post("/api/admin/lockdown", headers=admin, json={"mode": "app"})
    assert r.status_code == 202 and r.json()["requested"] == "app"
    assert len(launched) == 1
    cmd = launched[0][-1]
    assert "-Verb RunAs" in cmd and cmd.endswith("-Mode App'") and str(lockdown.SCRIPT) in cmd


def test_request_change_rejects_unknown_mode(launched):
    with pytest.raises(KeyError):
        lockdown.request_change("Computer -Force", "pytest")
    assert launched == []


def test_checklist_runs_and_core_lockdown_items_pass(client, admin):
    r = client.get("/api/admin/checklist", headers=admin)
    assert r.status_code == 200
    body = r.json()
    items = {i["key"]: i for i in body["items"]}
    assert {i["area"] for i in body["items"]} >= {"Network", "Database", "API", "Web app", "AI model",
                                                  "Secrets and files", "Firewall and internet", "Backups", "Accounts"}
    for key in ["readonly_grants", "readonly_session", "api_role", "public_create", "auth_required", "docs_off",
                "cors", "csp", "env_ignored", "no_secrets_in_code", "no_hosted_sdks", "extensions"]:
        assert items[key]["status"] == "pass", items[key]
    assert sum(body["counts"].values()) == len(body["items"])


def test_checklist_never_returns_secret_values(client, admin):
    text = client.get("/api/admin/checklist", headers=admin).text
    for key, value in os.environ.items():
        if ("PASSWORD" in key or "SECRET" in key) and len(value) >= 6:
            assert value not in text, key


def test_route_check_catches_an_unprotected_route():
    demo = FastAPI()

    @demo.get("/api/protected")
    def protected(_=Depends(current_user)):
        return {}

    @demo.get("/api/leaky")
    def leaky():
        return {}

    result = {i["key"]: i for i in checklist.check_api(demo)}["auth_required"]
    assert result["status"] == "fail" and "/api/leaky" in result["detail"] and "/api/protected" not in result["detail"]
