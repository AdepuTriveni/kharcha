"""Extraction output schema shared by tiers 2-4 (PROJECT_SPEC §10.3)."""

import json
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from kharcha_common.events import Channel, Direction, TxnStatus
from kharcha_common.prompts import Prompt


class ExtractionResult(BaseModel):
    """What a model returns for one message. Amounts stay as text until validation."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")

    is_transaction: bool
    amount: str | None = None
    direction: Direction | None = None
    channel: Channel = Channel.UNKNOWN
    status: TxnStatus = TxnStatus.SUCCESS
    merchant_raw: str | None = None
    counterparty_vpa: str | None = None
    reference_id: str | None = None
    account_hint: str | None = None
    balance_after: str | None = None
    promised_refund_days: int | None = Field(default=None, ge=0)

    @field_validator("amount", "balance_after", "reference_id", "account_hint", mode="before")
    @classmethod
    def _numbers_as_text(cls, value: Any) -> Any:
        # Models sometimes emit numbers; JSON is decoded with Decimal so no float appears.
        if isinstance(value, Decimal | int) and not isinstance(value, bool):
            return str(value)
        return value

    @field_validator("channel", mode="before")
    @classmethod
    def _unknown_channel(cls, value: Any) -> Any:
        return value if value in Channel.__members__ else Channel.UNKNOWN

    @field_validator("merchant_raw", "counterparty_vpa", mode="before")
    @classmethod
    def _blank_is_none(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value


def decode_extraction(raw: str) -> ExtractionResult:
    """Decode model JSON output. Floats are read as Decimal (never float money)."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    data = json.loads(text, parse_float=Decimal)
    if not isinstance(data, dict):
        raise ValueError("extraction output is not a JSON object")
    return ExtractionResult.model_validate(data)


def build_messages(
    prompt: Prompt, *, sender: str | None, source_app: str | None, text: str
) -> list[dict[str, str]]:
    user = (
        prompt.section("user")
        .replace("{sender}", sender or "unknown")
        .replace("{source_app}", source_app or "unknown")
        .replace("{text}", text.replace(">>>", "> > >"))
    )
    return [
        {"role": "system", "content": prompt.section("system")},
        {"role": "user", "content": user},
    ]
