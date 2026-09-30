from decimal import Decimal

import pytest

from kharcha_common.events import Channel, Direction, TxnStatus
from kharcha_processor.extraction import ExtractionResult, decode_extraction
from kharcha_processor.validation import Validated, ValidationFailure, validate_extraction

TEXT = "Paid Rs.349.00 to zomato@hdfcbank from A/c XX1234. UPI Ref 412345678901. Avl Bal Rs 1,200"


def _result(**overrides: object) -> ExtractionResult:
    base: dict[str, object] = {
        "isTransaction": True,
        "amount": "349.00",
        "direction": "DEBIT",
        "channel": "UPI",
        "status": "SUCCESS",
        "merchantRaw": "zomato@hdfcbank",
        "referenceId": "412345678901",
        "accountHint": "1234",
        "balanceAfter": "1,200",
    }
    base.update(overrides)
    return ExtractionResult.model_validate(base)


def test_valid_extraction() -> None:
    assert validate_extraction(_result(), TEXT) == Validated(34900, 120000)


@pytest.mark.parametrize(
    ("overrides", "failure"),
    [
        ({"isTransaction": False}, ValidationFailure.NOT_TRANSACTION),
        ({"amount": None}, ValidationFailure.MISSING_AMOUNT),
        ({"amount": "three hundred"}, ValidationFailure.BAD_AMOUNT),
        ({"amount": "0"}, ValidationFailure.BAD_AMOUNT),
        ({"amount": "394.00"}, ValidationFailure.AMOUNT_NOT_IN_TEXT),
        ({"referenceId": "999999999999"}, ValidationFailure.REFERENCE_NOT_IN_TEXT),
        ({"direction": None}, ValidationFailure.MISSING_DIRECTION),
        ({"direction": "CREDIT"}, ValidationFailure.DIRECTION_MISMATCH),
        ({"status": "FAILED"}, ValidationFailure.FAILED_WITHOUT_KEYWORD),
        ({"status": "REVERSED"}, ValidationFailure.REVERSED_WITHOUT_KEYWORD),
        ({"balanceAfter": "5,555"}, ValidationFailure.BAD_BALANCE),
    ],
)
def test_validation_failures(overrides: dict[str, object], failure: ValidationFailure) -> None:
    assert validate_extraction(_result(**overrides), TEXT) is failure


def test_failed_status_allowed_with_keyword() -> None:
    text = "Txn of Rs 1,249.50 at AMAZON failed due to insufficient balance"
    result = _result(amount="1,249.50", status="FAILED", referenceId=None, balanceAfter=None)
    assert validate_extraction(result, text) == Validated(124950, None)


def test_decode_reads_numbers_without_float() -> None:
    result = decode_extraction('{"isTransaction": true, "amount": 349.5, "direction": "DEBIT"}')
    assert result.amount == "349.5"
    assert isinstance(Decimal(result.amount), Decimal)


def test_decode_strips_code_fence_and_unknown_channel() -> None:
    raw = '```json\n{"isTransaction": true, "amount": "10", "channel": "IMPS"}\n```'
    result = decode_extraction(raw)
    assert result.channel is Channel.UNKNOWN
    assert result.status is TxnStatus.SUCCESS


def test_decode_rejects_non_object() -> None:
    with pytest.raises(ValueError, match="not a JSON object"):
        decode_extraction("[1, 2]")


def test_direction_enum() -> None:
    assert _result().direction is Direction.DEBIT
