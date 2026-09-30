"""Payloads for the Phase 1 pipeline topics (PROJECT_SPEC §7.3).

Agent, feedback and nudge payloads are added in their phases (see docs/PROGRESS.md notes).
"""

from pydantic import AwareDatetime, Field

from kharcha_common.events.base import CamelModel
from kharcha_common.events.enums import (
    CashEntryType,
    Channel,
    Direction,
    ParseMethod,
    TxnKind,
    TxnStatus,
)
from kharcha_common.money import Paise, PositivePaise


class DeviceParseResult(CamelModel):
    amount_paise: PositivePaise
    direction: Direction
    channel: Channel
    status: TxnStatus
    merchant_raw: str | None = None
    reference_id: str | None = None


class DeviceParse(CamelModel):
    """Optional on-device model output. The server re-validates it; server result wins."""

    model_version: str
    result: DeviceParseResult
    latency_ms: int = Field(ge=0)


class RawEventPayload(CamelModel):
    source_app: str | None = None
    sender: str | None = None
    title: str | None = None
    text: str | None = None
    posted_at: AwareDatetime
    device_id: str
    redacted: bool
    replay: bool = False
    device_parse: DeviceParse | None = None


class ParsedTransactionPayload(CamelModel):
    raw_event_id: str
    amount_paise: PositivePaise
    currency: str = "INR"
    direction: Direction
    channel: Channel
    status: TxnStatus
    merchant_raw: str | None = None
    counterparty_vpa: str | None = None
    reference_id: str | None = None
    account_hint: str | None = Field(default=None, max_length=4)
    balance_after_paise: Paise | None = None
    txn_time: AwareDatetime
    parse_method: ParseMethod
    model_version: str | None = None
    rule_id: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    promised_refund_days: int | None = Field(default=None, ge=0)


class CleanTransactionPayload(CamelModel):
    """Consumers upsert by ``transactionId`` + ``version``."""

    transaction_id: str
    amount_paise: PositivePaise
    direction: Direction
    kind: TxnKind
    status: TxnStatus
    merchant_id: str | None = None
    merchant_name: str | None = None
    category: str
    is_essential: bool
    reference_id: str | None = None
    txn_time: AwareDatetime
    source_event_ids: list[str]
    version: int = Field(ge=1)


class CashEventPayload(CamelModel):
    entry_type: CashEntryType
    amount_paise: PositivePaise
    category: str | None = None
    note: str | None = None
    related_transaction_id: str | None = None
