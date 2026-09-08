"""llm_models - 답변 모델 레지스트리 (허가·기본·기능 플래그·로컬 GPU 모델)

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-08
"""
import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

# WHY. 모델 목록이 .env의 ANSWER_MODELS 한 줄이라, 사용자에게 어떤 모델을 허가할지
# 관리자가 화면에서 정할 수 없었고(소유자 요구 2026-09-08), 서버의 GPU에 띄운
# Ollama 모델(gpt-oss:120b, gemma4:31b …)을 쓸 길도 없었다. 이 표는 .env 목록을
# 대체하지 않고 덧씌운다: .env 모델은 행이 없어도 허가된 채 보이고, 행은 허가를
# 끄거나 기본을 정하거나 기능 플래그를 고칠 때, 그리고 로컬 모델을 등록할 때 생긴다.
# 그래서 빈 표 = 지금까지의 동작(app/llm/catalog.py).


def upgrade() -> None:
    op.create_table(
        "llm_models",
        sa.Column("id", sa.String(200), primary_key=True),
        sa.Column("label", sa.String(200), nullable=True),
        sa.Column("provider", sa.String(20), nullable=False, server_default="openai"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        # NULL = 이름으로 유추(config.py의 접두어 표). 관리자가 토글하면 값이 생긴다.
        sa.Column("supports_vision", sa.Boolean(), nullable=True),
        sa.Column("supports_reasoning", sa.Boolean(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("provider IN ('openai', 'local')", name="ck_llm_models_provider"),
    )
    # 기본 모델은 하나 - 프롬프트·워크플로우 버전의 is_active와 같은 부분 유니크.
    op.create_index(
        "uq_llm_models_default", "llm_models", ["is_default"], unique=True,
        postgresql_where=sa.text("is_default"),
    )


def downgrade() -> None:
    op.drop_index("uq_llm_models_default", table_name="llm_models")
    op.drop_table("llm_models")
