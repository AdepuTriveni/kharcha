"""``model-shadow`` payload (PROJECT_SPEC §10.5): what each tier said about one raw event.

No message text: only the extracted fields, so the topic is safe to keep for 7 days.
"""

from enum import StrEnum

from pydantic import Field

from kharcha_common.events.base import CamelModel
from kharcha_common.events.enums import Direction, ParseMethod, TxnStatus
from kharcha_common.money import Paise


class TierOutcome(StrEnum):
    TRANSACTION = "TRANSACTION"
    NOT_TRANSACTION = "NOT_TRANSACTION"
    FAILED = "FAILED"


class TierResult(CamelModel):
    parse_method: ParseMethod
    version: str | None = Field(default=None, description="rule id or model version")
    outcome: TierOutcome
    amount_paise: Paise | None = None
    direction: Direction | None = None
    status: TxnStatus | None = None
    merchant_raw: str | None = None
    reference_id: str | None = None


class ModelShadowPayload(CamelModel):
    raw_event_id: str
    tier_results: list[TierResult] = Field(min_length=2)
    agreement: bool
