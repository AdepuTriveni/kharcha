"""Insights entry point (``kharcha-insights``)."""

import asyncio
from dataclasses import dataclass

from faststream import AckPolicy, FastStream
from faststream.confluent import KafkaBroker
from faststream.confluent.annotations import KafkaMessage
from prometheus_client import start_http_server
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kharcha_common.db import make_engine, make_sessionmaker
from kharcha_common.events import AgentTaskEvent, CleanTransactionEvent
from kharcha_common.idempotency import is_processed, mark_processed
from kharcha_common.kafka import (
    BrokerPublisher,
    EventPublisher,
    derived_event_id,
    make_broker,
    run_with_retry_and_dlt,
)
from kharcha_common.logging import configure_logging
from kharcha_common.settings import Settings, get_settings
from kharcha_common.time import utcnow
from kharcha_common.topics import Topic
from kharcha_insights.triggers import tasks_for, to_payload

TRIGGERS_CONSUMER = "insights.triggers"
AGENT_TASK_TYPE = "AGENT_TASK"


@dataclass(frozen=True)
class InsightsDeps:
    sessions: async_sessionmaker[AsyncSession]
    publisher: EventPublisher


async def handle_clean_transaction(body: bytes, deps: InsightsDeps) -> None:
    event = CleanTransactionEvent.model_validate_json(body)
    async with deps.sessions() as session:
        if await is_processed(session, TRIGGERS_CONSUMER, event.event_id):
            return
        specs = await tasks_for(session, event.user_id, event.payload)
    now = utcnow()
    for spec in specs:
        payload = to_payload(spec, event.user_id, now)
        await deps.publisher.publish_event(
            Topic.AGENT_TASKS,
            AgentTaskEvent(
                event_id=derived_event_id("agent-task", event.user_id, spec.dedupe_key),
                user_id=event.user_id,
                type=AGENT_TASK_TYPE,
                occurred_at=event.payload.txn_time,
                producer="insights",
                causation_id=event.event_id,
                payload=payload,
            ),
        )
    async with deps.sessions.begin() as session:
        await mark_processed(session, TRIGGERS_CONSUMER, event.event_id)


def build_broker(settings: Settings) -> KafkaBroker:
    broker = make_broker(settings, client_id="kharcha-insights")
    publisher = BrokerPublisher(broker)
    deps = InsightsDeps(make_sessionmaker(make_engine(settings)), publisher)

    @broker.subscriber(
        Topic.CLEAN_TRANSACTIONS,
        group_id=TRIGGERS_CONSUMER,
        auto_offset_reset="earliest",
        ack_policy=AckPolicy.NACK_ON_ERROR,
    )
    async def on_clean(message: KafkaMessage) -> None:
        key = message.raw_message.key()  # type: ignore[union-attr]
        await run_with_retry_and_dlt(
            lambda body: handle_clean_transaction(body, deps),
            body=message.body,
            key=key if isinstance(key, bytes) else None,
            topic=Topic.CLEAN_TRANSACTIONS,
            consumer=TRIGGERS_CONSUMER,
            publisher=publisher,
        )

    return broker


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    start_http_server(settings.metrics_port + 1)
    asyncio.run(FastStream(build_broker(settings)).run())
