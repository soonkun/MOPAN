"""branding 싱글턴 + users.nickname - 가져다 쓰는 사람의 화면과 호칭

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-05
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

# WHY. MOPAN은 가져다 자기 것으로 만드는 바탕 시스템인데, 정작 화면의 이름
# (사이드바의 MOPAN)과 새 대화 첫 화면의 문구·추천 질문이 코드에 박혀 있었다.
# branding은 한 행짜리 표다 - 배포당 브랜딩은 하나이고, CHECK (id)가 그것을
# 데이터베이스 수준에서 강제한다 (여러 행이 생기면 어느 행이 화면인지가
# 코드의 암묵에 떨어진다).
#
# 값이 전부 NULL 허용인 것은 의도다: NULL은 "기본값을 쓴다"이고, 기본값은
# 코드가 안다. 마이그레이션이 기본 문구를 데이터로 굳혀 두면 코드의 문구를
# 고칠 때마다 이미 배포된 행과 어긋난다.
#
# users.nickname은 호칭이다. 새 대화 화면과 잡담 응답이 "OO님, 안녕하세요"
# 라고 부를 수 있게 하고, 프로필에서 본인이 고친다.


def upgrade() -> None:
    op.create_table(
        "branding",
        sa.Column("id", sa.Boolean(), primary_key=True, server_default=sa.text("true")),
        sa.Column("app_title", sa.String(length=60), nullable=True),
        sa.Column("tagline_primary", sa.String(length=200), nullable=True),
        sa.Column("tagline_secondary", sa.String(length=300), nullable=True),
        sa.Column(
            "suggested_questions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("id", name="ck_branding_singleton"),
    )
    op.add_column("users", sa.Column("nickname", sa.String(length=60), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "nickname")
    op.drop_table("branding")
