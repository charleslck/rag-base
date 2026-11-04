"""Background ingestion: uploads return early, the pool finishes the file."""
from __future__ import annotations

import pytest

from app import database as db, ingest_jobs, providers
from helpers import FAQ_TEXT, make_pdf, upload


@pytest.fixture(autouse=True)
def drain_pool():
    """Never let a queued job from one test write into the next test's database."""
    yield
    assert ingest_jobs.wait_idle(timeout=15), "ingestion pool did not drain"


def documents(client, user):
    return client.get("/api/documents", headers=user["headers"]).json()["documents"]


# --------------------------------------------------------------- queued ingest

def test_upload_returns_before_embedding_finishes(client, user, patch_settings):
    with patch_settings(ingest_sync=False):
        result = upload(client, user["headers"], ("faq.txt", FAQ_TEXT.encode())).json()["results"][0]

        assert result["ok"] is True
        assert result["status"] == "processing"
        assert result["chunks"] >= 1
        # the row is already visible, but has no chunks yet
        listed = documents(client, user)[0]
        assert listed["status"] == "processing"
        assert listed["num_chunks"] == 0

        assert ingest_jobs.wait_idle(timeout=15)

    listed = documents(client, user)[0]
    assert listed["status"] == "ready"
    assert listed["num_chunks"] == result["chunks"]
    assert listed["error"] is None


def test_a_queued_document_becomes_searchable(client, user, patch_settings):
    from helpers import FAQ_QUESTION, parse_sse, sse_text

    with patch_settings(ingest_sync=False):
        upload(client, user["headers"], ("faq.txt", FAQ_TEXT.encode()))
        assert ingest_jobs.wait_idle(timeout=15)

    resp = client.post("/api/chat", headers=user["headers"], json={"message": FAQ_QUESTION})
    assert "30 days" in sse_text(parse_sse(resp.text))


def test_batches_keep_the_chunk_order(client, user, patch_settings):
    body = ("Paragraph about refunds and shipping. " * 300).encode()

    with patch_settings(ingest_sync=False, ingest_batch=2):
        result = upload(client, user["headers"], ("long.txt", body)).json()["results"][0]
        assert result["chunks"] > 2
        assert ingest_jobs.wait_idle(timeout=15)

    rows = db.get_conn().execute(
        "SELECT idx FROM chunks WHERE doc_id = ? ORDER BY idx", (result["id"],)
    ).fetchall()
    assert [r["idx"] for r in rows] == list(range(result["chunks"]))


# ------------------------------------------------------------------- failures

def test_unreadable_files_still_fail_inside_the_request(client, user, patch_settings):
    with patch_settings(ingest_sync=False):
        result = upload(client, user["headers"], ("broken.pdf", b"not a pdf at all")).json()["results"][0]

    assert result["ok"] is False
    assert result["error"]
    assert documents(client, user)[0]["status"] == "error"


def test_an_embedding_failure_marks_the_document(client, user, patch_settings, monkeypatch):
    def boom(texts):
        raise RuntimeError("embedding API is down")

    monkeypatch.setattr(providers, "embed_texts", boom)

    with patch_settings(ingest_sync=False):
        upload(client, user["headers"], ("faq.txt", FAQ_TEXT.encode()))
        assert ingest_jobs.wait_idle(timeout=15)

    listed = documents(client, user)[0]
    assert listed["status"] == "error"
    assert "RuntimeError" in listed["error"]
    assert db.get_conn().execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"] == 0


def test_a_document_deleted_before_the_job_lands_is_not_resurrected(client, user):
    """The worker checks ownership before writing, so a delete wins the race."""
    uid = db.get_user_by_email(user["email"])["id"]

    ingest_jobs._run("doc-that-never-existed", uid, ["some text"])

    assert db.get_conn().execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"] == 0


def test_a_restart_closes_out_interrupted_uploads(client, user):
    uid = db.get_user_by_email(user["email"])["id"]
    doc_id = db.create_document(uid, "half-done.pdf", status="processing")

    closed = db.fail_stuck_documents()

    assert closed == 1
    doc = db.get_document(doc_id, uid)
    assert doc["status"] == "error"
    assert "restart" in doc["error"].lower()


def test_a_ready_document_is_left_alone(client, user):
    uid = db.get_user_by_email(user["email"])["id"]
    doc_id = db.create_document(uid, "fine.txt", status="ready")

    assert db.fail_stuck_documents() == 0
    assert db.get_document(doc_id, uid)["status"] == "ready"


def test_ingest_sync_runs_inline(client, user, patch_settings):
    with patch_settings(ingest_sync=True):
        result = upload(client, user["headers"], ("faq.txt", FAQ_TEXT.encode())).json()["results"][0]

    assert result["status"] == "ready"
    assert documents(client, user)[0]["status"] == "ready"


def test_a_pdf_is_parsed_and_queued(client, user, patch_settings):
    with patch_settings(ingest_sync=False):
        result = upload(
            client, user["headers"], ("guide.pdf", make_pdf("PDF document body."))
        ).json()["results"][0]
        assert result["ok"] is True
        assert ingest_jobs.wait_idle(timeout=15)

    assert documents(client, user)[0]["status"] == "ready"
