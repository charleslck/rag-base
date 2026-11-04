"""Model providers.

Two interchangeable providers behind one small interface:

- OpenAIProvider: real LLM answers + text-embedding-3-small embeddings via the
  OpenAI SDK (works with any OpenAI-compatible endpoint: OpenAI, Azure,
  Groq, Together, local vLLM/Ollama with an OpenAI-compatible server...).
- DemoProvider: used automatically when OPENAI_API_KEY is not set. Embeddings
  are deterministic feature-hash vectors (lexical overlap retrieval) and
  answers are extractive, so buyers can try the whole product flow offline.
"""
import hashlib
import re
import threading
import time

import numpy as np

from .config import settings

_stopwords = set(
    "the a an and or but if then of at by for with about into through during before after "
    "above below to from up down in out on off over under again further once here there when "
    "where why how all any both each few more most other some such no nor not only own same "
    "so than too very can will just should now i you he she it we they what which who whom "
    "this that these those am is are was were be been being have has had having do does did "
    "doing would could ought".split()
)


_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")

# Bump whenever _tokens changes its output: stored demo vectors are keyed by
# token hash, so they must be rebuilt after a tokenizer change.
TOKENIZER_VERSION = 2


def has_cjk(text: str) -> bool:
    """True when the text contains at least one Chinese character."""
    return bool(_CJK_CHAR_RE.search(text or ""))


def _cjk_tokens(run: str) -> list[str]:
    """Character bigrams for a CJK run ("退款政策" -> 退款/款政/政策).

    Chinese has no spaces, so word-level splitting is unavailable without a
    dictionary. Unigrams lose all precision (any 款 matches), whereas bigrams
    carry most of the signal: 退款 no longer collides with 汇款 or 条款.
    Single-character runs stay as-is so "税" still works.
    """
    if len(run) == 1:
        return [run]
    return [run[i : i + 2] for i in range(len(run) - 1)]


def _tokens(text: str) -> list[str]:
    """Latin words (minus stopwords) + CJK bigrams — works for EN and ZH text."""
    out = []
    for m in _CJK_RE.finditer(text):
        out.extend(_cjk_tokens(m.group(0)))
    for t in re.findall(r"[a-z0-9]+", text.lower()):
        if t not in _stopwords and len(t) > 1:
            out.append(t)
    return out


class DemoEmbedding:
    """Feature-hashing bag-of-words vectors: L2-normalized, deterministic."""

    def __init__(self, dim: int = 512):
        self.dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            for tok in _tokens(text):
                h = int.from_bytes(hashlib.md5(tok.encode()).digest()[:4], "big")
                out[i, h % self.dim] += 1.0
        out = np.sqrt(out)  # sqrt(tf) damps repeated-word dominance
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return out / norms


class DemoChat:
    """Extractive answers from retrieved chunks — offline demo engine.

    All user-facing strings are bilingual so a Chinese deployment never falls
    back to English boilerplate.
    """

    @staticmethod
    def _stemmed(text: str) -> set[str]:
        # light stemming: drop trailing 's' so plan/plans, refund/refunds match
        return {t[:-1] if t.endswith("s") and len(t) > 3 else t for t in _tokens(text)}

    def answer(self, question: str, contexts: list[dict]) -> str:
        zh = has_cjk(question)
        qtoks = self._stemmed(question)
        if not qtoks:
            return "请就知识库中的文档提问。" if zh else "Please ask a question about the documents in your knowledge base."
        best: list[tuple[float, str]] = []
        for ctx in contexts:
            body = ctx["content"].replace("\n", " ")
            # CJK sentences often end without a following space, so split on the
            # terminator itself rather than requiring whitespace after it
            sentences = re.split(r"(?<=[。！？；])|(?<=[.!?])\s+", body)
            for sent in sentences:
                clean = re.sub(r"[#>*`]", "", sent).strip()
                # Chinese carries more meaning per character: 12 chars is already
                # a full sentence, while 25 latin chars is roughly the equivalent
                if len(clean) < (12 if has_cjk(clean) else 25):
                    continue
                stoks = self._stemmed(clean)
                if not stoks:
                    continue
                overlap = len(qtoks & stoks) / max(1, len(qtoks))
                if overlap >= 0.15:
                    best.append((overlap, clean))
        best.sort(key=lambda x: -x[0])
        picked, seen = [], set()
        for _, sent in best:
            key = sent[:60]
            if key in seen:
                continue
            seen.add(key)
            picked.append(sent)
            if len(picked) >= 3:
                break
        if not picked:
            return (
                "在所上传的文档中找不到与该问题相关的内容。可以换个说法提问，或先把相关文档加入知识库。"
                if zh
                else "I could not find anything relevant to that in the uploaded documents. "
                "Try rephrasing, or add documents that cover this topic to the knowledge base."
            )
        head = "根据你的知识库：" if zh else "Based on your knowledge base:"
        return head + "\n\n" + "\n\n".join(f"• {s}" for s in picked)

    def stream(self, text: str):
        """Yield small slices so Chinese text streams visibly (no spaces to split on)."""
        i, n = 0, len(text)
        while i < n:
            ch = text[i]
            if _CJK_CHAR_RE.match(ch):
                j = i
                while j < n and _CJK_CHAR_RE.match(text[j]):
                    j += 1
                run = text[i:j]
                for k in range(0, len(run), 3):
                    yield run[k : k + 3]
                    time.sleep(0.012)
                i = j
            elif ch.isalnum():
                j = i
                while j < n and (text[j].isalnum() or text[j] in "_-"):
                    j += 1
                yield text[i:j]
                time.sleep(0.012)
                i = j
            else:
                j = i
                while j < n and not text[j].isalnum() and not _CJK_CHAR_RE.match(text[j]):
                    j += 1
                yield text[i:j]
                i = j


_openai_client = None
_chat_client = None
_client_lock = threading.Lock()


def _get_embed_client():
    global _openai_client
    with _client_lock:
        if _openai_client is None:
            from openai import OpenAI

            _openai_client = OpenAI(api_key=settings.embedding_api_key, base_url=settings.embedding_base_url)
    return _openai_client


def _get_chat_client():
    global _chat_client
    with _client_lock:
        if _chat_client is None:
            from openai import OpenAI

            _chat_client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)
    return _chat_client


def embed_texts(texts: list[str]) -> np.ndarray:
    if settings.embedding_local:
        return DemoEmbedding().embed(texts)
    client = _get_embed_client()
    resp = client.embeddings.create(model=settings.embedding_model, input=texts)
    vecs = np.array([d.embedding for d in resp.data], dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vecs / norms


def ensure_vector_compatibility() -> int:
    """Rebuild locally-hashed vectors when the tokenizer changed.

    Real embedding APIs are version-independent (the provider owns the model),
    but demo vectors hash our own tokens, so a tokenizer change silently
    invalidates every stored vector. Runs once per version and is a no-op for
    an empty knowledge base.

    Returns the number of chunks re-embedded.
    """
    if not settings.embedding_local:
        return 0
    from . import database as db  # local import: avoids an import cycle

    version = str(TOKENIZER_VERSION)
    if db.get_meta("tokenizer_version") == version:
        return 0

    conn = db.get_conn()
    rows = conn.execute("SELECT id, content FROM chunks ORDER BY id").fetchall()
    total = 0
    batch = 200
    for start in range(0, len(rows), batch):
        window = rows[start : start + batch]
        vecs = DemoEmbedding().embed([r["content"] for r in window])
        conn.executemany(
            "INSERT INTO embeddings (chunk_id, vector) VALUES (?,?) "
            "ON CONFLICT(chunk_id) DO UPDATE SET vector = excluded.vector",
            [(r["id"], db.vec_to_blob(v)) for r, v in zip(window, vecs)],
        )
        conn.commit()
        total += len(window)
    db.set_meta("tokenizer_version", version)
    return total


def chat_once(messages: list[dict], contexts: list[dict] | None = None) -> str:
    if settings.chat_demo:
        q = messages[-1]["content"] if messages else ""
        return DemoChat().answer(q, contexts or [])
    client = _get_chat_client()
    resp = client.chat.completions.create(model=settings.llm_model, messages=messages, temperature=0.2)
    return resp.choices[0].message.content or ""


def chat_stream(messages: list[dict], contexts: list[dict] | None = None):
    if settings.chat_demo:
        q = messages[-1]["content"] if messages else ""
        yield from DemoChat().stream(DemoChat().answer(q, contexts or []))
        return
    client = _get_chat_client()
    stream = client.chat.completions.create(
        model=settings.llm_model, messages=messages, temperature=0.2, stream=True
    )
    for event in stream:
        delta = event.choices[0].delta.content if event.choices else None
        if delta:
            yield delta
