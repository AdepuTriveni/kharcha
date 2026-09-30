"""Request/response bodies for the ingest API (PROJECT_SPEC §9)."""

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, Field

from kharcha_common.events import CamelModel, RawEventPayload, RawEventType

MAX_BATCH = 100
MAX_TEXT_CHARS = 2000
MAX_PAST = timedelta(days=400)
MAX_FUTURE = timedelta(minutes=5)


class UploadEvent(CamelModel):
    """One phone event. The server wraps it in the envelope and adds the user id."""

    event_id: str
    type: RawEventType
    occurred_at: AwareDatetime
    payload: RawEventPayload


class BatchRequest(CamelModel):
    # Items are validated one by one so one bad event does not reject the batch.
    events: list[dict[str, Any]] = Field(max_length=MAX_BATCH)


class EventStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    DUPLICATE = "DUPLICATE"
    REJECTED = "REJECTED"


class EventResult(CamelModel):
    event_id: str | None
    status: EventStatus
    reason: str | None = None


class BatchResponse(CamelModel):
    results: list[EventResult]


def check_upload_rules(event: UploadEvent, now: datetime) -> str | None:
    """Server-side rules from §9. Returns a rejection reason or None."""
    payload = event.payload
    if not payload.redacted:
        return "NOT_REDACTED"
    if payload.text is not None and len(payload.text) > MAX_TEXT_CHARS:
        return "TEXT_TOO_LONG"
    if not now - MAX_PAST <= payload.posted_at <= now + MAX_FUTURE:
        return "POSTED_AT_OUT_OF_RANGE"
    if payload.replay:
        return "REPLAY_NOT_ALLOWED"
    return None
