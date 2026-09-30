"""Rule parser for chat cash entries (PROJECT_SPEC §13).

Examples: ``150 vada pav``, ``chai 20``, ``got 500 from mom``, ``1.2k shoes``, ``atm 2000``.
The own model / teacher fallback for free-form entries arrives with W6.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from kharcha_common.categories import Category
from kharcha_common.events import CashEntryType
from kharcha_common.money import MoneyError, rupees_to_paise

_AMOUNT = re.compile(
    r"(?<![\w.])(?:rs\.?|inr|₹)?\s*([0-9]+(?:\.[0-9]{1,2})?)\s*(k|/-)?(?![\w.])", re.I
)
_RECEIVED = re.compile(r"\b(got|received|recieved|mila|mile|milaa|from|credited)\b", re.I)
_ATM = re.compile(r"\b(atm|withdrew|withdrawn|withdrawal|nikale|nikala)\b", re.I)
_FILLER = re.compile(r"\b(rs|inr|rupees?|for|on|spent|paid|kharcha|diye|diya|de diye)\b", re.I)
MAX_CASH_PAISE = 10_00_000_00  # ₹10 lakh; anything larger is almost certainly a typo

_CATEGORY_WORDS: dict[Category, tuple[str, ...]] = {
    Category.DINING_OUT: (
        "chai",
        "tea",
        "coffee",
        "vada",
        "pav",
        "samosa",
        "lunch",
        "dinner",
        "breakfast",
        "biryani",
        "dosa",
        "idli",
        "momos",
        "pani puri",
        "golgappe",
        "snack",
        "juice",
        "canteen",
    ),
    Category.GROCERIES: (
        "sabzi",
        "vegetables",
        "veggies",
        "milk",
        "doodh",
        "grocery",
        "kirana",
        "fruits",
        "eggs",
        "atta",
        "rice",
    ),
    Category.TRANSPORT: (
        "auto",
        "rickshaw",
        "bus",
        "metro",
        "train",
        "cab",
        "taxi",
        "petrol",
        "diesel",
        "parking",
        "toll",
        "fuel",
    ),
    Category.SHOPPING: ("shoes", "shirt", "clothes", "tshirt", "jeans", "bag", "gift"),
    Category.ENTERTAINMENT: ("movie", "cinema", "game", "party"),
    Category.HEALTH: ("medicine", "medical", "doctor", "pharmacy", "tablets"),
    Category.BILLS_UTILITIES: ("recharge", "electricity", "bill", "gas", "water"),
    Category.RENT: ("rent", "kiraya"),
}


@dataclass(frozen=True, slots=True)
class CashEntry:
    entry_type: CashEntryType
    amount_paise: int
    note: str | None
    category: Category | None


def _amount_paise(number: str, suffix: str | None) -> int:
    value = Decimal(number)
    if suffix and suffix.lower() == "k":
        value *= 1000
    return rupees_to_paise(value.quantize(Decimal("0.01")))


def guess_category(note: str | None) -> Category:
    if not note:
        return Category.CASH_UNCATEGORIZED
    lowered = f" {note.lower()} "
    for category, words in _CATEGORY_WORDS.items():
        if any(f" {w} " in lowered or lowered.strip().startswith(w) for w in words):
            return category
    return Category.CASH_UNCATEGORIZED


def parse_cash_entry(text: str) -> CashEntry | None:
    """Parse one chat line into a cash entry, or None if it does not look like one."""
    cleaned = text.strip()
    matches = list(_AMOUNT.finditer(cleaned))
    if len(matches) != 1 or len(cleaned) > 120:
        return None
    match = matches[0]
    try:
        amount = _amount_paise(match.group(1), match.group(2))
    except (InvalidOperation, MoneyError):
        return None
    if not 0 < amount <= MAX_CASH_PAISE:
        return None

    rest = (cleaned[: match.start()] + " " + cleaned[match.end() :]).strip()
    if _ATM.search(rest):
        entry_type = CashEntryType.ATM_WITHDRAWAL
    elif _RECEIVED.search(rest):
        entry_type = CashEntryType.CASH_RECEIVED
    else:
        entry_type = CashEntryType.CASH_SPEND
    note = re.sub(r"\s+", " ", _FILLER.sub(" ", rest)).strip(" -,.:") or None
    category = guess_category(note) if entry_type is CashEntryType.CASH_SPEND else None
    return CashEntry(entry_type=entry_type, amount_paise=amount, note=note, category=category)
