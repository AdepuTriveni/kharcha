"""W8 on real Postgres: weekly review -> orchestrator -> Coach v1 (scripted model, real tools)
-> agent-results -> policy gate -> Telegram. Transcripts land in agent_runs.
"""

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import fakeredis
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from kharcha_common.events import EventEnvelope
from kharcha_common.ids import uuid7
from kharcha_common.topics import Topic
from kharcha_notifier.bot import BotDeps
from kharcha_notifier.scheduler import publish_weekly_reviews
from kharcha_notifier.telegram import Button
from kharcha_runtime.loop import PROPOSE
from kharcha_runtime.types import ModelResponse, ToolCall, ToolSpec
from tests.integration.agent_path import deliver
from tests.integration.conftest import PgUrls

pytestmark = pytest.mark.integration

USER = "u_w8"
CHAT = 8080
SUNDAY_1105_IST = datetime(2026, 10, 4, 5, 35, tzinfo=UTC)


@dataclass
class QueuePublisher:
    queues: dict[str, list[bytes]] = field(default_factory=dict)

    async def publish_event(self, topic: str, event: EventEnvelope[Any]) -> None:
        self.queues.setdefault(str(topic), []).append(event.model_dump_json(by_alias=True).encode())

    async def publish_raw(
        self, topic: str, body: bytes, key: bytes | None, headers: Mapping[str, str]
    ) -> None:
        self.queues.setdefault(topic, []).append(body)


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


class Scripted:
    """Calls get_weekly_summary, then proposes each text in turn, then says done."""

    def __init__(self, *texts: str, ptype: str = "ROAST") -> None:
        self.turns: list[ModelResponse] = [
            ModelResponse(None, (ToolCall("c0", "get_weekly_summary", {"week": "this"}),))
        ]
        for i, text in enumerate(texts):
            args = {"type": ptype, "text": text, "reason": "weekly", "category": "FOOD_DELIVERY"}
            self.turns.append(ModelResponse(None, (ToolCall(f"p{i}", PROPOSE, args),)))
        self.turns.append(ModelResponse("done"))
        self.seen_tool_data: list[str] = []

    async def complete(
        self, messages: Sequence[Mapping[str, Any]], tools: Sequence[ToolSpec]
    ) -> ModelResponse:
        if messages[-1].get("role") == "tool":
            self.seen_tool_data.append(str(messages[-1]["content"]))
        return self.turns.pop(0)


@dataclass
class Env:
    bot: BotDeps
    publisher: QueuePublisher
    messenger: FakeMessenger


@pytest.fixture
async def env(migrated_db: PgUrls) -> AsyncIterator[Env]:
    engine = create_async_engine(migrated_db.async_url)
    async with engine.begin() as conn:
        for table in (
            "forecasts",
            "agent_runs",
            "alerts_sent",
            "cash_ledger",
            "transaction_sources",
            "transactions",
            "processed_events",
            "raw_events",
            "budgets",
            "user_merchant_overrides",
            "users",
        ):
            await conn.execute(sa.text(f"DELETE FROM {table}"))
        await conn.execute(
            sa.text(
                "INSERT INTO users (id, telegram_chat_id, roast_level, quiet_start, quiet_end) "
                "VALUES (:u, :c, 'MEDIUM', '22:00', '08:00')"
            ),
            {"u": USER, "c": CHAT},
        )
        for day in (1, 2, 3, 3):  # Thu-Sat: 4 Zomato orders of ₹355
            await conn.execute(
                sa.text(
                    "INSERT INTO transactions (id, user_id, amount_paise, direction, kind, status,"
                    " channel, merchant_raw, category, is_essential, txn_time, parse_method) "
                    "VALUES (:id, :u, 35500, 'DEBIT', 'SPEND', 'SUCCESS', 'UPI', 'Zomato', "
                    "'FOOD_DELIVERY', false, :t, 'RULE')"
                ),
                {"id": "t_" + uuid7().hex, "u": USER, "t": datetime(2026, 10, day, 8, tzinfo=UTC)},
            )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    publisher, messenger = QueuePublisher(), FakeMessenger()
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    yield Env(BotDeps(sessions, publisher, redis, messenger), publisher, messenger)
    await engine.dispose()


async def _weekly_task(env: Env) -> bytes:
    assert await publish_weekly_reviews(env.bot, now=SUNDAY_1105_IST) == 1
    assert await publish_weekly_reviews(env.bot, now=SUNDAY_1105_IST + timedelta(minutes=1)) == 0
    assert await publish_weekly_reviews(env.bot, now=SUNDAY_1105_IST - timedelta(hours=2)) == 0
    (task,) = env.publisher.queues.pop(Topic.AGENT_TASKS.value)
    assert json.loads(task)["payload"]["trigger"] == "WEEKLY_REVIEW"
    return task


async def _row(env: Env, sql: str) -> tuple[Any, ...]:
    async with env.bot.sessions() as session:
        return tuple((await session.execute(sa.text(sql))).one())


async def test_grounded_roast_is_sent_and_run_recorded(env: Env) -> None:
    task = await _weekly_task(env)
    model = Scripted("₹1,420 on Zomato this week, 4 orders. Ghar ka khana try karo?")
    await deliver(task, env.bot, env.publisher, SUNDAY_1105_IST, model=model)
    await deliver(task, env.bot, env.publisher, SUNDAY_1105_IST, model=model)  # redelivery

    assert env.messenger.sent == ["₹1,420 on Zomato this week, 4 orders. Ghar ka khana try karo?"]
    assert "<<<TOOL_DATA" in model.seen_tool_data[0]
    assert '"spentPaise": 142000' in model.seen_tool_data[0]
    assert await _row(env, "SELECT alert_type, status FROM alerts_sent") == ("ROAST", "SENT")
    status, prompt, transcript = await _row(
        env, "SELECT status, prompt_version, transcript FROM agent_runs"
    )
    assert (status, prompt) == ("OK", "coach/v1")
    assert [e["kind"] for e in transcript][:3] == ["start", "model", "tool"]


async def test_ungrounded_model_falls_back_to_template(env: Env) -> None:
    task = await _weekly_task(env)
    model = Scripted("₹2,000 on Zomato!", "₹2,500 on Zomato!!")
    await deliver(task, env.bot, env.publisher, SUNDAY_1105_IST, model=model)
    assert env.messenger.sent == [
        "This week so far: ₹1,420.00 over 4 payments. Top: Zomato (₹1,420.00)."
    ]
    assert await _row(env, "SELECT alert_type FROM alerts_sent") == ("NUDGE",)


async def test_roast_off_downgrades_to_plain_nudge(env: Env) -> None:
    async with env.bot.sessions.begin() as session:
        await session.execute(sa.text("UPDATE users SET roast_level = 'OFF'"))
    task = await _weekly_task(env)
    model = Scripted("₹1,420 on Zomato this week, 4 orders. Bas karo!")
    await deliver(task, env.bot, env.publisher, SUNDAY_1105_IST, model=model)
    assert env.messenger.sent == [
        "This week so far: ₹1,420.00 over 4 payments. Top: Zomato (₹1,420.00)."
    ]


async def test_quiet_hours_suppress_everything(env: Env) -> None:
    async with env.bot.sessions.begin() as session:
        await session.execute(
            sa.text("UPDATE users SET quiet_start = '10:00', quiet_end = '12:00'")
        )
    task = await _weekly_task(env)
    await deliver(task, env.bot, env.publisher, SUNDAY_1105_IST, model=Scripted("₹1,420 on Zomato"))
    assert env.messenger.sent == []
    assert await _row(env, "SELECT status FROM alerts_sent") == ("SUPPRESSED",)
