"""Grounding check (PROJECT_SPEC §27.4): every number in AI text must come from tool results.

Numbers are extracted with Indian formats (``₹1,23,456``, ``1.2k``, ``22nd``) and compared
with the allowed values after rupee/paise conversion, rounding to ₹1.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

_NUMBER = re.compile(
    r"(?<![\w.])(?:₹|rs\.?\s*|inr\s*)?"
    r"([0-9]{1,3}(?:,[0-9]{2,3})+|[0-9]+)(?:\.([0-9]+))?"
    r"\s*(k|lakh|lac|cr)?(?:st|nd|rd|th)?(?![\w])",
    re.I,
)
_MULTIPLIER = {"k": 1_000, "lakh": 100_000, "lac": 100_000, "cr": 10_000_000}


@dataclass(frozen=True, slots=True)
class GroundingResult:
    ok: bool
    unmatched: tuple[str, ...]


def extract_numbers(text: str) -> list[tuple[str, Decimal]]:
    """Return ``(original_token, value)`` for every number in ``text``."""
    out = []
    for match in _NUMBER.finditer(text):
        whole, frac, unit = match.group(1), match.group(2), match.group(3)
        value = Decimal(whole.replace(",", "") + (f".{frac}" if frac else ""))
        if unit:
            value *= _MULTIPLIER[unit.lower()]
        out.append((match.group(0).strip(), value))
    return out


def allowed_values(
    *, paise: Iterable[int] = (), counts: Iterable[int] = (), dates: Iterable[date] = ()
) -> set[Decimal]:
    """Values an AI message may mention: rupee amounts (rounded to ₹1), counts, day numbers."""
    values: set[Decimal] = set()
    for p in paise:
        rupees = Decimal(p) / 100
        values.add(rupees.quantize(Decimal(1)))
        values.add(rupees.quantize(Decimal("0.01")))
    values.update(Decimal(c) for c in counts)
    for d in dates:
        values.update({Decimal(d.day), Decimal(d.year), Decimal(d.month)})
    return values


def check_grounding(text: str, allowed: set[Decimal]) -> GroundingResult:
    unmatched = []
    for token, value in extract_numbers(text):
        candidates = {value, value.quantize(Decimal(1))}
        if not candidates & allowed:
            unmatched.append(token)
    return GroundingResult(ok=not unmatched, unmatched=tuple(unmatched))
