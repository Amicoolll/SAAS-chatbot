import uuid
from sqlalchemy import String, DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector

from app.core.config import settings
from app.db.session import Base

EMBED_DIM = settings.EMBED_DIM

class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String, index=True)
    user_id: Mapped[str] = mapped_column(String, index=True)

    drive_file_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)
    mime_type: Mapped[str] = mapped_column(String)
    modified_time: Mapped[str] = mapped_column(String)
    web_view_link: Mapped[str] = mapped_column(String, default="")

    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String, index=True)
    user_id: Mapped[str] = mapped_column(String, index=True)

    document_id: Mapped[str] = mapped_column(String, ForeignKey("documents.id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)

    embedding: Mapped[list[float]] = mapped_column(Vector(EMBED_DIM))

    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now())



#Mark: saving the conversations

#conversation table creating the ID for every conversation and saving it to the message table 
# class Conversation(Base):
#     __tablename__ = "conversations"

#     id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
#     tenant_id: Mapped[str] = mapped_column(String, index=True)
#     user_id: Mapped[str] = mapped_column(String, index=True)

#     title: Mapped[str] = mapped_column(String, default="New chat")
#     created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now())
#     updated_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

# #Message table is saving the converation 

# class Message(Base):
#     __tablename__ = "messages"

#     id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
#     tenant_id: Mapped[str] = mapped_column(String, index=True)
#     user_id: Mapped[str] = mapped_column(String, index=True)

#     conversation_id: Mapped[str] = mapped_column(String, ForeignKey("conversations.id"), index=True)
#     role: Mapped[str] = mapped_column(String)  # "user" or "assistant" or "system"
#     content: Mapped[str] = mapped_column(Text)

#     created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now())