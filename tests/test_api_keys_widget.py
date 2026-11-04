"""API key management and the embeddable widget / server-to-server chat route."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from helpers import FAQ_TEXT, parse_sse, sse_text, upload


STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


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

    # the origin is echoed back, not wildcarded: a key's allow-list decides it
    assert resp.headers["access-control-allow-origin"] == "https://customer.example"
    assert resp.headers["vary"] == "Origin"
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
    assert resp.headers["access-control-allow-origin"] == "https://customer.example"
    assert "POST" in resp.headers["access-control-allow-methods"]
    assert "X-API-Key" in resp.headers["access-control-allow-headers"]


def test_other_routes_do_not_get_cors_headers(client, user):
    resp = client.get("/api/documents", headers={**user["headers"], "Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in resp.headers


# ------------------------------------------------------- streaming (SSE) mode

def widget_stream(client, key, message=WIDGET_QUESTION, conversation_id=None):
    payload = {"message": message, "stream": True}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    return client.post("/api/widget/chat", headers={"X-API-Key": key}, json=payload)


def test_widget_chat_streams_sse_when_asked(client, user):
    upload(client, user["headers"], ("faq.txt", FAQ_TEXT.encode()))
    key = make_key(client, user["headers"])["key"]

    resp = widget_stream(client, key)

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers.get("x-accel-buffering") == "no"
    events = parse_sse(resp.text)
    assert len(events) >= 2
    assert all("delta" in event or "done" in event for event in events)
    assert "refund policy allows returns within 30 days" in sse_text(events).lower()


def test_widget_stream_done_event_carries_id_and_sources(client, user):
    upload(client, user["headers"], ("faq.txt", FAQ_TEXT.encode()))
    key = make_key(client, user["headers"])["key"]

    events = parse_sse(widget_stream(client, key).text)
    done = events[-1]

    assert done["done"] is True
    assert done["conversation_id"]
    assert done["sources"][0]["doc_name"] == "faq.txt"


def test_widget_stream_persists_both_turns(client, user):
    upload(client, user["headers"], ("faq.txt", FAQ_TEXT.encode()))
    key = make_key(client, user["headers"])["key"]

    done = parse_sse(widget_stream(client, key).text)[-1]
    cid = done["conversation_id"]

    messages = client.get(
        f"/api/conversations/{cid}/messages", headers=user["headers"]
    ).json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["content"].strip()
    assert messages[1]["sources"]


def test_widget_stream_continues_an_existing_conversation(client, user):
    key = make_key(client, user["headers"])["key"]
    first = parse_sse(widget_stream(client, key, "What is the refund policy?").text)[-1]

    second = parse_sse(
        widget_stream(client, key, "And the shipping time?", first["conversation_id"]).text
    )[-1]

    assert second["conversation_id"] == first["conversation_id"]
    messages = client.get(
        f"/api/conversations/{first['conversation_id']}/messages", headers=user["headers"]
    ).json()["messages"]
    assert len(messages) == 4


def test_widget_stream_requires_a_valid_key(client):
    resp = client.post(
        "/api/widget/chat", headers={"X-API-Key": "rbk_nope"}, json={"message": "hi", "stream": True}
    )
    assert resp.status_code == 401


def test_widget_json_mode_stays_the_default(client, user):
    key = make_key(client, user["headers"])["key"]

    resp = client.post("/api/widget/chat", headers={"X-API-Key": key}, json={"message": "hello"})

    assert resp.headers["content-type"].startswith("application/json")
    assert set(resp.json()) == {"conversation_id", "answer", "sources"}


def test_embed_pages_are_served(client):
    """/widget is the iframe target, /demo is the simulated host page around it."""
    for path in ("/widget", "/demo"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")

    demo = client.get("/demo").text
    assert 'id="launcher"' in demo  # the floating button the visitor clicks
    assert 'id="frame"' in demo  # lazily-filled iframe pointing at /widget
    assert "/widget?key=" in demo


def test_integration_sections_are_a_left_rail_of_tabs(client):
    """The three integration sections are tabs, not stacked headings.

    A tab with no pane (or a pane no tab points at) would show an empty dialog,
    which no server-side route test would catch — so check the pairing here.
    """
    html = client.get("/").text

    names = re.findall(r'data-int-tab="([a-z]+)"', html)
    assert names == ["embed", "api", "keys"]
    for name in names:
        assert f'aria-controls="int-pane-{name}"' in html
        assert f'id="int-pane-{name}"' in html
    assert re.findall(r'id="int-pane-([a-z]+)"', html) == names

    # exactly one pane starts visible, otherwise all three render at once again
    # (scoped to this modal: the workspace dialog reuses the same classes)
    int_modal = html.split('id="int-modal"')[1].split('id="ws-modal"')[0]
    assert int_modal.count('class="int-pane active"') == 1
    assert 'role="tablist"' in html
    # the old headings are gone — the labels live in the rail now
    assert "int-section" not in html



def test_code_blocks_are_syntax_highlighted(client):
    """Both code blocks in the dialog go through the highlighter.

    The highlighter emits ``<span class="tok-X">``; if a token rule and a style
    rule ever drift apart, that token silently falls back to plain text — no
    route test would notice. Pair them up here instead.
    """
    page = client.get("/").text

    # highlight.js must be defined before app.js renders the first snippet
    assert '<script src="/static/highlight.js"></script>' in page
    assert page.index("highlight.js") < page.index("app.js")

    # the embed snippet became a code block too — a <textarea> cannot be coloured
    assert '<pre class="code-block code-block--embed"><code id="embed-code">' in page
    assert '<textarea id="embed-code"' not in page

    script = client.get("/static/highlight.js")
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert "RAGhl" in script.text

    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    assert "--tok-str" in css and "--code-bg" in css

    # every class a rule can emit needs a paint rule, or the token renders grey
    emitted = set(re.findall(r'^\s+\["([a-z]+)",\s*"', script.text, re.MULTILINE))
    assert len(emitted) >= 8, emitted
    missing = sorted(c for c in emitted if f".tok-{c}" not in css)
    assert not missing, f"no .tok-{missing} rule in styles.css"

    # and the app has to actually call it for both panes
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'RAGhl.highlight(code, "html")' in app_js
    assert "RAGhl.highlight(code, API_SNIPPET_LANG" in app_js
