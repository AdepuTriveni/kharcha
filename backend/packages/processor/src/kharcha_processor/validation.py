"""Validation applied to every parsing tier (PROJECT_SPEC §10.2)."""

import re
from dataclasses import dataclass
from enum import StrEnum

from kharcha_common.events import Direction, TxnStatus
from kharcha_common.money import MoneyError
from kharcha_processor.amounts import all_number_amounts, parse_amount_text
from kharcha_processor.extraction import ExtractionResult


class ValidationFailure(StrEnum):
    NOT_TRANSACTION = "NOT_TRANSACTION"
    BAD_MODEL_OUTPUT = "BAD_MODEL_OUTPUT"
    MISSING_AMOUNT = "MISSING_AMOUNT"
    BAD_AMOUNT = "BAD_AMOUNT"
    AMOUNT_NOT_IN_TEXT = "AMOUNT_NOT_IN_TEXT"
    REFERENCE_NOT_IN_TEXT = "REFERENCE_NOT_IN_TEXT"
    MISSING_DIRECTION = "MISSING_DIRECTION"
    DIRECTION_MISMATCH = "DIRECTION_MISMATCH"
    FAILED_WITHOUT_KEYWORD = "FAILED_WITHOUT_KEYWORD"
    REVERSED_WITHOUT_KEYWORD = "REVERSED_WITHOUT_KEYWORD"
    BAD_BALANCE = "BAD_BALANCE"


_DEBIT = re.compile(
    r"\b(debited|paid|sent|spent|withdrawn|withdrawal|purchase|payment of|transferred to|"
    r"dr\.?)\b",
    re.I,
)
_CREDIT = re.compile(r"\b(credited|received|deposited|cr\.?|refunded|added to)\b", re.I)
_FAILED = re.compile(r"\b(failed|failure|declined|unsuccessful|could not be|rejected)\b", re.I)
_REVERSAL = re.compile(r"\b(revers\w*|refund\w*)\b", re.I)


@dataclass(frozen=True, slots=True)
class Validated:
    amount_paise: int
    balance_after_paise: int | None


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def validate_extraction(result: ExtractionResult, text: str) -> Validated | ValidationFailure:
    if not result.is_transaction:
        return ValidationFailure.NOT_TRANSACTION
    if result.amount is None:
        return ValidationFailure.MISSING_AMOUNT
    try:
        amount = parse_amount_text(result.amount)
    except MoneyError:
        return ValidationFailure.BAD_AMOUNT
    if amount <= 0:
        return ValidationFailure.BAD_AMOUNT
    if amount not in all_number_amounts(text):
        return ValidationFailure.AMOUNT_NOT_IN_TEXT
    if result.reference_id and _normalize(result.reference_id) not in _normalize(text):
        return ValidationFailure.REFERENCE_NOT_IN_TEXT

    if result.direction is None:
        return ValidationFailure.MISSING_DIRECTION
    has_debit, has_credit = bool(_DEBIT.search(text)), bool(_CREDIT.search(text))
    if result.direction is Direction.DEBIT and has_credit and not has_debit:
        return ValidationFailure.DIRECTION_MISMATCH
    if result.direction is Direction.CREDIT and has_debit and not has_credit:
        return ValidationFailure.DIRECTION_MISMATCH

    if result.status is TxnStatus.FAILED and not _FAILED.search(text):
        return ValidationFailure.FAILED_WITHOUT_KEYWORD
    if result.status is TxnStatus.REVERSED and not _REVERSAL.search(text):
        return ValidationFailure.REVERSED_WITHOUT_KEYWORD

    balance = None
    if result.balance_after is not None:
        try:
            balance = parse_amount_text(result.balance_after)
        except MoneyError:
            return ValidationFailure.BAD_BALANCE
        if abs(balance) not in all_number_amounts(text):
            return ValidationFailure.BAD_BALANCE
    return Validated(amount_paise=amount, balance_after_paise=balance)
