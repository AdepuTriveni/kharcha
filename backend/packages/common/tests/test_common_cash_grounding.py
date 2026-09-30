from datetime import date
from decimal import Decimal

import pytest

from kharcha_common.cash import CashBalance, balance_from_totals, ledger_id_for
from kharcha_common.cash_parser import guess_category, parse_cash_entry
from kharcha_common.categories import Category
from kharcha_common.events import CashEntryType
from kharcha_common.grounding import allowed_values, check_grounding, extract_numbers


@pytest.mark.parametrize(
    ("text", "entry_type", "paise", "note", "category"),
    [
        ("150 vada pav", CashEntryType.CASH_SPEND, 15000, "vada pav", Category.DINING_OUT),
        ("chai 20", CashEntryType.CASH_SPEND, 2000, "chai", Category.DINING_OUT),
        ("got 500 from mom", CashEntryType.CASH_RECEIVED, 50000, "got from mom", None),
        ("1.2k shoes", CashEntryType.CASH_SPEND, 120000, "shoes", Category.SHOPPING),
        ("atm 2000", CashEntryType.ATM_WITHDRAWAL, 200000, "atm", None),
        ("auto 80/-", CashEntryType.CASH_SPEND, 8000, "auto", Category.TRANSPORT),
        ("Rs 45.50 sabzi", CashEntryType.CASH_SPEND, 4550, "sabzi", Category.GROCERIES),
        (
            "300 random stuff",
            CashEntryType.CASH_SPEND,
            30000,
            "random stuff",
            Category.CASH_UNCATEGORIZED,
        ),
    ],
)
def test_parse_cash_entry(
    text: str, entry_type: CashEntryType, paise: int, note: str, category: Category | None
) -> None:
    entry = parse_cash_entry(text)
    assert entry is not None
    assert (entry.entry_type, entry.amount_paise, entry.note, entry.category) == (
        entry_type,
        paise,
        note,
        category,
    )


@pytest.mark.parametrize(
    "text", ["hello", "how much did I spend?", "150 chai and 20 samosa", "0 chai", "", "x" * 200]
)
def test_not_cash_entries(text: str) -> None:
    assert parse_cash_entry(text) is None


def test_guess_category_default() -> None:
    assert guess_category(None) is Category.CASH_UNCATEGORIZED


def test_cash_balance_never_negative() -> None:
    assert CashBalance(50000, 20000).describe() == "Cash in hand: ₹300.00"
    assert CashBalance(0, 15000).describe() == "You logged ₹150.00 more than tracked cash"
    totals = {"ATM_WITHDRAWAL": 200000, "CASH_RECEIVED": 50000, "CASH_SPEND": 15000}
    assert balance_from_totals(totals).balance_paise == 235000


def test_ledger_id_is_deterministic() -> None:
    assert ledger_id_for("e1") == ledger_id_for("e1") != ledger_id_for("e2")


def test_extract_numbers_indian_formats() -> None:
    values = [v for _, v in extract_numbers("₹1,420 on 4 orders, 1.2k left, broke on the 22nd")]
    assert values == [Decimal(1420), Decimal(4), Decimal(1200), Decimal(22)]


def test_grounding_accepts_tool_numbers_and_rejects_others() -> None:
    allowed = allowed_values(paise=[142000], counts=[4], dates=[date(2026, 10, 22)])
    text = "Bro, 4th biryani this week 🍗 ₹1,420 gone. Broke: the 22nd."
    assert check_grounding(text, allowed).ok
    bad = check_grounding("₹1,500 gone on 4 orders", allowed)
    assert not bad.ok
    assert bad.unmatched == ("₹1,500",)


def test_grounding_rounds_to_rupee() -> None:
    allowed = allowed_values(paise=[34950])
    assert check_grounding("₹350 on food", allowed).ok
    assert check_grounding("₹349.50 on food", allowed).ok
