from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String, func
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

MODEL_PROVIDERS = ("openai", "local")


class LlmModel(Base):
    """답변 모델 레지스트리의 한 행. 마이그레이션 0018의 WHY 참조 - .env 목록을
    덧씌우는 표라 행이 없는 .env 모델도 허가된 모델이다(app/llm/catalog.py)."""

    __tablename__ = "llm_models"
    __table_args__ = (
        CheckConstraint("provider IN ('openai', 'local')", name="ck_llm_models_provider"),
        Index("uq_llm_models_default", "is_default", unique=True, postgresql_where=sa_text("is_default")),
    )

    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="openai", server_default="openai")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=sa_text("true"))
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_text("false"))
    supports_vision: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    supports_reasoning: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
