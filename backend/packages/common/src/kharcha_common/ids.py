"""Identifier helpers. Event ids are UUIDv7 (RFC 9562), time-ordered."""

import secrets
import time
import uuid

# Namespace for deterministic (UUIDv5) ids derived from other event ids.
NAMESPACE_EVENTS = uuid.UUID("6f1d3c2a-5b7e-4c1d-9a8f-2e4b6c8d0a1f")


def uuid7(unix_ms: int | None = None) -> uuid.UUID:
    """Generate a UUIDv7: 48-bit Unix ms timestamp, version 7, variant 10, 74 random bits."""
    ms = time.time_ns() // 1_000_000 if unix_ms is None else unix_ms
    if not 0 <= ms < 1 << 48:
        raise ValueError("timestamp out of range for UUIDv7")
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    value = (ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=value)


def uuid7_unix_ms(value: uuid.UUID) -> int:
    """Extract the Unix ms timestamp from a UUIDv7."""
    if value.version != 7:
        raise ValueError("not a UUIDv7")
    return value.int >> 80
