from fastapi import FastAPI
from fastapi.responses import Response
from sqlalchemy import text

from app.db.session import engine, Base
from app.db import models  # <-- IMPORTANT
from app.services.drive.oauth import router as drive_oauth_router
from app.services.drive.routes import router as drive_routes
from app.api.demo import router as demo_router
from app.api.chat import router as chat_router
from app.api.index import router as index_router
from app.api.chat_pg import router as chat_pg_router
from app.api.agents import router as agents_router
from app.db import models_chat
from app.api.conversations import router as conversations_router
app = FastAPI(title="Enterprise Drive Chatbot")

app.include_router(drive_oauth_router)
app.include_router(drive_routes)
app.include_router(demo_router)
app.include_router(chat_router)
app.include_router(index_router)
app.include_router(chat_pg_router)
app.include_router(agents_router)
app.include_router(conversations_router)

@app.on_event("startup")
def startup():
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(bind=engine)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)