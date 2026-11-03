"""BM25 lexical retrieval, index caching and per-user isolation."""
from __future__ import annotations

import numpy as np
import pytest

from app import bm25, database as db
from helpers import create_test_user


DOCUMENTS = [
    ["refund", "policy", "refund", "policy", "thirty", "days"],
    ["shipping", "express", "delivery", "hours"],
    ["refund", "form", "download"],
]


# ------------------------------------------------------------------ scoring

def test_matching_document_scores_higher():
    index = bm25.BM25Index(DOCUMENTS)
    scores = index.scores(["refund"])
    assert scores[0] > scores[2] > 0
    assert scores[1] == 0


def test_unknown_terms_score_zero_everywhere():
    index = bm25.BM25Index(DOCUMENTS)
    assert index.scores(["nonexistentterm"]).tolist() == [0.0, 0.0, 0.0]


def test_empty_query_returns_zeros():
    index = bm25.BM25Index(DOCUMENTS)
    assert index.scores([]).tolist() == [0.0, 0.0, 0.0]


def test_empty_index_is_safe():
    index = bm25.BM25Index([])
    assert index.N == 0
    assert index.scores(["anything"]).tolist() == []


def test_term_frequency_increases_the_score():
    index = bm25.BM25Index([["refund"], ["refund", "refund", "refund"]])
    scores = index.scores(["refund"])
    assert scores[1] > scores[0]


def test_longer_documents_are_penalised():
    long_doc = ["refund"] + [f"filler{i}" for i in range(100)]
    index = bm25.BM25Index([["refund", "policy"], long_doc])
    scores = index.scores(["refund"])
    assert scores[0] > scores[1]


def test_scores_are_returned_as_a_float_array():
    scores = bm25.BM25Index(DOCUMENTS).scores(["refund"])
    assert isinstance(scores, np.ndarray)
    assert scores.dtype == np.float64
    assert scores.shape == (3,)


def test_query_terms_are_deduplicated():
    index = bm25.BM25Index(DOCUMENTS)
    once = index.scores(["refund"])
    twice = index.scores(["refund", "refund"])
    np.testing.assert_allclose(once, twice)


# ------------------------------------------------------- index + cache

@pytest.fixture()
def seeded_user():
    uid = create_test_user("bm25@example.com")
    doc_id = db.create_document(uid, "faq.txt")
    chunks = ["Refund policy allows returns within 30 days.", "Shipping takes five days."]
    for idx, content in enumerate(chunks):
        db.insert_chunk(doc_id, uid, idx, content, np.ones(4, dtype=np.float32))
    db.finish_document(doc_id, len(chunks))
    return uid, doc_id


def test_bm25_scores_rank_the_relevant_chunk_first(seeded_user):
    uid, _ = seeded_user
    scores = bm25.bm25_scores(uid, "refund policy")
    assert scores.shape == (2,)
    assert int(np.argmax(scores)) == 0
    assert scores[0] > 0


def test_index_is_cached_between_calls(seeded_user):
    uid, _ = seeded_user
    first = bm25.get_index(uid)
    second = bm25.get_index(uid)
    assert first is second


def test_index_is_rebuilt_after_a_new_chunk(seeded_user):
    uid, doc_id = seeded_user
    first = bm25.get_index(uid)

    db.insert_chunk(doc_id, uid, 2, "Warranty covers manufacturing defects.", np.ones(4, dtype=np.float32))

    second = bm25.get_index(uid)
    assert second is not first
    assert second.N == 3
    assert bm25.bm25_scores(uid, "warranty")[2] > 0


def test_index_is_rebuilt_after_a_deletion(seeded_user):
    uid, doc_id = seeded_user
    assert bm25.get_index(uid).N == 2

    db.delete_document(doc_id, uid)

    assert bm25.get_index(uid).N == 0
    assert bm25.bm25_scores(uid, "refund").tolist() == []


def test_indexes_are_isolated_per_user(seeded_user):
    uid, _ = seeded_user
    other = create_test_user("other@example.com")
    other_doc = db.create_document(other, "other.txt")
    db.insert_chunk(other_doc, other, 0, "Completely different subject matter.", np.ones(4, dtype=np.float32))

    assert bm25.get_index(uid).N == 2
    assert bm25.get_index(other).N == 1


def test_user_without_documents_gets_an_empty_index():
    uid = create_test_user("empty@example.com")
    assert bm25.get_index(uid).N == 0
    assert bm25.bm25_scores(uid, "anything").tolist() == []


def test_chinese_queries_match(seeded_user):
    uid, doc_id = seeded_user
    # Chunks are indexed in (doc_id, idx) order, so idx=2 lands at position 2.
    db.insert_chunk(doc_id, uid, 2, "退款政策允许在购买后三十天内退货。", np.ones(4, dtype=np.float32))
    scores = bm25.bm25_scores(uid, "退款政策")
    assert int(np.argmax(scores)) == 2
    assert scores[:2].tolist() == [0.0, 0.0]
