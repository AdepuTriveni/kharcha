"""Kafka helpers shared by all services (PROJECT_SPEC §7.2).

- Producers use ``acks=all`` + idempotence; key = ``user_id``.
- Consumers commit offsets only after the handler returns (``AckPolicy.NACK_ON_ERROR``),
  and handlers return only after their DB transaction commits.
- :func:`run_with_retry_and_dlt` retries transient errors 3x with exponential backoff and then
  publishes the original message + error headers to ``<topic>.DLT``.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from faststream.confluent import KafkaBroker
from pydantic import ValidationError
from tenacity import (
    AsyncRetrying,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from kharcha_common.events.base import EventEnvelope
from kharcha_common.ids import NAMESPACE_EVENTS
from kharcha_common.settings import Settings
from kharcha_common.topics import DLT_SUFFIX

log = logging.getLogger(__name__)


class PermanentError(Exception):
    """Processing can never succeed for this message; send it to the DLT without retrying."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class EventPublisher(Protocol):
    async def publish_event(self, topic: str, event: EventEnvelope[Any]) -> None: ...

    async def publish_raw(
        self, topic: str, body: bytes, key: bytes | None, headers: Mapping[str, str]
    ) -> None: ...


def derived_event_id(*parts: str) -> str:
    """Deterministic event id so a re-processed input yields the same output event id."""
    return str(uuid.uuid5(NAMESPACE_EVENTS, "/".join(parts)))


def make_broker(settings: Settings, *, client_id: str) -> KafkaBroker:
    return KafkaBroker(
        settings.kafka_bootstrap_servers,
        client_id=client_id,
        acks="all",
        enable_idempotence=True,
        allow_auto_create_topics=False,
    )


class BrokerPublisher:
    """:class:`EventPublisher` backed by a FastStream broker."""

    def __init__(self, broker: KafkaBroker) -> None:
        self._broker = broker

    async def publish_event(self, topic: str, event: EventEnvelope[Any]) -> None:
        await self._broker.publish(
            event.model_dump_json(by_alias=True).encode(),
            topic=topic,
            key=event.kafka_key(),
            headers={"content-type": "application/json", "event-type": event.type},
        )

    async def publish_raw(
        self, topic: str, body: bytes, key: bytes | None, headers: Mapping[str, str]
    ) -> None:
        await self._broker.publish(body, topic=topic, key=key, headers=dict(headers))


async def run_with_retry_and_dlt(
    handler: Callable[[bytes], Awaitable[None]],
    *,
    body: bytes,
    key: bytes | None,
    topic: str,
    consumer: str,
    publisher: EventPublisher,
    attempts: int = 3,
    max_wait_s: float = 4.0,
) -> None:
    """Run ``handler(body)``; on repeated or permanent failure publish to ``<topic>.DLT``.

    Returns normally in both cases so the offset can be committed. If publishing to the DLT
    itself fails, the exception propagates and the message is redelivered.
    """
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(attempts),
            wait=wait_exponential(multiplier=0.5, max=max_wait_s),
            retry=retry_if_not_exception_type((PermanentError, ValidationError)),
            reraise=True,
        ):
            with attempt:
                await handler(body)
    except Exception as exc:
        reason = exc.reason if isinstance(exc, PermanentError) else type(exc).__name__
        log.warning(
            "sending message to DLT", extra={"topic": topic, "consumer": consumer, "reason": reason}
        )
        await publisher.publish_raw(
            f"{topic}{DLT_SUFFIX}",
            body,
            key,
            {
                "x-dlt-source-topic": topic,
                "x-dlt-consumer": consumer,
                "x-dlt-reason": reason[:500],
                "x-dlt-error-type": type(exc).__name__,
            },
        )
