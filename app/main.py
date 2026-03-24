import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.core.config import settings
from app.db.session import engine, Base
from app.db import models  # noqa: F401 — register models
from app.db import models_pipeline  # noqa: F401 — register pipeline_state table
from app.services.drive.oauth import router as drive_oauth_router
from app.services.drive.routes import router as drive_routes
from app.api.demo import router as demo_router
from app.api.chat import router as chat_router
from app.api.index import router as index_router
from app.api.chat_pg import router as chat_pg_router
from app.api.agents import router as agents_router
from app.db import models_chat  # noqa: F401
from app.api.conversations import router as conversations_router
from app.api.pipeline import router as pipeline_router
from app.api.documents import router as documents_router

logger = logging.getLogger(__name__)

app = FastAPI(title="Enterprise Drive Chatbot")


def _ensure_pipeline_progress_columns() -> None:
    """
    Backward-compatible schema patch:
    create progress columns when app code is newer than deployed DB schema.
    """
    statements = [
        "ALTER TABLE pipeline_state ADD COLUMN IF NOT EXISTS drive_sync_progress_json TEXT",
        "ALTER TABLE pipeline_state ADD COLUMN IF NOT EXISTS index_progress_json TEXT",
    ]
    try:
        with engine.begin() as conn:
            for stmt in statements:
                conn.execute(text(stmt))
    except Exception as e:
        # Keep startup alive; endpoints can still work without live progress.
        logger.warning("Skipping pipeline_state progress column patch: %s", e)

# CORS: allow frontend (different origin / ngrok / other network)
_origins = (
    ["*"]
    if settings.CORS_ORIGINS.strip() == "*"
    else [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(drive_oauth_router)
app.include_router(drive_routes)
app.include_router(demo_router)
app.include_router(chat_router)
app.include_router(index_router)
app.include_router(chat_pg_router)
app.include_router(agents_router)
app.include_router(conversations_router)
app.include_router(pipeline_router)
app.include_router(documents_router)


@app.on_event("startup")
def startup():
    if settings.CREATE_PGVECTOR_EXTENSION:
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        except ProgrammingError as e:
            # AWS RDS: only rds_superuser / master can CREATE EXTENSION
            logger.warning(
                "Skipping CREATE EXTENSION vector: %s. "
                "Enable pgvector once with an admin connection, e.g. "
                "CREATE EXTENSION IF NOT EXISTS vector; "
                "Then set CREATE_PGVECTOR_EXTENSION=false in env if you want to silence this.",
                e.orig if getattr(e, "orig", None) else e,
            )
    Base.metadata.create_all(bind=engine)
    _ensure_pipeline_progress_columns()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)
