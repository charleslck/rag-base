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
            created_at REAL NOT NULL,
            folder_id TEXT
        );
        -- Folders are a tree per user (parent_id). Deleting one puts its
        -- documents back at the root instead of taking them with it, so a
        -- mis-click never loses anything; folder_id is deliberately not a
        -- foreign key, because the cascade would have to mean the opposite.
        CREATE TABLE IF NOT EXISTS folders (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            parent_id TEXT,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_folders_user ON folders(user_id);
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
            config TEXT NOT NULL DEFAULT '{}',
            allowed_origins TEXT NOT NULL DEFAULT '',
            plan TEXT NOT NULL DEFAULT 'free',
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the first release (SQLite has no IF NOT EXISTS)."""
    have = {r["name"] for r in conn.execute("PRAGMA table_info(api_keys)")}
    for column, ddl in (
        ("config", "TEXT NOT NULL DEFAULT '{}'"),
        ("allowed_origins", "TEXT NOT NULL DEFAULT ''"),
        ("plan", "TEXT NOT NULL DEFAULT 'free'"),
    ):
        if column not in have:
            conn.execute(f"ALTER TABLE api_keys ADD COLUMN {column} {ddl}")

    # documents.folder_id arrived with the file library's folders
    docs = {r["name"] for r in conn.execute("PRAGMA table_info(documents)")}
    if "folder_id" not in docs:
        conn.execute("ALTER TABLE documents ADD COLUMN folder_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_docs_folder ON documents(folder_id)")


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

def create_document(user_id: str, filename: str, folder_id: str | None = None) -> str:
    did = new_id()
    conn = get_conn()
    conn.execute(
        "INSERT INTO documents (id, user_id, filename, created_at, folder_id) VALUES (?,?,?,?,?)",
        (did, user_id, filename, now(), folder_id or None),
    )
    conn.commit()
    return did


def doc_count_for_user(user_id: str) -> int:
    return get_conn().execute(
        "SELECT COUNT(*) c FROM documents WHERE user_id = ?", (user_id,)
    ).fetchone()["c"]


def list_documents(user_id: str):
    rows = get_conn().execute(
        "SELECT id, filename, status, num_chunks, error, created_at, folder_id "
        "FROM documents WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_document(doc_id: str, user_id: str):
    row = get_conn().execute(
        "SELECT * FROM documents WHERE id = ? AND user_id = ?", (doc_id, user_id)
    ).fetchone()
    return dict(row) if row else None


def document_digests(user_id: str, doc_ids=None, limit: int = 200):
    """(filename, opening text) for each ready document, oldest filename first.

    Used to give the model a one-line-per-document index of the whole knowledge
    base: filename alone is not enough to answer "which singers are in here",
    while the first few dozen characters of the first chunk usually name the
    category. Returns (rows, total) where total counts past `limit`.
    """
    where = " FROM documents d WHERE d.user_id = ? AND d.status = 'ready'"
    params: list = [user_id]
    if doc_ids:
        where += f" AND d.id IN ({','.join('?' * len(doc_ids))})"
        params.extend(doc_ids)
    # count first, without the per-row chunk lookup: wrapping the full select
    # in COUNT(*) would make SQLite open a chunk scan for every document
    total = get_conn().execute("SELECT COUNT(*) c" + where, params).fetchone()["c"]
    sql = (
        "SELECT d.filename AS filename, "
        "(SELECT c.content FROM chunks c WHERE c.doc_id = d.id ORDER BY c.idx LIMIT 1) AS head"
        + where
        + " ORDER BY d.filename LIMIT ?"
    )
    rows = get_conn().execute(sql, params + [limit]).fetchall()
    return [dict(r) for r in rows], int(total)


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


def delete_documents(doc_ids, user_id: str) -> int:
    """Delete several documents in one transaction, returns how many went.

    Chunks and embeddings follow through the foreign-key cascade, so a hundred
    deletes cost one statement instead of a hundred connections and commits.
    Ids the caller does not own simply do not match — no error, no leak.
    """
    ids = [d for d in dict.fromkeys(doc_ids) if d]
    if not ids:
        return 0
    conn = get_conn()
    cur = conn.execute(
        f"DELETE FROM documents WHERE user_id = ? AND id IN ({','.join('?' * len(ids))})",
        [user_id, *ids],
    )
    conn.commit()
    return cur.rowcount


# ---------------------------------------------------------------- folders
# A per-user tree. Documents point at a folder with a plain column, so a
# document can be moved with one UPDATE and nothing else has to stay in sync.

def list_folders(user_id: str):
    """Every folder of the user, plus how many documents sit directly inside."""
    rows = get_conn().execute(
        "SELECT f.id, f.name, f.parent_id, f.created_at, "
        "(SELECT COUNT(*) FROM documents d WHERE d.folder_id = f.id) AS docs "
        "FROM folders f WHERE f.user_id = ? ORDER BY f.created_at",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_folder(folder_id: str, user_id: str):
    row = get_conn().execute(
        "SELECT * FROM folders WHERE id = ? AND user_id = ?", (folder_id, user_id)
    ).fetchone()
    return dict(row) if row else None


def create_folder(user_id: str, name: str, parent_id: str | None = None):
    conn = get_conn()
    if parent_id and not conn.execute(
        "SELECT 1 FROM folders WHERE id = ? AND user_id = ?", (parent_id, user_id)
    ).fetchone():
        return None  # a parent id that is not yours is a 404, not a new root
    fid = new_id()
    conn.execute(
        "INSERT INTO folders (id, user_id, name, parent_id, created_at) VALUES (?,?,?,?,?)",
        (fid, user_id, name, parent_id or None, now()),
    )
    conn.commit()
    return {"id": fid, "name": name, "parent_id": parent_id or None, "docs": 0}


def rename_folder(folder_id: str, user_id: str, name: str) -> bool:
    conn = get_conn()
    cur = conn.execute(
        "UPDATE folders SET name = ? WHERE id = ? AND user_id = ?", (name, folder_id, user_id)
    )
    conn.commit()
    return cur.rowcount > 0


def _folder_subtree(conn: sqlite3.Connection, folder_id: str, user_id: str) -> list[str]:
    rows = conn.execute(
        "WITH RECURSIVE sub(id) AS ("
        "  SELECT id FROM folders WHERE id = ? AND user_id = ?"
        "  UNION ALL"
        "  SELECT f.id FROM folders f JOIN sub ON f.parent_id = sub.id"
        "  WHERE f.user_id = ?"
        ") SELECT id FROM sub",
        (folder_id, user_id, user_id),
    ).fetchall()
    return [r["id"] for r in rows]


def delete_folder(folder_id: str, user_id: str):
    """Drop a folder and everything under it; documents go back to the root.

    Deleting the folders' documents instead would make one mis-click able to
    destroy a hundred files — and there is a batch delete for that already.
    """
    conn = get_conn()
    ids = _folder_subtree(conn, folder_id, user_id)
    if not ids:
        return None
    marks = ",".join("?" * len(ids))
    moved = conn.execute(
        f"UPDATE documents SET folder_id = NULL WHERE user_id = ? AND folder_id IN ({marks})",
        [user_id, *ids],
    ).rowcount
    conn.execute(
        f"DELETE FROM folders WHERE user_id = ? AND id IN ({marks})", [user_id, *ids]
    )
    conn.commit()
    return {"folders": len(ids), "documents": moved}


def move_documents(doc_ids, user_id: str, folder_id: str | None = None) -> int:
    """Put documents in a folder (or back at the root when folder_id is None)."""
    ids = [d for d in dict.fromkeys(doc_ids) if d]
    if not ids:
        return 0
    conn = get_conn()
    if folder_id and not conn.execute(
        "SELECT 1 FROM folders WHERE id = ? AND user_id = ?", (folder_id, user_id)
    ).fetchone():
        return 0
    cur = conn.execute(
        f"UPDATE documents SET folder_id = ? WHERE user_id = ? AND id IN ({','.join('?' * len(ids))})",
        [folder_id or None, user_id, *ids],
    )
    conn.commit()
    return cur.rowcount


def all_chunk_vectors(user_id: str, doc_ids=None):
    """Return (chunk_rows, matrix) for cosine search.

    The ORDER BY matters: the BM25 index is built in the same order, and RRF
    fuses the two score vectors positionally — a different row order here would
    silently pair each chunk's dense score with another chunk's lexical score.
    """
    sql = (
        "SELECT c.id, c.doc_id, c.idx, c.content, e.vector FROM chunks c "
        "JOIN embeddings e ON e.chunk_id = c.id WHERE c.user_id = ?"
    )
    params: list = [user_id]
    if doc_ids:
        sql += f" AND c.doc_id IN ({','.join('?' * len(doc_ids))})"
        params.extend(doc_ids)
    sql += " ORDER BY c.doc_id, c.idx"
    rows = get_conn().execute(sql, params).fetchall()
    if not rows:
        return [], np.zeros((0, settings.embedding_dim), dtype=np.float32)
    vecs = np.stack([blob_to_vec(r["vector"]) for r in rows]).astype(np.float32)
    return [dict(r) for r in rows], vecs


# ---------------------------------------------------------------- api keys

import hashlib
import secrets as _secrets


def create_api_key(user_id: str, label: str = "", config=None, plan: str = "free",
                   allowed_origins: str = "") -> tuple[dict, str]:
    """Returns (record, full_key). The full key is shown only once."""
    full = "rbk_" + _secrets.token_hex(24)
    record = {
        "id": new_id(),
        "user_id": user_id,
        "prefix": full[:10],
        "key_hash": hashlib.sha256(full.encode()).hexdigest(),
        "label": label[:80],
        "config": json.dumps(config or {}),
        "allowed_origins": allowed_origins or "",
        "plan": plan if plan in ("free", "pro") else "free",
        "created_at": now(),
    }
    conn = get_conn()
    conn.execute(
        "INSERT INTO api_keys (id, user_id, prefix, key_hash, label, config, allowed_origins, plan, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (record["id"], user_id, record["prefix"], record["key_hash"], record["label"],
         record["config"], record["allowed_origins"], record["plan"], record["created_at"]),
    )
    conn.commit()
    record["config"] = json.loads(record["config"])
    return record, full


def _key_dict(row) -> dict:
    d = dict(row)
    try:
        d["config"] = json.loads(d.get("config") or "{}")
    except (TypeError, ValueError):
        d["config"] = {}
    return d


def list_api_keys(user_id: str):
    rows = get_conn().execute(
        "SELECT id, prefix, label, config, allowed_origins, plan, created_at "
        "FROM api_keys WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    return [_key_dict(r) for r in rows]


def get_api_key(key_id: str, user_id: str):
    row = get_conn().execute(
        "SELECT id, prefix, label, config, allowed_origins, plan, created_at "
        "FROM api_keys WHERE id = ? AND user_id = ?",
        (key_id, user_id),
    ).fetchone()
    return _key_dict(row) if row else None


def update_api_key(key_id: str, user_id: str, fields: dict) -> bool:
    """Patch a key. Only the fields an integrator may own are writable."""
    allowed = ("label", "config", "allowed_origins", "plan")
    sets, values = [], []
    for name in allowed:
        if name not in fields:
            continue
        value = fields[name]
        if name == "config":
            value = json.dumps(value if isinstance(value, dict) else {})
        sets.append(f"{name} = ?")
        values.append(value)
    if not sets:
        return False
    values.extend([key_id, user_id])
    conn = get_conn()
    cur = conn.execute(f"UPDATE api_keys SET {', '.join(sets)} WHERE id = ? AND user_id = ?", values)
    conn.commit()
    return cur.rowcount > 0


def revoke_api_key(key_id: str, user_id: str) -> bool:
    conn = get_conn()
    cur = conn.execute("DELETE FROM api_keys WHERE id = ? AND user_id = ?", (key_id, user_id))
    conn.commit()
    return cur.rowcount > 0


def set_key_plan(key_id: str, plan: str) -> bool:
    """Admin-only: white-label is a paid capability, so the plan is not
    something the key owner can grant themselves."""
    conn = get_conn()
    cur = conn.execute("UPDATE api_keys SET plan = ? WHERE id = ?", (plan, key_id))
    conn.commit()
    return cur.rowcount > 0


def get_api_key_record(full_key: str):
    """The key row plus its owner — everything a widget request needs to decide."""
    row = get_conn().execute(
        "SELECT k.id AS id, k.prefix, k.label, k.config, k.allowed_origins, k.plan, "
        "u.id AS user_id, u.email, u.is_admin "
        "FROM api_keys k JOIN users u ON u.id = k.user_id WHERE k.key_hash = ?",
        (hashlib.sha256(full_key.encode()).hexdigest(),),
    ).fetchone()
    return _key_dict(row) if row else None


def get_user_by_api_key(full_key: str):
    record = get_api_key_record(full_key)
    if not record:
        return None
    return {
        "id": record["user_id"],
        "email": record["email"],
        "is_admin": record["is_admin"],
    }


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
