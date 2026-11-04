"""On-premise ops: audit trail, data export and account deletion.

Everything here is account- or instance-scoped; there is no organisation to
transfer, invite or delete.
"""
from __future__ import annotations

import json

from app import database as db
from helpers import bearer, upload


# ------------------------------------------------------------------ audit

def test_key_lifecycle_and_deletes_land_in_the_audit_log(client, user):
    h = user["headers"]
    key = client.post("/api/api-keys", headers=h, json={"label": "site"}).json()
    client.patch(f"/api/api-keys/{key['id']}", headers=h, json={"label": "renamed"})
    client.delete(f"/api/api-keys/{key['id']}", headers=h)

    upload(client, h, ("doc.txt", b"content"))
    doc_id = client.get("/api/documents", headers=h).json()["documents"][0]["id"]
    client.delete(f"/api/documents/{doc_id}", headers=h)

    events = client.get("/api/audit", headers=h).json()["events"]
    actions = [e["action"] for e in events]
    for expected in ("key.create", "key.update", "key.revoke", "document.delete",
                     "user.register"):
        assert expected in actions, expected
    assert all(e["actor_email"] for e in events)


def test_audit_requires_a_signed_in_operator(client, user):
    assert client.get("/api/audit").status_code == 401
    assert client.get("/api/audit", headers=user["headers"]).status_code == 200


# ------------------------------------------------------------------ export

def test_export_contains_everything_but_no_credentials(client, user):
    h = user["headers"]
    upload(client, h, ("faq.txt", b"Refunds take five business days."))
    client.post("/api/conversations", headers=h, json={"title": "export me"})
    key = client.post("/api/api-keys", headers=h, json={"label": "site"}).json()

    resp = client.get("/api/export", headers=h)
    assert resp.status_code == 200
    assert "attachment; filename=" in resp.headers["Content-Disposition"]

    payload = json.loads(resp.text)
    assert payload["account"]["email"] == user["email"]
    assert len(payload["documents"]) == 1
    assert "Refunds take five business days" in payload["documents"][0]["text"]
    assert payload["conversations"]
    # key material: prefixes only, never the hashes
    assert [k["prefix"] for k in payload["api_keys"]] == [key["prefix"]]
    assert "key_hash" not in resp.text
    assert "password_hash" not in resp.text
    # a single-account export has no tenant objects
    assert "org" not in payload and "members" not in payload


def test_export_requires_authentication(client, user):
    assert client.get("/api/export").status_code == 401


# ------------------------------------------------------------------ deletion

def test_account_deletion_requires_the_password(client, user):
    assert client.request("DELETE", "/api/account", headers=user["headers"],
                          json={"password": "wrong"}).status_code == 401
    assert db.get_user_by_email(user["email"]) is not None


def test_deleting_an_account_takes_everything_it_owns(client, user):
    h = user["headers"]
    upload(client, h, ("gone.txt", b"all my knowledge"))
    client.post("/api/api-keys", headers=h, json={"label": "k"})

    resp = client.request("DELETE", "/api/account", headers=h,
                          json={"password": user["password"]})
    assert resp.status_code == 200
    assert db.get_user_by_email(user["email"]) is None
    assert db.stats()["documents"] == 0
    assert db.user_count() == 1  # only the instance admin remains


def test_deleting_an_account_is_recorded(client, user):
    assert client.request("DELETE", "/api/account", headers=user["headers"],
                          json={"password": user["password"]}).status_code == 200
    # the trail survives the account that produced it
    assert db.list_audit_events()
    assert any(e["action"] == "account.delete" for e in db.list_audit_events())
