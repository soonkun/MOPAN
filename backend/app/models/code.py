"""코드(코딩 에이전트) - 사용자별 프로필. 계약: docs/superpowers/plans/2026-09-24-code-agent.md

작업 공간·세션은 여기 없다. 서버 작업 공간은 디스크의 디렉터리 목록이 곧 목록이고, 세션은
OpenCode가 사용자 홈의 sqlite에 스스로 남긴다. DB가 알아야 하는 것은 두 가지뿐이다:
샌드박스 uid(그 사용자의 파일을 소유하는 리눅스 uid)와 내 컴퓨터 연결용 토큰의 해시.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CodeProfile(Base):
    __tablename__ = "code_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # 샌드박스 안에서 opencode와 그 bash가 실행되는 실제 uid. 사용자마다 다르고 재사용하지 않는다 -
    # 파일 소유권이 곧 격리다(app/code/runtime.py).
    sandbox_uid: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    # 내 컴퓨터 연결(컴패니언) 토큰의 sha256 hex. 원문은 만들 때 한 번만 보여 준다.
    bridge_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 코드 탭이 llm 프록시로 쓴 토큰 누계 {prompt_tokens, completion_tokens}(app/code/llm_proxy.py). 사용자 관리의
    # 토큰 사용량 열이 채팅(messages.usage)·딥 리서치(research_runs.usage)와 합쳐 보여 준다.
    usage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
