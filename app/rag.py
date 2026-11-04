"""Retrieval-augmented generation: cosine search over the user's chunks,
prompt assembly with numbered citations, and answer generation.
"""
import re

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


# ---------------------------------------------------------------- intent

# Markers of a question that asks about the corpus as a whole rather than about
# one passage: "有哪些人", "列出所有导演", "how many documents".
_ENUM_RE = re.compile(
    r"有哪些|哪些|哪几|有谁|都有谁|都是谁|列出|列举|名单|全部|所有|一共|总共|"
    r"多少位|多少个|几位|几个|分别是谁|分别有|都有什么",
)
_ENUM_RE_EN = re.compile(r"\b(list|all of|which|who are|how many|every)\b", re.IGNORECASE)


def has_enumeration_marker(question: str) -> bool:
    """Cheap pre-check: does the wording ask for a list at all?"""
    q = question.strip()
    return bool(_ENUM_RE.search(q) or _ENUM_RE_EN.search(q))


def is_corpus_question(question: str, filenames: list[str]) -> bool:
    """True when the question sweeps the whole knowledge base.

    An enumeration marker alone is not enough: "周星驰演过哪些电影" contains
    "哪些" but is a lookup of one document, and widening it would only dilute
    the context. So a question that names a document in the base keeps the
    narrow path; one that names nobody is asking about the collection.
    """
    q = question.strip()
    if not has_enumeration_marker(q):
        return False
    for name in filenames:
        stem = re.sub(r"\.(pdf|docx?|txt|md|markdown|html?)$", "", name, flags=re.IGNORECASE)
        stem = re.sub(r"^[\d\s._-]+", "", stem).strip()
        if len(stem) >= 2 and (stem in q or name in q):
            return False
    return True


def _digest(text: str | None, chars: int = 60) -> str:
    """First `chars` characters of a document, flattened to one line."""
    if not text:
        return ""
    flat = re.sub(r"\s+", " ", re.sub(r"[#>*`]", " ", text)).strip()
    return flat[:chars].rstrip()


def corpus_inventory(user_id: str, doc_ids=None, budget: int | None = None) -> str:
    """One line per document, for questions that ask what the base covers."""
    rows, total = db.document_digests(user_id, doc_ids, settings.inventory_docs)
    return _inventory_block(rows, total, budget) if rows else ""


def _inventory_block(rows: list[dict], total: int, budget: int | None = None) -> str:
    """Render the index: filename plus the opening of each document.

    Six passages can never summarise a hundred-document base, so the model gets
    an index instead. The opening usually names the category ("电影男演员"),
    which is what a "which singers are in here" question actually needs. Capped
    by count and by characters so a huge base cannot blow up the prompt.
    """
    budget = budget or settings.context_chars // 2
    lines, used = [], 0
    for row in rows:
        digest = _digest(row["head"])
        line = f"- {row['filename']}: {digest}" if digest else f"- {row['filename']}"
        if used + len(line) > budget:
            break
        lines.append(line)
        used += len(line) + 1
    if not lines:
        return ""
    shown = len(lines)
    head = (
        f"Knowledge-base inventory ({total} documents in total"
        + (f", showing {shown}" if shown < total else "")
        + "):"
    )
    tail = (
        "How to use the inventory:\n"
        "- It answers questions about what the knowledge base holds: the total "
        "document count, which people or topics it covers, and lists of them. "
        "Prefer it over guessing from the numbered passages, which are only a "
        "sample of the base.\n"
        "- Each line is a filename plus the opening words of that document, so it "
        "often states the category directly (e.g. the opening says the person is "
        "a film actor, a director or a singer); that is enough to classify it.\n"
        "- Facts taken from an inventory line must NOT carry a [n] marker: those "
        "markers point at the numbered passages above, and the inventory is not "
        "one of them. If the answer leans mostly on the inventory, say so once, "
        "in the same language as the answer — e.g. \"根据文档清单\" in Chinese — "
        "then answer normally, without inventing contents for a bare filename."
    )
    return head + "\n" + "\n".join(lines) + "\n" + tail


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
    "4. Be concise and well structured.\n"
    "5. Always reply in the same language as the user's question: Chinese "
    "question → Chinese answer (简体中文), English question → English answer. "
    "Keep the [1] citation markers unchanged in any language."
)


def retrieve(user_id: str, question: str, top_k: int | None = None,
             doc_ids: list[str] | None = None, breadth: bool = False) -> list[dict]:
    """Hybrid search over one user's chunks, optionally scoped to `doc_ids`.

    The scope is applied inside both retrievers rather than as a post-filter:
    an embed may only be allowed to expose a subset of the knowledge base, and
    filtering afterwards would leak the rest through idf/rank positions.

    `breadth=True` trades depth for coverage: a wider top-k and at most one
    chunk per document, which is what a "list everyone in here" question needs.
    """
    k = top_k or (settings.list_top_k if breadth else settings.top_k)
    per_doc_cap = 1 if breadth else 3
    rows, matrix = db.all_chunk_vectors(user_id, doc_ids)
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
    fused = _rrf_fuse(scores, bm25.bm25_scores(user_id, question, doc_ids).astype(np.float32))
    order = np.argsort(-fused)[: max(k * 3, k + 24)]

    # light diversity pass: cap chunks per document, then take top-k
    per_doc: dict[str, int] = {}
    chosen = []
    # local lexical vectors score lower than dense embeddings — relax the floor
    threshold = 0.05 if settings.embedding_local else settings.retrieval_threshold
    for i in order:
        row = rows[int(i)]
        if float(scores[int(i)]) < threshold and float(fused[int(i)]) == 0:
            continue
        if per_doc.get(row["doc_id"], 0) >= per_doc_cap:
            continue
        per_doc[row["doc_id"]] = per_doc.get(row["doc_id"], 0) + 1
        chosen.append((float(fused[int(i)]), row))
        if len(chosen) >= k:
            break

    hit_doc_ids = {r["doc_id"] for _, r in chosen}
    docs = {
        d["id"]: d["filename"]
        for d in (
            db.get_conn()
            .execute(
                f"SELECT id, filename FROM documents WHERE id IN ({','.join('?' * len(hit_doc_ids))})",
                tuple(hit_doc_ids),
            )
            .fetchall()
        )
    } if hit_doc_ids else {}

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


def build_messages(question: str, contexts: list[dict], history: list[dict],
                   has_documents: bool, inventory: str = "") -> list[dict]:
    if contexts:
        # a wide retrieval (corpus questions) can be many times the normal
        # payload; keep the best-ranked blocks and stop at the character budget
        blocks, used = [], 0
        for c in contexts:
            block = f"[{c['rank']}] (from {c['doc_name']})\n{c['content']}"
            if blocks and used + len(block) > settings.context_chars:
                break
            blocks.append(block)
            used += len(block) + 2
        context_msg = "Knowledge-base context:\n" + "\n\n".join(blocks)
    elif inventory:
        # a corpus question that matched no passage still has the inventory:
        # telling the model "nothing was retrieved" here would contradict it
        context_msg = (
            "Knowledge-base context: (no passage matched this question, but the "
            "inventory below describes what the knowledge base contains. Answer "
            "from the inventory where it applies, and say plainly that the detail "
            "is not in the retrieved passages.)"
        )
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
    if inventory:
        context_msg += "\n\n" + inventory
    messages = [{"role": "system", "content": SYSTEM_PROMPT + "\n\n" + context_msg}]
    messages.extend(history[-10:])
    messages.append({"role": "user", "content": question})
    return messages


def _search(user_id: str, question: str, doc_ids=None):
    """Plan the retrieval: (breadth, inventory) for this question.

    The marker check runs first so an ordinary question never pays for the
    inventory query; only "有哪些…" wording can reach the extra lookups.
    """
    if not has_enumeration_marker(question):
        return False, ""
    if settings.list_top_k <= 0 or settings.inventory_docs <= 0:
        return False, ""  # escape hatch: behave like the pre-inventory build
    rows, total = db.document_digests(user_id, doc_ids, settings.inventory_docs)
    if not rows:
        return False, ""
    if not is_corpus_question(question, [r["filename"] for r in rows]):
        return False, ""
    return True, _inventory_block(rows, total)


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


def answer_stream(user_id: str, question: str, history: list[dict],
                  doc_ids: list[str] | None = None, fallback: str = "", usage=None):
    """Yield (token, event) tuples. Final event carries the sources list.

    `fallback`, when set by an embed's config, is returned verbatim instead of
    calling the model on an empty retrieval — it keeps the customer's own
    "sorry, not covered" wording and saves a pointless completion.

    `usage`, when given, is filled in place so the caller can meter the answer.
    """
    breadth, inventory = _search(user_id, question, doc_ids)
    contexts = retrieve(user_id, question, doc_ids=doc_ids, breadth=breadth)
    if usage is not None:
        usage.contexts = len(contexts)
    if not contexts and not inventory and fallback:
        if usage is not None:
            usage.status = "fallback"
        yield ("delta", fallback)
        yield ("done", [])
        return
    has_docs = bool(doc_ids) or db.doc_count_for_user(user_id) > 0
    messages = build_messages(question, contexts, history, has_docs, inventory)
    # passed as a kwarg only when someone is metering: a provider-like callable
    # with the original two-argument signature keeps working
    meter = {"usage": usage} if usage is not None else {}
    try:
        for token in providers.chat_stream(messages, contexts, **meter):
            yield ("delta", token)
    except Exception:
        if usage is not None:
            usage.status = "error"
        yield ("delta", "\n\n[Model request failed — check the LLM key / base URL / model settings in .env.]")
    yield ("done", _sources_payload(contexts))


def answer_once(user_id: str, question: str, history: list[dict],
                doc_ids: list[str] | None = None, fallback: str = "",
                usage=None) -> tuple[str, list[dict]]:
    breadth, inventory = _search(user_id, question, doc_ids)
    contexts = retrieve(user_id, question, doc_ids=doc_ids, breadth=breadth)
    if usage is not None:
        usage.contexts = len(contexts)
    if not contexts and not inventory and fallback:
        if usage is not None:
            usage.status = "fallback"
        return fallback, []
    has_docs = bool(doc_ids) or db.doc_count_for_user(user_id) > 0
    messages = build_messages(question, contexts, history, has_docs, inventory)
    meter = {"usage": usage} if usage is not None else {}
    try:
        text = providers.chat_once(messages, contexts, **meter)
    except Exception:
        if usage is not None:
            usage.status = "error"
        text = "[Model request failed — check the LLM key / base URL / model settings in .env.]"
    return text, _sources_payload(contexts)
