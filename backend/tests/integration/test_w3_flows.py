"""W3 flows against real Postgres: Telegram linking, chat cash entry + Undo, frequency trigger
-> coach message, daily summary exactly once.

Kafka is replaced by an in-memory publisher that hands events to the next handler, so the
same handler code runs as in production. Telegram is a fake Messenger; Redis is fakeredis.
"""

import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any

import fakeredis
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from kharcha_common.events import EventEnvelope
from kharcha_common.linking import create_link_code
from kharcha_common.topics import Topic
from kharcha_insights.main import InsightsDeps, handle_clean_transaction
from kharcha_notifier.bot import BotDeps, handle_update
from kharcha_notifier.confirm import handle_cash_event as confirm_cash_event
from kharcha_notifier.scheduler import send_due_summaries
from kharcha_notifier.telegram import Button
from kharcha_processor.extraction import ExtractionResult
from kharcha_processor.handlers import (
    ProcessorDeps,
    handle_cash_event,
    handle_parsed_transaction,
    handle_raw_event,
)
from tests.integration.agent_path import deliver
from tests.integration.conftest import PgUrls

pytestmark = pytest.mark.integration

USER = "u_w3"
CHAT = 424242


@dataclass
class Sent:
    chat_id: int
    text: str
    buttons: list[list[Button]] | None


@dataclass
class FakeMessenger:
    sent: list[Sent] = field(default_factory=list)
    edits: list[tuple[int, int, str]] = field(default_factory=list)
    answers: list[tuple[str, str | None]] = field(default_factory=list)

    async def send_message(
        self, chat_id: int, text: str, buttons: list[list[Button]] | None = None
    ) -> int:
        self.sent.append(Sent(chat_id, text, buttons))
        return len(self.sent)

    async def edit_message_text(self, chat_id: int, message_id: int, text: str) -> None:
        self.edits.append((chat_id, message_id, text))

    async def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        self.answers.append((callback_id, text))


@dataclass
class QueuePublisher:
    """Collects published events per topic; tests drain them into the next handler."""

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

    async def extract(self, **_: Any) -> ExtractionResult:
        raise AssertionError("teacher must not be called for cash text")


@pytest.fixture
async def env(migrated_db: PgUrls) -> AsyncIterator[tuple[BotDeps, ProcessorDeps, QueuePublisher]]:
    engine = create_async_engine(migrated_db.async_url)
    async with engine.begin() as conn:
        for table in (
            "agent_runs",
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
        await conn.execute(sa.text("INSERT INTO users (id) VALUES (:u)"), {"u": USER})
    sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    publisher = QueuePublisher()
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    bot = BotDeps(sessions, publisher, redis, FakeMessenger())
    processor = ProcessorDeps(sessions, publisher, NoTeacher())
    yield bot, processor, publisher
    await engine.dispose()


def _message(text: str, message_id: int) -> dict[str, Any]:
    return {
        "update_id": message_id,
        "message": {"message_id": message_id, "text": text, "chat": {"id": CHAT}},
    }


async def _link(bot: BotDeps) -> None:
    code = await create_link_code(bot.redis, USER)
    await handle_update(_message(f"/start {code}", 1), bot)


async def test_unlinked_chat_is_told_to_link(
    env: tuple[BotDeps, ProcessorDeps, QueuePublisher],
) -> None:
    bot, _, _ = env
    await handle_update(_message("150 vada pav", 1), bot)
    assert "not linked" in bot.messenger.sent[-1].text  # type: ignore[attr-defined]


async def test_cash_entry_confirm_and_undo(
    env: tuple[BotDeps, ProcessorDeps, QueuePublisher],
) -> None:
    bot, processor, publisher = env
    messenger: FakeMessenger = bot.messenger  # type: ignore[assignment]
    await _link(bot)
    assert messenger.sent[-1].text.startswith("🔗 Linked!")

    await handle_update(_message("150 vada pav", 2), bot)
    await handle_update(_message("150 vada pav", 2), bot)  # Telegram redelivery
    raw = publisher.take(Topic.RAW_EVENTS)
    assert len(raw) == 2
    for body in raw:
        await handle_raw_event(body, processor)
    cash = publisher.take(Topic.CASH_EVENTS)
    assert len(cash) == 1  # second delivery skipped by processed_events
    await handle_cash_event(cash[0], processor)
    await handle_cash_event(cash[0], processor)  # idempotent
    await confirm_cash_event(cash[0], bot)
    await confirm_cash_event(cash[0], bot)  # confirmation sent once

    confirms = [s for s in messenger.sent if s.text.startswith("✅")]
    assert [s.text for s in confirms] == ["✅ Logged cash spend ₹150.00 · vada pav (dining out)"]
    undo = confirms[0].buttons[0][0]  # type: ignore[index]
    async with bot.sessions() as session:
        assert (
            await session.execute(sa.text("SELECT count(*) FROM cash_ledger"))
        ).scalar_one() == 1

    await handle_update(_message("/cash", 3), bot)
    assert messenger.sent[-1].text == "You logged ₹150.00 more than tracked cash"

    callback = {
        "update_id": 4,
        "callback_query": {
            "id": "cb1",
            "data": undo.callback_data,
            "message": {"message_id": 7, "text": confirms[0].text, "chat": {"id": CHAT}},
        },
    }
    await handle_update(callback, bot)
    assert messenger.answers == [("cb1", "Undone")]
    assert messenger.edits[0][2].startswith("↩️ Undone")
    async with bot.sessions() as session:
        assert (
            await session.execute(sa.text("SELECT count(*) FROM cash_ledger"))
        ).scalar_one() == 0


async def _clean_txn(
    processor: ProcessorDeps, publisher: QueuePublisher, n: int, when: datetime
) -> None:
    from kharcha_common.events import (
        Channel,
        Direction,
        ParsedTransactionEvent,
        ParsedTransactionPayload,
        ParseMethod,
        TxnStatus,
    )
    from kharcha_common.ids import uuid7

    raw_id = str(uuid7())
    async with processor.sessions.begin() as session:
        await session.execute(
            sa.text(
                "INSERT INTO raw_events (event_id, user_id, type, posted_at) "
                "VALUES (:id, :u, 'RAW_NOTIFICATION', :t)"
            ),
            {"id": raw_id, "u": USER, "t": when},
        )
    parsed = ParsedTransactionEvent(
        user_id=USER,
        type="PARSED_TRANSACTION",
        occurred_at=when,
        producer="test",
        payload=ParsedTransactionPayload(
            raw_event_id=raw_id,
            amount_paise=35500 + n,
            direction=Direction.DEBIT,
            channel=Channel.UPI,
            status=TxnStatus.SUCCESS,
            merchant_raw="zomato@hdfcbank",
            txn_time=when,
            parse_method=ParseMethod.TEACHER_LLM,
            confidence=0.9,
        ),
    )
    await handle_parsed_transaction(parsed.model_dump_json(by_alias=True).encode(), processor)


async def test_frequency_trigger_sends_one_nudge(
    env: tuple[BotDeps, ProcessorDeps, QueuePublisher],
) -> None:
    bot, processor, publisher = env
    messenger: FakeMessenger = bot.messenger  # type: ignore[assignment]
    await _link(bot)
    noon_ist = datetime(2026, 10, 3, 6, 30, tzinfo=UTC)
    insights = InsightsDeps(bot.sessions, publisher)

    for n in range(4):
        await _clean_txn(processor, publisher, n, noon_ist - timedelta(hours=n))
    clean = publisher.take(Topic.CLEAN_TRANSACTIONS)
    assert len(clean) == 4
    for body in clean:
        await handle_clean_transaction(body, insights)
    tasks = publisher.take(Topic.AGENT_TASKS)
    assert len(tasks) == 2  # 3rd and 4th payment; same task id (one dedupe key per week)
    for body in tasks:
        await deliver(body, bot, publisher, noon_ist)

    nudges = [s.text for s in messenger.sent if "zomato" in s.text]
    assert len(nudges) == 1
    assert "payments in the last 7 days" in nudges[0]  # category OTHER: plain, not a roast
    async with bot.sessions() as session:
        row = (await session.execute(sa.text("SELECT alert_type, status FROM alerts_sent"))).one()
    assert tuple(row) == ("NUDGE", "SENT")


async def test_budget_trigger_and_daily_summary(
    env: tuple[BotDeps, ProcessorDeps, QueuePublisher],
) -> None:
    bot, processor, publisher = env
    messenger: FakeMessenger = bot.messenger  # type: ignore[assignment]
    await _link(bot)
    await handle_update(_message("/budget other 500", 2), bot)
    assert messenger.sent[-1].text == "Budget for other set to ₹500.00 a month."

    noon_ist = datetime(2026, 10, 3, 6, 30, tzinfo=UTC)
    await _clean_txn(processor, publisher, 0, noon_ist)  # ₹355.00 -> 71%: nothing
    await _clean_txn(processor, publisher, 1, noon_ist)  # ₹710.01 -> crosses 80% and 100%
    insights = InsightsDeps(bot.sessions, publisher)
    for body in publisher.take(Topic.CLEAN_TRANSACTIONS):
        await handle_clean_transaction(body, insights)
    tasks = publisher.take(Topic.AGENT_TASKS)
    # Same timestamp: both payments see the month total, so the task may be emitted twice,
    # but with the same event id and dedupe key.
    assert len({json.loads(t)["eventId"] for t in tasks}) == 1
    assert all(b'"trigger":"BUDGET"' in t for t in tasks)
    sent_before = len(messenger.sent)
    for body in tasks:
        await deliver(body, bot, publisher, noon_ist)
    assert len(messenger.sent) == sent_before + 1
    assert "142% of your ₹500.00 budget" in messenger.sent[-1].text

    before = len(messenger.sent)
    evening_ist = datetime(2026, 10, 3, 16, 0, tzinfo=UTC)  # 21:30 IST
    assert await send_due_summaries(bot, time(21, 30), now=evening_ist) == 1
    assert await send_due_summaries(bot, time(21, 30), now=evening_ist) == 0
    summary = messenger.sent[before].text
    assert summary.startswith("📊 Today, 3 Oct\nSpent ₹710.01 in 2 payments")
