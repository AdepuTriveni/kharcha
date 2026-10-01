"""Personal MCP tokens for the user's own assistants (PROJECT_SPEC §24).

Read-only, revocable, 90-day expiry; only the sha256 of the token is stored.
(The §8.1 analyst views move to 0005_analyst.)

Revision ID: 0004_mcp_tokens
Revises: 0003_corrections
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_mcp_tokens"
down_revision: str | None = "0003_corrections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE mcp_tokens (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL REFERENCES users(id),
          token_hash TEXT NOT NULL UNIQUE,
          label TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          expires_at TIMESTAMPTZ NOT NULL,
          revoked_at TIMESTAMPTZ,
          last_used_at TIMESTAMPTZ)
        """
    )
    op.execute("CREATE INDEX ON mcp_tokens (user_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mcp_tokens")
