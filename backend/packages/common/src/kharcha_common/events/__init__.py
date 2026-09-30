"""Pydantic event models: the single source of truth for event schemas (ADR-006)."""

from kharcha_common.events.base import SCHEMA_VERSION, CamelModel, EventEnvelope
from kharcha_common.events.enums import (
    CashEntryType,
    Channel,
    Direction,
    ParseMethod,
    RawEventType,
    TxnKind,
    TxnStatus,
)
from kharcha_common.events.payloads import (
    CashEventPayload,
    CleanTransactionPayload,
    DeviceParse,
    DeviceParseResult,
    ParsedTransactionPayload,
    RawEventPayload,
)

RawEvent = EventEnvelope[RawEventPayload]
ParsedTransactionEvent = EventEnvelope[ParsedTransactionPayload]
CleanTransactionEvent = EventEnvelope[CleanTransactionPayload]
CashEvent = EventEnvelope[CashEventPayload]

__all__ = [
    "SCHEMA_VERSION",
    "CamelModel",
    "CashEntryType",
    "CashEvent",
    "CashEventPayload",
    "Channel",
    "CleanTransactionEvent",
    "CleanTransactionPayload",
    "DeviceParse",
    "DeviceParseResult",
    "Direction",
    "EventEnvelope",
    "ParseMethod",
    "ParsedTransactionEvent",
    "ParsedTransactionPayload",
    "RawEvent",
    "RawEventPayload",
    "RawEventType",
    "TxnKind",
    "TxnStatus",
]
