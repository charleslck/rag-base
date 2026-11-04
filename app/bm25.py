"""BM25 lexical retrieval with per-user in-memory index and caching.

The index is built lazily on first query and invalidated whenever the user's
document/chunk counts change (uploads and deletions). Tokenization is shared
with the demo embedding provider: latin words + CJK bigrams.
"""
import hashlib
import math
import threading
from collections import Counter, defaultdict

import numpy as np

from . import database as db
from .providers import _tokens

_CACHE_LIMIT = 256  # a scoped index per (user, doc set) — bound the memory


class BM25Index:
    """Classic BM25 over pre-tokenized documents (chunks)."""

    def __init__(self, docs_tokens: list[list[str]]):
        self.N = len(docs_tokens)
        self.dl = np.array([len(toks) for t, toks in enumerate(docs_tokens)], dtype=np.float64)
        self.avgdl = float(self.dl.mean()) if self.N else 0.0
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        df: Counter[str] = Counter()
        for i, toks in enumerate(docs_tokens):
            for term, tf in Counter(toks).items():
                self.postings[term].append((i, tf))
                df[term] += 1
        self.df = df
        self.k1 = 1.5
        self.b = 0.75

    def scores(self, query_tokens: list[str]) -> np.ndarray:
        out = np.zeros(self.N, dtype=np.float64)
        if not self.N or self.avgdl == 0:
            return out
        k1, b = self.k1, self.b
        for term in set(query_tokens):
            df_t = self.df.get(term)
            if not df_t:
                continue
            idf = math.log((self.N - df_t + 0.5) / (df_t + 0.5) + 1.0)
            for i, tf in self.postings[term]:
                denom = tf + k1 * (1 - b + b * self.dl[i] / self.avgdl)
                out[i] += idf * tf * (k1 + 1) / denom
        return out


_lock = threading.Lock()
_cache: dict[str, tuple[tuple[int, int], BM25Index]] = {}


def _fingerprint(user_id: str) -> tuple[int, int]:
    row = db.get_conn().execute(
        "SELECT COUNT(*) docs, COALESCE((SELECT COUNT(*) FROM chunks WHERE user_id = ?), 0) chunks "
        "FROM documents WHERE user_id = ?",
        (user_id, user_id),
    ).fetchone()
    return int(row["docs"]), int(row["chunks"])


def get_index(user_id: str, doc_ids=None) -> BM25Index:
    """Index for one user, optionally restricted to a set of documents.

    A scoped index gets its own cache slot: restricting the corpus changes both
    avgdl and every idf, so scores from a full index cannot simply be filtered.
    """
    fp = _fingerprint(user_id)
    sig = hashlib.md5(",".join(sorted(doc_ids)).encode()).hexdigest()[:16] if doc_ids else ""
    cache_key = user_id + "|" + sig
    with _lock:
        cached = _cache.get(cache_key)
        if cached and cached[0] == fp:
            return cached[1]
    sql = "SELECT c.id, c.content FROM chunks c WHERE c.user_id = ?"
    params: list = [user_id]
    if doc_ids:
        sql += f" AND c.doc_id IN ({','.join('?' * len(doc_ids))})"
        params.extend(doc_ids)
    sql += " ORDER BY c.doc_id, c.idx"  # must match db.all_chunk_vectors
    rows = db.get_conn().execute(sql, params).fetchall()
    index = BM25Index([_tokens(r["content"]) for r in rows])
    with _lock:
        if len(_cache) >= _CACHE_LIMIT:
            _cache.clear()
        _cache[cache_key] = (fp, index)
    return index


def bm25_scores(user_id: str, question: str, doc_ids=None) -> np.ndarray:
    return get_index(user_id, doc_ids).scores(_tokens(question))
