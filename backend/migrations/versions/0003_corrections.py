"""Per-user merchant/category corrections (PROJECT_SPEC §11.3, FR-12).

User corrections "win for that user", so they live beside the global ``merchant_aliases``
instead of replacing them. (The §8.1 analyst views move to 0004_analyst.)

Revision ID: 0003_corrections
Revises: 0002_ai
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_corrections"
down_revision: str | None = "0002_ai"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE user_merchant_overrides (
          user_id TEXT NOT NULL REFERENCES users(id),
          alias TEXT NOT NULL,
          merchant_id TEXT REFERENCES merchants(id),
          category TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (user_id, alias))
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_merchant_overrides")
