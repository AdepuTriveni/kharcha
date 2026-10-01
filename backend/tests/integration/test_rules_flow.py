"""Tier 1 rules end to end on real Postgres (PROJECT_SPEC §10.4-10.5).

Seed rule parses without the teacher; shadow mismatches auto-disable; a synthesized
candidate earns ACTIVE after 5 consistent matches and then replaces the teacher.
"""

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from kharcha_common.events import EventEnvelope, RawEvent, RawEventPayload
from kharcha_common.ids import uuid7
from kharcha_common.topics import Topic
from kharcha_processor.extraction import ExtractionResult
from kharcha_processor.handlers import ProcessorDeps, handle_parsed_transaction, handle_raw_event
from kharcha_processor.rule_store import seed_rules
from kharcha_processor.synthesis import Cooldown, ProposedRule, Sample
from tests.integration.conftest import PgUrls

pytestmark = pytest.mark.integration

USER = "u_rules"
T0 = datetime(2026, 10, 3, 8, 0, tzinfo=UTC)
AMOUNT = r"(?P<amount>[\d,]+(?:\.\d{1,2})?)"


@dataclass
class QueuePublisher:
    queues: dict[str, list[bytes]] = field(default_factory=dict)

    async def publish_event(self, topic: str, event: EventEnvelope[Any]) -> None:
        self.queues.setdefault(str(topic), []).append(event.model_dump_json(by_alias=True).encode())

    async def publish_raw(
        self, topic: str, body: bytes, key: bytes | None, headers: Mapping[str, str]
    ) -> None:
        self.queues.setdefault(topic, []).append(body)

    def take(self, topic: Topic) -> list[dict[str, Any]]:
        return [json.loads(b) for b in self.queues.pop(topic.value, [])]


class TableTeacher:
    """Returns whatever label the test registered for a text."""

    model_version = "table#parser/v1"

    def __init__(self) -> None:
        self.labels: dict[str, dict[str, Any]] = {}
        self.calls = 0

    async def extract(
        self, *, sender: str | None, source_app: str | None, text: str
    ) -> ExtractionResult:
        self.calls += 1
        return ExtractionResult.model_validate(self.labels[text])


class FakeWriter:
    def __init__(self, regex: str) -> None:
        self.regex = regex
        self.calls: list[int] = []

    async def write_rule(self, key: str, samples: Sequence[Sample]) -> ProposedRule:
        self.calls.append(len(samples))
        return ProposedRule(
            regex=self.regex,
            field_map={"direction": "DEBIT", "channel": "UPI", "status": "SUCCESS"},
        )


@pytest.fixture
async def sessions(migrated_db: PgUrls) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
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
            "parse_rules",
            "users",
        ):
            await conn.execute(sa.text(f"DELETE FROM {table}"))
        await conn.execute(sa.text("INSERT INTO users (id) VALUES (:u)"), {"u": USER})
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker.begin() as session:
        await seed_rules(session)
    yield maker
    await engine.dispose()


async def _raw(
    sessions: async_sessionmaker[AsyncSession], text: str, at: datetime, sender: str = "AX-HDFCBK"
) -> bytes:
    event = RawEvent(
        event_id=str(uuid7()),
        user_id=USER,
        type="RAW_SMS",
        occurred_at=at,
        producer="test",
        payload=RawEventPayload(
            sender=sender, text=text, posted_at=at, device_id="d", redacted=True
        ),
    )
    async with sessions.begin() as session:
        await session.execute(
            sa.text(
                "INSERT INTO raw_events (event_id, user_id, type, sender, text, posted_at) "
                "VALUES (:id, :u, 'RAW_SMS', :s, :x, :p)"
            ),
            {"id": event.event_id, "u": USER, "s": sender, "x": text, "p": at},
        )
    return event.model_dump_json(by_alias=True).encode()


async def _rule(sessions: async_sessionmaker[AsyncSession], rule_id: str) -> tuple[Any, ...]:
    async with sessions() as session:
        row = (
            await session.execute(
                sa.text(
                    "SELECT status, match_count, mismatch_count, origin FROM parse_rules "
                    "WHERE id = :id"
                ),
                {"id": rule_id},
            )
        ).one()
    return tuple(row)


def _hdfc(n: int) -> tuple[str, dict[str, Any]]:
    ref = f"41234567{n:04d}"
    text = f"Rs.{n}.00 debited from A/c XX1234 on 03-10-26 to VPA zomato@hdfcbank. UPI Ref {ref}"
    label = {
        "isTransaction": True,
        "amount": f"{n}.00",
        "direction": "DEBIT",
        "channel": "UPI",
        "status": "SUCCESS",
        "merchantRaw": "zomato@hdfcbank",
        "counterpartyVpa": "zomato@hdfcbank",
        "referenceId": ref,
        "accountHint": "1234",
    }
    return text, label


async def test_seed_rule_parses_without_teacher_and_redelivery_is_idempotent(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    teacher, publisher = TableTeacher(), QueuePublisher()
    deps = ProcessorDeps(sessions, publisher, teacher, shadow_rate=0.0)
    text, _ = _hdfc(349)
    body = await _raw(sessions, text, T0)

    await handle_raw_event(body, deps)
    await handle_raw_event(body, deps)  # redelivery

    parsed = publisher.take(Topic.PARSED_TRANSACTIONS)
    assert len(parsed) == 1
    assert parsed[0]["payload"]["parseMethod"] == "RULE"
    assert parsed[0]["payload"]["ruleId"] == "r_hdfc_upi_debit_v1"
    assert parsed[0]["payload"]["amountPaise"] == 34900
    assert teacher.calls == 0
    assert publisher.take(Topic.MODEL_SHADOW) == []


async def test_shadow_mismatches_disable_rule(sessions: async_sessionmaker[AsyncSession]) -> None:
    teacher, publisher = TableTeacher(), QueuePublisher()
    deps = ProcessorDeps(sessions, publisher, teacher, shadow_rate=1.0)
    for n in (101, 102, 103):
        text, label = _hdfc(n)
        teacher.labels[text] = {**label, "amount": "1.00"}  # teacher disagrees
        await handle_raw_event(await _raw(sessions, text, T0 + timedelta(minutes=n)), deps)

    shadows = publisher.take(Topic.MODEL_SHADOW)
    assert len(shadows) == 3
    assert not any(s["payload"]["agreement"] for s in shadows)
    assert "text" not in json.dumps(shadows[0]["payload"])
    assert await _rule(sessions, "r_hdfc_upi_debit_v1") == ("DISABLED", 0, 3, "SEED")

    # Disabled: the next message goes to the teacher.
    text, label = _hdfc(104)
    teacher.labels[text] = label
    await handle_raw_event(await _raw(sessions, text, T0 + timedelta(hours=3)), deps)
    last = publisher.take(Topic.PARSED_TRANSACTIONS)[-1]
    assert last["payload"]["parseMethod"] == "TEACHER_LLM"


NEW_TEMPLATE = (
    rf"Sent Rs\.?{AMOUNT} from HDFC Bank A/c \*\*(?P<acct>\d{{4}}) to (?P<vpa>[\w.\-]+@\w+)"
    r" on [\d/]+ Ref (?P<ref>\d{6,})"
)


def _sent(n: int) -> tuple[str, dict[str, Any]]:
    ref, vpa = f"51234567{n:04d}", f"shop{n}@ybl"
    text = f"Sent Rs.{n}.00 from HDFC Bank A/c **1234 to {vpa} on 03/10/26 Ref {ref}"
    label = {
        "isTransaction": True,
        "amount": f"{n}.00",
        "direction": "DEBIT",
        "channel": "UPI",
        "status": "SUCCESS",
        "merchantRaw": vpa,
        "counterpartyVpa": vpa,
        "referenceId": ref,
        "accountHint": "1234",
    }
    return text, label


async def test_synthesized_candidate_is_promoted_then_used(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    teacher, publisher, writer = TableTeacher(), QueuePublisher(), FakeWriter(NEW_TEMPLATE)
    deps = ProcessorDeps(
        sessions, publisher, teacher, rule_writer=writer, shadow_rate=0.0, cooldown=Cooldown(0)
    )

    async def one(n: int) -> dict[str, Any]:
        text, label = _sent(n)
        teacher.labels[text] = label
        await handle_raw_event(await _raw(sessions, text, T0 + timedelta(minutes=10 * n)), deps)
        (parsed,) = publisher.take(Topic.PARSED_TRANSACTIONS)
        # Dedup writes the transaction, which later becomes a synthesis sample.
        await handle_parsed_transaction(json.dumps(parsed).encode(), deps)
        publisher.take(Topic.CLEAN_TRANSACTIONS)
        payload: dict[str, Any] = parsed["payload"]
        return payload

    await one(1)
    assert writer.calls == []  # one sample is not enough
    await one(2)
    assert writer.calls == [2]

    async with sessions() as session:
        (rule_id,) = (
            await session.scalars(sa.text("SELECT id FROM parse_rules WHERE origin='SYNTHESIZED'"))
        ).all()
    assert await _rule(sessions, rule_id) == ("CANDIDATE", 0, 0, "SYNTHESIZED")

    for n in range(3, 8):
        assert (await one(n))["parseMethod"] == "TEACHER_LLM"
    assert await _rule(sessions, rule_id) == ("ACTIVE", 5, 0, "SYNTHESIZED")

    calls = teacher.calls
    payload = await one(8)
    assert payload["parseMethod"] == "RULE"
    assert payload["ruleId"] == rule_id
    assert payload["amountPaise"] == 800
    assert teacher.calls == calls
