"""Account security that an on-premise install still needs.

No email verification, no self-service reset links and no invitations: those
belonged to the hosted version. What remains is the login throttle, password
change with session revocation, and JWT version compatibility.
"""
from __future__ import annotations

import time

import jwt
import pytest

from app import database as db
from app.config import settings
from helpers import bearer, login, register


# ---------------------------------------------------------------- registration

def test_registration_is_local_and_creates_no_mail_tokens(client):
    data = register(client, "local@example.com", "secret123")
    assert data["token"]
    assert data["user"] == {"email": "local@example.com", "is_admin": True}
    # no hosted-account plumbing is exposed at all
    for path in ("/api/auth/verify-email", "/api/auth/forgot-password",
                 "/api/auth/reset-password", "/api/org/invites"):
        assert client.post(path, json={}).status_code == 404


def test_me_reports_only_the_account(client, user):
    body = client.get("/api/me", headers=user["headers"]).json()
    assert body["email"] == "user@example.com"
    assert "org" not in body and "kbs" not in body and "role" not in body


# ---------------------------------------------------------------- password

def test_change_password_requires_the_current_one(client, user):
    assert client.post("/api/auth/change-password", headers=user["headers"], json={
        "current_password": "wrong", "new_password": "changed123",
    }).status_code == 401

    resp = client.post("/api/auth/change-password", headers=user["headers"], json={
        "current_password": user["password"], "new_password": "changed123",
    })
    assert resp.status_code == 200
    # the response carries a replacement token: old one died with the password
    assert client.get("/api/me", headers=user["headers"]).status_code == 401
    assert client.get("/api/me", headers=bearer(resp.json()["token"])).status_code == 200
    assert login(client, user["email"], "changed123").status_code == 200


def test_admin_reset_revokes_existing_sessions(client, admin, user):
    """The operator's recovery path (no mail server) also kills old tokens."""
    uid = db.get_user_by_email("user@example.com")["id"]
    resp = client.post(f"/api/admin/users/{uid}/reset-password",
                       headers=admin["headers"], json={"new_password": "reset123"})
    assert resp.status_code == 200
    assert client.get("/api/me", headers=user["headers"]).status_code == 401
    assert login(client, user["email"], "reset123").status_code == 200


def test_sign_out_everywhere_kills_only_the_callers_sessions(client, user):
    other = register(client, "bystander@example.com", "secret123")

    assert client.post("/api/auth/sessions/revoke", headers=user["headers"]).status_code == 200
    assert client.get("/api/me", headers=user["headers"]).status_code == 401
    assert client.get("/api/me", headers=bearer(other["token"])).status_code == 200


# ---------------------------------------------------------------- login throttle

def test_login_locks_after_repeated_failures(client, user):
    for _ in range(settings.login_max_attempts):
        assert login(client, user["email"], "wrongpass").status_code == 401

    locked = login(client, user["email"], "wrongpass")
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0
    # even the right password is refused while the window is open
    assert login(client, user["email"], user["password"]).status_code == 429


def test_a_successful_login_clears_the_failure_counter(client, user):
    for _ in range(settings.login_max_attempts - 1):
        login(client, user["email"], "wrongpass")
    assert login(client, user["email"], user["password"]).status_code == 200
    assert login(client, user["email"], "wrongpass").status_code == 401


def test_the_lockout_is_per_email_and_ip(client, user):
    for _ in range(settings.login_max_attempts):
        login(client, "nobody@example.com", "wrongpass")
    assert login(client, user["email"], user["password"]).status_code == 200


# ---------------------------------------------------------------- token internals

def test_tokens_without_a_version_claim_still_work(client, user):
    """JWTs minted before the version counter existed count as version 0."""
    uid = db.get_user_by_email(user["email"])["id"]
    legacy = jwt.encode({"sub": uid, "adm": False, "exp": time.time() + 600},
                        settings.jwt_secret, algorithm="HS256")
    assert client.get("/api/me", headers=bearer(legacy)).status_code == 200
