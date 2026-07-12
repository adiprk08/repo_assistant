"""chat sessions and messages

Revision ID: c7f1a2e93b45
Revises: b3d92a41c7e0
Create Date: 2026-07-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "c7f1a2e93b45"
down_revision: str | Sequence[str] | None = "b3d92a41c7e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("repo_id", sa.UUID(), nullable=False),
        sa.Column("snapshot_id", sa.UUID(), nullable=False),
        sa.Column("commit_sha", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "summary_covered_messages", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["repo_id"], ["repos.id"]),
        sa.ForeignKeyConstraint(["snapshot_id"], ["snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_sessions_repo", "chat_sessions", ["repo_id"], unique=False)

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("usage", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_chat_messages_session", "chat_messages", ["session_id", "seq"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_chat_messages_session", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_chat_sessions_repo", table_name="chat_sessions")
    op.drop_table("chat_sessions")
