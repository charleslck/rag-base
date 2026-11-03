"""Static assets, health/status endpoint and generic 404 behaviour."""
from __future__ import annotations

import pytest


def test_status_is_public_and_reports_demo_mode(client):
    body = client.get("/api/status").json()

    assert body == {"demo_mode": True, "embedding_local": True, "model": "demo-extractive"}


def test_status_reflects_a_configured_model(client, patch_settings):
    with patch_settings(llm_api_key="sk-live", embedding_api_key="sk-embed", llm_model="gpt-4o-mini"):
        body = client.get("/api/status").json()

    assert body == {"demo_mode": False, "embedding_local": False, "model": "gpt-4o-mini"}


def test_index_page_is_served(client):
    resp = client.get("/")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "RAGbase" in resp.text
    assert "/static/app.js" in resp.text


def test_widget_page_is_served(client):
    resp = client.get("/widget")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "RAGbase" in resp.text


@pytest.mark.parametrize("path", ["/static/app.js", "/static/styles.css", "/static/widget.html"])
def test_static_assets_are_served(client, path):
    assert client.get(path).status_code == 200


def test_unknown_static_asset_returns_404(client):
    assert client.get("/static/missing-file.js").status_code == 404


def test_unknown_api_route_returns_404(client):
    assert client.get("/api/nope").status_code == 404


def test_method_not_allowed_is_reported(client, user):
    assert client.put("/api/documents", headers=user["headers"]).status_code == 405


def test_openapi_schema_lists_the_main_routes(client):
    schema = client.get("/openapi.json").json()
    paths = set(schema["paths"])

    for expected in (
        "/api/auth/register",
        "/api/auth/login",
        "/api/me",
        "/api/documents",
        "/api/chat",
        "/api/widget/chat",
        "/api/admin/stats",
    ):
        assert expected in paths
