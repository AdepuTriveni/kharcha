"""Consumer logic, independent of the Kafka client so it can be unit-tested.

Delivery is at-least-once. Output event ids are derived from input ids, so a redelivered
message republishes the same events and downstream consumers skip them.
"""

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kharcha_common.cash_parser import parse_cash_entry
from kharcha_common.events import (
    CashEvent,
    CashEventPayload,
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
from kharcha_processor.cash import write_cash_entry
from kharcha_processor.dedup import apply_parsed, clean_payload
from kharcha_processor.parser import Failed, parse_raw_event
from kharcha_processor.teacher import Extractor

log = logging.getLogger(__name__)

PARSER_CONSUMER = "processor.parser"
DEDUP_CONSUMER = "processor.dedup"
CASH_CONSUMER = "processor.cash"
PRODUCER = "processor"
PARSED_TYPE = "PARSED_TRANSACTION"
CLEAN_TYPE = "CLEAN_TRANSACTION"
CASH_TYPE = "CASH_EVENT"

# MANUAL_VOICE / WIDGET_TAP / BILL_PHOTO get their own parsers (W6, §21).
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
    elif event.type == RawEventType.MANUAL_TEXT:
        await _handle_manual_text(event, deps)
    else:
        log.info("skipped event type", extra={**extra, "reason": event.type})

    async with deps.sessions.begin() as session:
        await mark_processed(session, PARSER_CONSUMER, event.event_id)


async def _handle_manual_text(event: RawEvent, deps: ProcessorDeps) -> None:
    entry = parse_cash_entry(event.payload.text or "")
    if entry is None:
        log.info("manual text is not a cash entry", extra={"event_id": event.event_id})
        return
    await deps.publisher.publish_event(
        Topic.CASH_EVENTS,
        CashEvent(
            event_id=derived_event_id("cash", event.event_id),
            user_id=event.user_id,
            type=CASH_TYPE,
            occurred_at=event.occurred_at,
            producer=PRODUCER,
            causation_id=event.event_id,
            payload=CashEventPayload(
                entry_type=entry.entry_type,
                amount_paise=entry.amount_paise,
                category=entry.category.value if entry.category else None,
                note=entry.note,
            ),
        ),
    )


async def handle_cash_event(body: bytes, deps: ProcessorDeps) -> None:
    event = CashEvent.model_validate_json(body)
    async with deps.sessions.begin() as session:
        if await mark_processed(session, CASH_CONSUMER, event.event_id):
            await write_cash_entry(session, event)


async def handle_parsed_transaction(body: bytes, deps: ProcessorDeps) -> None:
    """processor.dedup: merge or insert, then republish every changed row (§11)."""
    event = ParsedTransactionEvent.model_validate_json(body)
    async with deps.sessions.begin() as session:
        is_new = await mark_processed(session, DEDUP_CONSUMER, event.event_id)
        rows = await apply_parsed(session, event.user_id, event.payload)
        payloads = [await clean_payload(session, row) for row in rows]
    if is_new:
        for p in payloads:
            metrics.TRANSACTIONS_WRITTEN.labels(p.kind.value).inc()

    # Published after commit, also on redelivery, so a crash between the two cannot lose it.
    for payload in payloads:
        await deps.publisher.publish_event(
            Topic.CLEAN_TRANSACTIONS,
            CleanTransactionEvent(
                event_id=derived_event_id("clean", payload.transaction_id, str(payload.version)),
                user_id=event.user_id,
                type=CLEAN_TYPE,
                occurred_at=event.occurred_at,
                producer=PRODUCER,
                causation_id=event.event_id,
                payload=payload,
            ),
        )
