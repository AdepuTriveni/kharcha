"""Consumer logic, independent of the Kafka client so it can be unit-tested.

Delivery is at-least-once. Output event ids are derived from input ids, so a redelivered
message republishes the same events and downstream consumers skip them.
"""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kharcha_common.db.models import TransactionSource
from kharcha_common.events import (
    CleanTransactionEvent,
    ParsedTransactionEvent,
    ParsedTransactionPayload,
    RawEvent,
    RawEventType,
)
from kharcha_common.idempotency import is_processed, mark_processed
from kharcha_common.kafka import EventPublisher, PermanentError, derived_event_id
from kharcha_common.topics import Topic
from kharcha_processor import metrics
from kharcha_processor.parser import Failed, parse_raw_event
from kharcha_processor.teacher import Extractor
from kharcha_processor.writer import clean_payload, write_transaction

log = logging.getLogger(__name__)

PARSER_CONSUMER = "processor.parser"
WRITER_CONSUMER = "processor.txn-writer"
PRODUCER = "processor"
PARSED_TYPE = "PARSED_TRANSACTION"
CLEAN_TYPE = "CLEAN_TRANSACTION"

# MANUAL_TEXT / MANUAL_VOICE / WIDGET_TAP / BILL_PHOTO get their own parsers (W3, §21).
BANK_MESSAGE_TYPES = frozenset({RawEventType.RAW_NOTIFICATION, RawEventType.RAW_SMS})


@dataclass(frozen=True)
class ProcessorDeps:
    sessions: async_sessionmaker[AsyncSession]
    publisher: EventPublisher
    teacher: Extractor


async def handle_raw_event(body: bytes, deps: ProcessorDeps) -> None:
    event = RawEvent.model_validate_json(body)
    extra = {"event_id": event.event_id, "consumer": PARSER_CONSUMER}

    async with deps.sessions() as session:
        if await is_processed(session, PARSER_CONSUMER, event.event_id):
            return

    if event.type in BANK_MESSAGE_TYPES:
        outcome = await parse_raw_event(event, deps.teacher)
        if isinstance(outcome, Failed):
            raise PermanentError(f"validation:{outcome.reason.value}")
        if isinstance(outcome, ParsedTransactionPayload):
            await deps.publisher.publish_event(
                Topic.PARSED_TRANSACTIONS,
                ParsedTransactionEvent(
                    event_id=derived_event_id("parsed", event.event_id),
                    user_id=event.user_id,
                    type=PARSED_TYPE,
                    occurred_at=event.occurred_at,
                    producer=PRODUCER,
                    causation_id=event.event_id,
                    payload=outcome,
                ),
            )
            log.info("parsed", extra={**extra, "method": outcome.parse_method.value})
        else:
            log.info("not a transaction", extra={**extra, "reason": type(outcome).__name__})
    else:
        log.info("skipped event type", extra={**extra, "reason": event.type})

    async with deps.sessions.begin() as session:
        await mark_processed(session, PARSER_CONSUMER, event.event_id)


async def handle_parsed_transaction(body: bytes, deps: ProcessorDeps) -> None:
    event = ParsedTransactionEvent.model_validate_json(body)
    async with deps.sessions.begin() as session:
        is_new = await mark_processed(session, WRITER_CONSUMER, event.event_id)
        row = await write_transaction(session, event.user_id, event.payload)
        sources = (
            await session.execute(
                select(TransactionSource.raw_event_id).where(
                    TransactionSource.transaction_id == row.id
                )
            )
        ).scalars()
        payload = clean_payload(row, sorted(str(uuid.UUID(str(s))) for s in sources))
    if is_new:
        metrics.TRANSACTIONS_WRITTEN.labels(row.kind).inc()

    # Published after commit, also on redelivery, so a crash between the two cannot lose it.
    await deps.publisher.publish_event(
        Topic.CLEAN_TRANSACTIONS,
        CleanTransactionEvent(
            event_id=derived_event_id("clean", row.id, str(row.version)),
            user_id=event.user_id,
            type=CLEAN_TYPE,
            occurred_at=event.occurred_at,
            producer=PRODUCER,
            causation_id=event.event_id,
            payload=payload,
        ),
    )
