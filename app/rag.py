"""Retrieval-augmented generation: cosine search over the user's chunks,
prompt assembly with numbered citations, and answer generation.
"""
import numpy as np

from . import bm25, database as db, providers
from .config import settings

RRF_K = 60  # rank-fusion constant


def _rrf_fuse(vec_scores: np.ndarray, bm_scores: np.ndarray) -> np.ndarray:
    """Reciprocal Rank Fusion of two score vectors.

    Each candidate's fused score is the sum of 1/(RRF_K + rank) over both
    rankings, which combines the two retrievers without score normalisation.
    """
    n = len(vec_scores)
    fused = np.zeros(n, dtype=np.float64)
    for scores in (vec_scores, bm_scores):
        order = np.argsort(-scores)
        ranks = np.empty(n, dtype=np.float64)
        ranks[order] = np.arange(1, n + 1, dtype=np.float64)
        hit = scores > 0
        fused[hit] += 1.0 / (RRF_K + ranks[hit])
    return fused

import re


def _snippet(text: str, limit: int = 400) -> str:
    clean = re.sub(r"[#>*`]", "", text).strip()
    clean = re.sub(r"\s+", " ", clean)
    return clean[:limit]


SYSTEM_PROMPT = (
    "You are a knowledgeable assistant that answers questions strictly based on "
    "the provided knowledge-base context. Rules:\n"
    "1. Answer using ONLY the information in the context below. Never invent facts.\n"
    "2. Cite every claim with the context number in square brackets, e.g. [1], [2].\n"
    "3. If the context does not contain the answer, say exactly that the "
    "knowledge base does not cover it, and suggest what to upload.\n"
    "4. Be concise and well structured."
)


def retrieve(user_id: str, question: str, top_k: int | None = None) -> list[dict]:
    k = top_k or settings.top_k
    rows, matrix = db.all_chunk_vectors(user_id)
    if not rows:
        return []
    qvec = providers.embed_texts([question])[0].astype(np.float32)
    # stored vectors may come from a different provider dimension (demo mode):
    # pad both sides to a common dimension for the dot product
    dim = int(max(matrix.shape[1], qvec.shape[0]))
    if matrix.shape[1] < dim:
        matrix = np.pad(matrix, ((0, 0), (0, dim - matrix.shape[1])))
    if qvec.shape[0] < dim:
        qvec = np.pad(qvec, (0, dim - qvec.shape[0]))
    scores = matrix @ qvec

    # hybrid fusion: dense/vector ranking + BM25 lexical ranking via RRF
    fused = _rrf_fuse(scores, bm25.bm25_scores(user_id, question).astype(np.float32))
    order = np.argsort(-fused)[: k * 3]

    # light diversity pass: cap chunks per document, then take top-k
    per_doc: dict[str, int] = {}
    chosen = []
    # local lexical vectors score lower than dense embeddings — relax the floor
    threshold = 0.05 if settings.embedding_local else settings.retrieval_threshold
    for i in order:
        row = rows[int(i)]
        if float(scores[int(i)]) < threshold and float(fused[int(i)]) == 0:
            continue
        if per_doc.get(row["doc_id"], 0) >= 3:
            continue
        per_doc[row["doc_id"]] = per_doc.get(row["doc_id"], 0) + 1
        chosen.append((float(fused[int(i)]), row))
        if len(chosen) >= k:
            break

    doc_ids = {r["doc_id"] for _, r in chosen}
    docs = {
        d["id"]: d["filename"]
        for d in (
            db.get_conn()
            .execute(
                f"SELECT id, filename FROM documents WHERE id IN ({','.join('?' * len(doc_ids))})",
                tuple(doc_ids),
            )
            .fetchall()
        )
    } if doc_ids else {}

    out = []
    for rank, (score, row) in enumerate(chosen, start=1):
        out.append(
            {
                "rank": rank,
                "doc_id": row["doc_id"],
                "doc_name": docs.get(row["doc_id"], "document"),
                "chunk_idx": row["idx"],
                "score": round(score, 4),
                "content": row["content"],
            }
        )
    return out


def build_messages(question: str, contexts: list[dict], history: list[dict], has_documents: bool) -> list[dict]:
    if contexts:
        blocks = "\n\n".join(
            f"[{c['rank']}] (from {c['doc_name']})\n{c['content']}" for c in contexts
        )
        context_msg = f"Knowledge-base context:\n{blocks}"
    elif not has_documents:
        context_msg = (
            "Knowledge-base context: (empty — the user has NOT uploaded any documents yet. "
            "Tell them to upload documents first; do not answer from your own knowledge.)"
        )
    else:
        context_msg = (
            "Knowledge-base context: (no relevant passages were retrieved for this question, "
            "but the user HAS uploaded documents. Say that the knowledge base has no relevant "
            "information on this topic — do NOT claim it is empty — and suggest rephrasing the "
            "question or asking about the uploaded documents' topics.)"
        )
    messages = [{"role": "system", "content": SYSTEM_PROMPT + "\n\n" + context_msg}]
    messages.extend(history[-10:])
    messages.append({"role": "user", "content": question})
    return messages


def _sources_payload(contexts: list[dict]) -> list[dict]:
    return [
        {
            "rank": c["rank"],
            "doc_id": c["doc_id"],
            "doc_name": c["doc_name"],
            "score": c["score"],
            "snippet": _snippet(c["content"]),
        }
        for c in contexts
    ]


def answer_stream(user_id: str, question: str, history: list[dict]):
    """Yield (token, event) tuples. Final event carries the sources list."""
    contexts = retrieve(user_id, question)
    has_docs = db.doc_count_for_user(user_id) > 0
    messages = build_messages(question, contexts, history, has_docs)
    try:
        for token in providers.chat_stream(messages, contexts):
            yield ("delta", token)
    except Exception:
        yield ("delta", "\n\n[Model request failed — check the LLM key / base URL / model settings in .env.]")
    yield ("done", _sources_payload(contexts))


def answer_once(user_id: str, question: str, history: list[dict]) -> tuple[str, list[dict]]:
    contexts = retrieve(user_id, question)
    has_docs = db.doc_count_for_user(user_id) > 0
    messages = build_messages(question, contexts, history, has_docs)
    try:
        text = providers.chat_once(messages, contexts)
    except Exception:
        text = "[Model request failed — check the LLM key / base URL / model settings in .env.]"
    return text, _sources_payload(contexts)
