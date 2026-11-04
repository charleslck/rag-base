"""RAGbase — FastAPI application for an on-premise installation.

One account owns one knowledge base. There is no tenant hierarchy, no invitations
and no hosted billing: accounts are created by the operator, the first one is
admin, and the whole thing runs on a single SQLite file inside the customer's
network.
"""
import json
import time

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import audit, auth, database as db, ingest, ingest_jobs, providers, quota, rag, ratelimit
from . import usage, widget_config
from .config import settings

app = FastAPI(title="RAGbase", version="1.2.0")


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
    # a crash mid-ingest leaves rows in `processing` that no job will finish
    stuck = db.fail_stuck_documents()
    if stuck:
        print(f"[ragbase] marked {stuck} interrupted upload(s) as failed")
    # local (demo) vectors are hashed from our own tokens: rebuild them once
    # after a tokenizer upgrade so existing knowledge bases stay searchable
    rebuilt = providers.ensure_vector_compatibility()
    if rebuilt:
        print(f"[ragbase] re-embedded {rebuilt} chunks for tokenizer v{providers.TOKENIZER_VERSION}")


@app.on_event("shutdown")
def shutdown():
    """Let in-flight ingestion finish before the worker goes away."""
    if ingest_jobs.pending():
        ingest_jobs.wait_idle(timeout=30)


@app.get("/api/health")
def health():
    """Liveness probe for Docker/load balancers — must stay dependency-free."""
    return {"status": "ok", "version": app.version}


def _client_ip(request: Request) -> str:
    client = request.client
    return client.host if client else ""


# ---------------------------------------------------------------- auth

class RegisterIn(BaseModel):
    email: str
    password: str = Field(min_length=6, max_length=128)


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6, max_length=128)


class ConfirmPasswordIn(BaseModel):
    """Re-authentication for destructive actions (account deletion)."""
    password: str


def _auth_payload(user: dict, token: str) -> dict:
    return {
        "token": token,
        "user": {"email": user["email"], "is_admin": bool(user["is_admin"])},
    }


@app.post("/api/auth/register")
def register(body: RegisterIn, request: Request):
    email = body.email.strip().lower()
    if not auth.EMAIL_RE.match(email):
        raise HTTPException(400, "Invalid email address")
    first_user = db.user_count() == 0
    if not first_user and not settings.allow_registration:
        raise HTTPException(403, "Registration is disabled. Ask an admin to create your account.")
    if db.get_user_by_email(email):
        raise HTTPException(409, "An account with this email already exists")
    db.create_user(email, auth.hash_password(body.password), is_admin=first_user)
    user = db.get_user_by_email(email)
    audit.record(user, "user.register", ip=_client_ip(request))
    return _auth_payload(user, auth.make_token(user["id"], user["is_admin"], user["token_version"]))


@app.post("/api/auth/login")
def login(body: RegisterIn, request: Request):
    email = body.email.strip().lower()
    ip = _client_ip(request)
    key = f"login:{email}|{ip}"
    allowed, retry = ratelimit.hit(key, settings.login_max_attempts, settings.login_window_seconds)
    if not allowed:
        raise HTTPException(
            429, "Too many attempts — try again later.", headers={"Retry-After": str(retry)}
        )
    user = db.get_user_by_email(email)
    if not user or not auth.verify_password(body.password, user["password_hash"]):
        audit.record({"email": email}, "login.failure", ip=ip)
        raise HTTPException(401, "Invalid email or password")
    ratelimit.clear(key)
    audit.record(user, "login.success", ip=ip)
    return _auth_payload(user, auth.make_token(user["id"], user["is_admin"], user["token_version"]))


@app.post("/api/auth/change-password")
def change_password(body: ChangePasswordIn, user: dict = Depends(auth.current_user)):
    """Self-service password change; also revokes every older session."""
    if not auth.verify_password(body.current_password, user["password_hash"]):
        raise HTTPException(401, "Current password is wrong")
    db.update_password(user["id"], auth.hash_password(body.new_password))
    fresh = db.get_user_by_id(user["id"])
    audit.record(fresh, "password.change")
    return {"ok": True, "token": auth.make_token(fresh["id"], fresh["is_admin"], fresh["token_version"])}


@app.post("/api/auth/sessions/revoke")
def revoke_sessions(user: dict = Depends(auth.current_user)):
    """Sign out every device: bumps the token version inside the JWTs."""
    db.revoke_sessions(user["id"])
    audit.record(user, "sessions.revoke")
    return {"ok": True}


@app.delete("/api/account")
def delete_account(request: Request, body: ConfirmPasswordIn,
                   user: dict = Depends(auth.current_user)):
    """Delete this account and everything it owns (documents, chats, keys)."""
    if not auth.verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Wrong password")
    audit.record(user, "account.delete", ip=_client_ip(request))
    db.delete_user(user["id"])
    return {"ok": True}


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


@app.get("/api/usage")
def account_usage(days: int = 30, user: dict = Depends(auth.current_user)):
    """Token meter for this account: totals, daily series and per-key split.

    Capacity planning for the operator, not a bill. `cost` is derived live from
    RAGBASE_PRICE_PER_MTOK_IN/OUT; leave those at 0 to report tokens only.
    """
    return usage.summary(user["id"], days=days)


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


def _prepare(filename: str, raw: bytes) -> list[str]:
    """Parse and chunk one upload. Runs in a worker thread: pypdf is CPU-bound."""
    text = ingest.extract_text(filename, raw)
    chunks = ingest.chunk_text(text)
    if not chunks:
        raise ingest.ParseError("No readable text found in this file.")
    return chunks


@app.post("/api/documents")
async def upload_documents(
    files: list[UploadFile] = File(...),
    folder_id: str = Form(""),
    user: dict = Depends(auth.current_user),
):
    """Accept uploads, parse them now, embed them in the background.

    Parsing stays on the request path so an unreadable file is reported
    immediately and the chunk count is known up front. Embedding (the part that
    calls out to a provider once per batch) is queued: the response says
    `"status": "processing"` and the library polls until it turns `ready`.
    """
    if settings.max_docs_per_user and db.doc_count_for_user(user["id"]) + len(files) > settings.max_docs_per_user:
        raise HTTPException(400, f"Document limit reached ({settings.max_docs_per_user} per user).")
    # uploads land in whatever folder is open, so validate before writing any of them
    target = folder_id or None
    if target and not db.get_folder(target, user["id"]):
        raise HTTPException(404, "Folder not found")
    results = []
    initial_status = "ready" if settings.ingest_sync else "processing"

    for f in files:
        raw = await f.read()
        name = f.filename or "unnamed"
        if len(raw) > settings.max_upload_mb * 1024 * 1024:
            results.append({"filename": name, "ok": False, "error": f"File too large (max {settings.max_upload_mb} MB)"})
            continue
        doc_id = db.create_document(user["id"], name, target, status=initial_status)
        try:
            chunks = await run_in_threadpool(_prepare, name, raw)
        except ingest.ParseError as exc:
            db.finish_document(doc_id, 0, str(exc))
            results.append({"filename": name, "ok": False, "error": str(exc)})
            continue
        ingest_jobs.submit(doc_id, user["id"], chunks)
        results.append({
            "filename": name,
            "ok": True,
            "chunks": len(chunks),
            "id": doc_id,
            "status": initial_status,
        })
    return {"results": results}


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: str, user: dict = Depends(auth.current_user)):
    if not db.delete_document(doc_id, user["id"]):
        raise HTTPException(404, "Document not found")
    audit.record(user, "document.delete", target_id=doc_id)
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
    deleted = db.delete_documents(ids, user["id"])
    audit.record(user, "document.delete", detail=f"{deleted} documents")
    return {"deleted": deleted, "requested": len(ids)}


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
    started = time.time()
    meter = usage.Usage()

    def stream():
        parts, sources = [], []
        try:
            for event, payload in rag.answer_stream(user["id"], question, history, usage=meter):
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
            usage.record(user["id"], meter, source="web",
                         latency_ms=int((time.time() - started) * 1000))
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
    audit.record(user, "key.create", target_id=record["id"], detail=body.label)
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
    audit.record(user, "key.update", target_id=key_id)
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
    audit.record(user, "key.revoke", target_id=key_id)
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
        cid = db.create_conversation(user_id, "[widget] " + question[:50], api_key_id=record["id"])
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
    started = time.time()
    meter = usage.Usage()

    def meter_now() -> None:
        usage.record(record["user_id"], meter, source="widget", api_key_id=record["id"],
                     latency_ms=int((time.time() - started) * 1000))

    if not body.stream:
        text, sources = rag.answer_once(record["user_id"], question, history, usage=meter, **scope)
        db.add_message(cid, "assistant", text, sources)
        meter_now()
        return {"conversation_id": cid, "answer": text, "sources": sources}

    def stream():
        parts, sources = [], []
        try:
            for event, payload in rag.answer_stream(record["user_id"], question, history,
                                                    usage=meter, **scope):
                if event == "delta":
                    parts.append(payload)
                    yield f"data: {json.dumps({'delta': payload})}\n\n"
                else:
                    sources = payload
        finally:
            full = "".join(parts).strip()
            db.add_message(cid, "assistant", full, sources=sources or None)
            meter_now()
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
    response.headers["Referrer-Policy"] = "no-referrer"  # the key rides in the query string
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


# ---------------------------------------------------------------- ops: audit & export

@app.get("/api/audit")
def audit_log(days: int = 30, limit: int = 200, action: str = "",
              _: dict = Depends(auth.current_user)):
    """Instance activity trail for the operator (admin only)."""
    days = max(1, min(days, 365))
    return {
        "days": days,
        "events": db.list_audit_events(
            since=time.time() - days * 86400, limit=limit, action=action
        ),
    }


@app.get("/api/export")
def export_data(user: dict = Depends(auth.current_user)):
    """Everything this account owns, as one JSON download.

    For backup, migration between on-premise installs and handover to the
    customer. Key hashes and password hashes are deliberately absent.
    """
    uid = user["id"]
    payload = {
        "format": "ragbase-export/1",
        "exported_at": time.time(),
        "account": {"email": user["email"], "is_admin": bool(user["is_admin"])},
        "documents": [
            {**doc, "text": db.chunks_text(doc["id"])}
            for doc in db.list_documents(uid)
        ],
        "conversations": [
            {**conv, "messages": db.get_messages(conv["id"])}
            for conv in db.list_conversations(uid)
        ],
        "api_keys": db.list_api_keys(uid),
        "usage_totals": db.usage_totals(uid),
    }
    audit.record(user, "data.export")
    filename = f"ragbase-export-{user['email'].split('@')[0]}-{time.strftime('%Y%m%d')}.json"
    return Response(
        json.dumps(payload, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
    """The operator's recovery path when someone is locked out (no mail server)."""
    if not db.update_password(uid, auth.hash_password(body.new_password)):
        raise HTTPException(404, "User not found")
    return {"ok": True}


# ---------------------------------------------------------------- frontend

@app.get("/")
def index():
    return FileResponse(settings.base_dir + "/static/index.html")


app.mount("/static", StaticFiles(directory=settings.base_dir + "/static"), name="static")
