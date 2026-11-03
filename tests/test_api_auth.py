"""HTTP auth flow: registration, login, bearer protection, /api/me."""
from __future__ import annotations

import time

import jwt
import pytest

from app import database as db
from app.config import settings
from helpers import bearer, login, register


# ------------------------------------------------------------------- register

def test_first_account_becomes_admin(client):
    data = register(client, "boss@example.com", "bosspass123")

    assert data["token"]
    assert data["user"]["email"] == "boss@example.com"
    assert data["user"]["is_admin"] is True

    me = client.get("/api/me", headers=bearer(data["token"])).json()
    assert me["is_admin"] is True


def test_email_is_normalised(client):
    data = register(client, "  MiXeD@Example.COM  ", "secret123")
    assert data["user"]["email"] == "mixed@example.com"
    assert db.get_user_by_email("mixed@example.com") is not None


def test_second_account_is_a_regular_user(client, admin):
    data = register(client, "second@example.com", "secret123")
    me = client.get("/api/me", headers=bearer(data["token"])).json()
    assert me["is_admin"] is False


def test_register_response_reports_the_stored_admin_flag(client, admin):
    data = register(client, "second@example.com", "secret123")
    assert data["user"]["is_admin"] is False


def test_password_is_hashed_not_stored_in_plaintext(client):
    register(client, "hash@example.com", "secret123")
    stored = db.get_user_by_email("hash@example.com")
    assert stored["password_hash"] != "secret123"
    assert stored["password_hash"].startswith("$2")


@pytest.mark.parametrize("email", ["", "nope", "@example.com", "user@", "a b@c.de"])
def test_invalid_email_is_rejected(client, email):
    resp = client.post("/api/auth/register", json={"email": email, "password": "secret123"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid email address"


def test_short_password_is_rejected_by_validation(client):
    resp = client.post("/api/auth/register", json={"email": "a@b.co", "password": "12345"})
    assert resp.status_code == 422


def test_overlong_password_is_rejected_by_validation(client):
    resp = client.post("/api/auth/register", json={"email": "a@b.co", "password": "x" * 129})
    assert resp.status_code == 422


def test_duplicate_email_is_case_insensitive(client):
    register(client, "dup@example.com", "secret123")
    resp = client.post("/api/auth/register", json={"email": "DUP@example.com", "password": "secret123"})
    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]


def test_registration_can_be_closed_for_invite_only_instances(client, patch_settings):
    register(client, "admin@example.com", "adminpass123")  # first user always allowed

    with patch_settings(allow_registration=False):
        blocked = client.post("/api/auth/register", json={"email": "new@example.com", "password": "secret123"})
        assert blocked.status_code == 403
        assert "Registration is disabled" in blocked.json()["detail"]

    assert db.user_count() == 1


# ---------------------------------------------------------------------- login

def test_login_returns_a_usable_token(client):
    register(client, "login@example.com", "secret123")

    resp = login(client, "login@example.com", "secret123")

    assert resp.status_code == 200
    body = resp.json()
    assert body["user"] == {"email": "login@example.com", "is_admin": True}
    assert client.get("/api/me", headers=bearer(body["token"])).status_code == 200


def test_login_accepts_different_casing_and_whitespace(client):
    register(client, "case@example.com", "secret123")
    assert login(client, "  CASE@Example.com ", "secret123").status_code == 200


def test_login_with_wrong_password_fails(client, admin):
    resp = login(client, admin["email"], "wrong-password")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid email or password"


def test_login_with_unknown_email_fails(client):
    resp = login(client, "ghost@example.com", "secret123")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid email or password"


def test_login_does_not_reveal_which_field_was_wrong(client, admin):
    wrong_password = login(client, admin["email"], "nope123").json()["detail"]
    wrong_email = login(client, "ghost@example.com", "nope123").json()["detail"]
    assert wrong_password == wrong_email


# ------------------------------------------------------------------ /api/me

def test_me_requires_a_token(client):
    resp = client.get("/api/me")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Missing authorization token"


def test_me_reports_instance_capabilities(client, user):
    body = client.get("/api/me", headers=user["headers"]).json()

    assert body["email"] == "user@example.com"
    assert body["demo_mode"] is True
    assert body["embedding_local"] is True
    assert body["model"] == "demo-extractive"
    assert body["max_docs"] == settings.max_docs_per_user


def test_me_reports_the_configured_model_when_a_key_is_set(client, user, patch_settings):
    with patch_settings(llm_api_key="sk-live", llm_model="gpt-4o-mini"):
        body = client.get("/api/me", headers=user["headers"]).json()

    assert body["demo_mode"] is False
    assert body["model"] == "gpt-4o-mini"


# ------------------------------------------------------------ token failures

def test_garbage_token_is_rejected(client):
    resp = client.get("/api/me", headers=bearer("not.a.jwt"))
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or expired token"


def test_token_signed_with_another_secret_is_rejected(client, user):
    uid = db.get_user_by_email("user@example.com")["id"]
    forged = jwt.encode({"sub": uid, "adm": True, "exp": time.time() + 600}, "a-wrong-secret-that-is-long-enough-to-satisfy-hs256", algorithm="HS256")

    resp = client.get("/api/me", headers=bearer(forged))
    assert resp.status_code == 401


def test_expired_token_is_rejected(client, user):
    uid = db.get_user_by_email("user@example.com")["id"]
    expired = jwt.encode(
        {"sub": uid, "adm": False, "exp": time.time() - 10}, settings.jwt_secret, algorithm="HS256"
    )

    resp = client.get("/api/me", headers=bearer(expired))
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or expired token"


def test_token_for_a_deleted_user_is_rejected(client, user):
    uid = db.get_user_by_email("user@example.com")["id"]
    db.delete_user(uid)

    resp = client.get("/api/me", headers=user["headers"])
    assert resp.status_code == 401
    assert resp.json()["detail"] == "User not found"


def test_non_bearer_authorization_scheme_is_rejected(client):
    resp = client.get("/api/me", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert resp.status_code == 401
