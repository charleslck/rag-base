"""Pytest fixtures and app bootstrapping for the RAGbase test suite.

`app/config.py` builds a *frozen* `Settings` singleton at import time and merges
`.env` without overriding variables that already exist in the environment.
Every relevant variable is therefore pinned here **before** the `app` package is
imported, which gives the suite three useful properties:

* it never touches the developer's real `data/ragbase.db`;
* it never calls a real model provider (empty API keys => demo mode);
* every test starts from a completely empty database.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
for path in (str(ROOT), str(TESTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

# --------------------------------------------------------------------------
# Environment must be fixed before `app` is imported.
# --------------------------------------------------------------------------
TEST_DATA_DIR = tempfile.mkdtemp(prefix="ragbase-pytest-")

os.environ.update(
    {
        "RAGBASE_DATA_DIR": TEST_DATA_DIR,
        "RAGBASE_JWT_SECRET": "pytest-secret-key-0123456789abcdef",
        "RAGBASE_JWT_EXPIRE_HOURS": "72",
        "RAGBASE_ALLOW_REGISTRATION": "true",
        "RAGBASE_MAX_DOCS_PER_USER": "0",
        "RAGBASE_MAX_UPLOAD_MB": "25",
        "RAGBASE_CHUNK_SIZE": "900",
        "RAGBASE_CHUNK_OVERLAP": "150",
        "RAGBASE_TOP_K": "6",
        "RAGBASE_RETRIEVAL_THRESHOLD": "0.15",
        "RAGBASE_EMBEDDING_DIM": "1536",
        # Offline demo mode: no chat model, no embedding API.
        "RAGBASE_LLM_API_KEY": "",
        "RAGBASE_LLM_BASE_URL": "",
        "RAGBASE_LLM_MODEL": "test-model",
        "RAGBASE_EMBEDDING_API_KEY": "",
        "OPENAI_API_KEY": "",
        "OPENAI_BASE_URL": "",
    }
)

from starlette.testclient import TestClient  # noqa: E402  (must follow env setup)

from app import bm25, database as db, providers  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from helpers import register  # noqa: E402

# Delete children before parents so ON DELETE CASCADE never has to run.
_TABLES_IN_DELETE_ORDER = (
    "messages",
    "conversations",
    "api_keys",
    "embeddings",
    "chunks",
    "documents",
    "users",
)


@pytest.fixture(autouse=True)
def isolated_database():
    """Give every test an empty database and cold in-memory caches."""
    conn = db.get_conn()
    for table in _TABLES_IN_DELETE_ORDER:
        conn.execute(f"DELETE FROM {table}")
    conn.commit()

    bm25._cache.clear()
    providers._openai_client = None
    providers._chat_client = None
    yield


@pytest.fixture()
def temp_dir():
    """A throwaway directory (used instead of `tmp_path`)."""
    path = Path(tempfile.mkdtemp(prefix="ragbase-testcase-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture()
def client():
    """A TestClient with lifespan startup applied (creates the schema)."""
    with TestClient(fastapi_app) as test_client:
        yield test_client


@contextlib.contextmanager
def _override_settings(**changes):
    """Temporarily change the frozen settings singleton.

    `Settings` is a frozen dataclass, so `setattr` is blocked; `object.__setattr__`
    bypasses the guard without touching the dataclass definition.
    """
    previous = {key: getattr(settings, key) for key in changes}
    for key, value in changes.items():
        object.__setattr__(settings, key, value)
    try:
        yield settings
    finally:
        for key, value in previous.items():
            object.__setattr__(settings, key, value)


@pytest.fixture()
def patch_settings():
    """`with patch_settings(max_upload_mb=0): ...`"""
    return _override_settings


@pytest.fixture()
def admin(client):
    """First registered account — becomes the instance admin."""
    data = register(client, "admin@example.com", "adminpass123")
    return {
        "email": "admin@example.com",
        "password": "adminpass123",
        "token": data["token"],
        "headers": {"Authorization": f"Bearer {data['token']}"},
    }


@pytest.fixture()
def user(client, admin):
    """Second account — a regular, non-admin user.

    Depends on `admin` so the admin is always registered first (registration
    order decides who gets the admin flag).
    """
    data = register(client, "user@example.com", "userpass123")
    return {
        "email": "user@example.com",
        "password": "userpass123",
        "token": data["token"],
        "headers": {"Authorization": f"Bearer {data['token']}"},
    }


@pytest.fixture()
def reload_settings(monkeypatch):
    """Reload `app.config` with a temporary environment and return its Settings.

    `Settings` field defaults are evaluated once when the class is defined, so a
    plain `Settings()` call would keep the values captured at import time —
    reloading the module is the only way to exercise env parsing.
    """
    import importlib

    from app import config as config_module

    original = config_module.settings
    # Reloading re-runs `_load_env_file()`, which writes straight into os.environ
    # (monkeypatch cannot track that), so snapshot the whole environment.
    env_snapshot = dict(os.environ)

    def load(**env):
        for key, value in env.items():
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, value)
        return importlib.reload(config_module).settings

    yield load

    # Restore module state; other modules imported the original singleton.
    importlib.reload(config_module)
    config_module.settings = original
    os.environ.clear()
    os.environ.update(env_snapshot)
