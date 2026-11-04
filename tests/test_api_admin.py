"""Admin-only endpoints: user listing, stats and password resets."""
from __future__ import annotations

import pytest

from app import auth, database as db
from helpers import FAQ_TEXT, bearer, login, upload


def test_admin_can_list_users(client, admin, user):
    resp = client.get("/api/admin/users", headers=admin["headers"])

    assert resp.status_code == 200
    emails = [u["email"] for u in resp.json()["users"]]
    assert emails == ["admin@example.com", "user@example.com"]
    assert "password_hash" not in resp.json()["users"][0]


def test_regular_users_cannot_list_users(client, user):
    resp = client.get("/api/admin/users", headers=user["headers"])
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Admin only"


def test_admin_routes_require_authentication(client):
    assert client.get("/api/admin/users").status_code == 401
    assert client.get("/api/admin/stats").status_code == 401


def test_admin_stats_reflect_the_database(client, admin, user):
    upload(client, user["headers"], ("faq.txt", FAQ_TEXT.encode()))
    client.post("/api/chat", headers=user["headers"], json={"message": "hello"})

    stats = client.get("/api/admin/stats", headers=admin["headers"]).json()

    assert stats["users"] == 2
    assert stats["documents"] == 1
    assert stats["chunks"] >= 1
    assert stats["conversations"] == 1


def test_regular_users_cannot_read_stats(client, user):
    assert client.get("/api/admin/stats", headers=user["headers"]).status_code == 403


def test_admin_resets_a_password(client, admin, user):
    uid = db.get_user_by_email("user@example.com")["id"]

    resp = client.post(
        f"/api/admin/users/{uid}/reset-password", headers=admin["headers"], json={"new_password": "brand-new-pass"}
    )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert login(client, "user@example.com", "brand-new-pass").status_code == 200
    assert login(client, "user@example.com", "userpass123").status_code == 401


def test_reset_unknown_user_returns_404(client, admin):
    resp = client.post(
        "/api/admin/users/ghost/reset-password", headers=admin["headers"], json={"new_password": "whatever123"}
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "User not found"


@pytest.mark.parametrize("new_password", ["12345", "x" * 129])
def test_reset_validates_password_length(client, admin, user, new_password):
    uid = db.get_user_by_email("user@example.com")["id"]
    resp = client.post(
        f"/api/admin/users/{uid}/reset-password",
        headers=admin["headers"],
        json={"new_password": new_password},
    )
    assert resp.status_code == 422


def test_non_admins_cannot_reset_passwords(client, user):
    uid = db.get_user_by_email("user@example.com")["id"]
    resp = client.post(
        f"/api/admin/users/{uid}/reset-password", headers=user["headers"], json={"new_password": "another-pass"}
    )
    assert resp.status_code == 403
    assert login(client, "user@example.com", "userpass123").status_code == 200


def test_admin_reset_revokes_existing_sessions(client, admin):
    # A password reset bumps the token version, so every JWT minted before it
    # dies immediately — a reset means "something may be compromised".
    uid = db.get_user_by_email("admin@example.com")["id"]
    client.post(
        f"/api/admin/users/{uid}/reset-password", headers=admin["headers"], json={"new_password": "adminpass456"}
    )
    assert client.get("/api/me", headers=admin["headers"]).status_code == 401
    fresh = login(client, "admin@example.com", "adminpass456")
    assert fresh.status_code == 200


def test_password_reset_through_the_service_layer(client, user):
    """The docs advertise this as a recovery path when the admin is locked out."""
    uid = db.get_user_by_email("user@example.com")["id"]
    db.update_password(uid, auth.hash_password("recovered123"))

    assert login(client, "user@example.com", "recovered123").status_code == 200
    # a reset also revokes the sessions issued before it
    assert client.get("/api/me", headers=bearer(user["token"])).status_code == 401
