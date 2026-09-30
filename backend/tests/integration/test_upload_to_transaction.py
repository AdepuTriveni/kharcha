"""End to end (W2): HTTP upload -> raw-events -> parser -> parsed-transactions -> transactions row.

Real Postgres and Kafka (testcontainers); the teacher LLM is faked. Re-uploading the same
events must not create duplicates, and an invalid parse must land in raw-events.DLT.
"""

import asyncio
import json
import time
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from confluent_kafka import Consumer
from confluent_kafka.admin import AdminClient, NewTopic  # type: ignore[attr-defined]
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from kharcha_common.ids import uuid7
from kharcha_common.settings import Settings
from kharcha_common.time import utcnow
from kharcha_common.topics import TOPIC_SPECS, dlt
from kharcha_ingest.auth import hash_api_key
from kharcha_ingest.main import create_app
from kharcha_processor.extraction import ExtractionResult
from kharcha_processor.main import build_broker
from tests.integration.conftest import PgUrls

pytestmark = pytest.mark.integration

USER = "u_it"
API_KEY = "khk_integration_test_key"
PAYMENT = "Paid Rs.10.00 to chaiwala@okaxis from A/c XX1234. UPI Ref 512345678901"
HALLUCINATED = "Paid Rs.99.00 to someone@okaxis. UPI Ref 512345678902"
OTP = "482913 is your OTP for a txn of Rs 10. Do not share"


class FakeTeacher:
    model_version = "fake#parser/v1"

    async def extract(
        self, *, sender: str | None, source_app: str | None, text: str
    ) -> ExtractionResult:
        amount = "10.00" if "chaiwala" in text else "9999"  # 9999 is not in the text
        return ExtractionResult.model_validate(
            {
                "isTransaction": True,
                "amount": amount,
                "direction": "DEBIT",
                "channel": "UPI",
                "status": "SUCCESS",
                "merchantRaw": "chaiwala@okaxis",
                "counterpartyVpa": "chaiwala@okaxis",
                "referenceId": "512345678901" if "chaiwala" in text else None,
                "accountHint": "1234",
            }
        )


def _create_topics(bootstrap: str) -> None:
    admin = AdminClient({"bootstrap.servers": bootstrap})
    names = [t.value for t in TOPIC_SPECS] + [dlt(t) for t in TOPIC_SPECS]
    futures = admin.create_topics(
        [NewTopic(n, num_partitions=3, replication_factor=1) for n in names]
    )
    for future in futures.values():
        future.result(timeout=30)


def _drain(bootstrap: str, topic: str, wait_s: float = 10.0) -> list[dict[str, Any]]:
    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": f"it-{uuid.uuid4()}",
            "auto.offset.reset": "earliest",
        }
    )
    consumer.subscribe([topic])
    out: list[dict[str, Any]] = []
    deadline = time.monotonic() + wait_s
    try:
        while time.monotonic() < deadline:
            message = consumer.poll(0.5)
            if message is not None and message.error() is None and (value := message.value()):
                out.append(json.loads(value))
    finally:
        consumer.close()
    return out


def _event(text: str) -> dict[str, Any]:
    posted = (utcnow() - timedelta(minutes=1)).isoformat()
    return {
        "eventId": str(uuid7()),
        "type": "RAW_NOTIFICATION",
        "occurredAt": posted,
        "payload": {
            "sourceApp": "com.phonepe.app",
            "sender": "AX-HDFCBK",
            "title": "Paid",
            "text": text,
            "postedAt": posted,
            "deviceId": "d_it",
            "redacted": True,
        },
    }


@pytest.fixture(scope="module")
def stack(migrated_db: PgUrls, kafka: str) -> Settings:
    _create_topics(kafka)
    engine = sa.create_engine(migrated_db.sync_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO users (id, display_name) VALUES (:id, 'IT')"), {"id": USER}
        )
    engine.dispose()
    return Settings(
        env="test",
        database_url=migrated_db.async_url,
        kafka_bootstrap_servers=kafka,
        api_keys={hash_api_key(API_KEY): USER},
    )


async def _count(engine: AsyncEngine, sql: str) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(sa.text(sql))).scalar_one())


async def _wait_for(engine: AsyncEngine, sql: str, expected: int) -> int:
    value = -1
    for _ in range(120):
        value = await _count(engine, sql)
        if value >= expected:
            return value
        await asyncio.sleep(0.5)
    return value


async def test_upload_creates_one_transaction(stack: Settings) -> None:
    broker = build_broker(stack, teacher=FakeTeacher())
    await broker.start()
    engine = create_async_engine(stack.database_url)
    app = create_app(stack)
    events = [_event(PAYMENT), _event(OTP), _event(HALLUCINATED)]
    headers = {"Authorization": f"Bearer {API_KEY}"}
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client,
        ):
            first = await client.post("/v1/events:batch", json={"events": events}, headers=headers)
            assert first.status_code == 200
            assert [r["status"] for r in first.json()["results"]] == ["ACCEPTED"] * 3

            parser_done = (
                "SELECT count(*) FROM processed_events WHERE consumer = 'processor.parser'"
            )
            assert await _wait_for(engine, "SELECT count(*) FROM transactions", 1) == 1
            # OTP is dropped and marked processed; the hallucination goes to the DLT instead.
            assert await _wait_for(engine, parser_done, 2) == 2

            again = await client.post("/v1/events:batch", json={"events": events}, headers=headers)
            assert [r["status"] for r in again.json()["results"]] == ["DUPLICATE"] * 3
            await asyncio.sleep(5)

        assert await _count(engine, "SELECT count(*) FROM raw_events") == 3
        assert await _count(engine, "SELECT count(*) FROM transactions") == 1
        assert await _count(engine, "SELECT count(*) FROM transaction_sources") == 1
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.text(
                        "SELECT user_id, amount_paise, direction, kind, channel, reference_id,"
                        " account_hint, parse_method FROM transactions"
                    )
                )
            ).one()
        assert tuple(row) == (
            USER,
            1000,
            "DEBIT",
            "SPEND",
            "UPI",
            "512345678901",
            "1234",
            "TEACHER_LLM",
        )
    finally:
        await broker.stop()
        await engine.dispose()

    clean = _drain(stack.kafka_bootstrap_servers, "clean-transactions")
    assert len({m["payload"]["transactionId"] for m in clean}) == 1  # republished, same id
    assert clean[0]["payload"]["amountPaise"] == 1000
    dead = _drain(stack.kafka_bootstrap_servers, "raw-events.DLT", wait_s=5)
    assert len({m["eventId"] for m in dead}) == 1
    assert dead[0]["payload"]["text"] == HALLUCINATED
