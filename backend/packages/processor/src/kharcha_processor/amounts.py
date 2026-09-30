"""Find rupee amounts in message text (PROJECT_SPEC §10.2).

Handles ``Rs.349``, ``Rs 349.00``, ``INR 1,249.50``, ``₹2,000``, ``1,00,000``.
"""

import re

from kharcha_common.money import MoneyError, rupees_to_paise

# A number: digits with optional Indian/Western comma grouping and up to 2 decimals.
_NUMBER = (
    r"(?:[0-9]{1,3}(?:,[0-9]{3})+"  # 1,249 / 10,000 (western grouping)
    r"|[0-9]{1,2}(?:,[0-9]{2})+,[0-9]{3}"  # 1,00,000 / 12,34,567 (Indian grouping)
    r"|[0-9]+)"  # 349
    r"(?:\.[0-9]{1,2})?"
)
_CURRENCY = re.compile(rf"(?:(?<![a-z])(?:rs\.?|inr)|₹)\s*({_NUMBER})(?![0-9])", re.I)
_ANY_NUMBER = re.compile(rf"(?<![0-9])({_NUMBER})(?![0-9])")


def _to_paise(tokens: list[str]) -> list[int]:
    out = []
    for token in tokens:
        try:
            out.append(rupees_to_paise(token))
        except MoneyError:
            continue
    return out


def currency_amounts(text: str) -> list[int]:
    """Amounts (paise) written with a currency marker, in order of appearance."""
    return _to_paise(_CURRENCY.findall(text))


def all_number_amounts(text: str) -> set[int]:
    """Every number in the text read as rupees (paise). Used for anti-hallucination checks."""
    return set(_to_paise(_ANY_NUMBER.findall(text))) | set(currency_amounts(text))


def parse_amount_text(value: str) -> int:
    """Parse an amount copied from a message (``"1,249.50"``, ``"Rs.349"``) into paise."""
    return rupees_to_paise(value.strip())
