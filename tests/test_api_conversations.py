"""Conversation CRUD endpoints."""
from __future__ import annotations

import pytest


def new_conversation(client, headers, title=None):
    payload = {} if title is None else {"title": title}
    resp = client.post("/api/conversations", headers=headers, json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_listing_starts_empty(client, user):
    assert client.get("/api/conversations", headers=user["headers"]).json() == {"conversations": []}


def test_create_with_default_title(client, user):
    cid = new_conversation(client, user["headers"])

    conversations = client.get("/api/conversations", headers=user["headers"]).json()["conversations"]
    assert conversations[0]["id"] == cid
    assert conversations[0]["title"] == "New conversation"


def test_create_with_custom_title(client, user):
    new_conversation(client, user["headers"], "Refund questions")
    conversations = client.get("/api/conversations", headers=user["headers"]).json()["conversations"]
    assert conversations[0]["title"] == "Refund questions"


def test_long_titles_are_truncated_to_120_chars(client, user):
    new_conversation(client, user["headers"], "t" * 500)
    conversations = client.get("/api/conversations", headers=user["headers"]).json()["conversations"]
    assert len(conversations[0]["title"]) == 120


def test_conversations_are_listed_by_recency(client, user):
    first = new_conversation(client, user["headers"], "first")
    second = new_conversation(client, user["headers"], "second")

    listed = client.get("/api/conversations", headers=user["headers"]).json()["conversations"]
    assert [c["id"] for c in listed] == [second, first]


def test_messages_of_a_new_conversation_are_empty(client, user):
    cid = new_conversation(client, user["headers"])
    resp = client.get(f"/api/conversations/{cid}/messages", headers=user["headers"])
    assert resp.status_code == 200
    assert resp.json() == {"messages": []}


def test_messages_of_unknown_conversation_return_404(client, user):
    resp = client.get("/api/conversations/unknown/messages", headers=user["headers"])
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Conversation not found"


def test_delete_conversation(client, user):
    cid = new_conversation(client, user["headers"])

    assert client.delete(f"/api/conversations/{cid}", headers=user["headers"]).json() == {"ok": True}
    assert client.get("/api/conversations", headers=user["headers"]).json()["conversations"] == []
    assert client.delete(f"/api/conversations/{cid}", headers=user["headers"]).status_code == 404


def test_conversations_are_private(client, admin, user):
    cid = new_conversation(client, admin["headers"], "admin only")

    assert client.get("/api/conversations", headers=user["headers"]).json()["conversations"] == []
    assert client.get(f"/api/conversations/{cid}/messages", headers=user["headers"]).status_code == 404
    assert client.delete(f"/api/conversations/{cid}", headers=user["headers"]).status_code == 404


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/api/conversations"),
        ("post", "/api/conversations"),
        ("get", "/api/conversations/abc/messages"),
        ("delete", "/api/conversations/abc"),
    ],
)
def test_all_conversation_routes_require_authentication(client, method, path):
    kwargs = {"json": {"title": "x"}} if method == "post" else {}
    assert getattr(client, method)(path, **kwargs).status_code == 401
