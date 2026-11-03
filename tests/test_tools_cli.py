"""The operational CLI in `app/tools.py`."""
from __future__ import annotations

import sys

import pytest

from app import auth, database as db, tools


def run(monkeypatch, *argv) -> int:
    monkeypatch.setattr(sys, "argv", ["python -m app.tools", *argv])
    return tools.main()


def test_create_user(monkeypatch, capsys):
    assert run(monkeypatch, "create-user", "ops@example.com", "secret123") == 0

    stored = db.get_user_by_email("ops@example.com")
    assert stored is not None
    assert stored["is_admin"] == 0
    assert auth.verify_password("secret123", stored["password_hash"])
    assert "Created user ops@example.com" in capsys.readouterr().out


def test_create_admin(monkeypatch):
    assert run(monkeypatch, "create-user", "root@example.com", "secret123", "--admin") == 0
    assert db.get_user_by_email("root@example.com")["is_admin"] == 1


def test_create_user_normalises_the_email(monkeypatch):
    assert run(monkeypatch, "create-user", "  Ops@Example.COM ", "secret123") == 0
    assert db.get_user_by_email("ops@example.com") is not None


def test_create_existing_user_fails(monkeypatch, capsys):
    db.create_user("dup@example.com", "hash", False)

    assert run(monkeypatch, "create-user", "dup@example.com", "secret123") == 1
    assert "User already exists" in capsys.readouterr().err


def test_list_users(monkeypatch, capsys):
    db.create_user("admin@example.com", "hash", True)
    db.create_user("member@example.com", "hash", False)

    assert run(monkeypatch, "list-users") == 0

    out = capsys.readouterr().out
    assert "admin@example.com" in out
    assert "member@example.com" in out
    assert out.count("admin") >= 1
    assert "id=" in out


def test_reset_password(monkeypatch, capsys):
    db.create_user("lost@example.com", auth.hash_password("old-password"), False)

    assert run(monkeypatch, "reset-password", "lost@example.com", "new-password") == 0

    stored = db.get_user_by_email("lost@example.com")
    assert auth.verify_password("new-password", stored["password_hash"])
    assert not auth.verify_password("old-password", stored["password_hash"])
    assert "Password reset for lost@example.com" in capsys.readouterr().out


def test_reset_password_for_unknown_user(monkeypatch, capsys):
    assert run(monkeypatch, "reset-password", "ghost@example.com", "new-password") == 1
    assert "User not found" in capsys.readouterr().err


def test_reset_password_is_case_insensitive_on_email(monkeypatch):
    db.create_user("lost@example.com", "hash", False)
    assert run(monkeypatch, "reset-password", "LOST@Example.com", "new-password") == 0


def test_no_command_exits_with_usage_error(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["python -m app.tools"])
    with pytest.raises(SystemExit) as excinfo:
        tools.main()
    assert excinfo.value.code == 2


def test_unknown_command_exits_with_usage_error(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["python -m app.tools", "frobnicate"])
    with pytest.raises(SystemExit) as excinfo:
        tools.main()
    assert excinfo.value.code == 2


def test_cli_users_can_log_in_through_the_api(monkeypatch, client):
    assert run(monkeypatch, "create-user", "cli@example.com", "secret123") == 0

    resp = client.post("/api/auth/login", json={"email": "cli@example.com", "password": "secret123"})
    assert resp.status_code == 200
    assert resp.json()["user"]["is_admin"] is False
