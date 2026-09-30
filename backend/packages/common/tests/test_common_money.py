from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kharcha_common.money import MoneyError, format_inr, paise_to_rupees, rupees_to_paise


@pytest.mark.parametrize(
    ("text", "paise"),
    [
        ("Rs.349.00", 34900),
        ("Rs 1,234.5", 123450),
        ("₹ 20", 2000),
        ("INR 1,00,000.05", 10000005),
        ("0.01", 1),
        ("-15.25", -1525),
    ],
)
def test_rupees_text_to_paise(text: str, paise: int) -> None:
    assert rupees_to_paise(text) == paise


@pytest.mark.parametrize("bad", ["", "abc", "1.234", "Rs.", "1.2.3"])
def test_rejects_bad_text(bad: str) -> None:
    with pytest.raises(MoneyError):
        rupees_to_paise(bad)


def test_rejects_float_and_bool() -> None:
    with pytest.raises(MoneyError):
        rupees_to_paise(3.5)  # type: ignore[arg-type]
    with pytest.raises(MoneyError):
        rupees_to_paise(True)


def test_rejects_sub_paisa_decimal() -> None:
    with pytest.raises(MoneyError):
        rupees_to_paise(Decimal("1.005"))


@given(st.integers(min_value=-(10**15), max_value=10**15))
def test_paise_decimal_round_trip(paise: int) -> None:
    assert rupees_to_paise(paise_to_rupees(paise)) == paise


@given(st.integers(min_value=0, max_value=10**15))
def test_format_round_trip(paise: int) -> None:
    assert rupees_to_paise(format_inr(paise)) == paise


@pytest.mark.parametrize(
    ("paise", "text"),
    [
        (0, "₹0.00"),
        (34900, "₹349.00"),
        (123456, "₹1,234.56"),
        (12345678, "₹1,23,456.78"),
        (1234567890, "₹1,23,45,678.90"),
        (-5000, "-₹50.00"),
    ],
)
def test_format_inr_indian_grouping(paise: int, text: str) -> None:
    assert format_inr(paise) == text
