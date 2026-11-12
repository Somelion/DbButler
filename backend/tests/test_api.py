"""HTTP-level checks through FastAPI's TestClient. The client is created
without a `with` block, so the app's lifespan (store migrations, scheduler)
never runs — nothing here needs a database."""

import os

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.version import APP_VERSION

client = TestClient(app)
AUTH = {"Authorization": f"Bearer {os.environ['AUTH_TOKEN']}"}


def test_health_is_open():
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("path", ["/version", "/api/version"])
def test_version_is_open_and_matches_the_version_file(path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.json() == {"version": APP_VERSION}
    assert APP_VERSION != "unknown"


def test_api_requires_the_token():
    assert client.get("/api/auth/check").status_code == 401
    assert client.get("/api/auth/check", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/auth/check", headers=AUTH).json() == {"ok": True}


def test_non_ascii_auth_header_is_a_401_not_a_500():
    response = client.get("/api/auth/check", headers={"Authorization": "Bearer töken".encode("latin-1")})
    assert response.status_code == 401


def test_every_api_route_is_protected():
    open_paths = {"/api/version"}
    # The machine integrations carry their own narrower token instead.
    integration_guards = {"/api/mcp": "require_mcp_token", "/api/metrics": "require_metrics_token", "/metrics": "require_metrics_token"}
    for route in app.routes:
        path = getattr(route, "path", "")
        if path in integration_guards:
            names = [dep.call.__name__ for dep in route.dependant.dependencies]
            assert names == [integration_guards[path]], path
            continue
        if not path.startswith("/api/") or path in open_paths:
            continue
        dependencies = [dep.call for dep in route.dependant.dependencies]
        assert any(dep.__name__ == "require_auth" for dep in dependencies), path


def test_openapi_reports_the_app_version():
    assert app.version == APP_VERSION
    assert app.title == "DbButler API"
