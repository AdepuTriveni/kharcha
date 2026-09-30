"""Time helpers. Store UTC-aware datetimes; display in IST (CLAUDE.md rule 2)."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


class NaiveDatetimeError(ValueError):
    """Raised when a naive datetime reaches code that needs an aware one."""


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_aware(dt: datetime) -> datetime:
    """Return ``dt`` converted to UTC; reject naive datetimes instead of guessing."""
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise NaiveDatetimeError(f"naive datetime not allowed: {dt.isoformat()}")
    return dt.astimezone(UTC)


def to_ist(dt: datetime) -> datetime:
    """Convert an aware datetime to IST for display."""
    return ensure_aware(dt).astimezone(IST)
