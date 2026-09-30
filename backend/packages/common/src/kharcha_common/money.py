"""Money helpers. Money is always ``int`` paise; ``Decimal`` is used only for display.

Floats are rejected everywhere (CLAUDE.md rule 1).
"""

import re
from decimal import Decimal
from typing import Annotated

from pydantic import Field, StrictInt

PAISE_PER_RUPEE = 100

# Pydantic field types for event/API models. StrictInt rejects floats and numeric strings.
Paise = Annotated[StrictInt, Field(description="Amount in paise (1 rupee = 100 paise)")]
PositivePaise = Annotated[StrictInt, Field(gt=0, description="Positive amount in paise")]

_RUPEE_TEXT = re.compile(r"^(?:rs\.?|inr|₹)?\s*(-)?\s*([0-9][0-9,]*)(?:\.([0-9]{1,2}))?$", re.I)


class MoneyError(ValueError):
    """Raised when a value cannot be represented exactly as paise."""


def rupees_to_paise(value: str | Decimal | int) -> int:
    """Convert rupees to paise exactly.

    Accepts ``int`` rupees, ``Decimal`` with at most 2 decimal places, or text such as
    ``"Rs.1,234.50"``, ``"₹ 349"``, ``"INR 20.5"``. Never accepts ``float``.
    """
    if isinstance(value, bool):
        raise MoneyError("bool is not money")
    if isinstance(value, int):
        return value * PAISE_PER_RUPEE
    if isinstance(value, Decimal):
        return _decimal_to_paise(value)
    if isinstance(value, str):
        match = _RUPEE_TEXT.match(value.strip())
        if match is None:
            raise MoneyError(f"not a rupee amount: {value!r}")
        sign, whole, frac = match.groups()
        paise = int(whole.replace(",", "")) * PAISE_PER_RUPEE + int((frac or "0").ljust(2, "0"))
        return -paise if sign else paise
    raise MoneyError(f"unsupported money type: {type(value).__name__}")


def _decimal_to_paise(value: Decimal) -> int:
    if not value.is_finite():
        raise MoneyError(f"not a finite amount: {value}")
    scaled = value * PAISE_PER_RUPEE
    if scaled != scaled.to_integral_value():
        raise MoneyError(f"more than 2 decimal places: {value}")
    return int(scaled)


def paise_to_rupees(paise: int) -> Decimal:
    """Exact rupee value for display, e.g. ``34900 -> Decimal("349.00")``."""
    return (Decimal(paise) / PAISE_PER_RUPEE).quantize(Decimal("0.01"))


def format_inr(paise: int, *, symbol: str = "₹") -> str:
    """Format with Indian digit grouping: ``12345678 -> "₹1,23,456.78"``."""
    sign = "-" if paise < 0 else ""
    rupees, rem = divmod(abs(paise), PAISE_PER_RUPEE)
    digits = str(rupees)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join([*groups, tail])
    return f"{sign}{symbol}{digits}.{rem:02d}"
