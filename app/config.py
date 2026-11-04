"""Central configuration. All values can be overridden via environment variables.

Copy .env.example to .env and adjust for your deployment. The .env file is
loaded automatically (without overriding real environment variables).
"""
import os
from dataclasses import dataclass


def _load_env_file() -> None:
    """Minimal .env loader: KEY=VALUE lines, no override of existing env."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except FileNotFoundError:
        pass


_load_env_file()


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # --- Paths ---
    base_dir: str = os.path.dirname(os.path.abspath(__file__))
    data_dir: str = os.environ.get("RAGBASE_DATA_DIR", "data")

    # --- Security ---
    jwt_secret: str = os.environ.get("RAGBASE_JWT_SECRET", "change-me-in-production")
    jwt_expire_hours: int = int(os.environ.get("RAGBASE_JWT_EXPIRE_HOURS", "72"))
    allow_registration: bool = _bool("RAGBASE_ALLOW_REGISTRATION", True)

    # --- Chat model (any OpenAI-compatible endpoint) ---
    # No key => demo mode: a local extractive engine answers from the
    # retrieved chunks so the full product flow works without any API.
    llm_api_key: str = os.environ.get("RAGBASE_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    llm_base_url: str = (
        os.environ.get("RAGBASE_LLM_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or "https://api.openai.com/v1"
    )
    llm_model: str = os.environ.get("RAGBASE_LLM_MODEL", "gpt-4o-mini")

    # --- Embeddings ---
    # Endpoints like DeepSeek that offer no embedding API can simply leave
    # this empty: retrieval falls back to local feature-hash vectors (still
    # hybrid: BM25 lexical search always runs alongside).
    embedding_api_key: str = os.environ.get("RAGBASE_EMBEDDING_API_KEY", "")
    embedding_base_url: str = os.environ.get("RAGBASE_EMBEDDING_BASE_URL", "https://api.openai.com/v1")
    embedding_model: str = os.environ.get("RAGBASE_EMBEDDING_MODEL", "text-embedding-3-small")
    embedding_dim: int = int(os.environ.get("RAGBASE_EMBEDDING_DIM", "1536"))

    # --- Limits ---
    max_docs_per_user: int = int(os.environ.get("RAGBASE_MAX_DOCS_PER_USER", "0"))  # 0 = unlimited
    max_upload_mb: int = int(os.environ.get("RAGBASE_MAX_UPLOAD_MB", "25"))
    chunk_size: int = int(os.environ.get("RAGBASE_CHUNK_SIZE", "900"))
    chunk_overlap: int = int(os.environ.get("RAGBASE_CHUNK_OVERLAP", "150"))
    top_k: int = int(os.environ.get("RAGBASE_TOP_K", "6"))
    retrieval_threshold: float = float(os.environ.get("RAGBASE_RETRIEVAL_THRESHOLD", "0.15"))
    # A question about the corpus as a whole ("which singers are in here?")
    # cannot be answered from six passages — the model just reports whichever
    # six scored highest and presents them as the complete list. Such questions
    # get a wider net plus a one-line-per-document inventory.
    list_top_k: int = int(os.environ.get("RAGBASE_LIST_TOP_K", "20"))
    inventory_docs: int = int(os.environ.get("RAGBASE_INVENTORY_DOCS", "200"))
    # total characters of retrieved text handed to the model, inventory included
    context_chars: int = int(os.environ.get("RAGBASE_CONTEXT_CHARS", "24000"))

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "ragbase.db")

    @property
    def chat_demo(self) -> bool:
        """True when no chat model is configured (local extractive answers)."""
        return not self.llm_api_key.strip()

    @property
    def embedding_local(self) -> bool:
        """True when no embedding API is configured (feature-hash vectors)."""
        return not self.embedding_api_key.strip()

    @property
    def demo_mode(self) -> bool:
        return self.chat_demo


settings = Settings()
