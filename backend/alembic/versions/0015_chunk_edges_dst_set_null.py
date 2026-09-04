"""chunk_edges.dst의 CASCADE를 SET NULL로 - 대상 문서 재적재가 간선을 지우지 않게

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-05
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

# WHY. 0014의 dst FK는 ON DELETE CASCADE였고, 그때는 맞았다 - 모든 간선이 문서
# 안을 가리켰고, 문서 재적재는 자기 간선을 어차피 전량 교체한다. 문서 간 해소가
# 생기면서 전제가 깨졌다: 실용신안법의 준용 간선이 특허법의 청크를 가리키는데,
# 특허법을 재적재하면 그 청크들이 지워지면서 **실용신안법의 간선 행이 통째로**
# 따라 지워졌다 (측정: 재적재 직후 실용신안법 cross-doc 간선 0개). 간선의 주인은
# 인용하는 문서이므로, 대상이 사라지면 행이 아니라 dst만 비워져 "미해소"로
# 돌아가는 것이 맞다 - 그리고 대상 문서가 다시 색인되면 relink가 다시 잇는다.


def upgrade() -> None:
    op.drop_constraint("fk_chunk_edges_dst_chunk_id_chunks", "chunk_edges", type_="foreignkey")
    op.create_foreign_key(
        "fk_chunk_edges_dst_chunk_id_chunks",
        "chunk_edges",
        "chunks",
        ["dst_chunk_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_chunk_edges_dst_chunk_id_chunks", "chunk_edges", type_="foreignkey")
    op.create_foreign_key(
        "fk_chunk_edges_dst_chunk_id_chunks",
        "chunk_edges",
        "chunks",
        ["dst_chunk_id"],
        ["id"],
        ondelete="CASCADE",
    )
