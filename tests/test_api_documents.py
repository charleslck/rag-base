"""Document endpoints: upload, listing, deletion, limits and isolation."""
from __future__ import annotations

import pytest

from helpers import FAQ_TEXT, make_docx, make_pdf, upload


# --------------------------------------------------------------- upload happy path

def test_upload_txt_returns_chunk_count_and_id(client, user):
    resp = upload(client, user["headers"], ("faq.txt", FAQ_TEXT.encode()))

    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert result["ok"] is True
    assert result["filename"] == "faq.txt"
    assert result["chunks"] >= 1
    assert result["id"]


def test_uploaded_document_appears_in_the_listing(client, user):
    doc_id = upload(client, user["headers"], ("faq.txt", FAQ_TEXT.encode())).json()["results"][0]["id"]

    docs = client.get("/api/documents", headers=user["headers"]).json()["documents"]

    assert len(docs) == 1
    assert docs[0]["id"] == doc_id
    assert docs[0]["status"] == "ready"
    assert docs[0]["num_chunks"] >= 1
    assert docs[0]["error"] is None


def test_document_listing_starts_empty(client, user):
    assert client.get("/api/documents", headers=user["headers"]).json() == {"documents": []}


def test_multiple_files_in_one_request(client, user):
    resp = upload(
        client,
        user["headers"],
        ("a.txt", b"Alpha content about refunds."),
        ("b.txt", b"Beta content about shipping."),
        ("c.txt", b"Gamma content about warranty."),
    )

    results = resp.json()["results"]
    assert [r["filename"] for r in results] == ["a.txt", "b.txt", "c.txt"]
    assert all(r["ok"] for r in results)
    assert len(client.get("/api/documents", headers=user["headers"]).json()["documents"]) == 3


@pytest.mark.parametrize(
    "filename, payload",
    [
        ("notes.md", b"# Heading\n\nMarkdown body."),
        ("notes.markdown", b"Plain markdown."),
        ("page.html", b"<html><body><p>HTML body.</p></body></html>"),
        ("guide.docx", make_docx(["Word document body."])),
        ("guide.pdf", make_pdf("PDF document body.")),
    ],
)
def test_supported_formats_are_ingested(client, user, filename, payload):
    result = upload(client, user["headers"], (filename, payload)).json()["results"][0]
    assert result["ok"] is True, result
    assert result["chunks"] >= 1


def test_chunk_count_matches_the_chunker(client, user):
    from app import ingest

    body = ("Paragraph about refunds. " * 400).encode()
    result = upload(client, user["headers"], ("long.txt", body)).json()["results"][0]

    expected = len(ingest.chunk_text(ingest.extract_text("long.txt", body)))
    assert result["chunks"] == expected > 1


# ------------------------------------------------------------- upload failures

def test_unsupported_file_type_is_reported_per_file(client, user):
    resp = upload(client, user["headers"], ("virus.exe", b"MZ\x90\x00"))

    result = resp.json()["results"][0]
    assert result["ok"] is False
    assert "Unsupported file type" in result["error"]

    docs = client.get("/api/documents", headers=user["headers"]).json()["documents"]
    assert docs[0]["status"] == "error"
    assert "Unsupported file type" in docs[0]["error"]


def test_empty_file_is_rejected(client, user):
    result = upload(client, user["headers"], ("empty.txt", b"   \n\n\t")).json()["results"][0]

    assert result["ok"] is False
    assert "No readable text found" in result["error"]


def test_oversized_file_is_rejected(client, user, patch_settings):
    with patch_settings(max_upload_mb=0):
        result = upload(client, user["headers"], ("big.txt", b"any content")).json()["results"][0]

    assert result["ok"] is False
    assert "File too large (max 0 MB)" in result["error"]


def test_partial_failures_do_not_stop_the_other_files(client, user):
    resp = upload(
        client,
        user["headers"],
        ("good.txt", b"Valid text about refunds."),
        ("bad.bin", b"\x00\x01\x02"),
    )
    results = resp.json()["results"]
    assert results[0]["ok"] is True
    assert results[1]["ok"] is False


def test_request_without_files_is_rejected(client, user):
    resp = client.post("/api/documents", headers=user["headers"])
    assert resp.status_code == 422


def test_upload_requires_authentication(client):
    resp = client.post("/api/documents", files=[("files", ("a.txt", b"hello", "text/plain"))])
    assert resp.status_code == 401


# -------------------------------------------------------------------- limits

def test_document_limit_is_enforced(client, user, patch_settings):
    with patch_settings(max_docs_per_user=2):
        first = upload(client, user["headers"], ("a.txt", b"first doc"))
        second = upload(client, user["headers"], ("b.txt", b"second doc"))
        assert first.status_code == 200 and second.status_code == 200

        third = upload(client, user["headers"], ("c.txt", b"third doc"))

    assert third.status_code == 400
    assert "Document limit reached (2 per user)" in third.json()["detail"]


def test_document_limit_counts_files_in_a_single_request(client, user, patch_settings):
    with patch_settings(max_docs_per_user=1):
        resp = upload(
            client,
            user["headers"],
            ("a.txt", b"first"),
            ("b.txt", b"second"),
        )

    assert resp.status_code == 400
    assert client.get("/api/documents", headers=user["headers"]).json()["documents"] == []


def test_zero_means_unlimited(client, user, patch_settings):
    with patch_settings(max_docs_per_user=0):
        for index in range(5):
            resp = upload(client, user["headers"], (f"doc{index}.txt", f"content {index}".encode()))
            assert resp.status_code == 200

    assert len(client.get("/api/documents", headers=user["headers"]).json()["documents"]) == 5


# -------------------------------------------------------------------- delete

def test_delete_removes_the_document(client, user):
    doc_id = upload(client, user["headers"], ("faq.txt", FAQ_TEXT.encode())).json()["results"][0]["id"]

    assert client.delete(f"/api/documents/{doc_id}", headers=user["headers"]).json() == {"ok": True}
    assert client.get("/api/documents", headers=user["headers"]).json()["documents"] == []


def test_delete_unknown_document_returns_404(client, user):
    resp = client.delete("/api/documents/nope", headers=user["headers"])
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Document not found"


def test_delete_requires_authentication(client, user):
    doc_id = upload(client, user["headers"], ("faq.txt", b"body")).json()["results"][0]["id"]
    assert client.delete(f"/api/documents/{doc_id}").status_code == 401


# --------------------------------------------------------------- bulk delete

def test_bulk_delete_clears_a_whole_selection_in_one_request(client, user):
    ids = [
        r["id"]
        for r in upload(
            client,
            user["headers"],
            ("a.txt", b"Alpha content about refunds."),
            ("b.txt", b"Beta content about shipping."),
            ("c.txt", b"Gamma content about warranty."),
        ).json()["results"]
    ]

    resp = client.post("/api/documents/delete", json={"ids": ids[:2]}, headers=user["headers"])

    assert resp.status_code == 200
    assert resp.json() == {"deleted": 2, "requested": 2}
    left = client.get("/api/documents", headers=user["headers"]).json()["documents"]
    assert [d["id"] for d in left] == [ids[2]]


def test_bulk_delete_takes_the_chunks_and_vectors_with_it(client, user):
    """Orphaned chunks stay searchable, so a delete that misses them is worse
    than a delete that fails."""
    from app import database as db

    doc_id = upload(client, user["headers"], ("faq.txt", FAQ_TEXT.encode())).json()["results"][0]["id"]
    uid = db.get_user_by_email("user@example.com")["id"]
    assert len(db.all_chunk_vectors(uid)[0]) > 0

    client.post("/api/documents/delete", json={"ids": [doc_id]}, headers=user["headers"])

    assert db.all_chunk_vectors(uid)[0] == []
    assert db.get_conn().execute("SELECT COUNT(*) c FROM embeddings").fetchone()["c"] == 0


def test_bulk_delete_ignores_ids_that_are_not_yours(client, admin, user):
    """A crafted body must not become a way to probe other accounts."""
    foreign = upload(client, admin["headers"], ("private.txt", b"Admin only.")).json()["results"][0]["id"]
    mine = upload(client, user["headers"], ("mine.txt", b"My own document.")).json()["results"][0]["id"]

    resp = client.post("/api/documents/delete", json={"ids": [foreign, mine, "nope"]}, headers=user["headers"])

    assert resp.json() == {"deleted": 1, "requested": 3}
    assert len(client.get("/api/documents", headers=admin["headers"]).json()["documents"]) == 1


def test_bulk_delete_needs_at_least_one_id(client, user):
    resp = client.post("/api/documents/delete", json={"ids": []}, headers=user["headers"])
    assert resp.status_code == 400
    assert resp.json()["detail"] == "No document ids given"


def test_bulk_delete_requires_authentication(client, user):
    doc_id = upload(client, user["headers"], ("faq.txt", b"body")).json()["results"][0]["id"]
    assert client.post("/api/documents/delete", json={"ids": [doc_id]}).status_code == 401


# ---------------------------------------------------------------- isolation

def test_users_cannot_see_or_delete_other_users_documents(client, admin, user):
    doc_id = upload(client, admin["headers"], ("private.txt", b"Admin only content.")).json()["results"][0]["id"]

    assert client.get("/api/documents", headers=user["headers"]).json()["documents"] == []
    assert client.delete(f"/api/documents/{doc_id}", headers=user["headers"]).status_code == 404
    assert len(client.get("/api/documents", headers=admin["headers"]).json()["documents"]) == 1


def test_documents_do_not_survive_a_deleted_account(client, user):
    from app import database as db

    upload(client, user["headers"], ("faq.txt", FAQ_TEXT.encode()))
    db.delete_user(db.get_user_by_email("user@example.com")["id"])

    assert db.stats()["documents"] == 0
    assert client.get("/api/documents", headers=user["headers"]).status_code == 401
