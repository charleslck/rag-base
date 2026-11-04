"""RAGbase — FastAPI application: auth, document management, RAG chat (SSE)."""
import json

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, database as db, ingest, providers, quota, rag, widget_config
from .config import settings

app = FastAPI(title="RAGbase", version="1.0.0")


def _cors_headers(origin: str) -> dict:
    """Echo the caller's origin. Widget traffic is allow-listed per key, so a
    wildcard would hand any site the ability to spend a customer's quota."""
    return {
        "Access-Control-Allow-Origin": origin or "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, X-API-Key",
        "Access-Control-Max-Age": "86400",
        "Vary": "Origin",
    }


def _key_from_request(request: Request) -> str:
    """Widget calls carry the key in a header (API) or the query (page loads)."""
    return request.headers.get("x-api-key") or request.query_params.get("key", "")


@app.middleware("http")
async def widget_cors(request: Request, call_next):
    """Allow approved sites to call /api/widget/* and refuse everyone else."""
    if request.url.path.startswith("/api/widget"):
        origin = request.headers.get("origin", "")
        record = db.get_api_key_record(_key_from_request(request))
        allowed = widget_config.parse_origins(record["allowed_origins"]) if record else []
        # server-to-server callers send no Origin and are trusted by holding the
        # key; only browser requests are subject to the allow-list
        if origin and not widget_config.origin_allowed(allowed, origin):
            return Response(
                status_code=403,
                content='{"detail":"This origin is not allowed for the supplied API key"}',
                media_type="application/json",
            )
        if request.method == "OPTIONS":
            return Response(status_code=200, headers=_cors_headers(origin))
        response = await call_next(request)
        for name, value in _cors_headers(origin).items():
            response.headers[name] = value
        return response
    if request.url.path == "/" or request.url.path.startswith("/static"):
        # static assets must never be cached across deploys: the HTML and JS
        # are versioned together, and a stale cached script breaks the page
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-cache"
        return response
    return await call_next(request)


@app.on_event("startup")
def startup():
    db.init_db()
    # local (demo) vectors are hashed from our own tokens: rebuild them once
    # after a tokenizer upgrade so existing knowledge bases stay searchable
    rebuilt = providers.ensure_vector_compatibility()
    if rebuilt:
        print(f"[ragbase] re-embedded {rebuilt} chunks for tokenizer v{providers.TOKENIZER_VERSION}")


@app.get("/api/health")
def health():
    """Liveness probe for Docker/load balancers — must stay dependency-free."""
    return {"status": "ok", "version": app.version}


# ---------------------------------------------------------------- auth

class RegisterIn(BaseModel):
    email: str
    password: str = Field(min_length=6, max_length=128)


@app.post("/api/auth/register")
def register(body: RegisterIn):
    email = body.email.strip().lower()
    if not auth.EMAIL_RE.match(email):
        raise HTTPException(400, "Invalid email address")
    first_user = db.user_count() == 0
    if not first_user and not settings.allow_registration:
        raise HTTPException(403, "Registration is disabled. Ask an admin to create your account.")
    if db.get_user_by_email(email):
        raise HTTPException(409, "An account with this email already exists")
    user = db.create_user(email, auth.hash_password(body.password), is_admin=first_user)
    return {
        "token": auth.make_token(user["id"], user["is_admin"]),
        "user": {"email": email, "is_admin": bool(user["is_admin"])},
    }


@app.post("/api/auth/login")
def login(body: RegisterIn):
    user = db.get_user_by_email(body.email.strip().lower())
    if not user or not auth.verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Invalid email or password")
    return {
        "token": auth.make_token(user["id"], user["is_admin"]),
        "user": {"email": user["email"], "is_admin": bool(user["is_admin"])},
    }


@app.get("/api/me")
def me(user: dict = Depends(auth.current_user)):
    return {
        "email": user["email"],
        "is_admin": bool(user["is_admin"]),
        "demo_mode": settings.chat_demo,
        "embedding_local": settings.embedding_local,
        "model": "demo-extractive" if settings.chat_demo else settings.llm_model,
        "max_docs": settings.max_docs_per_user,
    }


# ---------------------------------------------------------------- status

@app.get("/api/status")
def status():
    return {
        "demo_mode": settings.chat_demo,
        "embedding_local": settings.embedding_local,
        "model": "demo-extractive" if settings.chat_demo else settings.llm_model,
    }


# ---------------------------------------------------------------- documents

@app.get("/api/documents")
def documents(user: dict = Depends(auth.current_user)):
    return {"documents": db.list_documents(user["id"])}


@app.post("/api/documents")
async def upload_documents(
    files: list[UploadFile] = File(...),
    folder_id: str = Form(""),
    user: dict = Depends(auth.current_user),
):
    if settings.max_docs_per_user and db.doc_count_for_user(user["id"]) + len(files) > settings.max_docs_per_user:
        raise HTTPException(400, f"Document limit reached ({settings.max_docs_per_user} per user).")
    # uploads land in whatever folder is open, so validate before writing any of them
    target = folder_id or None
    if target and not db.get_folder(target, user["id"]):
        raise HTTPException(404, "Folder not found")
    results = []
    import numpy as np

    for f in files:
        raw = await f.read()
        if len(raw) > settings.max_upload_mb * 1024 * 1024:
            results.append({"filename": f.filename, "ok": False, "error": f"File too large (max {settings.max_upload_mb} MB)"})
            continue
        doc_id = db.create_document(user["id"], f.filename or "unnamed", target)
        try:
            text = ingest.extract_text(f.filename or "", raw)
            chunks = ingest.chunk_text(text)
            if not chunks:
                raise ingest.ParseError("No readable text found in this file.")
            from . import providers

            vectors = providers.embed_texts(chunks)
            for idx, (chunk, vec) in enumerate(zip(chunks, vectors)):
                db.insert_chunk(doc_id, user["id"], idx, chunk, vec.astype(np.float32))
            db.finish_document(doc_id, len(chunks))
            results.append({"filename": f.filename, "ok": True, "chunks": len(chunks), "id": doc_id})
        except ingest.ParseError as exc:
            db.finish_document(doc_id, 0, str(exc))
            results.append({"filename": f.filename, "ok": False, "error": str(exc)})
    return {"results": results}


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: str, user: dict = Depends(auth.current_user)):
    if not db.delete_document(doc_id, user["id"]):
        raise HTTPException(404, "Document not found")
    return {"ok": True}


class BulkDeleteIn(BaseModel):
    ids: list[str] = Field(default_factory=list)


@app.post("/api/documents/delete")
def delete_documents(body: BulkDeleteIn, user: dict = Depends(auth.current_user)):
    """Batch delete for the file library.

    POST rather than DELETE-with-body: proxies and some HTTP clients drop the
    payload of a DELETE request, and a silently empty body would look like a
    successful no-op.
    """
    ids = [d for d in dict.fromkeys(body.ids) if d]
    if not ids:
        raise HTTPException(400, "No document ids given")
    # SQLite caps host parameters per statement; the library never selects more
    # than a screenful anyway, so a hard ceiling keeps a crafted body harmless.
    if len(ids) > 500:
        raise HTTPException(400, "Too many documents in one request (max 500)")
    return {"deleted": db.delete_documents(ids, user["id"]), "requested": len(ids)}


class MoveIn(BaseModel):
    ids: list[str] = Field(default_factory=list)
    folder_id: str | None = None  # None moves back to the root


@app.post("/api/documents/move")
def move_documents(body: MoveIn, user: dict = Depends(auth.current_user)):
    ids = [d for d in dict.fromkeys(body.ids) if d]
    if not ids:
        raise HTTPException(400, "No document ids given")
    if len(ids) > 500:
        raise HTTPException(400, "Too many documents in one request (max 500)")
    if body.folder_id and not db.get_folder(body.folder_id, user["id"]):
        raise HTTPException(404, "Folder not found")
    return {"moved": db.move_documents(ids, user["id"], body.folder_id)}


# ---------------------------------------------------------------- folders

class FolderIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent_id: str | None = None


class FolderRename(BaseModel):
    name: str = Field(min_length=1, max_length=120)


@app.get("/api/folders")
def folders(user: dict = Depends(auth.current_user)):
    return {"folders": db.list_folders(user["id"])}


@app.post("/api/folders")
def create_folder(body: FolderIn, user: dict = Depends(auth.current_user)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Folder name is empty")
    folder = db.create_folder(user["id"], name, body.parent_id)
    if folder is None:
        raise HTTPException(404, "Parent folder not found")
    return folder


@app.patch("/api/folders/{folder_id}")
def rename_folder(folder_id: str, body: FolderRename, user: dict = Depends(auth.current_user)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Folder name is empty")
    if not db.rename_folder(folder_id, user["id"], name):
        raise HTTPException(404, "Folder not found")
    return {"ok": True}


@app.delete("/api/folders/{folder_id}")
def delete_folder(folder_id: str, user: dict = Depends(auth.current_user)):
    """Remove the folder and its subfolders; the documents go back to the root."""
    result = db.delete_folder(folder_id, user["id"])
    if result is None:
        raise HTTPException(404, "Folder not found")
    return result


# ---------------------------------------------------------------- conversations

@app.get("/api/conversations")
def conversations(user: dict = Depends(auth.current_user)):
    return {"conversations": db.list_conversations(user["id"])}


class NewConvIn(BaseModel):
    title: str = "New conversation"


@app.post("/api/conversations")
def new_conversation(body: NewConvIn, user: dict = Depends(auth.current_user)):
    cid = db.create_conversation(user["id"], body.title[:120])
    return {"id": cid}


@app.get("/api/conversations/{cid}/messages")
def conversation_messages(cid: str, user: dict = Depends(auth.current_user)):
    if not db.get_conversation(cid, user["id"]):
        raise HTTPException(404, "Conversation not found")
    return {"messages": db.get_messages(cid)}


@app.delete("/api/conversations/{cid}")
def delete_conversation(cid: str, user: dict = Depends(auth.current_user)):
    if not db.delete_conversation(cid, user["id"]):
        raise HTTPException(404, "Conversation not found")
    return {"ok": True}


# ---------------------------------------------------------------- chat (SSE)

class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None


@app.post("/api/chat")
def chat(body: ChatIn, user: dict = Depends(auth.current_user)):
    question = body.message.strip()
    if not question:
        raise HTTPException(400, "Empty message")

    if body.conversation_id:
        conv = db.get_conversation(body.conversation_id, user["id"])
        if not conv:
            raise HTTPException(404, "Conversation not found")
        cid = body.conversation_id
    else:
        cid = db.create_conversation(user["id"], question[:60])

    history = [
        {"role": m["role"], "content": m["content"]}
        for m in db.get_messages(cid)
        if m["role"] in ("user", "assistant")
    ][-20:]

    db.add_message(cid, "user", question)

    def stream():
        parts, sources = [], []
        try:
            for event, payload in rag.answer_stream(user["id"], question, history):
                if event == "delta":
                    parts.append(payload)
                    yield f"data: {json.dumps({'delta': payload})}\n\n"
                else:
                    sources = payload
        finally:
            full = "".join(parts).strip()
            db.add_message(cid, "assistant", full, sources=sources or None)
            if db.get_conversation(cid, user["id"])["title"] == "New conversation":
                db.touch_conversation(cid, question[:60])
        yield f"data: {json.dumps({'done': True, 'conversation_id': cid, 'sources': sources})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


# ---------------------------------------------------------------- API keys & widget integration

class ApiKeyIn(BaseModel):
    label: str = ""


class ApiKeyPatchIn(BaseModel):
    label: str | None = None
    config: dict | None = None
    allowed_origins: list[str] | str | None = None


def _decoded_key(key: dict) -> dict:
    """Present a key row the way the dashboard edits it."""
    key["config"] = widget_config.normalize_config(key["config"], key["plan"])
    key["allowed_origins"] = widget_config.parse_origins(key["allowed_origins"])
    return key


@app.post("/api/api-keys")
def create_key(body: ApiKeyIn, user: dict = Depends(auth.current_user)):
    record, full = db.create_api_key(user["id"], body.label)
    return {"key": full, "id": record["id"], "prefix": record["prefix"]}


@app.get("/api/api-keys")
def list_keys(user: dict = Depends(auth.current_user)):
    return {"keys": [_decoded_key(k) for k in db.list_api_keys(user["id"])]}


@app.patch("/api/api-keys/{key_id}")
def patch_key(key_id: str, body: ApiKeyPatchIn, user: dict = Depends(auth.current_user)):
    """Edit branding, behaviour, scope and the origin allow-list of one key."""
    existing = db.get_api_key(key_id, user["id"])
    if not existing:
        raise HTTPException(404, "API key not found")

    fields: dict = {}
    if body.label is not None:
        fields["label"] = body.label.strip()[:80]
    if body.allowed_origins is not None:
        origins = widget_config.parse_origins(body.allowed_origins)
        # a typo must not silently leave the key open to the whole internet
        if body.allowed_origins and not origins:
            raise HTTPException(400, "No valid domain found in the allow-list")
        fields["allowed_origins"] = "\n".join(origins)
    if body.config is not None:
        merged = widget_config.merge_config(existing["config"], body.config)
        fields["config"] = widget_config.normalize_config(merged, existing["plan"])
    if not fields:
        raise HTTPException(400, "Nothing to update")

    db.update_api_key(key_id, user["id"], fields)
    return {"ok": True, "key": _decoded_key(db.get_api_key(key_id, user["id"]))}


@app.get("/api/api-keys/{key_id}/usage")
def key_usage(key_id: str, user: dict = Depends(auth.current_user)):
    key = db.get_api_key(key_id, user["id"])
    if not key:
        raise HTTPException(404, "API key not found")
    content = widget_config.normalize_config(key["config"], key["plan"])["content"]
    return {
        "usage": quota.usage(key_id),
        "limits": {"perMin": content["quotaPerMin"], "perDay": content["quotaPerDay"]},
        "plan": key["plan"],
    }


@app.delete("/api/api-keys/{key_id}")
def revoke_key(key_id: str, user: dict = Depends(auth.current_user)):
    if not db.revoke_api_key(key_id, user["id"]):
        raise HTTPException(404, "API key not found")
    return {"ok": True}


class WidgetChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None
    stream: bool = False


def _widget_key(request: Request, x_api_key: str | None = Header(None)) -> dict:
    """Resolve an API key to its record, enforcing that key's origin allow-list.

    The key arrives in a header for API calls and in the query string when the
    embed page asks for its own configuration.
    """
    token = x_api_key or request.query_params.get("key", "")
    if not token:
        raise HTTPException(401, "Missing X-API-Key header")
    record = db.get_api_key_record(token)
    if not record:
        raise HTTPException(401, "Invalid API key")
    origin = request.headers.get("origin", "")
    allowed = widget_config.parse_origins(record["allowed_origins"])
    if origin and not widget_config.origin_allowed(allowed, origin):
        raise HTTPException(403, "This origin is not allowed for the supplied API key")
    return record


@app.get("/api/widget/config")
def widget_public_config(record: dict = Depends(_widget_key)):
    """Branding + behaviour for the embed to render itself.

    Scoping and quota stay out of this payload: a visitor can read it in the
    page source, and neither belongs to them.
    """
    cfg = widget_config.normalize_config(record["config"], record["plan"])
    return {
        "config": widget_config.public_config(cfg),
        "plan": record["plan"],
        "whiteLabel": {
            "available": record["plan"] == "pro",
            "hidden": cfg["branding"]["hideBranding"],
        },
    }


def _widget_turn(record: dict, body: WidgetChatIn) -> tuple[str, str, list[dict]]:
    """Resolve (or open) the conversation and persist the user turn."""
    user_id = record["user_id"]
    question = body.message.strip()
    if not question:
        raise HTTPException(400, "Empty message")
    if body.conversation_id and db.get_conversation(body.conversation_id, user_id):
        cid = body.conversation_id
    else:
        cid = db.create_conversation(user_id, "[widget] " + question[:50])
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in db.get_messages(cid)
        if m["role"] in ("user", "assistant")
    ][-20:]
    db.add_message(cid, "user", question)
    return cid, question, history


@app.post("/api/widget/chat")
def widget_chat(body: WidgetChatIn, record: dict = Depends(_widget_key)):
    """Chat for embeds and server-to-server integrations.

    Returns JSON by default. With `"stream": true` it answers with the same
    `text/event-stream` the web app uses: `data: {"delta": ...}` per token,
    then `data: {"done": true, "conversation_id": ..., "sources": [...]}`.
    Multi-turn conversations are kept alive by passing conversation_id back.
    """
    cfg = widget_config.normalize_config(record["config"], record["plan"])
    content, behavior = cfg["content"], cfg["behavior"]

    allowed, status = quota.check(record["id"], content["quotaPerMin"], content["quotaPerDay"])
    if not allowed:
        raise HTTPException(
            429,
            f"Rate limit exceeded ({status['reason']}): this key allows "
            f"{status['limit']} requests per {status['reason']}.",
            headers={"Retry-After": str(status["retryAfter"])},
        )

    cid, question, history = _widget_turn(record, body)
    scope = {"doc_ids": content["docIds"], "fallback": behavior["fallbackMessage"]}

    if not body.stream:
        text, sources = rag.answer_once(record["user_id"], question, history, **scope)
        db.add_message(cid, "assistant", text, sources)
        return {"conversation_id": cid, "answer": text, "sources": sources}

    def stream():
        parts, sources = [], []
        try:
            for event, payload in rag.answer_stream(record["user_id"], question, history, **scope):
                if event == "delta":
                    parts.append(payload)
                    yield f"data: {json.dumps({'delta': payload})}\n\n"
                else:
                    sources = payload
        finally:
            full = "".join(parts).strip()
            db.add_message(cid, "assistant", full, sources=sources or None)
        yield f"data: {json.dumps({'done': True, 'conversation_id': cid, 'sources': sources})}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx must not buffer the stream
        },
    )


def _frame_ancestors(allowed: list[str]) -> str:
    """CSP value pinning who may iframe this page (browser-enforced, no bypass)."""
    if "*" in allowed:
        return "*"
    parts = ["'self'"]  # the /demo preview lives on this origin
    for host in allowed:
        parts.append("https://" + host)
        parts.append("http://" + host)
    return " ".join(parts)


@app.get("/widget")
def widget_page(key: str = ""):
    """Standalone embeddable chat page: <iframe src="https://host/widget?key=rbk_...">

    A key with a configured allow-list advertises it through `frame-ancestors`,
    so browsers refuse to render the widget on any other domain.
    """
    response = FileResponse(settings.base_dir + "/static/widget.html")
    response.headers["X-Content-Type-Options"] = "nosniff"
    # the widget is versioned with the JS it loads: a stale cached copy would
    # keep calling removed endpoints after an upgrade
    response.headers["Cache-Control"] = "no-cache"
    record = db.get_api_key_record(key) if key else None
    if record:
        allowed = widget_config.parse_origins(record["allowed_origins"])
        if allowed:
            response.headers["Content-Security-Policy"] = "frame-ancestors " + _frame_ancestors(allowed)
    return response


@app.get("/demo")
def demo_page():
    """Simulated customer site: an empty page whose bottom-right launcher opens
    the widget in a floating panel — the same thing the embed snippet produces."""
    return FileResponse(settings.base_dir + "/static/demo.html", headers={"Cache-Control": "no-cache"})


@app.get("/sdk.js")
def sdk_script():
    """Loader for developers: <script src=".../sdk.js" data-key="rbk_..."></script>"""
    return FileResponse(
        settings.base_dir + "/static/sdk.js",
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )


# ---------------------------------------------------------------- admin

@app.get("/api/admin/users")
def admin_users(_: dict = Depends(auth.require_admin)):
    return {"users": db.list_users()}


@app.get("/api/admin/stats")
def admin_stats(_: dict = Depends(auth.require_admin)):
    return db.stats()


class ResetPasswordIn(BaseModel):
    new_password: str = Field(min_length=6, max_length=128)


class PlanIn(BaseModel):
    plan: str


@app.patch("/api/admin/api-keys/{key_id}/plan")
def admin_set_plan(key_id: str, body: PlanIn, _: dict = Depends(auth.require_admin)):
    """Grant or revoke white-label. Only an admin can move a key to `pro`."""
    if body.plan not in ("free", "pro"):
        raise HTTPException(400, "plan must be 'free' or 'pro'")
    if not db.set_key_plan(key_id, body.plan):
        raise HTTPException(404, "API key not found")
    return {"ok": True, "plan": body.plan}


@app.post("/api/admin/users/{uid}/reset-password")
def admin_reset_password(uid: str, body: ResetPasswordIn, _: dict = Depends(auth.require_admin)):
    if not db.update_password(uid, auth.hash_password(body.new_password)):
        raise HTTPException(404, "User not found")
    return {"ok": True}


# ---------------------------------------------------------------- frontend

@app.get("/")
def index():
    return FileResponse(settings.base_dir + "/static/index.html")


app.mount("/static", StaticFiles(directory=settings.base_dir + "/static"), name="static")
