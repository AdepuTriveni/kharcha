"""Processor service entry point (``kharcha-processor``): FastStream wiring only."""

import asyncio

from faststream import AckPolicy, FastStream
from faststream.confluent import KafkaBroker
from faststream.confluent.annotations import KafkaMessage
from prometheus_client import start_http_server

from kharcha_common.db import make_engine, make_sessionmaker
from kharcha_common.kafka import BrokerPublisher, make_broker, run_with_retry_and_dlt
from kharcha_common.logging import configure_logging
from kharcha_common.settings import Settings, get_settings
from kharcha_common.topics import Topic
from kharcha_processor.handlers import (
    CASH_CONSUMER,
    DEDUP_CONSUMER,
    PARSER_CONSUMER,
    ProcessorDeps,
    handle_cash_event,
    handle_parsed_transaction,
    handle_raw_event,
)
from kharcha_processor.rule_store import seed_rules
from kharcha_processor.synthesis import RuleWriter, TeacherRuleWriter
from kharcha_processor.teacher import Extractor, TeacherLLM


def _key(message: KafkaMessage) -> bytes | None:
    key = message.raw_message.key()  # type: ignore[union-attr]
    return key if isinstance(key, bytes) else None


def build_broker(
    settings: Settings, teacher: Extractor | None = None, rule_writer: RuleWriter | None = None
) -> KafkaBroker:
    broker = make_broker(settings, client_id="kharcha-processor")
    publisher = BrokerPublisher(broker)
    if rule_writer is None and settings.rule_synthesis_enabled:
        rule_writer = TeacherRuleWriter(settings)
    model = (
        TeacherLLM(settings.model_copy(update={"llm_model": settings.server_model}))
        if settings.server_model
        else None
    )
    deps = ProcessorDeps(
        sessions=make_sessionmaker(make_engine(settings)),
        publisher=publisher,
        teacher=teacher or TeacherLLM(settings),
        rule_writer=rule_writer,
        shadow_rate=settings.parser_shadow_rate,
        model=model,
        model_shadow=settings.server_model_mode == "SHADOW",
    )

    @broker.subscriber(
        Topic.RAW_EVENTS,
        group_id=PARSER_CONSUMER,
        auto_offset_reset="earliest",
        ack_policy=AckPolicy.NACK_ON_ERROR,
    )
    async def on_raw_event(message: KafkaMessage) -> None:
        await run_with_retry_and_dlt(
            lambda body: handle_raw_event(body, deps),
            body=message.body,
            key=_key(message),
            topic=Topic.RAW_EVENTS,
            consumer=PARSER_CONSUMER,
            publisher=publisher,
        )

    @broker.subscriber(
        Topic.PARSED_TRANSACTIONS,
        group_id=DEDUP_CONSUMER,
        auto_offset_reset="earliest",
        ack_policy=AckPolicy.NACK_ON_ERROR,
    )
    async def on_parsed(message: KafkaMessage) -> None:
        await run_with_retry_and_dlt(
            lambda body: handle_parsed_transaction(body, deps),
            body=message.body,
            key=_key(message),
            topic=Topic.PARSED_TRANSACTIONS,
            consumer=DEDUP_CONSUMER,
            publisher=publisher,
        )

    @broker.subscriber(
        Topic.CASH_EVENTS,
        group_id=CASH_CONSUMER,
        auto_offset_reset="earliest",
        ack_policy=AckPolicy.NACK_ON_ERROR,
    )
    async def on_cash(message: KafkaMessage) -> None:
        await run_with_retry_and_dlt(
            lambda body: handle_cash_event(body, deps),
            body=message.body,
            key=_key(message),
            topic=Topic.CASH_EVENTS,
            consumer=CASH_CONSUMER,
            publisher=publisher,
        )

    return broker


async def _seed_rules(settings: Settings) -> None:
    """Insert hand-written rules once; existing rows keep their counters and status."""
    engine = make_engine(settings)
    try:
        async with make_sessionmaker(engine).begin() as session:
            await seed_rules(session)
    finally:
        await engine.dispose()


async def _main(settings: Settings) -> None:
    await _seed_rules(settings)
    await FastStream(build_broker(settings)).run()


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    start_http_server(settings.metrics_port)
    asyncio.run(_main(settings))
