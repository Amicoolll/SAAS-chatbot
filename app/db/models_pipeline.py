"""Per-tenant/user pipeline status for Drive sync and indexing (background jobs)."""
import uuid
from sqlalchemy import String, DateTime, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class PipelineState(Base):
    """
    One row per (tenant_id, user_id). Updated when background drive sync / index run.
    Frontend polls GET /pipeline/status to know when it is safe to index or chat.
    """

    __tablename__ = "pipeline_state"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_pipeline_tenant_user"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String, index=True)
    user_id: Mapped[str] = mapped_column(String, index=True)

    drive_sync_status: Mapped[str] = mapped_column(String, default="idle")  # idle|running|success|error
    drive_sync_started_at: Mapped["DateTime | None"] = mapped_column(DateTime(timezone=True), nullable=True)
    drive_sync_finished_at: Mapped["DateTime | None"] = mapped_column(DateTime(timezone=True), nullable=True)
    drive_sync_result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    drive_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Live progress while drive_sync_status == "running" (JSON). Poll GET /pipeline/status.
    drive_sync_progress_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    index_status: Mapped[str] = mapped_column(String, default="idle")  # idle|running|success|error
    index_started_at: Mapped["DateTime | None"] = mapped_column(DateTime(timezone=True), nullable=True)
    index_finished_at: Mapped["DateTime | None"] = mapped_column(DateTime(timezone=True), nullable=True)
    index_result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    index_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Live progress while index_status == "running" (JSON). Poll GET /pipeline/status.
    index_progress_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    updated_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
