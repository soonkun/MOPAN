"""agents -> workflows, versioned graphs, and every existing row converted

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-31

THE RENAME IS THE POINT, not a tidy-up. "에이전트" is retired: 워크플로우 is a graph a
person authored and 슈퍼 에이전트 is the mode where the model authors one. Renaming
the UI and leaving `agent` in the database and the code would hand the next
person exactly the confusion this slice exists to remove, so the tables, the
columns, the constraints, the indexes and the API paths move together.

`ALTER TABLE ... RENAME` throughout rather than create-copy-drop: it keeps every
row, every id and every foreign key that points at one, so `messages` written
before this migration still name the same thing afterwards.

**`agents.orchestrator` IS DROPPED.** That column is the one that mixed the two
layers - "a fixed procedure" was switching on "autonomous planning". A workflow is
by definition not autonomous planning; 슈퍼 에이전트 stays a per-conversation choice.

**EVERY EXISTING ROW IS CONVERTED, not discarded.** Each becomes version 1 of an
equivalent graph:

    input  ->  tool: rag (its allowed collections)  ->  answer

with `arguments.query` = `{{input.text}}`, so the executor runs exactly the search
`retrieve()` ran for that agent and `answer()` sees the same evidence. Prompt and
model are untouched columns, so they carry over unchanged.

**WHAT IS DELIBERATELY NOT CONVERTED, and it is a deviation from one sentence of
the design spec.** Section 6 also says "허용 도구가 있으면 병렬 tool 노드로 붙인다" -
attach an agent's allowed MCP tools as parallel tool nodes. That is NOT done here,
for two reasons that the same paragraph's stronger claim ("동작이 바뀌지 않는 변환")
depends on:

1. An agent's tool list is a PERMISSION list. Nothing called those tools
   automatically before this migration - the user picked one by hand, or a plan
   named one. Turning the list into nodes would call every one of them on every
   question, which changes behaviour rather than preserving it.
2. An MCP tool node needs ARGUMENTS, and there are none to write. A `write` or
   `destructive` tool invoked with `{}` on every question is precisely the
   unattended call the approval gate exists to prevent.

The tools stay where they were - in `workflow_tools`, as the boundary - and an
admin adds a tool node with real arguments on the canvas. The graph an admin then
edits is the one this migration wrote, which is what section 6 was after.
"""

import json
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def _graph(collection_names: list[str]) -> dict:
    """The behaviour-preserving graph for one converted row.

    `collections` empty means the whole catalogue, written out by
    `validate_graph` at load time against whatever this workflow may reach - the
    same rule an unrestricted agent followed, where an empty `agent_collections`
    meant unrestricted. So an unrestricted agent gets an empty list here and
    keeps searching everything; a restricted one gets its names and keeps
    searching only those.

    Coordinates are laid out left to right at the spacing the canvas uses, so a
    converted workflow opens as a readable three-node row rather than a pile at
    the origin.
    """
    return {
        "nodes": [
            {"id": "input", "kind": "input", "label": "질문", "x": 0, "y": 0},
            {
                "id": "search",
                "kind": "tool",
                "label": "문서 검색",
                "tool": "rag",
                "collections": collection_names,
                "arguments": {"query": "{{input.text}}"},
                "x": 260,
                "y": 0,
            },
            {"id": "answer", "kind": "answer", "label": "답변", "x": 520, "y": 0},
        ],
        "edges": [
            {"from": "input", "to": "search"},
            {"from": "search", "to": "answer"},
        ],
    }


def upgrade() -> None:
    # -- the rename -------------------------------------------------------
    op.rename_table("agents", "workflows")
    op.rename_table("agent_collections", "workflow_collections")
    op.rename_table("agent_tools", "workflow_tools")
    op.alter_column("workflow_collections", "agent_id", new_column_name="workflow_id")
    op.alter_column("workflow_tools", "agent_id", new_column_name="workflow_id")
    op.alter_column("messages", "agent_name", new_column_name="workflow_name")

    # Constraints and indexes carry their old names through a table rename, and a
    # name that still says `agent` is the confusion this migration exists to
    # remove - so every one is renamed too. Raw SQL because alembic has no
    # rename-constraint operation.
    for old, new in (
        ("pk_agents", "pk_workflows"),
        ("uq_agents_name", "uq_workflows_name"),
        ("fk_agents_created_by_users", "fk_workflows_created_by_users"),
    ):
        op.execute(f'ALTER TABLE workflows RENAME CONSTRAINT "{old}" TO "{new}"')
    for old, new in (
        ("pk_agent_collections", "pk_workflow_collections"),
        ("fk_agent_collections_agent_id_agents", "fk_workflow_collections_workflow_id_workflows"),
        (
            "fk_agent_collections_collection_id_collections",
            "fk_workflow_collections_collection_id_collections",
        ),
    ):
        op.execute(f'ALTER TABLE workflow_collections RENAME CONSTRAINT "{old}" TO "{new}"')
    for old, new in (
        ("pk_agent_tools", "pk_workflow_tools"),
        ("fk_agent_tools_agent_id_agents", "fk_workflow_tools_workflow_id_workflows"),
        ("fk_agent_tools_tool_id_mcp_tools", "fk_workflow_tools_tool_id_mcp_tools"),
    ):
        op.execute(f'ALTER TABLE workflow_tools RENAME CONSTRAINT "{old}" TO "{new}"')
    op.execute('ALTER INDEX "ix_agents_created_by" RENAME TO "ix_workflows_created_by"')
    op.execute(
        'ALTER INDEX "ix_agent_collections_collection_id" '
        'RENAME TO "ix_workflow_collections_collection_id"'
    )
    op.execute('ALTER INDEX "ix_agent_tools_tool_id" RENAME TO "ix_workflow_tools_tool_id"')

    # -- the column that mixed the layers ---------------------------------
    op.drop_column("workflows", "orchestrator")

    # -- versions ---------------------------------------------------------
    op.create_table(
        "workflow_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        # Nodes, edges AND node coordinates. The coordinates had no column to live
        # in while this was `agents`, which is the whole reason the old canvas
        # could not store a layout.
        sa.Column("graph", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_versions"),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
            name="fk_workflow_versions_workflow_id_workflows",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_workflow_versions_created_by_users",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("workflow_id", "version", name="uq_workflow_versions_workflow_version"),
    )
    op.create_index("ix_workflow_versions_workflow_id", "workflow_versions", ["workflow_id"])
    op.create_index("ix_workflow_versions_created_by", "workflow_versions", ["created_by"])
    # Exactly one active version per workflow, as a database guarantee. Same
    # device as uq_prompts_name_active.
    op.create_index(
        "uq_workflow_versions_workflow_active",
        "workflow_versions",
        ["workflow_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    # Which version answered, beside `workflow_name`. An INTEGER, not a foreign
    # key, for the same reason the name is a string: a transcript must survive an
    # admin deleting the workflow it names.
    op.add_column("messages", sa.Column("workflow_version", sa.Integer(), nullable=True))

    # -- every existing row, converted ------------------------------------
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, created_by FROM workflows")).fetchall()
    for workflow_id, created_by in rows:
        names = [
            name
            for (name,) in connection.execute(
                sa.text(
                    "SELECT c.name FROM workflow_collections wc "
                    "JOIN collections c ON c.id = wc.collection_id "
                    "WHERE wc.workflow_id = :wid ORDER BY c.name"
                ),
                {"wid": workflow_id},
            ).fetchall()
        ]
        connection.execute(
            sa.text(
                "INSERT INTO workflow_versions (id, workflow_id, version, is_active, graph, note, created_by) "
                "VALUES (:id, :wid, 1, true, CAST(:graph AS jsonb), :note, :by)"
            ),
            {
                # Generated here rather than with gen_random_uuid(): pgcrypto is
                # not assumed anywhere else in this schema and 0001 does not
                # install it.
                "id": uuid.uuid4(),
                "wid": workflow_id,
                "graph": json.dumps(_graph(names), ensure_ascii=False),
                "note": "에이전트에서 자동 변환된 그래프입니다.",
                "by": created_by,
            },
        )


def downgrade() -> None:
    # Every pytest session opens with `downgrade base`, so this path runs
    # constantly and is not theoretical. The graphs are lost, which is honest:
    # `agents` has nowhere to put one.
    op.drop_column("messages", "workflow_version")
    op.drop_index("uq_workflow_versions_workflow_active", table_name="workflow_versions")
    op.drop_index("ix_workflow_versions_created_by", table_name="workflow_versions")
    op.drop_index("ix_workflow_versions_workflow_id", table_name="workflow_versions")
    op.drop_table("workflow_versions")

    op.add_column(
        "workflows",
        sa.Column("orchestrator", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    op.execute('ALTER INDEX "ix_workflow_tools_tool_id" RENAME TO "ix_agent_tools_tool_id"')
    op.execute(
        'ALTER INDEX "ix_workflow_collections_collection_id" '
        'RENAME TO "ix_agent_collections_collection_id"'
    )
    op.execute('ALTER INDEX "ix_workflows_created_by" RENAME TO "ix_agents_created_by"')
    for new, old in (
        ("pk_workflow_tools", "pk_agent_tools"),
        ("fk_workflow_tools_workflow_id_workflows", "fk_agent_tools_agent_id_agents"),
        ("fk_workflow_tools_tool_id_mcp_tools", "fk_agent_tools_tool_id_mcp_tools"),
    ):
        op.execute(f'ALTER TABLE workflow_tools RENAME CONSTRAINT "{new}" TO "{old}"')
    for new, old in (
        ("pk_workflow_collections", "pk_agent_collections"),
        ("fk_workflow_collections_workflow_id_workflows", "fk_agent_collections_agent_id_agents"),
        (
            "fk_workflow_collections_collection_id_collections",
            "fk_agent_collections_collection_id_collections",
        ),
    ):
        op.execute(f'ALTER TABLE workflow_collections RENAME CONSTRAINT "{new}" TO "{old}"')
    for new, old in (
        ("pk_workflows", "pk_agents"),
        ("uq_workflows_name", "uq_agents_name"),
        ("fk_workflows_created_by_users", "fk_agents_created_by_users"),
    ):
        op.execute(f'ALTER TABLE workflows RENAME CONSTRAINT "{new}" TO "{old}"')

    op.alter_column("messages", "workflow_name", new_column_name="agent_name")
    op.alter_column("workflow_tools", "workflow_id", new_column_name="agent_id")
    op.alter_column("workflow_collections", "workflow_id", new_column_name="agent_id")
    op.rename_table("workflow_tools", "agent_tools")
    op.rename_table("workflow_collections", "agent_collections")
    op.rename_table("workflows", "agents")
