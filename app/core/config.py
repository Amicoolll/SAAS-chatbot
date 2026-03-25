from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    DATABASE_URL: str
    # Try CREATE EXTENSION vector on startup (local/dev). On AWS RDS, app users often lack this
    # privilege — enable pgvector once as master/admin, then set this to false to skip the attempt.
    CREATE_PGVECTOR_EXTENSION: bool = True

    # OpenAI
    OPENAI_API_KEY: str | None = None
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_CHAT_MODEL: str = "gpt-4o-mini"

    # Embedding dimension must match OPENAI_EMBEDDING_MODEL (1536 for text-embedding-3-small)
    EMBED_DIM: int = 1536

    # RAG / indexing
    RAG_DISTANCE_THRESHOLD: float = 0.45
    EMBED_BATCH_SIZE: int = 64
    CHUNK_SIZE: int = 1200
    CHUNK_OVERLAP: int = 200
    RETRIEVAL_TOP_K: int = 8
    CHAT_HISTORY_LIMIT: int = 10

    # Multi-tenant: default when headers are not provided (dev only; use auth in prod)
    DEFAULT_TENANT_ID: str = "demo_tenant"
    DEFAULT_USER_ID: str = "demo_user"

    # CORS: comma-separated origins, e.g. "http://localhost:3000,https://myapp.com" or "*" for all (dev)
    CORS_ORIGINS: str = "*"
    # Optional frontend base URL for OAuth callback redirect, e.g. "https://app.example.com"
    FRONTEND_URL: str | None = None
    # Logging
    LOG_LEVEL: str = "INFO"

    # Google Drive API: per-request socket timeout (seconds). Without this, large
    # PDFs / slow networks can look "hung" forever on a single read.
    DRIVE_HTTP_TIMEOUT_SEC: int = 300
    # Retries for each chunked download request (transient 5xx / connection errors).
    DRIVE_DOWNLOAD_NUM_RETRIES: int = 5
    # Chunk size for resumable media download (bytes). Smaller = more round trips,
    # shorter stall if one chunk times out.
    DRIVE_DOWNLOAD_CHUNKSIZE: int = 32 * 1024 * 1024


settings = Settings()
