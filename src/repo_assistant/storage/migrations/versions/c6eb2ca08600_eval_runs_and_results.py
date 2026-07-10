"""eval runs and results

Revision ID: c6eb2ca08600
Revises: 73bbc64414db
Create Date: 2026-07-10 20:25:27.740970

Note: autogenerate also proposed dropping the pg_trgm symbol indexes (they are
created via raw SQL in 73bbc64414db and so are invisible to model metadata). Those
drops were removed by hand — the trigram indexes must stay.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c6eb2ca08600"
down_revision: str | Sequence[str] | None = "73bbc64414db"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("overall", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("per_dataset", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "eval_results",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("dataset", sa.String(), nullable=False),
        sa.Column("question_id", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("ranking", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["eval_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_eval_results_run", "eval_results", ["run_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_eval_results_run", table_name="eval_results")
    op.drop_table("eval_results")
    op.drop_table("eval_runs")
