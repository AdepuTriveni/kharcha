"""AI tables (PROJECT_SPEC §8, 0002_ai): agent runs, memories, risk, bandit, models, evals.

Revision ID: 0002_ai
Revises: 0001_core
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_ai"
down_revision: str | None = "0001_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# One statement per op.execute: asyncpg cannot run several statements in one prepared call.
UPGRADE = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    """
    CREATE TABLE agent_runs (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
      task_id TEXT, agent TEXT NOT NULL, prompt_version TEXT NOT NULL, model TEXT NOT NULL,
      started_at TIMESTAMPTZ NOT NULL, finished_at TIMESTAMPTZ,
      status TEXT NOT NULL,
      transcript JSONB NOT NULL,
      input_tokens INT, output_tokens INT, cost_micros BIGINT DEFAULT 0)
    """,
    "CREATE INDEX ON agent_runs (user_id, agent, started_at)",
    """
    CREATE TABLE memories (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
      kind TEXT NOT NULL,
      content TEXT NOT NULL, embedding vector(768),
      confidence REAL NOT NULL DEFAULT 0.7, source_run_id TEXT,
      due_at TIMESTAMPTZ, status TEXT NOT NULL DEFAULT 'ACTIVE',
      last_used_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now())
    """,
    "CREATE INDEX ON memories USING hnsw (embedding vector_cosine_ops)",
    "CREATE INDEX ON memories (user_id, kind, status)",
    """
    CREATE TABLE risk_scores (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
      scored_at TIMESTAMPTZ NOT NULL, window_start TIMESTAMPTZ NOT NULL,
      window_end TIMESTAMPTZ NOT NULL, probability REAL NOT NULL, model_version TEXT NOT NULL,
      features JSONB NOT NULL)
    """,
    "CREATE INDEX ON risk_scores (user_id, scored_at)",
    """
    CREATE TABLE nudge_decisions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
      risk_score_id TEXT REFERENCES risk_scores(id), context_bucket TEXT NOT NULL,
      arm TEXT NOT NULL, propensity REAL NOT NULL, policy_version TEXT NOT NULL,
      decided_at TIMESTAMPTZ NOT NULL, reward REAL, reward_components JSONB,
      rewarded_at TIMESTAMPTZ)
    """,
    """
    CREATE TABLE bandit_state (scope TEXT NOT NULL, context_bucket TEXT NOT NULL,
      arm TEXT NOT NULL, alpha REAL NOT NULL DEFAULT 1, beta REAL NOT NULL DEFAULT 1,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY (scope, context_bucket, arm))
    """,
    """
    CREATE TABLE model_versions (id TEXT PRIMARY KEY, kind TEXT NOT NULL, base_model TEXT,
      artifact_uri TEXT NOT NULL, sha256 TEXT NOT NULL, eval_report JSONB NOT NULL,
      status TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())
    """,
    """
    CREATE TABLE eval_runs (id TEXT PRIMARY KEY, suite TEXT NOT NULL, subject TEXT NOT NULL,
      metrics JSONB NOT NULL, git_sha TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now())
    """,
]

TABLES_IN_DROP_ORDER = [
    "eval_runs",
    "model_versions",
    "bandit_state",
    "nudge_decisions",
    "risk_scores",
    "memories",
    "agent_runs",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for table in TABLES_IN_DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table}")
