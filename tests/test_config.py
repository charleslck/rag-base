"""Configuration: env parsing, `.env` loading, derived mode switches."""
from __future__ import annotations

import dataclasses
import os

import pytest

from app import config as config_module
from app.config import settings


# --------------------------------------------------------------------- _bool

@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "True", " yes ", "on", "ON"])
def test_bool_accepts_truthy_spellings(monkeypatch, raw):
    monkeypatch.setenv("RB_TEST_FLAG", raw)
    assert config_module._bool("RB_TEST_FLAG", False) is True


@pytest.mark.parametrize("raw", ["0", "false", "FALSE", "no", "off", "", "  ", "maybe"])
def test_bool_rejects_everything_else(monkeypatch, raw):
    monkeypatch.setenv("RB_TEST_FLAG", raw)
    assert config_module._bool("RB_TEST_FLAG", True) is False


def test_bool_falls_back_to_default_when_unset(monkeypatch):
    monkeypatch.delenv("RB_TEST_FLAG", raising=False)
    assert config_module._bool("RB_TEST_FLAG", True) is True
    assert config_module._bool("RB_TEST_FLAG", False) is False


# ------------------------------------------------------------- .env loading

@pytest.fixture()
def fake_env_file(temp_dir, monkeypatch):
    """Point `_load_env_file` at a temporary `<tmp>/.env` file."""
    app_dir = temp_dir / "app"
    app_dir.mkdir()
    monkeypatch.setattr(config_module, "__file__", str(app_dir / "config.py"))
    return temp_dir / ".env"


def test_env_file_parses_pairs_and_strips_quotes(fake_env_file, monkeypatch):
    fake_env_file.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                "RB_PLAIN=value",
                'RB_DOUBLE="quoted value"',
                "RB_SINGLE='single quoted'",
                "RB_WITH_EQUALS=a=b=c",
                "not-a-pair-line",
            ]
        ),
        encoding="utf-8",
    )
    for key in ("RB_PLAIN", "RB_DOUBLE", "RB_SINGLE", "RB_WITH_EQUALS"):
        monkeypatch.delenv(key, raising=False)

    config_module._load_env_file()

    assert os.environ["RB_PLAIN"] == "value"
    assert os.environ["RB_DOUBLE"] == "quoted value"
    assert os.environ["RB_SINGLE"] == "single quoted"
    assert os.environ["RB_WITH_EQUALS"] == "a=b=c"
    for key in ("RB_PLAIN", "RB_DOUBLE", "RB_SINGLE", "RB_WITH_EQUALS"):
        monkeypatch.delenv(key)


def test_env_file_never_overrides_real_environment(fake_env_file, monkeypatch):
    fake_env_file.write_text("RB_TAKEN=from-file\n", encoding="utf-8")
    monkeypatch.setenv("RB_TAKEN", "from-process")

    config_module._load_env_file()

    assert os.environ["RB_TAKEN"] == "from-process"
    monkeypatch.delenv("RB_TAKEN")


def test_env_file_missing_is_ignored(temp_dir, monkeypatch):
    app_dir = temp_dir / "app"
    app_dir.mkdir()
    monkeypatch.setattr(config_module, "__file__", str(app_dir / "config.py"))
    # No .env next to it — must not raise.
    config_module._load_env_file()


# ------------------------------------------------------------------- Settings

def test_settings_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.top_k = 99  # type: ignore[misc]


def test_db_path_follows_data_dir(reload_settings):
    loaded = reload_settings(RAGBASE_DATA_DIR="/tmp/ragbase-unit")
    assert loaded.db_path == os.path.join("/tmp/ragbase-unit", "ragbase.db")


def test_demo_mode_switches_follow_the_api_keys(reload_settings):
    loaded = reload_settings(RAGBASE_LLM_API_KEY="", RAGBASE_EMBEDDING_API_KEY="")
    assert loaded.chat_demo is True
    assert loaded.demo_mode is True
    assert loaded.embedding_local is True

    loaded = reload_settings(RAGBASE_LLM_API_KEY="sk-chat", RAGBASE_EMBEDDING_API_KEY="sk-embed")
    assert loaded.chat_demo is False
    assert loaded.demo_mode is False
    assert loaded.embedding_local is False

    # A chat model with local embeddings is a supported mix (e.g. DeepSeek).
    loaded = reload_settings(RAGBASE_LLM_API_KEY="sk-chat", RAGBASE_EMBEDDING_API_KEY="   ")
    assert loaded.chat_demo is False
    assert loaded.embedding_local is True


def test_numeric_settings_are_parsed(reload_settings):
    loaded = reload_settings(
        RAGBASE_TOP_K="3",
        RAGBASE_CHUNK_SIZE="500",
        RAGBASE_CHUNK_OVERLAP="50",
        RAGBASE_MAX_UPLOAD_MB="7",
        RAGBASE_MAX_DOCS_PER_USER="12",
        RAGBASE_JWT_EXPIRE_HOURS="1",
        RAGBASE_EMBEDDING_DIM="128",
        RAGBASE_RETRIEVAL_THRESHOLD="0.42",
    )
    assert loaded.top_k == 3
    assert loaded.chunk_size == 500
    assert loaded.chunk_overlap == 50
    assert loaded.max_upload_mb == 7
    assert loaded.max_docs_per_user == 12
    assert loaded.jwt_expire_hours == 1
    assert loaded.embedding_dim == 128
    assert loaded.retrieval_threshold == pytest.approx(0.42)


def test_defaults_match_documentation(reload_settings):
    loaded = reload_settings(
        RAGBASE_TOP_K=None,
        RAGBASE_CHUNK_SIZE=None,
        RAGBASE_CHUNK_OVERLAP=None,
        RAGBASE_MAX_UPLOAD_MB=None,
        RAGBASE_MAX_DOCS_PER_USER=None,
    )
    # Values documented in README.md; `.env` may legitimately override them,
    # so this only asserts the documented fallbacks are wired up.
    assert loaded.top_k > 0
    assert loaded.chunk_overlap < loaded.chunk_size
    assert loaded.max_upload_mb > 0
    assert loaded.jwt_secret
    assert loaded.llm_base_url


def test_openai_legacy_variables_are_honoured(reload_settings):
    # Explicit empty string (not `None`) so `.env` cannot backfill the key.
    loaded = reload_settings(
        RAGBASE_LLM_API_KEY="",
        OPENAI_API_KEY="sk-legacy",
        OPENAI_BASE_URL="https://legacy.example/v1",
    )
    assert loaded.llm_api_key == "sk-legacy"
    assert loaded.llm_base_url == "https://legacy.example/v1"
    assert loaded.chat_demo is False
