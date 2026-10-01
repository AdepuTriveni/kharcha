"""Recurring payments and income (PROJECT_SPEC §14): same merchant, amount within ±10%,
roughly weekly or monthly (±3 days), with enough occurrences.
"""

import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise

from kharcha_insights.forecast.model import Recurring

AMOUNT_TOLERANCE_PCT = 10
INTERVAL_TOLERANCE_DAYS = 3
PERIODS = {7: 3, 30: 2}  # interval -> minimum occurrences


@dataclass(frozen=True, slots=True)
class PastTxn:
    id: str
    key: str  # merchant id, else normalized merchant text
    name: str
    amount_paise: int  # signed: income > 0
    day: date
    category: str | None = None


def _close(a: int, b: int) -> bool:
    return abs(a - b) * 100 <= AMOUNT_TOLERANCE_PCT * abs(b)


def _series(txns: list[PastTxn]) -> tuple[Recurring, set[str]] | None:
    txns = sorted(txns, key=lambda t: t.day)
    median = int(statistics.median(t.amount_paise for t in txns))
    same = [t for t in txns if _close(t.amount_paise, median)]
    for period, minimum in PERIODS.items():
        if len(same) < minimum:
            continue
        gaps = [(b.day - a.day).days for a, b in pairwise(same)]
        if gaps and all(abs(g - period) <= INTERVAL_TOLERANCE_DAYS for g in gaps):
            last = same[-1]
            amount = int(statistics.median(t.amount_paise for t in same))
            item = Recurring(
                name=last.name,
                amount_paise=amount,
                next_date=last.day + timedelta(days=period),
                interval_days=period,
                category=last.category,
            )
            return item, {t.id for t in same}
    return None


def detect_recurring(txns: Sequence[PastTxn]) -> tuple[list[Recurring], set[str]]:
    """Recurring items and the ids of the transactions they explain."""
    groups: dict[tuple[str, bool], list[PastTxn]] = defaultdict(list)
    for t in txns:
        if t.amount_paise != 0:
            groups[(t.key, t.amount_paise > 0)].append(t)
    found: list[Recurring] = []
    used: set[str] = set()
    for _, group in sorted(groups.items()):
        if len(group) < min(PERIODS.values()):
            continue
        series = _series(group)
        if series is not None:
            found.append(series[0])
            used |= series[1]
    return found, used
