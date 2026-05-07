from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_DEFAULT_QUERY_DOMAINS: list[str] = [
    "medical",
    "logistics",
    "support",
    "hr",
    "finance",
    "legal",
    "general",
    "multi_domain",
]

_DEFAULT_QUERY_INTENTS: list[str] = [
    "faq",
    "search",
    "summarization",
    "troubleshooting",
    "status_lookup",
    "policy_lookup",
    "comparison",
    "analysis",
    "workflow_help",
]


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

    # Hybrid RAG: dense (pgvector) + Postgres FTS, fused with RRF. Off by default
    # so existing deployments behave exactly like pure vector retrieval.
    ENABLE_HYBRID_RAG: bool = False
    # When True (and ENABLE_HYBRID_RAG), pick hybrid vs vector per analyze_query().
    # When False, ENABLE_HYBRID_RAG alone forces hybrid for every message (legacy).
    HYBRID_RAG_ANALYTICS_ROUTING: bool = True
    # Optional debug payload on POST /chat_pg (domain, intent, retrieval choice).
    CHAT_RETURN_QUERY_ROUTING: bool = False
    # Max candidates per leg before fusion (final count stays RETRIEVAL_TOP_K / k param).
    HYBRID_FTS_CANDIDATE_K: int = 30
    HYBRID_VECTOR_CANDIDATE_MIN: int = 20
    HYBRID_VECTOR_CANDIDATE_MULTIPLIER: int = 2
    HYBRID_RRF_K: int = 60
    # Postgres text search config; must match chunks.content_tsv definition (startup DDL).
    FTS_LANGUAGE: str = "simple"

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

    # Web search fallback (per-tenant, requires feature flag + this key)
    TAVILY_API_KEY: str | None = None
    WEB_SEARCH_MAX_RESULTS: int = 5
    # Master kill switch. Disabled by default — re-enable by setting
    # WEB_SEARCH_GLOBAL_ENABLED=true in .env when the feature is ready to ship.
    WEB_SEARCH_GLOBAL_ENABLED: bool = False

    # Admin API auth
    ADMIN_TOKEN: str | None = None

    # Feature flag in-process cache TTL
    FEATURE_FLAG_CACHE_TTL_SECONDS: int = 60

    # Aviation domain — partner API config (see AVIATION_PARTNER_API.md).
    # Empty defaults are intentional: the AviationDomain plugin requires
    # explicit configuration, and tests inject their own client.
    AIRLINE_API_BASE_URL: str = ""
    AIRLINE_API_TOKEN: str | None = None
    AIRLINE_API_TIMEOUT_SEC: float = 10.0
    AIRLINE_API_MAX_RETRIES: int = 2

    # Hard domain filter (semantic similarity). Applied only when tenant has
    # the strict_domain feature flag enabled. Below this cosine similarity
    # to the in-domain centroid, the question is refused as off-topic.
    # Typical values: 0.25 (lenient) ... 0.45 (strict). Default 0.35.
    DOMAIN_GUARD_THRESHOLD: float = 0.35

    # Query understanding (rule-based MVP): allowed labels from config / env JSON lists
    QUERY_UNDERSTANDING_DOMAINS: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_QUERY_DOMAINS),
    )
    QUERY_UNDERSTANDING_INTENTS: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_QUERY_INTENTS),
    )

    @field_validator("DRIVE_DOWNLOAD_CHUNKSIZE", mode="before")
    @classmethod
    def _coerce_drive_download_chunksize(cls, v: object) -> object:
        """
        Some CI/CD/YAML escaping can accidentally inject a trailing backslash into
        numeric env var values (e.g. "16777216\\").
        """
        if isinstance(v, str):
            v = v.strip().rstrip("\\")
        return v

    @model_validator(mode="after")
    def _ensure_query_understanding_labels(self) -> "Settings":
        if "general" not in self.QUERY_UNDERSTANDING_DOMAINS:
            self.QUERY_UNDERSTANDING_DOMAINS = [
                *self.QUERY_UNDERSTANDING_DOMAINS,
                "general",
            ]
        return self


settings = Settings()
