import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

RESEARCH_STATUSES = ("queued", "planning", "searching", "gap", "synthesis", "done", "failed", "cancelled")
RESEARCH_TERMINAL = frozenset({"done", "failed", "cancelled"})

# 원본 store.LIMITS 그대로 (min, max, default).
BUDGET_LIMITS = {
    "sub_queries": (1, 12, 6),
    "top_k_per_query": (1, 15, 5),
    "gap_rounds": (0, 3, 1),
    "max_evidence_chunks": (5, 80, 24),
}


def clamp_budget(raw: dict | None) -> dict:
    out = {}
    for key, (lo, hi, default) in BUDGET_LIMITS.items():
        try:
            value = int((raw or {}).get(key, default))
        except (TypeError, ValueError):
            value = default
        out[key] = max(lo, min(hi, value))
    return out


class ResearchProject(Base):
    """딥 리서치 '방' - 지침(버전 이력)·예산·범위·모델을 묶는 단위(원본 CR-62)."""

    __tablename__ = "research_projects"
    __table_args__ = (
        UniqueConstraint("name", name="uq_research_projects_name"),
        Index("ix_research_projects_created_by", "created_by"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    budget: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reasoning_effort: Mapped[str | None] = mapped_column(String(20), nullable=True)
    gap_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    collection_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb"))
    # 플래너의 "관점 예시" 한 줄(원본 CR-62 planner_hint) - 하위 질의를 어느 축으로 나눌지.
    planner_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 어느 템플릿에서 태어났는지(app/research/templates.py). 화면 표시용, 동작에는 안 쓴다.
    template_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ResearchInstruction(Base):
    __tablename__ = "research_instructions"
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_research_instructions_version"),
        Index("uq_research_instructions_active", "project_id", unique=True, postgresql_where=sa_text("is_active")),
        Index("ix_research_instructions_created_by", "created_by"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("research_projects.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_text("false"))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ResearchRun(Base):
    """한 번의 실행. 원본 turns에 대응. steps가 진행 로그(원본 CR-65의 steps_json)."""

    __tablename__ = "research_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','planning','searching','gap','synthesis','done','failed','cancelled')",
            name="ck_research_runs_status",
        ),
        Index("ix_research_runs_project_created", "project_id", "created_at"),
        Index("ix_research_runs_created_by_status", "created_by", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("research_projects.id", ondelete="CASCADE"), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    attachment_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachment_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", server_default="queued")
    steps: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb"))
    report: Mapped[str | None] = mapped_column(Text, nullable=True)
    sources: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb"))
    sub_queries: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=sa_text("'[]'::jsonb"))
    # 검토 대상 요약(첨부를 먼저 읽어 뽑은 구조화 요약). 없으면 None - 첨부 없는 질문형 실행.
    scope: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_text("false"))
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reasoning_effort: Mapped[str | None] = mapped_column(String(20), nullable=True)
    usage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=sa_text("'{}'::jsonb"))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
