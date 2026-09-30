import pytest
from hypothesis import given
from hypothesis import strategies as st

from kharcha_common.money import format_inr
from kharcha_processor.amounts import all_number_amounts, currency_amounts
from kharcha_processor.prefilter import DropReason, prefilter


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Paid Rs.349 to Zomato", [34900]),
        ("Rs 349.00 debited", [34900]),
        ("INR 1,249.50 spent on card XX1234", [124950]),
        ("₹2,000 received from Ravi", [200000]),
        ("Rs.1,00,000 credited", [10000000]),
        ("INR 12,34,567.89 credited", [123456789]),
        ("inr10 sent", [1000]),
        ("Rs.349.00 debited. Avl Bal Rs 12,000.50", [34900, 1200050]),
        ("for 2 hours 5 mins", []),
    ],
)
def test_currency_amounts(text: str, expected: list[int]) -> None:
    assert currency_amounts(text) == expected


def test_all_numbers_includes_bare_numbers() -> None:
    assert 34900 in all_number_amounts("Sent 349.00 to ravi@okaxis")


@given(st.integers(min_value=1, max_value=10**12))
def test_formatted_amount_is_found(paise: int) -> None:
    rendered = format_inr(paise).replace("₹", "Rs.")
    assert currency_amounts(f"Paid {rendered} to someone") == [paise]


TRANSACTIONS = [
    "Paid Rs.349.00 to zomato@hdfcbank from A/c XX1234. UPI Ref 412345678901",
    "Rs.500.00 debited from a/c **1234 on 03-10-26 to VPA ravi@okaxis. Ref No 412345678901",
    "Your A/c XX1234 is credited with INR 25,000.00 on 01-10-26 by NEFT. Avl Bal INR 40,120.50",
    "Txn of Rs 1,249.50 on HDFC Bank Card x4321 at AMAZON failed due to insufficient balance",
    "Cashback of Rs 20 credited to your wallet",
    "You've received ₹150 from Mom",
]


@pytest.mark.parametrize("text", TRANSACTIONS)
def test_transactions_pass_prefilter(text: str) -> None:
    assert prefilter(text) is None


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("", DropReason.EMPTY),
        (None, DropReason.EMPTY),
        ("123456 is your OTP for txn of Rs 349 at Zomato. Do not share.", DropReason.OTP),
        ("Get cashback up to Rs 500 on your next recharge! T&C apply", DropReason.PROMO),
        ("You are pre-approved for a loan of Rs 5,00,000. Apply now", DropReason.PROMO),
        ("Avl Bal in A/c XX1234 is Rs 12,000.50 as on 03-10-26", DropReason.BALANCE_ONLY),
        ("ravi@okaxis has requested money Rs 200 via UPI", DropReason.COLLECT_REQUEST),
        ("Your statement is ready", DropReason.NO_AMOUNT),
    ],
)
def test_prefilter_drops(text: str | None, reason: DropReason) -> None:
    assert prefilter(text) is reason
