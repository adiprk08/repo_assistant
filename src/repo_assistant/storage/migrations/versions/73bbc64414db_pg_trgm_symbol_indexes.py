"""pg_trgm symbol indexes

Revision ID: 73bbc64414db
Revises: cefe81ec8d3e
Create Date: 2026-07-10 14:06:04.952175

Enables trigram fuzzy matching on symbol names for the symbol retrieval channel
(docs/adr/0004, docs/ARCHITECTURE.md §5). GIN trigram indexes keep both exact
(ILIKE) and similarity (%) lookups fast as the symbol table grows.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "73bbc64414db"
down_revision: str | Sequence[str] | None = "cefe81ec8d3e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_symbols_name_trgm ON symbols USING gin (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_symbols_qualified_name_trgm "
        "ON symbols USING gin (qualified_name gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_symbols_qualified_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_symbols_name_trgm")
    # Leave the pg_trgm extension in place; other objects may depend on it.
