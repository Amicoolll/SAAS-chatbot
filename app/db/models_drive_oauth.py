"""Persist Google Drive OAuth tokens so multi-worker / long jobs still see Drive as connected."""
import uuid
from sqlalchemy import String, Text, DateTime, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class DriveOAuthToken(Base):
    """
    One row per (tenant_id, user_id). Survives process restarts and is visible to all workers.
    In-memory TOKEN_STORE is a per-process cache filled from this table on demand.
    """

    __tablename__ = "drive_oauth_tokens"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_drive_oauth_tenant_user"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String, index=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
