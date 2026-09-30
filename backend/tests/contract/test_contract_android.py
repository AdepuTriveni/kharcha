"""The Android upload fixture must be accepted by the ingest API models (ADR-006).

android-app/.../UploadContractTest.kt asserts the app serializes exactly this fixture.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from kharcha_ingest.schemas import BatchRequest, UploadEvent, check_upload_rules

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "android-app/app/src/test/resources/contract/upload_batch.json"
)


def test_android_fixture_is_valid_upload() -> None:
    batch = BatchRequest.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))
    now = datetime(2026, 10, 3, 14, 0, tzinfo=UTC)
    events = [UploadEvent.model_validate(raw) for raw in batch.events]
    assert [e.type.value for e in events] == ["RAW_NOTIFICATION", "RAW_SMS"]
    assert all(check_upload_rules(e, now) is None for e in events)
    assert events[1].payload.sender == "AX-HDFCBK"
