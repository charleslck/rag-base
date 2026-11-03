"""The SSE chat endpoint: streaming, persistence, titles and error handling."""
from __future__ import annotations

import pytest

from helpers import (
    FAQ_ANSWER_SNIPPET,
    FAQ_QUESTION,
    FAQ_TEXT,
    parse_sse,
    sse_text,
    upload,
)


def ask(client, headers, message, conversation_id=None):
    """POST /api/chat and return (response, parsed events, streamed text)."""
    payload = {"message": message}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    resp = client.post("/api/chat", headers=headers, json=payload)
    if resp.status_code != 200:
        return resp, [], ""
    events = parse_sse(resp.text)
    return resp, events, sse_text(events)


def messages_of(client, headers, conversation_id):
    return client.get(f"/api/conversations/{conversation_id}/messages", headers=headers).json()["messages"]


# ----------------------------------------------------------------- streaming

def test_chat_streams_server_sent_events(client, user):
    upload(client, user["headers"], ("faq.txt", FAQ_TEXT.encode()))
    resp, events, text = ask(client, user["headers"], FAQ_QUESTION)

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert len(events) >= 2
    assert all("delta" in event or "done" in event for event in events)
    assert text.strip()


def test_chat_answers_from_the_knowledge_base(client, user):
    upload(client, user["headers"], ("faq.txt", FAQ_TEXT.encode()))

    _, events, text = ask(client, user["headers"], FAQ_QUESTION)

    assert FAQ_ANSWER_SNIPPET in text.lower()
    done = events[-1]
    assert done["done"] is True
    assert done["sources"][0]["doc_name"] == "faq.txt"
    assert FAQ_ANSWER_SNIPPET in done["sources"][0]["snippet"].lower()


def test_done_event_carries_the_conversation_id(client, user):
    _, events, _ = ask(client, user["headers"], "hello there")

    conversation_id = events[-1]["conversation_id"]
    assert conversation_id
    assert client.get(f"/api/conversations/{conversation_id}/messages", headers=user["headers"]).status_code == 200


def test_sources_are_numbered_and_scored(client, user):
    upload(client, user["headers"], ("faq.txt", FAQ_TEXT.encode()))
    _, events, _ = ask(client, user["headers"], FAQ_QUESTION)

    sources = events[-1]["sources"]
    assert sources
    assert [s["rank"] for s in sources] == list(range(1, len(sources) + 1))
    assert all(isinstance(s["score"], (int, float)) for s in sources)
    assert all(s["doc_id"] and s["doc_name"] for s in sources)


def test_chat_without_documents_returns_no_sources(client, user):
    _, events, text = ask(client, user["headers"], "What is the refund policy?")

    assert events[-1]["sources"] == []
    assert "could not find anything relevant" in text.lower()


# --------------------------------------------------------------- persistence

def test_user_and_assistant_messages_are_persisted(client, user):
    _, events, text = ask(client, user["headers"], "Hello, how are you?")
    conversation_id = events[-1]["conversation_id"]

    stored = messages_of(client, user["headers"], conversation_id)

    assert [m["role"] for m in stored] == ["user", "assistant"]
    assert stored[0]["content"] == "Hello, how are you?"
    assert stored[1]["content"] == text.strip()


def test_assistant_message_keeps_its_sources(client, user):
    upload(client, user["headers"], ("faq.txt", FAQ_TEXT.encode()))
    _, events, _ = ask(client, user["headers"], FAQ_QUESTION)
    conversation_id = events[-1]["conversation_id"]

    stored = messages_of(client, user["headers"], conversation_id)
    assistant = stored[-1]

    assert assistant["role"] == "assistant"
    assert assistant["sources"]
    assert assistant["sources"][0]["doc_name"] == "faq.txt"
    assert "snippet" in assistant["sources"][0]


def test_new_conversation_is_titled_after_the_first_question(client, user):
    question = "How long does the refund policy last for customers in Europe?"
    _, events, _ = ask(client, user["headers"], question)
    conversation_id = events[-1]["conversation_id"]

    conversations = client.get("/api/conversations", headers=user["headers"]).json()["conversations"]
    conv = next(c for c in conversations if c["id"] == conversation_id)
    assert conv["title"] == question[:60]


def test_follow_up_messages_reuse_the_conversation(client, user):
    _, first, _ = ask(client, user["headers"], "First question about refunds")
    conversation_id = first[-1]["conversation_id"]

    _, second, _ = ask(client, user["headers"], "And a follow-up?", conversation_id=conversation_id)

    assert second[-1]["conversation_id"] == conversation_id
    assert len(messages_of(client, user["headers"], conversation_id)) == 4
    assert len(client.get("/api/conversations", headers=user["headers"]).json()["conversations"]) == 1


# ------------------------------------------------------------- error handling

def test_unknown_conversation_returns_404(client, user):
    resp, _, _ = ask(client, user["headers"], "hello", conversation_id="does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Conversation not found"


def test_cannot_write_into_another_users_conversation(client, admin, user):
    _, events, _ = ask(client, admin["headers"], "admin question")
    conversation_id = events[-1]["conversation_id"]

    resp, _, _ = ask(client, user["headers"], "hijack", conversation_id=conversation_id)
    assert resp.status_code == 404


def test_empty_message_is_rejected(client, user):
    resp = client.post("/api/chat", headers=user["headers"], json={"message": ""})
    assert resp.status_code == 422


def test_whitespace_only_message_is_rejected(client, user):
    resp = client.post("/api/chat", headers=user["headers"], json={"message": "    "})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Empty message"


def test_overlong_message_is_rejected(client, user):
    resp = client.post("/api/chat", headers=user["headers"], json={"message": "x" * 8001})
    assert resp.status_code == 422


def test_message_is_trimmed_before_storage(client, user):
    _, events, _ = ask(client, user["headers"], "   spaced question   ")
    stored = messages_of(client, user["headers"], events[-1]["conversation_id"])
    assert stored[0]["content"] == "spaced question"


def test_chat_requires_authentication(client):
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 401


def test_model_failures_are_reported_inside_the_stream(client, user, monkeypatch):
    from app import providers

    def boom(messages, contexts=None):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(providers, "chat_stream", boom)

    _, events, text = ask(client, user["headers"], "anything")
    assert "Model request failed" in text
    assert events[-1]["done"] is True


def test_failed_generation_still_records_the_exchange(client, user, monkeypatch):
    from app import providers

    def boom(messages, contexts=None):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(providers, "chat_stream", boom)
    _, events, _ = ask(client, user["headers"], "anything")

    stored = messages_of(client, user["headers"], events[-1]["conversation_id"])
    assert [m["role"] for m in stored] == ["user", "assistant"]
    assert "Model request failed" in stored[-1]["content"]


@pytest.mark.parametrize("question", ["退款政策是什么？", "How do refunds work?"])
def test_questions_in_any_language_are_answered(client, user, question):
    upload(client, user["headers"], ("faq.txt", FAQ_TEXT.encode()))
    _, events, text = ask(client, user["headers"], question)
    assert text.strip()
    assert events[-1]["done"] is True


def test_chat_renames_a_placeholder_conversation(client, user):
    """POST /api/conversations creates "New conversation"; the first message names it."""
    cid = client.post("/api/conversations", headers=user["headers"], json={}).json()["id"]

    _, events, _ = ask(client, user["headers"], "How do refunds work?", conversation_id=cid)
    assert events[-1]["conversation_id"] == cid

    conversations = client.get("/api/conversations", headers=user["headers"]).json()["conversations"]
    assert conversations[0]["title"] == "How do refunds work?"


def test_chat_leaves_custom_titles_alone(client, user):
    cid = client.post("/api/conversations", headers=user["headers"], json={"title": "Support"}).json()["id"]

    ask(client, user["headers"], "How do refunds work?", conversation_id=cid)

    conversations = client.get("/api/conversations", headers=user["headers"]).json()["conversations"]
    assert conversations[0]["title"] == "Support"
