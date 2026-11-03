"""Retrieval, rank fusion, prompt assembly and answer generation."""
from __future__ import annotations

import numpy as np
import pytest

from app import database as db, providers, rag
from helpers import (
    FAQ_ANSWER_SNIPPET,
    FAQ_QUESTION,
    create_test_user,
    seed_document,
    seed_faq_document,
)


# ------------------------------------------------------------- rank fusion

def test_rrf_single_ranking_only():
    fused = rag._rrf_fuse(np.array([3.0, 0.0, 0.0]), np.zeros(3))
    assert fused[0] == pytest.approx(1 / (rag.RRF_K + 1))
    assert fused[1] == 0.0
    assert fused[2] == 0.0


def test_rrf_merges_two_rankings_symmetrically():
    fused = rag._rrf_fuse(np.array([1.0, 0.0]), np.array([0.0, 1.0]))
    assert fused[0] == pytest.approx(fused[1])
    assert fused.sum() == pytest.approx(2 / (rag.RRF_K + 1))


def test_rrf_agreement_beats_disagreement():
    # chunk 0 is first in both rankings, chunk 1 only in one of them
    fused = rag._rrf_fuse(np.array([0.9, 0.8, 0.0]), np.array([5.0, 0.0, 0.0]))
    assert fused[0] > fused[1]
    assert fused[2] == 0.0


def test_rrf_ignores_zero_scores():
    fused = rag._rrf_fuse(np.zeros(2), np.zeros(2))
    assert fused.tolist() == [0.0, 0.0]


# ----------------------------------------------------------------- snippet

def test_snippet_strips_markdown_noise_and_collapses_whitespace():
    assert rag._snippet("# Title\n\n> quote   here") == "Title quote here"


def test_snippet_truncates_long_text():
    assert len(rag._snippet("x" * 1000)) == 400


def test_snippet_handles_empty_text():
    assert rag._snippet("") == ""


# --------------------------------------------------------- prompt assembly

def _context(rank: int = 1, name: str = "faq.txt", content: str = "Body") -> dict:
    return {"rank": rank, "doc_id": "d1", "doc_name": name, "chunk_idx": 0, "score": 0.5, "content": content}


def test_build_messages_embeds_numbered_context():
    messages = rag.build_messages("Why?", [_context(1), _context(2, "guide.pdf", "Other")], [], True)
    system = messages[0]["content"]

    assert messages[0]["role"] == "system"
    assert rag.SYSTEM_PROMPT in system
    assert "[1] (from faq.txt)" in system
    assert "[2] (from guide.pdf)" in system
    assert messages[-1] == {"role": "user", "content": "Why?"}


def test_build_messages_says_no_documents_were_uploaded():
    system = rag.build_messages("Why?", [], [], has_documents=False)[0]["content"]
    assert "NOT uploaded any documents" in system
    assert "Tell them to upload documents first" in system


def test_build_messages_distinguishes_no_hits_from_empty_base():
    system = rag.build_messages("Why?", [], [], has_documents=True)[0]["content"]
    assert "no relevant passages were retrieved" in system
    assert "do NOT claim it is empty" in system


def test_build_messages_keeps_only_the_last_ten_history_turns():
    history = [{"role": "user", "content": f"q{i}"} for i in range(15)]
    messages = rag.build_messages("now?", [], history, True)

    assert len(messages) == 1 + 10 + 1
    assert messages[1]["content"] == "q5"
    assert messages[-2]["content"] == "q14"
    assert messages[-1]["content"] == "now?"


def test_sources_payload_contains_rank_name_score_and_snippet():
    payload = rag._sources_payload([_context(1)])
    assert set(payload[0]) == {"rank", "doc_id", "doc_name", "score", "snippet"}
    assert payload[0]["snippet"] == "Body"


# ------------------------------------------------------------------ retrieve

def test_retrieve_returns_nothing_without_documents():
    uid = create_test_user("empty@example.com")
    assert rag.retrieve(uid, "anything at all") == []


def test_retrieve_finds_the_relevant_document():
    uid = create_test_user("rag@example.com")
    doc_id = seed_faq_document(uid)

    hits = rag.retrieve(uid, FAQ_QUESTION)

    assert hits
    top = hits[0]
    assert top["rank"] == 1
    assert top["doc_id"] == doc_id
    assert top["doc_name"] == "faq.txt"
    assert top["score"] > 0
    assert FAQ_ANSWER_SNIPPET in top["content"].lower()


def test_retrieve_ranks_are_sequential():
    uid = create_test_user()
    seed_document(uid, "faq.txt", [f"Refund policy paragraph number {i}." for i in range(4)])

    hits = rag.retrieve(uid, "refund policy", top_k=4)
    assert [h["rank"] for h in hits] == list(range(1, len(hits) + 1))


def test_retrieve_respects_top_k():
    uid = create_test_user()
    for i in range(6):
        seed_document(uid, f"doc{i}.txt", [f"Refund policy section {i} with details."])

    assert len(rag.retrieve(uid, "refund policy", top_k=2)) <= 2
    assert len(rag.retrieve(uid, "refund policy", top_k=4)) <= 4


def test_retrieve_uses_the_configured_top_k_by_default(patch_settings):
    uid = create_test_user()
    for i in range(6):
        seed_document(uid, f"doc{i}.txt", [f"Refund policy section {i} with details."])

    with patch_settings(top_k=2):
        assert len(rag.retrieve(uid, "refund policy")) <= 2


def test_retrieve_caps_chunks_per_document():
    uid = create_test_user()
    doc_id = seed_document(
        uid, "big.txt", [f"Refund policy clause {i}: refunds are paid within 5 days." for i in range(6)]
    )

    hits = rag.retrieve(uid, "refund policy", top_k=6)

    assert sum(1 for h in hits if h["doc_id"] == doc_id) <= 3


def test_retrieve_spans_multiple_documents():
    uid = create_test_user()
    first = seed_document(uid, "a.txt", ["Refund policy allows 30 day returns."])
    second = seed_document(uid, "b.txt", ["Refund policy exceptions apply to gift cards."])

    hits = rag.retrieve(uid, "refund policy", top_k=6)
    assert {h["doc_id"] for h in hits} == {first, second}


def test_retrieve_ignores_unrelated_questions():
    uid = create_test_user()
    seed_faq_document(uid)
    assert rag.retrieve(uid, "xyzzy plugh qwerty") == []


def test_retrieve_never_leaks_other_users_documents():
    owner = create_test_user("owner@example.com")
    stranger = create_test_user("stranger@example.com")
    seed_faq_document(owner)

    assert rag.retrieve(stranger, FAQ_QUESTION) == []


def test_retrieve_works_before_any_faq_is_written():
    uid = create_test_user()
    seed_document(uid, "chinese.txt", ["退款政策允许在购买后三十天内退货。"])
    hits = rag.retrieve(uid, "退款政策")
    assert hits and hits[0]["doc_name"] == "chinese.txt"


# -------------------------------------------------------- answer generation

def test_answer_once_returns_text_and_sources():
    uid = create_test_user()
    seed_faq_document(uid)

    text, sources = rag.answer_once(uid, FAQ_QUESTION, [])

    assert FAQ_ANSWER_SNIPPET in text.lower()
    assert sources and sources[0]["rank"] == 1


def test_answer_once_without_documents_returns_no_sources():
    uid = create_test_user()
    text, sources = rag.answer_once(uid, "what is the refund policy?", [])
    assert sources == []
    assert "could not find anything relevant" in text


def test_answer_once_reports_provider_failures(monkeypatch):
    uid = create_test_user()

    def boom(messages, contexts=None):
        raise RuntimeError("upstream 500")

    monkeypatch.setattr(providers, "chat_once", boom)
    text, _ = rag.answer_once(uid, "question", [])
    assert "Model request failed" in text


def test_answer_stream_emits_deltas_then_done():
    uid = create_test_user()
    seed_faq_document(uid)

    events = list(rag.answer_stream(uid, FAQ_QUESTION, []))

    assert events[0][0] == "delta"
    assert events[-1][0] == "done"

    text = "".join(payload for kind, payload in events if kind == "delta")
    assert FAQ_ANSWER_SNIPPET in text.lower()

    sources = events[-1][1]
    assert sources and set(sources[0]) == {"rank", "doc_id", "doc_name", "score", "snippet"}


def test_answer_stream_reports_provider_failures(monkeypatch):
    uid = create_test_user()

    def boom(messages, contexts=None):
        raise RuntimeError("no route to host")

    monkeypatch.setattr(providers, "chat_stream", boom)
    events = list(rag.answer_stream(uid, "question", []))
    text = "".join(payload for kind, payload in events if kind == "delta")
    assert "Model request failed" in text
    assert events[-1][0] == "done"


def test_answer_stream_still_reports_sources_when_the_model_fails(monkeypatch):
    uid = create_test_user()
    seed_faq_document(uid)

    def boom(messages, contexts=None):
        raise RuntimeError("model down")

    monkeypatch.setattr(providers, "chat_stream", boom)
    events = list(rag.answer_stream(uid, FAQ_QUESTION, []))
    assert events[-1][1]  # retrieval succeeded even though generation did not


# ------------------------------------------------- embedding dimension mixing

def test_retrieve_pads_when_stored_vectors_are_smaller_than_the_query():
    """Demo vectors are 512-d while a real embedding endpoint may return more."""
    uid = create_test_user()
    doc_id = db.create_document(uid, "tiny.txt")
    db.insert_chunk(doc_id, uid, 0, "Refund policy applies to all orders.", np.ones(4, dtype=np.float32))
    db.finish_document(doc_id, 1)

    hits = rag.retrieve(uid, "refund policy")  # 512-d query, 4-d stored vector

    assert hits and hits[0]["doc_id"] == doc_id


def test_retrieve_pads_when_the_query_vector_is_smaller(monkeypatch):
    uid = create_test_user()
    seed_faq_document(uid)

    monkeypatch.setattr(providers, "embed_texts", lambda texts: np.ones((len(texts), 8), dtype=np.float32))

    hits = rag.retrieve(uid, "refund policy")  # 8-d query, 512-d stored vectors

    assert hits
