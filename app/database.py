"""SQLite storage: users, documents, chunks (with float32 embedding blobs),
conversations and messages. A single-file database keeps deployment trivial;
swap this module for Postgres + pgvector for large deployments.
"""
import json
import os
import sqlite3
import threading
import time
import uuid

import numpy as np

from .config import settings

_local = threading.local()


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    # the database file may have been removed/replaced externally (backup
    # restore, redeploy, data-dir wipe) while the server keeps running
    if conn is not None and not os.path.exists(settings.db_path):
        try:
            conn.close()
        except Exception:
            pass
        conn = None
        _local.conn = None
    if conn is None:
        os.makedirs(settings.data_dir, exist_ok=True)
        conn = sqlite3.connect(settings.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _ensure_schema(conn)
        _local.conn = conn
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ready',
            num_chunks INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chunks (
            id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            idx INTEGER NOT NULL,
            content TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS embeddings (
            chunk_id TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
            vector BLOB NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL DEFAULT 'New conversation',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            sources TEXT,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_user ON chunks(user_id);
        CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
        CREATE TABLE IF NOT EXISTS api_keys (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            prefix TEXT NOT NULL,
            key_hash TEXT UNIQUE NOT NULL,
            label TEXT,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    conn.commit()


def init_db() -> None:
    """Idempotent: open (or recreate) the database and ensure the schema exists."""
    get_conn()


def new_id() -> str:
    return uuid.uuid4().hex


def now() -> float:
    return time.time()


def vec_to_blob(vec) -> bytes:
    arr = np.asarray(vec, dtype=np.float32)
    return arr.tobytes()


def blob_to_vec(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


# ---------------------------------------------------------------- meta
# Small key/value store for schema/format bookkeeping (e.g. the tokenizer
# version that produced the stored demo vectors).

def get_meta(key: str) -> str | None:
    row = get_conn().execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(key: str, value: str) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


# ---------------------------------------------------------------- users

def create_user(email: str, password_hash: str, is_admin: bool) -> dict:
    uid = new_id()
    get_conn().execute(
        "INSERT INTO users (id, email, password_hash, is_admin, created_at) VALUES (?,?,?,?,?)",
        (uid, email, password_hash, int(is_admin), now()),
    )
    get_conn().commit()
    return {"id": uid, "email": email, "is_admin": is_admin}


def get_user_by_email(email: str):
    row = get_conn().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    return dict(row) if row else None


def get_user_by_id(uid: str):
    row = get_conn().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return dict(row) if row else None


def user_count() -> int:
    return get_conn().execute("SELECT COUNT(*) c FROM users").fetchone()["c"]


def list_users():
    rows = get_conn().execute("SELECT id, email, is_admin, created_at FROM users ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]


def update_password(uid: str, password_hash: str) -> bool:
    cur = get_conn().execute(
        "UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, uid)
    )
    get_conn().commit()
    return cur.rowcount > 0


def delete_user(uid: str) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM users WHERE id = ?", (uid,))
    conn.commit()


# ---------------------------------------------------------------- documents

def create_document(user_id: str, filename: str) -> str:
    did = new_id()
    conn = get_conn()
    conn.execute(
        "INSERT INTO documents (id, user_id, filename, created_at) VALUES (?,?,?,?)",
        (did, user_id, filename, now()),
    )
    conn.commit()
    return did


def doc_count_for_user(user_id: str) -> int:
    return get_conn().execute(
        "SELECT COUNT(*) c FROM documents WHERE user_id = ?", (user_id,)
    ).fetchone()["c"]


def list_documents(user_id: str):
    rows = get_conn().execute(
        "SELECT id, filename, status, num_chunks, error, created_at FROM documents WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_document(doc_id: str, user_id: str):
    row = get_conn().execute(
        "SELECT * FROM documents WHERE id = ? AND user_id = ?", (doc_id, user_id)
    ).fetchone()
    return dict(row) if row else None


def finish_document(doc_id: str, num_chunks: int, error: str | None = None) -> None:
    get_conn().execute(
        "UPDATE documents SET status = ?, num_chunks = ?, error = ? WHERE id = ?",
        ("error" if error else "ready", num_chunks, error, doc_id),
    )
    get_conn().commit()


def insert_chunk(doc_id: str, user_id: str, idx: int, content: str, vector) -> None:
    cid = new_id()
    conn = get_conn()
    conn.execute(
        "INSERT INTO chunks (id, doc_id, user_id, idx, content) VALUES (?,?,?,?,?)",
        (cid, doc_id, user_id, idx, content),
    )
    conn.execute("INSERT INTO embeddings (chunk_id, vector) VALUES (?,?)", (cid, vec_to_blob(vector)))
    conn.commit()


def delete_document(doc_id: str, user_id: str) -> bool:
    conn = get_conn()
    cur = conn.execute("DELETE FROM documents WHERE id = ? AND user_id = ?", (doc_id, user_id))
    conn.commit()
    return cur.rowcount > 0


def all_chunk_vectors(user_id: str):
    """Return (chunk_rows, matrix) for cosine search."""
    rows = get_conn().execute(
        "SELECT c.id, c.doc_id, c.idx, c.content, e.vector FROM chunks c "
        "JOIN embeddings e ON e.chunk_id = c.id WHERE c.user_id = ?",
        (user_id,),
    ).fetchall()
    if not rows:
        return [], np.zeros((0, settings.embedding_dim), dtype=np.float32)
    vecs = np.stack([blob_to_vec(r["vector"]) for r in rows]).astype(np.float32)
    return [dict(r) for r in rows], vecs


# ---------------------------------------------------------------- api keys

import hashlib
import secrets as _secrets


def create_api_key(user_id: str, label: str = "") -> tuple[dict, str]:
    """Returns (record, full_key). The full key is shown only once."""
    full = "rbk_" + _secrets.token_hex(24)
    record = {
        "id": new_id(),
        "user_id": user_id,
        "prefix": full[:10],
        "key_hash": hashlib.sha256(full.encode()).hexdigest(),
        "label": label[:80],
        "created_at": now(),
    }
    conn = get_conn()
    conn.execute(
        "INSERT INTO api_keys (id, user_id, prefix, key_hash, label, created_at) VALUES (?,?,?,?,?,?)",
        (record["id"], user_id, record["prefix"], record["key_hash"], record["label"], record["created_at"]),
    )
    conn.commit()
    return record, full


def list_api_keys(user_id: str):
    rows = get_conn().execute(
        "SELECT id, prefix, label, created_at FROM api_keys WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def revoke_api_key(key_id: str, user_id: str) -> bool:
    conn = get_conn()
    cur = conn.execute("DELETE FROM api_keys WHERE id = ? AND user_id = ?", (key_id, user_id))
    conn.commit()
    return cur.rowcount > 0


def get_user_by_api_key(full_key: str):
    row = get_conn().execute(
        "SELECT u.* FROM api_keys k JOIN users u ON u.id = k.user_id WHERE k.key_hash = ?",
        (hashlib.sha256(full_key.encode()).hexdigest(),),
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------- conversations

def create_conversation(user_id: str, title: str = "New conversation") -> str:
    cid = new_id()
    conn = get_conn()
    conn.execute(
        "INSERT INTO conversations (id, user_id, title, created_at, updated_at) VALUES (?,?,?,?,?)",
        (cid, user_id, title, now(), now()),
    )
    conn.commit()
    return cid


def list_conversations(user_id: str):
    rows = get_conn().execute(
        "SELECT id, title, created_at, updated_at FROM conversations WHERE user_id = ? ORDER BY updated_at DESC",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_conversation(cid: str, user_id: str):
    row = get_conn().execute(
        "SELECT * FROM conversations WHERE id = ? AND user_id = ?", (cid, user_id)
    ).fetchone()
    return dict(row) if row else None


def touch_conversation(cid: str, title: str | None = None) -> None:
    if title:
        get_conn().execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?", (title, now(), cid)
        )
    else:
        get_conn().execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now(), cid))
    get_conn().commit()


def delete_conversation(cid: str, user_id: str) -> bool:
    conn = get_conn()
    cur = conn.execute("DELETE FROM conversations WHERE id = ? AND user_id = ?", (cid, user_id))
    conn.commit()
    return cur.rowcount > 0


def add_message(conversation_id: str, role: str, content: str, sources=None) -> None:
    get_conn().execute(
        "INSERT INTO messages (id, conversation_id, role, content, sources, created_at) VALUES (?,?,?,?,?,?)",
        (new_id(), conversation_id, role, content, json.dumps(sources) if sources else None, now()),
    )
    get_conn().commit()


def get_messages(cid: str):
    rows = get_conn().execute(
        "SELECT role, content, sources, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at",
        (cid,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["sources"] = json.loads(d["sources"]) if d["sources"] else []
        out.append(d)
    return out


# admin stats
def stats():
    conn = get_conn()
    def c(sql):
        return conn.execute(sql).fetchone()["c"]
    return {
        "users": c("SELECT COUNT(*) c FROM users"),
        "documents": c("SELECT COUNT(*) c FROM documents"),
        "chunks": c("SELECT COUNT(*) c FROM chunks"),
        "conversations": c("SELECT COUNT(*) c FROM conversations"),
    }
