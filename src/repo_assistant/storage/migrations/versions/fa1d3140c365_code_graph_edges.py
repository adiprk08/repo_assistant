"""code graph edges

Revision ID: fa1d3140c365
Revises: c6eb2ca08600
Create Date: 2026-07-10 20:37:34.475455

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fa1d3140c365"
down_revision: str | Sequence[str] | None = "c6eb2ca08600"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "edges",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("snapshot_id", sa.UUID(), nullable=False),
        sa.Column("src", sa.String(), nullable=False),
        sa.Column("dst", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("src_file", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_edges_snapshot_src", "edges", ["snapshot_id", "src"], unique=False)
    op.create_index("ix_edges_snapshot_dst", "edges", ["snapshot_id", "dst"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_edges_snapshot_dst", table_name="edges")
    op.drop_index("ix_edges_snapshot_src", table_name="edges")
    op.drop_table("edges")
