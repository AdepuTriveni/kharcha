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
    PARSER_CONSUMER,
    WRITER_CONSUMER,
    ProcessorDeps,
    handle_cash_event,
    handle_parsed_transaction,
    handle_raw_event,
)
from kharcha_processor.teacher import Extractor, TeacherLLM


def _key(message: KafkaMessage) -> bytes | None:
    key = message.raw_message.key()  # type: ignore[union-attr]
    return key if isinstance(key, bytes) else None


def build_broker(settings: Settings, teacher: Extractor | None = None) -> KafkaBroker:
    broker = make_broker(settings, client_id="kharcha-processor")
    publisher = BrokerPublisher(broker)
    deps = ProcessorDeps(
        sessions=make_sessionmaker(make_engine(settings)),
        publisher=publisher,
        teacher=teacher or TeacherLLM(settings),
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
        group_id=WRITER_CONSUMER,
        auto_offset_reset="earliest",
        ack_policy=AckPolicy.NACK_ON_ERROR,
    )
    async def on_parsed(message: KafkaMessage) -> None:
        await run_with_retry_and_dlt(
            lambda body: handle_parsed_transaction(body, deps),
            body=message.body,
            key=_key(message),
            topic=Topic.PARSED_TRANSACTIONS,
            consumer=WRITER_CONSUMER,
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


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    start_http_server(settings.metrics_port)
    asyncio.run(FastStream(build_broker(settings)).run())
