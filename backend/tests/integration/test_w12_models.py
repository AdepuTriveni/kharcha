"""W12 on real Postgres: model registry (gate-enforced promotion, rollback) + phone manifest."""

from collections.abc import AsyncIterator, Mapping
from typing import Any

import fakeredis
import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from kharcha_common.events import EventEnvelope
from kharcha_common.model_registry import ModelStatus, PromotionError, promote, register
from kharcha_common.settings import Settings
from kharcha_ingest.auth import hash_api_key
from kharcha_ingest.main import create_app
from tests.integration.conftest import PgUrls

pytestmark = pytest.mark.integration

KEY = "k_w12"


class NullPublisher:
    async def publish_event(self, topic: str, event: EventEnvelope[Any]) -> None:
        return None

    async def publish_raw(
        self, topic: str, body: bytes, key: bytes | None, headers: Mapping[str, str]
    ) -> None:
        return None


def _manifest(version: str) -> dict[str, Any]:
    return {
        "version": version,
        "baseModel": "Qwen/Qwen2.5-0.5B-Instruct",
        "quant": "Q4_K_M",
        "file": f"kharcha-parser-{version}-q4_k_m.gguf",
        "sizeBytes": 398_000_000,
        "sha256": "ab" * 32,
        "minRamMb": 3000,
        "promptVersion": "parser/v1",
    }


@pytest.fixture
async def env(
    migrated_db: PgUrls,
) -> AsyncIterator[tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession]]]:
    engine = create_async_engine(migrated_db.async_url)
    async with engine.begin() as conn:
        await conn.execute(sa.text("DELETE FROM model_versions"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(
        Settings(env="test", database_url=migrated_db.async_url, api_keys={hash_api_key(KEY): "u"}),
        publisher=NullPublisher(),
        redis=fakeredis.FakeAsyncRedis(),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client,
    ):
        yield client, sessions
    await engine.dispose()


async def test_promotion_gate_rollback_and_manifest(
    env: tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = env
    auth = {"Authorization": f"Bearer {KEY}"}
    assert (await client.get("/v1/models/parser/latest", headers=auth)).status_code == 404

    async with sessions.begin() as session:
        failed = await register(
            session, _manifest("v1"), "https://h/v1.gguf", {"gate": {"passed": False}}
        )
        good = await register(
            session, _manifest("v2"), "https://h/v2.gguf", {"gate": {"passed": True}}
        )
        good3 = await register(
            session, _manifest("v3"), "https://h/v3.gguf", {"gate": {"passed": True}}
        )

    async with sessions.begin() as session:
        await promote(session, failed, ModelStatus.SHADOW)  # shadow needs no gate
        with pytest.raises(PromotionError, match="gate"):
            await promote(session, failed, ModelStatus.ACTIVE)
    async with sessions.begin() as session:
        await promote(session, good, ModelStatus.ACTIVE)

    manifest = (await client.get("/v1/models/parser/latest", headers=auth)).json()
    assert manifest == {
        "id": "parser-v2-q4_k_m",
        "version": "v2",
        "url": "https://h/v2.gguf",
        "sha256": "ab" * 32,
        "sizeBytes": 398_000_000,
        "minRamMb": 3000,
        "promptVersion": "parser/v1",
    }

    async with sessions.begin() as session:
        await promote(session, good3, ModelStatus.ACTIVE)
    async with sessions.begin() as session:
        await promote(session, good, ModelStatus.ACTIVE)  # one-click rollback
    async with sessions() as session:
        statuses: dict[str, str] = dict(
            (await session.execute(sa.text("SELECT id, status FROM model_versions"))).all()
        )
    assert statuses == {
        "parser-v1-q4_k_m": "SHADOW",
        "parser-v2-q4_k_m": "ACTIVE",
        "parser-v3-q4_k_m": "RETIRED",
    }
