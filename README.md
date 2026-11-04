# RAGbase

**Chat with your documents. Every answer cites its sources.**

[English](README.md) · [简体中文](README.zh-CN.md)

RAGbase is a production-ready, self-hosted **RAG (retrieval-augmented generation)** web app.
Upload PDFs, DOCX, Markdown and more — then ask questions in natural language and get
streaming AI answers grounded **only in your knowledge base**, with numbered citations
linking back to the exact source passages.

No vector database. No build step. One process, one SQLite file.

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Tests](https://img.shields.io/badge/tests-326%20passing-brightgreen)](#tests)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

![RAGbase chat with citations](docs/1_chat_with_citations.png)

## Features

- 🔐 **Accounts & admin** — JWT auth, bcrypt hashing. The first registered account
  becomes admin; admins can reset any user's password.
- 📄 **Document ingestion** — PDF, DOCX, TXT, Markdown, HTML. Smart chunking with overlap,
  per-user quotas and upload-size limits.
- 🧠 **Hybrid retrieval** — vector embeddings fused with **BM25 lexical search via RRF**
  (Reciprocal Rank Fusion). Precise on keywords, robust on semantics. Per-document
  diversity caps and relevance thresholding keep answers on-topic.
- 🀄 **Chinese-first** — bilingual UI (zh/en, follows the browser language) and CJK
  **bigram** indexing, so 退款 no longer collides with 汇款 or 条款. See
  [Chinese support](#chinese-support).
- 💬 **Streaming chat** — token-by-token answers over SSE, per-user conversation history.
- 🎯 **Source citations** — numbered chips in every answer plus a relevance-scored sources
  panel showing the exact passages used.
- 🌐 **Website widget** — a floating iframe chat that embeds into any customer site with a
  copy-paste snippet, backed by scoped, revocable API keys (`rbk_…`, stored hashed).
- 🔌 **REST + SSE API** — server-to-server chat endpoint for custom front-ends.
- 🧪 **Demo mode** — runs fully offline without any API key (local extractive engine), so
  you can evaluate the entire product before plugging in a model.
- 🗄️ **Zero external services** — vectors live in SQLite as float32 blobs, searched with
  numpy cosine similarity; a per-user BM25 index stays in memory. Great for hundreds of
  documents; swap in Postgres + pgvector when you outgrow it.

## Quick start

```bash
git clone https://github.com/<you>/ragbase.git
cd ragbase
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # fill in your LLM key (optional)
python run.py                      # serves on http://localhost:7100
```

Open http://localhost:7100, create an account, switch to the **Knowledge base** tab,
upload documents, and start chatting.

> **Prefer containers?** `docker compose up -d --build` gives you the same app on
> http://localhost:8000 with no local Python at all — see [Docker](#docker--one-command).

> **No LLM key?** The app runs in demo mode and still retrieves and quotes relevant
> passages — perfect for trying the product end to end. Endpoints without an embeddings
> API (e.g. DeepSeek) work out of the box: chat goes to the LLM, embeddings stay local,
> and BM25 keeps lexical search strong.

## Chinese support

Chinese ships as a first-class language, not an afterthought.

**Interface** — a dependency-free i18n layer (`app/static/i18n.js`) with `zh`/`en`
dictionaries. The UI follows `navigator.language` on first visit (Chinese browsers get
Chinese, English browsers stay English), the topbar button switches at runtime, and the
choice is remembered in `localStorage`. Dates format per UI language, so an English
interface never shows 11月4日. The embeddable widget is bilingual too, with
`?lang=zh` / `?lang=en` for per-customer defaults.

**Retrieval** — Chinese has no spaces, so without a dictionary you must pick a splitting
granularity. RAGbase indexes **character bigrams**:

| Text | Unigrams (old) | Bigrams (now) |
|---|---|---|
| `退款政策` | 退 / 款 / 政 / 策 | 退款 / 款政 / 政策 |
| query `退款` vs doc `汇款手续费` | matches (both contain 款) | no match |
| query `退款` vs doc `退款政策` | matches | matches |

Single-character runs stay as-is (`税` still works). The same tokenizer feeds both BM25
and the local feature-hash vectors, so both retrievers agree on vocabulary.

Demo vectors are hashes of those tokens, so changing the tokenizer moves the coordinate
space. On startup the app compares `meta.tokenizer_version` and re-embeds every chunk
in batches when it differs — no manual migration, no data loss. Deployments using a real
embedding API are unaffected (the provider owns the model).

**Answers** — the system prompt pins the reply language to the question's language while
keeping `[1]` citation markers intact; demo-mode fallbacks are bilingual; SSE streams
Chinese in small slices so it types out visibly instead of appearing in one dump.

## Works with any OpenAI-compatible LLM

| Provider | Chat | Embeddings |
|---|---|---|
| OpenAI | ✅ | ✅ |
| DeepSeek | ✅ | local fallback (BM25 carries retrieval) |
| Groq / Together / vLLM / Ollama | ✅ | per provider |

Set `RAGBASE_LLM_BASE_URL`, `RAGBASE_LLM_API_KEY` and `RAGBASE_LLM_MODEL` in `.env`.
Everything else is automatic.

![Knowledge base](docs/2_knowledge_base.png)

## Embed it in any website

Create an API key in the **Integrate** tab, then drop one snippet into the customer's page:

```html
<iframe src="https://your-host/widget?key=rbk_..." style="position:fixed;bottom:20px;right:20px;width:380px;height:600px;border:none;z-index:9999;border-radius:16px;box-shadow:0 8px 30px rgba(0,0,0,.35);" title="Knowledge base chat"></iframe>
```

Or call the API directly (CORS enabled):

```bash
curl -X POST https://your-host/api/widget/chat \
  -H "X-API-Key: rbk_..." -H "Content-Type: application/json" \
  -d '{"message": "What is the refund policy?", "conversation_id": null}'
# → {"conversation_id": "...", "answer": "...", "sources": [...]}
```

API keys are scoped to one knowledge base and can only chat — they cannot manage documents
or users. Revoke anytime from the **Integrate** tab. The widget page
(`app/static/widget.html`) is self-contained, so title, greeting and colors are easy to
rebrand per customer.

![Embed in your website](docs/hero_2_integration.png)

## Configuration

All settings live in `.env` (see [.env.example](.env.example)):

| Variable | Default | Purpose |
|---|---|---|
| `RAGBASE_LLM_API_KEY` | — | Enables the real LLM. Any OpenAI-compatible endpoint works via `RAGBASE_LLM_BASE_URL`. Legacy `OPENAI_API_KEY`/`OPENAI_BASE_URL` are also honoured. |
| `RAGBASE_LLM_BASE_URL` | `https://api.openai.com/v1` | Chat endpoint |
| `RAGBASE_LLM_MODEL` | `gpt-4o-mini` | Chat model (e.g. `deepseek-chat`) |
| `RAGBASE_EMBEDDING_API_KEY` | — | Embeddings. Leave empty for local feature-hash vectors + BM25. |
| `RAGBASE_JWT_SECRET` | — | **Change in production** |
| `RAGBASE_ALLOW_REGISTRATION` | `true` | Set `false` to make the instance invite-only |
| `RAGBASE_MAX_DOCS_PER_USER` | `0` (unlimited) | Per-user document cap |
| `RAGBASE_MAX_UPLOAD_MB` | `25` | Upload size limit per file |
| `RAGBASE_CHUNK_SIZE` / `RAGBASE_CHUNK_OVERLAP` | `900` / `150` | Chunking parameters |
| `RAGBASE_TOP_K` | `6` | Retrieved passages per question |
| `RAGBASE_DATA_DIR` | `data` | SQLite database location |

## Docker — one command

```bash
git clone https://github.com/<you>/ragbase.git && cd ragbase
docker compose up -d --build
# → http://localhost:8000
```

That is the whole install. No Python, no venv, no `.env` needed: with no API key the
container boots in demo mode, so you can register, upload a PDF and get cited answers
immediately. Add a key later and restart — nothing else changes.

Or use the launcher, which also creates `.env` on first run, waits for the health check
and prints the URL:

```bash
./docker.sh                 # build + start + wait until healthy
./docker.sh logs            # follow logs
./docker.sh restart         # rebuild and restart, data kept
./docker.sh stop            # stop, data kept
./docker.sh destroy         # wipe container + data volume
./docker.sh reset-password user@example.com newpass
```

| File | Purpose |
|---|---|
| `Dockerfile` | Python 3.12-slim, non-root (`uid 10001`), `HEALTHCHECK` on `/api/health` |
| `docker-compose.yml` | Port, env vars, named `ragbase-data` volume, log rotation |
| `docker-entrypoint.sh` | Auto-generates a persistent `RAGBASE_JWT_SECRET`, fixes volume ownership, drops root |
| `.dockerignore` | Keeps `.venv`, local `data/`, screenshots and media out of the image (≈6 KB of build context vs. 40 MB) |
| `.env.example` | Every supported variable, documented |

**Configure it** — edit `.env` (copy from `.env.example`) and `docker compose up -d`:

```ini
RAGBASE_PORT=8000                       # host port
RAGBASE_LLM_API_KEY=sk-...              # leave empty for demo mode
RAGBASE_LLM_BASE_URL=https://api.deepseek.com/v1
RAGBASE_LLM_MODEL=deepseek-chat
RAGBASE_ALLOW_REGISTRATION=false        # invite-only after you create the admin
```

**Keep data in a visible folder** instead of a named volume — swap the volume line in
`docker-compose.yml`:

```yaml
    volumes:
      - ./data:/app/data
```

**Plain `docker run`**, if you prefer no compose:

```bash
docker build -t ragbase .
docker run -d --name ragbase -p 8000:8000 --restart unless-stopped \
  --env-file .env -v ragbase-data:/app/data ragbase
```

**Backup / restore** — the instance is one SQLite file inside the volume:

```bash
docker run --rm -v ragbase-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/ragbase-backup.tar.gz -C /data .
```

The container is stateless apart from `/app/data`; `docker compose down` (without `-v`)
never touches your documents.

**Any VPS** — behind nginx/Caddy with HTTPS:

```ini
[Service]
WorkingDirectory=/opt/ragbase
EnvironmentFile=/opt/ragbase/.env
ExecStart=/opt/ragbase/.venv/bin/python run.py --host 127.0.0.1 --port 7100
Restart=always
```

## Tests

The suite is hermetic: `tests/conftest.py` pins the environment **before** the app is
imported, so pytest always boots in demo mode against a throwaway SQLite database.
No LLM key, no network, and your `data/` folder is never touched.

```bash
pip install -r requirements-dev.txt
pytest                                        # 326 tests, ~45s
pytest --cov=app --cov-report=term-missing    # 99% coverage of app/
```

| Area | File |
|---|---|
| Config, `.env` parsing, mode switches | `tests/test_config.py` |
| bcrypt hashing, JWT, email validation | `tests/test_auth_unit.py` |
| SQLite layer: users, documents, chunks, API keys, conversations, cascades | `tests/test_database.py` |
| Parsing & chunking (txt, md, html, docx, pdf) | `tests/test_ingest.py` |
| Demo engine, OpenAI-compatible chat/embeddings, tokenizer & vector migration | `tests/test_providers.py` |
| BM25 scoring, index caching, per-user isolation, Chinese queries | `tests/test_bm25.py` |
| Retrieval, RRF fusion, prompt assembly, answers | `tests/test_rag.py` |
| HTTP: auth, documents, SSE chat, conversations, API keys & widget, admin, static/CORS | `tests/test_api_*.py` |
| Ops CLI (`python -m app.tools`) | `tests/test_tools_cli.py` |

## Architecture

```
ragbase/
├── run.py                  # entry point
├── app/
│   ├── main.py             # FastAPI routes: auth, documents, chat (SSE), admin
│   ├── config.py           # env-driven settings
│   ├── database.py         # SQLite: users, docs, chunks(+vectors), conversations
│   ├── auth.py             # JWT + bcrypt
│   ├── ingest.py           # parsing (pdf/docx/txt/md/html) + chunking
│   ├── bm25.py             # per-user lexical index, cached in memory
│   ├── providers.py        # OpenAI / demo providers, zh/en tokenizer, vector migration
│   ├── rag.py              # retrieval, RRF fusion, prompt building, cited answering
│   └── tools.py            # ops CLI
└── app/static/             # frontend (vanilla JS, no build): i18n.js, app.js, styles.css
```

## Operations

```bash
python -m app.tools reset-password user@example.com newpassword   # works even if admin is locked out
python -m app.tools create-user user@example.com password [--admin]
python -m app.tools list-users
```

## Customization

- **Branding:** app name and logo in `app/static/index.html`; colors are CSS custom
  properties at the top of `app/static/styles.css`.
- **Copy & languages:** `I18N.en` / `I18N.zh` in `app/static/i18n.js` — add a dictionary
  to add a language.
- **Persona:** edit `SYSTEM_PROMPT` in `app/rag.py`.
- **Retrieval quality:** tune `RAGBASE_TOP_K`, threshold, and chunk size via `.env`;
  CJK granularity lives in `providers._cjk_tokens()` (bump `TOKENIZER_VERSION` after
  changing it — vectors are rebuilt on the next start).

## License

[MIT](LICENSE) — use it, ship it, rebrand it, sell it to your customers.
