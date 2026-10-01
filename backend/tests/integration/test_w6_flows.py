"""W6 on real Postgres: cash API + Undo, widget taps, ATM -> cash, user corrections (§9, §13)."""

import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import fakeredis
import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from kharcha_common.events import (
    Channel,
    Direction,
    EventEnvelope,
    ParsedTransactionEvent,
    ParsedTransactionPayload,
    ParseMethod,
    RawEvent,
    RawEventPayload,
    TxnStatus,
)
from kharcha_common.ids import uuid7
from kharcha_common.merchants import seed_merchants
from kharcha_common.settings import Settings
from kharcha_common.time import utcnow
from kharcha_common.topics import Topic
from kharcha_ingest.auth import hash_api_key
from kharcha_ingest.main import create_app
from kharcha_processor.extraction import ExtractionResult
from kharcha_processor.handlers import (
    ProcessorDeps,
    handle_cash_event,
    handle_parsed_transaction,
    handle_raw_event,
)
from tests.integration.conftest import PgUrls

pytestmark = pytest.mark.integration

USER, OTHER = "u_w6", "u_w6_other"
KEY, OTHER_KEY = "k_w6_test_key", "k_w6_other_key"
AUTH = {"Authorization": f"Bearer {KEY}"}
OTHER_AUTH = {"Authorization": f"Bearer {OTHER_KEY}"}


@dataclass
class QueuePublisher:
    queues: dict[str, list[bytes]] = field(default_factory=dict)

    async def publish_event(self, topic: str, event: EventEnvelope[Any]) -> None:
        self.queues.setdefault(str(topic), []).append(event.model_dump_json(by_alias=True).encode())

    async def publish_raw(
        self, topic: str, body: bytes, key: bytes | None, headers: Mapping[str, str]
    ) -> None:
        self.queues.setdefault(topic, []).append(body)

    def take(self, topic: Topic) -> list[bytes]:
        return self.queues.pop(topic.value, [])


class NoTeacher:
    model_version = "none"

    async def extract(
        self, *, sender: str | None, source_app: str | None, text: str
    ) -> ExtractionResult:
        raise AssertionError("not used")


@dataclass
class Env:
    client: httpx.AsyncClient
    publisher: QueuePublisher
    deps: ProcessorDeps
    sessions: async_sessionmaker[AsyncSession]

    async def drain_cash(self) -> int:
        bodies = self.publisher.take(Topic.CASH_EVENTS)
        for body in bodies:
            await handle_cash_event(body, self.deps)
        return len(bodies)


@pytest.fixture
async def env(migrated_db: PgUrls) -> AsyncIterator[Env]:
    engine = create_async_engine(migrated_db.async_url)
    async with engine.begin() as conn:
        for table in (
            "user_merchant_overrides",
            "alerts_sent",
            "cash_ledger",
            "transaction_sources",
            "transactions",
            "processed_events",
            "raw_events",
            "budgets",
            "users",
        ):
            await conn.execute(sa.text(f"DELETE FROM {table}"))
        await conn.execute(sa.text("DELETE FROM merchants WHERE left(id, 3) = 'um_'"))
        await conn.execute(
            sa.text("INSERT INTO users (id) VALUES (:a), (:b)"), {"a": USER, "b": OTHER}
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions.begin() as session:
        await seed_merchants(session)
    publisher = QueuePublisher()
    settings = Settings(
        env="test",
        database_url=migrated_db.async_url,
        api_keys={hash_api_key(KEY): USER, hash_api_key(OTHER_KEY): OTHER},
    )
    app = create_app(settings, publisher=publisher, redis=fakeredis.FakeAsyncRedis())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client,
    ):
        yield Env(client, publisher, ProcessorDeps(sessions, publisher, NoTeacher()), sessions)
    await engine.dispose()


async def _balance(env: Env, headers: dict[str, str] = AUTH) -> int:
    response = await env.client.get("/v1/cash/balance", headers=headers)
    assert response.status_code == 200
    value: int = response.json()["balancePaise"]
    return value


async def test_cash_entry_balance_and_undo(env: Env) -> None:
    entry_id = str(uuid7())
    body = {
        "entryId": entry_id,
        "entryType": "CASH_RECEIVED",
        "amountPaise": 50000,
        "note": "from mom",
        "occurredAt": utcnow().isoformat(),
    }
    first = await env.client.post("/v1/cash", json=body, headers=AUTH)
    assert first.status_code == 202
    await env.client.post("/v1/cash", json=body, headers=AUTH)  # client retry
    assert await env.drain_cash() == 2
    assert await _balance(env) == 50000

    spend = {**body, "entryId": str(uuid7()), "entryType": "CASH_SPEND", "amountPaise": 80000}
    await env.client.post("/v1/cash", json=spend, headers=AUTH)
    await env.drain_cash()
    response = await env.client.get("/v1/cash/balance", headers=AUTH)
    assert response.json()["text"] == "You logged ₹300.00 more than tracked cash"

    assert (
        await env.client.delete(f"/v1/cash/{spend['entryId']}", headers=OTHER_AUTH)
    ).status_code == 404
    assert (
        await env.client.delete(f"/v1/cash/{spend['entryId']}", headers=AUTH)
    ).status_code == 204
    assert await _balance(env) == 50000
    assert await _balance(env, OTHER_AUTH) == 0

    bad = {**body, "entryId": str(uuid7()), "entryType": "UNACCOUNTED"}
    assert (await env.client.post("/v1/cash", json=bad, headers=AUTH)).status_code == 422
    old = {
        **body,
        "entryId": str(uuid7()),
        "occurredAt": (utcnow() - timedelta(days=40)).isoformat(),
    }
    assert (await env.client.post("/v1/cash", json=old, headers=AUTH)).status_code == 422


async def test_widget_tap_logs_cash_and_undo_by_event_id(env: Env) -> None:
    now = utcnow()
    event = RawEvent(
        event_id=str(uuid7()),
        user_id=USER,
        type="WIDGET_TAP",
        occurred_at=now,
        producer="android",
        payload=RawEventPayload(text="20 chai", posted_at=now, device_id="d", redacted=True),
    )
    await handle_raw_event(event.model_dump_json(by_alias=True).encode(), env.deps)
    assert await env.drain_cash() == 1
    assert await _balance(env) == -2000

    undo = await env.client.delete(f"/v1/cash/{event.event_id}", headers=AUTH)
    assert undo.status_code == 204
    assert await _balance(env) == 0


async def _parsed(
    env: Env,
    user: str,
    *,
    amount: int,
    merchant: str | None,
    channel: Channel = Channel.UPI,
    at: datetime,
) -> str:
    raw_id = str(uuid7())
    async with env.sessions.begin() as session:
        await session.execute(
            sa.text(
                "INSERT INTO raw_events (event_id, user_id, type, posted_at) "
                "VALUES (:id, :u, 'RAW_SMS', :p)"
            ),
            {"id": raw_id, "u": user, "p": at},
        )
    event = ParsedTransactionEvent(
        event_id=str(uuid7()),
        user_id=user,
        type="PARSED_TRANSACTION",
        occurred_at=at,
        producer="test",
        payload=ParsedTransactionPayload(
            raw_event_id=raw_id,
            amount_paise=amount,
            direction=Direction.DEBIT,
            channel=channel,
            status=TxnStatus.SUCCESS,
            merchant_raw=merchant,
            account_hint="1234",
            txn_time=at,
            parse_method=ParseMethod.RULE,
            confidence=0.99,
        ),
    )
    await handle_parsed_transaction(event.model_dump_json(by_alias=True).encode(), env.deps)
    (clean,) = env.publisher.take(Topic.CLEAN_TRANSACTIONS)
    txn_id: str = json.loads(clean)["payload"]["transactionId"]
    return txn_id


async def test_atm_withdrawal_adds_cash(env: Env) -> None:
    at = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
    txn_id = await _parsed(env, USER, amount=200000, merchant=None, channel=Channel.ATM, at=at)
    (cash,) = env.publisher.take(Topic.CASH_EVENTS)
    payload = json.loads(cash)["payload"]
    assert payload == {
        "entryType": "ATM_WITHDRAWAL",
        "amountPaise": 200000,
        "category": None,
        "note": None,
        "relatedTransactionId": txn_id,
    }
    await handle_cash_event(cash, env.deps)
    await handle_cash_event(cash, env.deps)  # redelivery
    assert await _balance(env) == 200000


async def test_correction_wins_for_that_user_only(env: Env) -> None:
    t0 = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
    stall = "SHARMA TEA STALL"
    txn_id = await _parsed(env, USER, amount=3000, merchant=stall, at=t0)
    other_before = await _parsed(env, OTHER, amount=3000, merchant=stall, at=t0)

    page = (await env.client.get("/v1/transactions", headers=AUTH)).json()
    assert [i["id"] for i in page["items"]] == [txn_id]
    assert page["items"][0]["category"] == "OTHER"

    assert (
        await env.client.patch(
            f"/v1/transactions/{other_before}", json={"category": "RENT"}, headers=AUTH
        )
    ).status_code == 404
    response = await env.client.patch(
        f"/v1/transactions/{txn_id}",
        json={"merchantName": "Sharma Chai", "category": "DINING_OUT"},
        headers=AUTH,
    )
    assert response.status_code == 200
    corrected = response.json()
    assert corrected["merchantName"] == "Sharma Chai"
    assert corrected["category"] == "DINING_OUT"
    assert corrected["userCorrected"] is True
    assert corrected["version"] == 2
    (clean,) = env.publisher.take(Topic.CLEAN_TRANSACTIONS)
    assert json.loads(clean)["payload"]["version"] == 2

    later = await _parsed(env, USER, amount=4000, merchant=stall, at=t0 + timedelta(days=1))
    other_later = await _parsed(env, OTHER, amount=4000, merchant=stall, at=t0 + timedelta(days=1))
    async with env.sessions() as session:
        rows: dict[str, str] = dict(
            (
                await session.execute(
                    sa.text("SELECT id, category FROM transactions WHERE id IN (:a, :b)"),
                    {"a": later, "b": other_later},
                )
            ).all()
        )
    assert rows == {later: "DINING_OUT", other_later: "OTHER"}


async def test_transactions_are_paginated(env: Env) -> None:
    t0 = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
    ids = [
        await _parsed(env, USER, amount=1000 + i, merchant="zomato", at=t0 + timedelta(hours=i))
        for i in range(3)
    ]
    first = (await env.client.get("/v1/transactions?limit=2", headers=AUTH)).json()
    assert [i["id"] for i in first["items"]] == [ids[2], ids[1]]
    assert first["items"][0]["merchantName"] == "Zomato"
    second = (
        await env.client.get(f"/v1/transactions?limit=2&cursor={first['nextCursor']}", headers=AUTH)
    ).json()
    assert [i["id"] for i in second["items"]] == [ids[0]]
    assert second["nextCursor"] is None
    assert (await env.client.get("/v1/transactions?cursor=zz", headers=AUTH)).status_code == 400
