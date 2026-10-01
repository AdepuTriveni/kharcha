"""Labeled examples for parsing (PROJECT_SPEC §10.3, §15.2-15.3).

One JSON object per line. ``label`` uses the §10.3 extraction schema; amounts are kept as the
text written in the message (e.g. ``"1,249.50"``), never as floats.
"""

import json
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

Direction = Literal["DEBIT", "CREDIT"]
Channel = Literal["UPI", "CARD", "ATM", "NETBANKING", "WALLET", "CASH", "UNKNOWN"]
Status = Literal["SUCCESS", "FAILED", "PENDING", "REVERSED", "REFUND_INITIATED"]
Source = Literal["REAL", "SYNTHETIC", "HARD_NEGATIVE"]


class Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class Extraction(Camel):
    is_transaction: bool
    amount: str | None = None
    direction: Direction | None = None
    channel: Channel | None = None
    status: Status | None = None
    merchant_raw: str | None = None
    counterparty_vpa: str | None = None
    reference_id: str | None = None
    account_hint: str | None = None
    balance_after: str | None = None
    promised_refund_days: int | None = None


class LabeledExample(Camel):
    event_id: str
    sender: str | None = None
    source_app: str | None = None
    text: str
    source: Source = "REAL"
    label: Extraction
    reviewed: bool = False
    template: str = Field(default="", description="sender template signature (§15.3)")


_VPA = re.compile(r"\b[\w.\-]+@[a-z][a-z0-9]+\b", re.I)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_MASKED = re.compile(r"(?:\bXX|\*\*|\bx)\d{3,4}\b", re.I)
_CAPS_WORD = re.compile(r"\b[A-Z][A-Z0-9&.'-]{2,}\b")
_SPACES = re.compile(r"\s+")


def template_signature(text: str, sender: str | None = None) -> str:
    """Message with numbers, VPAs, account hints and capitalised names masked.

    Two messages from the same bank template map to the same signature, which keeps one
    template inside a single split (§15.3).
    """
    t = _VPA.sub("<vpa>", text)
    t = _MASKED.sub("<acct>", t)
    t = _NUMBER.sub("<n>", t)
    t = _CAPS_WORD.sub("<name>", t)
    t = _SPACES.sub(" ", t).strip().lower()
    prefix = (sender or "").split("-")[-1].upper()
    return f"{prefix}|{t}"


def read_jsonl(path: Path) -> Iterator[LabeledExample]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield LabeledExample.model_validate(json.loads(line))


def write_jsonl(path: Path, examples: Iterable[LabeledExample]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for example in examples:
            handle.write(example.model_dump_json(by_alias=True) + "\n")
            count += 1
    return count


def append_jsonl(path: Path, example: LabeledExample) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(example.model_dump_json(by_alias=True) + "\n")
