from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    DATABASE_URL: str

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


settings = Settings()
