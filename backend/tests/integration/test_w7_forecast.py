"""W7 on real Postgres: forecast inputs, GET /v1/forecast + what-if, BROKE_DATE_MOVED -> Coach."""

import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import fakeredis
import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from kharcha_common.events import (
    CleanTransactionEvent,
    CleanTransactionPayload,
    Direction,
    EventEnvelope,
    TxnKind,
    TxnStatus,
)
from kharcha_common.ids import uuid7
from kharcha_common.settings import Settings
from kharcha_common.time import IST, to_ist, utcnow
from kharcha_common.topics import Topic
from kharcha_ingest.auth import hash_api_key
from kharcha_ingest.main import create_app
from kharcha_insights.main import InsightsDeps, handle_clean_transaction
from kharcha_notifier.bot import BotDeps
from kharcha_notifier.coach_v0 import handle_agent_task
from kharcha_notifier.telegram import Button
from tests.integration.conftest import PgUrls

pytestmark = pytest.mark.integration

USER, NEW_USER = "u_w7", "u_w7_new"
KEY, NEW_KEY = "k_w7_key", "k_w7_new_key"
AUTH = {"Authorization": f"Bearer {KEY}"}
CHAT = 777


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


@dataclass
class FakeMessenger:
    sent: list[str] = field(default_factory=list)

    async def send_message(
        self, chat_id: int, text: str, buttons: list[list[Button]] | None = None
    ) -> int:
        self.sent.append(text)
        return len(self.sent)

    async def edit_message_text(self, chat_id: int, message_id: int, text: str) -> None:
        return None

    async def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        return None


@dataclass
class Env:
    client: httpx.AsyncClient
    sessions: async_sessionmaker[AsyncSession]
    publisher: QueuePublisher


@pytest.fixture
async def env(migrated_db: PgUrls) -> AsyncIterator[Env]:
    engine = create_async_engine(migrated_db.async_url)
    async with engine.begin() as conn:
        for table in (
            "forecasts",
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
        await conn.execute(
            sa.text(
                "INSERT INTO users (id, telegram_chat_id, quiet_start, quiet_end) "
                "VALUES (:a, :chat, '23:59', '00:00'), (:b, NULL, '22:00', '08:00')"
            ),
            {"a": USER, "b": NEW_USER, "chat": CHAT},
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    publisher = QueuePublisher()
    settings = Settings(
        env="test",
        database_url=migrated_db.async_url,
        api_keys={hash_api_key(KEY): USER, hash_api_key(NEW_KEY): NEW_USER},
    )
    app = create_app(settings, publisher=publisher, redis=fakeredis.FakeAsyncRedis())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client,
    ):
        yield Env(client, sessions, publisher)
    await engine.dispose()


async def _txn(
    env: Env,
    at: datetime,
    amount: int,
    *,
    balance: int | None = None,
    category: str = "FOOD_DELIVERY",
) -> str:
    txn_id = "t_" + uuid7().hex
    async with env.sessions.begin() as session:
        await session.execute(
            sa.text(
                "INSERT INTO transactions (id, user_id, amount_paise, direction, kind, status, "
                "channel, merchant_raw, category, is_essential, account_hint, balance_after_paise,"
                " txn_time, parse_method) VALUES (:id, :u, :amt, 'DEBIT', 'SPEND', 'SUCCESS', "
                "'UPI', :m, :c, false, '1234', :bal, :t, 'RULE')"
            ),
            {
                "id": txn_id,
                "u": USER,
                "amt": amount,
                "m": f"shop{at.day}",
                "c": category,
                "bal": balance,
                "t": at,
            },
        )
    return txn_id


async def _history(env: Env) -> datetime:
    """20 days of ₹100/day; bank ₹900 after the latest payment; ₹200 cash."""
    now = utcnow()
    noon_today = datetime.combine(to_ist(now).date(), datetime.min.time(), tzinfo=IST)
    for d in range(20, 0, -1):
        at = noon_today - timedelta(days=d) + timedelta(hours=12)
        await _txn(env, at, 10_000, balance=90_000 if d == 1 else None)
    async with env.sessions.begin() as session:
        await session.execute(
            sa.text(
                "INSERT INTO cash_ledger (id, user_id, entry_type, amount_paise, occurred_at) "
                "VALUES ('c_w7', :u, 'CASH_RECEIVED', 20000, :t)"
            ),
            {"u": USER, "t": now - timedelta(days=2)},
        )
    return now


async def test_forecast_endpoint_and_what_if(env: Env) -> None:
    now = await _history(env)
    response = await env.client.get("/v1/forecast", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "OK"
    assert (body["bankPaise"], body["cashPaise"], body["moneyNowPaise"]) == (
        90_000,
        20_000,
        110_000,
    )
    assert body["balanceFresh"] is True
    # Every past day cost exactly ₹100: ₹1,100 lasts 11 days in every run.
    assert body["daysLeftP50"] == 11
    assert body["brokeP20"] == body["brokeP50"] == body["brokeP80"]
    assert body["probBroke"] == 1.0
    assert body["topCategories"][0]["category"] == "FOOD_DELIVERY"

    skip = await env.client.get("/v1/forecast?skip=FOOD_DELIVERY&reductionPct=50", headers=AUTH)
    what_if = skip.json()["whatIf"]
    assert what_if["category"] == "FOOD_DELIVERY"
    assert what_if["daysGained"] == 11  # half the spend, twice the days

    async with env.sessions() as session:
        stored: int = (await session.execute(sa.text("SELECT count(*) FROM forecasts"))).scalar_one()
    assert stored == 1  # plain GET stores; what-if does not
    assert now


async def test_needs_balance_then_override(env: Env) -> None:
    headers = {"Authorization": f"Bearer {NEW_KEY}"}
    first = (await env.client.get("/v1/forecast", headers=headers)).json()
    assert first == {**first, "status": "NEEDS_BALANCE", "brokeP50": None}
    answered = (await env.client.get("/v1/forecast?balancePaise=500000", headers=headers)).json()
    assert answered["status"] == "OK"
    assert answered["brokeP50"] is None  # no spending history yet


async def test_broke_date_moving_earlier_nudges_once(env: Env) -> None:
    now = await _history(env)
    insights = InsightsDeps(env.sessions, env.publisher)

    async def clean(txn_id: str, amount: int) -> bytes:
        event = CleanTransactionEvent(
            event_id=str(uuid7()),
            user_id=USER,
            type="CLEAN_TRANSACTION",
            occurred_at=now,
            producer="test",
            payload=CleanTransactionPayload(
                transaction_id=txn_id,
                amount_paise=amount,
                direction=Direction.DEBIT,
                kind=TxnKind.SPEND,
                status=TxnStatus.SUCCESS,
                category="FOOD_DELIVERY",
                is_essential=False,
                txn_time=now,
                source_event_ids=[],
                version=1,
            ),
        )
        return event.model_dump_json(by_alias=True).encode()

    first = await _txn(env, now - timedelta(minutes=5), 1)
    await handle_clean_transaction(await clean(first, 1), insights)
    assert env.publisher.take(Topic.AGENT_TASKS) == []  # first forecast: nothing to compare

    # A ₹500 splurge brings the broke date 5 days closer; the previous forecast is 2 h old.
    async with env.sessions.begin() as session:
        await session.execute(
            sa.text("UPDATE forecasts SET computed_at = computed_at - interval '2 hours'")
        )
    big = await _txn(env, now - timedelta(minutes=1), 50_000)
    await handle_clean_transaction(await clean(big, 50_000), insights)
    (task_body,) = env.publisher.take(Topic.AGENT_TASKS)
    task = json.loads(task_body)["payload"]
    assert task["trigger"] == "BROKE_DATE_MOVED"
    assert task["dedupeKey"].startswith("broke:")

    messenger = FakeMessenger()
    bot = BotDeps(env.sessions, env.publisher, fakeredis.FakeAsyncRedis(), messenger)
    await handle_agent_task(task_body, bot, model=None, now=now)
    await handle_agent_task(task_body, bot, model=None, now=now)  # redelivery
    assert len(messenger.sent) == 1
    assert "money runs out around" in messenger.sent[0]
    assert "₹599.99" in messenger.sent[0]  # ₹900 + ₹200 cash - 1 paise - ₹500
