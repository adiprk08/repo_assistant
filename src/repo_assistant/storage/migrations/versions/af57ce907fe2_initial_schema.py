"""initial schema

Revision ID: af57ce907fe2
Revises:
Create Date: 2026-07-09 02:23:12.959768

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "af57ce907fe2"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "repos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("url", sa.String(), nullable=False, unique=True),
        sa.Column("provider", sa.String(), nullable=False, server_default="github"),
        sa.Column("default_ref", sa.String(), nullable=False, server_default="main"),
        sa.Column("visibility", sa.String(), nullable=False, server_default="public"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("active_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "repo_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("repos.id"), nullable=False
        ),
        sa.Column("commit_sha", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("stats", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("indexed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    op.create_foreign_key(
        "fk_repos_active_snapshot_id_snapshots",
        "repos",
        "snapshots",
        ["active_snapshot_id"],
        ["id"],
    )

    op.create_table(
        "files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("snapshots.id"),
            nullable=False,
        ),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("language", sa.String(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_hash", sa.String(), nullable=False),
    )

    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "repo_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("repos.id"), nullable=False
        ),
        sa.Column("job_type", sa.String(), nullable=False),
        sa.Column("stage", sa.String(), nullable=False, server_default="pending"),
        sa.Column("state", sa.String(), nullable=False, server_default="queued"),
        sa.Column("progress", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("checkpoints", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("jobs")
    op.drop_table("files")
    op.drop_constraint("fk_repos_active_snapshot_id_snapshots", "repos", type_="foreignkey")
    op.drop_table("snapshots")
    op.drop_table("repos")
