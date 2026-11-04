"""RAGbase — FastAPI application: auth, document management, RAG chat (SSE)."""
import json

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, database as db, ingest, providers, rag
from .config import settings

app = FastAPI(title="RAGbase", version="1.0.0")


@app.middleware("http")
async def widget_cors(request: Request, call_next):
    """Allow browser JS on customer sites to call /api/widget/* cross-origin."""
    if request.url.path.startswith("/api/widget"):
        if request.method == "OPTIONS":
            return Response(
                status_code=200,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "POST, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type, X-API-Key",
                    "Access-Control-Max-Age": "86400",
                },
            )
        response = await call_next(request)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
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
async def upload_documents(files: list[UploadFile] = File(...), user: dict = Depends(auth.current_user)):
    if settings.max_docs_per_user and db.doc_count_for_user(user["id"]) + len(files) > settings.max_docs_per_user:
        raise HTTPException(400, f"Document limit reached ({settings.max_docs_per_user} per user).")
    results = []
    import numpy as np

    for f in files:
        raw = await f.read()
        if len(raw) > settings.max_upload_mb * 1024 * 1024:
            results.append({"filename": f.filename, "ok": False, "error": f"File too large (max {settings.max_upload_mb} MB)"})
            continue
        doc_id = db.create_document(user["id"], f.filename or "unnamed")
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


@app.post("/api/api-keys")
def create_key(body: ApiKeyIn, user: dict = Depends(auth.current_user)):
    record, full = db.create_api_key(user["id"], body.label)
    return {"key": full, "id": record["id"], "prefix": record["prefix"]}


@app.get("/api/api-keys")
def list_keys(user: dict = Depends(auth.current_user)):
    return {"keys": db.list_api_keys(user["id"])}


@app.delete("/api/api-keys/{key_id}")
def revoke_key(key_id: str, user: dict = Depends(auth.current_user)):
    if not db.revoke_api_key(key_id, user["id"]):
        raise HTTPException(404, "API key not found")
    return {"ok": True}


class WidgetChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None


def _widget_user(x_api_key: str | None = Header(None)) -> dict:
    if not x_api_key:
        raise HTTPException(401, "Missing X-API-Key header")
    user = db.get_user_by_api_key(x_api_key)
    if not user:
        raise HTTPException(401, "Invalid API key")
    return user


@app.post("/api/widget/chat")
def widget_chat(body: WidgetChatIn, user: dict = Depends(_widget_user)):
    """Non-streaming chat for embeds/integrations. Conversation is kept alive
    by passing back the conversation_id we return."""
    question = body.message.strip()
    if body.conversation_id and db.get_conversation(body.conversation_id, user["id"]):
        cid = body.conversation_id
    else:
        cid = db.create_conversation(user["id"], "[widget] " + question[:50])
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in db.get_messages(cid)
        if m["role"] in ("user", "assistant")
    ][-20:]
    db.add_message(cid, "user", question)
    text, sources = rag.answer_once(user["id"], question, history)
    db.add_message(cid, "assistant", text, sources)
    return {"conversation_id": cid, "answer": text, "sources": sources}


@app.get("/widget")
def widget_page():
    """Standalone embeddable chat page: <iframe src="https://host/widget?key=rbk_...">"""
    return FileResponse(settings.base_dir + "/static/widget.html")


# ---------------------------------------------------------------- admin

@app.get("/api/admin/users")
def admin_users(_: dict = Depends(auth.require_admin)):
    return {"users": db.list_users()}


@app.get("/api/admin/stats")
def admin_stats(_: dict = Depends(auth.require_admin)):
    return db.stats()


class ResetPasswordIn(BaseModel):
    new_password: str = Field(min_length=6, max_length=128)


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
