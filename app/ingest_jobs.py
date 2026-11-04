"""Background embedding for uploads.

The upload request parses and chunks the file (cheap, and a malformed PDF has
to fail before anything is written), then hands the text to this pool. Embedding
is the expensive half: an embedding API call per batch of chunks plus one insert
per chunk. Doing that inside the request meant one 200-page PDF held the
connection open and — because the endpoint is `async def` and the work is
blocking — froze every other request in the process while it ran.

Status lives in the `documents` table (`processing` → `ready` / `error`), not in
this module, so the UI can poll it and a restart cannot leave a job half-known.
"""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from . import database as db, providers
from .config import settings

log = logging.getLogger("ragbase.ingest")

_pool = ThreadPoolExecutor(
    max_workers=max(1, settings.ingest_workers), thread_name_prefix="ragbase-ingest"
)
_cond = threading.Condition()
_pending = 0


def submit(doc_id: str, user_id: str, chunks: list[str]) -> None:
    """Queue one document for embedding.

    `RAGBASE_INGEST_SYNC=1` runs it inline instead: tests (and anyone debugging
    a parser) get a row that is already `ready` when the request returns.
    """
    if settings.ingest_sync:
        _run(doc_id, user_id, chunks)
        return
    global _pending
    with _cond:
        _pending += 1
    _pool.submit(_job, doc_id, user_id, chunks)


def _job(doc_id: str, user_id: str, chunks: list[str]) -> None:
    try:
        _run(doc_id, user_id, chunks)
    finally:
        global _pending
        with _cond:
            _pending -= 1
            _cond.notify_all()


def _run(doc_id: str, user_id: str, chunks: list[str]) -> None:
    """Embed and store one document in batches, then close out its row."""
    batch = max(1, settings.ingest_batch)
    stored = 0
    try:
        for start in range(0, len(chunks), batch):
            # the document may have been deleted while we were working; the
            # insert would then fail on the foreign key for no good reason
            if db.get_document(doc_id, user_id) is None:
                return
            window = chunks[start : start + batch]
            vectors = providers.embed_texts(window)
            stored += db.insert_chunks(doc_id, user_id, window, vectors, offset=start)
        db.finish_document(doc_id, stored)
    except Exception as exc:  # provider outage, bad vector, disk full…
        log.warning("ingestion failed for %s: %s", doc_id, exc)
        db.finish_document(doc_id, stored, f"{type(exc).__name__}: {exc}"[:300])


def pending() -> int:
    """Jobs queued or running right now."""
    with _cond:
        return _pending


def wait_idle(timeout: float | None = None) -> bool:
    """Block until the queue drains. Used at shutdown and by tests."""
    with _cond:
        return _cond.wait_for(lambda: _pending == 0, timeout)
