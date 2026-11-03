"""Test data builders and client helpers (plain functions, no fixtures).

Imported by conftest and by the individual test modules; `conftest` guarantees
the environment is configured before any of these run.
"""
from __future__ import annotations

import io
import json

from app import database as db, providers

# Small knowledge base used across retrieval / chat tests.
FAQ_TEXT = (
    "Refund policy\n\n"
    "Our refund policy allows returns within 30 days of purchase. "
    "Refunds are processed in 5 business days to the original payment method.\n\n"
    "Shipping\n\n"
    "Standard shipping takes 3 to 5 business days. Express shipping is delivered "
    "within 24 hours for orders placed before noon.\n"
)

FAQ_QUESTION = "How long does the refund policy last?"
FAQ_ANSWER_SNIPPET = "refund policy allows returns within 30 days"


# --------------------------------------------------------------- auth helpers

def register(client, email: str = "user@example.com", password: str = "secret123"):
    """Register an account and return the parsed JSON payload."""
    resp = client.post("/api/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


def login(client, email: str, password: str):
    """Raw login response (caller asserts the status code)."""
    return client.post("/api/auth/login", json={"email": email, "password": password})


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ----------------------------------------------------------- upload helpers

def upload(client, headers: dict, *files: tuple[str, bytes]):
    """POST /api/documents with one or more (filename, content) tuples."""
    parts = [
        ("files", (name, data, "application/octet-stream")) for name, data in files
    ]
    return client.post("/api/documents", headers=headers, files=parts)


# ----------------------------------------------------------------- SSE helpers

def parse_sse(body: str) -> list[dict]:
    """Turn a raw `text/event-stream` body into the list of JSON payloads."""
    events = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


def sse_text(events: list[dict]) -> str:
    return "".join(event.get("delta", "") for event in events)


# ------------------------------------------------------- document generators

def make_pdf(text: str = "Hello from a PDF document.") -> bytes:
    """Build a minimal, valid one-page PDF with a single line of text."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
    ]
    stream = f"BT /F1 18 Tf 72 700 Td ({text}) Tj ET".encode()
    objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")

    xref = out.tell()
    out.write(b"xref\n0 %d\n" % (len(objects) + 1))
    out.write(b"0000000000 65535 f \n")
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objects) + 1, xref)
    )
    return out.getvalue()


def make_docx(paragraphs: list[str]) -> bytes:
    """Build a real .docx in memory (python-docx is a runtime dependency)."""
    from docx import Document

    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ------------------------------------------------------------ database seeds

def create_test_user(email: str = "rag@example.com", is_admin: bool = False) -> str:
    """Insert a user directly (no HTTP round-trip) and return its id."""
    return db.create_user(email, "not-a-real-hash", is_admin)["id"]


def seed_document(user_id: str, filename: str, chunks: list[str]) -> str:
    """Insert a document plus its chunk embeddings exactly like an upload does."""
    doc_id = db.create_document(user_id, filename)
    vectors = providers.embed_texts(chunks)
    for idx, (chunk, vector) in enumerate(zip(chunks, vectors)):
        db.insert_chunk(doc_id, user_id, idx, chunk, vector.astype("float32"))
    db.finish_document(doc_id, len(chunks))
    return doc_id


def seed_faq_document(user_id: str, filename: str = "faq.txt") -> str:
    return seed_document(user_id, filename, [FAQ_TEXT])
