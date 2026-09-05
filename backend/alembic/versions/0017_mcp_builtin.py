"""mcp_servers.builtin - 기본 제공 서버는 삭제할 수 없다

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

# WHY. 동봉된 분류표 조회 MCP(/goods/mcp)는 "문서를 임베딩하면 그 안의 분류표가
# 곧바로 조회 도구가 된다"는, 이 시스템이 범용이 되는 성질 그 자체다. 그래서
# 예시가 아니라 기본 기능으로 승격한다: 부팅 시 자동 등록되고(app/mcp/seed.py),
# builtin=true인 행은 DELETE가 거부된다. 끄고 싶으면 비활성화(enabled=false)로 -
# 삭제와 달리 재기동 시딩이 존중하고, 언제든 되돌릴 수 있다.


def upgrade() -> None:
    op.add_column(
        "mcp_servers",
        sa.Column("builtin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("mcp_servers", "builtin")
