"""SQLite layer: users, documents, chunks, embeddings, API keys, conversations."""
from __future__ import annotations

import os
import subprocess
import sys
import time

import numpy as np
import pytest

from app import database as db
from app.config import settings


# ------------------------------------------------------------------- helpers

def _make_user(email: str = "db@example.com", is_admin: bool = False) -> str:
    return db.create_user(email, "hash", is_admin)["id"]


def _make_doc(user_id: str, filename: str = "doc.txt") -> str:
    return db.create_document(user_id, filename)


def _count(table: str) -> int:
    return db.get_conn().execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]


# --------------------------------------------------------------------- users

def test_create_user_returns_public_fields_and_persists():
    created = db.create_user("a@example.com", "hashed", is_admin=True)
    assert set(created) == {"id", "email", "is_admin"}
    assert created["is_admin"] is True

    stored = db.get_user_by_id(created["id"])
    assert stored["email"] == "a@example.com"
    assert stored["password_hash"] == "hashed"
    assert stored["is_admin"] == 1  # SQLite has no bool


def test_get_user_by_email_is_exact_match():
    db.create_user("Case@Example.com", "h", False)
    assert db.get_user_by_email("Case@Example.com") is not None
    assert db.get_user_by_email("case@example.com") is None
    assert db.get_user_by_email("missing@example.com") is None


def test_get_user_by_id_returns_none_for_unknown_id():
    assert db.get_user_by_id("does-not-exist") is None


def test_user_count_and_list_users_are_ordered_by_creation():
    first = _make_user("first@example.com", is_admin=True)
    time.sleep(0.01)
    second = _make_user("second@example.com")
    assert db.user_count() == 2

    listed = db.list_users()
    assert [u["id"] for u in listed] == [first, second]
    assert "password_hash" not in listed[0]


def test_duplicate_email_is_rejected_by_the_unique_constraint():
    db.create_user("dup@example.com", "h", False)
    with pytest.raises(Exception):
        db.create_user("dup@example.com", "h", False)


def test_update_password_reports_whether_a_row_changed():
    uid = _make_user()
    assert db.update_password(uid, "new-hash") is True
    assert db.get_user_by_id(uid)["password_hash"] == "new-hash"
    assert db.update_password("ghost", "new-hash") is False


def test_delete_user_cascades_to_every_child_table():
    uid = _make_user()
    doc_id = _make_doc(uid)
    db.insert_chunk(doc_id, uid, 0, "chunk body", np.ones(4, dtype=np.float32))
    cid = db.create_conversation(uid, "hello")
    db.add_message(cid, "user", "hi", sources=[{"rank": 1}])
    db.create_api_key(uid, "label")

    assert (_count("documents"), _count("chunks"), _count("embeddings")) == (1, 1, 1)
    assert (_count("conversations"), _count("messages"), _count("api_keys")) == (1, 1, 1)

    db.delete_user(uid)

    assert db.get_user_by_id(uid) is None
    for table in ("documents", "chunks", "embeddings", "conversations", "messages", "api_keys"):
        assert _count(table) == 0, table


# ------------------------------------------------------------------ vectors

def test_vector_blob_round_trip_keeps_float32_precision():
    original = [0.5, -1.25, 3.0, 0.0]
    blob = db.vec_to_blob(original)
    assert isinstance(blob, bytes)
    assert len(blob) == 4 * 4  # float32

    restored = db.blob_to_vec(blob)
    assert restored.dtype == np.float32
    np.testing.assert_allclose(restored, np.array(original, dtype=np.float32))


def test_vec_to_blob_accepts_numpy_and_lists():
    assert db.vec_to_blob(np.array([1, 2])) == db.vec_to_blob([1, 2])


# ----------------------------------------------------------------- documents

def test_create_and_fetch_document_scoped_to_owner():
    owner = _make_user("owner@example.com")
    stranger = _make_user("stranger@example.com")
    doc_id = _make_doc(owner, "guide.pdf")

    assert db.get_document(doc_id, owner)["filename"] == "guide.pdf"
    assert db.get_document(doc_id, stranger) is None
    assert db.get_document("missing", owner) is None


def test_new_documents_start_ready_with_zero_chunks():
    doc_id = _make_doc(_make_user())
    row = db.get_document(doc_id, db.list_users()[0]["id"])
    assert row["status"] == "ready"
    assert row["num_chunks"] == 0
    assert row["error"] is None


def test_finish_document_marks_success_and_failure():
    uid = _make_user()
    ok_doc = _make_doc(uid, "ok.txt")
    bad_doc = _make_doc(uid, "bad.bin")

    db.finish_document(ok_doc, 4)
    db.finish_document(bad_doc, 0, error="Unsupported file type")

    ok = db.get_document(ok_doc, uid)
    assert (ok["status"], ok["num_chunks"], ok["error"]) == ("ready", 4, None)

    bad = db.get_document(bad_doc, uid)
    assert (bad["status"], bad["num_chunks"], bad["error"]) == ("error", 0, "Unsupported file type")


def test_list_documents_is_newest_first_and_owner_scoped():
    uid = _make_user("a@example.com")
    other = _make_user("b@example.com")
    _make_doc(uid, "older.txt")
    time.sleep(0.01)
    _make_doc(uid, "newer.txt")
    _make_doc(other, "foreign.txt")

    listed = db.list_documents(uid)
    assert [d["filename"] for d in listed] == ["newer.txt", "older.txt"]
    assert db.list_documents(other)[0]["filename"] == "foreign.txt"
    assert db.doc_count_for_user(uid) == 2
    assert db.doc_count_for_user("nobody") == 0


def test_delete_document_is_owner_scoped_and_cascades():
    uid = _make_user("a@example.com")
    other = _make_user("b@example.com")
    doc_id = _make_doc(uid)
    db.insert_chunk(doc_id, uid, 0, "text", np.ones(3, dtype=np.float32))

    assert db.delete_document(doc_id, other) is False
    assert db.get_document(doc_id, uid) is not None

    assert db.delete_document(doc_id, uid) is True
    assert db.get_document(doc_id, uid) is None
    assert _count("chunks") == 0
    assert _count("embeddings") == 0
    assert db.delete_document(doc_id, uid) is False


def test_insert_chunk_keeps_index_and_content():
    uid = _make_user()
    doc_id = _make_doc(uid)
    db.insert_chunk(doc_id, uid, 0, "first", np.zeros(3, dtype=np.float32))
    db.insert_chunk(doc_id, uid, 1, "second", np.ones(3, dtype=np.float32))

    rows, matrix = db.all_chunk_vectors(uid)
    assert [r["content"] for r in rows] == ["first", "second"]
    assert [r["idx"] for r in rows] == [0, 1]
    assert matrix.shape == (2, 3)


def test_all_chunk_vectors_is_empty_but_well_shaped_without_chunks():
    rows, matrix = db.all_chunk_vectors(_make_user())
    assert rows == []
    assert matrix.shape == (0, settings.embedding_dim)


def test_chunks_are_scoped_per_user():
    alice = _make_user("alice@example.com")
    bob = _make_user("bob@example.com")
    for uid, text in ((alice, "alice text"), (bob, "bob text")):
        doc_id = _make_doc(uid)
        db.insert_chunk(doc_id, uid, 0, text, np.ones(2, dtype=np.float32))

    rows, _ = db.all_chunk_vectors(alice)
    assert [r["content"] for r in rows] == ["alice text"]


# ------------------------------------------------------------------ api keys

def test_api_key_is_hashed_and_only_shown_once():
    uid = _make_user()
    record, full = db.create_api_key(uid, "widget")

    assert full.startswith("rbk_")
    assert len(full) == 4 + 48  # "rbk_" + 24 random bytes as hex
    assert record["prefix"] == full[:10]
    assert record["key_hash"] != full
    assert len(record["key_hash"]) == 64  # sha256 hex

    stored = db.list_api_keys(uid)
    assert len(stored) == 1
    assert "key_hash" not in stored[0]
    assert stored[0]["label"] == "widget"


def test_get_user_by_api_key_requires_the_exact_raw_key():
    uid = _make_user()
    _, full = db.create_api_key(uid)

    assert db.get_user_by_api_key(full)["id"] == uid
    assert db.get_user_by_api_key(full[:-1]) is None
    assert db.get_user_by_api_key("rbk_deadbeef") is None
    assert db.get_user_by_api_key("") is None


def test_api_key_label_is_truncated_to_80_chars():
    uid = _make_user()
    record, _ = db.create_api_key(uid, "x" * 200)
    assert len(record["label"]) == 80


def test_revoke_api_key_is_owner_scoped():
    uid = _make_user("a@example.com")
    other = _make_user("b@example.com")
    record, full = db.create_api_key(uid)

    assert db.revoke_api_key(record["id"], other) is False
    assert db.get_user_by_api_key(full) is not None

    assert db.revoke_api_key(record["id"], uid) is True
    assert db.get_user_by_api_key(full) is None
    assert db.revoke_api_key(record["id"], uid) is False


# -------------------------------------------------------------- conversations

def test_conversations_are_listed_by_recency_and_scoped():
    uid = _make_user("a@example.com")
    other = _make_user("b@example.com")
    first = db.create_conversation(uid, "first")
    time.sleep(0.01)
    second = db.create_conversation(uid, "second")
    db.create_conversation(other, "theirs")

    assert [c["id"] for c in db.list_conversations(uid)] == [second, first]
    assert db.get_conversation(first, other) is None


def test_touch_conversation_updates_title_and_timestamp():
    uid = _make_user()
    cid = db.create_conversation(uid, "New conversation")
    before = db.get_conversation(cid, uid)["updated_at"]

    time.sleep(0.01)
    db.touch_conversation(cid, "Refund policy")
    updated = db.get_conversation(cid, uid)
    assert updated["title"] == "Refund policy"
    assert updated["updated_at"] > before

    db.touch_conversation(cid)
    assert db.get_conversation(cid, uid)["title"] == "Refund policy"


def test_delete_conversation_cascades_to_messages():
    uid = _make_user()
    cid = db.create_conversation(uid)
    db.add_message(cid, "user", "hello")

    assert db.delete_conversation(cid, "someone-else") is False
    assert _count("messages") == 1
    assert db.delete_conversation(cid, uid) is True
    assert _count("messages") == 0


def test_messages_keep_order_roles_and_sources():
    uid = _make_user()
    cid = db.create_conversation(uid)
    sources = [{"rank": 1, "doc_id": "d1", "doc_name": "faq.txt", "score": 0.5, "snippet": "x"}]
    db.add_message(cid, "user", "question?")
    db.add_message(cid, "assistant", "answer", sources=sources)

    messages = db.get_messages(cid)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["sources"] == []
    assert messages[1]["sources"] == sources
    assert messages[1]["content"] == "answer"


def test_stats_counts_everything():
    uid = _make_user()
    doc_id = _make_doc(uid)
    db.insert_chunk(doc_id, uid, 0, "a", np.ones(2, dtype=np.float32))
    cid = db.create_conversation(uid)
    db.add_message(cid, "user", "hi")

    assert db.stats() == {"users": 1, "documents": 1, "chunks": 1, "conversations": 1}


# ------------------------------------------------------------------ plumbing

def test_init_db_is_idempotent():
    db.init_db()
    db.init_db()
    assert db.user_count() == 0


def test_new_id_is_a_32_char_hex_string():
    values = {db.new_id() for _ in range(50)}
    assert len(values) == 50
    assert all(len(v) == 32 for v in values)
    assert all(int(v, 16) >= 0 for v in values)


def test_now_returns_a_unix_timestamp():
    assert abs(db.now() - time.time()) < 5


def test_schema_is_rebuilt_when_the_database_file_disappears(temp_dir):
    """The running server must recover from a wiped/restored data directory.

    Run in a subprocess so the parent's cached connections are untouched.
    """
    data_dir = temp_dir / "data"
    env = {**os.environ, "RAGBASE_DATA_DIR": str(data_dir)}
    script = (
        "from app import database as db\n"
        "db.init_db()\n"
        "print(db.user_count())\n"
        "db.create_user('a@b.co', 'h', True)\n"
        "print(db.user_count())\n"
    )

    def run():
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.split()

    assert run() == ["0", "1"]
    for suffix in ("", "-wal", "-shm"):
        path = data_dir / f"ragbase.db{suffix}"
        if path.exists():
            path.unlink()
    # Fresh file, fresh schema, empty tables.
    assert run() == ["0", "1"]
