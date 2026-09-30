from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kharcha_common.ids import uuid7, uuid7_unix_ms
from kharcha_common.time import NaiveDatetimeError, ensure_aware, to_ist, utcnow


def test_utcnow_is_aware() -> None:
    assert utcnow().tzinfo is UTC


def test_naive_rejected() -> None:
    with pytest.raises(NaiveDatetimeError):
        ensure_aware(datetime(2026, 10, 3, 13, 45))


def test_to_ist() -> None:
    dt = datetime(2026, 10, 3, 13, 45, tzinfo=UTC)
    ist = to_ist(dt)
    assert (ist.hour, ist.minute) == (19, 15)
    assert ist.utcoffset() == timedelta(hours=5, minutes=30)


def test_ensure_aware_converts_to_utc() -> None:
    dt = datetime(2026, 10, 3, 19, 15, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert ensure_aware(dt) == datetime(2026, 10, 3, 13, 45, tzinfo=UTC)


@given(st.integers(min_value=0, max_value=(1 << 48) - 1))
def test_uuid7_layout(ms: int) -> None:
    value = uuid7(ms)
    assert value.version == 7
    assert value.variant == "specified in RFC 4122"
    assert uuid7_unix_ms(value) == ms


def test_uuid7_time_ordered() -> None:
    assert str(uuid7(1_000)) < str(uuid7(2_000))
