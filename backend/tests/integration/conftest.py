"""Shared fixtures for integration tests (Docker required)."""

import socket
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

BACKEND = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class PgUrls:
    async_url: str
    sync_url: str


def alembic_config(async_url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", async_url)
    return cfg


def pg_urls(container: PostgresContainer) -> PgUrls:
    base = container.get_connection_url()  # postgresql://...
    return PgUrls(
        async_url=base.replace("postgresql://", "postgresql+asyncpg://", 1),
        sync_url=base.replace("postgresql://", "postgresql+psycopg://", 1),
    )


@pytest.fixture(scope="module")
def postgres() -> Iterator[PostgresContainer]:
    with PostgresContainer("pgvector/pgvector:pg17", driver=None) as pg:
        yield pg


@pytest.fixture(scope="module")
def migrated_db(postgres: PostgresContainer) -> PgUrls:
    urls = pg_urls(postgres)
    command.upgrade(alembic_config(urls.async_url), "head")
    return urls


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def kafka() -> Iterator[str]:
    """Single-node KRaft broker from the same image as infra/docker-compose.yml.

    The host port is fixed up front so the advertised listener matches it.
    """
    port = _free_port()
    container = (
        DockerContainer("apache/kafka:4.0.0")
        .with_bind_ports(9092, port)
        .with_envs(
            KAFKA_NODE_ID="1",
            KAFKA_PROCESS_ROLES="broker,controller",
            KAFKA_LISTENERS="PLAINTEXT://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093",
            KAFKA_ADVERTISED_LISTENERS=f"PLAINTEXT://localhost:{port}",
            KAFKA_LISTENER_SECURITY_PROTOCOL_MAP="PLAINTEXT:PLAINTEXT,CONTROLLER:PLAINTEXT",
            KAFKA_CONTROLLER_LISTENER_NAMES="CONTROLLER",
            KAFKA_CONTROLLER_QUORUM_VOTERS="1@localhost:9093",
            KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR="1",
            KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR="1",
            KAFKA_TRANSACTION_STATE_LOG_MIN_ISR="1",
            KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS="0",
            KAFKA_AUTO_CREATE_TOPICS_ENABLE="false",
        )
        .waiting_for(LogMessageWaitStrategy("Kafka Server started").with_startup_timeout(120))
    )
    with container:
        yield f"localhost:{port}"
