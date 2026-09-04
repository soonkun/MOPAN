"""한 행짜리 브랜딩 표 - 이 배포의 화면이 자기를 뭐라고 부르는가.

MOPAN은 가져다 자기 것으로 만드는 바탕 시스템이다. 사이드바의 제목, 새 대화
첫 화면의 문구, 추천 질문은 그 "자기 것"의 첫인상인데 코드에 박혀 있었다.

싱글턴인 이유: 배포당 브랜딩은 하나다. PK가 Boolean이고 CHECK (id)라서 행은
`true` 하나만 존재할 수 있다 - "어느 행이 화면인가"가 코드의 암묵이 아니라
스키마의 사실이 된다.

모든 값이 NULL 허용인 것도 의도다. NULL은 "기본값(코드의 문구)을 쓴다"이고,
빈 문자열과 다르다 - 관리자가 제목을 지우면 MOPAN으로 돌아가는 것이지 빈
제목이 되는 것이 아니다. 마스코트 이미지는 행이 아니라 업로드 디렉터리에
산다(app/branding/router.py) - 바이트 덩어리를 행에 넣으면 이 행을 읽는 모든
조회가 그 무게를 진다.
"""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Branding(Base):
    __tablename__ = "branding"
    __table_args__ = (CheckConstraint("id", name="ck_branding_singleton"),)

    id: Mapped[bool] = mapped_column(
        Boolean, primary_key=True, default=True, server_default=text("true")
    )
    app_title: Mapped[str | None] = mapped_column(String(60), nullable=True)
    tagline_primary: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tagline_secondary: Mapped[str | None] = mapped_column(String(300), nullable=True)
    suggested_questions: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
