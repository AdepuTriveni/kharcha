"""The Android upload fixture must be accepted by the ingest API models (ADR-006).

android-app/.../UploadContractTest.kt asserts the app serializes exactly this fixture.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from kharcha_common.cash_parser import parse_cash_entry
from kharcha_common.categories import Category
from kharcha_common.events import CashEntryType
from kharcha_ingest.schemas import BatchRequest, UploadEvent, check_upload_rules

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "android-app/app/src/test/resources/contract/upload_batch.json"
)


def test_android_fixture_is_valid_upload() -> None:
    batch = BatchRequest.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))
    now = datetime(2026, 10, 3, 14, 0, tzinfo=UTC)
    events = [UploadEvent.model_validate(raw) for raw in batch.events]
    assert [e.type.value for e in events] == ["RAW_NOTIFICATION", "RAW_SMS", "RAW_NOTIFICATION"]
    assert all(check_upload_rules(e, now) is None for e in events)
    assert events[1].payload.sender == "AX-HDFCBK"
    device = events[2].payload.device_parse
    assert device is not None
    assert device.result.amount_paise == 8900
    assert device.latency_ms == 820


def test_android_categories_match_backend() -> None:
    fixture = FIXTURE.parent / "categories.json"
    assert json.loads(fixture.read_text(encoding="utf-8")) == [c.value for c in Category]


def test_android_cash_presets_parse() -> None:
    presets = json.loads((FIXTURE.parent / "cash_presets.json").read_text(encoding="utf-8"))
    for preset in presets:
        rupees, paise = divmod(preset["amountPaise"], 100)
        text = f"{rupees}{f'.{paise:02d}' if paise else ''} {preset['note']}"
        entry = parse_cash_entry(text)
        assert entry is not None, text
        assert entry.entry_type is CashEntryType.CASH_SPEND
        assert entry.amount_paise == preset["amountPaise"]
