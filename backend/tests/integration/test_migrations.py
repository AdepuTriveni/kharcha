"""Alembic upgrade/downgrade against a real Postgres + pgvector, and ORM <-> schema drift."""

import pytest
import sqlalchemy as sa
from alembic import command
from testcontainers.community.postgres import PostgresContainer

from kharcha_common.db import Base
from kharcha_common.db import models as _models  # noqa: F401 - registers tables
from tests.integration.conftest import alembic_config, pg_urls

pytestmark = pytest.mark.integration

CORE_TABLES = {
    "users",
    "devices",
    "raw_events",
    "processed_events",
    "parse_rules",
    "merchants",
    "merchant_aliases",
    "transactions",
    "transaction_sources",
    "cash_ledger",
    "budgets",
    "forecasts",
    "alerts_sent",
    "alert_feedback",
    "refund_cases",
}
AI_TABLES = {
    "agent_runs",
    "memories",
    "risk_scores",
    "nudge_decisions",
    "bandit_state",
    "model_versions",
    "eval_runs",
}
ALL_TABLES = CORE_TABLES | AI_TABLES | {"user_merchant_overrides", "mcp_tokens"}


def _tables(sync_url: str) -> set[str]:
    engine = sa.create_engine(sync_url)
    try:
        return set(sa.inspect(engine).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()


def test_upgrade_and_downgrade(postgres: PostgresContainer) -> None:
    urls = pg_urls(postgres)
    cfg = alembic_config(urls.async_url)

    command.upgrade(cfg, "head")
    assert _tables(urls.sync_url) == ALL_TABLES

    command.downgrade(cfg, "0002_ai")
    assert _tables(urls.sync_url) == CORE_TABLES | AI_TABLES

    command.downgrade(cfg, "0001_core")
    assert _tables(urls.sync_url) == CORE_TABLES

    command.downgrade(cfg, "base")
    assert _tables(urls.sync_url) == set()

    command.upgrade(cfg, "head")
    assert _tables(urls.sync_url) == ALL_TABLES


def test_orm_models_match_migrations(postgres: PostgresContainer) -> None:
    urls = pg_urls(postgres)
    command.upgrade(alembic_config(urls.async_url), "head")
    engine = sa.create_engine(urls.sync_url)
    try:
        inspector = sa.inspect(engine)
        for table in Base.metadata.sorted_tables:
            db_cols = {c["name"]: c for c in inspector.get_columns(table.name)}
            assert set(db_cols) == {c.name for c in table.columns}, table.name
            for column in table.columns:
                assert db_cols[column.name]["nullable"] == column.nullable, (
                    f"{table.name}.{column.name} nullability differs"
                )
    finally:
        engine.dispose()
