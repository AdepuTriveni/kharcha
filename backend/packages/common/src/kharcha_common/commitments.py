"""Commitments in plain words (PROJECT_SPEC §20.1): "limit Zomato to 2 orders a week till 31 Oct".

Rules first (§25 intent step); the Memory Keeper model handles what the rules miss.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

from kharcha_common.time import IST

_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}
_LIMIT = re.compile(
    r"\b(?:limit|max(?:imum)?|only|at most|sirf|bas)\s+(?P<target>[a-z][a-z .&']{1,30}?)\s+"
    r"(?:to\s+)?(?P<n>\d{1,2})\s*(?:x|times?|orders?|baar)?\s*"
    r"(?:a|per|/|every|each|ek)\s*(?P<period>week|month|day|hafte|mahine)",
    re.I,
)
_NONE = re.compile(
    r"\b(?:no|stop|band|nahi)\s+(?P<target>[a-z][a-z .&']{1,30}?)\s+"
    r"(?:this|for a|for the|is)\s+(?P<period>week|month|hafta|mahina)",
    re.I,
)
_UNTIL = re.compile(r"\b(?:till|until|upto|by|tak)\s+(?P<day>\d{1,2})\s*(?P<mon>[a-z]{3})", re.I)
_PERIODS = {"hafte": "week", "hafta": "week", "mahine": "month", "mahina": "month"}


@dataclass(frozen=True, slots=True)
class Commitment:
    target: str  # merchant or category words, lowercase
    limit: int  # max payments per period (0 = none)
    period: str  # week | month | day
    until: date | None
    text: str

    def due_at(self) -> datetime | None:
        return datetime.combine(self.until, time(23, 59), tzinfo=IST) if self.until else None


def _until(text: str, today: date) -> date | None:
    m = _UNTIL.search(text)
    if not m or m.group("mon").lower()[:3] not in _MONTHS:
        return None
    month = _MONTHS[m.group("mon").lower()[:3]]
    day = int(m.group("day"))
    year = today.year + (1 if month < today.month else 0)
    try:
        return date(year, month, day)
    except ValueError:
        return None


def detect(text: str, today: date) -> Commitment | None:
    clean = " ".join(text.split())
    if m := _LIMIT.search(clean):
        period = _PERIODS.get(m.group("period").lower(), m.group("period").lower())
        return Commitment(
            m.group("target").strip().lower(),
            int(m.group("n")),
            period,
            _until(clean, today),
            clean,
        )
    if m := _NONE.search(clean):
        period = _PERIODS.get(m.group("period").lower(), m.group("period").lower())
        return Commitment(m.group("target").strip().lower(), 0, period, _until(clean, today), clean)
    return None


def progress(
    commitment_text: str, items: list[dict[str, Any]], today: date
) -> tuple[str, int, int] | None:
    """(target, payments this period, limit) for a stored commitment, from list_transactions."""
    parsed = detect(commitment_text, today)
    if parsed is None:
        return None
    count = sum(
        1
        for i in items
        if i.get("direction") == "DEBIT" and parsed.target in str(i.get("merchant", "")).lower()
    )
    return parsed.target, count, parsed.limit
