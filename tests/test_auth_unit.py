"""Auth primitives: bcrypt hashing, JWT issuing, email validation."""
from __future__ import annotations

import time

import jwt
import pytest

from app import auth
from app.config import settings


# ------------------------------------------------------------------ passwords

def test_hash_is_bcrypt_and_not_reversible():
    hashed = auth.hash_password("correct horse battery staple")
    assert hashed.startswith(("$2a$", "$2b$", "$2y$"))
    assert "correct horse" not in hashed


def test_hash_uses_a_fresh_salt_every_time():
    first = auth.hash_password("same-password")
    second = auth.hash_password("same-password")
    assert first != second
    assert auth.verify_password("same-password", first)
    assert auth.verify_password("same-password", second)


def test_verify_password_matches_only_the_right_password():
    hashed = auth.hash_password("s3cret!!")
    assert auth.verify_password("s3cret!!", hashed) is True
    assert auth.verify_password("S3cret!!", hashed) is False
    assert auth.verify_password("", hashed) is False


@pytest.mark.parametrize("broken_hash", ["", "not-a-hash", "$2b$12$tooshort", "plaintext"])
def test_verify_password_returns_false_for_malformed_hashes(broken_hash):
    # bcrypt raises ValueError on a bad salt; the wrapper must swallow it.
    assert auth.verify_password("whatever", broken_hash) is False


def test_long_passwords_are_supported():
    password = "p" * 72
    hashed = auth.hash_password(password)
    assert auth.verify_password(password, hashed) is True


# ----------------------------------------------------------------------- JWT

def test_make_token_round_trip():
    token = auth.make_token("user-123", is_admin=True)
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    assert payload["sub"] == "user-123"
    assert payload["adm"] is True
    assert payload["exp"] > time.time()


def test_make_token_encodes_non_admin_flag():
    token = auth.make_token("user-456", is_admin=False)
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    assert payload["adm"] is False


def test_token_expiry_follows_settings():
    token = auth.make_token("user-789", is_admin=False)
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    expected = time.time() + settings.jwt_expire_hours * 3600
    assert abs(payload["exp"] - expected) < 60


def test_tokens_signed_with_another_secret_are_rejected():
    forged = jwt.encode({"sub": "user-123", "adm": True}, "a-different-secret-that-is-long-enough-for-hs256", algorithm="HS256")
    with pytest.raises(jwt.PyJWTError):
        jwt.decode(forged, settings.jwt_secret, algorithms=["HS256"])


# --------------------------------------------------------------------- email

@pytest.mark.parametrize(
    "email",
    ["a@b.co", "user.name+tag@example.com", "USER@EXAMPLE.COM", "x@y.zz"],
)
def test_email_regex_accepts_valid_addresses(email):
    assert auth.EMAIL_RE.match(email)


@pytest.mark.parametrize(
    "email",
    ["", "no-at-sign", "@example.com", "user@", "user@host", "a b@example.com", "user@exa mple.com"],
)
def test_email_regex_rejects_invalid_addresses(email):
    assert not auth.EMAIL_RE.match(email)
