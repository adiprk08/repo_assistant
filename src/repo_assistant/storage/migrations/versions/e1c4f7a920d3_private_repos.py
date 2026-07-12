"""private repos: github installations + repo installation_id

Revision ID: e1c4f7a920d3
Revises: d5a3e1f6b208
Create Date: 2026-07-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1c4f7a920d3"
down_revision: str | Sequence[str] | None = "d5a3e1f6b208"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("repos", sa.Column("installation_id", sa.BigInteger(), nullable=True))
    op.create_table(
        "github_installations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("account_login", sa.String(), nullable=True),
        sa.Column("token_encrypted", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("installation_id"),
    )


def downgrade() -> None:
    op.drop_table("github_installations")
    op.drop_column("repos", "installation_id")
