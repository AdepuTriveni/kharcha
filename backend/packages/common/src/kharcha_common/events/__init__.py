"""Pydantic event models: the single source of truth for event schemas (ADR-006)."""

from kharcha_common.events.agents import AgentName, AgentTaskPayload, AgentTrigger, Priority
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
from kharcha_common.events.shadow import ModelShadowPayload, TierOutcome, TierResult

RawEvent = EventEnvelope[RawEventPayload]
ParsedTransactionEvent = EventEnvelope[ParsedTransactionPayload]
CleanTransactionEvent = EventEnvelope[CleanTransactionPayload]
CashEvent = EventEnvelope[CashEventPayload]
AgentTaskEvent = EventEnvelope[AgentTaskPayload]
ModelShadowEvent = EventEnvelope[ModelShadowPayload]

__all__ = [
    "SCHEMA_VERSION",
    "AgentName",
    "AgentTaskEvent",
    "AgentTaskPayload",
    "AgentTrigger",
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
    "ModelShadowEvent",
    "ModelShadowPayload",
    "ParseMethod",
    "ParsedTransactionEvent",
    "ParsedTransactionPayload",
    "Priority",
    "RawEvent",
    "RawEventPayload",
    "RawEventType",
    "TierOutcome",
    "TierResult",
    "TxnKind",
    "TxnStatus",
]
