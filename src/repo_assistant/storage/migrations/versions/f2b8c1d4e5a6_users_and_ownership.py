"""users, web sessions, per-user repo library + owner FKs (docs/adr/0023)

Revision ID: f2b8c1d4e5a6
Revises: e1c4f7a920d3
Create Date: 2026-07-14

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2b8c1d4e5a6"
down_revision: str | Sequence[str] | None = "e1c4f7a920d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("github_id", sa.BigInteger(), nullable=True),
        sa.Column("login", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("avatar_url", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("github_id"),
        sa.UniqueConstraint("login"),
    )

    op.create_table(
        "web_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_web_sessions_token", "web_sessions", ["token_hash"], unique=False)

    op.create_table(
        "user_repos",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("repo_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["repo_id"], ["repos.id"]),
        sa.PrimaryKeyConstraint("user_id", "repo_id"),
    )
    op.create_index("ix_user_repos_repo", "user_repos", ["repo_id"], unique=False)

    # Owner FKs on existing tables — nullable so pre-auth rows migrate cleanly.
    op.add_column("chat_sessions", sa.Column("user_id", sa.UUID(), nullable=True))
    op.create_foreign_key("fk_chat_sessions_user", "chat_sessions", "users", ["user_id"], ["id"])
    op.create_index(
        "ix_chat_sessions_user_repo", "chat_sessions", ["user_id", "repo_id"], unique=False
    )

    op.add_column("api_keys", sa.Column("user_id", sa.UUID(), nullable=True))
    op.create_foreign_key("fk_api_keys_user", "api_keys", "users", ["user_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_api_keys_user", "api_keys", type_="foreignkey")
    op.drop_column("api_keys", "user_id")

    op.drop_index("ix_chat_sessions_user_repo", table_name="chat_sessions")
    op.drop_constraint("fk_chat_sessions_user", "chat_sessions", type_="foreignkey")
    op.drop_column("chat_sessions", "user_id")

    op.drop_index("ix_user_repos_repo", table_name="user_repos")
    op.drop_table("user_repos")

    op.drop_index("ix_web_sessions_token", table_name="web_sessions")
    op.drop_table("web_sessions")

    op.drop_table("users")
