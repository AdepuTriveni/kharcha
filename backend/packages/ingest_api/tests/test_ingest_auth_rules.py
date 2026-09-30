from datetime import UTC, datetime, timedelta

import httpx
import pytest

from kharcha_common.events import RawEventPayload, RawEventType
from kharcha_common.settings import Settings
from kharcha_ingest.auth import hash_api_key, new_api_key, resolve_user
from kharcha_ingest.main import create_app
from kharcha_ingest.schemas import UploadEvent, check_upload_rules

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


def test_resolve_user() -> None:
    key = new_api_key()
    settings = Settings(env="test", api_keys={hash_api_key(key): "u_1"})
    assert resolve_user(settings, key) == "u_1"
    assert resolve_user(settings, key + "x") is None


def _upload(**payload: object) -> UploadEvent:
    base: dict[str, object] = {
        "posted_at": NOW,
        "device_id": "d_1",
        "redacted": True,
        "text": "Paid Rs.10 to x@y",
    }
    base.update(payload)
    return UploadEvent(
        event_id="0190f3a2-7c1e-7b3a-9d2e-4b1f6a0c9e11",
        type=RawEventType.RAW_NOTIFICATION,
        occurred_at=NOW,
        payload=RawEventPayload.model_validate(base),
    )


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({}, None),
        ({"redacted": False}, "NOT_REDACTED"),
        ({"text": "x" * 2001}, "TEXT_TOO_LONG"),
        ({"posted_at": NOW - timedelta(days=401)}, "POSTED_AT_OUT_OF_RANGE"),
        ({"posted_at": NOW + timedelta(minutes=6)}, "POSTED_AT_OUT_OF_RANGE"),
        ({"replay": True}, "REPLAY_NOT_ALLOWED"),
    ],
)
def test_upload_rules(payload: dict[str, object], reason: str | None) -> None:
    assert check_upload_rules(_upload(**payload), NOW) == reason


async def test_upload_requires_api_key() -> None:
    app = create_app(Settings(env="test"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.post("/v1/events:batch", json={"events": []})
        wrong = await client.post(
            "/v1/events:batch", json={"events": []}, headers={"Authorization": "Bearer nope"}
        )
    assert missing.status_code == 401
    assert wrong.status_code == 401


async def test_batch_limit() -> None:
    key = new_api_key()
    app = create_app(Settings(env="test", api_keys={hash_api_key(key): "u_1"}))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/events:batch",
            json={"events": [{}] * 101},
            headers={"Authorization": f"Bearer {key}"},
        )
    assert response.status_code == 422
