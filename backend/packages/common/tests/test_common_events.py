from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from kharcha_common.events import Direction, RawEvent, RawEventPayload, RawEventType
from kharcha_common.settings import Settings

SPEC_EXAMPLE: dict[str, Any] = {
    "eventId": "0190f3a2-7c1e-7b3a-9d2e-4b1f6a0c9e11",
    "userId": "u_8f2k1",
    "type": "RAW_NOTIFICATION",
    "schemaVersion": 1,
    "occurredAt": "2026-10-03T13:45:12Z",
    "producedAt": "2026-10-03T13:45:20Z",
    "producer": "ingest-api",
    "causationId": None,
    "payload": {
        "sourceApp": "com.phonepe.app",
        "sender": "AX-HDFCBK",
        "title": "Paid to Zomato",
        "text": "Paid Rs.349.00 to zomato@hdfcbank from A/c XX1234. UPI Ref 412345678901",
        "postedAt": "2026-10-03T13:45:12Z",
        "deviceId": "d_91ab",
        "redacted": True,
        "replay": False,
        "deviceParse": {
            "modelVersion": "kharcha-parser-1.5b-q4-v3",
            "result": {
                "amountPaise": 34900,
                "direction": "DEBIT",
                "channel": "UPI",
                "status": "SUCCESS",
                "merchantRaw": "zomato@hdfcbank",
                "referenceId": "412345678901",
            },
            "latencyMs": 820,
        },
    },
}


def test_spec_example_round_trips() -> None:
    event = RawEvent.model_validate(SPEC_EXAMPLE)
    assert event.payload.device_parse is not None
    assert event.payload.device_parse.result.amount_paise == 34900
    assert event.payload.device_parse.result.direction is Direction.DEBIT
    assert event.to_json_dict() == SPEC_EXAMPLE
    assert event.kafka_key() == b"u_8f2k1"


def _with_amount(amount: object) -> dict[str, Any]:
    data: dict[str, Any] = {**SPEC_EXAMPLE, "payload": dict(SPEC_EXAMPLE["payload"])}
    parse = dict(data["payload"]["deviceParse"])
    parse["result"] = {**parse["result"], "amountPaise": amount}
    data["payload"]["deviceParse"] = parse
    return data


@pytest.mark.parametrize("amount", [349.0, "34900", 0, -1])
def test_amount_must_be_positive_int_paise(amount: object) -> None:
    with pytest.raises(ValidationError):
        RawEvent.model_validate(_with_amount(amount))


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValidationError):
        RawEvent.model_validate({**SPEC_EXAMPLE, "occurredAt": "2026-10-03T13:45:12"})


def test_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError):
        RawEvent.model_validate({**SPEC_EXAMPLE, "surprise": 1})


def test_bad_event_id_rejected() -> None:
    with pytest.raises(ValidationError):
        RawEvent.model_validate({**SPEC_EXAMPLE, "eventId": "not-a-uuid"})


def test_default_event_id_is_uuid7() -> None:
    posted = datetime(2026, 10, 3, 13, 45, tzinfo=UTC)
    event = RawEvent(
        user_id="u_1",
        type=RawEventType.MANUAL_TEXT,
        occurred_at=posted,
        producer="test",
        payload=RawEventPayload(posted_at=posted, device_id="d_1", redacted=True),
    )
    assert event.event_id[14] == "7"


def test_prod_requires_database_url() -> None:
    with pytest.raises(ValidationError):
        Settings(env="prod")
    assert Settings(env="prod", database_url="postgresql+asyncpg://x@db/k").env == "prod"
