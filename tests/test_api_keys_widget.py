"""API key management and the embeddable widget / server-to-server chat route."""
from __future__ import annotations

import pytest

from helpers import FAQ_TEXT, upload


WIDGET_QUESTION = "How long is the refund policy?"


def make_key(client, headers, label="website"):
    resp = client.post("/api/api-keys", headers=headers, json={"label": label})
    assert resp.status_code == 200, resp.text
    return resp.json()


def widget_chat(client, key, message=WIDGET_QUESTION, conversation_id=None):
    payload = {"message": message}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    return client.post("/api/widget/chat", headers={"X-API-Key": key}, json=payload)


# ------------------------------------------------------------- key management

def test_create_key_returns_the_secret_only_once(client, user):
    created = make_key(client, user["headers"])

    assert created["key"].startswith("rbk_")
    assert created["prefix"] == created["key"][:10]
    assert created["id"]

    listed = client.get("/api/api-keys", headers=user["headers"]).json()["keys"]
    assert len(listed) == 1
    assert listed[0]["prefix"] == created["prefix"]
    assert listed[0]["label"] == "website"
    assert "key" not in listed[0]
    assert "key_hash" not in listed[0]


def test_keys_are_unique_per_call(client, user):
    first = make_key(client, user["headers"])
    second = make_key(client, user["headers"])
    assert first["key"] != second["key"]
    assert len(client.get("/api/api-keys", headers=user["headers"]).json()["keys"]) == 2


def test_keys_are_private_to_their_owner(client, admin, user):
    make_key(client, admin["headers"])
    assert client.get("/api/api-keys", headers=user["headers"]).json()["keys"] == []


def test_revoke_key(client, user):
    created = make_key(client, user["headers"])

    assert client.delete(f"/api/api-keys/{created['id']}", headers=user["headers"]).json() == {"ok": True}
    assert client.get("/api/api-keys", headers=user["headers"]).json()["keys"] == []
    assert client.delete(f"/api/api-keys/{created['id']}", headers=user["headers"]).status_code == 404


def test_cannot_revoke_someone_elses_key(client, admin, user):
    created = make_key(client, admin["headers"])

    assert client.delete(f"/api/api-keys/{created['id']}", headers=user["headers"]).status_code == 404
    assert len(client.get("/api/api-keys", headers=admin["headers"]).json()["keys"]) == 1


@pytest.mark.parametrize(
    "method, path",
    [("get", "/api/api-keys"), ("post", "/api/api-keys"), ("delete", "/api/api-keys/xyz")],
)
def test_key_routes_require_authentication(client, method, path):
    kwargs = {"json": {"label": "x"}} if method == "post" else {}
    assert getattr(client, method)(path, **kwargs).status_code == 401


# ---------------------------------------------------------------- widget chat

def test_widget_chat_answers_with_key_authentication(client, user):
    upload(client, user["headers"], ("faq.txt", FAQ_TEXT.encode()))
    key = make_key(client, user["headers"])["key"]

    resp = widget_chat(client, key)

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"]
    assert body["conversation_id"]
    assert body["sources"][0]["doc_name"] == "faq.txt"


def test_widget_does_not_need_a_bearer_token(client, user):
    key = make_key(client, user["headers"])["key"]
    # No Authorization header at all.
    assert widget_chat(client, key, "hello").status_code == 200


def test_widget_conversation_continues(client, user):
    key = make_key(client, user["headers"])["key"]

    first = widget_chat(client, key, "First question").json()
    second = widget_chat(client, key, "Second question", conversation_id=first["conversation_id"]).json()

    assert second["conversation_id"] == first["conversation_id"]
    stored = client.get(
        f"/api/conversations/{first['conversation_id']}/messages", headers=user["headers"]
    ).json()["messages"]
    assert [m["role"] for m in stored] == ["user", "assistant", "user", "assistant"]


def test_widget_ignores_unknown_or_foreign_conversations(client, admin, user):
    user_key = make_key(client, user["headers"])["key"]
    admin_conversation = widget_chat(client, make_key(client, admin["headers"])["key"]).json()["conversation_id"]

    resp = widget_chat(client, user_key, "hello", conversation_id=admin_conversation)
    assert resp.status_code == 200

    body = resp.json()
    assert body["conversation_id"] != admin_conversation


def test_widget_starts_with_a_tagged_title(client, user):
    key = make_key(client, user["headers"])["key"]
    cid = widget_chat(client, key, "Refund timeline please").json()["conversation_id"]

    conversations = client.get("/api/conversations", headers=user["headers"]).json()["conversations"]
    conversation = next(c for c in conversations if c["id"] == cid)
    assert conversation["title"].startswith("[widget] ")


@pytest.mark.parametrize(
    "headers",
    [{}, {"X-API-Key": ""}, {"X-API-Key": "rbk_not-a-real-key"}, {"X-API-Key": "totally wrong"}],
)
def test_widget_rejects_missing_or_invalid_keys(client, headers):
    resp = client.post("/api/widget/chat", headers=headers, json={"message": "hello"})
    assert resp.status_code == 401


def test_revoked_key_stops_working(client, user):
    created = make_key(client, user["headers"])
    assert widget_chat(client, created["key"]).status_code == 200

    client.delete(f"/api/api-keys/{created['id']}", headers=user["headers"])

    assert widget_chat(client, created["key"]).status_code == 401


def test_widget_key_cannot_manage_documents(client, user):
    key = make_key(client, user["headers"])["key"]
    # API keys are chat-only; they are not bearer tokens.
    assert client.get("/api/documents", headers={"X-API-Key": key}).status_code == 401
    assert client.get("/api/admin/users", headers={"X-API-Key": key}).status_code == 401


@pytest.mark.parametrize("message", ["", "x" * 4001])
def test_widget_validates_the_message(client, user, message):
    key = make_key(client, user["headers"])["key"]
    assert widget_chat(client, key, message).status_code == 422


def test_widget_reports_when_nothing_is_retrieved(client, user):
    key = make_key(client, user["headers"])["key"]
    body = widget_chat(client, key, "unrelated question about quantum physics").json()
    assert body["sources"] == []
    assert body["answer"]


# --------------------------------------------------------------------- CORS

def test_widget_route_sets_cors_headers_for_browsers(client, user):
    key = make_key(client, user["headers"])["key"]
    resp = client.post(
        "/api/widget/chat",
        headers={"X-API-Key": key, "Origin": "https://customer.example"},
        json={"message": "hello"},
    )

    assert resp.headers["access-control-allow-origin"] == "*"
    assert "X-API-Key" in resp.headers["access-control-allow-headers"]


def test_widget_preflight_is_answered(client):
    resp = client.options(
        "/api/widget/chat",
        headers={
            "Origin": "https://customer.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-API-Key, Content-Type",
        },
    )

    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "*"
    assert "POST" in resp.headers["access-control-allow-methods"]
    assert "X-API-Key" in resp.headers["access-control-allow-headers"]


def test_other_routes_do_not_get_cors_headers(client, user):
    resp = client.get("/api/documents", headers={**user["headers"], "Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in resp.headers
