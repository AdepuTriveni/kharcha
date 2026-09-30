"""Alembic upgrade/downgrade against a real Postgres + pgvector (testcontainers)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from testcontainers.community.postgres import PostgresContainer

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]
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


@pytest.fixture(scope="module")
def postgres() -> Iterator[PostgresContainer]:
    with PostgresContainer("pgvector/pgvector:pg17", driver=None) as pg:
        yield pg


def _alembic_config(async_url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", async_url)
    return cfg


def _tables(sync_url: str) -> set[str]:
    engine = sa.create_engine(sync_url)
    try:
        return set(sa.inspect(engine).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()


def test_upgrade_and_downgrade(postgres: PostgresContainer) -> None:
    base_url = postgres.get_connection_url()  # postgresql://...
    async_url = base_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    sync_url = base_url.replace("postgresql://", "postgresql+psycopg://", 1)
    cfg = _alembic_config(async_url)

    command.upgrade(cfg, "head")
    assert _tables(sync_url) == CORE_TABLES

    command.downgrade(cfg, "base")
    assert _tables(sync_url) == set()

    command.upgrade(cfg, "head")
    assert _tables(sync_url) == CORE_TABLES
