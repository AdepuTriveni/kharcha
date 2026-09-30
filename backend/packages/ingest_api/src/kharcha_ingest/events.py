"""``POST /v1/events:batch``: store raw events idempotently, then publish to ``raw-events``.

Order matters for at-least-once delivery: commit to Postgres first, then publish. If the
publish fails the client gets 503 and retries; the retry finds the rows (DUPLICATE) and
publishes again. The processor is idempotent on ``eventId``.
"""

import logging
import uuid

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kharcha_common.db.models import RawEventRow
from kharcha_common.events import RawEvent
from kharcha_common.kafka import EventPublisher
from kharcha_common.time import utcnow
from kharcha_common.topics import Topic
from kharcha_ingest import metrics
from kharcha_ingest.auth import CurrentUser
from kharcha_ingest.schemas import (
    BatchRequest,
    BatchResponse,
    EventResult,
    EventStatus,
    UploadEvent,
    check_upload_rules,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1")
PRODUCER = "ingest-api"


def _to_envelope(user_id: str, item: UploadEvent) -> RawEvent:
    return RawEvent(
        event_id=item.event_id,
        user_id=user_id,
        type=item.type.value,
        occurred_at=item.occurred_at,
        producer=PRODUCER,
        payload=item.payload,
    )


def _row(event: RawEvent) -> dict[str, object]:
    p = event.payload
    return {
        "event_id": uuid.UUID(event.event_id),
        "user_id": event.user_id,
        "type": event.type,
        "source_app": p.source_app,
        "sender": p.sender,
        "title": p.title,
        "text": p.text,
        "device_parse": p.device_parse.model_dump(mode="json", by_alias=True)
        if p.device_parse
        else None,
        "posted_at": p.posted_at,
    }


async def _store(
    session: AsyncSession, user_id: str, events: list[RawEvent]
) -> dict[str, EventStatus]:
    """Insert new rows; classify each event as ACCEPTED, DUPLICATE or REJECTED (other user)."""
    ids = [uuid.UUID(e.event_id) for e in events]
    inserted = set(
        (
            await session.execute(
                insert(RawEventRow)
                .values([_row(e) for e in events])
                .on_conflict_do_nothing()
                .returning(RawEventRow.event_id)
            )
        ).scalars()
    )
    owner_rows = await session.execute(
        select(RawEventRow.event_id, RawEventRow.user_id).where(RawEventRow.event_id.in_(ids))
    )
    owners = dict(owner_rows.all())
    out: dict[str, EventStatus] = {}
    for event_id in ids:
        if event_id in inserted:
            out[str(event_id)] = EventStatus.ACCEPTED
        elif owners.get(event_id) == user_id:
            out[str(event_id)] = EventStatus.DUPLICATE
        else:
            out[str(event_id)] = EventStatus.REJECTED
    return out


@router.post("/events:batch")
async def upload_events(
    body: BatchRequest, user_id: CurrentUser, request: Request
) -> BatchResponse:
    sessions: async_sessionmaker[AsyncSession] = request.app.state.sessions
    publisher: EventPublisher = request.app.state.publisher
    now = utcnow()

    results: list[EventResult] = []
    valid: dict[str, RawEvent] = {}
    for raw in body.events:
        try:
            item = UploadEvent.model_validate(raw)
            event = _to_envelope(user_id, item)
        except ValidationError:
            event_id = raw.get("eventId") if isinstance(raw.get("eventId"), str) else None
            results.append(
                EventResult(event_id=event_id, status=EventStatus.REJECTED, reason="INVALID")
            )
            continue
        if (reason := check_upload_rules(item, now)) is not None:
            results.append(
                EventResult(event_id=event.event_id, status=EventStatus.REJECTED, reason=reason)
            )
            continue
        valid.setdefault(event.event_id, event)

    statuses: dict[str, EventStatus] = {}
    if valid:
        async with sessions.begin() as session:
            statuses = await _store(session, user_id, list(valid.values()))
        try:
            for event_id, event_status in statuses.items():
                if event_status is not EventStatus.REJECTED:
                    await publisher.publish_event(Topic.RAW_EVENTS, valid[event_id])
        except Exception as exc:
            log.exception("publish to raw-events failed")
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "retry later") from exc

    for event_id, event_status in statuses.items():
        reason = "EVENT_ID_TAKEN" if event_status is EventStatus.REJECTED else None
        results.append(EventResult(event_id=event_id, status=event_status, reason=reason))
    for result in results:
        metrics.EVENTS_RECEIVED.labels(result.status.value).inc()
    return BatchResponse(results=results)
